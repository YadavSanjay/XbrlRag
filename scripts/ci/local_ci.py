"""Local CI: the GitHub gates, run on this machine for a PR head or for main.

GitHub Actions minutes ran out (2026-11), so PRs get no checks. This runs the same gates
in a throwaway detached worktree of the commit, with the tdd tools taken from origin/main
(as `tdd-spec.yml` does), and writes a JSON + markdown result under `data/ci/runs/<sha>/`.
`--post` adds the markdown as a PR comment.

Steps (required ones decide the verdict; advisory ones mirror the `standards` job):

- `tdd manifest-check`, `tdd path-guard` (PRs only) and `tdd ci`, with main's tools;
- `pytest test/unit` (the `unit-tests` job);
- ruff, the standards `new-code` and `ratchet` checks, and mypy.

The worktree lives outside the repository on purpose: `src/__init__.py` calls
`load_dotenv()`, which walks up from the package and would find the main checkout's
`.env`. Provider keys and backend settings are also removed from the environment, so a
run behaves like the GitHub runner, which had none.

Usage (py312):
    python scripts/ci/local_ci.py run --pr <number> [--post]
    python scripts/ci/local_ci.py run --ref main
    python scripts/ci/local_ci.py watch [--interval 120] [--post] [--include-drafts]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "data" / "ci" / "runs"
MARKER = "<!-- xbrlrag-local-ci"
COMMENT_LIMIT = 60_000  # GitHub's comment limit is 65,536 characters.
TAIL_LINES = 60

# Environment prefixes a CI run must not see: provider keys, backends, deploy mode, data
# roots. The GitHub runner had none of them.
SCRUB_PREFIXES = (
    "XBRL_", "XBRLRAG_", "ARAG_", "QDRANT_", "RAGINFER_", "OPENAI_", "ANTHROPIC_",
    "DEEPSEEK_", "JINA_", "GEMINI_", "GOOGLE_API", "AWS_", "HF_TOKEN", "SEC_EDGAR_",
    "PYTHONPATH",
)
CI_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"}


@dataclasses.dataclass(frozen=True)
class Target:
    """What one run checks."""

    sha: str
    head_ref: str  # the branch name, "" for main
    pr: int | None = None
    title: str = ""


@dataclasses.dataclass(frozen=True)
class Step:
    """One gate: a command run in the worktree."""

    name: str
    argv: list[str]
    required: bool
    timeout_s: int


@dataclasses.dataclass
class StepResult:
    """The outcome of one step."""

    name: str
    required: bool
    status: str  # "pass" | "fail" | "timeout" | "error"
    seconds: float
    returncode: int | None
    tail: str


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Returns `env` without backend/provider settings, plus the CI offline flags.

    Args:
        env: The environment to start from (usually `os.environ`).

    Returns:
        A new mapping; `env` is not changed.
    """
    out = {k: v for k, v in env.items() if not k.upper().startswith(SCRUB_PREFIXES)}
    out.update(CI_ENV)
    return out


def dotenv_above(path: Path) -> Path | None:
    """Returns the first `.env` in `path` or any parent, as `find_dotenv` would see it."""
    for d in (path, *path.parents):
        if (d / ".env").is_file():
            return d / ".env"
    return None


