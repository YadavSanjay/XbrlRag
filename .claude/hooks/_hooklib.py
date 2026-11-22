"""Shared helpers for the XbrlRag dev-time guardrail hooks (WS-8 / H2).

These hooks are deterministic gates wired in `.claude/settings.json`:
- `guard_protected_paths.py` (PreToolUse file tools) — deny access to secret files; fails closed.
- `format_python.py`        (PostToolUse Write|Edit) — auto-format edited .py.
- `stop_gate.py`            (Stop)                    — run `pytest test/unit`.

All logic lives in plain-stdlib Python (the hook shell here is Git Bash and `jq`
is not installed), so each hook is just `python .claude/hooks/<script>.py`.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict, Optional


def read_event() -> dict[str, Any]:
    """Parse the hook JSON delivered on stdin. Returns {} on any failure."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def emit(obj: dict[str, Any]) -> None:
    """Write a hook JSON response to stdout."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


# Candidate interpreters for the full project env (py312), most-preferred first.
# The base `python` on PATH lacks the project's deps (FlagEmbedding, psycopg2,
# the ruff pin), so lint fixes + tests must use py312. Override with
# XBRLRAG_HOOK_PYTHON when the env lives elsewhere (e.g. a teammate's machine).
_PY312_CANDIDATES = (
    os.environ.get("XBRLRAG_HOOK_PYTHON") or os.environ.get("ARAG_HOOK_PYTHON"),  # legacy name
    # The hooks start under the base conda `python`, so py312 sits beside it.
    os.path.join(sys.base_prefix, "envs", "py312", "python.exe"),
    os.path.join(sys.base_prefix, "envs", "py312", "bin", "python"),
    os.path.expanduser(r"~\Anaconda3\envs\py312\python.exe"),
    os.path.expanduser(r"~\miniconda3\envs\py312\python.exe"),
)


def env_python() -> str:
    """Absolute path to the project (py312) interpreter, or a PATH fallback.

    Falls back to `python`/`python3` on PATH, then the interpreter running this
    script. Never raises — a wrong interpreter surfaces as a hook error, which is
    more debuggable than a silent skip.
    """
    for cand in _PY312_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def file_path_from(event: dict[str, Any]) -> str | None:
    """Extract the target file path from a Write/Edit tool event."""
    ti = event.get("tool_input") or {}
    return ti.get("file_path") or (event.get("tool_response") or {}).get("filePath")
