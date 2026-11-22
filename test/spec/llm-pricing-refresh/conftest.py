"""Fixtures for llm-pricing-refresh: the published prices, copied by hand from the pages.

``PER_MILLION`` holds USD per 1M tokens as (input, cached input, cache write, output), read on
2026-11-14 from the Anthropic pricing page (Claude Sonnet 5.5) and the OpenAI pricing page
(Standard, gpt-6-luna short and long context). ``hand_cost`` applies them to one call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

LONG_PROMPT_THRESHOLD = 272_000

PER_MILLION: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "claude-sonnet-5-5": {"base": (2.00, 0.10, 2.50, 10.00)},
    "gpt-6-luna": {"base": (0.10, 0.01, 0.125, 0.50), "long": (0.20, 0.02, 0.25, 0.75)},
}


def per_thousand(values: tuple[float, float, float, float]) -> dict[str, float]:
    """The rate-card keys for a per-1M tuple, in USD per 1,000 tokens."""
    inp, read, write, out = values
    return {"input": inp / 1000, "cached_input": read / 1000, "cache_write": write / 1000,
            "output": out / 1000}


def hand_cost(model: str, *, prompt: int, cached: int, written: int, completion: int) -> float:
    """One call's cost from PER_MILLION; the long tier applies when prompt > 272,000."""
    rows = PER_MILLION[model]
    key = "long" if prompt > LONG_PROMPT_THRESHOLD and "long" in rows else "base"
    inp, read, write, out = rows[key]
    return ((prompt - cached - written) * inp + cached * read + written * write
            + completion * out) / 1_000_000


@pytest.fixture(scope="session")
def spec() -> SimpleNamespace:
    """The published prices and the hand-cost helper."""
    return SimpleNamespace(per_million=PER_MILLION, per_thousand=per_thousand,
                           hand_cost=hand_cost, threshold=LONG_PROMPT_THRESHOLD)
