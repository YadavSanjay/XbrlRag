"""WS-9 / E1 — unit tests for the eval core framework.

No backends: every grader is a pure function, and the runner is exercised through a
stub :class:`SystemUnderTest`. Live-pipeline coverage stays in
``test/integration/test_golden_eval_live.py``.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from src.eval.core import (
    CallableSystem,
    Coverage,
    EvalCase,
    EvalRunner,
    EvalSuite,
    Grade,
    RunnerConfig,
    SystemAnswer,
    compare_runs,
    grade_case,
)
from src.eval.core.coverage import classify, corpus_from_pairs, partition
from src.eval.core.graders.base import percentile
from src.eval.core.graders.citation import grade_grounding
from src.eval.core.graders.numeric import (
    all_fact_ids,
    all_numeric_values,
    close_enough,
    has_fact_citation,
    screen_rows,
)
from src.eval.core.graders.screen import compare_screen, spearman
from src.eval.core.report import CaseResult, Metrics, paraphrase_stability
from src.eval.core.runner import _Budget

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resp(steps, narrative_chunks=None):
    """A minimal XbrlRagResponse-shaped dict (the extractors accept dicts)."""
    return {
        "execution_result": {"status": "OK", "steps": steps},
        "narrative_chunks": narrative_chunks or [],
    }


def _lookup_step(value, fact_id=None, status="OK"):
    step = {"step_id": "s1", "op": "lookup", "status": status, "value": value}
    if fact_id is not None:
        step["citation"] = {"fact_id": fact_id}
    return step


def _case(**kw):
    base = dict(
        id="c1", question="q?", kind="lookup", expected_value=100.0, rel_tol=1e-4
    )
    base.update(kw)
    return EvalCase(**base)


def _answer(**kw):
    return SystemAnswer(**kw)


# ---------------------------------------------------------------------------
# EvalCase / EvalSuite
# ---------------------------------------------------------------------------


class TestEvalCase:
    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="unknown case kind"):
            EvalCase(id="x", question="q", kind="bogus")

    def test_accepts_golden_query_spelling(self):
        """GoldenCase JSON uses `query`; EvalCase uses `question`."""
        case = EvalCase.from_dict({"id": "x", "query": "What was revenue?"})
        assert case.question == "What was revenue?"

    def test_round_trips_through_dict(self):
        case = _case(
            id="rt",
            expected_fact_ids=[1, 2],
            tags=["revenue"],
            ticker="NVDA",
            fy=2026,
            alt_values=[0.5],
            coverage=Coverage.PARTIAL,
            order_sensitive=False,
        )
        again = EvalCase.from_dict(case.to_dict())
        assert again.expected_fact_ids == [1, 2]
        assert again.coverage is Coverage.PARTIAL
        assert again.order_sensitive is False
        assert again.alt_values == [0.5]

    def test_not_meaningful_flag_round_trips_and_defaults_off(self):
        flagged = _case(id="nm", expect_not_meaningful=True)
        assert flagged.to_dict()["expect_not_meaningful"] is True
        assert EvalCase.from_dict(flagged.to_dict()).expect_not_meaningful is True
        plain = _case(id="plain")
        assert "expect_not_meaningful" not in plain.to_dict()  # default omitted
        assert EvalCase.from_dict(plain.to_dict()).expect_not_meaningful is False

    def test_targets_includes_alternate_forms(self):
        assert _case(expected_value=0.75, alt_values=[75.0]).targets() == [0.75, 75.0]

    def test_coverage_string_is_coerced(self):
        assert EvalCase(id="x", question="q", coverage="OUT_OF_CORPUS").coverage is (
            Coverage.OUT_OF_CORPUS
        )


class TestEvalSuite:
    def test_load_and_filter(self, tmp_path):
        suite = EvalSuite(
            name="t",
            version="v1",
            cases=[
                _case(id="a", kind="lookup", tags=["revenue"], ticker="NVDA"),
                _case(
                    id="b",
                    kind="screen",
                    tags=["margin"],
                    ticker="AAPL",
                    expected_value=None,
                    expected_rows=[],
                ),
            ],
        )
        path = tmp_path / "suite.json"
        suite.save(str(path))
        loaded = EvalSuite.load(str(path))

        assert len(loaded) == 2
        assert loaded.qualified_name == "t_v1"
        assert [c.id for c in loaded.filter(kinds=["screen"]).cases] == ["b"]
        assert [c.id for c in loaded.filter(tags=["revenue"]).cases] == ["a"]
        assert [c.id for c in loaded.filter(tickers=["aapl"]).cases] == ["b"]
        assert set(loaded.by_kind()) == {"lookup", "screen"}

    def test_reads_ws2_golden_set_shape(self, tmp_path):
        """The WS-2 file is {"version": "xbrl_golden_v1", "cases": [{"query": ...}]}."""
        path = tmp_path / "golden.json"
        path.write_text(
            json.dumps(
                {
                    "version": "xbrl_golden_v1",
                    "cases": [{"id": "g1", "query": "Revenue?", "expected_value": 1.0}],
                }
            ),
            encoding="utf-8",
        )
        suite = EvalSuite.load(str(path))
        assert suite.name == "xbrl_golden"
        assert suite.version == "v1"
        assert suite.cases[0].question == "Revenue?"


# ---------------------------------------------------------------------------
# extraction (moved from WS-2 numeric.py, extended)
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_close_enough_zero_uses_absolute_band(self):
        assert close_enough(0.5, 0.0)
        assert not close_enough(1.5, 0.0)

    def test_only_ok_steps_contribute_values(self):
        resp = _resp([_lookup_step(10.0), _lookup_step(99.0, status="FAILED")])
        assert all_numeric_values(resp) == [10.0]

    def test_fact_ids_from_citation_components_and_rows(self):
        resp = _resp(
            [
                _lookup_step(1.0, fact_id=11),
                {
                    "op": "compute",
                    "status": "OK",
                    "value": 2.0,
                    "component_fact_ids": [12, "13"],
                },
                {
                    "op": "screen",
                    "status": "OK",
                    "rows": [
                        {"ticker": "NVDA", "value": 3.0, "fact_id": 14},
                        {"ticker": "AAPL", "value": 2.0, "citation": {"fact_id": 15}},
                    ],
                },
            ]
        )
        assert all_fact_ids(resp) == [11, 12, 13, 14, 15]

    def test_step_ids_are_not_fact_ids(self):
        """`fact_ids` in the synthesizer schema may hold step ids like 's1' — those
        are not grounding evidence."""
        resp = _resp(
            [
                {
                    "op": "lookup",
                    "status": "OK",
                    "value": 1.0,
                    "citation": {"fact_id": "s1"},
                }
            ]
        )
        assert all_fact_ids(resp) == []
        assert has_fact_citation(resp) is True  # WS-2 semantics: a citation exists

    def test_failed_steps_contribute_no_fact_ids(self):
        resp = _resp([_lookup_step(1.0, fact_id=11, status="FAILED")])
        assert all_fact_ids(resp) == []

    def test_component_fact_ids_as_a_JSONB_OBJECT_are_extracted(self):
        """mv_derived_metrics stores {"revenue": 77, "cost_of_revenue": 80}.
        Iterating the object yields its KEYS, so the evidence was silently dropped."""
        resp = _resp(
            [
                {
                    "op": "compute",
                    "status": "OK",
                    "value": 0.71,
                    "component_fact_ids": {"revenue": 77, "cost_of_revenue": 80},
                }
            ]
        )
        assert sorted(all_fact_ids(resp)) == [77, 80]

    def test_derived_metric_screen_rows_cite_via_component_fact_ids(self):
        """A ratio has no single fact, so the screen gives the row a synthetic id and
        puts the audited facts in per-row component_fact_ids. Missing those read a
        correctly-cited ranking as uncited."""
        resp = _resp(
            [
                {
                    "op": "screen",
                    "status": "OK",
                    "rows": [
                        {
                            "ticker": "NVDA",
                            "value": 0.71,
                            "fact_id": "GrossMarginPct:1045810:2026",
                            "citation": {"fact_id": None},
                            "component_fact_ids": {
                                "revenue": 77,
                                "cost_of_revenue": 80,
                            },
                        },
                    ],
                }
            ]
        )
        assert sorted(all_fact_ids(resp)) == [77, 80]
        assert has_fact_citation(resp) is True

    def test_screen_rows_extracted_in_plan_order(self):
        resp = _resp(
            [
                {
                    "op": "screen",
                    "status": "OK",
                    "rows": [{"ticker": "NVDA"}, {"ticker": "AAPL"}],
                }
            ]
        )
        assert [r["ticker"] for r in screen_rows(resp)] == ["NVDA", "AAPL"]


# ---------------------------------------------------------------------------
# scalar grading
# ---------------------------------------------------------------------------


class TestScalarGrading:
    def test_correct_when_value_matches_and_cited(self):
        g = grade_case(_case(), _answer(numeric_values=[100.0], fact_ids=[7]))
        assert g.grade is Grade.CORRECT and g.passed and g.matched_value == 100.0

    def test_partial_when_value_matches_but_uncited(self):
        """Right number, unaudited — meaningfully different from a wrong number."""
        g = grade_case(_case(), _answer(numeric_values=[100.0]))
        assert g.grade is Grade.PARTIAL and not g.passed
        assert "no fact_id citation" in g.reason

    def test_incorrect_when_value_wrong(self):
        g = grade_case(_case(), _answer(numeric_values=[42.0], fact_ids=[7]))
        assert g.grade is Grade.INCORRECT and not g.passed

    def test_refused_when_nothing_produced_and_declined(self):
        g = grade_case(_case(), _answer(refused=True, hedged=True))
        assert g.grade is Grade.REFUSED

    def test_incorrect_when_nothing_produced_without_declining(self):
        assert grade_case(_case(), _answer()).grade is Grade.INCORRECT

    def test_error_short_circuits(self):
        g = grade_case(_case(), _answer(error="boom", numeric_values=[100.0]))
        assert g.grade is Grade.ERROR and not g.passed

    def test_alternate_form_accepted(self):
        case = _case(expected_value=0.75, alt_values=[75.0])
        assert grade_case(case, _answer(numeric_values=[75.0], fact_ids=[1])).passed

    def test_grounding_mismatch_downgrades_to_partial(self):
        """Right number, wrong source — the distinction WS-2 could not make."""
        case = _case(expected_fact_ids=[11])
        g = grade_case(case, _answer(numeric_values=[100.0], fact_ids=[99]))
        assert g.grade is Grade.PARTIAL
        assert g.grounding_recall == 0.0

    def test_extra_citations_allowed_by_default(self):
        case = _case(expected_fact_ids=[11])
        g = grade_case(case, _answer(numeric_values=[100.0], fact_ids=[11, 12]))
        assert g.grade is Grade.CORRECT
        assert g.grounding_precision == pytest.approx(0.5)

    def test_require_exact_fact_rejects_extras(self):
        case = _case(expected_fact_ids=[11], require_exact_fact=True)
        assert grade_case(
            case, _answer(numeric_values=[100.0], fact_ids=[11, 12])
        ).grade is (Grade.PARTIAL)

    def test_unlabelled_case_is_partial_not_correct(self):
        case = _case(expected_value=None)
        g = grade_case(case, _answer(numeric_values=[1.0], fact_ids=[1]))
        assert g.grade is Grade.PARTIAL and not g.passed


class TestSilentWrong:
    def test_confident_cited_wrong_number_is_silent_wrong(self):
        g = grade_case(_case(), _answer(numeric_values=[42.0], fact_ids=[7]))
        assert g.silent_wrong is True

    def test_hedged_wrong_answer_is_not_silent(self):
        # A hedged miss is its own grade (eval-grader-honesty C2), not INCORRECT.
        g = grade_case(
            _case(), _answer(numeric_values=[42.0], fact_ids=[7], hedged=True)
        )
        assert g.grade is Grade.HEDGED_MISS and g.silent_wrong is False

    def test_uncited_wrong_answer_is_not_silent_wrong(self):
        assert grade_case(_case(), _answer(numeric_values=[42.0])).silent_wrong is False

    def test_refusal_is_never_silent_wrong(self):
        assert grade_case(_case(), _answer(refused=True)).silent_wrong is False

    def test_an_incomplete_breakdown_is_not_silent_wrong(self):
        """Silent-wrong is the FAITHFULNESS gate — a confident, cited, false number.
        A breakdown that returns real, correctly-cited facts at a different
        decomposition level is a RELEVANCE failure. NVIDIA FY2025 answered Data
        Center 115,186M where the truth wanted Compute 102,196M + Networking
        12,990M, which sum to exactly that. Conflating the two makes the gate cry
        wolf."""
        case = EvalCase(
            id="bd",
            question="Break down revenue by segment.",
            kind="breakdown",
            expected_value=None,
            expected_rows=[
                {"entity": "Compute", "value": 102196.0},
                {"entity": "Networking", "value": 12990.0},
                {"entity": "Gaming", "value": 11350.0},
            ],
            expected_fact_ids=[1, 2, 3],
        )
        # Answers with the PARENT (real, cited) plus one true child.
        g = grade_case(
            case, _answer(numeric_values=[115186.0, 11350.0], fact_ids=[9, 3])
        )
        assert g.grade is Grade.INCORRECT  # it did not cover what was asked
        assert g.incomplete is True
        assert g.silent_wrong is False  # but it asserted no false number

    def test_a_wrong_scalar_is_still_silent_wrong(self):
        """The refinement must not defang the gate where it genuinely applies."""
        g = grade_case(_case(), _answer(numeric_values=[42.0], fact_ids=[7]))
        assert g.incomplete is False and g.silent_wrong is True


# ---------------------------------------------------------------------------
# grounding
# ---------------------------------------------------------------------------


class TestGrounding:
    def test_unlabelled_grounding_is_satisfied_not_failed(self):
        r = grade_grounding([], [1, 2])
        assert r.satisfied and r.precision is None and r.recall is None

    def test_containment_satisfied_with_extras(self):
        r = grade_grounding([1], [1, 2])
        assert r.satisfied and r.recall == 1.0 and r.extra == [2]

    def test_missing_expected_fact_is_unsatisfied(self):
        r = grade_grounding([1, 2], [1])
        assert not r.satisfied and r.missing == [2]

    def test_no_produced_ids_scores_zero_precision(self):
        r = grade_grounding([1], [])
        assert r.precision == 0.0 and not r.satisfied


class TestGroundingEquivalenceClasses:
    """A filing tags one number on several rows, so citing a different duplicate is a
    correct citation. Demanding a specific surrogate fact_id measured row-selection
    convention and turned 48 correct baseline answers into PARTIAL."""

    CLASS = [[15488, 15811, 16207]]

    def test_any_member_of_the_class_satisfies_it(self):
        r = grade_grounding([16207], [15488], fact_id_classes=self.CLASS)
        assert r.satisfied and r.recall == 1.0

    def test_the_canonical_member_still_satisfies_it(self):
        assert grade_grounding([16207], [16207], fact_id_classes=self.CLASS).satisfied

    def test_an_unrelated_fact_still_fails(self):
        r = grade_grounding([16207], [99999], fact_id_classes=self.CLASS)
        assert not r.satisfied and r.missing == [
            15488
        ]  # canonical id of the unmet class

    def test_duplicates_cannot_satisfy_two_distinct_requirements(self):
        """Citing two members of ONE class must not evidence two required facts."""
        r = grade_grounding([1, 2], [1, 10], fact_id_classes=[[1, 10], [2, 20]])
        assert not r.satisfied and r.recall == 0.5 and r.missing == [2]

    def test_recall_counts_classes_not_ids(self):
        r = grade_grounding([1, 2], [1, 10, 2], fact_id_classes=[[1, 10], [2, 20]])
        assert r.recall == 1.0 and r.satisfied

    def test_falls_back_to_singletons_when_no_classes_given(self):
        """Suites built before equivalence classes existed must still grade."""
        assert grade_grounding([1], [1]).satisfied
        assert not grade_grounding([1], [2]).satisfied

    def test_case_derives_singleton_classes_by_default(self):
        assert _case(expected_fact_ids=[7, 8]).grounding_classes() == [[7], [8]]

    def test_case_prefers_explicit_classes(self):
        case = _case(expected_fact_ids=[7], fact_id_classes=[[7, 9]])
        assert case.grounding_classes() == [[7, 9]]

    def test_classes_survive_serialisation(self):
        case = _case(expected_fact_ids=[7], fact_id_classes=[[7, 9]])
        assert EvalCase.from_dict(case.to_dict()).grounding_classes() == [[7, 9]]

    def test_grade_case_accepts_an_equivalent_citation(self):
        """End to end: the exact shape that produced 48 false PARTIALs."""
        case = _case(expected_fact_ids=[16207], fact_id_classes=[[15488, 15811, 16207]])
        g = grade_case(case, _answer(numeric_values=[100.0], fact_ids=[15488]))
        assert g.grade is Grade.CORRECT


# ---------------------------------------------------------------------------
# screen grading
# ---------------------------------------------------------------------------


class TestScreenGrading:
    @staticmethod
    def _rows(pairs):
        return [{"ticker": t, "value": v} for t, v in pairs]

    def test_identical_ranking_passes(self):
        rows = self._rows([("NVDA", 0.75), ("AAPL", 0.46)])
        case = EvalCase(
            id="s",
            question="q",
            kind="screen",
            expected_rows=rows,
            expected_value=None,
            rel_tol=1e-3,
        )
        g = grade_case(case, _answer(rows=rows, fact_ids=[1]))
        assert g.grade is Grade.CORRECT
        assert g.screen_metrics["top1_exact"] is True
        assert g.screen_metrics["set_overlap"] == 1.0

    def test_wrong_winner_fails(self):
        case = EvalCase(
            id="s",
            question="q",
            kind="screen",
            expected_value=None,
            expected_rows=self._rows([("NVDA", 0.75), ("AAPL", 0.46)]),
        )
        g = grade_case(
            case,
            _answer(rows=self._rows([("AAPL", 0.46), ("NVDA", 0.75)]), fact_ids=[1]),
        )
        assert g.grade is Grade.INCORRECT and "top-1 mismatch" in g.reason

    def test_no_rows_is_incorrect(self):
        case = EvalCase(
            id="s",
            question="q",
            kind="screen",
            expected_value=None,
            expected_rows=self._rows([("NVDA", 1.0)]),
        )
        assert grade_case(case, _answer()).grade is Grade.INCORRECT

    def test_uncited_ranking_is_partial(self):
        rows = self._rows([("NVDA", 1.0), ("AAPL", 0.5)])
        case = EvalCase(
            id="s", question="q", kind="screen", expected_value=None, expected_rows=rows
        )
        assert grade_case(case, _answer(rows=rows)).grade is Grade.PARTIAL

    def test_value_disagreement_fails_even_with_right_order(self):
        case = EvalCase(
            id="s",
            question="q",
            kind="screen",
            expected_value=None,
            rel_tol=1e-3,
            expected_rows=self._rows([("NVDA", 0.75), ("AAPL", 0.46)]),
        )
        g = grade_case(
            case,
            _answer(rows=self._rows([("NVDA", 0.99), ("AAPL", 0.11)]), fact_ids=[1]),
        )
        assert g.grade is Grade.INCORRECT and "value agreement" in g.reason

    def test_spearman_needs_two_shared_entities(self):
        assert spearman(["A"], ["A"]) is None

    def test_spearman_detects_reversal(self):
        assert spearman(["A", "B", "C"], ["C", "B", "A"]) == pytest.approx(-1.0)

    def test_top_n_truncates_comparison(self):
        m = compare_screen(
            self._rows([("A", 3), ("B", 2), ("C", 1)]),
            self._rows([("A", 3), ("B", 2), ("Z", 0)]),
            top_n=2,
        )
        assert m.set_overlap == 1.0 and m.n_produced == 2

    def test_cik_used_when_ticker_absent(self):
        m = compare_screen(
            [{"cik": 320193, "value": 1.0}], [{"cik": 320193, "value": 1.0}]
        )
        assert m.top1_exact is True


# ---------------------------------------------------------------------------
# narrative grading
# ---------------------------------------------------------------------------


class TestNarrativeGrading:
    def test_unlabelled_narrative_is_partial_not_correct(self):
        """Phase 0 has no LLM judge — an ungraded case must not inflate accuracy."""
        case = EvalCase(id="n", question="q", kind="narrative", expected_value=None)
        g = grade_case(case, _answer(text="Some prose.", chunk_ids=["c1"]))
        assert g.grade is Grade.PARTIAL and not g.passed
        assert "Phase 3" in g.reason

    def test_chunk_recall_passes_when_expected_chunk_retrieved(self):
        case = EvalCase(
            id="n",
            question="q",
            kind="narrative",
            expected_value=None,
            expected_chunk_ids=["c1", "c2"],
        )
        g = grade_case(case, _answer(chunk_ids=["c1", "c9"]))
        assert g.grade is Grade.CORRECT and g.grounding_recall == pytest.approx(0.5)

    def test_no_expected_chunk_retrieved_is_incorrect(self):
        case = EvalCase(
            id="n",
            question="q",
            kind="narrative",
            expected_value=None,
            expected_chunk_ids=["c1"],
        )
        assert grade_case(case, _answer(chunk_ids=["c9"])).grade is Grade.INCORRECT


# ---------------------------------------------------------------------------
# metrics / report
# ---------------------------------------------------------------------------


def _result(case_id, grade_obj, **kw):
    case = _case(
        id=case_id,
        **{
            k: v
            for k, v in kw.items()
            if k in {"tags", "kind", "paraphrase_group", "ticker"}
        },
    )
    return CaseResult(
        case=case,
        grade=grade_obj,
        latency_ms=kw.get("latency_ms", 0.0),
        cost_usd=kw.get("cost_usd", 0.0),
    )


class TestMetrics:
    def _graded(self, case_id, answer, **case_kw):
        case = _case(id=case_id, **case_kw)
        return CaseResult(
            case=case, grade=grade_case(case, answer), cost_usd=0.01, latency_ms=100.0
        )

    def test_empty_metrics_are_zero_not_crash(self):
        m = Metrics.compute([])
        assert m.n == 0 and m.accuracy == 0.0

    def test_headline_rates(self):
        results = [
            self._graded("a", _answer(numeric_values=[100.0], fact_ids=[1])),  # CORRECT
            self._graded(
                "b", _answer(numeric_values=[42.0], fact_ids=[1])
            ),  # INCORRECT+silent
            self._graded("c", _answer(refused=True)),  # REFUSED
            self._graded("d", _answer(error="boom")),  # ERROR
        ]
        m = Metrics.compute(results)
        assert m.n == 4
        assert m.accuracy == 0.25
        assert m.silent_wrong_rate == 0.25
        assert m.refusal_rate == 0.25
        assert m.error_rate == 0.25
        assert m.total_cost_usd == pytest.approx(0.04)

    def test_grounding_averages_skip_unasserted_cases(self):
        """A case with no expected fact_ids must not drag the mean toward zero."""
        results = [
            self._graded("a", _answer(numeric_values=[100.0], fact_ids=[1])),
            self._graded(
                "b",
                _answer(numeric_values=[100.0], fact_ids=[1]),
                expected_fact_ids=[1],
            ),
        ]
        assert Metrics.compute(results).grounding_recall == 1.0

    def test_percentiles(self):
        assert percentile([], 0.5) is None
        assert percentile([10.0], 0.95) == 10.0
        assert percentile([0.0, 10.0], 0.5) == 5.0


class TestParaphraseStability:
    def test_none_when_no_groups(self):
        assert paraphrase_stability([]) is None

    def test_single_member_groups_excluded(self):
        g = grade_case(_case(), _answer(numeric_values=[100.0], fact_ids=[1]))
        assert paraphrase_stability([_result("a", g, paraphrase_group="grp")]) is None

    def test_disagreeing_group_scores_zero(self):
        ok = grade_case(_case(), _answer(numeric_values=[100.0], fact_ids=[1]))
        bad = grade_case(_case(), _answer(numeric_values=[1.0], fact_ids=[1]))
        results = [
            _result("a", ok, paraphrase_group="g"),
            _result("b", bad, paraphrase_group="g"),
        ]
        assert paraphrase_stability(results) == 0.0

    def test_agreeing_group_scores_one(self):
        ok = grade_case(_case(), _answer(numeric_values=[100.0], fact_ids=[1]))
        results = [
            _result("a", ok, paraphrase_group="g"),
            _result("b", ok, paraphrase_group="g"),
        ]
        assert paraphrase_stability(results) == 1.0


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class _StubSystem:
    """Answers by lookup table; records concurrency observed per kind."""

    name = "stub"

    def __init__(self, answers=None, delay=0.0):
        self._answers = answers or {}
        self._delay = delay
        self.calls = []
        self.max_concurrent_by_kind = {}
        self._active = {}
        self._lock = threading.Lock()

    def answer(self, case):
        with self._lock:
            self._active[case.kind] = self._active.get(case.kind, 0) + 1
            peak = max(
                self.max_concurrent_by_kind.get(case.kind, 0), self._active[case.kind]
            )
            self.max_concurrent_by_kind[case.kind] = peak
            self.calls.append(case.id)
        try:
            if self._delay:
                time.sleep(self._delay)
            return self._answers.get(
                case.id,
                SystemAnswer(numeric_values=[100.0], fact_ids=[1], cost_usd=0.01),
            )
        finally:
            with self._lock:
                self._active[case.kind] -= 1


class TestEvalRunner:
    def test_grades_every_case(self):
        cases = [_case(id=f"c{i}") for i in range(5)]
        report = EvalRunner(RunnerConfig(max_workers=4)).run(
            cases, _StubSystem(), suite="t"
        )
        assert report.status == "OK"
        assert report.metrics.n == 5 and report.metrics.accuracy == 1.0
        assert report.n_skipped == 0

    def test_raising_system_yields_error_grade_not_crash(self):
        class Boom:
            name = "boom"

            def answer(self, case):
                raise RuntimeError("kaboom")

        report = EvalRunner(RunnerConfig(max_workers=2)).run([_case(id="a")], Boom())
        assert report.metrics.n_error == 1
        assert "kaboom" in report.results[0].grade.reason

    def test_narrative_is_serialized(self):
        """Narrative reranks on CPU and has segfaulted under unbounded batching."""
        cases = [
            EvalCase(
                id=f"n{i}",
                question="q",
                kind="narrative",
                expected_value=None,
                expected_chunk_ids=["c1"],
            )
            for i in range(4)
        ]
        system = _StubSystem(delay=0.01)
        EvalRunner(RunnerConfig(max_workers=4)).run(cases, system)
        assert system.max_concurrent_by_kind["narrative"] == 1

    def test_non_narrative_runs_concurrently(self):
        cases = [_case(id=f"c{i}") for i in range(6)]
        system = _StubSystem(delay=0.05)
        EvalRunner(RunnerConfig(max_workers=4)).run(cases, system)
        assert system.max_concurrent_by_kind["lookup"] > 1

    def test_cost_budget_aborts_and_reports_skips(self):
        cases = [_case(id=f"c{i}") for i in range(20)]
        system = _StubSystem(delay=0.005)
        report = EvalRunner(RunnerConfig(max_workers=2, max_cost_usd=0.03)).run(
            cases, system
        )
        assert report.status == "ABORTED"
        assert report.n_skipped > 0
        assert report.metrics.n + report.n_skipped == 20

    def test_resume_skips_already_graded_cases(self):
        cases = [_case(id=f"c{i}") for i in range(4)]
        runner = EvalRunner(RunnerConfig(max_workers=2))
        first = runner.run(cases[:2], _StubSystem(), suite="t")

        system = _StubSystem()
        second = runner.run(cases, system, suite="t", prior=first)
        assert sorted(system.calls) == ["c2", "c3"]
        assert second.metrics.n == 4

    def test_run_suite_carries_suite_identity(self):
        suite = EvalSuite(
            name="t", version="v2", cases=[_case(id="a")], corpus_fingerprint="pm:abc"
        )
        report = EvalRunner().run_suite(suite, _StubSystem())
        assert report.suite == "t" and report.suite_version == "v2"
        assert report.corpus_fingerprint == "pm:abc"

    def test_callable_system_adapter(self):
        system = CallableSystem(
            "fn", lambda q: SystemAnswer(numeric_values=[100.0], fact_ids=[1])
        )
        report = EvalRunner().run([_case(id="a")], system)
        assert report.metrics.accuracy == 1.0
        assert report.system == "fn"

    def test_budget_without_limit_never_exhausts(self):
        budget = _Budget(None)
        budget.charge(1e6)
        assert budget.exhausted is False


class TestRunComparison:
    def _report(self, run_id, outcomes, fingerprint="pm:abc"):
        answers = {
            cid: SystemAnswer(numeric_values=[100.0 if ok else 1.0], fact_ids=[1])
            for cid, ok in outcomes.items()
        }
        cases = [_case(id=cid) for cid in outcomes]
        report = EvalRunner().run(cases, _StubSystem(answers), run_id=run_id)
        report.corpus_fingerprint = fingerprint
        return report

    def test_detects_regressions_and_improvements(self):
        a = self._report("a", {"c1": True, "c2": False})
        b = self._report("b", {"c1": False, "c2": True})
        cmp = compare_runs(a, b)
        assert cmp.regressions == ["c1"] and cmp.improvements == ["c2"]
        assert cmp.accuracy_delta == 0.0

    def test_flags_fingerprint_mismatch(self):
        a = self._report("a", {"c1": True}, fingerprint="pm:aaa")
        b = self._report("b", {"c1": True}, fingerprint="pm:bbb")
        assert compare_runs(a, b).fingerprint_mismatch is True

    def test_disjoint_case_sets_reported(self):
        a = self._report("a", {"c1": True})
        b = self._report("b", {"c2": True})
        cmp = compare_runs(a, b)
        assert cmp.only_in_a == ["c1"] and cmp.only_in_b == ["c2"]


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


class TestCoverage:
    CORPUS = corpus_from_pairs([("NVDA", 2026), ("AAPL", 2024)])

    def test_exact_match_in_corpus(self):
        assert (
            classify(_case(ticker="NVDA", fy=2026), self.CORPUS) is Coverage.IN_CORPUS
        )

    def test_known_ticker_unknown_year_is_partial(self):
        assert (
            classify(_case(ticker="NVDA", fy=1999), self.CORPUS)
            is Coverage.OUT_OF_CORPUS
        )
        assert classify(_case(ticker="NVDA", fy=None), self.CORPUS) is Coverage.PARTIAL

    def test_cross_filer_screen_is_unknown_not_out_of_corpus(self):
        """A screen legitimately names no company — it must not read as uncovered."""
        assert classify(_case(ticker=None, fy=None), self.CORPUS) is Coverage.UNKNOWN

    def test_partition_stamps_cases_and_reports_pct(self):
        cases = [
            _case(id="a", ticker="NVDA", fy=2026),
            _case(id="b", ticker="XOM", fy=2024),
        ]
        report = partition(cases, self.CORPUS)
        assert cases[0].coverage is Coverage.IN_CORPUS
        assert cases[1].coverage is Coverage.OUT_OF_CORPUS
        assert report.coverage_pct == 0.5
        assert report.to_dict()["out_of_corpus"] == 1
