"""Runaway-process watchdog (WS-8 / H4).

Autonomy safety for *agentic / dev* runs — the counterpart to B8, which pages on
*product* telemetry. This detects (and, opt-in, kills) XbrlRag-signature processes
that have gone runaway: orphaned children whose spawning parent died, tight
retry loops that overrun a wall-clock budget, and memory blow-ups.

Two hard safety properties:
1. **Scoped.** It only ever considers processes that either match the XbrlRag
   command-line signature or are descendants of an explicit `--root-pid`. It can
   never flag an arbitrary system process.
2. **Dry-run by default.** Killing is destructive, so it reports only unless
   `--kill` is passed. It also never touches its own PID, its parent, or PIDs ≤ 4.

The detection core (`find_runaways`) is pure and stdlib-only (unit-tested with
synthetic process lists); `psutil` is imported lazily only by the live collector.

Usage:
    python scripts/harness/watchdog.py                       # dry-run report (XbrlRag signature)
    python scripts/harness/watchdog.py --max-age-seconds 1800 --max-rss-mb 8000
    python scripts/harness/watchdog.py --root-pid 12345      # descendants of a run
    python scripts/harness/watchdog.py --kill                # actually terminate flagged procs
    python scripts/harness/watchdog.py --json --log-file logs/watchdog.jsonl

Scheduling: see docs/harness/UNATTENDED_RUNS.md for the headless-run policy,
caps, alert routing, and confirmed-rollback procedure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# Default command-line signature for "an XbrlRag process". Deliberately specific —
# never a match-all — so the watchdog cannot flag unrelated processes.
DEFAULT_SIGNATURE = (
    r"(-m\s+src\.|[\\/]src[\\/]|(^|\s)pytest(\s|$)|uvicorn\s+src\.api|"
    r"retrieval_pipeline|sec_edgar|src\.mcp_server|scripts[\\/](xbrl|eval|harness))"
)

# Critical PIDs the watchdog must never flag (System Idle / System on Windows).
_CRITICAL_PIDS = {0, 4}


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    name: str
    cmdline: str
    create_time: float  # epoch seconds
    rss_bytes: int = 0

    def running_secs(self, now: float) -> float:
        return max(0.0, now - self.create_time)

    def rss_mb(self) -> float:
        return self.rss_bytes / (1024 * 1024)


@dataclass
class Policy:
    signature: re.Pattern | None = None  # cmdline match scope
    root_pid: int | None = None  # descendant scope (alternative to signature)
    max_age_secs: float | None = None
    max_rss_mb: float | None = None
    flag_orphans: bool = True  # in-scope proc whose parent is gone
    min_orphan_age_secs: float = 30.0  # ignore just-spawned orphans (races)
    protected_pids: set[int] = field(default_factory=set)


@dataclass
class Flag:
    proc: ProcInfo
    reasons: list[str]


def _descendants(procs: list[ProcInfo], root_pid: int) -> set[int]:
    """PIDs of all transitive children of root_pid (root itself excluded)."""
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)
    out: set[int] = set()
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(children.get(pid, []))
    return out


def _ancestors(procs: list[ProcInfo], pid: int) -> set[int]:
    """PIDs of all transitive parents of *pid* (walk ppid up; pid itself excluded).

    Guards the watchdog from ever terminating a process it runs under, even one
    that matches the XbrlRag signature (e.g. a launching shell whose command line
    contains an XbrlRag command).
    """
    ppid_of = {p.pid: p.ppid for p in procs}
    out: set[int] = set()
    cur = ppid_of.get(pid)
    while cur is not None and cur not in out and cur > 0:
        out.add(cur)
        cur = ppid_of.get(cur)
    return out


def find_runaways(
    procs: list[ProcInfo],
    policy: Policy,
    now: float,
    self_pid: int,
    parent_pid: int | None = None,
) -> list[Flag]:
    """Pure detection: return the in-scope processes that are runaway.

    A process is *in scope* iff (root_pid set and it's a descendant) OR (its
    cmdline matches the signature). Self, the watchdog's entire **ancestor chain**
    (never kill a shell/agent we run under), protected, and critical PIDs are
    never flagged — even if they match the signature.
    """
    alive = {p.pid for p in procs}
    in_scope: set[int] = set()
    if policy.root_pid is not None:
        in_scope = _descendants(procs, policy.root_pid)
    scope_pids = (
        {self_pid}
        | _ancestors(procs, self_pid)
        | _CRITICAL_PIDS
        | set(policy.protected_pids)
    )
    if parent_pid is not None:
        scope_pids.add(parent_pid)

    flags: list[Flag] = []
    for p in procs:
        if p.pid in scope_pids or p.pid is None:
            continue
        scoped = (p.pid in in_scope) or (
            policy.signature is not None
            and bool(policy.signature.search(p.cmdline or ""))
        )
        if not scoped:
            continue

        reasons: list[str] = []
        age = p.running_secs(now)
        if policy.max_age_secs is not None and age > policy.max_age_secs:
            reasons.append(f"over-age ({int(age)}s > {int(policy.max_age_secs)}s)")
        if policy.max_rss_mb is not None and p.rss_mb() > policy.max_rss_mb:
            reasons.append(f"over-rss ({p.rss_mb():.0f}MB > {policy.max_rss_mb:.0f}MB)")
        if (
            policy.flag_orphans
            and p.ppid not in alive
            and p.ppid not in _CRITICAL_PIDS
            and age >= policy.min_orphan_age_secs
        ):
            reasons.append(f"orphaned (ppid {p.ppid} gone)")

        if reasons:
            flags.append(Flag(proc=p, reasons=reasons))
    return flags


# ---------------------------------------------------------------------------
# Live collection + termination (the only psutil-dependent parts)
# ---------------------------------------------------------------------------


def collect_processes() -> list[ProcInfo]:
    import psutil

    procs: list[ProcInfo] = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            info = p.info
            cmd = " ".join(info.get("cmdline") or []) or (info.get("name") or "")
            try:
                rss = p.memory_info().rss
            except Exception:
                rss = 0
            procs.append(
                ProcInfo(
                    pid=info["pid"],
                    ppid=info.get("ppid") or 0,
                    name=info.get("name") or "",
                    cmdline=cmd,
                    create_time=info.get("create_time") or 0.0,
                    rss_bytes=rss,
                )
            )
        except Exception:
            continue
    return procs


def terminate(pids: list[int], grace_secs: float = 5.0) -> dict[int, str]:
    """Terminate then (after grace) kill. Returns pid -> outcome. Destructive."""
    import psutil

    outcomes: dict[int, str] = {}
    procs = []
    for pid in pids:
        try:
            pr = psutil.Process(pid)
            pr.terminate()
            procs.append(pr)
        except psutil.NoSuchProcess:
            outcomes[pid] = "already-gone"
        except Exception as exc:
            outcomes[pid] = f"terminate-error: {exc}"
    _gone, alive = psutil.wait_procs(procs, timeout=grace_secs)
    for pr in _gone:
        outcomes.setdefault(pr.pid, "terminated")
    for pr in alive:
        try:
            pr.kill()
            outcomes[pr.pid] = "killed"
        except Exception as exc:
            outcomes[pr.pid] = f"kill-error: {exc}"
    return outcomes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_policy(args) -> Policy:
    sig = None if args.root_pid else re.compile(args.match, re.IGNORECASE)
    return Policy(
        signature=sig,
        root_pid=args.root_pid,
        max_age_secs=args.max_age_seconds,
        max_rss_mb=args.max_rss_mb,
        flag_orphans=not args.no_orphans,
        protected_pids=set(args.protect or []),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="XbrlRag runaway-process watchdog (H4)")
    ap.add_argument(
        "--match",
        default=DEFAULT_SIGNATURE,
        help="regex on cmdline defining an XbrlRag process (ignored if --root-pid)",
    )
    ap.add_argument(
        "--root-pid",
        type=int,
        default=None,
        help="instead of --match, scope to descendants of this PID",
    )
    ap.add_argument(
        "--max-age-seconds",
        type=float,
        default=1800.0,
        help="flag in-scope procs older than this (default 1800; 0 = flag all)",
    )
    ap.add_argument(
        "--max-rss-mb",
        type=float,
        default=None,
        help="flag in-scope procs over this resident memory",
    )
    ap.add_argument(
        "--no-orphans",
        action="store_true",
        help="do not flag in-scope orphans (dead parent)",
    )
    ap.add_argument(
        "--protect", type=int, nargs="*", default=[], help="extra PIDs to never flag"
    )
    ap.add_argument(
        "--kill",
        action="store_true",
        help="actually terminate flagged procs (default: dry-run report)",
    )
    ap.add_argument("--grace-seconds", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log-file", default=None, help="append a JSON line per run")
    args = ap.parse_args(argv)

    now = time.time()
    self_pid = os.getpid()
    parent_pid = os.getppid()
    procs = collect_processes()
    policy = _build_policy(args)
    flags = find_runaways(
        procs, policy, now=now, self_pid=self_pid, parent_pid=parent_pid
    )

    outcomes: dict[int, str] = {}
    if args.kill and flags:
        outcomes = terminate([f.proc.pid for f in flags], grace_secs=args.grace_seconds)

    record = {
        "ts": now,
        "scanned": len(procs),
        "self_pid": self_pid,
        "mode": "kill" if args.kill else "dry-run",
        "flagged": [
            {
                "pid": f.proc.pid,
                "ppid": f.proc.ppid,
                "name": f.proc.name,
                "age_s": int(f.proc.running_secs(now)),
                "rss_mb": round(f.proc.rss_mb(), 1),
                "reasons": f.reasons,
                "outcome": outcomes.get(f.proc.pid),
                "cmdline": f.proc.cmdline[:200],
            }
            for f in flags
        ],
    }

    if args.log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)
            with open(args.log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception as exc:
            sys.stderr.write(f"watchdog: could not write log-file: {exc}\n")

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        verb = "KILLED" if args.kill else "would flag"
        print(
            f"watchdog: scanned {len(procs)} procs, {verb} {len(flags)} runaway(s) "
            f"[{'kill' if args.kill else 'dry-run'}]"
        )
        for f in flags:
            oc = f" -> {outcomes.get(f.proc.pid)}" if args.kill else ""
            print(
                f"  pid={f.proc.pid} ppid={f.proc.ppid} age={int(f.proc.running_secs(now))}s "
                f"rss={f.proc.rss_mb():.0f}MB {f.proc.name}: {', '.join(f.reasons)}{oc}"
            )
            print(f"      cmd: {f.proc.cmdline[:160]}")
        if not flags:
            print("  (no runaways)")

    return 1 if flags else 0


if __name__ == "__main__":
    raise SystemExit(main())
