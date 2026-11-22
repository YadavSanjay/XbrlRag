"""Fixtures for the llm-provider-profiles spec tests (docs/specs/llm-provider-profiles.md).

Everything here is written by hand from the spec, never read back from the code under test:

- ``make_doc()`` / ``make_pricing()``: fresh deep copies of one valid ``llm.yaml`` document and
  its ``pricing.yaml`` rate card, built from requirements 1, 2 and 5. Besides the three committed
  profiles they hold a ``mixed`` profile that uses both providers.
- ``spec``: the spec's own tables (requirement 2's profiles, C5's committed rates) plus two
  values copied from ``main`` at f8ac973 when the spec was written: ``XBRL_STAGE_TIERS`` and
  ``MODEL_PRICING``, which requirements 4 and 5 say must not change.
- ``make_gateway()``: a real ``LLMGateway`` whose provider adapter is the fake (the I/O boundary
  C6 names), registered under every provider key.
- ``llm_env``: scrubs every variable that picks a profile or supplies a key, so a test sets
  exactly the ones it means.

Fixtures are session-scoped factories where hypothesis tests use them.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]

# Every provider key a model id could resolve to, so a test does not depend on which one
# the gateway infers.
PROVIDERS = ("openai", "openai_compat", "anthropic", "huggingface")

TIERS = ("small", "medium", "frontier")

# Requirement 2's table.
PROFILE_TABLE: dict[str, dict[str, str]] = {
    "openai": {"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"},
    "openai-5.6": {"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"},
    "anthropic": {
        "small": "claude-haiku-5-5",
        "medium": "claude-sonnet-5-5",
        "frontier": "claude-opus-5-5",
    },
}

# XBRL_STAGE_TIERS on main at f8ac973 (requirement 4: each stage keeps its tier).
STAGE_TIERS: dict[str, str] = {
    "query_decomposer": "medium",
    "intent_router": "medium",
    "plan_generator": "medium",
    "synthesizer": "medium",
    "faithfulness_verifier": "small",
    "closed_book": "medium",
    "naive_rag": "medium",
}

# C5's table of committed rates, USD per 1,000 tokens (Anthropic's and OpenAI's pricing pages,
# 2026-10, per 1M tokens / 1000, cache writes at 1.25x input).
COMMITTED_RATES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {
        "input": 0.004, "output": 0.02, "cached_input": 0.0002, "cache_write": 0.005,
    },
    # llm-pricing-refresh C1: Sonnet 5.5 cache reads are $0.10/MTok (0.05x input), not 0.1x
    # (Anthropic pricing page, read 2026-11-14).
    "claude-sonnet-5-5": {
        "input": 0.002, "output": 0.01, "cached_input": 0.0001, "cache_write": 0.0025,
    },
    "claude-haiku-5-5": {
        "input": 0.0001, "output": 0.0005, "cached_input": 0.00001, "cache_write": 0.000125,
    },
    "gpt-5.6-sol": {"input": 0.004, "output": 0.02, "cached_input": 0.0004, "cache_write": 0.005},
}
# C5: claude-haiku-5-5's tier above 100,000 prompt tokens.
HAIKU_TIER = {
    "above_prompt_tokens": 100_000,
    "input": 0.0005,
    "output": 0.0025,
    "cached_input": 0.00005,
    "cache_write": 0.000625,
}

# MODEL_PRICING on main at f8ac973 (requirement 5: every row keeps its rates).
TODAY_PRICING: dict[str, dict[str, float]] = {
    "gpt-5": {"input": 0.00125, "output": 0.01, "cached_input": 0.000125},
    "gpt-5-mini": {"input": 0.00025, "output": 0.002, "cached_input": 0.000025},
    "gpt-5-nano": {"input": 0.00005, "output": 0.0004, "cached_input": 0.000005},
    "gpt-5.6-sol": {"input": 0.004, "output": 0.02, "cached_input": 0.0004},
    "gpt-5.6-terra": {"input": 0.002, "output": 0.012, "cached_input": 0.0002},
    "gpt-5.6-luna": {"input": 0.0002, "output": 0.0012, "cached_input": 0.00002},
    "gpt-6-sol": {"input": 0.002, "output": 0.01, "cached_input": 0.0002},
    "gpt-6-luna": {"input": 0.0001, "output": 0.0005, "cached_input": 0.00001},
    "gpt-4o": {"input": 0.0025, "output": 0.01, "cached_input": 0.00125},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006, "cached_input": 0.000075},
    "claude-opus-4-8": {"input": 0.005, "output": 0.025, "cached_input": 0.0005},
    "claude-opus-4-7": {"input": 0.005, "output": 0.025, "cached_input": 0.0005},
    "claude-opus-4-6": {"input": 0.005, "output": 0.025, "cached_input": 0.0005},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015, "cached_input": 0.0003},
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005, "cached_input": 0.0001},
    "meta-llama/Llama-2-7b-chat-hf": {"input": 0.0, "output": 0.0},
    "mistralai/Mistral-7B-Instruct-v0.1": {"input": 0.0, "output": 0.0},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-4-32k": {"input": 0.06, "output": 0.12},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
    "gpt-3.5-turbo-16k": {"input": 0.003, "output": 0.004},
}

# ---------------------------------------------------------------------------- hand-built docs

DOC: dict[str, Any] = {
    "active_profile": "${XBRLRAG_LLM_PROFILE:-}",
    "providers": {
        "openai": {"api_key_env": "OPENAI_API_KEY"},
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
    },
    "models": {
        "gpt-5-nano": {"provider": "openai", "max_output_tokens": 4000},
        "gpt-5-mini": {"provider": "openai", "max_output_tokens": 8000, "temperature": True},
        "gpt-5": {"provider": "openai", "max_output_tokens": 8000},
        "gpt-5.6-luna": {"provider": "openai", "max_output_tokens": 4000},
        "gpt-5.6-terra": {"provider": "openai", "max_output_tokens": 8000},
        "gpt-5.6-sol": {"provider": "openai", "max_output_tokens": 8000},
        "claude-haiku-5-5": {
            "provider": "anthropic", "max_output_tokens": 8000, "temperature": False,
            "effort": "low",
        },
        "claude-sonnet-5-5": {
            "provider": "anthropic", "max_output_tokens": 16000, "temperature": False,
            "effort": "medium",
        },
        "claude-opus-5-5": {
            "provider": "anthropic", "max_output_tokens": 16000, "temperature": False,
        },
    },
    "profiles": {
        **copy.deepcopy(PROFILE_TABLE),
        "mixed": {"small": "gpt-5-nano", "medium": "claude-sonnet-5-5", "frontier": "gpt-5"},
        # arm-model-from-profile makes openai-6.1 requirement 3's row 4 (the fallback), so a
        # hand-built document names it too. A stand-in row over this document's own models,
        # distinct from every other profile's.
        "openai-6.1": {
            "small": "gpt-5.6-luna", "medium": "gpt-5.6-sol", "frontier": "claude-opus-5-5",
        },
    },
}

PRICING: dict[str, Any] = {
    "gpt-5-nano": dict(TODAY_PRICING["gpt-5-nano"]),
    "gpt-5-mini": dict(TODAY_PRICING["gpt-5-mini"]),
    "gpt-5": dict(TODAY_PRICING["gpt-5"]),
    "gpt-5.6-luna": dict(TODAY_PRICING["gpt-5.6-luna"]),
    "gpt-5.6-terra": dict(TODAY_PRICING["gpt-5.6-terra"]),
    "gpt-5.6-sol": dict(COMMITTED_RATES["gpt-5.6-sol"]),
    "claude-haiku-5-5": {**COMMITTED_RATES["claude-haiku-5-5"], "tiers": [dict(HAIKU_TIER)]},
    "claude-sonnet-5-5": dict(COMMITTED_RATES["claude-sonnet-5-5"]),
    "claude-opus-5-5": dict(COMMITTED_RATES["claude-opus-5-5"]),
}


class SpecTables:
    """The spec's tables and the values copied from main, as one fixture."""

    profile_table = PROFILE_TABLE
    stage_tiers = STAGE_TIERS
    committed_rates = COMMITTED_RATES
    haiku_tier = HAIKU_TIER
    today_pricing = TODAY_PRICING
    tiers = TIERS
    repo_root = REPO_ROOT


