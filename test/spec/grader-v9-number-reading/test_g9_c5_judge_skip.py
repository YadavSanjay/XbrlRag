"""C5: the text judge skips a failed synthesis, with a recorded reason
(docs/specs/grader-v9-number-reading.md, requirement 5)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.judge_wiring import judge_case, judge_skip_reason, run_graded
from src.eval.core.runner import RunnerConfig
from src.eval.core.system import SystemAnswer


def _cfg() -> RunnerConfig:
    return RunnerConfig(max_workers=1, progress_every=0)


def _lookup(case_id: str) -> EvalCase:
    return EvalCase.from_dict({"id": case_id, "question": "Revenue?", "kind": "lookup",
                               "ticker": "DEVA", "fy": 2025, "expected_value": 100.0})


@pytest.mark.criterion("C5")
def test_reasons_in_order_of_precedence(kit, text_case):
    # oracle: requirement 5, steps 1-5
    t = text_case("t1")
    plain = SystemAnswer(text="Services drove growth.")
    assert judge_skip_reason(_lookup("l1"), SystemAnswer(text=kit.E)) == "not_text_graded"
    assert judge_skip_reason(t, SystemAnswer(text=kit.E, error="RuntimeError('x')")) == \
        "answer_error"
    assert judge_skip_reason(t, SystemAnswer(text="Part one. " + kit.E + " " + kit.R)) == \
        "synthesis_error"
    assert judge_skip_reason(t, SystemAnswer(text=kit.R)) == "synthesis_refusal"
    assert judge_skip_reason(t, plain) is None


@pytest.mark.criterion("C5")
def test_judge_case_makes_no_call_on_a_fallback(kit, text_case, make_judge):
    # oracle: requirement 5 — no judge call for E or R; a plain answer is judged once
    judge, adapter = make_judge()
    t = text_case("t1")
    assert judge_case(judge, t, SystemAnswer(text=kit.E)) is None
    assert judge_case(judge, t, SystemAnswer(text=kit.R)) is None
    assert adapter.calls == 0
    assert judge_case(judge, t, SystemAnswer(text="Services drove growth.")) is not None
    assert adapter.calls == 1


@pytest.mark.criterion("C5")
def test_run_graded_skips_and_records(kit, text_case, make_judge, fake_system,
                                      unit_judge_cost):
    # oracle: requirement 5 — one plain, one E, one R: one judge call (cost from
    # configs/llm/pricing.yaml), scores on the plain case only, both skips recorded
    cases = [text_case("t1"), text_case("t2"), text_case("t3")]
    answers = {"t1": SystemAnswer(text="Services drove growth."),
               "t2": SystemAnswer(text=kit.E), "t3": SystemAnswer(text=kit.R)}
    judge, adapter = make_judge()
    report = run_graded(cases, fake_system(answers), judge=judge,
                        generator_profile="deepseek", config=_cfg(), metamorphic=False)
    assert adapter.calls == 1
    scores = {r.case.id: r.grade.judge_scores for r in report.results}
    assert scores["t1"] is not None
    assert scores["t2"] is None and scores["t3"] is None
    assert report.config.get("judge_skipped") == {"t2": "synthesis_error",
                                              "t3": "synthesis_refusal"}
    assert report.config["judge_errors"] == {}
    # tolerance: one float product; 1e-12 absolute is far below one token's cost
    assert report.config["judge_cost_usd"] == pytest.approx(unit_judge_cost, abs=1e-12)
    assert report.config["judge_summary"]["n_judged"] == 1


KINDS = st.sampled_from(["plain", "E", "R", "error"])


@pytest.mark.criterion("C5")
@settings(max_examples=25, deadline=None)
@given(kinds=st.lists(KINDS, min_size=1, max_size=6))
def test_calls_equal_the_plain_answers(kit, text_case, make_judge, fake_system, kinds):
    # oracle: requirement 5 — exactly the plain answers are judged; every other text case is
    # listed with its reason
    texts = {"plain": "Services drove growth.", "E": kit.E, "R": kit.R, "error": ""}
    reasons = {"E": "synthesis_error", "R": "synthesis_refusal", "error": "answer_error"}
    cases = [text_case(f"t{i}") for i in range(len(kinds))]
    answers = {
        f"t{i}": SystemAnswer(text=texts[k], error="RuntimeError('x')" if k == "error" else None)
        for i, k in enumerate(kinds)
    }
    judge, adapter = make_judge()
    report = run_graded(cases, fake_system(answers), judge=judge,
                        generator_profile="deepseek", config=_cfg(), metamorphic=False)
    assert adapter.calls == kinds.count("plain")
    assert report.config.get("judge_skipped") == {
        f"t{i}": reasons[k] for i, k in enumerate(kinds) if k != "plain"}
