"""Block-and-allow tests for the dev-time guardrail hooks in `.claude/hooks/`.

Every guard is tested both ways: cases it must block and cases it must allow. The
protected-path guard runs as a real subprocess (its exit code is the contract); the
formatter and Stop gate are imported and driven with their subprocess calls stubbed,
so no ruff or pytest run happens here.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOKS = os.path.join(REPO_ROOT, ".claude", "hooks")
GUARD = os.path.join(HOOKS, "guard_protected_paths.py")


def _load(name: str) -> ModuleType:
    """Import a hook script as a module."""
    sys.path.insert(0, HOOKS)
    spec = importlib.util.spec_from_file_location(name, os.path.join(HOOKS, f"{name}.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_guard(payload: str) -> subprocess.CompletedProcess[str]:
    """Run the guard with a raw stdin payload."""
    return subprocess.run(
        [sys.executable, GUARD], input=payload, capture_output=True, text=True, timeout=30,
        check=False,
    )


def _event(tool: str, **tool_input: Any) -> str:
    return json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    )


def _denied(proc: subprocess.CompletedProcess[str]) -> bool:
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] == "deny"


# --------------------------------------------------------------------------- guard: block


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "D:/Work/Repos/XbrlRag/.env",
        r"D:\Work\Repos\XbrlRag\.env",
        "config/.env.local",
        ".env.production",
        "id_rsa",
        "keys/server.key",
        "certs/tls.pem",
        "deploy/service-account-prod.json",
        "secrets.json",
        ".git-credentials",
        ".pgpass",
    ],
)
@pytest.mark.parametrize("tool", ["Write", "Edit", "Read"])
def test_guard_denies_protected_files(tool: str, path: str) -> None:
    assert _denied(_run_guard(_event(tool, file_path=path)))


def test_guard_denies_notebook_edit_of_protected_file() -> None:
    assert _denied(_run_guard(_event("NotebookEdit", notebook_path="x/.env")))


def test_guard_denies_symlink_to_protected_file(tmp_path: Any) -> None:
    secret = tmp_path / ".env"
    secret.write_text("KEY=1", encoding="utf-8")
    link = tmp_path / "harmless.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    assert _denied(_run_guard(_event("Edit", file_path=str(link))))


# --------------------------------------------------------------------------- guard: allow


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".env.template",
        "src/llm/config.py",
        "docs/design/README.md",
        "environment.yml",
        "keyboard.py",
        "test/unit/test_hooks.py",
    ],
)
def test_guard_allows_ordinary_files(path: str) -> None:
    proc = _run_guard(_event("Edit", file_path=path))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --------------------------------------------------------------------------- guard: fail closed


@pytest.mark.parametrize("payload", ["", "not json", "[1, 2]"])
def test_guard_blocks_unparseable_event(payload: str) -> None:
    proc = _run_guard(payload)
    assert proc.returncode == 2
    assert "Blocked by H2 guardrail" in proc.stderr


def test_guard_blocks_event_without_target_path() -> None:
    proc = _run_guard(_event("Write", content="x"))
    assert proc.returncode == 2


# --------------------------------------------------------------------------- guard: shell


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "Get-Content .env",
        r"type D:\Work\Repos\XbrlRag\.env",
        "grep KEY config/.env.local",
        "cp .env /tmp/x",
        "echo $(< .ENV)",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_denies_shell_commands_naming_env(tool: str, command: str) -> None:
    assert _denied(_run_guard(_event(tool, command=command)))


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "cat .env.example",
        "conda run -n py312 python -m pytest test/unit -q",
        "grep -rn environment src/",
        "ls .envrc-notes",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_allows_ordinary_shell_commands(tool: str, command: str) -> None:
    proc = _run_guard(_event(tool, command=command))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_guard_blocks_shell_tool_without_command(tool: str) -> None:
    assert _run_guard(_event(tool, command="  ")).returncode == 2


def test_guard_ignores_other_tools_without_a_path() -> None:
    proc = _run_guard(_event("WebSearch", query="10-K revenue"))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --------------------------------------------------------------------------- formatter


def _feed(monkeypatch: pytest.MonkeyPatch, event: dict[str, Any]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))


def test_formatter_skips_non_python_files(monkeypatch: pytest.MonkeyPatch) -> None:
    fmt = _load("format_python")
    calls: list[Any] = []
    monkeypatch.setattr(fmt.subprocess, "run", lambda *a, **k: calls.append(a))
    _feed(monkeypatch, {"tool_input": {"file_path": os.path.join(REPO_ROOT, "README.md")}})
    fmt.main()
    assert calls == []


def test_formatter_runs_ruff_without_removing_unused_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fmt = _load("format_python")
    calls: list[list[str]] = []
    monkeypatch.setattr(fmt.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    _feed(monkeypatch, {"tool_input": {"file_path": os.path.join(HOOKS, "_hooklib.py")}})
    fmt.main()
    assert len(calls) == 1
    assert "ruff" in calls[0] and "--fix" in calls[0]
    assert calls[0][calls[0].index("--unfixable") + 1] == "F401"


def test_formatter_never_blocks_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fmt = _load("format_python")

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("no interpreter")

    monkeypatch.setattr(fmt.subprocess, "run", boom)
    _feed(monkeypatch, {"tool_input": {"file_path": os.path.join(HOOKS, "_hooklib.py")}})
    fmt.main()  # must not raise


# --------------------------------------------------------------------------- stop gate


def _stop_gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed: list[str],
    pytest_rc: int | None,
    last_green: str | None = None,
    event: dict[str, Any] | None = None,
) -> tuple[ModuleType, list[dict[str, Any]]]:
    gate = _load("stop_gate")
    emitted: list[dict[str, Any]] = []
    monkeypatch.delenv("XBRLRAG_STOP_GATE", raising=False)
    monkeypatch.delenv("ARAG_STOP_GATE", raising=False)
    monkeypatch.setattr(gate, "emit", emitted.append)
    monkeypatch.setattr(gate, "env_python", lambda: sys.executable)
    monkeypatch.setattr(gate, "_changed_files", lambda: changed)
    monkeypatch.setattr(gate, "_remove_unused_imports", lambda py, files: [])
    monkeypatch.setattr(gate, "_standards", lambda py, ev: {})
    monkeypatch.setattr(gate, "_fingerprint", lambda files: "fp")
    monkeypatch.setattr(gate, "_last_green", lambda: last_green)
    monkeypatch.setattr(gate, "_save_green", lambda fp: None)

    def fake_run(cmd: list[str], **_k: Any) -> subprocess.CompletedProcess[str]:
        assert "pytest" in cmd, "only pytest may run in these tests"
        if pytest_rc is None:
            raise subprocess.TimeoutExpired(cmd, 1)
        return subprocess.CompletedProcess(cmd, pytest_rc, stdout="1 failed", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    _feed(monkeypatch, event or {})
    gate.main()
    return gate, emitted


def test_stop_gate_blocks_on_red_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(monkeypatch, changed=["src/llm/config.py"], pytest_rc=1)
    assert emitted and emitted[-1]["decision"] == "block"
    assert "RED" in emitted[-1]["reason"]


def test_stop_gate_allows_green_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(monkeypatch, changed=["src/llm/config.py"], pytest_rc=0)
    assert all(e.get("decision") != "block" for e in emitted)


def test_stop_gate_skips_docs_only_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(monkeypatch, changed=["docs/design/README.md"], pytest_rc=1)
    assert all(e.get("decision") != "block" for e in emitted)


def test_stop_gate_skips_unchanged_green_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(
        monkeypatch, changed=["src/llm/config.py"], pytest_rc=1, last_green="fp"
    )
    assert all(e.get("decision") != "block" for e in emitted)


def test_stop_gate_blocks_once_per_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(
        monkeypatch, changed=["src/x.py"], pytest_rc=1, event={"stop_hook_active": True}
    )
    assert all(e.get("decision") != "block" for e in emitted)
    assert "RED" in emitted[-1]["systemMessage"]


def test_stop_gate_fails_open_on_infrastructure_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _, emitted = _stop_gate(monkeypatch, changed=["src/x.py"], pytest_rc=None)
    assert all(e.get("decision") != "block" for e in emitted)
    assert "could not run pytest" in emitted[-1]["systemMessage"]


def test_stop_gate_respects_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _load("stop_gate")
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(gate, "emit", emitted.append)
    monkeypatch.setenv("XBRLRAG_STOP_GATE", "0")
    gate.main()
    assert emitted == []
