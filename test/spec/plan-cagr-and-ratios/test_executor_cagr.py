"""Spec tests for plan-cagr-and-ratios C4: `PlanExecutor` runs a `cagr` step.

docs/specs/plan-cagr-and-ratios.md, Requirement 4. The fact store is faked at its boundary
(conftest.py); the executor, the CAGR and the calculator are the product's own.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

REL, ABS = 1e-9, 1e-12  # exp/log closed form vs a power: float rounding only


def _closed_form(end, start, years):
    """S4: (end / start)^(1/n) - 1, computed as exp(log(end / start) / n) - 1."""
    return math.exp(math.log(end / start) / years) - 1


CAGR = {"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}


def _run(fx, answers, steps):
    return fx.steps_by_id(fx.execute(answers, steps))


@pytest.mark.criterion("C4")
def test_cagr_step_value_record_and_binding(fx):
    # oracle: Notes — 106.48e9 / 80e9 = 1.1^3, so g1 = 0.1 over 3 years; c1 = g1 * 100 = 10
    answers = {"r22": (fx.OK, fx.REVENUE[2022], "USD", 501),
               "r25": (fx.OK, fx.REVENUE[2025], "USD", 504)}
    steps = [fx.lookup_step("r22", 2022), fx.lookup_step("r25", 2025), CAGR,
             {"id": "c1", "op": "compute", "expr": "g1 * 100"}]
    out = _run(fx, answers, steps)
    g1 = out["g1"]
    assert g1["op"] == "cagr"
    assert g1["status"] == "OK", g1
    assert g1["value"] == pytest.approx(0.1, rel=REL, abs=ABS)
    assert g1["unit"] == "pure"
    assert "cagr" in g1, g1
    record = g1["cagr"]
    assert record["start"] == "r22" and record["end"] == "r25"
    assert record["start_fy"] == 2022 and record["end_fy"] == 2025
    assert record["years"] == 3
    assert record["start_value"] == pytest.approx(80e9, rel=1e-15)  # stored values, copied
    assert record["end_value"] == pytest.approx(106.48e9, rel=1e-15)
    assert record["fact_ids"] == [501, 504]
    assert out["c1"]["status"] == "OK"
    assert out["c1"]["value"] == pytest.approx(10.0, rel=REL)


@pytest.mark.criterion("C4")
def test_cagr_step_refusals(fx):
    # oracle: Requirement 4 — non-OK input, unit mismatch, other cik, underivable years and
    # an unknown start are ERROR naming the cause; a non-positive start is NOT_MEANINGFUL
    def g1(answers, steps):
        return _run(fx, answers, steps)["g1"]

    base = [fx.lookup_step("r22", 2022), fx.lookup_step("r25", 2025), CAGR]
    ok = {"r22": (fx.OK, 80e9, "USD", 1), "r25": (fx.OK, 106.48e9, "USD", 2)}

    r = g1({**ok, "r22": (fx.AMB, 80e9, "USD", 1)}, base)
    assert r["status"] == "ERROR" and "r22" in r["error"] and "AMBIGUOUS" in r["error"]
    assert r["value"] is None

    r = g1({**ok, "r25": (fx.OK, 106.48e9, "EUR", 2)}, base)
    assert r["status"] == "ERROR" and "USD" in r["error"] and "EUR" in r["error"]

    r = g1(ok, [fx.lookup_step("r22", 2022),
                fx.lookup_step("r25", 2025, cik="0000000002"), CAGR])
    assert r["status"] == "ERROR" and "cik" in r["error"]

    r = g1(ok, [fx.lookup_step("r22", 2025), fx.lookup_step("r25", 2025), CAGR])
    assert r["status"] == "ERROR" and "years" in r["error"]

    r = g1(ok, base[:2] + [{"id": "g1", "op": "cagr", "start": "zz", "end": "r25"}])
    assert r["status"] == "ERROR" and "start" in r["error"] and "zz" in r["error"]

    r = g1({**ok, "r22": (fx.OK, -5e9, "USD", 1)}, base)
    assert r["status"] == "NOT_MEANINGFUL" and r["error"]
    assert r["value"] is None and r["unit"] is None


@pytest.mark.criterion("C4")
@given(st.floats(1e6, 1e12), st.floats(1e6, 1e12), st.integers(2000, 2030),
       st.integers(1, 8))
def test_cagr_step_equals_the_closed_form(fx, start, end, fy0, years):
    # oracle: Requirements 1, 2, 4 — value = closed form over the derived years
    answers = {"r22": (fx.OK, start, "USD", 1), "r25": (fx.OK, end, "USD", 2)}
    steps = [fx.lookup_step("r22", fy0), fx.lookup_step("r25", fy0 + years), CAGR]
    g1 = _run(fx, answers, steps)["g1"]
    assert g1["status"] == "OK", g1
    assert g1["value"] == pytest.approx(_closed_form(end, start, years), rel=REL, abs=ABS)
    assert g1["cagr"]["years"] == years
