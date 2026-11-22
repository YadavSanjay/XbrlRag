"""Anthropic (Claude) provider adapter.

Design: docs/design/XbrlRag_LLM_Harness.md. Implements the
:class:`~src.llm.gateway.adapters.base.ProviderAdapter` seam against the
Anthropic Messages API. Selected by the XBRL stages under the ``anthropic`` LLM profile
(configs/llm/llm.yaml).

Spec: docs/specs/llm-anthropic-and-caching.md. The request is built for Claude 5.5:

  * the ``system`` messages become one top-level ``system`` text block marked
    ``cache_control: ephemeral`` (Anthropic does not accept a system role inside
    ``messages``);
  * a message carrying ``"cache_point": True`` becomes one text block with
    ``cache_control``; every other message keeps its content as a plain string;
  * ``max_tokens`` is the model's ``max_output_tokens`` from llm.yaml when the model is
    configured (adaptive thinking shares this budget), else the caller's
    ``max_completion_tokens``, else 4096;
  * the model's configured ``effort`` goes out as ``output_config.effort``;
  * ``temperature``, ``thinking`` and ``response_format`` are never sent: Claude 5.5 runs
    adaptive thinking by default and rejects sampling parameters, and JSON stays
    prompt-instructed (read by ``extract_json``). A temperature for a model configured
    ``temperature: false`` is a caller bug and raises ``ValueError``.

Calls always stream (``messages.stream`` then ``get_final_message``), which keeps long
thinking-plus-answer replies under the SDK's non-streaming time limit. ``parse`` joins
the ``text`` blocks (``thinking`` blocks are skipped) and raises
:class:`~src.llm.gateway.adapters.base.LLMRefusalError` on ``stop_reason == "refusal"``,
carrying the refusal's category, usage and whether Anthropic bills it
(:func:`refusal_is_billed`; docs/specs/llm-refusals-and-store.md).
The SDK client is built with ``max_retries=0``: the gateway's retry loop is the only one.
"""
from __future__ import annotations

import json
from typing import Any

from src.llm.gateway.adapters.base import (
    LLMEmptyReplyError,
    LLMRefusalError,
    ParsedResponse,
    ProviderAdapter,
    env_api_key,
    message_text,
    served_model_of,
    served_tier_of,
)
from src.llm.gateway.tools import ToolCall, ToolSpec, parse_arguments
from src.llm.profiles import LlmSettings, load_committed_settings
from src.logging_config import get_logger

logger = get_logger(__name__)

# Anthropic requires an explicit max_tokens; used for an unconfigured model when the
# caller supplies none.
_DEFAULT_MAX_TOKENS = 4096
_EPHEMERAL = {"type": "ephemeral"}

# The committed model rows (configs/llm/llm.yaml): output ceiling, temperature support
# and effort do not depend on the active profile.
_MODEL_ROWS: LlmSettings = load_committed_settings(environ={})

# Refusal categories Anthropic bills even when the refusal comes before any output.
# Source: Anthropic "Refusals and fallback" -> "How refusals are billed" (read 2026-10-21);
# Anthropic says the list may change, so re-check it there.
BILLED_REFUSAL_CATEGORIES: frozenset[str] = frozenset(
    {"bio", "frontier_llm", "reasoning_extraction"}
)


def _refusal_category(raw: Any) -> str | None:
    """The reply's ``stop_details.category``, or None."""
    details = getattr(raw, "stop_details", None)
    category = getattr(details, "category", None) if details is not None else None
    return category or None


def _refusal_message(raw: Any) -> str:
    """A refusal's error text, naming the reply's refusal category when it has one."""
    category = _refusal_category(raw)
    if category:
        return f"the model declined to answer (refusal category: {category})"
    return "the model declined to answer (refusal)"


def refusal_is_billed(category: str | None, completion_tokens: int) -> bool:
    """Whether Anthropic bills a refused call (docs/specs/llm-refusals-and-store.md).

    Args:
        category: The reply's ``stop_details.category``, or ``None``.
        completion_tokens: Output tokens the reply reports; more than 0 means the
            refusal arrived mid-stream.

    Returns:
        True for a mid-stream refusal or a billed category, else False.
    """
    return completion_tokens > 0 or category in BILLED_REFUSAL_CATEGORIES


