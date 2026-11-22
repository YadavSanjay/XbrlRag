"""Tests for the H7 re-audit staleness checker (WS-8 / H7).

Pure date/marker logic driven with explicit dates (no reliance on the system
clock), plus an integration pass over the real REAUDIT.md.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "harness"))

import check_reaudit as cr  # noqa: E402


def test_parse_markers():
    text = (
        "# doc\n<!-- reaudit:last=2026-07-14 -->\n"
        "<!-- reaudit:next_due=2026-10-12 -->\n<!-- reaudit:cadence_days=90 -->\n"
    )
    m = cr.parse_markers(text)
    assert m == {"last": "2026-07-14", "next_due": "2026-10-12", "cadence_days": "90"}


def test_status_ok_due_soon_overdue():
    due = dt.date(2026, 10, 12)
    assert cr.status(due, dt.date(2026, 7, 14))[0] == "ok"
    assert cr.status(due, dt.date(2026, 10, 1))[0] == "due-soon"  # within 14d
    over_level, days = cr.status(due, dt.date(2026, 10, 20))
    assert over_level == "overdue" and days < 0


def test_status_boundary_exactly_due():
    due = dt.date(2026, 10, 12)
    assert cr.status(due, dt.date(2026, 10, 12)) == ("due-soon", 0)  # 0 days left
    assert cr.status(due, dt.date(2026, 10, 13))[0] == "overdue"


def test_consistency_ok_and_bad():
    ok = {"last": "2026-07-14", "next_due": "2026-10-12", "cadence_days": "90"}
    assert cr.consistency_warnings(ok) == []
    bad = {"last": "2026-07-14", "next_due": "2026-08-01", "cadence_days": "90"}
    warns = cr.consistency_warnings(bad)
    assert warns and "cadence_days" in warns[0]
    inverted = {"last": "2026-07-14", "next_due": "2026-07-01", "cadence_days": "-13"}
    assert cr.consistency_warnings(inverted)


def test_real_doc_is_consistent_and_not_overdue_at_last():
    """The committed REAUDIT.md must be internally consistent and OK as of its own
    last-audit date (guards against a doc that ships already stale)."""
    with open(cr.REAUDIT_DOC, encoding="utf-8") as fh:
        markers = cr.parse_markers(fh.read())
    assert cr.consistency_warnings(markers) == []
    last = dt.date.fromisoformat(markers["last"])
    due = dt.date.fromisoformat(markers["next_due"])
    assert cr.status(due, last)[0] == "ok"


def test_gate_overdue_returns_nonzero():
    # Dates relative to the committed doc, so the test survives each re-audit.
    with open(cr.REAUDIT_DOC, encoding="utf-8") as fh:
        markers = cr.parse_markers(fh.read())
    last = dt.date.fromisoformat(markers["last"])
    due = dt.date.fromisoformat(markers["next_due"])
    overdue = (due + dt.timedelta(days=1)).isoformat()
    assert cr.main(["--gate", "--today", overdue]) == 1  # past due → overdue
    assert cr.main(["--gate", "--today", last.isoformat()]) == 0  # fresh → pass
