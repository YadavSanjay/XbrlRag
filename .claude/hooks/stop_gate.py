"""Stop-gate (WS-8 / H2): coding-standards report, then block stopping on a red unit build.

Three steps, all skipped when nothing relevant changed:

0. **Unused imports.** `ruff check --fix --select F401` on the changed `.py` files. The PostToolUse
   fixer leaves F401 alone (an import added one edit before its first use would be deleted), so
   the cleanup happens here, once the turn is over. Files it rewrote are named in the report.
1. **Coding standards (advisory).** Runs `tools/standards/check_standards.py stop --hook`: ruff on
   changed files, Google docstrings on changed lines, and the ratchet check when lint config
   changed. While advisory it only reports (as a system message); it decides that itself, from
   `standards.toml`.
2. **Unit tests (blocking).** The dev-time twin of the runtime faithfulness verifier — a
   guarantee, not a polite request. If a test input changed vs HEAD (see `_TEST_INPUT_PREFIXES`:
   code, tests, prompts, configs, scripts, tools, subagents, harness docs, root settings), runs
   `pytest test/unit` and refuses the stop if it fails, feeding the failure back.

Runs only when needed: the test run is skipped when HEAD and the changed test inputs are
byte-identical to the last green run (fingerprint in `.claude/state/stop-gate.json`), and a
docs-only change stops instantly. Blocks at most once per turn: when Claude Code reports
`stop_hook_active` (this hook already blocked), the stop is allowed with a warning. Uses the
project (py312) interpreter.

Escape hatches: set `XBRLRAG_STOP_GATE=0` to disable, or `disableAllHooks` in settings.
Fail-open on infrastructure errors (missing git / interpreter) so a broken environment never
traps the agent — only a genuine test failure blocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import emit, env_python, read_event

# What `pytest test/unit` reads: code, tests, prompt templates, configs, scripts, the standards
# tool, the subagent definitions + harness docs (test_subagents, test_feature_manifest), root
# settings and VISION.md.
_TEST_INPUT_PREFIXES = (
    "src/", "test/", "prompts/", "configs/", "scripts/", "tools/",
    ".claude/agents/", "docs/harness/",
)
_TEST_INPUT_ROOT_SUFFIXES = (".py", ".toml", ".ini", ".cfg", ".txt")
_TEST_INPUT_ROOT_FILES = ("VISION.md",)
_PYTEST_TIMEOUT_S = 600
_STANDARDS_TIMEOUT_S = 50
_RUFF_TIMEOUT_S = 30
_STATE = os.path.join(".claude", "state", "stop-gate.json")


def _is_test_input(path: str) -> bool:
    if path.startswith(_TEST_INPUT_PREFIXES):
        return True
    return "/" not in path and (
        path.endswith(_TEST_INPUT_ROOT_SUFFIXES) or path in _TEST_INPUT_ROOT_FILES
    )


def _head() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=False
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def _changed_files() -> list[str]:
    """Tracked-diff + untracked files, vs HEAD (repo-relative, forward slashes)."""
    files: set[str] = set()
    cmds = (
        ["git", "diff", "--name-only", "HEAD"],  # unstaged + staged vs HEAD
        ["git", "ls-files", "--others", "--exclude-standard"],  # new untracked
    )
    for cmd in cmds:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            out = proc.stdout
        except Exception:
            continue
        for line in out.splitlines():
            f = line.strip().replace("\\", "/")
            if f:
                files.add(f)
    return sorted(files)


def _file_digest(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).digest()
    except OSError:
        return b"<missing>"


def _remove_unused_imports(py: str, files: list[str]) -> list[str]:
    """Runs `ruff check --fix --select F401` on `files`; returns the files it rewrote."""
    existing = [f for f in files if os.path.isfile(f)]
    if not existing:
        return []
    before = {f: _file_digest(f) for f in existing}
    try:
        subprocess.run(
            [
                py, "-m", "ruff", "check", "--fix", "--select", "F401", "--force-exclude",
                "--quiet", "--exit-zero", *existing,
            ],
            capture_output=True,
            timeout=_RUFF_TIMEOUT_S,
            check=False,
        )
    except Exception:  # isolation point: cleanup is a convenience, never a gate
        return []
    return [f for f in existing if _file_digest(f) != before[f]]


def _fingerprint(files: list[str]) -> str:
    """Hash of HEAD plus the changed files' paths and contents (missing files hash as empty)."""
    digest = hashlib.sha256()
    digest.update(_head().encode())
    for path in files:
        digest.update(path.encode())
        digest.update(_file_digest(path))
    return digest.hexdigest()


