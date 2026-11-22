"""Unit tests for the WS-4 / H1 per-run cost ceiling + circuit breaker.

Two groups:
- TestRunBudgetLogic: the pure RunBudget.check / from_env logic (no gateway).
- TestGatewayEnforcement: the gateway halts a run at the ceiling / trips the
  breaker, checked before spending, against the active TraceContext totals.
"""
import logging

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import (
    CircuitBreakerOpen,
    RunBudget,
    RunBudgetExceeded,
    RunHalt,
)
from src.observability.trace_context import trace_query

# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

class TestRunBudgetLogic:
    def test_empty_budget_is_inert(self):
        b = RunBudget()
        assert not b.is_active()
        b.check(cost_usd=1e9, llm_calls=1e9, total_tokens=1e9, consecutive_failures=1e9)  # no raise

    def test_cost_ceiling(self):
        b = RunBudget(max_cost_usd=0.01)
        assert b.is_active()
        b.check(cost_usd=0.009)                       # under: ok
        with pytest.raises(RunBudgetExceeded):
            b.check(cost_usd=0.01)                     # at the ceiling: halt

    def test_calls_ceiling(self):
        b = RunBudget(max_llm_calls=2)
        b.check(llm_calls=1)
        with pytest.raises(RunBudgetExceeded):
            b.check(llm_calls=2)

    def test_tokens_ceiling(self):
        b = RunBudget(max_tokens=1000)
        b.check(total_tokens=999)
        with pytest.raises(RunBudgetExceeded):
            b.check(total_tokens=1000)

    def test_circuit_breaker(self):
        b = RunBudget(max_consecutive_failures=3)
        b.check(consecutive_failures=2)
        with pytest.raises(CircuitBreakerOpen):
            b.check(consecutive_failures=3)

    def test_breaker_checked_before_cost(self):
        """A failure storm trips the breaker even when under the cost ceiling."""
        b = RunBudget(max_cost_usd=1000.0, max_consecutive_failures=1)
        with pytest.raises(CircuitBreakerOpen):
            b.check(cost_usd=0.0, consecutive_failures=1)

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("XBRLRAG_RUN_MAX_COST_USD", "0.5")
        monkeypatch.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "20")
        monkeypatch.delenv("XBRLRAG_RUN_MAX_TOKENS", raising=False)
        monkeypatch.setenv("XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES", "5")
        b = RunBudget.from_env()
        assert b.max_cost_usd == 0.5
        assert b.max_llm_calls == 20
        assert b.max_tokens is None
        assert b.max_consecutive_failures == 5

    def test_exceptions_share_base(self):
        assert issubclass(RunBudgetExceeded, RunHalt)
        assert issubclass(CircuitBreakerOpen, RunHalt)


# ---------------------------------------------------------------------------
# Gateway enforcement
# ---------------------------------------------------------------------------

class _FakeAdapter(ProviderAdapter):
    """Minimal adapter: each invoke replays the next behavior (None => success,
    Exception => raise). Mirrors the fake in test_llm_gateway."""

    def __init__(self, provider="openai", *, parsed=None, behaviors=None):
        self.provider = provider
        self._parsed = parsed or ParsedResponse(
            content="ok", finish_reason="stop", prompt_tokens=1000, completion_tokens=500)
        self._behaviors = list(behaviors or [None])
        self.calls = 0

    def get_client(self):
        return object()

    def build_params(self, **kwargs):
        return kwargs

    def invoke(self, client, params):
        behavior = self._behaviors[min(self.calls, len(self._behaviors) - 1)]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        return "raw"

    def parse(self, raw, *, stage):
        return self._parsed


@pytest.fixture
def events():
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            import json
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


def _gateway(*, run_budget=None, behaviors=None, max_attempts=3):
    adapter = _FakeAdapter("openai", behaviors=behaviors)
    gw = LLMGateway(
        stage="synthesizer", cost_tracker=CostTracker(), sleep_fn=lambda _s: None,
        adapters={"openai": adapter}, run_budget=run_budget, max_attempts=max_attempts,
    )
    return gw, adapter


MSGS = [{"role": "user", "content": "hi"}]


class TestGatewayEnforcement:
    def test_call_ceiling_halts_at_limit(self, events):
        gw, adapter = _gateway(run_budget=RunBudget(max_llm_calls=2))
        with trace_query("q"):
            gw.complete(messages=MSGS, model="gpt-5-mini")   # call 1
            gw.complete(messages=MSGS, model="gpt-5-mini")   # call 2
            with pytest.raises(RunBudgetExceeded):
                gw.complete(messages=MSGS, model="gpt-5-mini")  # 3rd: halt before spend
        assert adapter.calls == 2                             # the 3rd never reached the provider
        assert any(e["kind"] == "budget_halt" for e in events)

    def test_cost_ceiling_halts(self):
        # gpt-5-mini: (1000/1000)*0.00025 + (500/1000)*0.002 = 0.00125 per call.
        gw, adapter = _gateway(run_budget=RunBudget(max_cost_usd=0.001))
        with trace_query("q"):
            gw.complete(messages=MSGS, model="gpt-5-mini")   # records 0.00125 >= 0.001
            with pytest.raises(RunBudgetExceeded):
                gw.complete(messages=MSGS, model="gpt-5-mini")
        assert adapter.calls == 1

    def test_circuit_breaker_trips_after_consecutive_failures(self):
        err = ValueError("permanent")
        gw, adapter = _gateway(
            run_budget=RunBudget(max_consecutive_failures=2),
            behaviors=[err, err, err, err], max_attempts=1)
        with trace_query("q"):
            for _ in range(2):
                with pytest.raises(ValueError):
                    gw.complete(messages=MSGS, model="gpt-5-mini")
            # 3rd call: breaker is open, halts BEFORE invoking the provider.
            with pytest.raises(CircuitBreakerOpen):
                gw.complete(messages=MSGS, model="gpt-5-mini")
        assert adapter.calls == 2

    def test_success_resets_the_breaker(self):
        err = ValueError("permanent")
        gw, adapter = _gateway(
            run_budget=RunBudget(max_consecutive_failures=2),
            behaviors=[err, None, err, None, None], max_attempts=1)
        with trace_query("q"):
            with pytest.raises(ValueError):
                gw.complete(messages=MSGS, model="gpt-5-mini")   # fail -> consec 1
            gw.complete(messages=MSGS, model="gpt-5-mini")       # success -> reset 0
            with pytest.raises(ValueError):
                gw.complete(messages=MSGS, model="gpt-5-mini")   # fail -> consec 1 (not 2)
            gw.complete(messages=MSGS, model="gpt-5-mini")       # success, no breaker trip

    def test_disabled_budget_is_no_op(self):
        gw, adapter = _gateway(run_budget=RunBudget())  # inactive
        with trace_query("q"):
            for _ in range(5):
                gw.complete(messages=MSGS, model="gpt-5-mini")
        assert adapter.calls == 5

    def test_no_enforcement_without_active_trace(self):
        """Budget keys off the run trace; with no trace active, nothing to cap."""
        gw, adapter = _gateway(run_budget=RunBudget(max_llm_calls=1))
        for _ in range(3):
            gw.complete(messages=MSGS, model="gpt-5-mini")
        assert adapter.calls == 3
