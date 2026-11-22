"""OpenAI (and OpenAI-compatible) provider adapter.

Design: docs/design/XbrlRag_LLM_Harness.md. This is the OpenAI
Chat Completions logic previously inlined in ``InstrumentedChatClient``, moved
behind the :class:`~src.llm.gateway.adapters.base.ProviderAdapter` seam
*verbatim* in behaviour — same param names, same finish-reason handling, same
empty-content ``ValueError``, same reasoning-token capture.

:class:`OpenAICompatAdapter` is the same adapter pointed at a ``base_url`` (vLLM,
Together, OpenRouter, a local server, …) — the OpenAI SDK speaks to any
OpenAI-compatible endpoint, so no request/response changes are needed.

Explicit prompt-cache points (docs/specs/llm-anthropic-and-caching.md): OpenAI documents
explicit cache breakpoints only for the Responses API, so a request with at least one
``"cache_point": True`` message goes to ``responses.create``; every other request goes
to Chat Completions exactly as before. Responses calls send ``store: False`` (Chat
Completions already defaults to it). :meth:`OpenAIAdapter.invoke` routes on the built
params (``input`` vs ``messages``), and :meth:`OpenAIAdapter.parse` reads either reply.
The SDK client is built with ``max_retries=0``: the gateway's retry loop is the only one.
"""
from __future__ import annotations

import inspect
import json
import os
from typing import Any

from src.llm.gateway.adapters.base import (
    LLMEmptyReplyError,
    ParsedResponse,
    ProviderAdapter,
    cache_point_count,
    env_api_key,
    message_text,
    served_model_of,
    served_tier_of,
)
from src.llm.gateway.tools import ToolCall, ToolSpec, parse_arguments
from src.llm.request_options import openai_service_tier
from src.logging_config import get_logger

logger = get_logger(__name__)


def _cache_write_tokens(usage: Any, prompt_details: Any) -> int:
    """Prompt tokens written to OpenAI's prompt cache, 0 when not reported.

    Models that bill cache writes (the gpt-5.6 family) report them as
    ``usage.prompt_tokens_details.cache_write_tokens`` (the field in OpenAI's
    ``CompletionUsage.PromptTokensDetails`` schema; the pinned Python SDK predates it, but
    its response models keep unknown fields, so ``getattr`` reads it). A top-level
    ``usage.cache_write_tokens`` is read as a fallback. The gpt-5 family reports neither.
    """
    for source in (prompt_details, usage):
        if source is None:
            continue
        value = getattr(source, "cache_write_tokens", None)
        if value:
            return int(value)
    return 0


_EXPLICIT = {"mode": "explicit"}
# Responses roles per neutral role (system instructions are "developer").
_RESPONSES_ROLE = {"system": "developer", "developer": "developer", "user": "user",
                   "assistant": "assistant"}


def _responses_input(messages: list[dict]) -> list[dict]:
    """The neutral messages as Responses ``input`` items, one text part each.

    Args:
        messages: The neutral messages; a ``"cache_point": True`` message's part carries
            an explicit ``prompt_cache_breakpoint``.

    Returns:
        The ``input`` list.

    Raises:
        ValueError: If a message has a role the Responses API has no item for.
    """
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        mapped = _RESPONSES_ROLE.get(str(role))
        if mapped is None:
            raise ValueError(f"no Responses input role for message role {role!r}")
        part: dict = {
            "type": "output_text" if mapped == "assistant" else "input_text",
            "text": message_text(m.get("content")),
        }
        if m.get("cache_point") is True:
            part["prompt_cache_breakpoint"] = dict(_EXPLICIT)
        items.append({"role": mapped, "content": [part]})
    return items


def _undeclared_kwargs(method: Any, params: dict) -> list[str]:
    """The keys of *params* that *method*'s signature neither declares nor accepts.

    The pinned OpenAI SDK predates some Responses request fields (such as
    ``prompt_cache_options``); its typed ``create`` rejects them as keywords, so
    :meth:`OpenAIAdapter.invoke` sends those in ``extra_body``, which the SDK merges
    into the JSON request unchanged.
    """
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return []
    accepted = signature.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in accepted.values()):
        return []
    return [key for key in params if key not in accepted]


