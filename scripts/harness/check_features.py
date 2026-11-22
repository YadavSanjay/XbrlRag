"""Machine-checkable feature/scope gate for XbrlRag (WS-8 / H5).

Reads `docs/harness/feature_manifest.yaml` — the per-item testable list of every
feature in the 8-workstream plan — runs each item's *check* (does the artifact
that proves "done" actually exist?), and reports satisfied vs unsatisfied.

Why this exists: the design docs describe the target in prose, but "done" was
never externally verifiable, so scope could drift mid-run. Each manifest item
carries an executable check that **starts failing** and only passes once the
feature is really built. The `--gate` mode makes that a hard contract:

    a `status: done` item whose check FAILS  → gate fails (a claimed feature
                                                regressed or was never real)
    a `status: todo` item whose check PASSES → warning (build the manifest is
                                                behind reality — flip it to done)

`docs/harness/feature_manifest.yaml` is the machine-checkable half of H5;
`VISION.md` is the human-readable scope doc the agent declines against.

Usage:
    python scripts/harness/check_features.py                 # full report
    python scripts/harness/check_features.py --gate          # CI/Stop gate (exit != 0 on drift)
    python scripts/harness/check_features.py --status done    # filter
    python scripts/harness/check_features.py --workstream WS-7
    python scripts/harness/check_features.py --json           # machine-readable

Check types (one key per check dict):
    path:     <relpath>                 artifact exists (file or dir)
    absent:   <relpath>                 artifact does NOT exist
    contains: {file: <relpath>, pattern: <regex>}   file matches regex
    cmd:      <shell string>            subprocess exits 0 (run at repo root)
An item's `checks:` all-pass ⇒ the item is *satisfied*. `manual: <note>` items
have no automated check; they are reported separately and excluded from the gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MANIFEST = os.path.join(REPO_ROOT, "docs", "harness", "feature_manifest.yaml")

VALID_STATUS = {"done", "partial", "todo", "deferred"}


# ---------------------------------------------------------------------------
# Check evaluation
# ---------------------------------------------------------------------------


def _rel(path: str) -> str:
    return os.path.join(REPO_ROOT, path.replace("/", os.sep))


def eval_check(check: dict[str, Any]) -> tuple[bool, str]:
    """Evaluate one check dict → (passed, human-detail)."""
    if "path" in check:
        p = check["path"]
        return os.path.exists(_rel(p)), f"path {p}"
    if "absent" in check:
        p = check["absent"]
        return (not os.path.exists(_rel(p))), f"absent {p}"
    if "contains" in check:
        spec = check["contains"]
        f, pat = spec["file"], spec["pattern"]
        fp = _rel(f)
        if not os.path.isfile(fp):
            return False, f"contains {f} (missing)"
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                ok = re.search(pat, fh.read()) is not None
        except Exception as exc:
            return False, f"contains {f} (read error: {exc})"
        return ok, f"contains {f} ~ /{pat}/"
    if "cmd" in check:
        cmd = check["cmd"]
        try:
            rc = subprocess.run(
                cmd,
                shell=True,
                cwd=REPO_ROOT,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
        except Exception as exc:
            return False, f"cmd (error: {exc})"
        return rc == 0, f"cmd `{cmd}`"
    return False, f"unknown check {list(check)}"


def eval_item(item: dict[str, Any]) -> dict[str, Any]:
    """Evaluate an item → dict with satisfied + per-check results."""
    if item.get("manual"):
        return {"satisfied": None, "checks": [], "manual": item["manual"]}
    checks = item.get("checks") or []
    results = [dict(zip(("passed", "detail"), eval_check(c))) for c in checks]
    satisfied = bool(checks) and all(r["passed"] for r in results)
    return {"satisfied": satisfied, "checks": results, "manual": None}


# ---------------------------------------------------------------------------
# Manifest loading + validation
# ---------------------------------------------------------------------------


def load_manifest(path: str = MANIFEST) -> list[dict[str, Any]]:
    import yaml

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    items = data.get("features") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("manifest must contain a 'features' list")
    return items


def validate_manifest(items: list[dict[str, Any]]) -> list[str]:
    """Return a list of schema problems (empty = valid)."""
    errs: list[str] = []
    seen = set()
    for i, it in enumerate(items):
        iid = it.get("id")
        if not iid:
            errs.append(f"item #{i} missing id")
            continue
        if iid in seen:
            errs.append(f"duplicate id {iid}")
        seen.add(iid)
        if not it.get("title"):
            errs.append(f"{iid}: missing title")
        st = it.get("status")
        if st not in VALID_STATUS:
            errs.append(f"{iid}: bad status {st!r} (want {sorted(VALID_STATUS)})")
        if not it.get("manual") and not it.get("checks"):
            errs.append(f"{iid}: needs 'checks' or 'manual'")
    return errs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _color(txt: str, code: str) -> str:
    if not sys.stdout.isatty():
        return txt
    return f"\033[{code}m{txt}\033[0m"


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp1252; the report uses → ≠ └─
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="XbrlRag machine-checkable feature/scope gate (H5)"
    )
    ap.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero if any done item is unsatisfied (scope-drift gate)",
    )
    ap.add_argument("--status", help="filter to a status (done/partial/todo/deferred)")
    ap.add_argument("--workstream", help="filter to a workstream id (e.g. WS-7)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--manifest", default=MANIFEST)
    args = ap.parse_args(argv)

    items = load_manifest(args.manifest)
    errs = validate_manifest(items)
    if errs:
        for e in errs:
            sys.stderr.write(f"MANIFEST ERROR: {e}\n")
        return 3

    rows = []
    for it in items:
        if args.status and it.get("status") != args.status:
            continue
        if args.workstream and it.get("workstream") != args.workstream:
            continue
        rows.append((it, eval_item(it)))

    # Gate logic: claimed-done must be satisfied; todo-but-satisfied is a warning.
    broken = [
        it for it, r in rows if it.get("status") == "done" and r["satisfied"] is False
    ]
    ahead = [
        it
        for it, r in rows
        if it.get("status") in ("todo", "deferred") and r["satisfied"] is True
    ]

    if args.json:
        out = [
            {
                "id": it["id"],
                "title": it["title"],
                "status": it.get("status"),
                "workstream": it.get("workstream"),
                "finding": it.get("finding"),
                "satisfied": r["satisfied"],
                "manual": r["manual"],
                "checks": r["checks"],
            }
            for it, r in rows
        ]
        print(
            json.dumps(
                {
                    "features": out,
                    "broken": [b["id"] for b in broken],
                    "ahead": [a["id"] for a in ahead],
                },
                indent=2,
            )
        )
        return 1 if (args.gate and broken) else 0

    # Human report
    done = sum(1 for it, r in rows if r["satisfied"] is True)
    unsat = sum(1 for it, r in rows if r["satisfied"] is False)
    manual = sum(1 for it, r in rows if r["satisfied"] is None)
    for it, r in rows:
        if r["satisfied"] is None:
            mark, code = "MANUAL", "33"
        elif r["satisfied"]:
            mark, code = "PASS", "32"
        else:
            mark, code = "FAIL", "31"
        tag = f"[{it.get('workstream','?')}/{it.get('finding','?')}]"
        print(f"{_color(mark.ljust(6), code)} {it['id']:<10} {tag:<14} {it['title']}")
        if r["satisfied"] is False:
            for c in r["checks"]:
                if not c["passed"]:
                    print(f"         └─ unsatisfied: {c['detail']}")
    print("-" * 72)
    print(f"satisfied={done}  unsatisfied={unsat}  manual={manual}  total={len(rows)}")
    if ahead:
        print(
            _color(
                f"NOTE: {len(ahead)} todo/deferred item(s) now PASS — flip to done: "
                + ", ".join(a["id"] for a in ahead),
                "33",
            )
        )
    if broken:
        print(
            _color(
                f"GATE FAIL: {len(broken)} done item(s) unsatisfied: "
                + ", ".join(b["id"] for b in broken),
                "31",
            )
        )

    if args.gate and broken:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