@pytest.fixture(scope="session")
def spec() -> type[SpecTables]:
    """The spec's tables."""
    return SpecTables


@pytest.fixture(scope="session")
def make_doc():
    """A factory for a fresh, valid ``llm.yaml`` document."""
    return lambda: copy.deepcopy(DOC)


@pytest.fixture(scope="session")
def make_pricing():
    """A factory for a fresh, valid ``pricing.yaml`` document."""
    return lambda: copy.deepcopy(PRICING)


# ---------------------------------------------------------------------------- audit sink


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off: no trace these tests open is persisted."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


# ---------------------------------------------------------------------------- environment

_LLM_ENV = (
    "XBRLRAG_LLM_PROFILE",
    "XBRLRAG_USE_REPLACEMENT_MODELS",
    "ARAG_LLM_PROFILE",
    "ARAG_USE_REPLACEMENT_MODELS",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_FILE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_FILE",
    "OPENAI_BASE_URL",
    "XBRLRAG_LLM_RPM",
    "XBRLRAG_LLM_TPM",
    "XBRLRAG_RUN_MAX_COST_USD",
    "XBRLRAG_RUN_MAX_LLM_CALLS",
    "XBRLRAG_RUN_MAX_TOKENS",
    "XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES",
)


@pytest.fixture
def llm_env(monkeypatch):
    """Removes every profile, switch, key and budget variable; returns ``monkeypatch``."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------- fake adapter


class FakeAdapter(ProviderAdapter):
    """A provider adapter that records each request and replies with fixed usage.

    ``cache_write_tokens`` is set on the parsed response after construction, so the fake
    builds against today's ``ParsedResponse`` as well as the extended one.
    """

    provider = "fake"

    def __init__(
        self,
        *,
        content: str = '{"ok": true}',
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_prompt_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        self.content = content
        self.usage = (prompt_tokens, completion_tokens, cached_prompt_tokens, cache_write_tokens)
        self.requests: list[dict[str, Any]] = []
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        self.requests.append(dict(kwargs))
        return dict(kwargs)

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        return params

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        prompt, completion, cached, written = self.usage
        parsed = ParsedResponse(
            content=self.content,
            finish_reason="stop",
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_prompt_tokens=cached,
        )
        parsed.cache_write_tokens = written
        return parsed


@pytest.fixture(scope="session")
def make_gateway():
    """A factory: ``make_gateway(**usage) -> (gateway, adapter, cost_tracker)``."""

    def build(**usage: Any) -> tuple[LLMGateway, FakeAdapter, CostTracker]:
        adapter = FakeAdapter(**usage)
        tracker = CostTracker()
        gateway = LLMGateway(
            stage="spec",
            cost_tracker=tracker,
            rate_limiter=None,
            run_budget=RunBudget(),
            max_attempts=1,
            sleep_fn=lambda _s: None,
            adapters={p: adapter for p in PROVIDERS},
        )
        return gateway, adapter, tracker

    return build


# ---------------------------------------------------------------------------- run_suite


@pytest.fixture(scope="session")
def run_suite_module():
    """``scripts/eval/run_suite.py`` imported by path (it is a script, not a package)."""
    path = REPO_ROOT / "scripts" / "eval" / "run_suite.py"
    spec_ = importlib.util.spec_from_file_location("run_suite_llm_profiles_under_test", path)
    module = importlib.util.module_from_spec(spec_)
    sys.modules["run_suite_llm_profiles_under_test"] = module
    spec_.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def tiny_suite(tmp_path_factory) -> Path:
    """A one-case synthetic suite file."""
    path = tmp_path_factory.mktemp("suite") / "suite.json"
    path.write_text(
        json.dumps(
            {
                "name": "spec",
                "version": "v1",
                "cases": [{"id": "c1", "question": "What was AAA revenue in fiscal 2024?",
                           "expected_value": 1.0, "ticker": "AAA", "fy": 2024}],
            }
        ),
        encoding="utf-8",
    )
    return path
