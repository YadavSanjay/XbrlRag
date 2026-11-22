"""C7: XbrlRag is graded on the values its final answer states
(docs/specs/grading-precision.md, requirement 5).

The pipeline is injected (``FakePipeline`` in conftest): it is the system's LLM/DB
boundary. ``XbrlRagResponse``, ``XbrlRagFullSystem`` and the graders are real.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.systems.xbrlrag_full import answer_rows, final_answer_text

VREL = 1e-12  # stated values are exact decimals times a power of ten
INTC_OPINC = 93_000_000.0  # INTC FY2023 operating income, decimals -6 (dev fixture)


def _lookup(value: float, fact_id: int) -> dict:
    return {"step_id": f"s{fact_id}", "op": "lookup", "status": "OK", "value": value,
            "citation": {"fact_id": fact_id}}


# Semis FY2025 net income, as the executor's screen step carries it (full precision).
SEMIS_STEP = {
    "step_id": "scr", "op": "screen", "status": "OK",
    "rows": [
        {"ticker": "NVDA", "value": 72_880_000_000.0, "fact_id": 501},
        {"ticker": "AVGO", "value": 23_126_000_000.0, "fact_id": 502},
        {"ticker": "MU", "value": 8_539_000_000.0, "citation": {"fact_id": 503}},
        {"ticker": "QCOM", "value": 5_541_000_000.0, "component_fact_ids": [601, 602]},
    ],
}


def _semis_details(nvda: str = "$72.88 billion") -> list[dict]:
    return [
        {"statement": f"NVDA ranked 1st with net income of {nvda}.", "fact_ids": [501]},
        {"statement": "AVGO followed at $23.126 billion.", "fact_ids": ["502"]},
        {"statement": "The ranking covers the semiconductor companies.", "fact_ids": [501]},
        {"statement": "MU was third: $8.539 billion, up 2% on the year.", "fact_ids": [503]},
        {"statement": "QCOM came next with $5.541 billion.", "fact_ids": [602]},
        {"statement": "NVDA again: $1 billion of something else.", "fact_ids": [501]},
    ]


@pytest.mark.criterion("C7")
def test_final_answer_text(xbrl_response):
    # oracle: requirement 5 — the summary, then each kept statement, one per line
    resp = xbrl_response("Summary line.", [{"statement": "First.", "fact_ids": [1]},
                                           {"statement": "Second.", "fact_ids": [2]}], [])
    assert final_answer_text(resp) == "Summary line.\nFirst.\nSecond."
    assert final_answer_text(resp.model_dump()) == "Summary line.\nFirst.\nSecond."
    bare = SimpleNamespace(answer_summary="Revenue was 1.", execution_result={"steps": []})
    assert final_answer_text(bare) == "Revenue was 1."


@pytest.mark.criterion("C7")
def test_answer_rows_follow_the_citing_statements(xbrl_response):
    # oracle: requirement 5 — each statement citing a screen row (fact_id, citation
    # fact_id, or a component fact id; int or digit string) states that row, with the
    # stated number nearest the row's execution value; a statement with no number states
    # no row; a ticker once (first statement wins). Hand-read from _semis_details.
    resp = xbrl_response("Top semis by FY2025 net income.", _semis_details(), [SEMIS_STEP])
    rows = answer_rows(resp)
    assert [r["ticker"] for r in rows] == ["NVDA", "AVGO", "MU", "QCOM"]
    assert [r["value"] for r in rows] == pytest.approx(
        [72.88e9, 23.126e9, 8.539e9, 5.541e9], rel=VREL)


@pytest.mark.criterion("C7")
def test_full_system_reads_its_final_answer(dev, xbrl_response, xbrlrag_answer):
    # oracle: requirement 5 — text and values from the final answer, fact_ids from the
    # execution result. INTC 93,000,000 at -6: "$93.4 million" rounds to 93 million
    # (CORRECT); "$95 million" does not, and the exact execution value no longer rescues it.
    case = dev("intc_opinc")
    details = [{"statement": "Operating income was $93.4 million in FY2023.", "fact_ids": [77]}]
    resp = xbrl_response("Intel's operating income was $93.4 million.", details,
                         [_lookup(INTC_OPINC, 77)])
    got = xbrlrag_answer(case, resp)
    assert got.text == ("Intel's operating income was $93.4 million.\n"
                        "Operating income was $93.4 million in FY2023.")
    assert got.numeric_values == pytest.approx([93.4e6, 93.4e6], rel=VREL)
    assert got.fact_ids == [77]
    assert grade_case(case, got).grade is Grade.CORRECT
    wrong = xbrl_response("Intel's operating income was $95 million.", [],
                          [_lookup(INTC_OPINC, 77)])
    assert grade_case(case, xbrlrag_answer(case, wrong)).grade is Grade.INCORRECT
    vague = xbrl_response("Intel's operating income was $0.1 billion.", [],
                          [_lookup(INTC_OPINC, 77)])
    # "$0.1 billion" is 93 million rounded at its own 0.1 billion: IMPRECISE (C9), not
    # rescued to CORRECT by the execution value either
    assert grade_case(case, xbrlrag_answer(case, vague)).grade.value == "IMPRECISE"


@pytest.mark.criterion("C7")
def test_full_system_screen_uses_stated_rows(dev, xbrl_response, xbrlrag_answer):
    # oracle: semis top 3 at -6 (NVDA 72,880, AVGO 23,126, MU 8,539 million): rows stated
    # to the million all agree; "$72.9 billion" for NVDA (72,900 million) does not, even
    # though the screen step holds 72,880,000,000 exactly
    case = dev("semis_ni_top3")
    good = xbrlrag_answer(case, xbrl_response("Top 3.", _semis_details(), [SEMIS_STEP]))
    assert [r["ticker"] for r in good.rows] == ["NVDA", "AVGO", "MU", "QCOM"]
    assert grade_case(case, good).screen_metrics["value_agreement"] == pytest.approx(
        1.0, abs=1e-12)  # k/n count ratio
    rough = xbrlrag_answer(case, xbrl_response("Top 3.", _semis_details("$72.9 billion"),
                                               [SEMIS_STEP]))
    assert grade_case(case, rough).screen_metrics["value_agreement"] == pytest.approx(
        2 / 3, abs=1e-12)  # k/n count ratio


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(tenths=st.integers(min_value=900, max_value=960), template=st.sampled_from([
    "Intel's operating income was ${x} million.",
    "Operating income came to ${x} million in FY2023.",
    "In fiscal 2023 the company earned ${x} million from operations.",
]))
def test_same_text_same_verdict_for_xbrlrag_and_a_text_arm(dev, xbrl_response, xbrlrag_answer,
                                                          tenths, template):
    # oracle: requirement 5 — both arms are graded on the stated value; 90.0..96.0 million
    # against 93 million at -6 is right exactly when it rounds to 93 (92.5 <= x < 93.5),
    # whatever XbrlRag's execution step holds
    case = dev("intc_opinc")
    text = template.format(x=f"{tenths / 10:.1f}")
    resp = xbrl_response(text, [], [_lookup(INTC_OPINC, 77)])
    xbrlrag = grade_case(case, xbrlrag_answer(case, resp))
    arm = parse_structured_answer(text)
    arm.fact_ids = [1]
    text_arm = grade_case(case, arm)
    want = 925 <= tenths < 935
    assert xbrlrag.value_correct is want
    assert text_arm.value_correct is want