def _usage(raw: Any, *, content: str, finish_reason: str | None, stage: str) -> ParsedResponse:
    """Maps a reply's usage to a :class:`ParsedResponse` carrying *content*.

    Anthropic reports cache-read/creation tokens SEPARATELY from ``input_tokens``
    (unlike OpenAI, where cached is a subset of ``prompt_tokens``). They are normalized to
    OpenAI semantics, so the shared cost model (cached ⊆ prompt) works uniformly.
    """
    usage = getattr(raw, "usage", None)
    if not usage:
        logger.warning("No usage metrics returned from LLM response (stage=%s)", stage)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
    return ParsedResponse(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=input_tokens + cache_read + cache_creation,
        completion_tokens=completion_tokens,
        reasoning_tokens=0,
        cached_prompt_tokens=cache_read,
        cache_write_tokens=cache_creation,
        served_model=served_model_of(raw),
        served_tier=served_tier_of(raw),
    )


def _plain_block(block: Any) -> dict:
    """A content block as a plain, JSON-safe dict (an SDK model or a dict)."""
    if isinstance(block, dict):
        return json.loads(json.dumps(block, default=str))
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            value = dump(mode="json", exclude_none=True)
        except TypeError:
            value = dump()
    else:
        value = dict(vars(block))
    return json.loads(json.dumps(value, default=str))


