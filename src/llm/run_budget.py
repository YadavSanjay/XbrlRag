"""WS-4 / H1 — per-run cost ceiling + circuit breaker.

The gateway already *measures* per-query spend (every call accumulates cost /
tokens / calls onto the active :class:`~src.observability.trace_context.TraceContext`),
but nothing *caps* it. On a pathological run — a screen fanning out to hundreds of
per-row verifier batches, or a transient-error storm multiplied by retries ×
escalation — calls multiply with nothing to stop them. This is the only gap that
can overspend on a single run.

:class:`RunBudget` is a hard ceiling checked in :meth:`LLMGateway.complete` *before*
each provider call, against the running totals on the active trace:

  * ``max_cost_usd`` / ``max_llm_calls`` / ``max_tokens`` — aggregate per-run caps.
    When the accumulated total reaches a cap, the next call raises
    :class:`RunBudgetExceeded` instead of spending more.
  * ``max_consecutive_failures`` — a circuit breaker around the retry + escalation
    + batch fan-out: after N consecutive permanent LLM failures, the next call
    raises :class:`CircuitBreakerOpen` rather than hammering a failing provider.

All thresholds default to unset (``is_active()`` is False), so a budget with no
env configuration is inert and existing behaviour is unchanged. The gateway logs a
``budget_halt`` event when a cap trips, so a halt is an observable, typed stop —
not an unbounded run.
"""
from __future__ import annotations

import contextvars
import os
import threading
from dataclasses import dataclass
from typing import Optional


class RunHalt(Exception):
    """Base for a run-level halt raised by the budget/breaker (not a provider error)."""


class RunBudgetExceeded(RunHalt):
    """Raised when a per-run cost / token / call ceiling is reached."""


class CircuitBreakerOpen(RunHalt):
    """Raised when consecutive permanent failures trip the circuit breaker."""


def _env_float(name: str) -> float | None:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _env_int(name: str) -> int | None:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return None
    try:
        return int(val)
    except ValueError:
        return None


@dataclass
class RunBudget:
    """Per-run hard ceilings + circuit-breaker threshold. Unset fields are inert."""

    max_cost_usd: float | None = None
    max_llm_calls: int | None = None
    max_tokens: int | None = None
    max_consecutive_failures: int | None = None

    @classmethod
    def from_env(cls) -> RunBudget:
        """Build from XBRLRAG_RUN_MAX_* env vars. All optional; any unset => inert."""
        return cls(
            max_cost_usd=_env_float("XBRLRAG_RUN_MAX_COST_USD"),
            max_llm_calls=_env_int("XBRLRAG_RUN_MAX_LLM_CALLS"),
            max_tokens=_env_int("XBRLRAG_RUN_MAX_TOKENS"),
            max_consecutive_failures=_env_int("XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES"),
        )

    def is_active(self) -> bool:
        return any(v is not None for v in (
            self.max_cost_usd, self.max_llm_calls, self.max_tokens,
            self.max_consecutive_failures,
        ))

    def check(
        self,
        *,
        cost_usd: float = 0.0,
        llm_calls: int = 0,
        total_tokens: int = 0,
        consecutive_failures: int = 0,
    ) -> None:
        """Raise if any active ceiling is reached by the current running totals.

        The circuit breaker is checked first, so a failure storm trips even when
        the run is under its cost ceiling.
        """
        if (self.max_consecutive_failures is not None
                and consecutive_failures >= self.max_consecutive_failures):
            raise CircuitBreakerOpen(
                f"{consecutive_failures} consecutive LLM failures "
                f">= limit {self.max_consecutive_failures}")
        if self.max_llm_calls is not None and llm_calls >= self.max_llm_calls:
            raise RunBudgetExceeded(
                f"{llm_calls} LLM calls >= limit {self.max_llm_calls}")
        if self.max_cost_usd is not None and cost_usd >= self.max_cost_usd:
            raise RunBudgetExceeded(
                f"run cost ${cost_usd:.4f} >= limit ${self.max_cost_usd:.4f}")
        if self.max_tokens is not None and total_tokens >= self.max_tokens:
            raise RunBudgetExceeded(
                f"{total_tokens} tokens >= limit {self.max_tokens}")


class FlexWaitExceeded(RunHalt):
    """Raised when a trace's Flex 429 waiting would pass its cap (docs/specs/flex-retry-halt.md)."""


class RunStopped(RunHalt):
    """Raised when the run's stop signal is seen at a Flex wait (docs/specs/flex-retry-halt.md)."""


class RunStopScope:
    """Makes *stop* the run's stop signal in the current context (docs/specs/flex-retry-halt.md).

    Args:
        stop: The run's stop event; :func:`run_stop_requested` is True while it is set.
    """

    def __init__(self, stop: threading.Event) -> None:
        self.stop = stop
        self._token: contextvars.Token[threading.Event | None] | None = None

    def __enter__(self) -> RunStopScope:
        self._token = _RUN_STOP.set(self.stop)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _RUN_STOP.reset(self._token)
            self._token = None


# The run's stop event for the current context. A contextvar, so each eval worker thread
# that installs a scope sees only its own run's signal.
_RUN_STOP: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "xbrlrag_run_stop", default=None
)


def run_stop_requested() -> bool:
    """Whether the innermost active :class:`RunStopScope`'s event is set.

    Returns:
        False when no scope is active in the current context.
    """
    stop = _RUN_STOP.get()
    return stop is not None and stop.is_set()


def run_stop_scope_active() -> bool:
    """Whether a :class:`RunStopScope` is active in the current context.

    Returns:
        True inside a scope, whether or not its event is set.
    """
    return _RUN_STOP.get() is not None
