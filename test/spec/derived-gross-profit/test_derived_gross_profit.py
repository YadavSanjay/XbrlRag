"""Spec tests for derived-gross-profit: a NOT_FOUND GrossProfit lookup is derived from
Revenue − CostOfRevenue when both resolve (docs/specs/derived-gross-profit.md).

The fact store is faked at the DB boundary (as in multidim-recovery); the executor and the
calculator run for real.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import PlanExecutor, StepStatus
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactLookupResult, LookupStatus

META = "0001326801"
OK, NF, AMB = LookupStatus.OK, LookupStatus.NOT_FOUND, LookupStatus.AMBIGUOUS

# Meta FY2024, corpus facts (2026-10-16): revenue 40054, cost of revenue 40057.
META_FACTS = {
    "GrossProfit": (NF, None, None, None),
    "Revenue": (OK, 164_501_000_000.0, "USD", 40054),
    "CostOfRevenue": (OK, 30_161_000_000.0, "USD", 40057),
}


def _lookup(sid, family):
    return {"id": sid, "op": "lookup", "cik": META, "fy": 2024, "concept_family": family,
            "dimensions": "NONE"}


PLAN = [_lookup("gp24", "GrossProfit"), _lookup("rev24", "Revenue"),
        {"id": "gm24", "op": "compute", "expr": "(gp24 / rev24) * 100"}]


class FakeFactStore:
    """The fact store (DB boundary): answers by concept_family; records every step asked."""

    def __init__(self, facts: dict):
        self.facts = facts
        self.calls: list[dict] = []

    def lookup(self, step: dict) -> FactLookupResult:
        self.calls.append(step)
        status, value, unit, fid = self.facts.get(step["concept_family"], (NF, None, None, None))
        return FactLookupResult(
            step_id=step["id"], status=status, value=value, unit=unit,
            citation=Citation(fact_id=fid) if fid is not None else None,
            confidence=0.8 if status is OK else (0.5 if status is AMB else 0.0))


def _run(facts, plan=PLAN):
    store = FakeFactStore(facts)
    return PlanExecutor(factstore_lookup=store).execute({"steps": plan}), store


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_meta_gross_profit_is_derived_with_both_facts_cited():
    # oracle: hand arithmetic — 164,501M − 30,161M = 134,340M (corpus facts 40054, 40057)
    result, _ = _run(META_FACTS)
    gp = result.step("gp24")
    assert gp.op == "lookup" and gp.status is StepStatus.OK
    assert gp.value == pytest.approx(134_340_000_000.0, rel=1e-12)  # exact difference
    assert gp.unit == "USD"
    assert gp.component_fact_ids == {"revenue": 40054, "cost_of_revenue": 40057}
    d = gp.to_dict()
    assert d["component_fact_ids"] == {"revenue": 40054, "cost_of_revenue": 40057}
    assert d["status"] == "OK"


@pytest.mark.criterion("C1")
def test_meta_gross_margin_computes_from_the_derived_value():
    # oracle: 134,340 / 164,501 × 100 = 81.6651570507…, mv_derived_metrics 0.81665157050717
    result, _ = _run(META_FACTS)
    gm = result.step("gm24")
    assert gm.status is StepStatus.OK
    assert gm.value == pytest.approx(81.665157050717, rel=1e-9)  # float division only
    assert result.bindings["gp24"] == pytest.approx(134_340_000_000.0, rel=1e-12)  # bound


@pytest.mark.criterion("C1")
@given(st.integers(min_value=2, max_value=10**6), st.data())
def test_derivation_is_revenue_minus_cost(rev_m, data):
    # oracle: arithmetic — gp = R − C, gm = (R − C) / R × 100
    cost_m = data.draw(st.integers(min_value=1, max_value=rev_m - 1))
    r, c = rev_m * 1e6, cost_m * 1e6
    facts = {**META_FACTS, "Revenue": (OK, r, "USD", 1), "CostOfRevenue": (OK, c, "USD", 2)}
    result, _ = _run(facts)
    assert result.step("gp24").value == pytest.approx(r - c, rel=1e-12)  # exact difference
    assert result.step("gm24").value == pytest.approx((r - c) / r * 100, rel=1e-9)  # division


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@pytest.mark.parametrize("broken", [
    {"CostOfRevenue": (NF, None, None, None)},
    {"CostOfRevenue": (AMB, 30_161_000_000.0, "USD", 40057)},
    {"Revenue": (AMB, 164_501_000_000.0, "USD", 40054)},
    {"CostOfRevenue": (OK, 30_161_000_000.0, "EUR", 40057)},
])
def test_never_guesses_from_one_or_an_ambiguous_component(broken):
    # oracle: Requirement 2 — derive only from two OK components in one unit
    derived, _ = _run(META_FACTS)
    assert derived.step("gp24").status is StepStatus.OK  # both components resolve
    result, _ = _run({**META_FACTS, **broken})
    gp = result.step("gp24")
    assert gp.status is StepStatus.NOT_FOUND
    assert gp.value is None
    assert not getattr(gp, "component_fact_ids", None)
    assert "component_fact_ids" not in gp.to_dict() or not gp.to_dict()["component_fact_ids"]
    assert result.step("gm24").status is not StepStatus.OK


@pytest.mark.criterion("C2")
@given(st.sampled_from([OK, NF, AMB]), st.sampled_from([OK, NF, AMB]),
       st.sampled_from(["USD", "EUR"]), st.sampled_from(["USD", "EUR"]))
def test_derived_exactly_when_both_ok_in_one_unit(rev_status, cost_status, rev_unit, cost_unit):
    # oracle: Requirement 2 as a truth table over component statuses and units
    facts = {**META_FACTS,
             "Revenue": (rev_status, 164_501_000_000.0, rev_unit, 40054),
             "CostOfRevenue": (cost_status, 30_161_000_000.0, cost_unit, 40057)}
    result, _ = _run(facts)
    gp = result.step("gp24")
    expected = rev_status is OK and cost_status is OK and rev_unit == cost_unit
    assert (gp.status is StepStatus.OK) is expected
    if expected:
        assert gp.value == pytest.approx(134_340_000_000.0, rel=1e-12)  # exact difference
    else:
        assert gp.status is StepStatus.NOT_FOUND and gp.value is None
    baseline, _ = _run(META_FACTS)
    assert baseline.step("gp24").status is StepStatus.OK  # both components resolve


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_resolved_gross_profit_makes_no_component_lookups():
    # oracle: Requirement 4 — the fallback runs only on NOT_FOUND
    tagged = {**META_FACTS, "GrossProfit": (OK, 134_340_000_000.0, "USD", 999)}
    result, store = _run(tagged)
    assert [c["id"] for c in store.calls] == ["gp24", "rev24"]
    assert result.step("gp24").citation == {"fact_id": 999} or (
        result.step("gp24").citation or {}).get("fact_id") == 999
    _, missing = _run(META_FACTS)
    assert [c["id"] for c in missing.calls] == ["gp24", "gp24.revenue",
                                                "gp24.cost_of_revenue", "rev24"]
    assert [c["concept_family"] for c in missing.calls[1:3]] == ["Revenue", "CostOfRevenue"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from(["OperatingIncome", "NetIncome", "CostOfRevenue", "Revenue"]),
       st.sampled_from([NF, AMB]))
def test_other_families_and_resolved_gross_profit_trigger_nothing(family, status):
    # oracle: metamorphic — only a NOT_FOUND GrossProfit adds component lookups
    plan = [_lookup("x1", family)]
    facts = {**META_FACTS, family: (status, None, None, None)}
    _, store = _run(facts, plan)
    assert [c["id"] for c in store.calls] == ["x1"]
    amb = {**META_FACTS, "GrossProfit": (AMB, 1.0, "USD", 7)}
    _, store2 = _run(amb, [_lookup("g", "GrossProfit")])
    assert [c["id"] for c in store2.calls] == ["g"]
    _, store3 = _run(META_FACTS, [_lookup("g", "GrossProfit")])
    assert [c["id"] for c in store3.calls] == ["g", "g.revenue", "g.cost_of_revenue"]