def build_steps(py: str, tdd: str, target: Target, base: str | None) -> list[Step]:
    """The gates for `target`, in run order.

    Args:
        py: The interpreter (py312).
        tdd: Path of main's `tools/tdd/tdd.py`.
        target: The commit under test.
        base: The merge-base with origin/main; None skips path-guard (a run on main).

    Returns:
        The steps.
    """
    std = "tools/standards/check_standards.py"
    steps = [Step("tdd manifest-check", [py, tdd, "manifest-check"], True, 120)]
    if base is not None:
        steps.append(Step("tdd path-guard", [py, tdd, "path-guard", "--base", base, "--head",
                                             target.sha, "--head-ref", target.head_ref], True, 300))
    steps += [
        Step("tdd ci (spec tests)", [py, tdd, "ci", "--head-ref", target.head_ref], True, 3600),
        Step("pytest test/unit", [py, "-m", "pytest", "test/unit", "-q", "-n", "auto",
                                     "-p", "no:cacheprovider"],
             True, 2400),
        Step("ruff", [py, "-m", "ruff", "check", ".", "--output-format", "concise"], False, 300),
        Step("standards new-code", [py, std, "new-code"], False, 300),
        Step("standards ratchet", [py, std, "ratchet"], False, 300),
        Step("mypy src", [py, "-m", "mypy", "src/"], False, 1200),
    ]
    return steps


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def _tail(path: Path, n: int = TAIL_LINES) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def run_step(step: Step, cwd: Path, env: Mapping[str, str], log: Path) -> StepResult:
    """Runs one step, its output to `log`; a timeout kills the whole process tree."""
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        fh.write(f"$ {' '.join(step.argv)}\n")
        fh.flush()
        try:
            proc = subprocess.Popen(step.argv, cwd=cwd, env=dict(env), stdout=fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except OSError as exc:
            fh.write(f"could not start: {exc}\n")
            return StepResult(step.name, step.required, "error", 0.0, None, str(exc))
        try:
            rc: int | None = proc.wait(timeout=step.timeout_s)
            status = "pass" if rc == 0 else "fail"
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.wait()
            rc, status = None, "timeout"
            fh.write(f"\nTIMEOUT after {step.timeout_s}s\n")
    return StepResult(step.name, step.required, status, time.monotonic() - t0, rc, _tail(log))


def verdict(results: Sequence[StepResult]) -> bool:
    """True when every required step passed."""
    return all(r.status == "pass" for r in results if r.required)


def render_report(target: Target, results: Sequence[StepResult], meta: Mapping[str, str]) -> str:
    """The markdown result, starting with a marker line naming the commit.

    Args:
        target: The commit under test.
        results: The step results, in run order.
        meta: `main_sha`, `host`, `started` (shown in the footer).

    Returns:
        Markdown of at most `COMMENT_LIMIT` characters.
    """
    ok = verdict(results)
    if target.pr:
        where = f"PR {target.pr} (`{target.head_ref}`)"
    else:
        where = f"`{target.head_ref or 'main'}`"
    head = [
        f"{MARKER} sha={target.sha} -->",
        f"## Local CI: {'✅ passed' if ok else '❌ failed'} on `{target.sha[:8]}`, {where}",
        "",
        "| Step | Gate | Result | Time |",
        "|---|---|---|---|",
    ]
    icon = {"pass": "✅ pass", "fail": "❌ fail", "timeout": "⏱️ timeout", "error": "❌ error"}
    for r in results:
        gate = "required" if r.required else "advisory"
        head.append(f"| {r.name} | {gate} | {icon[r.status]} | {r.seconds / 60:.1f} min |")
    foot = ["", f"tools/tdd from origin/main `{meta.get('main_sha', '?')[:8]}`; ran on "
                f"{meta.get('host', '?')} at {meta.get('started', '?')} "
                "(`scripts/ci/local_ci.py`; GitHub Actions is out of credit)."]
    body = "\n".join(head)
    details = []
    for r in results:
        if r.status != "pass":
            details.append(f"\n<details><summary>{r.name}: last {TAIL_LINES} lines</summary>\n\n"
                           f"```\n{r.tail}\n```\n</details>")
    room = COMMENT_LIMIT - len(body) - len("\n".join(foot)) - 100
    text = "".join(details)
    if len(text) > room:
        text = text[: max(room, 0)] + "\n\n(output truncated; full logs in data/ci/runs)"
    return body + text + "\n" + "\n".join(foot) + "\n"


def select_pending(prs: Sequence[Mapping[str, Any]], done: set[str],
                   include_drafts: bool = False) -> list[Target]:
    """The open PRs whose head commit has no result yet, oldest number first.

    Args:
        prs: `gh pr list --json number,headRefName,headRefOid,isDraft,title` rows.
        done: Head SHAs that already have a result.
        include_drafts: Also run draft PRs.

    Returns:
        The targets to run.
    """
    out = []
    for p in sorted(prs, key=lambda p: int(p["number"])):
        if p.get("isDraft") and not include_drafts:
            continue
        if p["headRefOid"] in done:
            continue
        out.append(Target(p["headRefOid"], p["headRefName"], int(p["number"]), p.get("title", "")))
    return out


# --- git / gh plumbing (not unit-tested: thin wrappers over the CLIs) -------------------


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _gh_json(*args: str) -> Any:
    out = subprocess.run(["gh", *args], cwd=REPO_ROOT, check=True, capture_output=True,
                         text=True).stdout
    return json.loads(out)


def done_shas() -> set[str]:
    """Head SHAs that already have a stored result."""
    if not RUNS_DIR.is_dir():
        return set()
    return {d.name for d in RUNS_DIR.iterdir() if (d / "result.json").is_file()}


def resolve_target(pr: int | None, ref: str | None) -> Target:
    """Fetches and resolves `--pr N` or `--ref BRANCH` to a commit."""
    if pr is not None:
        info = _gh_json("pr", "view", str(pr), "--json", "headRefName,headRefOid,title")
        _git("fetch", "--no-tags", "-q", "origin", f"+refs/pull/{pr}/head:refs/remotes/pr/{pr}")
        sha = _git("rev-parse", f"refs/remotes/pr/{pr}")
        if sha != info["headRefOid"]:
            raise RuntimeError(
                f"PR {pr}: fetched {sha[:8]} but GitHub says {info['headRefOid'][:8]}")
        return Target(sha, info["headRefName"], pr, info.get("title", ""))
    name = ref or "main"
    try:
        _git("fetch", "--no-tags", "-q", "origin", name)
        sha = _git("rev-parse", "FETCH_HEAD")
    except subprocess.CalledProcessError:
        if name == "main":
            raise
        sha = _git("rev-parse", "--verify", f"refs/heads/{name}")  # not pushed yet
    return Target(sha, "" if name == "main" else name)


def worktree_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "xbrlrag-ci"


def run_target(target: Target, post: bool) -> bool:
    """Runs every gate for `target` and stores (and optionally posts) the result.

    Returns:
        The verdict.
    """
    _git("fetch", "--no-tags", "-q", "origin", "main")
    main_sha = _git("rev-parse", "origin/main")
    out_dir = RUNS_DIR / target.sha
    out_dir.mkdir(parents=True, exist_ok=True)
    root = worktree_root()
    root.mkdir(parents=True, exist_ok=True)
    wt = root / target.sha[:12]
    tools = root / f"tdd-main-{main_sha[:12]}"
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[local-ci] {started} {target.head_ref or 'main'} {target.sha[:8]} -> {out_dir}",
          flush=True)
    if wt.exists():
        _remove_worktree(wt)
    _git("worktree", "add", "--detach", "-q", str(wt), target.sha)
    try:
        found = dotenv_above(wt)
        if found is not None:
            raise RuntimeError(f"a .env above the CI worktree would leak into the run: {found}")
        if not tools.exists():
            tools.mkdir()
            archive = subprocess.run(["git", "archive", "origin/main", "tools/tdd"], cwd=REPO_ROOT,
                                     check=True, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
                tf.extractall(tools, filter="data")
        base = None
        if target.head_ref:  # a PR or a branch: classify it against main
            base = _git("merge-base", "origin/main", target.sha)
        env = scrub_env(os.environ)
        steps = build_steps(sys.executable, str(tools / "tools" / "tdd" / "tdd.py"), target, base)
        results = []
        for i, step in enumerate(steps):
            slug = re.sub(r"[^a-z0-9]+", "-", step.name.lower()).strip("-")
            log = out_dir / f"{i:02d}_{slug}.log"
            r = run_step(step, wt, env, log)
            print(f"[local-ci]   {step.name}: {r.status} ({r.seconds / 60:.1f} min)", flush=True)
            results.append(r)
    finally:
        _remove_worktree(wt)
    meta = {"main_sha": main_sha, "host": os.environ.get("COMPUTERNAME", "local"),
            "started": started}
    report = render_report(target, results, meta)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    ok = verdict(results)
    record = {"target": dataclasses.asdict(target), "ok": ok, "meta": meta,
              "steps": [dataclasses.asdict(r) | {"tail": None} for r in results]}
    (out_dir / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    if post and target.pr is not None:
        subprocess.run(["gh", "pr", "comment", str(target.pr), "--body-file",
                        str(out_dir / "report.md")], cwd=REPO_ROOT, check=True)
    print(f"[local-ci] {'PASSED' if ok else 'FAILED'} {target.sha[:8]}", flush=True)
    return ok


def _remove_worktree(wt: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO_ROOT,
                   capture_output=True)
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, capture_output=True)


def watch(interval: int, post: bool, include_drafts: bool) -> None:
    """Polls open PRs (and main) forever; runs the gates on every new head commit."""
    lock = RUNS_DIR.parent / "watch.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"another watcher holds {lock}; delete it if none is running") from None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        while True:
            try:
                prs = _gh_json("pr", "list", "--state", "open", "--base", "main", "--json",
                               "number,headRefName,headRefOid,isDraft,title")
                done = done_shas()
                for t in select_pending(prs, done, include_drafts):
                    run_target(resolve_target(t.pr, None), post)
                main = resolve_target(None, "main")
                if main.sha not in done_shas():
                    run_target(main, post=False)
            except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
                print(f"[local-ci] poll failed: {exc}", flush=True)
            time.sleep(interval)
    finally:
        lock.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the gates once")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--pr", type=int)
    g.add_argument("--ref")
    r.add_argument("--post", action="store_true", help="post the result as a PR comment")
    w = sub.add_parser("watch", help="poll open PRs and main, run each new head")
    w.add_argument("--interval", type=int, default=120)
    w.add_argument("--post", action="store_true")
    w.add_argument("--include-drafts", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return 0 if run_target(resolve_target(a.pr, a.ref), a.post) else 1
    watch(a.interval, a.post, a.include_drafts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
