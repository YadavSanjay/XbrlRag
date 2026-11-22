"""Provider-neutral LLM gateway for the XBRL query flow.

Design: docs/design/XbrlRag_LLM_Harness.md.

This is the retry / cost / trace loop previously fused into
``InstrumentedChatClient``, now provider-agnostic: it drives a
:class:`~src.llm.gateway.adapters.base.ProviderAdapter` (selected per call from
the :class:`~src.llm.gateway.model_ref.ModelRef`'s provider) and stamps every
``llm_call`` event with ``provider`` / ``model_series`` / ``model_tier`` on top
of the existing ``model`` / ``prompt_version`` dimensions.

Behaviour vs. the old client is otherwise identical — bounded exponential
backoff on transient errors only, token+USD cost via ``CostTracker``, per-call
``llm_call``/``retry`` events tied to the active ``TraceContext``, and the same
empty-content / content-filter ``ValueError`` surfacing.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, List, NoReturn, Optional, Union

from src.llm.cost_tracker import CostTracker
from src.llm.gateway.adapters import (
    MAX_CACHE_POINTS,
    AnthropicAdapter,
    DeepSeekAdapter,
    LLMEmptyReplyError,
    LLMRefusalError,
    OpenAIAdapter,
    OpenAICompatAdapter,
    ParsedResponse,
    ProviderAdapter,
    cache_point_count,
)
from src.llm.gateway.adapters.base import is_flex_unavailable, retry_after_s
from src.llm.gateway.model_ref import ModelRef, resolve_model_ref
from src.llm.gateway.tools import ToolCall, ToolSpec
from src.llm.request_options import (
    flex_retry_policy,
    flex_trace_wait_cap_s,
    openai_service_tier,
    request_timeout_s,
)
from src.llm.run_budget import (
    FlexWaitExceeded,
    RunBudget,
    RunHalt,
    RunStopped,
    run_stop_requested,
    run_stop_scope_active,
)
from src.logging_config import get_logger
from src.observability.trace_context import current_query_id, current_trace, log_event

logger = get_logger(__name__)

# Inside a run stop scope, a Flex wait sleeps in slices of at most this many seconds, so a
# stop is seen within one slice (docs/specs/flex-retry-halt.md).
_FLEX_STOP_SLICE_S = 10.0

# Whether to capture the exact system/user prompt + model response text on each
# `llm_call` event (so the Trace Explorer can show them). On by default; set
# XBRLRAG_TRACE_LLM_BODIES=0 to record only token/cost/latency. Bodies are
# truncated to LLM_BODY_MAX_CHARS.
LLM_BODY_MAX_CHARS = 24000


def _trace_llm_bodies() -> bool:
    return os.environ.get("XBRLRAG_TRACE_LLM_BODIES", "1").strip().lower() not in {"0", "false", "no", ""}


def _message_text(messages: list[dict], role: str) -> str:
    """Flatten the text of every message with *role* (content is a str or a list
    of ``{type, text}`` parts, per the call sites in src/retriever/xbrl/*)."""
    out: list[str] = []
    for m in messages or []:
        if m.get("role") != role:
            continue
        content = m.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(str(part.get("text", "")))
    return "\n".join(out)


def _truncate(text: str, limit: int = LLM_BODY_MAX_CHARS) -> str:
    if text and len(text) > limit:
        return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"
    return text


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate (~4 chars/token) for rate-limiter pre-checks."""
    chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    chars += len(str(part.get("text", "")))
    return chars // 4


def _add_stage_timeout(params: dict, stage: str) -> None:
    """Adds the stage's request ``timeout`` to *params* when it has one.

    The long-context stages wait up to 15 minutes for a reply (Flex calls can take
    minutes); every other stage keeps the SDK client's default. Both SDKs take a
    per-request ``timeout`` keyword. A caller's own ``timeout`` wins
    (docs/specs/openai-6-1-profile.md, requirement 5).

    Args:
        params: The adapter's request kwargs, changed in place.
        stage: The calling stage.
    """
    timeout = request_timeout_s(stage)
    if timeout is not None:
        params.setdefault("timeout", timeout)


def retry_wait_s(
    attempt: int, base_backoff: float, max_backoff: float, retry_after: float | None = None
) -> float:
    """The wait before the next attempt (docs/specs/llm-flex-retry.md, requirement 4).

    The backoff is ``min(max_backoff, base_backoff * 2 ** (attempt - 1))``. A *retry_after*
    the provider asked for raises it to ``min(max_backoff, max(backoff, retry_after))``.

    Args:
        attempt: How many failures of this kind the call has had, from 1.
        base_backoff: The first wait, in seconds.
        max_backoff: The longest wait, in seconds.
        retry_after: The provider's ``Retry-After``, in seconds, or None.

    Returns:
        The wait, in seconds.
    """
    backoff = min(max_backoff, base_backoff * (2 ** (attempt - 1)))
    if retry_after is None:
        return backoff
    return min(max_backoff, max(backoff, retry_after))


@dataclass
class LLMCallResult:
    """Outcome of one (possibly retried) completion call, provider-tagged."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    attempts: int
    retries: int
    finish_reason: str | None
    outcome: str  # "ok" (failures raise; their llm_call events say "error" or "refusal")
    cached_prompt_tokens: int = 0  # B2: prompt tokens served from the provider cache
    cache_write_tokens: int = 0  # prompt tokens written to the provider cache
    prompt_templates: list[str] | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model_series: str | None = None
    model_tier: str | None = None
    # The model id the provider reported serving (``model`` stays the requested id).
    served_model: str | None = None
    # A tool turn's calls and its neutral assistant message (docs/specs/frontier-sec-tools.md).
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict | None = None


# Backwards-friendly alias for new call sites.
LLMResult = LLMCallResult


class LLMGateway:
    """Provider-neutral, instrumented completion gateway.

    Construct one per LLM-using component. Adapters are created lazily per
    provider on first use (so an OpenAI-only run never constructs an Anthropic
    client, and vice versa). Pass a shared :class:`CostTracker` for process-wide
    cost aggregation; otherwise a private one is used and per-query cost is still
    captured on the active trace + ``llm_call`` events.
    """

    def __init__(
        self,
        *,
        stage: str = "llm",
        api_key: str | None = None,
        cost_tracker: CostTracker | None = None,
        rate_limiter: Any = None,
        run_budget: RunBudget | None = None,
        max_attempts: int = 3,
        base_backoff: float = 0.5,
        max_backoff: float = 8.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        adapters: dict | None = None,
    ):
        self.default_stage = stage
        self._api_key = api_key
        self.cost_tracker = cost_tracker or CostTracker()
        self.rate_limiter = rate_limiter
        # WS-4 / H1: per-run cost ceiling + circuit breaker (None => disabled).
        self.run_budget = run_budget
        self.max_attempts = max(1, int(max_attempts))
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._sleep = sleep_fn
        self._adapters: dict[str, ProviderAdapter] = dict(adapters or {})

    # ------------------------------------------------------------------

    def _adapter_for(self, ref: ModelRef) -> ProviderAdapter:
        adapter = self._adapters.get(ref.provider)
        if adapter is None:
            adapter = self._build_default_adapter(ref)
            self._adapters[ref.provider] = adapter
        return adapter

    def _build_default_adapter(self, ref: ModelRef) -> ProviderAdapter:
        if ref.provider == "openai":
            return OpenAIAdapter(api_key=self._api_key)
        if ref.provider == "openai_compat":
            return OpenAICompatAdapter(api_key=self._api_key, base_url=ref.base_url)
        if ref.provider == "anthropic":
            return AnthropicAdapter(api_key=self._api_key)
        if ref.provider == "deepseek":
            return DeepSeekAdapter(api_key=self._api_key, base_url=ref.base_url)
        raise ValueError(f"No LLM adapter registered for provider {ref.provider!r}")

    def complete(
        self,
        *,
        messages: list[dict],
        model: str | ModelRef | None = None,
        model_ref: ModelRef | None = None,
        stage: str | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: str | dict | None = None,
        reasoning_effort: str | None = None,
        prompt_templates: list[str] | None = None,
        prompt_version: str | None = None,
        **kwargs: Any,
    ) -> LLMCallResult:
        """Make a completion call with retry, cost tracking, and tracing.

        Pass either a resolved ``model_ref`` (preferred — carries provider /
        series / tier) or a bare ``model`` id/alias which is resolved here.
        ``prompt_templates`` / ``prompt_version`` (from ``PromptManager.signature``)
        are stamped on the ``llm_call`` event for reproducibility.

        A message may carry ``"cache_point": True`` (the prompt up to the end of that
        message is worth caching); messages reach the adapter unchanged. A provider
        refusal (:class:`LLMRefusalError`) is final: it is never retried, is recorded as
        an ``llm_call`` event with ``outcome="refusal"`` (with its tokens and, when the
        provider bills it, its cost) and is re-raised.

        Raises:
            ValueError: If the request carries more than ``MAX_CACHE_POINTS`` cache
                points (checked before any provider call).
            LLMRefusalError: If the provider declined to answer.
        """
        stage = stage or self.default_stage
        points = cache_point_count(messages)
        if points > MAX_CACHE_POINTS:
            raise ValueError(
                f"{points} cache points in one request (stage={stage}); at most "
                f"{MAX_CACHE_POINTS} are allowed"
            )
        ref = model_ref if model_ref is not None else resolve_model_ref(model)
        adapter = self._adapter_for(ref)

        params = adapter.build_params(
            messages=messages,
            model=ref.model,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            extra=kwargs or None,
        )
        _add_stage_timeout(params, stage)
        return self._call(
            adapter=adapter, params=params, parse=adapter.parse, ref=ref, stage=stage,
            messages=messages, max_completion_tokens=max_completion_tokens,
            reasoning_effort=reasoning_effort, prompt_templates=prompt_templates,
            prompt_version=prompt_version, tools=False,
        )

    def _call(
        self,
        *,
        adapter: ProviderAdapter,
        params: dict,
        parse: Callable[..., ParsedResponse],
        ref: ModelRef,
        stage: str,
        messages: list[dict],
        max_completion_tokens: int | None,
        reasoning_effort: str | None,
        prompt_templates: list[str] | None,
        prompt_version: str | None,
        tools: bool,
    ) -> LLMCallResult:
        """The one retry / budget / cost / trace loop behind :meth:`complete` and
        :meth:`complete_tools`; *parse* reads the raw reply, and *tools* adds the call
        count to the ``llm_call`` event."""
        # H1: hard per-run ceiling + circuit breaker, checked BEFORE spending.
        self._enforce_run_budget(stage, ref)
        # A trace that hit its Flex wait cap stays halted: a degrading stage that swallowed
        # the halt must not start a fresh wait (docs/specs/flex-retry-halt.md).
        trace = current_trace()
        if trace is not None and trace.flex_wait_capped:
            self._halt(stage, ref, FlexWaitExceeded(
                f"trace already waited {trace.flex_wait_s:.0f}s on Flex 429s (cap reached)"))

        if self.rate_limiter is not None:
            est = _estimate_tokens(messages) + (max_completion_tokens or 0)
            self.rate_limiter.wait_if_needed(est)

        client = adapter.get_client()
        t_total = time.perf_counter()
        attempts = 0  # every attempt, of every kind
        flex_failures = 0  # Flex "resource unavailable" 429s
        other_failures = 0  # every other transient error
        flex_policy: tuple[int, float, float] | None = None  # read at the first Flex 429
        logger.debug("LLM call stage=%s provider=%s model=%s", stage, ref.provider, ref.model)
        # The tier actually sent (the adapter adds the run's tier on the OpenAI platform).
        sent_tier = params.get("service_tier")
        requested_tier = sent_tier if isinstance(sent_tier, str) else None

        while True:
            attempts += 1
            try:
                raw = adapter.invoke(client, params)
                parsed = parse(raw, stage=stage)
                return self._on_success(
                    parsed, ref, stage, attempts, t_total,
                    prompt_templates, prompt_version, messages,
                    reasoning_effort=reasoning_effort, tools=tools,
                    requested_tier=requested_tier,
                )
            except LLMRefusalError as refusal:
                # A refusal is the provider's final answer: no retry, and it neither
                # advances nor resets the circuit breaker (the call itself worked).
                self._on_refusal(
                    refusal, ref, stage, attempts, t_total,
                    prompt_templates, prompt_version, reasoning_effort,
                    requested_tier=requested_tier,
                )
                logger.warning(
                    "LLM refused (stage=%s provider=%s model=%s): %s",
                    stage, ref.provider, ref.model, refusal,
                )
                raise
            except Exception as exc:
                # A Flex "resource unavailable" 429 is retried under its own, longer policy,
                # counted apart from the other transient errors, and never touches the
                # breaker (docs/specs/llm-flex-retry.md, requirements 5-6).
                tier = openai_service_tier() if ref.provider == "openai" else None
                if is_flex_unavailable(exc, service_tier=tier):
                    flex_failures += 1
                    if flex_policy is None:
                        flex_policy = flex_retry_policy()
                    flex_max, flex_base, flex_cap = flex_policy
                    if flex_failures < flex_max:
                        wait = retry_wait_s(flex_failures, flex_base, flex_cap, retry_after_s(exc))
                        self._before_flex_wait(stage, ref, wait)
                        log_event(
                            "retry", stage=stage, provider=ref.provider, model=ref.model,
                            attempt=attempts, max_attempts=flex_max,
                            error_class="flex_unavailable", error=repr(exc),
                            wait_ms=round(wait * 1000.0, 1),
                        )
                        logger.warning(
                            "LLM Flex resource unavailable (stage=%s flex attempt=%d/%d), "
                            "retrying in %.1fs: %s",
                            stage, flex_failures, flex_max, wait, exc,
                        )
                        self._flex_wait(stage, ref, wait)
                        continue
                    self._on_flex_exhausted(
                        exc, ref, stage, attempts, t_total,
                        prompt_templates, prompt_version, reasoning_effort,
                    )
                    raise

                transient = adapter.is_transient(exc)
                if transient:
                    other_failures += 1
                if transient and other_failures < self.max_attempts:
                    wait = retry_wait_s(
                        other_failures, self.base_backoff, self.max_backoff, retry_after_s(exc)
                    )
                    log_event(
                        "retry", stage=stage, provider=ref.provider, model=ref.model,
                        attempt=attempts, max_attempts=self.max_attempts,
                        error_class="transient", error=repr(exc),
                        wait_ms=round(wait * 1000.0, 1),
                    )
                    logger.warning(
                        "LLM transient error (stage=%s attempt=%d/%d), retrying in %.2fs: %s",
                        stage, other_failures, self.max_attempts, wait, exc,
                    )
                    self._sleep(wait)
                    continue

                latency_ms = (time.perf_counter() - t_total) * 1000.0
                # H1: a permanent (retry-exhausted) failure advances the circuit
                # breaker's consecutive-failure counter on the active run.
                trace = current_trace()
                if trace is not None:
                    trace.consecutive_failures += 1
                usage_fields = (
                    self._record_empty_reply(exc.usage, ref, stage, attempts,
                                             requested_tier=requested_tier)
                    if isinstance(exc, LLMEmptyReplyError) else {}
                )
                log_event(
                    "llm_call", stage=stage, provider=ref.provider, model=ref.model,
                    model_series=ref.series, model_tier=ref.tier, outcome="error",
                    **usage_fields,
                    error_class="transient" if transient else "permanent",
                    error=repr(exc), attempts=attempts, retries=attempts - 1,
                    latency_ms=round(latency_ms, 1), reasoning_effort=reasoning_effort,
                    prompt_templates=prompt_templates, prompt_version=prompt_version,
                    served_model=None,
                )
                logger.error(
                    "LLM call failed (stage=%s provider=%s) after %d attempt(s): %s",
                    stage, ref.provider, attempts, exc,
                )
                raise

    def complete_tools(
        self,
        *,
        messages: list[dict],
        tools: list[ToolSpec],
        model_ref: ModelRef,
        stage: str | None = None,
        tool_choice: str = "auto",
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        prompt_templates: list[str] | None = None,
        prompt_version: str | None = None,
    ) -> LLMCallResult:
        """One model turn that may call tools, on the same loop as :meth:`complete`.

        Args:
            messages: The neutral conversation (docs/specs/frontier-sec-tools.md).
            tools: The tools offered.
            model_ref: The resolved model.
            stage: The calling stage; the gateway's default when None.
            tool_choice: ``"auto"``, or ``"none"`` to force a text reply.
            max_completion_tokens: The output budget.
            temperature: The sampling temperature, when the model takes one.
            reasoning_effort: The requested reasoning effort.
            prompt_templates: Prompt refs stamped on the ``llm_call`` event.
            prompt_version: Prompt version stamped on the event.

        Returns:
            The result, with ``tool_calls`` and ``assistant_message``.

        Raises:
            ToolsUnsupportedError: If the provider's adapter has no tool support.
            RunHalt: The run's budget or circuit breaker stopped the call.
            LLMRefusalError: If the provider declined to answer.
            ValueError: If the request carries more than ``MAX_CACHE_POINTS`` cache
                points, or the reply has neither text nor a tool call.
        """
        stage = stage or self.default_stage
        points = cache_point_count(messages)
        if points > MAX_CACHE_POINTS:
            raise ValueError(
                f"{points} cache points in one request (stage={stage}); at most "
                f"{MAX_CACHE_POINTS} are allowed"
            )
        adapter = self._adapter_for(model_ref)
        params = adapter.build_tool_params(
            messages=messages,
            model=model_ref.model,
            tools=tools,
            tool_choice=tool_choice,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        _add_stage_timeout(params, stage)
        return self._call(
            adapter=adapter, params=params, parse=adapter.parse_tool_turn, ref=model_ref,
            stage=stage, messages=messages, max_completion_tokens=max_completion_tokens,
            reasoning_effort=reasoning_effort, prompt_templates=prompt_templates,
            prompt_version=prompt_version, tools=True,
        )


    # ------------------------------------------------------------------

    def _halt(self, stage: str, ref: ModelRef, halt: RunHalt) -> NoReturn:
        """Logs a ``budget_halt`` event for *halt* and raises it; the breaker is untouched."""
        log_event(
            "budget_halt", stage=stage, provider=ref.provider, model=ref.model,
            reason=type(halt).__name__, detail=str(halt),
        )
        logger.warning("run halted around a Flex wait (stage=%s): %s", stage, halt)
        raise halt

    def _before_flex_wait(self, stage: str, ref: ModelRef, wait: float) -> None:
        """Checks the run before a Flex wait (docs/specs/flex-retry-halt.md).

        In order: the run's stop signal, the budget and breaker, then the trace's wait cap
        (its total so far plus *wait* past the cap halts, and the trace stays capped).
        Otherwise *wait* is added to the trace's total.

        Raises:
            RunStopped: The run's stop signal is set.
            RunHalt: The budget or breaker tripped.
            FlexWaitExceeded: The wait would pass the trace's cap.
        """
        if run_stop_requested():
            self._halt(stage, ref, RunStopped("run stop requested before a Flex wait"))
        self._enforce_run_budget(stage, ref)
        trace = current_trace()
        if trace is None:
            return
        cap = flex_trace_wait_cap_s()
        if trace.flex_wait_s + wait > cap:
            trace.flex_wait_capped = True
            self._halt(stage, ref, FlexWaitExceeded(
                f"Flex wait {wait:.1f}s after {trace.flex_wait_s:.1f}s would pass the "
                f"trace cap of {cap:.0f}s"))
        trace.flex_wait_s += wait

    def _flex_wait(self, stage: str, ref: ModelRef, wait: float) -> None:
        """Sleeps out a Flex wait, then re-checks the run (docs/specs/flex-retry-halt.md).

        Inside a :class:`~src.llm.run_budget.RunStopScope` the wait is sliced into sleeps
        of at most ``_FLEX_STOP_SLICE_S``, checking the stop signal between slices;
        outside one it is a single sleep.

        Raises:
            RunStopped: The run's stop signal was set during or by the end of the wait.
            RunHalt: The budget or breaker tripped by the end of the wait.
        """
        if not run_stop_scope_active():
            self._sleep(wait)
        else:
            left = wait
            while left > 0:
                piece = min(_FLEX_STOP_SLICE_S, left)
                self._sleep(piece)
                left -= piece
                if left > 0 and run_stop_requested():
                    self._halt(stage, ref, RunStopped("run stop requested during a Flex wait"))
        if run_stop_requested():
            self._halt(stage, ref, RunStopped("run stop requested during a Flex wait"))
        self._enforce_run_budget(stage, ref)

    def _enforce_run_budget(self, stage: str, ref: ModelRef) -> None:
        """Raise a :class:`RunHalt` if the active run has reached a cost/token/call
        ceiling or the circuit breaker has tripped. No-op when no budget is
        configured or no trace is active. Logs a ``budget_halt`` event on trip."""
        budget = self.run_budget
        if budget is None or not budget.is_active():
            return
        trace = current_trace()
        if trace is None:
            return
        total_tokens = trace.total_prompt_tokens + trace.total_completion_tokens
        try:
            budget.check(
                cost_usd=trace.total_cost_usd,
                llm_calls=trace.llm_calls,
                total_tokens=total_tokens,
                consecutive_failures=trace.consecutive_failures,
            )
        except RunHalt as halt:
            log_event(
                "budget_halt", stage=stage, provider=ref.provider, model=ref.model,
                reason=type(halt).__name__, detail=str(halt),
                llm_calls=trace.llm_calls, total_cost_usd=round(trace.total_cost_usd, 6),
                total_tokens=total_tokens, consecutive_failures=trace.consecutive_failures,
            )
            logger.warning("run halted before LLM call (stage=%s): %s", stage, halt)
            raise

    # ------------------------------------------------------------------

    def _record_empty_reply(
        self, usage: ParsedResponse, ref: ModelRef, stage: str, attempts: int,
        *, requested_tier: str | None = None,
    ) -> dict[str, Any]:
        """Records a billed reply with no content (docs/specs/empty-reply-cost.md).

        Args:
            usage: The reply, parsed.
            ref: The model called.
            stage: The calling stage.
            attempts: Attempts made, including this one.
            requested_tier: The ``service_tier`` the request sent, or None.

        Returns:
            The token, finish-reason and cost fields for the call's ``llm_call`` event.
        """
        record = self.cost_tracker.record_usage(
            model=ref.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            metadata={"stage": stage, "provider": ref.provider,
                      "query_id": current_query_id(), "outcome": "error"},
            cached_prompt_tokens=usage.cached_prompt_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )
        trace = current_trace()
        if trace is not None:
            trace.record_llm_call(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=record.estimated_cost_usd,
                retries=attempts - 1,
                cached_prompt_tokens=usage.cached_prompt_tokens,
                model=ref.model,
                served_model=usage.served_model,
                provider=ref.provider,
                stage=stage,
                requested_tier=requested_tier,
                served_tier=usage.served_tier,
            )
        return {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.prompt_tokens + usage.completion_tokens,
            "cached_prompt_tokens": usage.cached_prompt_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "finish_reason": usage.finish_reason,
            "cost_usd": round(record.estimated_cost_usd, 6),
        }

    def _on_flex_exhausted(
        self,
        exc: BaseException,
        ref: ModelRef,
        stage: str,
        attempts: int,
        t_total: float,
        prompt_templates: list[str] | None,
        prompt_version: str | None,
        reasoning_effort: str | None,
    ) -> None:
        """Records a call that ran out of Flex retries (docs/specs/llm-flex-retry.md).

        The ``llm_call`` event says ``outcome="error"``, ``error_class="flex_unavailable"``.
        Flex 429s are not billed, so nothing reaches the cost tracker or the trace's call
        count, and ``trace.consecutive_failures`` is left unchanged.

        Args:
            exc: The last Flex 429, which the caller re-raises.
            ref: The call's resolved model.
            stage: The calling stage.
            attempts: Attempts made, of every kind, including this one.
            t_total: ``time.perf_counter()`` at the start of the call.
            prompt_templates: Prompt refs stamped on the event.
            prompt_version: Prompt version stamped on the event.
            reasoning_effort: The requested reasoning effort, stamped on the event.
        """
        latency_ms = (time.perf_counter() - t_total) * 1000.0
        log_event(
            "llm_call", stage=stage, provider=ref.provider, model=ref.model,
            model_series=ref.series, model_tier=ref.tier, outcome="error",
            error_class="flex_unavailable", error=repr(exc),
            attempts=attempts, retries=attempts - 1, latency_ms=round(latency_ms, 1),
            reasoning_effort=reasoning_effort,
            prompt_templates=prompt_templates, prompt_version=prompt_version,
            served_model=None,
        )
        logger.error(
            "LLM call failed (stage=%s provider=%s): Flex resource unavailable after %d "
            "attempt(s): %s",
            stage, ref.provider, attempts, exc,
        )

    def _on_refusal(
        self,
        refusal: LLMRefusalError,
        ref: ModelRef,
        stage: str,
        attempts: int,
        t_total: float,
        prompt_templates: list[str] | None,
        prompt_version: str | None,
        reasoning_effort: str | None,
        *,
        requested_tier: str | None = None,
    ) -> None:
        """Records a refused call's tokens and cost (docs/specs/llm-refusals-and-store.md).

        The refusal's usage (when the provider reported one) goes on the ``llm_call``
        event and the active trace. Its cost is the cost tracker's cost of that usage when
        the provider bills the refusal, and 0 otherwise; only a billed refusal is recorded
        in the cost tracker. ``trace.consecutive_failures`` is left unchanged.

        Args:
            refusal: The refusal the adapter raised.
            ref: The call's resolved model.
            stage: The calling stage.
            attempts: Attempts made, including this one.
            t_total: ``time.perf_counter()`` at the start of the call.
            prompt_templates: Prompt refs stamped on the event.
            prompt_version: Prompt version stamped on the event.
            reasoning_effort: The requested reasoning effort, stamped on the event.
            requested_tier: The ``service_tier`` the request sent, or None.
        """
        latency_ms = (time.perf_counter() - t_total) * 1000.0
        usage = getattr(refusal, "usage", None)
        billed = bool(getattr(refusal, "billed", False))
        cost_usd = 0.0
        if usage is not None and billed:
            record = self.cost_tracker.record_usage(
                model=ref.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                metadata={"stage": stage, "provider": ref.provider,
                          "query_id": current_query_id(), "outcome": "refusal"},
                cached_prompt_tokens=usage.cached_prompt_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            cost_usd = record.estimated_cost_usd

        token_fields: dict = {}
        if usage is not None:
            token_fields = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.prompt_tokens + usage.completion_tokens,
                "cached_prompt_tokens": usage.cached_prompt_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
            }

        served_model = getattr(usage, "served_model", None) if usage is not None else None
        trace = current_trace()
        if trace is not None:
            trace.record_llm_call(
                prompt_tokens=usage.prompt_tokens if usage is not None else 0,
                completion_tokens=usage.completion_tokens if usage is not None else 0,
                cost_usd=cost_usd,
                retries=attempts - 1,
                cached_prompt_tokens=usage.cached_prompt_tokens if usage is not None else 0,
                model=ref.model,
                served_model=served_model,
                provider=ref.provider,
                stage=stage,
                requested_tier=requested_tier,
                served_tier=getattr(usage, "served_tier", None) if usage is not None else None,
            )

        log_event(
            "llm_call", stage=stage, provider=ref.provider, model=ref.model,
            model_series=ref.series, model_tier=ref.tier, outcome="refusal",
            error_class="refusal", error=repr(refusal),
            refusal_category=getattr(refusal, "category", None), billed=billed,
            cost_usd=round(cost_usd, 6), **token_fields,
            attempts=attempts, retries=attempts - 1, latency_ms=round(latency_ms, 1),
            reasoning_effort=reasoning_effort,
            prompt_templates=prompt_templates, prompt_version=prompt_version,
            served_model=served_model,
        )

    def _on_success(
        self, parsed, ref: ModelRef, stage: str, attempts: int, t_total: float,
        prompt_templates: list[str] | None = None,
        prompt_version: str | None = None,
        messages: list[dict] | None = None,
        reasoning_effort: str | None = None,
        tools: bool = False,
        requested_tier: str | None = None,
    ) -> LLMCallResult:
        latency_ms = (time.perf_counter() - t_total) * 1000.0
        tool_calls = list(getattr(parsed, "tool_calls", None) or [])
        if tools and not tool_calls and not str(parsed.content).strip():
            raise ValueError("LLM returned empty message content and no tool call")

        record = self.cost_tracker.record_usage(
            model=ref.model,
            prompt_tokens=parsed.prompt_tokens,
            completion_tokens=parsed.completion_tokens,
            metadata={"stage": stage, "provider": ref.provider, "query_id": current_query_id()},
            cached_prompt_tokens=parsed.cached_prompt_tokens,
            cache_write_tokens=parsed.cache_write_tokens,
        )
        cost_usd = record.estimated_cost_usd
        retries = attempts - 1
        served_model = getattr(parsed, "served_model", None)
        served_tier = getattr(parsed, "served_tier", None)

        trace = current_trace()
        if trace is not None:
            trace.record_llm_call(
                prompt_tokens=parsed.prompt_tokens,
                completion_tokens=parsed.completion_tokens,
                cost_usd=cost_usd,
                retries=retries,
                cached_prompt_tokens=parsed.cached_prompt_tokens,
                model=ref.model,
                served_model=served_model,
                provider=ref.provider,
                stage=stage,
                requested_tier=requested_tier,
                served_tier=served_tier,
            )
            trace.consecutive_failures = 0  # H1: a success resets the breaker

        body_fields: dict = {}
        if _trace_llm_bodies():
            body_fields = {
                "system": _truncate(_message_text(messages or [], "system")),
                "user": _truncate(_message_text(messages or [], "user")),
                "response": _truncate(str(parsed.content)),
            }
        if tools:
            body_fields["tool_calls"] = len(tool_calls)

        log_event(
            "llm_call", stage=stage, provider=ref.provider, model=ref.model,
            model_series=ref.series, model_tier=ref.tier, outcome="ok",
            prompt_tokens=parsed.prompt_tokens, completion_tokens=parsed.completion_tokens,
            total_tokens=parsed.prompt_tokens + parsed.completion_tokens,
            cached_prompt_tokens=parsed.cached_prompt_tokens,
            cache_write_tokens=parsed.cache_write_tokens,
            cost_usd=round(cost_usd, 6),
            reasoning_tokens=parsed.reasoning_tokens, reasoning_effort=reasoning_effort,
            latency_ms=round(latency_ms, 1), attempts=attempts, retries=retries,
            finish_reason=parsed.finish_reason,
            prompt_templates=prompt_templates, prompt_version=prompt_version,
            served_model=served_model,
            **body_fields,
        )

        return LLMCallResult(
            content=str(parsed.content),
            model=ref.model,
            prompt_tokens=parsed.prompt_tokens,
            completion_tokens=parsed.completion_tokens,
            total_tokens=parsed.prompt_tokens + parsed.completion_tokens,
            cached_prompt_tokens=parsed.cached_prompt_tokens,
            cache_write_tokens=parsed.cache_write_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            attempts=attempts,
            retries=retries,
            finish_reason=parsed.finish_reason,
            outcome="ok",
            prompt_templates=prompt_templates,
            prompt_version=prompt_version,
            provider=ref.provider,
            model_series=ref.series,
            model_tier=ref.tier,
            served_model=served_model,
            tool_calls=tool_calls,
            assistant_message=getattr(parsed, "assistant_message", None),
        )
