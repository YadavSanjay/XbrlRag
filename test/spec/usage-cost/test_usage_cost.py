"""Spec tests for usage-cost: v1 fact and narrative responses report their paid-model
spend as ``usage.cost_usd`` (docs/specs/usage-cost.md).

The meter and the schemas are pure: no DB, no Qdrant, no LLM. The gateway's recording
call (``TraceContext.record_llm_call``) stands in for a paid model call.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from src.api.v1.schemas import (
    FactLookupResponse,
    NarrativeSearchResponse,
    ResolvedCompanyModel,
    UsageModel,
)
from src.observability.trace_context import TraceContext, current_trace, trace_query
from src.observability.usage_meter import UsageMeter

costs = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
cost_lists = st.lists(costs, max_size=20)


def _assert_usage_fields() -> None:
    assert {"cost_usd", "llm_calls"} <= set(UsageModel.model_fields)


def _record(cost_list) -> None:
    for c in cost_list:
        current_trace().record_llm_call(prompt_tokens=10, completion_tokens=5, cost_usd=c)


def _fact_response(usage):
    return FactLookupResponse(
        company=ResolvedCompanyModel(cik="0001045810", ticker="NVDA"),
        concept="Revenue",
        series=[],
        usage=usage,
    )


def _narrative_response(usage):
    return NarrativeSearchResponse(
        company=ResolvedCompanyModel(cik="0001045810", ticker="NVDA"),
        query="supply chain risk",
        chunks=[],
        usage=usage,
    )


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_meter_sums_recorded_calls():
    # oracle: hand sum 0.0004 + 0.0012 + 0.00003 = 0.00163 over 3 calls
    with UsageMeter() as m:
        assert isinstance(current_trace(), TraceContext)
        _record([0.0004, 0.0012, 0.00003])
        during = m.usage()
    after = m.usage()
    for u in (during, after):
        # abs=1e-12: float summation of three small decimals, far below a cent
        assert u["cost_usd"] == pytest.approx(0.00163, abs=1e-12)
        assert u["llm_calls"] == 3


@pytest.mark.criterion("C1")
def test_meter_with_no_calls_is_exactly_free():
    # oracle: requirement: no paid call is 0.0 (free), not a missing value
    # red-exempt: zero the correct cost is 0.0; llm_calls and the float type still differ
    with UsageMeter() as m:
        pass
    u = m.usage()
    assert u == {"cost_usd": 0.0, "llm_calls": 0}
    assert type(u["cost_usd"]) is float
    assert type(u["llm_calls"]) is int


@pytest.mark.criterion("C1")
@given(cost_lists)
def test_meter_matches_sum_property(cost_list):
    # oracle: sum and count of the generated costs, computed here
    with UsageMeter() as m:
        _record(cost_list)
    u = m.usage()
    # abs=1e-12: at most 20 additions of values <= 1; rounding error is ~1e-15
    assert u["cost_usd"] == pytest.approx(math.fsum(cost_list), abs=1e-12)
    assert u["llm_calls"] == len(cost_list)
    assert u["cost_usd"] >= 0.0


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(cost_lists, cost_lists)
def test_meter_split_is_additive(first, second):
    # oracle: metamorphic: metering a list in two parts adds up to metering it whole
    with UsageMeter() as a:
        _record(first)
    with UsageMeter() as b:
        _record(second)
    with UsageMeter() as whole:
        _record(first + second)
    ua, ub, uw = a.usage(), b.usage(), whole.usage()
    # abs=1e-12: re-association of at most 40 additions of values <= 1
    assert ua["cost_usd"] + ub["cost_usd"] == pytest.approx(uw["cost_usd"], abs=1e-12)
    assert ua["llm_calls"] + ub["llm_calls"] == uw["llm_calls"] == len(first) + len(second)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_meter_persists_nothing_and_restores_no_trace(sink):
    # oracle: requirement: a raw lookup is not a query; it adds no audit row
    assert current_trace() is None
    with UsageMeter() as m:
        _record([0.002])
    assert sink.persisted == []
    assert current_trace() is None
    assert m.usage()["llm_calls"] == 1


@pytest.mark.criterion("C2")
def test_meter_credits_enclosing_trace(sink):
    # oracle: hand sums: outer 0.01 before + metered 0.002 + 0.003 = 0.015 over 3 calls;
    # the meter itself sees only its own 0.005 over 2 calls
    with trace_query("enclosing query") as outer:
        outer.record_llm_call(cost_usd=0.01)
        with UsageMeter() as m:
            _record([0.002, 0.003])
        assert current_trace() is outer
        # abs=1e-12: three small decimals
        assert m.usage()["cost_usd"] == pytest.approx(0.005, abs=1e-12)
        assert m.usage()["llm_calls"] == 2
        assert outer.total_cost_usd == pytest.approx(0.015, abs=1e-12)
        assert outer.llm_calls == 3
    # only the enclosing query is persisted, once
    assert sink.persisted == [outer.query_id]


@pytest.mark.criterion("C2")
def test_meter_restores_and_credits_on_exception(sink):
    # oracle: requirement: an exception propagates unchanged and no spend is lost
    class StageError(Exception):
        pass

    with trace_query("enclosing query") as outer:
        with pytest.raises(StageError):
            with UsageMeter() as m:
                _record([0.004])
                raise StageError("stage failed")
        assert current_trace() is outer
        # abs=1e-12: a single small decimal
        assert outer.total_cost_usd == pytest.approx(0.004, abs=1e-12)
        assert outer.llm_calls == 1
        assert m.usage()["llm_calls"] == 1
    assert current_trace() is None


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(cost_lists, cost_lists)
def test_enclosing_trace_grows_by_metered_usage(before, metered):
    # oracle: metamorphic: the enclosing trace's totals grow by exactly the meter's usage
    with trace_query("enclosing query") as outer:
        for c in before:
            outer.record_llm_call(cost_usd=c)
        cost0, calls0 = outer.total_cost_usd, outer.llm_calls
        with UsageMeter() as m:
            _record(metered)
        assert current_trace() is outer
        u = m.usage()
        # abs=1e-12: at most 40 additions of values <= 1
        assert outer.total_cost_usd - cost0 == pytest.approx(u["cost_usd"], abs=1e-12)
        assert outer.llm_calls - calls0 == u["llm_calls"] == len(metered)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_usage_model_accepts_valid_and_rejects_broken_costs():
    # oracle: faultline's contract: cost_usd finite and >= 0, llm_calls >= 0
    _assert_usage_fields()
    # abs=0: the value is stored, not transformed
    assert UsageModel(cost_usd=0.0, llm_calls=0).cost_usd == pytest.approx(0.0, abs=0)
    # abs=0: the value is stored, not transformed
    assert UsageModel(cost_usd=0.0004, llm_calls=1).cost_usd == pytest.approx(0.0004, abs=0)
    for bad in (-0.0001, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            UsageModel(cost_usd=bad, llm_calls=0)
    with pytest.raises(ValidationError):
        UsageModel(cost_usd=0.0, llm_calls=-1)


@pytest.mark.criterion("C3")
@given(costs, st.floats(min_value=1e-9, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_usage_model_sign_property(good, magnitude):
    # oracle: faultline's contract: any finite x >= 0 is accepted, any x < 0 rejected
    _assert_usage_fields()
    # abs=0: the value is stored, not transformed
    assert UsageModel(cost_usd=good, llm_calls=0).cost_usd == pytest.approx(good, abs=0)
    with pytest.raises(ValidationError):
        UsageModel(cost_usd=-magnitude, llm_calls=0)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_responses_require_usage_block():
    # oracle: faultline's contract: the block is always present ("unknown" != "free")
    _assert_usage_fields()
    for model in (FactLookupResponse, NarrativeSearchResponse):
        assert "usage" in model.model_fields, model.__name__
        assert model.model_fields["usage"].is_required(), model.__name__
    with pytest.raises(ValidationError):
        FactLookupResponse(company=ResolvedCompanyModel(), concept="Revenue")
    with pytest.raises(ValidationError):
        NarrativeSearchResponse(company=ResolvedCompanyModel(), query="supply chain risk")


@pytest.mark.criterion("C4")
def test_responses_serialize_usage_at_top_level():
    # oracle: faultline's contract: top-level usage.cost_usd, 0.0 for a free call
    # red-exempt: zero 0.0 is the correct cost of a database-only lookup
    _assert_usage_fields()
    fact = _fact_response(UsageModel(cost_usd=0.0, llm_calls=0)).model_dump(mode="json")
    narr = _narrative_response(UsageModel(cost_usd=0.0004, llm_calls=1)).model_dump(mode="json")
    assert "usage" in fact and "usage" in narr
    assert type(fact["usage"]["cost_usd"]) is float
    # abs=0: serialization must not alter the value
    assert fact["usage"]["cost_usd"] == pytest.approx(0.0, abs=0)
    assert narr["usage"]["cost_usd"] == pytest.approx(0.0004, abs=0)


@pytest.mark.criterion("C4")
@given(costs)
def test_response_usage_round_trip_property(cost):
    # oracle: the generated cost; serialization must carry it unchanged
    _assert_usage_fields()
    for build in (_fact_response, _narrative_response):
        dumped = build(UsageModel(cost_usd=cost, llm_calls=1)).model_dump(mode="json")
        assert "usage" in dumped
        # abs=0: JSON-mode dump keeps the float as-is
        assert dumped["usage"]["cost_usd"] == pytest.approx(cost, abs=0)
        assert dumped["usage"]["llm_calls"] == 1
