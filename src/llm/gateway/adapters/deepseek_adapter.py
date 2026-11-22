"""DeepSeek adapter: Chat Completions on DeepSeek's OpenAI-compatible endpoint.

DeepSeek speaks the OpenAI wire format with three differences this adapter handles
(docs/specs/deepseek-provider.md, requirement 4):

* the output budget is ``max_tokens``, not ``max_completion_tokens``;
* reasoning is set by ``reasoning_effort``: the caller's value, else the model's ``effort``
  in ``configs/llm/llm.yaml``;
* caching is automatic, so a ``"cache_point": True`` marker is dropped and the request is
  always Chat Completions, never the Responses API.

Replies are parsed by the inherited :meth:`OpenAIAdapter.parse`: DeepSeek reports cache hits
under ``usage.prompt_tokens_details.cached_tokens`` like OpenAI.
"""

from __future__ import annotations

from src.llm.gateway.adapters.base import env_api_key
from src.llm.gateway.adapters.openai_adapter import OpenAICompatAdapter
from src.llm.gateway.tools import ToolSpec
from src.llm.profiles import LlmSettings, load_committed_settings

# The committed model rows: a model's configured effort does not depend on the active
# profile.
_MODEL_ROWS: LlmSettings = load_committed_settings(environ={})

DEEPSEEK_KEY_ENV = "DEEPSEEK_API_KEY"


class DeepSeekAdapter(OpenAICompatAdapter):
    """OpenAI-compatible adapter for DeepSeek's API."""

    provider = "deepseek"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        """Creates the adapter.

        Args:
            api_key: The DeepSeek key; when None, ``DEEPSEEK_API_KEY`` (or its ``_FILE``).
                Never falls back to the OpenAI key, which must not reach DeepSeek.
            base_url: The endpoint; the gateway passes the provider row's ``base_url``.
        """
        # The parent constructors fall back to OPENAI_API_KEY and OPENAI_BASE_URL, so they
        # are not called; these are the attributes OpenAIAdapter.get_client reads.
        self.api_key = api_key or env_api_key(DEEPSEEK_KEY_ENV)
        self.base_url = base_url
        self._client = None

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
        """Returns the kwargs of one DeepSeek Chat Completions call.

        Args:
            messages: The neutral messages; a ``cache_point`` key is removed from each.
            model: The DeepSeek model id.
            max_completion_tokens: The output budget, sent as ``max_tokens``.
            temperature: Sent only when given.
            response_format: ``"json"`` for JSON-object mode, or a dict passed through.
            reasoning_effort: Sent unchanged; when None, the model's configured effort.
            extra: Further kwargs, passed through.

        Returns:
            The request kwargs.
        """
        chat_messages = [
            {key: value for key, value in message.items() if key != "cache_point"}
            for message in messages
        ]
        if reasoning_effort is None and _MODEL_ROWS.has_model(model):
            reasoning_effort = _MODEL_ROWS.effort_for(model)
        params = super().build_params(
            messages=chat_messages,
            model=model,
            max_completion_tokens=None,
            temperature=temperature,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            extra=extra,
        )
        if max_completion_tokens is not None:
            params["max_tokens"] = max_completion_tokens
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
        """One DeepSeek Chat Completions request offering tools (requirement 8).

        The compat request with DeepSeek's own rules: ``max_tokens``, the configured effort
        when the caller gives none, and no ``cache_point``.
        """
        if reasoning_effort is None and _MODEL_ROWS.has_model(model):
            reasoning_effort = _MODEL_ROWS.effort_for(model)
        params = super().build_tool_params(
            messages=messages,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            max_completion_tokens=None,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            extra=extra,
        )
        if max_completion_tokens is not None:
            params["max_tokens"] = max_completion_tokens
        return params