def _last_green() -> str | None:
    try:
        with open(_STATE, encoding="utf-8") as fh:
            return json.load(fh).get("green")
    except (OSError, ValueError):
        return None


def _save_green(fingerprint: str) -> None:
    os.makedirs(os.path.dirname(_STATE), exist_ok=True)
    with open(_STATE, "w", encoding="utf-8") as fh:
        json.dump({"green": fingerprint}, fh)


def _standards(py: str, event: dict[str, Any]) -> dict[str, Any]:
    """Runs the standards check; returns its hook response ({} when there is nothing to say)."""
    try:
        proc = subprocess.run(
            [py, os.path.join("tools", "standards", "check_standards.py"), "stop", "--hook"],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_STANDARDS_TIMEOUT_S,
            check=False,
        )
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except Exception as exc:  # isolation point: a broken check must never trap the agent
        return {"systemMessage": f"standards: Stop check did not finish ({exc}); skipped."}


def main() -> None:
    """Runs the hook."""
    # ARAG_STOP_GATE is the pre-rename name, still honored.
    if "0" in (os.environ.get("XBRLRAG_STOP_GATE", "1"), os.environ.get("ARAG_STOP_GATE", "1")):
        return  # explicitly disabled

    event = read_event()
    py = env_python()
    all_changed = _changed_files()
    cleaned = _remove_unused_imports(py, [f for f in all_changed if f.endswith(".py")])
    standards = _standards(py, event)
    messages = []
    if cleaned:
        messages.append("Stop-gate removed unused imports (F401) in: " + ", ".join(cleaned))
    messages += [m for m in (standards.get("systemMessage"), standards.get("reason")) if m]

    changed = [f for f in all_changed if _is_test_input(f)]
    fingerprint = _fingerprint(changed)
    if not changed or fingerprint == _last_green():
        if standards.get("decision") == "block":
            emit({"decision": "block", "reason": "\n\n".join(messages)})
        elif messages:
            emit({"systemMessage": "\n\n".join(messages)})
        return  # nothing (new) to test — allow the stop

    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "test/unit", "-q", "-x", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=_PYTEST_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:
        # Infra failure (no interpreter, timeout) — fail open, don't trap.
        messages.append(f"H2 Stop-gate could not run pytest ({exc}); allowing stop.")
        emit({"systemMessage": "\n\n".join(messages)})
        return

    if proc.returncode == 0:
        _save_green(fingerprint)
        if standards.get("decision") == "block":
            emit({"decision": "block", "reason": "\n\n".join(messages)})
        elif messages:
            emit({"systemMessage": "\n\n".join(messages)})
        return  # green — allow the stop

    tail = "\n".join((proc.stdout or "").splitlines()[-25:])
    reason = (
        "H2 Stop-gate: `pytest test/unit` is RED after your changes "
        f"({len(changed)} file(s) touched). Fix the failing tests before "
        "stopping. Last output:\n" + tail
    )
    if event.get("stop_hook_active"):
        # Already blocked once this turn: allow the stop rather than loop, but say so.
        messages.append(reason)
        emit({"systemMessage": "\n\n".join(messages)})
        return
    emit({"decision": "block", "reason": "\n\n".join([reason, *messages])})


if __name__ == "__main__":
    main()
