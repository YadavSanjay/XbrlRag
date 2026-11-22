"""Spec tests for revenue-subfamily-fallback: a NOT_FOUND ServiceRevenue/ProductRevenue
lookup whose hint names the sub-family is retried once as Revenue with the same hints
(docs/specs/revenue-subfamily-fallback.md).

The fact store is faked at the DB boundary (as in derived-gross-profit); the executor and the
calculator run for real.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import PlanExecutor, StepStatus, subfamily_fallback_step
from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactLookupResult, LookupStatus

META = "0001326801"
AAPL = "0000320193"
OK, NF = LookupStatus.OK, LookupStatus.NOT_FOUND
AMB, ERR = LookupStatus.AMBIGUOUS, LookupStatus.ERROR

# Meta FY2025 "Service, Other" revenue, corpus fact 41640 (trace 6e5849cd4ac847c4, 2026-10-16).
SERVICE_OTHER = (OK, 2_584_000_000.0, "USD", 41640)
META_FACTS = {"ServiceRevenue": (NF, None, None, None), "Revenue": SERVICE_OTHER}


def _step(family="ServiceRevenue", dims=None, sid="s25", cik=META, op="lookup"):
    return {"id": sid, "op": op, "cik": cik, "fy": 2025, "fiscal_period": "FY",
            "concept_family": family,
            "dimensions": {"product": "Service, Other"} if dims is None else dims}


class FakeFactStore:
    """The fact store (DB boundary): answers by concept_family; records every step asked."""

    def __init__(self, facts: dict):
        self.facts = facts
        self.calls: list[dict] = []

    def lookup(self, step: dict) -> FactLookupResult:
        self.calls.append(copy.deepcopy(step))
        status, value, unit, fid = self.facts.get(step["concept_family"], (NF, None, None, None))
        return FactLookupResult(
            step_id=step["id"], status=status, value=value, unit=unit,
            citation=Citation(fact_id=fid) if fid is not None else None,
            confidence=0.8 if status is OK else (0.5 if status is AMB else 0.0))


def _run(facts, plan):
    store = FakeFactStore(facts)
    return PlanExecutor(factstore_lookup=store).execute({"steps": plan}), store


def _families(store):
    return [c["concept_family"] for c in store.calls]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("family,dims", [
    ("ServiceRevenue", {"product": "Service, Other"}),
    ("ServiceRevenue", {"service": "Service, Other"}),
    ("ProductRevenue", {"product": "Product"}),
    ("ServiceRevenue", {"product": "SERVICES"}),
])
def test_qualifying_steps_are_retried_as_revenue(family, dims):
    # oracle: Requirement 1 — same step, concept_family replaced by "Revenue", hints unchanged
    step = _step(family, dims)
    retry = subfamily_fallback_step(step)
    assert retry == {**step, "concept_family": "Revenue"}
    assert retry["dimensions"] == dims
    assert retry is not step
    assert step["concept_family"] == family  # input untouched


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("step", [
    _step("ProductRevenue", "NONE", cik=AAPL),
    _step("ServiceRevenue", {}),
    _step("ServiceRevenue", {"geography": "Americas"}),
    _step("ServiceRevenue", {"product": "Product"}),
    _step("Revenue", {"product": "Service, Other"}),
    _step(op="screen"),
])
def test_non_qualifying_steps_are_not_retried(step):
    # oracle: Requirement 1 — no hint naming the sub-family, or not a sub-family lookup
    assert subfamily_fallback_step(_step())["concept_family"] == "Revenue"  # control
    assert subfamily_fallback_step(step) is None


_HINT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ,", max_size=12)


@pytest.mark.criterion("C1")
@given(st.sampled_from(["ServiceRevenue", "ProductRevenue"]), _HINT, _HINT,
       st.sampled_from(["product", "service", "segment", "x"]), st.booleans())
def test_retry_iff_a_hint_names_the_subfamily(family, pre, post, key, upper):
    # oracle: Requirement 1 as a predicate — retried iff a lowercased hint contains the word
    word = "service" if family == "ServiceRevenue" else "product"
    hint = pre + word + post
    hint = hint.upper() if upper else hint
    step = _step(family, {key: hint})
    before = copy.deepcopy(step)
    retry = subfamily_fallback_step(step)
    assert step == before  # never mutated
    assert retry is not None
    assert {k for k in retry if retry[k] != step.get(k)} == {"concept_family"}
    assert retry["concept_family"] == "Revenue"
    other = "product" if word == "service" else "service"
    plain = (pre + post).replace(word, "").replace(other, "") or "americas"
    if word not in plain:
        assert subfamily_fallback_step(_step(family, {key: plain})) is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(["Revenue", "OperatingIncome", "NetIncome", "CostOfRevenue",
                        "DeferredRevenue", "GrossProfit"]),
       st.sampled_from(["Service, Other", "Product", "services"]))
def test_other_families_are_never_retried(family, hint):
    # oracle: metamorphic — only the two sub-families map to Revenue
    assert subfamily_fallback_step(_step("ServiceRevenue", {"product": hint})) is not None \
        or "service" not in hint.lower()
    assert subfamily_fallback_step(_step("ProductRevenue", {"product": "Product"})) is not None
    assert subfamily_fallback_step(_step(family, {"product": hint})) is None


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_meta_service_other_resolves_through_revenue():
    # oracle: corpus fact 41640 = 2,584,000,000 USD; x2 = 2 × 2,584M by hand
    plan = [_step(), {"id": "x2", "op": "compute", "expr": "s25 * 2"}]
    result, _ = _run(META_FACTS, plan)
    s25 = result.step("s25")
    assert s25.op == "lookup" and s25.status is StepStatus.OK
    assert s25.value == pytest.approx(2_584_000_000.0, rel=1e-12)  # the fact's value, unchanged
    assert s25.unit == "USD"
    assert (s25.citation or {}).get("fact_id") == 41640
    assert s25.confidence == pytest.approx(0.8, abs=1e-9)  # the Revenue lookup's confidence
    assert result.bindings["s25"] == pytest.approx(2_584_000_000.0, rel=1e-12)  # bound
    assert result.step("x2").value == pytest.approx(5_168_000_000.0, rel=1e-12)  # exact double


@pytest.mark.criterion("C2")
@given(st.sampled_from([OK, NF, AMB, ERR]))
def test_step_is_ok_exactly_when_the_retry_is_ok(retry_status):
    # oracle: Requirement 2 as a truth table over the Revenue retry's status
    facts = {**META_FACTS, "Revenue": (retry_status, 2_584_000_000.0, "USD", 41640)}
    result, _ = _run(facts, [_step()])
    s25 = result.step("s25")
    if retry_status is OK:
        assert s25.status is StepStatus.OK
        assert s25.value == pytest.approx(2_584_000_000.0, rel=1e-12)  # the fact's value
    else:
        assert s25.status is StepStatus.NOT_FOUND and s25.value is None
    control, _ = _run(META_FACTS, [_step()])
    assert control.step("s25").status is StepStatus.OK  # an OK retry resolves the step


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_one_retry_with_the_same_hints_and_id():
    # oracle: Requirement 2 — exactly one extra lookup, same id and hints, family Revenue
    _, store = _run(META_FACTS, [_step()])
    assert _families(store) == ["ServiceRevenue", "Revenue"]
    assert [c["id"] for c in store.calls] == ["s25", "s25"]
    assert all(c["dimensions"] == {"product": "Service, Other"} for c in store.calls)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("facts,step,expected", [
    ({"ServiceRevenue": SERVICE_OTHER, "Revenue": SERVICE_OTHER}, _step(), ["ServiceRevenue"]),
    ({"ServiceRevenue": (AMB, 1.0, "USD", 7), "Revenue": SERVICE_OTHER}, _step(),
     ["ServiceRevenue"]),
    (META_FACTS, _step(dims={"geography": "Americas"}), ["ServiceRevenue"]),
    ({"Revenue": SERVICE_OTHER}, _step("ProductRevenue", "NONE", cik=AAPL), ["ProductRevenue"]),
    ({"Revenue": SERVICE_OTHER}, _step("OperatingIncome", {"product": "Service, Other"}),
     ["OperatingIncome"]),
])
def test_no_retry_outside_scope(facts, step, expected):
    # oracle: Requirement 3 — resolved, ambiguous or non-qualifying lookups make one call
    _, control = _run(META_FACTS, [_step()])
    assert _families(control) == ["ServiceRevenue", "Revenue"]  # the qualifying miss retries
    _, store = _run(facts, [step])
    assert _families(store) == expected


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from([
    _step("OperatingIncome", {"product": "Service, Other"}, sid="o1"),
    _step("ServiceRevenue", {"geography": "Americas"}, sid="g1", cik=AAPL),  # not a sibling
    _step("ProductRevenue", "NONE", sid="p1"),
    _step("Revenue", "NONE", sid="r1"),
]))
def test_adding_a_non_qualifying_step_adds_one_call(extra):
    # oracle: metamorphic — a step the helper rejects costs exactly its own lookup (extras
    # are never siblings of s25, so the multidim-recovery intersection does not run)
    _, base = _run(META_FACTS, [_step()])
    _, more = _run(META_FACTS, [_step(), extra])
    assert _families(base) == ["ServiceRevenue", "Revenue"]
    assert len(more.calls) == len(base.calls) + 1
    assert more.calls[-1]["id"] == extra["id"]
