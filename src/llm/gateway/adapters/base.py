"""Provider adapter interface for the LLM gateway.

Design: docs/design/XbrlRag_LLM_Harness.md.

A :class:`ProviderAdapter` isolates everything provider-specific — SDK client
construction (incl. ``base_url``), request-param translation, response parsing,
and transient-error classification — behind a small, uniform surface. The
provider-neutral retry / cost / trace loop lives in
:class:`~src.llm.gateway.gateway.LLMGateway`, which drives adapters through this
interface.

The gateway calls, per attempt::

    client  = adapter.get_client()
    params  = adapter.build_params(messages=..., model=..., ...)
    raw     = adapter.invoke(client, params)      # may raise (transient or not)
    parsed  = adapter.parse(raw, stage=...)       # may raise ValueError (bad output)

Both ``invoke`` and ``parse`` may raise; the gateway classifies the exception
with :meth:`ProviderAdapter.is_transient` to decide whether to retry.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from src.llm.gateway.tools import ToolCall, ToolSpec, ToolsUnsupportedError
from src.llm.profiles import LlmConfigError, api_key

# Exception *class names* treated as transient (matched by name so we don't hard
# depend on any provider SDK's exception hierarchy being importable here). Shared
# across providers since the network-level failure modes are the same.
TRANSIENT_EXC_NAMES = frozenset({
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "APIError",
    "ServiceUnavailableError",
    "OverloadedError",       # Anthropic 529
    "Timeout",
    "ConnectTimeout",
    "ReadTimeout",
    "ConnectionError",
})

# HTTP statuses worth retrying.
TRANSIENT_STATUS = frozenset({408, 409, 425, 429})


def env_api_key(env_var: str) -> str | None:
    """A provider key from ``env_var`` or the file ``<env_var>_FILE`` names, else None.

    Adapters resolve their key lazily, so constructing one for an unused provider never
    requires a key; :func:`src.llm.profiles.api_key` does the reading.
    """
    try:
        return api_key(env_var)
    except LlmConfigError:
        return None


# Error types a provider may report inside a 2xx streamed reply (Anthropic sends an SSE
# ``error`` event after the HTTP 200, which the SDK raises with the response's status).
_TRANSIENT_BODY_TYPES = frozenset({
    "overloaded_error", "api_error", "rate_limit_error", "timeout_error",
})


def _body_error_type(exc: BaseException) -> str | None:
    """The ``error.type`` of an SDK error's JSON body, when it carries one."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return str(error["type"])
    return None


def classify_transient(exc: BaseException) -> bool:
    """Classifies an exception as retryable (transient) or not.

    The rule is the same for every provider. An HTTP status decides when the error has
    one: 408, 409, 425, 429 and every status of 500 or more are transient, and every
    other 4xx is not. Without an HTTP error status, connection and timeout errors are
    transient, recognized by the SDKs' class names or as Python's own ``TimeoutError``
    and ``ConnectionError``. An error event inside a 2xx streamed reply is transient
    when its body names an overload, rate-limit, timeout or server error.

    Args:
        exc: The exception raised by an adapter's ``invoke`` or ``parse``.

    Returns:
        True when the call may be retried.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "http_status", None)
    if isinstance(status, int) and status >= 400:
        return status in TRANSIENT_STATUS or status >= 500
    if isinstance(status, int) and _body_error_type(exc) in _TRANSIENT_BODY_TYPES:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return type(exc).__name__ in TRANSIENT_EXC_NAMES


def _error_object(exc: BaseException) -> dict | None:
    """The error object of an SDK error's body: ``body["error"]`` (Anthropic) or ``body``."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    return inner if isinstance(inner, dict) else body


def _names_resource_unavailable(error: dict | None) -> bool:
    """Whether the error object's code, type or message says a resource is unavailable."""
    if error is None:
        return False
    for key in ("code", "type", "message"):
        value = error.get(key)
        if isinstance(value, str):
            text = value.lower().replace("_", " ").replace("-", " ")
            if "resource unavailable" in text:
                return True
    return False


