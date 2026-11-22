# Dev-time guardrail hooks (WS-8 / H2)

Deterministic Claude Code hooks that *enforce* dev discipline instead of relying
on the agent to remember it — the dev-time counterpart to the runtime
faithfulness verifier. All logic is plain-stdlib Python (the hook shell is Git
Bash and `jq` isn't installed), so each hook is just `python .claude/hooks/<script>.py`.
The launcher `python` is the base interpreter (3.10); real work runs in py312.

| Script | Event | Matcher | What it does |
|--------|-------|---------|--------------|
| [`guard_protected_paths.py`](guard_protected_paths.py) | PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit\|Read\|Bash\|PowerShell` | **Denies** file-tool access to secret/credential files — `.env` (but not `.env.example`/`.template`/…), `*.pem`/`*.key`/`*.pfx`, `id_rsa`, `.git-credentials`, `service-account*.json`, etc., including through a symlink — and any Bash/PowerShell command whose text names a non-template env file (`cat .env`, `Get-Content .env.local`). The shell check is text matching: it stops accidental reads, not a determined workaround. **Fails closed**: an unparseable event, a file tool with no target path, a shell tool with no command, or a guard error blocks the call (exit 2). |
| [`format_python.py`](format_python.py) | PostToolUse | `Edit\|Write\|MultiEdit` | Runs `ruff check --fix --unfixable F401` on an edited `.py` file (no-op otherwise): unused imports are left for the Stop hook, so an import added before its first use survives. No formatter. Never blocks. |
| [`stop_gate.py`](stop_gate.py) | Stop | — | 0. **Removes unused imports** (`ruff check --fix --select F401`) in changed `.py` files and names the files it rewrote. 1. **Coding standards** via `tools/standards/check_standards.py stop --hook` (advisory: reports, never blocks). 2. **Blocks stopping on a red build**: if a test input changed vs HEAD (`src/`, `test/`, `prompts/`, `configs/`, `scripts/`, `tools/`, `.claude/agents/`, `docs/harness/`, root `.py`/`.toml`/`.ini`/`.cfg`/`.txt`, `VISION.md`), runs `pytest test/unit -x` and refuses the stop if it fails. Skips the test run when HEAD and the changed test inputs are unchanged since the last green run; blocks at most once per turn (`stop_hook_active`). |

Shared helpers (stdin parsing, interpreter resolution) live in
[`_hooklib.py`](_hooklib.py). Run state (fingerprints) lives in the gitignored `.claude/state/`.

## Interpreter

The fixer and the Stop-gate need the **py312** env (full deps + the pinned ruff
from `requirements-dev.txt`), not base `python`. `_hooklib.env_python()` resolves it in
order: `$XBRLRAG_HOOK_PYTHON` → `envs\py312` beside the base conda `python` running the hook →
`~\Anaconda3|miniconda3\envs\py312\python.exe` → `python`/`python3` on PATH.
**On a different machine, set `XBRLRAG_HOOK_PYTHON`** to your env's python.

The guard uses only stdlib, so any interpreter runs it.

## Tests

[`test/unit/test_hooks.py`](../../test/unit/test_hooks.py) tests every hook both ways: the
guard's block, allow and fail-closed cases (run as a real subprocess), the fixer's skip and
never-block behavior, and the Stop gate's block on red, allow on green, docs-only skip,
once-per-turn and fail-open cases (subprocess calls stubbed). Change a hook, extend the tests.

## Wiring

`.claude/settings.json` is committed and carries only the hook registration. Personal
permission entries belong in `.claude/settings.local.json` (gitignored). After changing
settings.json, open `/hooks` once (or restart) so Claude Code reloads the config.

## Escape hatches

- `XBRLRAG_STOP_GATE=0` — disable the Stop-gate (e.g. mid-refactor with
  intentionally-red tests).
- `disableAllHooks: true` in settings — disable every hook.
- The Stop-gate is **fail-open** on infrastructure errors (missing interpreter,
  git, or a timeout): only a genuine test failure blocks a stop. The guard is the
  opposite: as a security control it **fails closed**.

## Verifying a change to these hooks

Pipe a synthetic event straight in (no Claude Code needed):

```bash
# guard: should print a deny JSON
echo '{"tool_input":{"file_path":".env"}}' | python .claude/hooks/guard_protected_paths.py
# fixer: applies ruff's safe fixes to the file in place
echo '{"tool_input":{"file_path":"some_messy.py"}}' | python .claude/hooks/format_python.py
# stop-gate: no-op unless .py files changed (standards) or src/test .py changed vs HEAD (pytest)
echo '{}' | python .claude/hooks/stop_gate.py
```
