"""Tolerant JSON extraction for LLM output.

Design: docs/design/XbrlRag_LLM_Harness.md.

Provider-neutral safety net: even with JSON-mode / structured-output requested,
a model (especially a non-OpenAI one) may wrap its JSON in ```json fences or
prepend a sentence. ``extract_json`` strips fences and, failing a direct parse,
pulls the outermost balanced ``{...}`` / ``[...]`` value out of surrounding
prose. It raises :class:`json.JSONDecodeError` when nothing parses — the same
exception the stage call sites already catch, so their fallback paths are
unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Any

# A whole-string ```lang ... ``` fenced block (the common markdown wrapper).
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*[ \t]*\n?(.*?)\n?```$", re.DOTALL)


def _strip_fences(s: str) -> str:
    m = _FENCE_RE.match(s.strip())
    return m.group(1).strip() if m else s


def _find_outermost_json(s: str) -> str | None:
    """Return the first balanced ``{...}`` or ``[...]`` substring, or None.

    Brace/bracket depth is counted while respecting JSON string literals (so
    braces inside quoted text don't affect nesting).
    """
    start = None
    open_ch = ""
    for i, ch in enumerate(s):
        if ch in "{[":
            start, open_ch = i, ch
            break
    if start is None:
        return None

    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None  # unbalanced


def extract_json(text: str) -> Any:
    """Parse JSON from raw LLM output, tolerating code fences / surrounding prose.

    Raises ``json.JSONDecodeError`` if no JSON value can be recovered.
    """
    if text is None:
        raise json.JSONDecodeError("no content to parse", "", 0)

    s = _strip_fences(str(text).strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    candidate = _find_outermost_json(s)
    if candidate is None:
        raise json.JSONDecodeError("no JSON object or array found in content", s or "", 0)
    return json.loads(candidate)
