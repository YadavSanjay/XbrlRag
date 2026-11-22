"""Per-response usage meter: the paid-model spend of one API response.

Opens a private :class:`~src.observability.trace_context.TraceContext` so every
``LLMGateway`` call inside the block records its cost there, without the query
events or audit persistence of :func:`~src.observability.trace_context.trace_query`
(a raw fact lookup is not a query and must not add an ``audit.query_audit`` row).
See docs/specs/usage-cost.md.
"""

from __future__ import annotations

import uuid
from contextvars import Token
from types import TracebackType

# The meter is a sibling of trace_query in this package, so it sets the same
# context var directly rather than going through trace_query's side effects.
from src.observability.trace_context import TraceContext, _trace_var, current_trace


class UsageMeter:
    """Context manager that measures the LLM spend of the enclosed block.

    On exit the active trace is restored, and an enclosing trace (if any) is
    credited with the block's spend. Exceptions are never suppressed.
    """

    def __init__(self) -> None:
        self._trace = TraceContext(query_id=uuid.uuid4().hex[:16])
        self._outer: TraceContext | None = None
        self._token: Token[TraceContext | None] | None = None

    def __enter__(self) -> UsageMeter:
        self._outer = current_trace()
        self._token = _trace_var.set(self._trace)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _trace_var.reset(self._token)
            self._token = None
        if self._outer is not None:
            # Hand the block's spend to the enclosing trace so its totals stay complete.
            self._outer.llm_calls += self._trace.llm_calls
            self._outer.total_prompt_tokens += self._trace.total_prompt_tokens
            self._outer.total_completion_tokens += self._trace.total_completion_tokens
            self._outer.total_cached_prompt_tokens += self._trace.total_cached_prompt_tokens
            self._outer.total_cost_usd += self._trace.total_cost_usd
            self._outer.total_retries += self._trace.total_retries

    def usage(self) -> dict[str, float | int]:
        """Return the spend recorded so far.

        Returns:
            ``{"cost_usd": float, "llm_calls": int}``; ``cost_usd`` is ``0.0``
            when no paid call was made.
        """
        return {
            "cost_usd": float(self._trace.total_cost_usd),
            "llm_calls": int(self._trace.llm_calls),
        }
