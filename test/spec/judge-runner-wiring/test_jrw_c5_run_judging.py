"""C5: run_graded judges each text-graded case once and records the judge in the run."""

from __future__ import annotations

import pytest

from src.eval.core.judge_wiring import run_graded
from src.eval.core.run_columns import run_columns
from src.eval.core.runner import RunnerConfig
from src.eval.core.system import SystemAnswer
from src.llm.run_budget import RunBudgetExceeded, RunHalt

JUDGE_VERSION = "v1:claude-opus-5-5"
KEYS = ("correctness", "completeness", "citation_support", "unsupported_claims")
STEPS = [{"step_id": "s1", "op": "lookup", "status": "OK", "value": 100.0, "unit": "USD",
          "citation": {"fact_id": 501}}]
CHUNKS = [{"chunk_id": "c-1", "text": "Services drove growth."}]


def _cfg(**kw) -> RunnerConfig:
    kw.setdefault("max_workers", 1)
    kw.setdefault("progress_every", 0)
    return RunnerConfig(**kw)


def _cases(make_case):
    return [
        make_case("t1", kind="narrative", expected_text="Ref one.",
                  key_points=["p0", "p1", "p2"]),
        make_case("t2", kind="narrative", expected_text="Ref two.", key_points=["p0", "p1"]),
        make_case("l1", kind="lookup", ticker="AAA", fy=2024, concept="Revenue",
                  expected_value=100.0, expected_fact_ids=[501]),
    ]


def _answers(make_response, cost=0.01):
    t1 = make_response(details=[
        {"statement": "right: revenue was 100.", "fact_ids": [501], "chunk_ids": []},
        {"statement": "wrong: hardware drove it.", "fact_ids": [], "chunk_ids": ["c-1"]},
    ], steps=STEPS, chunks=CHUNKS)
    t2 = make_response(details=[{"statement": "right: services grew.", "fact_ids": [],
                                 "chunk_ids": []}], steps=STEPS)
    l1 = make_response(details=[{"statement": "Revenue was 100.", "fact_ids": [501],
                                 "chunk_ids": []}], steps=STEPS)
    return {
        "t1": SystemAnswer(text="t1", raw=t1, cost_usd=cost),
        "t2": SystemAnswer(text="t2", raw=t2, cost_usd=cost),
        "l1": SystemAnswer(text="l1", raw=l1, numeric_values=[100.0], fact_ids=[501],
                           cost_usd=cost),
    }


def _rule(claim: dict) -> tuple[bool, bool]:
    return claim["text"].startswith("right"), True


def _even(index: int) -> bool:
    return index % 2 == 0


# oracle: eval-judge requirement 4 by hand, with the rule "correct iff it starts with
# 'right', the judge says supported, key point covered iff its index is even":
#   t1: s0 correct, s1 not -> 1/2; both cite something -> support 2/2, 0 unsupported;
#       points 0, 2 of 3 covered -> 2/3
#   t2: s0 correct -> 1/1; it cites nothing, so it is unsupported whatever the judge
#       says -> support 0/1, 1 unsupported; point 0 of 2 covered -> 1/2
EXPECTED = {
    "t1": {"correctness": 0.5, "completeness": 2 / 3, "citation_support": 1.0,
           "unsupported_claims": 0},
    "t2": {"correctness": 1.0, "completeness": 0.5, "citation_support": 0.0,
           "unsupported_claims": 1},
}


def _scores(report) -> dict:
    return {r.case.id: r.grade.judge_scores for r in report.results}


def _assert_scores(got: dict, expected: dict) -> None:
    assert set(got) == set(KEYS)
    for key in KEYS:
        # tolerance: ratios of small ints; any correct division is within 1e-12
        assert got[key] == pytest.approx(expected[key], abs=1e-12), key


@pytest.mark.criterion("C5")
def test_text_cases_are_judged_once(make_judge, rule, make_case, make_response, fake_system,
                                    unit_judge_cost):
    # oracle: EXPECTED (by hand) and requirement 7; judge cost from configs/llm/pricing.yaml
    judge, adapter = make_judge(rule(_rule, _even))
    system = fake_system(_answers(make_response))
    report = run_graded(_cases(make_case), system, judge=judge, generator_profile="deepseek",
                        config=_cfg())

    assert adapter.calls == 2
    assert sorted(p["reference_answer"] for p in adapter.payloads) == ["Ref one.", "Ref two."]
    scores = _scores(report)
    _assert_scores(scores["t1"], EXPECTED["t1"])
    _assert_scores(scores["t2"], EXPECTED["t2"])
    assert scores["l1"] is None

    assert report.config["judge_version"] == JUDGE_VERSION
    assert run_columns(report)["judge_version"] == JUDGE_VERSION
    assert report.config["judge_errors"] == {}
    # tolerance: a sum of two float products; 1e-12 absolute is far below one token's cost
    assert report.config["judge_cost_usd"] == pytest.approx(2 * unit_judge_cost, abs=1e-12)
    # the case costs stay the system's own
    for r in report.results:
        # tolerance: the system's cost passed through unchanged
        assert r.cost_usd == pytest.approx(0.01, abs=1e-12)
    assert report.config["judge_summary"]["status"] == "uncalibrated"
    assert report.status == "OK"