def _positive_finite(value: Any) -> float | None:
    """*value* as a positive finite float, else None (an HTTP-date included)."""
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def is_flex_unavailable(exc: BaseException, *, service_tier: str | None = None) -> bool:
    """Whether *exc* is a Flex "resource unavailable" 429 (docs/specs/llm-flex-retry.md).

    True when the error's HTTP status is 429, its error code is not ``insufficient_quota``,
    and either its error body (code, type or message) names a resource being unavailable,
    or *service_tier* is ``"flex"``.

    Args:
        exc: The exception raised by an adapter's ``invoke`` or ``parse``.
        service_tier: The OpenAI service tier the call was sent at; None when none was set
            or the provider is not OpenAI.

    Returns:
        True when the gateway retries the error under the Flex retry policy.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "http_status", None)
    if status != 429:
        return False
    error = _error_object(exc)
    if error is not None and (
        error.get("code") == "insufficient_quota" or error.get("type") == "insufficient_quota"
    ):
        return False
    return _names_resource_unavailable(error) or service_tier == "flex"


def retry_after_s(exc: BaseException) -> float | None:
    """The wait, in seconds, an error's response asks for (docs/specs/llm-flex-retry.md).

    Reads ``retry-after-ms`` (milliseconds) first, then ``retry-after`` (seconds), from the
    headers of the SDK error's ``response``, as the pinned SDKs do.

    Args:
        exc: The exception raised by an adapter's ``invoke`` or ``parse``.

    Returns:
        The positive, finite wait in seconds; None when there is no response, no such
        header, or its value is not a positive number (an HTTP-date included).
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    millis = _positive_finite(headers.get("retry-after-ms"))
    if millis is not None:
        return millis / 1000.0
    return _positive_finite(headers.get("retry-after"))


@dataclass
class ParsedResponse:
    """Normalized, provider-agnostic result of one completion call."""

    content: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0
    # WS-4 / B2: prompt tokens served from the provider's prompt cache (a subset of
    # prompt_tokens), billed at a lower rate. OpenAI caches automatically and reports
    # this under usage.prompt_tokens_details.cached_tokens; Anthropic reports
    # usage.cache_read_input_tokens. 0 when the provider reports no cache hit.
    cached_prompt_tokens: int = 0
    # Prompt tokens written to the provider's prompt cache (also a subset of
    # prompt_tokens), billed at the model's cache_write rate (configs/llm/pricing.yaml).
    # Anthropic reports usage.cache_creation_input_tokens; OpenAI reports
    # usage.prompt_tokens_details.cache_write_tokens on models that bill cache writes.
    cache_write_tokens: int = 0
    # The model id the provider's reply reports (``raw.model``), e.g. the dated snapshot
    # an alias resolved to; None when the reply names none (docs/specs/served-model-id.md).
    served_model: str | None = None
    # A tool turn's calls and its neutral assistant message, which the caller appends to
    # the conversation unchanged (docs/specs/frontier-sec-tools.md, requirements 2-3).
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict | None = None
    # The service tier the provider reports having served the call at (OpenAI
    # ``raw.service_tier``, Anthropic ``raw.usage.service_tier``); None when not reported.
    served_tier: str | None = None


def served_tier_of(raw: Any) -> str | None:
    """The service tier a raw provider reply reports, exactly as given.

    OpenAI's Chat Completions and Responses replies carry ``service_tier`` on the reply
    (e.g. ``"default"`` or ``"flex"``); Anthropic's carry it on ``usage``.

    Args:
        raw: The SDK reply (OpenAI response or Anthropic message).

    Returns:
        The first non-blank string of ``raw.service_tier`` and ``raw.usage.service_tier``,
        else None.
    """
    for source in (raw, getattr(raw, "usage", None)):
        tier = getattr(source, "service_tier", None) if source is not None else None
        if isinstance(tier, str) and tier.strip():
            return tier
    return None


def served_model_of(raw: Any) -> str | None:
    """The model id a raw provider reply reports, exactly as given.

    Args:
        raw: The SDK reply (OpenAI response or Anthropic message).

    Returns:
        ``raw.model`` when it is a non-blank string, else None.
    """
    model = getattr(raw, "model", None)
    if isinstance(model, str) and model.strip():
        return model
    return None