def _responses_finish_reason(raw: Any) -> str | None:
    """A Chat-style finish reason for a Responses reply."""
    status = getattr(raw, "status", None)
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = getattr(raw, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason == "max_output_tokens":
            return "length"
        if reason == "content_filter":
            return "content_filter"
        return str(reason) if reason else "incomplete"
    return str(status) if status else None


def _responses_usage(
    raw: Any, *, content: str, finish_reason: str | None, stage: str
) -> ParsedResponse:
    """A Responses reply's usage as a :class:`ParsedResponse` carrying *content*.

    Missing usage details count as 0, and cache reads and writes never exceed the prompt.
    """
    usage = getattr(raw, "usage", None)
    if usage is None:
        logger.warning("No usage metrics returned from LLM response (stage=%s)", stage)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    in_details = getattr(usage, "input_tokens_details", None)
    out_details = getattr(usage, "output_tokens_details", None)
    cached = int(getattr(in_details, "cached_tokens", 0) or 0)
    written = int(getattr(in_details, "cache_write_tokens", 0) or 0)
    reasoning = int(getattr(out_details, "reasoning_tokens", 0) or 0)
    # Both are subsets of prompt_tokens; never let a provider quirk claim more.
    cached = min(cached, prompt_tokens)
    written = min(written, max(0, prompt_tokens - cached))
    return ParsedResponse(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning,
        cached_prompt_tokens=cached,
        cache_write_tokens=written,
        served_model=served_model_of(raw),
        served_tier=served_tier_of(raw),
    )


def _chat_usage(
    raw: Any, *, content: str, finish_reason: str | None, stage: str
) -> ParsedResponse:
    """A Chat Completions reply's usage as a :class:`ParsedResponse` carrying *content*."""
    usage = getattr(raw, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    # gpt-5 family report reasoning tokens (a subset of completion_tokens) under
    # completion_tokens_details — capture them for cost attribution.
    reasoning_tokens = 0
    cached_prompt_tokens = 0
    cache_write_tokens = 0
    if usage:
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        # B2: prompt tokens served from OpenAI's automatic prompt cache (a
        # subset of prompt_tokens), billed cheaper. Reported under
        # usage.prompt_tokens_details.cached_tokens.
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached_prompt_tokens = (
            int(getattr(prompt_details, "cached_tokens", 0) or 0) if prompt_details else 0
        )
        cache_write_tokens = _cache_write_tokens(usage, prompt_details)
        # Both are subsets of prompt_tokens; never let a provider quirk claim more.
        cache_write_tokens = min(
            cache_write_tokens, max(0, prompt_tokens - cached_prompt_tokens)
        )
    else:
        logger.warning("No usage metrics returned from LLM response (stage=%s)", stage)

    return ParsedResponse(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        cache_write_tokens=cache_write_tokens,
        served_model=served_model_of(raw),
        served_tier=served_tier_of(raw),
    )


def plain_dict(item: Any) -> dict:
    """An SDK object (or a dict) as a plain, JSON-safe dict, unset fields left out.

    Args:
        item: A pydantic SDK model, or a dict.

    Returns:
        A new dict.
    """
    if isinstance(item, dict):
        return json.loads(json.dumps(item, default=str))
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="json", exclude_none=True)
        except TypeError:
            value = dump()
        return json.loads(json.dumps(value, default=str))
    return json.loads(json.dumps(dict(vars(item)), default=str))


def chat_tool_messages(messages: list[dict]) -> list[dict]:
    """The neutral conversation as Chat Completions messages (requirement 8).

    Args:
        messages: The neutral messages; a ``cache_point`` key is dropped.

    Returns:
        The ``messages`` list: an assistant turn with its calls (arguments as JSON
        strings) and any ``provider_state`` fields, and a tool result as a ``tool`` message.
    """
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant" and ("tool_calls" in message or "provider_state" in message):
            wire: dict = {"role": "assistant", "content": message.get("content") or ""}
            wire.update(dict(message.get("provider_state") or {}))
            calls = [
                {"id": call["id"], "type": "function",
                 "function": {"name": call["name"],
                              "arguments": json.dumps(call.get("arguments") or {})}}
                for call in message.get("tool_calls") or []
            ]
            if calls:
                wire["tool_calls"] = calls
            out.append(wire)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": message["tool_call_id"],
                        "content": message.get("content", "")})
        else:
            out.append({k: v for k, v in message.items() if k != "cache_point"})
    return out