class AnthropicAdapter(ProviderAdapter):
    """The Anthropic Messages API behind the gateway's provider-neutral seam."""

    provider = "anthropic"

    def __init__(self, *, api_key: str | None = None):
        self.api_key = api_key or env_api_key("ANTHROPIC_API_KEY")
        self._client: Any = None

    def get_client(self) -> Any:
        """Returns the cached SDK client, built with ``max_retries=0`` on first use."""
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:  # pragma: no cover - environment dependent
                raise ImportError("Install anthropic package: pip install anthropic") from e
            if not self.api_key:
                raise ValueError(
                    "Anthropic API key required via param or ANTHROPIC_API_KEY env var"
                )
            self._client = Anthropic(api_key=self.api_key, max_retries=0)
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
        """Returns the kwargs of one ``messages.stream`` call.

        Args:
            messages: The neutral messages; a ``"cache_point": True`` message becomes a
                cached text block.
            model: The concrete Claude model id.
            max_completion_tokens: The output budget for an unconfigured model.
            temperature: Never sent; must be None for a model configured
                ``temperature: false``.
            response_format: Never sent (JSON is prompt-instructed).
            reasoning_effort: Never sent; the model's configured effort is used.
            extra: Passthrough kwargs, added last.

        Returns:
            The request kwargs.

        Raises:
            ValueError: If a temperature is given for a model that rejects one.
        """
        configured = _MODEL_ROWS.has_model(model)
        if (
            temperature is not None
            and configured
            and not _MODEL_ROWS.temperature_allowed(model)
        ):
            raise ValueError(
                f"model {model!r} does not accept a temperature (llm.yaml "
                f"temperature: false); got temperature={temperature!r}"
            )

        system_parts: list[str] = []
        conv: list[dict] = []
        for m in messages or []:
            role = m.get("role")
            text = message_text(m.get("content"))
            if role == "system":
                system_parts.append(text)
            elif m.get("cache_point") is True:
                conv.append({
                    "role": role,
                    "content": [{"type": "text", "text": text, "cache_control": dict(_EPHEMERAL)}],
                })
            else:
                conv.append({"role": role, "content": text})

        if configured:
            max_tokens = _MODEL_ROWS.max_output_tokens_for(model)
        else:
            max_tokens = max_completion_tokens or _DEFAULT_MAX_TOKENS
        params: dict = {"model": model, "max_tokens": max_tokens}
        if system_parts:
            # The (large, static) system prefix is a cache breakpoint of its own.
            params["system"] = [{
                "type": "text",
                "text": "\n\n".join(system_parts),
                "cache_control": dict(_EPHEMERAL),
            }]
        params["messages"] = conv
        effort = _MODEL_ROWS.effort_for(model) if configured else None
        if effort is not None:
            params["output_config"] = {"effort": effort}
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
        """One ``messages.stream`` request offering tools (requirement 7).

        :meth:`build_params` with ``tools`` and ``tool_choice``. An assistant turn goes
        back as its ``provider_state["content"]`` blocks, verbatim (thinking blocks and
        their signatures included). Consecutive tool results, and a user message that
        follows them, form one ``user`` message: the ``tool_result`` blocks in order, then
        the text block, which carries ``cache_control`` when it is a cache point.

        Raises:
            ValueError: If a temperature is given for a model that rejects one.
        """
        system = [m for m in messages if m.get("role") == "system"]
        params = self.build_params(
            messages=system, model=model, max_completion_tokens=max_completion_tokens,
            temperature=temperature, reasoning_effort=reasoning_effort,
        )
        conv: list[dict] = []
        pending: list[dict] | None = None  # the open user message of tool results
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                         "content": m.get("content", "")}
                if pending is None:
                    pending = []
                    conv.append({"role": "user", "content": pending})
                pending.append(block)
                continue
            if role == "user" and pending is not None:
                block = {"type": "text", "text": message_text(m.get("content"))}
                if m.get("cache_point") is True:
                    block["cache_control"] = dict(_EPHEMERAL)
                pending.append(block)
                pending = None
                continue
            pending = None
            if role == "assistant" and isinstance(m.get("provider_state"), dict) \
                    and "content" in m["provider_state"]:
                conv.append({"role": "assistant", "content": m["provider_state"]["content"]})
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": message_text(m.get("content"))})
                blocks.extend(
                    {"type": "tool_use", "id": c["id"], "name": c["name"],
                     "input": c.get("arguments") or {}}
                    for c in m["tool_calls"]
                )
                conv.append({"role": "assistant", "content": blocks})
            elif m.get("cache_point") is True:
                conv.append({
                    "role": role,
                    "content": [{"type": "text", "text": message_text(m.get("content")),
                                 "cache_control": dict(_EPHEMERAL)}],
                })
            else:
                conv.append({"role": role, "content": message_text(m.get("content"))})
        params["messages"] = conv
        params["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        params["tool_choice"] = {"type": tool_choice}
        params.update(extra or {})
        return params

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Reads a final message that may hold ``tool_use`` blocks (requirement 7).

        Every block is kept, as a plain dict, in ``provider_state["content"]``.

        Raises:
            LLMRefusalError: If ``stop_reason`` is ``"refusal"``, as :meth:`parse` does.
            ValueError: If the reply holds neither text nor a tool call.
        """
        finish_reason = getattr(raw, "stop_reason", None)
        if finish_reason == "refusal":
            self.parse(raw, stage=stage)  # raises the refusal, with its usage
        blocks = [_plain_block(b) for b in (getattr(raw, "content", None) or [])]
        calls: list[ToolCall] = []
        texts: list[str] = []
        for block in blocks:
            kind = block.get("type")
            if kind == "text":
                texts.append(str(block.get("text", "")))
            elif kind == "tool_use":
                arguments, error = parse_arguments(block.get("input"))
                calls.append(ToolCall(str(block.get("id")), str(block.get("name")),
                                      arguments, error))
        content = "".join(texts)
        if finish_reason == "max_tokens":
            logger.warning("LLM tool turn truncated: stop_reason=max_tokens (stage=%s)", stage)
        parsed = _usage(raw, content=content, finish_reason=finish_reason, stage=stage)
        if not calls and not content.strip():
            raise LLMEmptyReplyError("LLM returned empty message content and no tool call",
                                     parsed)
        parsed.tool_calls = calls
        parsed.assistant_message = {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in calls],
            "provider_state": {"content": blocks},
        }
        return parsed

    def invoke(self, client: Any, params: dict) -> Any:
        """Streams the call and returns the final message; never ``messages.create``."""
        with client.messages.stream(**params) as stream:
            return stream.get_final_message()

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        """Reads a final Messages reply.

        Args:
            raw: The final message from :meth:`invoke`.
            stage: The calling stage, for log lines.

        Returns:
            The joined text of the ``text`` blocks, with usage normalized so cache reads
            and writes are subsets of ``prompt_tokens``.

        Raises:
            LLMRefusalError: If ``stop_reason`` is ``"refusal"``; it carries the reply's
                ``category``, its ``usage`` (mapped as for a success, ``content=""``) and
                whether it is ``billed``.
            ValueError: If the reply has no text.
        """
        finish_reason = getattr(raw, "stop_reason", None)
        if finish_reason == "refusal":
            message = _refusal_message(raw)
            logger.warning("LLM refusal (stage=%s): %s", stage, message)
            category = _refusal_category(raw)
            usage = _usage(raw, content="", finish_reason=finish_reason, stage=stage)
            raise LLMRefusalError(
                message,
                category=category,
                usage=usage,
                billed=refusal_is_billed(category, usage.completion_tokens),
            )

        blocks = getattr(raw, "content", None)
        if not blocks:
            raise ValueError("LLM returned an empty response or no content blocks")

        text_parts: list[str] = []
        for block in blocks:
            if getattr(block, "type", None) == "text":
                text_parts.append(str(getattr(block, "text", "")))
        content = "".join(text_parts)

        if finish_reason == "max_tokens":
            logger.warning("LLM response truncated: stop_reason=max_tokens (stage=%s)", stage)
        parsed = _usage(raw, content=content, finish_reason=finish_reason, stage=stage)
        if not content or not content.strip():
            raise LLMEmptyReplyError("LLM returned empty message content", parsed)
        return parsed
