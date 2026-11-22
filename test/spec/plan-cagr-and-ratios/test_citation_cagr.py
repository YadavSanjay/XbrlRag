"""Spec tests for plan-cagr-and-ratios C10: a statement citing a cagr step may state the CAGR
and its two inputs (docs/specs/plan-cagr-and-ratios.md, Requirement 10). Pure.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unmatched_numbers


def _evidence(start, end, years, fy0=2023):
    g = (end / start) ** (1 / years) - 1  # S4 definition (the fixture's own value)

    def lookup(sid, value, fid):
        return {"step_id": sid, "op": "lookup", "status": "OK", "value": value,
                "unit": "USD", "citation": {"fact_id": fid}, "confidence": 0.9,
                "linked_chunk_ids": [], "error": None}

    cagr = {"step_id": "g1", "op": "cagr", "status": "OK", "value": g, "unit": "pure",
            "citation": None, "confidence": 0.9, "linked_chunk_ids": [], "error": None,
            "cagr": {"start": "r0", "end": "r1", "start_fy": fy0, "end_fy": fy0 + years,
                     "years": years, "start_value": start, "end_value": end,
                     "fact_ids": [201, 202]}}
    execution = {"status": "OK", "steps": [lookup("r0", start, 201), lookup("r1", end, 202),
                                           cagr]}
    plan = {"steps": [{"id": "r0", "op": "lookup", "fy": fy0},
                      {"id": "r1", "op": "lookup", "fy": fy0 + years},
                      {"id": "g1", "op": "cagr", "start": "r0", "end": "r1"}]}
    return execution, plan, g


def _unmatched(statement, start, end, years):
    execution, plan, _ = _evidence(start, end, years)
    detail = {"statement": statement, "fact_ids": ["g1"], "chunk_ids": []}
    return [x["value"] for x in unmatched_numbers(detail, execution, plan, [])]


@pytest.mark.criterion("C10")
def test_cagr_statement_with_its_inputs_is_supported():
    # oracle: Notes — 121e9 / 100e9 = 1.21 = 1.1^2, a 10.0% CAGR over 2 years; $131.0 billion
    # matches neither input
    s = ("Revenue grew at a 10.0% CAGR from $100.0 billion in FY2023 to $121.0 billion in "
         "FY2025.")
    assert _unmatched(s, 100e9, 121e9, 2) == []
    bad = ("Revenue grew at a 10.0% CAGR from $100.0 billion in FY2023 to $131.0 billion in "
           "FY2025.")
    assert _unmatched(bad, 100e9, 121e9, 2) == [pytest.approx(131e9, rel=1e-12)]


@pytest.mark.criterion("C10")
@given(st.integers(10, 900), st.integers(10, 900), st.integers(1, 6))
def test_any_rounded_cagr_and_inputs_are_supported(start_b, end_b, years):
    # oracle: Requirement 10 — inputs stated in billions to one decimal (exact here), the
    # CAGR as a percent to one decimal (within 0.1 point); 3x the start matches nothing
    start, end = start_b * 1e9, end_b * 1e9
    g = math.exp(math.log(end / start) / years) - 1
    s = (f"Revenue changed at a {100 * g:.1f}% compound annual rate from ${start_b:.1f} "
         f"billion to ${end_b:.1f} billion.")
    assert _unmatched(s, start, end, years) == []
    triple = 3 * start_b
    if triple == end_b:
        triple += 1
    bad = f"Revenue reached ${triple:.1f} billion, a {100 * g:.1f}% compound annual rate."
    assert _unmatched(bad, start, end, years) == [pytest.approx(triple * 1e9, rel=1e-12)]
