"""C6: ``render_hx_report``.

Oracle: requirement 13 of docs/specs/hx-authoring.md, which fixes the layout line by line.
The resolutions are built by hand; one carries a stray ``question`` key that must never
be printed.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import render_hx_report

LEAK = "Q-zqxjvleakprobe"


def _item(name, status, n):
    return {"name": name, "status": status, "classes": [[i + 1] for i in range(n)]
            if status != "verified" else [[n]]}


def _resolutions():
    return [
        {"id": "a1", "kind": "cross_filer", "status": "verified",
         "items": [_item("rows[0]", "verified", 1)], "classes": [[1]], "question": LEAK},
        {"id": "a2", "kind": "cross_period", "status": "not_found",
         "items": [_item("rows[0]", "verified", 3), _item("rows[1]", "not_found", 0),
                   _item("rows[2]", "ambiguous", 2)],
         "classes": [[3], [], []]},
        {"id": "a3", "kind": "text", "status": "skipped", "items": [], "classes": []},
        {"id": "a4", "kind": "cross_filer", "status": "arith_mismatch",
         "items": [_item("rows[0]", "verified", 1), _item("rows[1]", "verified", 2)],
         "classes": [[1], [2]], "note": LEAK},
    ]


@pytest.mark.criterion("C6")
def test_layout_line_by_line(kit):
    # oracle: requirement 13 — header, blank, one line per case (+ failing item lines for
    # a status other than verified/skipped), blank, total, status counts in fixed order.
    text = render_hx_report(_resolutions())
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines[0] == "H-X answer-key check"
    assert lines[1] == ""
    assert lines[2].split() == ["a1", "cross_filer", "verified", "items=1"]
    assert lines[3].split() == ["a2", "cross_period", "not_found", "items=3"]
    assert lines[4].startswith("    rows[1]: not_found, 0 ")
    assert lines[5].startswith("    rows[2]: ambiguous, 2 ")
    assert lines[6].split() == ["a3", "text", "skipped", "items=0"]
    assert lines[7].split() == ["a4", "cross_filer", "arith_mismatch", "items=2"]
    assert lines[8:] == ["", "total: 4", "verified: 1", "not_found: 1", "arith_mismatch: 1",
                         "skipped: 1"]
    assert LEAK not in text


@pytest.mark.criterion("C6")
def test_status_order_puts_unknown_statuses_last_sorted(kit):
    # oracle: requirement 13 — verified, ambiguous, not_found, arith_mismatch, skipped, then
    # any other status, sorted.
    res = [{"id": f"i{n}", "kind": "text", "status": s, "items": [], "classes": []}
           for n, s in enumerate(["zeta", "skipped", "alpha", "ambiguous", "verified"])]
    lines = render_hx_report(res).splitlines()
    assert lines[-6:] == ["total: 5", "verified: 1", "ambiguous: 1", "skipped: 1",
                          "alpha: 1", "zeta: 1"]


_STATUSES = ["verified", "ambiguous", "not_found", "arith_mismatch", "skipped"]
_ITEM_STATUSES = ["verified", "ambiguous", "not_found"]


@pytest.mark.criterion("C6")
@given(spec=st.lists(st.tuples(st.sampled_from(_STATUSES),
                               st.lists(st.sampled_from(_ITEM_STATUSES), max_size=4)),
                     min_size=1, max_size=12))
def test_line_count_matches_the_layout(kit, spec):
    # oracle: requirement 13 — 2 header lines + n case lines + failing item lines (only
    # under a status other than verified/skipped) + blank + total + one per status.
    res = []
    extra = 0
    for n, (status, item_statuses) in enumerate(spec):
        items = [_item(f"rows[{k}]", s, 1) for k, s in enumerate(item_statuses)]
        res.append({"id": f"r{n}", "kind": "cross_filer", "status": status, "items": items,
                    "classes": [], "question": LEAK})
        if status not in ("verified", "skipped"):
            extra += sum(1 for s in item_statuses if s != "verified")
    text = render_hx_report(res)
    n_statuses = len({s for s, _ in spec})
    assert len(text.splitlines()) == 2 + len(spec) + extra + 2 + n_statuses
    assert f"total: {len(spec)}" in text.splitlines()
    assert LEAK not in text
