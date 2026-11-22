"""C4: ``resolve_hx_case`` for cross_filer cases.

Oracle: requirements 9-12 of docs/specs/hx-authoring.md, with set B's agreement and class
rules (set-b-tooling requirements 3 and 6). The expected classes are read off the
hand-built filer-years in ``_store``; the arithmetic is shown in each test.
"""

from __future__ import annotations

import copy
import random
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import resolve_hx_case

M = 1e6
ACC = "0000000001-25-000007"


def _facts(kit):
    """FY2025 candidates (synthetic). DEVA's 300 is tagged twice on one concept/period."""
    c = kit.cand
    return {
        ("DEVA", 2025): [c(1, "Revenues", 300 * M), c(11, "Revenues", 300 * M),
                         c(4, "OperatingIncomeLoss", 77 * M),
                         c(5, "Revenues", 50 * M, member="Cloud"),
                         c(6, "OtherIncome", 50 * M)],
        ("DEVB", 2025): [c(2, "Revenues", 200 * M),
                         c(8, "AccountsPayable", 555 * M), c(9, "AccruedLiabilities", 555 * M)],
        ("DEVC", 2025): [c(3, "Revenues", 100 * M)],
    }


def _filings(kit):
    """DEVA's FY2025 as a later filing reports it (a recast 310)."""
    return {("DEVA", ACC, 2025): [kit.cand(21, "Revenues", 310 * M)]}


def _store(kit):
    return kit.FakeStore(_facts(kit), _filings(kit))


def _items(got):
    return [(i["name"], i["status"], i["classes"]) for i in got["items"]]


@pytest.mark.criterion("C4")
def test_screen_rows_verified_each_reading_its_own_filer(kit):
    # oracle: requirement 10 — row i reads facts_for(row ticker, case fy); DEVA 300 -> facts
    # 1 and 11 (same concept/period: one class); DEVB 200 -> fact 2.
    store = _store(kit)
    got = resolve_hx_case(kit.cross_filer("x1"), store)
    assert got["id"] == "x1"
    assert got["kind"] == "cross_filer"
    assert got["status"] == "verified"
    assert _items(got) == [("rows[0]", "verified", [[1, 11]]), ("rows[1]", "verified", [[2]])]
    assert got["classes"] == [[1, 11], [2]]
    assert Counter(store.calls) == Counter({("facts_for", "DEVA", 2025): 1,
                                            ("facts_for", "DEVB", 2025): 1})


@pytest.mark.criterion("C4")
def test_missing_and_duplicate_amounts(kit):
    # oracle: requirement 10 — DEVB has no 250 -> not_found; 555 is tagged on two concepts
    # -> two classes -> ambiguous; requirement 12: the first bad item gives the status and
    # a non-verified item's class entry is [].
    case = kit.cross_filer("x2")
    case["rows"] = [kit.row("DEVA", "300"), kit.row("DEVB", "250")]
    got = resolve_hx_case(case, _store(kit))
    assert got["status"] == "not_found"
    assert _items(got)[1] == ("rows[1]", "not_found", [])
    assert got["classes"] == [[1, 11], []]

    case["rows"] = [kit.row("DEVB", "555"), kit.row("DEVA", "300")]
    got = resolve_hx_case(case, _store(kit))
    assert got["status"] == "ambiguous"
    assert _items(got)[0] == ("rows[0]", "ambiguous", [[8], [9]])
    assert got["classes"] == [[], [1, 11]]


@pytest.mark.criterion("C4")
def test_sum_median_count_rank_verified_and_off(kit):
    # oracle: requirement 11 — rows DEVA 300, DEVB 200, DEVC 100 (millions)
    store = _store(kit)
    # sum 300 + 200 = 500 agrees; 510: |500 - 510| = 10e6 > max(0.5e6, 0.51e6) -> off
    assert resolve_hx_case(kit.cross_filer("s", "sum"), store)["status"] == "verified"
    off = kit.cross_filer("s", "sum", expected="510")
    assert resolve_hx_case(off, store)["status"] == "arith_mismatch"
    # median of 300, 200, 100 is 200; 250 is off by 50e6 > max(0.5e6, 0.25e6)
    assert resolve_hx_case(kit.cross_filer("m", "median"), store)["status"] == "verified"
    off = kit.cross_filer("m", "median", expected="250")
    assert resolve_hx_case(off, store)["status"] == "arith_mismatch"
    # count of 2 rows is 2; a written 3 is off (exact equality)
    assert resolve_hx_case(kit.cross_filer("c", "count"), store)["status"] == "verified"
    off = kit.cross_filer("c", "count", expected=3)
    assert resolve_hx_case(off, store)["status"] == "arith_mismatch"
    # DEVB 200: one value (300) is greater -> rank 2; a written 1 is off
    assert resolve_hx_case(kit.cross_filer("p", "peer_relative"), store)["status"] == "verified"
    off = kit.cross_filer("p", "peer_relative", expected=1)
    assert resolve_hx_case(off, store)["status"] == "arith_mismatch"


@pytest.mark.criterion("C4")
def test_unresolved_row_status_comes_before_the_arithmetic(kit):
    # oracle: requirement 12 — the first non-verified item status wins over arith_mismatch.
    case = kit.cross_filer("s2", "sum", expected="999")
    case["rows"] = [kit.row("DEVA", "300"), kit.row("DEVB", "201")]
    assert resolve_hx_case(case, _store(kit))["status"] == "not_found"


@pytest.mark.criterion("C4")
def test_a_row_with_an_accession_reads_that_filing(kit):
    # oracle: requirement 10 — with an accession, the candidates are facts_in_filing(DEVA,
    # ACC, 2025): the recast 310 (fact 21), which facts_for does not hold.
    store = _store(kit)
    case = kit.cross_filer("a1")
    case["rows"] = [kit.row("DEVA", "310", accession=ACC), kit.row("DEVB", "200")]
    got = resolve_hx_case(case, store)
    assert got["status"] == "verified"
    assert got["classes"] == [[21], [2]]
    assert ("facts_in_filing", "DEVA", ACC, 2025) in store.calls
    assert ("facts_for", "DEVA", 2025) not in store.calls


@pytest.mark.criterion("C4")
def test_a_member_hint_restricts_to_dimensional_facts_with_that_label(kit):
    # oracle: requirement 10 — member "cloud" -> only dimensional candidates whose label
    # contains it (case-insensitive): fact 5, not the flat 50e6 decoy (fact 6).
    case = kit.cross_filer("d1", member="cloud")
    case["rows"] = [kit.row("DEVA", "50")]
    got = resolve_hx_case(case, _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[5]]
    flat = kit.cross_filer("d2")
    flat["rows"] = [kit.row("DEVA", "50")]
    assert resolve_hx_case(flat, _store(kit))["classes"] == [[6]]


@pytest.mark.criterion("C4")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1),
       shape=st.sampled_from(["screen_top", "sum", "median", "count", "peer_relative"]))
def test_shuffling_candidates_does_not_change_the_result(kit, seed, shape):
    # oracle: requirements 10-12 — classes are sorted sets, so candidate order is irrelevant;
    # the unshuffled result is itself pinned by the point tests above.
    rng = random.Random(seed)
    facts = copy.deepcopy(_facts(kit))
    for cands in facts.values():
        rng.shuffle(cands)
    case = kit.cross_filer("sh", shape)
    base = resolve_hx_case(case, _store(kit))
    assert base["status"] == "verified"
    assert resolve_hx_case(case, kit.FakeStore(facts, _filings(kit))) == base
