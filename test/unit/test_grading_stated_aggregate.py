"""Unit tests for the grading-stated-aggregate wiring outside the locked spec tests.

Covers decisions the spec leaves to the implementer (docs/specs/grading-stated-aggregate.md):
"exactly one stated number matching a row value" counts distinct values, not occurrences
(decided on the spec PR); the rule applies to H-X ``cross_filer`` cases as to v2 ones; a
typographic apostrophe declines like a straight one. Grading is pure: nothing is faked.
"""

from __future__ import annotations

import pytest

from src.eval.core.answer_parsing import aggregate_sentences, extract_stated_numbers
from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2, v2_subkind
from src.eval.core.system import SystemAnswer

_ROWS = [("DEVA", 100.0), ("DEVB", 200.0), ("DEVC", 300.0)]
_LISTING = "DEVA had 100, DEVB had 200, DEVC had 300."


def _v2_median(key: float) -> EvalCase:
    return EvalCase.from_dict({
        "id": "u-gsa-median", "suite": "xbrl_v2_dev", "kind": "compute",
        "origin": "v2:screen.median", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": "What was the median of the metric across the retailers?",
        "tags": ["split:dev", "shape:screen.median"], "expected_value": key,
        "disclosures": {"universe": "retailers", "members": 3,
                        "ranked": ["DEVA", "DEVB", "DEVC"], "excluded": [],
                        "period_ends": {}, "disclose_period_ends": False},
        "key": {"definition": None, "disagreements": [], "inputs": [],
                "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
                "source": "sec_companyfacts", "status": "verified"},
    })


def _hx_median(key: float) -> EvalCase:
    return EvalCase.from_dict({"id": "u-gsa-hx", "question": "q?", "unit": "USD",
                               "rel_tol": 1e-4, "expected_value": key,
                               "tags": ["split:external", "cross_filer", "median"]})


def _answer(text: str, *, hedged: bool = True) -> SystemAnswer:
    values = [n.value for n in extract_stated_numbers(text)]
    return SystemAnswer(text=text, numeric_values=values,
                        rows=[{"ticker": t, "value": v} for t, v in _ROWS],
                        fact_ids=[1], hedged=hedged)


class TestDistinctValues:
    def test_one_value_stated_twice_is_one_stated_number(self):
        # Decision on the spec PR: "200 (200 USD)" states one row-matching number, 200.
        text = "The median is 200 (200 USD). " + _LISTING
        g = grade_v2(_v2_median(200.0), _answer(text))
        assert g.grade is Grade.CORRECT
        assert g.matched_value == pytest.approx(200.0, rel=1e-12)

    def test_a_wrong_value_stated_twice_is_kept_and_wrong(self):
        text = "The median is 300 (300 USD). " + _LISTING
        g = grade_v2(_v2_median(200.0), _answer(text))
        assert g.grade is Grade.HEDGED_MISS

    def test_two_distinct_row_values_state_none(self):
        text = "The median is 200 or 300. " + _LISTING
        g = grade_v2(_v2_median(200.0), _answer(text))
        assert g.grade is Grade.REFUSED

    def test_a_non_row_number_beside_the_row_value_does_not_count(self):
        # 3 (the member count) matches no row, so 200 is still the one row-matching number.
        text = "The median of the 3 members is 200. " + _LISTING
        g = grade_v2(_v2_median(200.0), _answer(text))
        assert g.grade is Grade.CORRECT


class TestHxCases:
    def test_hx_median_subkind(self):
        assert v2_subkind(_hx_median(200.0)) == "median"

    def test_a_hedged_hx_median_stated_with_the_rows_is_correct(self):
        g = grade_v2(_hx_median(200.0), _answer("The median is 200. " + _LISTING))
        assert g.grade is Grade.CORRECT

    def test_a_hedged_hx_answer_listing_only_rows_is_refused(self):
        g = grade_v2(_hx_median(200.0), _answer(_LISTING))
        assert g.grade is Grade.REFUSED


class TestDeclineWords:
    @pytest.mark.parametrize("sentence", ["The median can’t be stated.",
                                          "The median isn’t 200."])
    def test_a_typographic_apostrophe_declines(self, sentence):
        assert aggregate_sentences(sentence, "median") == []

    def test_a_typographic_declined_median_is_refused(self):
        text = "The median can’t be determined; DEVB had 200. " + _LISTING
        g = grade_v2(_v2_median(200.0), _answer(text))
        assert g.grade is Grade.REFUSED
