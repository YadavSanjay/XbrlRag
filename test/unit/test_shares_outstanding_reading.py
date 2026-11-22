"""Unit tests for the shares-outstanding-reading branches its spec tests do not reach.

Spec: docs/specs/shares-outstanding-reading.md. The concept_mismatch drop of the shapes other
than lookup (requirement 1), a point-in-time grade, which never carries reading marks
(requirement 4), and readings whose value is not a number. Expected values are read off the
cases built here.
"""

from __future__ import annotations

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.numeric import match_reading
from src.eval.core.system import SystemAnswer
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, key_intent

_ISSUED = {"concept": "us-gaap:CommonStockSharesIssued", "unit": "shares"}
_INV = {"AAA": {"cik": "0000900101", "years": {
    y: {"SharesOutstanding": dict(_ISSUED)} for y in (2022, 2023, 2024)}}}


class _NoSec:
    def company_facts(self, cik: str) -> dict:
        raise AssertionError("a concept mismatch drops before any fetch")

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are not read")


class _NoStore:
    def value(self, *args: object) -> None:
        raise AssertionError("nothing is keyed")


@pytest.mark.parametrize("intent", [
    Intent("trend", tickers=["AAA"], fiscal_years=[2022, 2023, 2024],
           metrics=["SharesOutstanding"]),
    Intent("point_in_time", tickers=["AAA"], fiscal_years=[2023], filing_year=2024,
           metrics=["SharesOutstanding"]),
    Intent("compute", tickers=["AAA"], fiscal_years=[2022, 2024],
           metrics=["SharesOutstanding"], operation="difference"),
])
def test_issued_shares_drop_every_single_filer_shape(intent):
    with pytest.raises(IntentDroppedError) as err:
        key_intent(intent, _INV, {}, _NoSec(), _NoStore())  # type: ignore[arg-type]  # stand-ins
    assert err.value.args == ("concept_mismatch",)


def test_point_in_time_rows_carry_no_marks():
    case = EvalCase.from_dict({
        "id": "pit", "question": "q", "kind": "lookup", "rel_tol": 1e-4,
        "expected_rows": [{"entity": "original", "value": 100.0},
                          {"entity": "latest", "value": 110.0}],
        "tags": ["shape:point_in_time"],
    })
    grade = grade_case(case, SystemAnswer(text="100 and 110", numeric_values=[100.0, 110.0],
                                          fact_ids=[1]))
    assert grade.matched_readings is None
    assert "matched_readings" not in grade.to_dict()


def test_a_reading_without_a_number_never_matches():
    assert match_reading(5.0, [], [{"reading": "cover_page", "value": "5"}], 1e-4) is None
    assert match_reading(5.0, [], [{"reading": "cover_page", "value": True}], 1e-4) is None
