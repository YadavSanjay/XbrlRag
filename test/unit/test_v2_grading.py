"""Unit tests for the v2 grader beyond the locked spec (docs/specs/v2-grading.md).

Covers the real legacy suite's dispatch (every sql_derived_v1 case still goes to the same
grader as before v2-grading), and behaviour the spec leaves to the implementer: the
compound's ``incomplete`` flag, a compound without parts, trend evidence, single-digit
period-end days, ``None`` extra keys and unknown decline reasons. Cases are made up.
"""

from __future__ import annotations

import json
import os

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import (
    grade_breakdown,
    grade_case,
    grade_legacy,
    grade_narrative,
    grade_scalar,
    grade_screen,
)
from src.eval.core.graders.v2 import disclosure_checks, grade_v2, v2_subkind
from src.eval.core.system import SystemAnswer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUITE_PATH = os.path.join(REPO_ROOT, "test", "eval", "suites", "sql_derived_v1.json")
M = 1e6

# Today's (origin/main before v2-grading) grader per kind.
_LEGACY_TARGET = {
    "screen": grade_screen,
    "breakdown": grade_breakdown,
    "narrative": grade_narrative,
    "lookup": grade_scalar,
    "compute": grade_scalar,
    "dimensional": grade_scalar,
}


def _suite_cases() -> list[dict]:
    with open(SUITE_PATH, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _answers() -> list[SystemAnswer]:
    # Synthetic answers only: empty, refused, and one with a value, a row and a citation.
    return [
        SystemAnswer(),
        SystemAnswer(text="Cannot answer.", refused=True),
        SystemAnswer(text="DEVA 4", numeric_values=[1.0], rows=[{"ticker": "DEVA",
                     "value": 1.0}], fact_ids=[1]),
    ]


class TestLegacySuiteDispatch:
    def test_suite_is_the_full_legacy_suite(self):
        with open(SUITE_PATH, encoding="utf-8") as f:
            assert len(_suite_cases()) == json.load(f)["n_cases"]

    def test_every_case_keeps_its_legacy_grader(self):
        for d in _suite_cases():
            case = EvalCase.from_dict(d)
            assert v2_subkind(case) is None, case.id
            target = _LEGACY_TARGET[case.kind]
            for ans in _answers():
                got = grade_case(case, ans)
                assert got.checks is None, case.id
                assert "checks" not in got.to_dict(), case.id
                assert got.to_dict() == target(case, ans).to_dict(), case.id

    def test_every_case_round_trips_unchanged(self):
        for d in _suite_cases():
            case = EvalCase.from_dict(d)
            again = EvalCase.from_dict(case.to_dict())
            assert again.to_dict() == case.to_dict(), case.id
            assert case.universe == [] and case.disclosures is None, case.id


def _hx(hx_kind: str, hx_subtype: str | None = None, **fields) -> EvalCase:
    tags = ["split:external", hx_kind] + ([hx_subtype] if hx_subtype else [])
    return EvalCase.from_dict({"id": "u-1", "question": "q?", "tags": tags, **fields})


_RANKING = {"kind": "screen", "universe": ["DEVA", "DEVB", "DEVC"],
            "expected_exclusions": [{"ticker": "DEVC", "reason": "not_tagged"}],
            "expected_rows": [{"entity": "DEVA", "value": 3 * M},
                              {"entity": "DEVB", "value": 2 * M}], "top_n": 2}
_DEPENDENT = {"kind": "breakdown", "expected_rows": [{"entity": "FY2024", "value": 5 * M},
                                                     {"entity": "FY2025", "value": 6 * M}]}
_ROWS = [{"ticker": "DEVA", "value": 3 * M, "fact_id": 1},
         {"ticker": "DEVB", "value": 2 * M, "fact_id": 2}]
_DISCLOSED = "Of the 3 filers DEVA leads DEVB; DEVC is untagged."


class TestCompound:
    def _case(self) -> EvalCase:
        return _hx("compound", **_RANKING, parts=[{"part": "ranking", **_RANKING},
                                                  {"part": "dependent", **_DEPENDENT}])

    def test_wrong_part_that_is_incomplete_marks_the_compound_incomplete(self):
        g = grade_v2(self._case(), SystemAnswer(text=_DISCLOSED, rows=_ROWS,
                                                numeric_values=[5 * M], fact_ids=[1, 2]))
        assert g.grade is Grade.INCORRECT
        assert g.incomplete is True
        assert g.silent_wrong is False

    def test_wrong_ranking_is_not_incomplete(self):
        g = grade_v2(self._case(), SystemAnswer(text=_DISCLOSED, rows=_ROWS[::-1],
                                                numeric_values=[5 * M, 6 * M],
                                                fact_ids=[1, 2]))
        assert g.grade is Grade.INCORRECT
        assert g.incomplete is False
        assert g.silent_wrong is True

    def test_compound_without_parts_grades_as_today_with_empty_checks(self):
        case = _hx("compound", **_RANKING)
        ans = SystemAnswer(text=_DISCLOSED, rows=_ROWS, fact_ids=[1, 2])
        g = grade_v2(case, ans)
        want = grade_legacy(case, ans).to_dict()
        assert g.checks == {}
        assert {k: v for k, v in g.to_dict().items() if k != "checks"} == want


class TestTrendEvidence:
    def test_trend_carries_component_metrics_and_grounding(self):
        case = _hx("cross_period", "trend", **_DEPENDENT, expected_fact_ids=[7, 8])
        g = grade_v2(case, SystemAnswer(numeric_values=[5 * M, 6 * M], fact_ids=[7, 8]))
        assert g.grade is Grade.CORRECT
        assert g.screen_metrics is not None
        assert g.screen_metrics["value_coverage"] == pytest.approx(1.0, abs=0)  # 2 of 2
        assert g.grounding_recall == pytest.approx(1.0, abs=0)  # both facts cited

    def test_uncited_trend_is_partial_but_value_correct(self):
        case = _hx("cross_period", "trend", **_DEPENDENT)
        g = grade_v2(case, SystemAnswer(numeric_values=[5 * M, 6 * M]))
        assert g.grade is Grade.PARTIAL
        assert g.value_correct is True


class TestDisclosureDetails:
    def test_single_digit_day_without_leading_zero(self):
        d = {"id": "v", "question": "q?", "kind": "screen", "tags": ["shape:screen.top"],
             "disclosures": {"universe": "large_caps", "ranked": ["DEVA"], "excluded": [],
                             "period_ends": {"DEVA": "2024-09-05"},
                             "disclose_period_ends": True}}
        case = EvalCase.from_dict(d)
        assert disclosure_checks(case, "Among large caps, Sept 5.")["period_ends"] is False
        got = disclosure_checks(case, "Among large caps; year ends September 5, 2024.")
        assert got == {"universe": True, "exclusions": True, "period_ends": True}
        assert disclosure_checks(case, "Ends 5 Sep 2024.")["period_ends"] is True

    def test_none_extra_keys_read_as_defaults(self):
        case = EvalCase.from_dict({"id": "n", "question": "q?", "universe": None,
                                   "subject": None})
        assert case.universe == []
        assert case.subject is None
        assert "universe" not in case.to_dict()


class TestEdgeCases:
    def test_unknown_decline_reason_is_partial_when_declined(self):
        case = _hx("honesty", "decline", kind="narrative", decline_reason="other")
        g = grade_v2(case, SystemAnswer(text="Not tagged.", refused=True))
        assert g.grade is Grade.PARTIAL
        assert g.checks == {"declined": True, "no_number": True, "reason": False}

    def test_peer_relative_without_subject_is_incorrect(self):
        case = EvalCase.from_dict({"id": "p", "question": "q?", "kind": "screen",
                                   "tags": ["shape:peer_relative"]})
        g = grade_v2(case, SystemAnswer(numeric_values=[2.0], fact_ids=[1]))
        assert g.grade is Grade.INCORRECT
        assert g.checks == {"rank": False}

    def test_ambiguous_without_assumptions_never_passes(self):
        case = _hx("honesty", "ambiguous", kind="narrative")
        g = grade_v2(case, SystemAnswer(text="Anything."))
        assert g.grade is Grade.INCORRECT
        assert g.checks == {}
