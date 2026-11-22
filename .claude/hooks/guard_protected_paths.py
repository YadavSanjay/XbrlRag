"""PreToolUse guard (WS-8 / H2): deny access to secret / protected files.

Blocks a dev agent from editing or reading `.env`, private keys, or credential files.
Two kinds of tool call are checked:

- File tools (Write, Edit, MultiEdit, NotebookEdit, Read): the target path, as given and
  after resolving symlinks, so a link to `.env` is blocked too.
- Shell tools (Bash, PowerShell): the command text, for any mention of an env file
  (`cat .env`, `Get-Content .env.local`). This is text matching, so it stops accidental
  reads, not a determined workaround.

Example-only variants (`.env.example`, `.env.template`, …) are allowed so the committed
templates stay editable.

Contract: emit a PreToolUse `permissionDecision: "deny"` to block; stay silent (exit 0)
to allow. **Fail closed**: if the event can't be parsed, a file tool names no target
path, a shell tool names no command, or the guard itself errors, the tool call is
blocked (exit 2 with the reason on stderr), because a security guard that silently
allows on error is not a guard.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import emit  # noqa: E402

# Allowed template variants of an env file (never carry real secrets).
_ENV_ALLOW_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults")

# Exact protected basenames.
_PROTECTED_NAMES = {
    ".env",
    ".git-credentials",
    ".pgpass",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "credentials",
    "credentials.json",
}

# Protected filename suffixes (private keys / cert material / secret bundles).
_PROTECTED_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")

# Tool-input keys that name the target file, by tool.
_PATH_KEYS = ("file_path", "notebook_path", "path")

_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
_SHELL_TOOLS = {"Bash", "PowerShell"}

# `.env`, optionally with dotted suffixes, not followed by a word char, dot or dash.
_ENV_IN_COMMAND = re.compile(r"(?i)\.env((?:\.[\w-]+)*)(?![\w.\-])")

# Exit code that makes Claude Code treat a PreToolUse hook as a blocking error.
_BLOCK_EXIT = 2


def is_protected_name(path: str) -> bool:
    """Whether a path's basename marks it as a secret or credential file."""
    name = os.path.basename(path.replace("\\", "/")).strip()
    low = name.lower()

    # .env and .env.<anything-not-a-template>
    if low == ".env":
        return True
    if low.startswith(".env."):
        return not low.endswith(_ENV_ALLOW_SUFFIXES)

    if low in _PROTECTED_NAMES:
        return True
    if low.endswith(_PROTECTED_SUFFIXES):
        return True
    # service-account / secret json bundles
    return ("service-account" in low or low.startswith("secret")) and low.endswith(".json")


def is_protected(path: str) -> bool:
    """Whether a path, as given or after resolving symlinks, is protected."""
    if is_protected_name(path):
        return True
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    return resolved != path and is_protected_name(resolved)


def command_names_env(command: str) -> bool:
    """Whether a shell command mentions a non-template env file."""
    for match in _ENV_IN_COMMAND.finditer(command):
        if not match.group(1).lower().endswith(_ENV_ALLOW_SUFFIXES):
            return True
    return False


def target_path(event: dict[str, Any]) -> str | None:
    """The file a file-tool event targets, or None when it names none."""
    tool_input = event.get("tool_input") or {}
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _block(reason: str) -> None:
    """Block the tool call as a hook error (fail closed)."""
    sys.stderr.write(f"Blocked by H2 guardrail: {reason}\n")
    sys.exit(_BLOCK_EXIT)


def main() -> None:
    """Runs the hook."""
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except ValueError:
        _block("the hook event could not be parsed, so the target file is unknown.")
        return
    if not isinstance(event, dict):
        _block("the hook event is not a JSON object.")
        return

    tool = event.get("tool_name") or ""
    if tool in _SHELL_TOOLS:
        command = (event.get("tool_input") or {}).get("command")
        if not isinstance(command, str) or not command.strip():
            _block(f"the {tool} tool call names no command.")
            return
        if command_names_env(command):
            emit(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "Blocked by H2 guardrail: the command names .env, which holds "
                            "live secrets. Use .env.example or a placeholder, or ask the "
                            "operator."
                        ),
                    }
                }
            )
        return

    path = target_path(event)
    if path is None:
        if tool in _FILE_TOOLS or not tool:
            _block(f"the {tool or 'file'} tool call names no target path.")
        return

    if is_protected(path):
        tool = tool or "this tool"
        emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Blocked by H2 guardrail: '{os.path.basename(path)}' is a protected "
                        f"secret/credential file, so {tool} may not touch it. Edit it manually "
                        "if you really must, or use a template variant (e.g. .env.example)."
                    ),
                }
            }
        )
    # else: allow silently (exit 0, no output)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # isolation point: any guard failure blocks rather than allows
        _block(f"the guard failed ({type(exc).__name__}: {exc}).")
