"""C3: one case is judged once, with the right inputs, and its scores map to the grade."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import answer_claims, judge_case, judge_scores
from src.eval.core.run_columns import judge_columns
from src.eval.core.system import SystemAnswer
from src.llm.run_budget import RunBudgetExceeded, RunHalt

JUDGE_VERSION = "v1:claude-opus-5-5"
KEYS = {"correctness", "completeness", "citation_support", "unsupported_claims"}
STEPS = [{"step_id": "s1", "op": "lookup", "status": "OK", "value": 100.0, "unit": "USD",
          "citation": {"fact_id": 501}}]
CHUNKS = [{"chunk_id": "c-1", "text": "Services drove growth."}]
DETAILS = [
    {"statement": "right: revenue was 100.", "fact_ids": [501], "chunk_ids": []},
    {"statement": "wrong: growth came from hardware.", "fact_ids": [], "chunk_ids": ["c-1"]},
]


def _text_case(make_case, case_id="t1"):
    return make_case(case_id, kind="narrative", question="How did AAA's revenue do?",
                     expected_text="Revenue was 100, driven by services.",
                     key_points=["revenue 100", "services", "no hardware"])


def _correct_if_right(claim: dict) -> tuple[bool, bool]:
    return claim["text"].startswith("right"), True


@pytest.mark.criterion("C3")
def test_one_call_with_the_case_inputs(make_judge, rule, make_case, make_response):
    # oracle: requirement 4 and eval-judge requirement 4, by hand — claims s0 correct and
    # s1 not (1 of 2), both cited and supported (2 of 2), key points 0 and 2 of 3 covered
    judge, adapter = make_judge(rule(_correct_if_right, lambda i: i != 1))
    case = _text_case(make_case)
    answer = SystemAnswer(text="t", raw=make_response(details=DETAILS, steps=STEPS,
                                                      chunks=CHUNKS))
    result = judge_case(judge, case, answer)

    assert adapter.calls == 1
    payload = adapter.payloads[0]
    assert payload["question"] == "How did AAA's revenue do?"
    assert payload["reference_answer"] == "Revenue was 100, driven by services."
    assert [p["text"] for p in payload["key_points"]] == ["revenue 100", "services",
                                                          "no hardware"]
    expected_claims = [
        {"id": c.claim_id, "text": c.text, "cited_passages": list(c.cited_passages)}
        for c in answer_claims(answer)
    ]
    assert payload["claims"] == expected_claims
    assert [c["id"] for c in payload["claims"]] == ["s0", "s1"]

    assert result is not None and result.error is None
    assert result.judge_version == JUDGE_VERSION
    # tolerance: ratios of small ints; any correct division is within 1e-12
    assert result.correctness == pytest.approx(0.5, abs=1e-12)
    assert result.completeness == pytest.approx(2 / 3, abs=1e-12)
    assert result.citation_support == pytest.approx(1.0, abs=1e-12)
    assert result.unsupported_claims == 0

    scores = judge_scores(result)
    assert set(scores) == KEYS
    # tolerance: as above
    assert scores["correctness"] == pytest.approx(0.5, abs=1e-12)
    assert scores["completeness"] == pytest.approx(2 / 3, abs=1e-12)
    assert scores["citation_support"] == pytest.approx(1.0, abs=1e-12)
    assert scores["unsupported_claims"] == 0
    columns = judge_columns(scores)
    # tolerance: as above
    assert columns["judge_completeness"] == pytest.approx(2 / 3, abs=1e-12)


@pytest.mark.criterion("C3")
def test_not_judged_without_key_points_or_on_error(make_judge, make_case, make_response):
    # oracle: requirement 4 — None and no call when not text-graded or the answer errored
    judge, adapter = make_judge()
    answer = SystemAnswer(text="t", raw=make_response(details=DETAILS, steps=STEPS,
                                                      chunks=CHUNKS))
    no_points = make_case("n1", kind="narrative", expected_text="Ref.")
    assert judge_case(judge, no_points, answer) is None
    lookup = make_case("l1", expected_value=100.0)
    assert judge_case(judge, lookup, answer) is None
    errored = SystemAnswer(error="RuntimeError('boom')")
    assert judge_case(judge, _text_case(make_case), errored) is None
    assert adapter.calls == 0


@pytest.mark.criterion("C3")
def test_judge_error_gives_four_nones(make_judge, make_case, make_response):
    # oracle: requirement 5 — a judge_error's scores are never imputed
    judge, adapter = make_judge(lambda _payload: "this is not JSON")
    answer = SystemAnswer(text="t", raw=make_response(details=DETAILS, steps=STEPS,
                                                      chunks=CHUNKS))
    result = judge_case(judge, _text_case(make_case), answer)
    assert adapter.calls == 1
    assert result is not None and result.error is not None
    assert result.error.startswith("judge_error: ")
    scores = judge_scores(result)
    assert scores == dict.fromkeys(KEYS)
    assert all(v is None for v in judge_columns(scores).values())


@pytest.mark.criterion("C3")
def test_run_halt_propagates(make_judge, make_case, make_response):
    # oracle: requirement 4 — RunHalt from the judge is never swallowed
    judge, _ = make_judge(error=RunBudgetExceeded("cap reached"))
    answer = SystemAnswer(text="t", raw=make_response(details=DETAILS, steps=STEPS,
                                                      chunks=CHUNKS))
    with pytest.raises(RunHalt):
        judge_case(judge, _text_case(make_case), answer)


@pytest.mark.criterion("C3")
@given(
    verdicts=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=6),
    covered=st.lists(st.booleans(), min_size=1, max_size=5),
)
def test_scores_follow_the_formulas(make_judge, rule, make_case, make_response, verdicts,
                                    covered):
    # oracle: eval-judge requirement 4's formulas, evaluated here; every statement cites a
    # resolvable fact, so 'supported' is the judge's own verdict
    details = [{"statement": f"claim {i}", "fact_ids": [501], "chunk_ids": []}
               for i in range(len(verdicts))]
    by_id = {f"s{i}": v for i, v in enumerate(verdicts)}
    judge, adapter = make_judge(rule(lambda c: by_id[c["id"]], lambda i: covered[i]))
    case = make_case("h1", kind="narrative", expected_text="Ref.",
                     key_points=[f"point {i}" for i in range(len(covered))])
    answer = SystemAnswer(text="t", raw=make_response(details=details, steps=STEPS))
    scores = judge_scores(judge_case(judge, case, answer))
    n = len(verdicts)
    n_supported = sum(s for _, s in verdicts)
    assert adapter.calls == 1
    # tolerance: ratios of small ints; any correct division is within 1e-12
    assert scores["correctness"] == pytest.approx(sum(c for c, _ in verdicts) / n, abs=1e-12)
    assert scores["completeness"] == pytest.approx(sum(covered) / len(covered), abs=1e-12)
    assert scores["citation_support"] == pytest.approx(n_supported / n, abs=1e-12)
    assert scores["unsupported_claims"] == n - n_supported
