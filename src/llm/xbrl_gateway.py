"""Shared LLM-gateway factory for the XBRL query stages (WS-4 / B9 + H1).

The five XBRL query stages (decomposer, intent router, plan generator,
synthesizer, faithfulness verifier) each construct their own
:class:`~src.llm.gateway.LLMGateway`. Building them through this factory injects
two cross-cutting controls that were previously unwired:

  * **B9 — a PROCESS-WIDE rate limiter.** One shared :class:`RateLimiter` across
    every stage and every concurrent query, so portfolio-wide fan-out (e.g. a
    ``scenariogen`` batch) throttles to the provider's RPM/TPM instead of
    429-storming. Built once from ``XBRLRAG_LLM_RPM`` / ``XBRLRAG_LLM_TPM``; if neither
    is set the limiter is ``None`` and rate limiting is a no-op.

  * **H1 — a per-run cost ceiling + circuit breaker.** A :class:`RunBudget` from
    ``XBRLRAG_RUN_MAX_*``; inert unless a threshold is set.

Both default to disabled, so wiring stages through the factory changes nothing
until the operator sets the env vars.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from src.llm.gateway import LLMGateway
from src.llm.rate_limiter import RateLimiter
from src.llm.run_budget import RunBudget

# Process-wide shared rate limiter (B9). Lazily built once; shared by every stage
# and every concurrent query so the throttle is global, not per-component.
_shared_rate_limiter: RateLimiter | None = None
_rate_limiter_built = False
_lock = threading.Lock()


def _env_int(name: str) -> int | None:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return None
    try:
        return int(val)
    except ValueError:
        return None


def get_shared_rate_limiter() -> RateLimiter | None:
    """The process-wide RateLimiter, or None when neither RPM nor TPM is set.

    Built once and cached; call :func:`reset_shared_state` (tests) to rebuild."""
    global _shared_rate_limiter, _rate_limiter_built
    if _rate_limiter_built:
        return _shared_rate_limiter
    with _lock:
        if _rate_limiter_built:
            return _shared_rate_limiter
        rpm = _env_int("XBRLRAG_LLM_RPM")
        tpm = _env_int("XBRLRAG_LLM_TPM")
        _shared_rate_limiter = RateLimiter(rpm=rpm, tpm=tpm) if (rpm or tpm) else None
        _rate_limiter_built = True
        return _shared_rate_limiter


def reset_shared_state() -> None:
    """Drop the cached shared rate limiter so it is rebuilt from the current env.
    Intended for tests that set XBRLRAG_LLM_RPM / XBRLRAG_LLM_TPM."""
    global _shared_rate_limiter, _rate_limiter_built
    with _lock:
        _shared_rate_limiter = None
        _rate_limiter_built = False


def build_xbrl_gateway(
    stage: str,
    *,
    api_key: str | None = None,
    rate_limiter: object = "__shared__",
    run_budget: RunBudget | None = None,
    **kwargs,
) -> LLMGateway:
    """Construct an LLMGateway for an XBRL stage with the shared rate limiter (B9)
    and a per-run budget (H1) injected. ``rate_limiter``/``run_budget`` may be
    overridden (e.g. in tests); by default the shared limiter and an env-derived
    budget are used."""
    limiter = get_shared_rate_limiter() if rate_limiter == "__shared__" else rate_limiter
    budget = run_budget if run_budget is not None else RunBudget.from_env()
    return LLMGateway(
        stage=stage,
        api_key=api_key,
        rate_limiter=limiter,
        run_budget=budget,
        **kwargs,
    )
