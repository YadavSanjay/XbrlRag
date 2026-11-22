"""Per-run request options: the OpenAI service tier and the long-context request timeout.

Spec: docs/specs/openai-6-1-profile.md. The tier is read from ``XBRLRAG_OPENAI_SERVICE_TIER``
(``scripts/eval/run_suite.py --service-tier`` sets it for a run); the OpenAI adapter sends it
on every call and the eval runner records it with the run. The long-context stages get a
15-minute request timeout, because their calls can take minutes at OpenAI's Flex tier.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

from src.llm.profiles import LlmConfigError

SERVICE_TIER_ENV = "XBRLRAG_OPENAI_SERVICE_TIER"
# The values OpenAI's API takes for the request's service_tier.
SERVICE_TIERS: tuple[str, ...] = ("auto", "default", "flex", "priority")

# Stages whose single call sends a whole filing (or runs a long tool loop): the
# long-context arms (oracle and resolved share "long_context") and frontier_sec_tools.
LONG_CONTEXT_STAGES = frozenset({"long_context", "long_context_multi", "frontier_sec_tools"})
# OpenAI's flex-processing guide: raise the client timeout to 15 minutes.
LONG_CONTEXT_TIMEOUT_S = 900.0

# The Flex "resource unavailable" 429 retry policy (docs/specs/llm-flex-retry.md).
FLEX_RETRY_MAX_ATTEMPTS_ENV = "XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS"
FLEX_RETRY_BASE_ENV = "XBRLRAG_FLEX_RETRY_BASE_S"
FLEX_RETRY_MAX_BACKOFF_ENV = "XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S"
# The per-trace cap on total Flex waiting, in seconds (docs/specs/flex-retry-halt.md).
FLEX_TRACE_WAIT_CAP_ENV = "XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S"
DEFAULT_FLEX_TRACE_WAIT_CAP_S = 3600.0
FLEX_RETRY_DEFAULTS: tuple[int, float, float] = (15, 5.0, 300.0)


def openai_service_tier(environ: Mapping[str, str] | None = None) -> str | None:
    """The OpenAI service tier requested for this process, or None for the default.

    Args:
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        ``auto``, ``default``, ``flex`` or ``priority``; None when the variable is unset or
        blank.

    Raises:
        LlmConfigError: If the variable holds any other value.
    """
    env = os.environ if environ is None else environ
    value = (env.get(SERVICE_TIER_ENV) or "").strip()
    if not value:
        return None
    if value not in SERVICE_TIERS:
        raise LlmConfigError(
            f"{SERVICE_TIER_ENV}={value!r} is not a service tier; use one of {SERVICE_TIERS}"
        )
    return value


def flex_retry_policy(environ: Mapping[str, str] | None = None) -> tuple[int, float, float]:
    """The retry policy for Flex "resource unavailable" 429s (docs/specs/llm-flex-retry.md).

    Read from ``XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS`` (default 15),
    ``XBRLRAG_FLEX_RETRY_BASE_S`` (default 5.0) and ``XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S``
    (default 300.0); an unset or blank variable takes its default.

    Args:
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        ``(max_attempts, base_backoff_s, max_backoff_s)``.

    Raises:
        LlmConfigError: If a value does not parse, ``max_attempts`` is below 1, the base is
            not a positive finite number, or the maximum is not finite or is below the base.
    """
    env = os.environ if environ is None else environ
    default_attempts, default_base, default_max = FLEX_RETRY_DEFAULTS

    def _text(name: str) -> str:
        return (env.get(name) or "").strip()

    raw_attempts = _text(FLEX_RETRY_MAX_ATTEMPTS_ENV)
    if raw_attempts:
        try:
            max_attempts = int(raw_attempts)
        except ValueError:
            raise LlmConfigError(
                f"{FLEX_RETRY_MAX_ATTEMPTS_ENV}={raw_attempts!r} is not an integer"
            ) from None
    else:
        max_attempts = default_attempts
    if max_attempts < 1:
        raise LlmConfigError(f"{FLEX_RETRY_MAX_ATTEMPTS_ENV}={max_attempts} must be at least 1")

    def _seconds(name: str, default: float) -> float:
        raw = _text(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise LlmConfigError(f"{name}={raw!r} is not a number of seconds") from None

    base = _seconds(FLEX_RETRY_BASE_ENV, default_base)
    if not math.isfinite(base) or base <= 0:
        raise LlmConfigError(f"{FLEX_RETRY_BASE_ENV}={base!r} must be a positive finite number")
    maximum = _seconds(FLEX_RETRY_MAX_BACKOFF_ENV, default_max)
    if not math.isfinite(maximum) or maximum < base:
        raise LlmConfigError(
            f"{FLEX_RETRY_MAX_BACKOFF_ENV}={maximum!r} must be finite and at least "
            f"{FLEX_RETRY_BASE_ENV} ({base!r})"
        )
    return max_attempts, base, maximum


def request_timeout_s(stage: str) -> float | None:
    """The request timeout of a stage's LLM calls, or None for the SDK's default.

    Args:
        stage: The calling stage's name.

    Returns:
        900.0 for the long-context stages, None for every other stage.
    """
    return LONG_CONTEXT_TIMEOUT_S if stage in LONG_CONTEXT_STAGES else None


def flex_trace_wait_cap_s(environ: Mapping[str, str] | None = None) -> float:
    """The most seconds one trace may wait on Flex 429s (docs/specs/flex-retry-halt.md).

    Read from ``XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S`` (default 3600.0); an unset or blank
    variable takes the default.

    Args:
        environ: The environment to read; ``os.environ`` when None.

    Returns:
        The cap in seconds.

    Raises:
        LlmConfigError: The value is not a positive finite number.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(FLEX_TRACE_WAIT_CAP_ENV) or "").strip()
    if not raw:
        return DEFAULT_FLEX_TRACE_WAIT_CAP_S
    try:
        cap = float(raw)
    except ValueError:
        raise LlmConfigError(
            f"{FLEX_TRACE_WAIT_CAP_ENV}={raw!r} is not a number of seconds"
        ) from None
    if not math.isfinite(cap) or cap <= 0:
        raise LlmConfigError(
            f"{FLEX_TRACE_WAIT_CAP_ENV}={raw!r} must be a positive finite number of seconds"
        )
    return cap
