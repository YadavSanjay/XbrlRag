"""Unit tests for the WS-4 / B9 shared XBRL gateway factory.

build_xbrl_gateway injects a PROCESS-WIDE rate limiter (B9) and a per-run budget
(H1) into each stage's gateway. Both default to disabled unless the XBRLRAG_LLM_* /
XBRLRAG_RUN_* env vars are set, so wiring stages through the factory is inert by
default.
"""
import pytest

from src.llm import xbrl_gateway
from src.llm.rate_limiter import RateLimiter
from src.llm.xbrl_gateway import (
    build_xbrl_gateway,
    get_shared_rate_limiter,
    reset_shared_state,
)


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Drop the cached shared limiter around each test so env changes take effect."""
    reset_shared_state()
    yield
    reset_shared_state()


class TestSharedRateLimiter:
    def test_none_when_no_env(self, monkeypatch):
        monkeypatch.delenv("XBRLRAG_LLM_RPM", raising=False)
        monkeypatch.delenv("XBRLRAG_LLM_TPM", raising=False)
        assert get_shared_rate_limiter() is None
        gw = build_xbrl_gateway("synthesizer", api_key="sk-test")
        assert gw.rate_limiter is None

    def test_built_from_rpm_env(self, monkeypatch):
        monkeypatch.setenv("XBRLRAG_LLM_RPM", "120")
        monkeypatch.delenv("XBRLRAG_LLM_TPM", raising=False)
        rl = get_shared_rate_limiter()
        assert isinstance(rl, RateLimiter)
        assert rl.rpm == 120 and rl.tpm is None

    def test_built_from_tpm_env(self, monkeypatch):
        monkeypatch.delenv("XBRLRAG_LLM_RPM", raising=False)
        monkeypatch.setenv("XBRLRAG_LLM_TPM", "50000")
        rl = get_shared_rate_limiter()
        assert rl.tpm == 50000

    def test_limiter_is_shared_singleton(self, monkeypatch):
        monkeypatch.setenv("XBRLRAG_LLM_RPM", "60")
        first = get_shared_rate_limiter()
        second = get_shared_rate_limiter()
        assert first is second                      # cached, not rebuilt
        gw1 = build_xbrl_gateway("intent_router", api_key="sk-test")
        gw2 = build_xbrl_gateway("synthesizer", api_key="sk-test")
        assert gw1.rate_limiter is gw2.rate_limiter is first   # one throttle for all stages


class TestFactoryInjection:
    def test_run_budget_from_env(self, monkeypatch):
        monkeypatch.setenv("XBRLRAG_RUN_MAX_COST_USD", "0.25")
        monkeypatch.setenv("XBRLRAG_RUN_MAX_LLM_CALLS", "40")
        gw = build_xbrl_gateway("plan_generator", api_key="sk-test")
        assert gw.run_budget.is_active()
        assert gw.run_budget.max_cost_usd == 0.25
        assert gw.run_budget.max_llm_calls == 40

    def test_run_budget_inert_without_env(self, monkeypatch):
        for k in ("XBRLRAG_RUN_MAX_COST_USD", "XBRLRAG_RUN_MAX_LLM_CALLS",
                  "XBRLRAG_RUN_MAX_TOKENS", "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES"):
            monkeypatch.delenv(k, raising=False)
        gw = build_xbrl_gateway("verifier", api_key="sk-test")
        assert not gw.run_budget.is_active()

    def test_rate_limiter_override(self, monkeypatch):
        monkeypatch.setenv("XBRLRAG_LLM_RPM", "60")   # would build a shared limiter…
        gw = build_xbrl_gateway("decomposer", api_key="sk-test", rate_limiter=None)
        assert gw.rate_limiter is None              # …but the explicit override wins

    def test_custom_rate_limiter_passthrough(self):
        custom = RateLimiter(rpm=5)
        gw = build_xbrl_gateway("synthesizer", api_key="sk-test", rate_limiter=custom)
        assert gw.rate_limiter is custom

    def test_stage_is_propagated(self):
        gw = build_xbrl_gateway("intent_router", api_key="sk-test")
        assert gw.default_stage == "intent_router"
