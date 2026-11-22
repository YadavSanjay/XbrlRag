"""Fixtures for the deepseek-provider spec tests (docs/specs/deepseek-provider.md).

Everything here is written by hand from the spec, never read back from the code under test:

- ``DEEPSEEK_RATES``: requirement 2's table (DeepSeek's peak rates, USD per 1M tokens / 1000);
- ``DEEPSEEK_MODELS``: requirement 1's model rows and ``DEEPSEEK_PROFILE`` its profile;
- ``make_docs()``: a fresh, valid in-memory ``(llm.yaml, pricing.yaml)`` pair with the three
  providers (openai, anthropic, deepseek), one OpenAI and one Anthropic model besides the two
  DeepSeek models, and a profile per provider;
- ``loads(doc, pricing)``: whether the pair loads, False on ``LlmConfigError`` (so a test that
  expects a document to load fails by assertion, not by an exception);
- ``reply(...)``: a plain object shaped like a DeepSeek Chat Completions reply.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.llm.profiles import LlmConfigError, load_llm_settings

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
LLM_YAML = REPO_ROOT / "configs" / "llm" / "llm.yaml"
PRICING_YAML = REPO_ROOT / "configs" / "llm" / "pricing.yaml"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"

# Requirement 2: DeepSeek's Models & Pricing page (retrieved 2026-10-29), peak rates per 1M
# tokens (cache hit / cache miss / output) divided by 1,000.
DEEPSEEK_RATES: dict[str, dict[str, float]] = {
    "deepseek-flash": {"input": 0.30 / 1000, "cached_input": 0.006 / 1000, "output": 1.20 / 1000},
    "deepseek-v4-pro": {
        "input": 1.32 / 1000, "cached_input": 0.044 / 1000, "output": 3.96 / 1000,
    },
}

# Requirement 1's model rows.
DEEPSEEK_MODELS: dict[str, dict[str, Any]] = {
    "deepseek-flash": {"provider": "deepseek", "max_output_tokens": 8000, "effort": "low"},
    "deepseek-v4-pro": {"provider": "deepseek", "max_output_tokens": 16000, "effort": "high"},
}

# Requirement 1's profile.
DEEPSEEK_PROFILE: dict[str, str] = {
    "small": "deepseek-flash",
    "medium": "deepseek-flash",
    "frontier": "deepseek-v4-pro",
}

# ---------------------------------------------------------------------------- hand-built docs

DOC: dict[str, Any] = {
    "active_profile": "${XBRLRAG_LLM_PROFILE:-}",
    "providers": {
        "openai": {"api_key_env": "OPENAI_API_KEY"},
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY"},
        "deepseek": {"api_key_env": DEEPSEEK_KEY_ENV, "base_url": DEEPSEEK_BASE_URL},
    },
    "models": {
        "gpt-5-mini": {"provider": "openai", "max_output_tokens": 8000},
        "claude-sonnet-5-5": {
            "provider": "anthropic", "max_output_tokens": 16000, "temperature": False,
            "effort": "high",
        },
        **copy.deepcopy(DEEPSEEK_MODELS),
    },
    "profiles": {
        "openai": {"small": "gpt-5-mini", "medium": "gpt-5-mini", "frontier": "gpt-5-mini"},
        "anthropic": {
            "small": "claude-sonnet-5-5", "medium": "claude-sonnet-5-5",
            "frontier": "claude-sonnet-5-5",
        },
        "deepseek": dict(DEEPSEEK_PROFILE),
    },
}

PRICING: dict[str, Any] = {
    "gpt-5-mini": {"input": 0.00025, "output": 0.002, "cached_input": 0.000025},
    "claude-sonnet-5-5": {
        "input": 0.002, "output": 0.01, "cached_input": 0.0002, "cache_write": 0.0025,
    },
    **copy.deepcopy(DEEPSEEK_RATES),
}


def make_docs() -> tuple[dict[str, Any], dict[str, Any]]:
    """A fresh deep copy of the valid ``(llm.yaml, pricing.yaml)`` pair."""
    return copy.deepcopy(DOC), copy.deepcopy(PRICING)


def loads(doc: dict[str, Any], pricing: dict[str, Any]) -> bool:
    """Whether the pair loads; False when it raises ``LlmConfigError``."""
    try:
        load_llm_settings(doc, pricing, profile="openai", environ={})
    except LlmConfigError:
        return False
    return True


def committed_llm_doc() -> dict[str, Any]:
    """The committed ``configs/llm/llm.yaml`` as parsed YAML."""
    return yaml.safe_load(LLM_YAML.read_text(encoding="utf-8"))


def committed_pricing_doc() -> dict[str, Any]:
    """The committed ``configs/llm/pricing.yaml`` as parsed YAML."""
    return yaml.safe_load(PRICING_YAML.read_text(encoding="utf-8"))


def reply(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    reasoning_tokens: int,
    content: str = '{"ok": true}',
    model: str = "deepseek-flash",
) -> SimpleNamespace:
    """A plain object shaped like a DeepSeek Chat Completions reply."""
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(role="assistant", content=content),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


class SpecTables:
    """The spec's tables and helpers, as one fixture."""

    base_url = DEEPSEEK_BASE_URL
    key_env = DEEPSEEK_KEY_ENV
    rates = DEEPSEEK_RATES
    models = DEEPSEEK_MODELS
    profile = DEEPSEEK_PROFILE
    make_docs = staticmethod(make_docs)
    loads = staticmethod(loads)
    committed_llm_doc = staticmethod(committed_llm_doc)
    committed_pricing_doc = staticmethod(committed_pricing_doc)
    reply = staticmethod(reply)


@pytest.fixture(scope="session")
def spec() -> type[SpecTables]:
    """The spec's tables and helpers."""
    return SpecTables