class ProviderAdapter(ABC):
    """Translate the gateway's neutral request into a provider call and back."""

    provider: str = "base"

    @abstractmethod
    def get_client(self) -> Any:
        """Return (lazily creating + caching) the provider SDK client."""

    @abstractmethod
    def build_params(
        self,
        *,
        messages: list[dict],
        model: str,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: str | dict | None = None,
        reasoning_effort: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        """Translate neutral params into the provider's request kwargs.

        ``max_completion_tokens`` / ``reasoning_effort`` are named after the
        OpenAI Chat Completions API (the current lingua franca); non-OpenAI
        adapters map or drop them as appropriate. ``response_format`` is neutral:
        the sentinel string ``"json"`` requests JSON-object output, a dict is a
        provider-native override, and ``None`` is freeform. ``extra`` carries any
        caller-supplied passthrough kwargs.
        """

    @abstractmethod
    def invoke(self, client: Any, params: dict) -> Any:
        """Make the raw provider call. May raise transient or permanent errors."""

    @abstractmethod
    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Parse a raw provider response into a :class:`ParsedResponse`.

        Raises ``ValueError`` for unusable output (empty content, content filter,
        no choices) — the gateway treats these as permanent (non-transient).
        """

    def build_tool_params(
        self,
        *,
        messages: list[dict],
        model: str,
        tools: list[ToolSpec],
        tool_choice: str = "auto",
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        """The kwargs of one request that offers tools (docs/specs/frontier-sec-tools.md).

        Raises:
            ToolsUnsupportedError: Always, here; an adapter with tools overrides this.
        """
        raise ToolsUnsupportedError(
            f"the {self.provider!r} provider adapter does not support tool calling"
        )

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Reads a reply that may hold tool calls, text or both.

        Raises:
            ToolsUnsupportedError: Always, here; an adapter with tools overrides this.
        """
        raise ToolsUnsupportedError(
            f"the {self.provider!r} provider adapter does not support tool calling"
        )

    def is_transient(self, exc: BaseException) -> bool:
        """Whether *exc* from :meth:`invoke` should be retried. Override to
        add provider-specific rules; defaults to the shared name/status classifier."""
        return classify_transient(exc)


class LLMEmptyReplyError(ValueError):
    """The reply had no content, though the provider billed it.

    A ``ValueError`` subclass, so a caller that treats unusable output as a permanent
    failure keeps doing so. It carries the reply's usage, so the gateway can record the
    call's tokens and cost (docs/specs/empty-reply-cost.md).

    Attributes:
        usage: The reply, parsed; its counts are 0 when the provider reported none.
    """

    def __init__(self, message: str, usage: ParsedResponse) -> None:
        """Holds the message and the reply's usage.

        Args:
            message: The error's text.
            usage: The reply, parsed.
        """
        super().__init__(message)
        self.usage = usage


class LLMRefusalError(ValueError):
    """The provider declined to answer; a final outcome, never retried.

    A ``ValueError`` subclass, so a caller that already treats unusable output as a
    permanent failure keeps doing so; a caller that retries or escalates on bad output
    checks for this type first and does not retry a refusal.

    A provider that reports usage on a refusal attaches it, so the gateway can record the
    refused call's tokens and, when billed, its cost (docs/specs/llm-refusals-and-store.md).

    Attributes:
        category: The provider's refusal category (Anthropic ``stop_details.category``),
            or None.
        usage: The reply's usage, mapped as a successful reply's is, with ``content=""``;
            None when the provider reported none.
        billed: Whether the provider bills this refusal.
    """

    def __init__(
        self,
        *args: Any,
        category: str | None = None,
        usage: ParsedResponse | None = None,
        billed: bool = False,
    ) -> None:
        super().__init__(*args)
        self.category = category
        self.usage = usage
        self.billed = billed


# The most cache points one request may carry: OpenAI allows 4 cache writes per request,
# one of them implicit, and Anthropic 4 breakpoints, one of them on the system prompt.
MAX_CACHE_POINTS = 3


def message_text(content: Any) -> str:
    """The text of a neutral message's ``content``: a string, or ``text`` parts joined.

    Args:
        content: A message's ``content``: a string or a list of ``{"type": "text"}``
            parts (other parts are skipped).

    Returns:
        The text; parts are joined with newlines.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return str(content or "")


def cache_point_count(messages: list[dict]) -> int:
    """Counts the messages marked ``"cache_point": True``.

    Args:
        messages: The gateway's provider-neutral message list.

    Returns:
        The number of cache points in the request.
    """
    return sum(1 for message in messages if message.get("cache_point") is True)