@pytest.mark.criterion("C5")
def test_judge_error_and_errored_answer(make_judge, make_case, make_response, fake_system):
    # oracle: requirement 7 — a malformed reply gives four Nones and a judge_errors entry;
    # an errored answer is not judged
    judge, adapter = make_judge(lambda _payload: "not json at all")
    answers = _answers(make_response)
    answers["t2"] = SystemAnswer(error="RuntimeError('pipeline down')")
    report = run_graded(_cases(make_case), fake_system(answers), judge=judge,
                        generator_profile="openai-5.6", config=_cfg())

    assert adapter.calls == 1
    scores = _scores(report)
    assert scores["t1"] == dict.fromkeys(KEYS)
    assert scores["t2"] is None
    assert scores["l1"] is None
    assert set(report.config["judge_errors"]) == {"t1"}
    assert report.config["judge_errors"]["t1"].startswith("judge_error: ")


@pytest.mark.criterion("C5")
def test_judge_cost_counts_against_the_cap(make_judge, make_case, make_response, fake_system,
                                           unit_judge_cost):
    # oracle: requirement 7 — answers cost 0, each judge call unit_judge_cost; a cap of
    # 1.5 calls is reached after the second case, so the third is never attempted
    cases = [make_case(f"t{i}", kind="narrative", expected_text="Ref.", key_points=["p"])
             for i in (1, 2, 3)]
    answers = {c.id: SystemAnswer(text=f"Answer {c.id}.") for c in cases}
    judge, adapter = make_judge()
    system = fake_system(answers)
    report = run_graded(cases, system, judge=judge, generator_profile="deepseek",
                        config=_cfg(max_cost_usd=1.5 * unit_judge_cost))
    assert [r.case.id for r in report.results] == ["t1", "t2"]
    assert system.answered == ["t1", "t2"]
    assert adapter.calls == 2
    assert report.status == "ABORTED"
    assert report.n_skipped == 1


@pytest.mark.criterion("C5")
def test_run_halt_propagates(make_judge, make_case, make_response, fake_system):
    # oracle: requirement 7 — RunHalt from the judge stops the run; it is not an ERROR grade
    judge, _ = make_judge(error=RunBudgetExceeded("judge budget reached"))
    with pytest.raises(RunHalt):
        run_graded(_cases(make_case), fake_system(_answers(make_response)), judge=judge,
                   generator_profile="deepseek", config=_cfg())


@pytest.mark.criterion("C5")
def test_without_a_judge(make_case, make_response, fake_system):
    # oracle: requirement 7 — no judge: nothing judged, no judge keys but the summary
    report = run_graded(_cases(make_case), fake_system(_answers(make_response)),
                        config=_cfg())
    assert all(r.grade.judge_scores is None for r in report.results)
    assert len(report.results) == 3
    assert "judge_version" not in report.config
    assert run_columns(report)["judge_version"] is None
    assert report.config["judge_summary"]["status"] == "none"


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_scores_do_not_depend_on_case_order(make_judge, rule, make_case, make_response,
                                            fake_system):
    # oracle: requirement 7 — each case is judged on its own answer
    forward_judge, _ = make_judge(rule(_rule, _even))
    forward = run_graded(_cases(make_case), fake_system(_answers(make_response)),
                         judge=forward_judge, generator_profile="deepseek", config=_cfg())
    backward_judge, _ = make_judge(rule(_rule, _even))
    backward = run_graded(list(reversed(_cases(make_case))),
                          fake_system(_answers(make_response)), judge=backward_judge,
                          generator_profile="deepseek", config=_cfg(max_workers=3))
    a, b = _scores(forward), _scores(backward)
    assert set(a) == set(b) == {"t1", "t2", "l1"}
    assert a["l1"] is None and b["l1"] is None
    for cid in ("t1", "t2"):
        _assert_scores(a[cid], EXPECTED[cid])
        _assert_scores(b[cid], EXPECTED[cid])
