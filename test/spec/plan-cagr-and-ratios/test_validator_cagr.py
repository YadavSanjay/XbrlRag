"""Spec tests for plan-cagr-and-ratios C3: `PlanValidator.validate` and the `cagr` op.

docs/specs/plan-cagr-and-ratios.md, Requirement 3. Pure.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_validator import PlanValidator

CIK = "0000000001"


def _lookup(sid, fy, **kw):
    step = {"id": sid, "op": "lookup", "cik": CIK, "fy": fy, "concept_family": "Revenue",
            "dimensions": "NONE", "section_hint": "Income Statement"}
    step.update(kw)
    return step


def _plan(cagr=None, extra=()):
    steps = [_lookup("r22", 2022), _lookup("r25", 2025)]
    steps.append(cagr if cagr is not None else {"id": "g1", "op": "cagr", "start": "r22",
                                                 "end": "r25"})
    steps.extend(extra)
    return {"steps": steps}


def _errors(plan):
    return PlanValidator().validate(plan)


@pytest.mark.criterion("C3")
def test_cagr_plan_is_accepted_and_a_compute_may_use_it():
    # oracle: Requirement 3 — two earlier lookups of one cik and family, years derivable;
    # a later compute may reference the cagr id
    assert _errors(_plan()) == []
    assert _errors(_plan(extra=[{"id": "c1", "op": "compute", "expr": "g1 * 100"}])) == []
    quarter = {"steps": [_lookup("q23", 2023, fiscal_period="Q2"),
                         _lookup("q25", 2025, fiscal_period="Q2"),
                         {"id": "g1", "op": "cagr", "start": "q23", "end": "q25"}]}
    assert _errors(quarter) == []


def _one_error(plan, *parts):
    errors = _errors(plan)
    assert len(errors) == 1, errors
    for part in parts:
        assert part in errors[0], (part, errors[0])
    return errors[0]


@pytest.mark.criterion("C3")
def test_bad_start_or_end_is_one_error_naming_the_field():
    # oracle: Requirement 3 — one error per field naming step[2], the id, the quoted field
    # and the value's repr, for a missing, forward, unknown or non-lookup reference
    for field in ("start", "end"):
        cagr = {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}
        del cagr[field]
        _one_error(_plan(cagr), "step[2]", "g1", f"'{field}'", "None")
        for value in ("zz", "c9"):
            cagr = {"id": "g1", "op": "cagr", "start": "r22", "end": "r25", field: value}
            _one_error(_plan(cagr), "step[2]", "g1", f"'{field}'", repr(value))
    # forward: the cagr is placed before its end lookup
    forward = {"steps": [_lookup("r22", 2022),
                         {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"},
                         _lookup("r25", 2025)]}
    _one_error(forward, "step[1]", "g1", "'end'", "'r25'")
    # a compute is not a lookup
    non_lookup = {"steps": [_lookup("r22", 2022), _lookup("r25", 2025),
                            {"id": "c1", "op": "compute", "expr": "r25 - r22"},
                            {"id": "g1", "op": "cagr", "start": "r22", "end": "c1"}]}
    _one_error(non_lookup, "step[3]", "g1", "'end'", "'c1'")


@pytest.mark.criterion("C3")
def test_years_cik_family_and_a_years_key():
    # oracle: Requirement 3 — underivable years name both inputs; a different cik or
    # concept_family is one error each; a planned `years` key is one error naming 'years'
    same_year = {"steps": [_lookup("ra", 2025), _lookup("rb", 2025),
                           {"id": "g1", "op": "cagr", "start": "ra", "end": "rb"}]}
    _one_error(same_year, "step[2]", "g1", "ra", "rb")
    reversed_ = {"steps": [_lookup("r25", 2025), _lookup("r22", 2022),
                           {"id": "g1", "op": "cagr", "start": "r25", "end": "r22"}]}
    _one_error(reversed_, "step[2]", "g1", "r25", "r22")
    periods = {"steps": [_lookup("ra", 2022, fiscal_period="Q1"),
                         _lookup("rb", 2025, fiscal_period="Q3"),
                         {"id": "g1", "op": "cagr", "start": "ra", "end": "rb"}]}
    _one_error(periods, "step[2]", "g1", "ra", "rb")
    other_cik = {"steps": [_lookup("r22", 2022), _lookup("r25", 2025, cik="0000000002"),
                           {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}]}
    _one_error(other_cik, "cik")
    other_family = {"steps": [_lookup("r22", 2022),
                              _lookup("r25", 2025, concept_family="NetIncome"),
                              {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}]}
    _one_error(other_family, "concept_family")
    _one_error(_plan({"id": "g1", "op": "cagr", "start": "r22", "end": "r25", "years": 3}),
               "'years'")


@pytest.mark.criterion("C3")
@given(st.integers(-5, 50))
def test_any_planned_years_is_exactly_one_error(years):
    # oracle: Requirement 3 — the years are derived, never planned, whatever the value
    plan = _plan({"id": "g1", "op": "cagr", "start": "r22", "end": "r25", "years": years})
    before = copy.deepcopy(plan)
    errors = _errors(plan)
    assert len(errors) == 1, errors
    assert "'years'" in errors[0]
    assert plan == before
