"""Harness re-audit staleness gate (WS-8 / H7).

Every harness component encodes a "the model can't do X" assumption that goes
stale as models improve; without a cadence, scaffolding accretes as cargo-cult
complexity. [`docs/harness/REAUDIT.md`](../../docs/harness/REAUDIT.md) records the
dated cadence + a per-component register; this script makes the cadence
*enforceable* rather than aspirational — it fails when the next re-audit is
overdue, so CI / the feature gate can nag before the scaffolding rots.

REAUDIT.md carries machine markers the checker reads:

    <!-- reaudit:last=2026-07-14 -->
    <!-- reaudit:next_due=2026-10-12 -->
    <!-- reaudit:cadence_days=90 -->

Usage:
    python scripts/harness/check_reaudit.py                 # status
    python scripts/harness/check_reaudit.py --gate          # exit 1 if OVERDUE
    python scripts/harness/check_reaudit.py --gate --strict  # also fail if DUE-SOON
    python scripts/harness/check_reaudit.py --today 2026-11-01   # evaluate as-of a date
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from typing import Dict, Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REAUDIT_DOC = os.path.join(REPO_ROOT, "docs", "harness", "REAUDIT.md")

WARN_WITHIN_DAYS = 14  # "due-soon" window before next_due

_MARKER = re.compile(r"<!--\s*reaudit:(\w+)=([0-9-]+)\s*-->")


def parse_markers(text: str) -> dict[str, str]:
    """Extract the reaudit:* markers from REAUDIT.md into a {key: value} dict."""
    return {m.group(1): m.group(2) for m in _MARKER.finditer(text)}


def _to_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def status(
    next_due: _dt.date, today: _dt.date, warn_within_days: int = WARN_WITHIN_DAYS
) -> tuple[str, int]:
    """Return (level, days_until_due). level ∈ {ok, due-soon, overdue}.

    days_until_due is negative when overdue.
    """
    days = (next_due - today).days
    if days < 0:
        return "overdue", days
    if days <= warn_within_days:
        return "due-soon", days
    return "ok", days


def consistency_warnings(markers: dict[str, str]) -> list:
    """Sanity-check the markers: next_due should be last + cadence_days."""
    warns = []
    try:
        last = _to_date(markers["last"])
        nxt = _to_date(markers["next_due"])
        cadence = int(markers["cadence_days"])
    except (KeyError, ValueError) as exc:
        return [f"missing/invalid marker: {exc}"]
    if nxt <= last:
        warns.append(f"next_due ({nxt}) is not after last ({last})")
    expected = last + _dt.timedelta(days=cadence)
    if nxt != expected:
        warns.append(
            f"next_due ({nxt}) != last + cadence_days ({expected}); "
            "re-audit sooner on a major model release is fine, but keep them consistent"
        )
    return warns


def main(argv: list | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="XbrlRag harness re-audit staleness gate (H7)"
    )
    ap.add_argument("--gate", action="store_true", help="exit 1 when OVERDUE")
    ap.add_argument(
        "--strict", action="store_true", help="with --gate, also fail on DUE-SOON"
    )
    ap.add_argument(
        "--today", default=None, help="evaluate as-of YYYY-MM-DD (default: system date)"
    )
    ap.add_argument("--doc", default=REAUDIT_DOC)
    args = ap.parse_args(argv)

    if not os.path.isfile(args.doc):
        sys.stderr.write(f"re-audit doc not found: {args.doc}\n")
        return 3
    with open(args.doc, encoding="utf-8") as fh:
        markers = parse_markers(fh.read())

    for key in ("last", "next_due", "cadence_days"):
        if key not in markers:
            sys.stderr.write(f"REAUDIT.md missing marker reaudit:{key}\n")
            return 3

    today = _to_date(args.today) if args.today else _dt.date.today()
    next_due = _to_date(markers["next_due"])
    level, days = status(next_due, today)

    for w in consistency_warnings(markers):
        print(f"WARN: {w}")

    icon = {"ok": "OK", "due-soon": "DUE-SOON", "overdue": "OVERDUE"}[level]
    print(
        f"[{icon}] harness re-audit — last {markers['last']}, next due {markers['next_due']} "
        f"(cadence {markers['cadence_days']}d). As of {today}: "
        + (f"{days} day(s) remaining." if days >= 0 else f"{-days} day(s) OVERDUE.")
    )
    if level != "ok":
        print(
            "  → Re-run the harness re-audit per docs/harness/REAUDIT.md "
            "(re-score against the AI-harness-engineering standard; retire scaffolding "
            "the current model no longer needs)."
        )

    if args.gate:
        if level == "overdue" or (args.strict and level == "due-soon"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
