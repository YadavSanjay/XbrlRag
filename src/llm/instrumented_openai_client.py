"""Backward-compatible OpenAI chat-completions gateway (thin façade).

Per docs/design/XbrlRag_LLM_Harness.md, the retry / cost /
trace loop and the OpenAI-specific request/response handling were split into a
provider-neutral gateway (:class:`src.llm.gateway.LLMGateway`) driving a
:class:`~src.llm.gateway.adapters.OpenAIAdapter`. New call sites should use
``LLMGateway`` with a resolved :class:`~src.llm.gateway.ModelRef` directly.

This module is retained so existing imports keep working: ``InstrumentedChatClient``
is now a thin OpenAI-pinned façade over the gateway, and ``LLMCallResult`` /
``_is_transient`` / the transient-classification constants / body helpers are
re-exported from their new homes.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, List, Optional

from src.llm.cost_tracker import CostTracker
from src.llm.gateway.adapters import OpenAIAdapter
from src.llm.gateway.adapters.base import (  # re-exported for backward compat
    TRANSIENT_EXC_NAMES,
    TRANSIENT_STATUS,
)
from src.llm.gateway.adapters.base import (
    classify_transient as _is_transient,
)
from src.llm.gateway.gateway import (  # re-exported for backward compat
    LLM_BODY_MAX_CHARS,
    LLMCallResult,
    LLMGateway,
    _message_text,
    _trace_llm_bodies,
    _truncate,
)
from src.logging_config import get_logger

logger = get_logger(__name__)

__all__ = [
    "InstrumentedChatClient",
    "LLMCallResult",
    "_is_transient",
    "TRANSIENT_EXC_NAMES",
    "TRANSIENT_STATUS",
    "LLM_BODY_MAX_CHARS",
    "_message_text",
    "_truncate",
    "_trace_llm_bodies",
]


class InstrumentedChatClient:
    """OpenAI-pinned façade over :class:`~src.llm.gateway.LLMGateway`.

    Preserves the original constructor + ``complete()`` surface (and the
    ``_client`` attribute the unit tests inject a fake into). Internally it owns
    a single :class:`OpenAIAdapter` and a gateway configured to use it.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        stage: str = "llm",
        cost_tracker: CostTracker | None = None,
        rate_limiter: Any = None,
        max_attempts: int = 3,
        base_backoff: float = 0.5,
        max_backoff: float = 8.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")
        self.api_key = api_key
        self.default_stage = stage
        self._adapter = OpenAIAdapter(api_key=api_key)
        self._gateway = LLMGateway(
            stage=stage,
            api_key=api_key,
            cost_tracker=cost_tracker,
            rate_limiter=rate_limiter,
            max_attempts=max_attempts,
            base_backoff=base_backoff,
            max_backoff=max_backoff,
            sleep_fn=sleep_fn,
            adapters={"openai": self._adapter},
        )

    # The unit tests (and any legacy callers) inject a fake OpenAI client via
    # ``client._client = ...``; proxy that onto the underlying adapter.
    @property
    def _client(self) -> Any:
        return self._adapter._client

    @_client.setter
    def _client(self, value: Any) -> None:
        self._adapter._client = value

    @property
    def cost_tracker(self) -> CostTracker:
        return self._gateway.cost_tracker

    @property
    def rate_limiter(self) -> Any:
        return self._gateway.rate_limiter

    @property
    def max_attempts(self) -> int:
        return self._gateway.max_attempts

    def complete(
        self,
        *,
        messages: list[dict],
        model: str,
        stage: str | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
        reasoning_effort: str | None = None,
        prompt_templates: list[str] | None = None,
        prompt_version: str | None = None,
        **kwargs: Any,
    ) -> LLMCallResult:
        """Delegate to the gateway, resolving *model* to an OpenAI ModelRef."""
        return self._gateway.complete(
            messages=messages,
            model=model,
            stage=stage or self.default_stage,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            prompt_templates=prompt_templates,
            prompt_version=prompt_version,
            **kwargs,
        )