def chat_tools(tools: list[ToolSpec]) -> list[dict]:
    """Tool specs in the Chat Completions ``tools`` shape."""
    return [
        {"type": "function",
         "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


class OpenAIAdapter(ProviderAdapter):
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        # api_key resolved lazily so constructing an adapter for an unused
        # provider never forces a key to exist.
        self.api_key = api_key or env_api_key("OPENAI_API_KEY")
        self.base_url = base_url
        self._client: Any = None  # lazily created OpenAI client

    def get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:  # pragma: no cover - environment dependent
                raise ImportError("Install openai package: pip install openai>=1.0.0") from e
            if not self.api_key:
                raise ValueError(
                    "OpenAI API key required via param or OPENAI_API_KEY env var"
                )
            kwargs: dict = {"api_key": self.api_key, "max_retries": 0}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

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
        """Returns the kwargs of one Chat Completions or Responses call.

        A request with no cache point is the Chat Completions request it always was. A
        request with at least one is a Responses request (requirement 7 of
        docs/specs/llm-anthropic-and-caching.md).

        Either request carries the run's ``service_tier`` when one is set
        (docs/specs/openai-6-1-profile.md).

        Raises:
            ValueError: If a provider-native (dict) ``response_format`` comes with a
                cache point, or a message role has no Responses item.
            LlmConfigError: If ``XBRLRAG_OPENAI_SERVICE_TIER`` holds an unknown tier.
        """
        if cache_point_count(messages) > 0:
            return self._with_service_tier(self._responses_params(
                messages=messages, model=model,
                max_completion_tokens=max_completion_tokens, temperature=temperature,
                response_format=response_format, reasoning_effort=reasoning_effort,
                extra=extra,
            ))
        params: dict = {"model": model, "messages": messages, **(extra or {})}
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens
        if temperature is not None:
            params["temperature"] = temperature
        # Neutral response-format: the sentinel "json" maps to OpenAI JSON-object
        # mode; a dict is passed through verbatim (legacy / structured-output).
        if response_format == "json":
            params["response_format"] = {"type": "json_object"}
        elif isinstance(response_format, dict):
            params["response_format"] = response_format
        if reasoning_effort is not None:
            params["reasoning_effort"] = reasoning_effort
        return self._with_service_tier(params)

    def _with_service_tier(self, params: dict) -> dict:
        """*params* with the run's ``service_tier`` added, on the OpenAI platform only.

        The tier is read on every build, so a run's setting reaches every call. An
        OpenAI-compatible endpoint (``OpenAICompatAdapter``, ``DeepSeekAdapter``) never gets
        one: Flex is an OpenAI-platform tier. A caller's own ``service_tier`` wins.

        Raises:
            LlmConfigError: If ``XBRLRAG_OPENAI_SERVICE_TIER`` holds an unknown tier.
        """
        if self.provider != "openai":
            return params
        tier = openai_service_tier()
        if tier is not None:
            params.setdefault("service_tier", tier)
        return params

    @staticmethod
    def _responses_params(
        *,
        messages: list[dict],
        model: str,
        max_completion_tokens: int | None,
        temperature: float | None,
        response_format: str | dict | None,
        reasoning_effort: str | None,
        extra: dict | None,
    ) -> dict:
        """The kwargs of one ``responses.create`` call with explicit cache points."""
        if isinstance(response_format, dict):
            raise ValueError(
                "a provider-native response_format cannot be combined with a cache point "
                "(the Responses path supports only response_format='json')"
            )
        params: dict = {
            "model": model,
            "input": _responses_input(messages),
            "prompt_cache_options": dict(_EXPLICIT),
        }
        if max_completion_tokens is not None:
            params["max_output_tokens"] = max_completion_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if reasoning_effort is not None:
            params["reasoning"] = {"effort": reasoning_effort}
        if response_format == "json":
            params["text"] = {"format": {"type": "json_object"}}
        # Responses defaults store to true (OpenAI keeps the request and reply); nothing
        # here reads a stored response (docs/specs/llm-refusals-and-store.md).
        params["store"] = False
        params.update(extra or {})
        return params

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
        """One ``responses.create`` request offering tools (requirement 6).

        ``store`` is False, so reasoning travels in the request: ``include`` asks for it,
        and an assistant turn goes back as its ``provider_state["output"]`` items, verbatim.

        Raises:
            ValueError: If a message has a role the Responses API has no item for.
        """
        items: list[dict] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                items.append({"type": "function_call_output",
                              "call_id": message["tool_call_id"],
                              "output": message.get("content", "")})
            elif role == "assistant" and isinstance(message.get("provider_state"), dict) \
                    and "output" in message["provider_state"]:
                items.extend(message["provider_state"]["output"])
            elif role == "assistant" and message.get("tool_calls"):
                if message.get("content"):
                    items.extend(_responses_input([message]))
                for call in message["tool_calls"]:
                    items.append({"type": "function_call", "call_id": call["id"],
                                  "name": call["name"],
                                  "arguments": json.dumps(call.get("arguments") or {})})
            else:
                items.extend(_responses_input([message]))
        params: dict = {
            "model": model,
            "input": items,
            # Responses function tools default to strict, which needs every property required
            # and additionalProperties false; the tools' optional arguments are neither.
            "tools": [
                {"type": "function", "name": t.name, "description": t.description,
                 "parameters": t.parameters, "strict": False}
                for t in tools
            ],
            "tool_choice": tool_choice,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if max_completion_tokens is not None:
            params["max_output_tokens"] = max_completion_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if reasoning_effort is not None:
            params["reasoning"] = {"effort": reasoning_effort}
        if cache_point_count(messages) > 0:
            params["prompt_cache_options"] = dict(_EXPLICIT)
        params.update(extra or {})
        return self._with_service_tier(params)

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Reads a Responses reply that may hold function calls (requirement 6).

        Every output item is kept, as a plain dict, in ``provider_state["output"]``.

        Raises:
            ValueError: If the reply was blocked by the content filter, or holds neither
                text nor a function call.
        """
        finish_reason = _responses_finish_reason(raw)
        if finish_reason == "content_filter":
            raise ValueError("LLM response blocked by content filter")
        if finish_reason == "length":
            logger.warning("LLM tool turn truncated: reason=max_output_tokens (stage=%s)", stage)
        output = [plain_dict(item) for item in (getattr(raw, "output", None) or [])]
        calls: list[ToolCall] = []
        for item in output:
            if item.get("type") != "function_call":
                continue
            arguments, error = parse_arguments(item.get("arguments"))
            calls.append(ToolCall(str(item.get("call_id")), str(item.get("name")),
                                  arguments, error))
        content = getattr(raw, "output_text", None)
        text = "" if content is None else str(content)
        if calls and finish_reason == "stop":
            finish_reason = "tool_calls"
        parsed = _responses_usage(raw, content=text, finish_reason=finish_reason, stage=stage)
        if not calls and not text.strip():
            raise LLMEmptyReplyError("LLM returned empty message content and no tool call",
                                     parsed)
        parsed.tool_calls = calls
        parsed.assistant_message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in calls],
            "provider_state": {"output": output},
        }
        return parsed

    def invoke(self, client: Any, params: dict) -> Any:
        """Calls ``responses.create`` for ``input`` params, else Chat Completions."""
        if "input" in params and "messages" not in params:
            create = client.responses.create
            undeclared = _undeclared_kwargs(create, params)
            if not undeclared:
                return create(**params)
            kwargs = {k: v for k, v in params.items() if k not in undeclared}
            extra_body = dict(kwargs.pop("extra_body", None) or {})
            extra_body.update({k: params[k] for k in undeclared})
            return create(**kwargs, extra_body=extra_body)
        return client.chat.completions.create(**params)

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Parses a Chat Completions or a Responses reply.

        Raises:
            ValueError: If the reply is empty, has no choices or text, or was blocked by
                the content filter.
        """
        if raw is not None and getattr(raw, "object", None) == "response":
            return self._parse_responses(raw, stage=stage)
        if not raw or not getattr(raw, "choices", None):
            raise ValueError("LLM returned an empty response or no choices")

        choice = raw.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            raise ValueError("LLM response blocked by content filter")
        if finish_reason == "length":
            logger.warning("LLM response truncated: finish_reason=length (stage=%s)", stage)
        elif finish_reason not in (None, "stop", "tool_calls", "function_call"):
            logger.warning("Unexpected finish_reason=%s (stage=%s)", finish_reason, stage)

        content = getattr(getattr(choice, "message", None), "content", None)
        parsed = _chat_usage(raw, content="" if content is None else str(content),
                             finish_reason=finish_reason, stage=stage)
        if not str(parsed.content).strip():
            raise LLMEmptyReplyError("LLM returned empty message content", parsed)
        return parsed

    @staticmethod
    def _parse_responses(raw: Any, *, stage: str) -> ParsedResponse:
        """Parses a Responses reply (requirement 8); missing usage details count as 0."""
        finish_reason = _responses_finish_reason(raw)
        if finish_reason == "content_filter":
            raise ValueError("LLM response blocked by content filter")
        if finish_reason == "length":
            logger.warning("LLM response truncated: status=incomplete, "
                           "reason=max_output_tokens (stage=%s)", stage)
        elif finish_reason not in (None, "stop"):
            logger.warning("Unexpected Responses status/reason=%s (stage=%s)",
                           finish_reason, stage)

        content = getattr(raw, "output_text", None)
        parsed = _responses_usage(
            raw, content="" if content is None else str(content),
            finish_reason=finish_reason, stage=stage,
        )
        if not str(parsed.content).strip():
            raise LLMEmptyReplyError("LLM returned empty message content", parsed)
        return parsed


class OpenAICompatAdapter(OpenAIAdapter):
    """OpenAIAdapter pinned to an OpenAI-compatible ``base_url`` endpoint."""

    provider = "openai_compat"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            api_key=api_key
            or env_api_key("OPENAI_API_KEY")
            or os.environ.get("OPENAI_COMPAT_API_KEY"),
            base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
        )

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
        """One Chat Completions request offering tools (requirement 8).

        A ``cache_point`` key never reaches the wire: Chat Completions caches on its own.
        """
        params: dict = {
            "model": model,
            "messages": chat_tool_messages(messages),
            "tools": chat_tools(tools),
            "tool_choice": tool_choice,
        }
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens
        if temperature is not None:
            params["temperature"] = temperature
        if reasoning_effort is not None:
            params["reasoning_effort"] = reasoning_effort
        params.update(extra or {})
        return params

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Reads a Chat Completions reply that may hold tool calls (requirement 8).

        A ``reasoning_content`` in the reply goes in ``provider_state``, so it goes back on
        the next turn.

        Raises:
            ValueError: If the reply has no choices, was blocked by the content filter, or
                holds neither text nor a tool call.
        """
        if not raw or not getattr(raw, "choices", None):
            raise ValueError("LLM returned an empty response or no choices")
        choice = raw.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            raise ValueError("LLM response blocked by content filter")
        if finish_reason == "length":
            logger.warning("LLM tool turn truncated: finish_reason=length (stage=%s)", stage)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        text = "" if content is None else str(content)
        calls: list[ToolCall] = []
        for wire in getattr(message, "tool_calls", None) or []:
            function = getattr(wire, "function", None)
            arguments, error = parse_arguments(getattr(function, "arguments", None))
            calls.append(ToolCall(str(getattr(wire, "id", "")),
                                  str(getattr(function, "name", "")), arguments, error))
        parsed = _chat_usage(raw, content=text, finish_reason=finish_reason, stage=stage)
        if not calls and not text.strip():
            raise LLMEmptyReplyError("LLM returned empty message content and no tool call",
                                     parsed)
        state: dict = {}
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            state["reasoning_content"] = reasoning
        parsed.tool_calls = calls
        parsed.assistant_message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in calls],
            "provider_state": state,
        }
        return parsed
