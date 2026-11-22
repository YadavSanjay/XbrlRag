# Provider-neutral tool calling for the LLM gateway.
#
# The types a caller needs for one tool-calling conversation through
# :meth:`~src.llm.gateway.gateway.LLMGateway.complete_tools`.
#
# Spec: docs/specs/frontier-sec-tools.md.

from __future__ import annotations

import json
from typing import Any


class ToolsUnsupportedError(ValueError):
    """The provider's adapter has no tool support; raised before any provider call."""


class ToolSpec:
    """One tool offered to the model."""

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        """Holds the tool's definition.

        Args:
            name: The tool's name.
            description: What the tool does, for the model.
            parameters: The arguments, as a JSON schema object.
        """
        self.name = name
        self.description = description
        self.parameters = parameters

    def __repr__(self) -> str:
        return f"ToolSpec(name={self.name!r})"


class ToolCall:
    """One tool call the model asked for."""

    def __init__(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, Any] | None,
        arguments_error: str | None = None,
    ) -> None:
        """Holds the call.

        Args:
            call_id: The provider's id, echoed in the tool's result.
            name: The tool asked for.
            arguments: The parsed arguments; None when they were not a JSON object.
            arguments_error: Why the arguments could not be read, when they could not.
        """
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.arguments_error = arguments_error

    def __repr__(self) -> str:
        return (
            f"ToolCall(call_id={self.call_id!r}, name={self.name!r}, "
            f"arguments={self.arguments!r}, arguments_error={self.arguments_error!r})"
        )


def parse_arguments(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Reads a call's arguments, which must be a JSON object (requirement 5).

    Args:
        raw: The arguments as the provider sent them: a JSON string, or a decoded value.

    Returns:
        ``(arguments, None)`` for an object, else ``(None, reason)``.
    """
    value: Any = raw
    if isinstance(raw, (str, bytes, bytearray)):
        if not raw:
            return {}, None
        try:
            value = json.loads(raw)
        except ValueError as exc:
            return None, f"the arguments are not valid JSON ({exc}); the reply may be cut off"
    if isinstance(value, dict):
        return value, None
    return None, f"the arguments are not a JSON object (got {type(value).__name__})"


def tool_result_message(call_id: str, content: str) -> dict[str, Any]:
    """The neutral message that answers one tool call.

    Args:
        call_id: The call's id.
        content: The result, as text.

    Returns:
        ``{"role": "tool", "tool_call_id": call_id, "content": content}``.
    """
    return {"role": "tool", "tool_call_id": call_id, "content": content}
