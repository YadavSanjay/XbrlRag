"""C6: ``select_hand_checks`` and ``summarize``.

Oracle: requirements 8 and 9 of docs/specs/sec-companyfacts-check.md. The selection oracle
is the requirement's own recipe, ``random.Random(f"{seed}:{origin}").sample(sorted ids, k)``,
computed in the test; summary counts are tallied by hand from hand-built ``CaseCheck``s.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.core import select_hand_checks, summarize

SEED = 20261021
CAPS = {"dimensional": 6, "breakdown": 2}  # requirement 8


def _case(kind, origin, i):
    tag = origin.split("#")[1]
    case = {
        "id": f"aq-{tag}-{kind}:fy=2024-member=M{i:02d}-ticker=AAPL",
        "question": f"Synthetic {kind} question {i}.",
        "kind": kind,
        "suite": "sql_derived_v1",
        "ticker": "AAPL",
        "fy": 2024,
        "concept": "Revenue",
        "expected_fact_ids": [1000 + i],
        "origin": origin,
        "rel_tol": 0.0001,
    }
    if kind in ("breakdown", "screen"):
        case["expected_rows"] = [{"entity": "X", "value": float(i)}]
    else:
        case["expected_value"] = float(i)
    return case


def _pool(sizes):
    """``sizes``: ``{(kind, origin): n}`` → n cases per origin; listed in a scrambled order."""
    cases = [_case(kind, origin, i) for (kind, origin), n in sizes.items() for i in range(n)]
    return cases[::-1]


def _expected_ids(cases, seed):
    """Requirement 8, computed directly from its recipe."""
    by_origin: dict[str, list[str]] = {}
    kind_of: dict[str, str] = {}
    for c in cases:
        if c["kind"] in CAPS:
            by_origin.setdefault(c["origin"], []).append(c["id"])
            kind_of[c["origin"]] = c["kind"]
    out: list[str] = []
    for origin, ids in by_origin.items():
        k = min(CAPS[kind_of[origin]], len(ids))
        out += random.Random(f"{seed}:{origin}").sample(sorted(ids), k)
    return sorted(out)


# origins of 10, 3, 1 and 0 cases (the 0-case origin simply has no case), plus non-pool kinds
_SIZES = {
    ("dimensional", "analyst_queries.sql#3d"): 10,
    ("breakdown", "analyst_queries.sql#3a"): 3,
    ("dimensional", "analyst_queries.sql#8b"): 1,
    ("breakdown", "analyst_queries.sql#4a"): 0,
    ("lookup", "analyst_queries.sql#1a"): 10,
    ("compute", "analyst_queries.sql#2c"): 4,
    ("screen", "analyst_queries.sql#1b"): 3,
}


@pytest.mark.criterion("C6")
def test_per_origin_counts(kit):
    # oracle: requirement 8 — min(6, 10) = 6 from #3d, min(2, 3) = 2 from #3a, min(6, 1) = 1
    # from #8b, nothing from the empty #4a or from lookup/compute/screen origins
    got = select_hand_checks(_pool(_SIZES), SEED)
    counts = Counter(c["origin"] for c in got)
    assert counts == Counter({"analyst_queries.sql#3d": 6, "analyst_queries.sql#3a": 2,
                              "analyst_queries.sql#8b": 1})


@pytest.mark.criterion("C6")
def test_selection_follows_the_seeded_recipe(kit):
    # oracle: requirement 8 — Random(f"{seed}:{origin}").sample(sorted ids, k) per origin,
    # computed here; the result is sorted by id and is the case dicts themselves
    pool = _pool(_SIZES)
    got = select_hand_checks(pool, SEED)
    expected = _expected_ids(pool, SEED)
    assert len(expected) == 9
    assert [c["id"] for c in got] == expected
    by_id = {c["id"]: c for c in pool}
    assert got == [by_id[i] for i in expected]


@pytest.mark.criterion("C6")
def test_output_is_sorted_and_deterministic(kit):
    # oracle: requirement 8 — sorted by case id; the same inputs always give the same list,
    # whatever order the cases arrive in; another seed follows the same recipe
    pool = _pool(_SIZES)
    first = select_hand_checks(pool, SEED)
    ids = [c["id"] for c in first]
    assert ids == sorted(ids)
    assert len(ids) == 9
    assert [c["id"] for c in select_hand_checks(list(reversed(pool)), SEED)] == ids
    assert [c["id"] for c in select_hand_checks(pool, 7)] == _expected_ids(pool, 7)


def _cc(case_id, kind, status, facts, reason=None):
    return {"case_id": case_id, "kind": kind, "status": status, "reason": reason,
            "facts": [{"fact_id": i, "status": s, "sec_value": None, "sign_flip": False,
                       "restatements": []} for i, s in facts],
            "items": []}


@pytest.mark.criterion("C6")
def test_summarize_counts_rates_and_distinct_facts(kit):
    # oracle: requirement 9, hand tally of the six CaseChecks below —
    # lookup: match 2, mismatch 1, unverifiable 1 → checked 3, match_rate 2/3;
    # compute: unverifiable 1, template_unknown 1 → checked 0, match_rate None;
    # dimensional: hand_check 1 → checked 0;
    # all: match 2, mismatch 1, unverifiable 2, template_unknown 1, hand_check 1 → 2/3;
    # facts: 1 appears in two cases but counts once → match 2 (1, 2), value_mismatch 1 (3),
    # concept_absent 1 (4)
    checks = [
        _cc("a", "lookup", "match", [(1, "match")]),
        _cc("b", "lookup", "match", [(2, "match")]),
        _cc("c", "lookup", "mismatch", [(3, "value_mismatch")]),
        _cc("d", "lookup", "unverifiable", [(4, "concept_absent")], "concept_absent"),
        _cc("e", "compute", "unverifiable", [(1, "match")], "no_value"),
        _cc("f", "compute", "template_unknown", []),
        _cc("g", "dimensional", "hand_check", [], "dimensional"),
    ]
    s = summarize(checks)
    lk, cp, dm, al = s["lookup"], s["compute"], s["dimensional"], s["all"]
    assert (lk.get("match", 0), lk.get("mismatch", 0), lk.get("unverifiable", 0)) == (2, 1, 1)
    assert lk["checked"] == 3
    # tolerance: an exact rational 2/3; rel=1e-12 only absorbs the division
    assert lk["match_rate"] == pytest.approx(2 / 3, rel=1e-12)
    assert (cp.get("unverifiable", 0), cp.get("template_unknown", 0)) == (1, 1)
    assert cp["checked"] == 0
    assert cp["match_rate"] is None
    assert dm.get("hand_check", 0) == 1
    assert dm["match_rate"] is None
    assert (al.get("match", 0), al.get("mismatch", 0), al.get("unverifiable", 0),
            al.get("template_unknown", 0), al.get("hand_check", 0)) == (2, 1, 2, 1, 1)
    assert al["checked"] == 3
    assert al["match_rate"] == pytest.approx(2 / 3, rel=1e-12)
    fsc = s["fact_status_counts"]
    assert {k: v for k, v in fsc.items() if v} == {"match": 2, "value_mismatch": 1,
                                                    "concept_absent": 1}


_SIZE = st.integers(min_value=0, max_value=12)


@pytest.mark.criterion("C6")
@given(d1=_SIZE, d2=_SIZE, b1=_SIZE, b2=_SIZE, other=_SIZE,
       seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_selection_is_a_capped_duplicate_free_subset(kit, d1, d2, b1, b2, other, seed):
    # oracle: requirement 8 — a subset of the dimensional/breakdown pool, no duplicates,
    # min(6, n) / min(2, n) per origin, sorted, and equal to the seeded recipe
    sizes = {
        ("dimensional", "analyst_queries.sql#3d"): d1,
        ("dimensional", "analyst_queries.sql#8b"): d2,
        ("breakdown", "analyst_queries.sql#3a"): b1,
        ("breakdown", "analyst_queries.sql#4a"): b2,
        ("lookup", "analyst_queries.sql#1a"): other,
    }
    pool = _pool(sizes)
    got = [c["id"] for c in select_hand_checks(pool, seed)]
    allowed = {c["id"] for c in pool if c["kind"] in CAPS}
    assert set(got) <= allowed
    assert len(got) == len(set(got))
    assert got == sorted(got)
    counts = Counter(i.split(":")[0] for i in got)
    assert counts["aq-3d-dimensional"] == min(6, d1)
    assert counts["aq-8b-dimensional"] == min(6, d2)
    assert counts["aq-3a-breakdown"] == min(2, b1)
    assert counts["aq-4a-breakdown"] == min(2, b2)
    assert got == _expected_ids(pool, seed)


_STATUSES = ["match", "mismatch", "unverifiable", "template_unknown", "hand_check"]


@pytest.mark.criterion("C6")
@given(st.lists(st.tuples(st.sampled_from(["lookup", "compute", "screen"]),
                          st.sampled_from(_STATUSES)), min_size=1, max_size=30))
def test_summary_rate_is_match_over_checked(kit, rows):
    # oracle: requirement 9 — per kind and overall, checked = match + mismatch and
    # match_rate = match / checked (None when checked is 0); counts tallied directly
    checks = [_cc(f"c{i}", kind, status, []) for i, (kind, status) in enumerate(rows)]
    s = summarize(checks)
    for kind in {k for k, _ in rows} | {"all"}:
        tally = Counter(st_ for k, st_ in rows if kind in ("all", k))
        got = s[kind]
        for status in _STATUSES:
            assert got.get(status, 0) == tally[status]
        checked = tally["match"] + tally["mismatch"]
        assert got["checked"] == checked
        if checked == 0:
            assert got["match_rate"] is None
        else:
            # tolerance: an exact rational; rel=1e-12 only absorbs the division
            assert got["match_rate"] == pytest.approx(tally["match"] / checked, rel=1e-12)
