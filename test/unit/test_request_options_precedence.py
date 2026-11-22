"""Precedence of the per-run request options (docs/specs/openai-6-1-profile.md).

The locked spec tests fix what the run's service tier and the long-context timeout are and
where they reach. These cover the one choice the implementation made on top: a value the
caller already put in the request wins over the run's.
"""

from __future__ import annotations

import pytest

from src.llm.gateway.adapters.openai_adapter import OpenAIAdapter
from src.llm.gateway.gateway import _add_stage_timeout
from src.llm.request_options import LONG_CONTEXT_TIMEOUT_S, SERVICE_TIER_ENV

_MESSAGES = [{"role": "user", "content": "hi"}]


def test_a_callers_service_tier_wins_over_the_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")
    params = OpenAIAdapter().build_params(
        messages=_MESSAGES, model="gpt-6.1-sol", extra={"service_tier": "priority"}
    )
    assert params["service_tier"] == "priority"


def test_the_runs_tier_fills_a_request_without_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SERVICE_TIER_ENV, "flex")
    params = OpenAIAdapter().build_params(messages=_MESSAGES, model="gpt-6.1-sol")
    assert params["service_tier"] == "flex"


def test_a_callers_timeout_wins_over_the_stages() -> None:
    params = {"timeout": 30.0}
    _add_stage_timeout(params, "long_context")
    assert params["timeout"] == 30.0


def test_the_stage_timeout_fills_a_request_without_one() -> None:
    params: dict = {}
    _add_stage_timeout(params, "frontier_sec_tools")
    assert params["timeout"] == LONG_CONTEXT_TIMEOUT_S


def test_a_short_stage_gets_no_timeout_key() -> None:
    params: dict = {}
    _add_stage_timeout(params, "synthesizer")
    assert "timeout" not in params
