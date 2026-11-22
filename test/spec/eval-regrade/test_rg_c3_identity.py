"""C3: re-grading against the unchanged suite reproduces the stored run."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.regrade import regrade_report
from src.eval.core.runner import EvalRunner, RunnerConfig
from src.eval.core.system import SystemAnswer


def _regrade(source, cases, **kw):
    return regrade_report(
        source, cases, suite="xbrl_v2_dev", suite_version="1", corpus_fingerprint="pm:source",
        **kw,
    )


class _ScriptedSystem:
    """A fake system (the LLM boundary): a fixed answer per question."""

    name = "xbrlrag_full"

    def __init__(self, by_question: dict[str, SystemAnswer]) -> None:
        self._by_question = by_question

    def answer(self, case: EvalCase) -> SystemAnswer:
        return self._by_question[case.question]


def _screen_case(case_id: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        question="List the top 3 companies by revenue in fiscal 2024, ranked.",
        kind="screen",
        expected_rows=[
            {"ticker": "AAA", "value": 300.0},
            {"ticker": "BBB", "value": 200.0},
            {"ticker": "CCC", "value": 100.0},
        ],
        top_n=3,
        tags=["split:dev"],
    )


@pytest.mark.criterion("C3")
def test_a_runner_produced_run_regrades_to_itself(lookup_case, answer):
    # oracle: the stored run itself (identity, requirement 7)
    cases = [
        lookup_case("r1", 100.0),
        lookup_case("r2", 200.0),
        lookup_case("r3", 300.0),
        lookup_case("r4", 400.0),
        _screen_case("s1"),
    ]
    answers = {
        cases[0].question: answer([100.0], query_id="q-1", cost_usd=0.01, latency_ms=10.0),
        cases[1].question: answer([250.0], query_id="q-2", cost_usd=0.02, latency_ms=20.0),
        cases[2].question: answer([], refused=True, cited=False, latency_ms=30.0),
        cases[3].question: answer([400.0], cited=False, latency_ms=40.0),
        cases[4].question: SystemAnswer(
            text="AAA, BBB, CCC",
            rows=[
                {"ticker": "AAA", "value": 300.0, "fact_id": 1},
                {"ticker": "BBB", "value": 200.0, "fact_id": 2},
                {"ticker": "CCC", "value": 100.0, "fact_id": 3},
            ],
            fact_ids=[1, 2, 3],
            latency_ms=50.0,
        ),
    }
    run = EvalRunner(RunnerConfig(max_workers=1)).run(
        cases, _ScriptedSystem(answers), suite="xbrl_v2_dev", suite_version="1",
        corpus_fingerprint="pm:source", run_id="eval-bbbbbbbbbbbb",
    )
    source = json.loads(json.dumps(run.to_dict(), default=str))
    regraded = _regrade(source, cases)

    stored = {r["case_id"]: r["grade"] for r in source["results"]}
    assert {r.case.id: r.grade.grade.value for r in regraded.results} == stored
    assert stored == {
        "r1": "CORRECT", "r2": "INCORRECT", "r3": "REFUSED", "r4": "PARTIAL", "s1": "CORRECT",
    }
    assert regraded.metrics.to_dict() == source["metrics"]
    assert regraded.config["regrade"]["n_grade_changed"] == 0
    assert [r.case.id for r in regraded.results] == [r["case_id"] for r in source["results"]]


_KINDS = {
    "right": ([1.0], {}, True),  # the key itself, cited: CORRECT
    "wrong": ([7.5], {}, True),  # INCORRECT, silent-wrong
    "uncited": ([1.0], {}, False),  # PARTIAL, value-correct
    "hedged": ([7.5], {"hedged": True}, True),  # HEDGED_MISS
    "refused": ([], {"refused": True}, False),  # REFUSED
    "error": ([], {"error": "TimeoutError('read')"}, False),  # ERROR
}


@pytest.mark.criterion("C3")
@settings(max_examples=60)
@given(
    kinds=st.lists(st.sampled_from(sorted(_KINDS)), min_size=1, max_size=12),
    via_payloads=st.booleans(),
)
def test_any_run_regrades_to_its_stored_grades(
    lookup_case, answer, build_report, as_json_source, as_payload_source, kinds, via_payloads
):
    # oracle: the stored grades and metrics (identity, requirement 7)
    pairs = []
    for i, kind in enumerate(kinds):
        key = 1000.0 + i
        values, flags, cited = _KINDS[kind]
        stated = [key * v for v in values]
        pairs.append((lookup_case(f"c{i}", key), answer(stated, cited=cited, **flags)))
    report = build_report(pairs)
    source = as_payload_source(report) if via_payloads else as_json_source(report)
    cases = [c for c, _ in pairs]

    regraded = _regrade(source, cases)
    assert [r.grade.to_dict() for r in regraded.results] == [
        r.grade.to_dict() for r in report.results
    ]
    assert regraded.metrics.to_dict() == report.metrics.to_dict()
    assert regraded.config["regrade"]["n_grade_changed"] == 0
    assert regraded.config["regrade"]["n_regraded"] == len(kinds)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_regrading_a_regrade_is_idempotent(lookup_case, answer, build_report, as_json_source,
                                           rekey):
    # oracle: requirement 7 — a second re-grade with the same cases changes nothing
    cases = [lookup_case("i1", 100.0), lookup_case("i2", 200.0)]
    source = as_json_source(
        build_report([(cases[0], answer([100.0])), (cases[1], answer([250.0]))])
    )
    new_cases = [cases[0], rekey(cases[1], 250.0)]
    first = _regrade(source, new_cases, run_id="eval-111111111111")
    second = _regrade(
        json.loads(json.dumps(first.to_dict(), default=str)), new_cases,
        run_id="eval-222222222222",
    )
    assert [r.grade.to_dict() for r in second.results] == [
        r.grade.to_dict() for r in first.results
    ]
    assert first.config["regrade"]["n_grade_changed"] == 1
    assert second.config["regrade"]["n_grade_changed"] == 0
    assert second.config["regrade"]["source_run_id"] == "eval-111111111111"
    assert second.config["regrade"]["root_run_id"] == "eval-aaaaaaaaaaaa"
