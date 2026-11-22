"""C5: ``resolve_hx_case`` for cross_period, compound, text and honesty cases.

Oracle: requirements 9-12 of docs/specs/hx-authoring.md, S1 (as originally reported vs
latest) and S4 (CAGR). The expected classes are read off the hand-built candidates in
``_store``; the arithmetic is shown in each test.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import resolve_hx_case

M = 1e6


def _store(kit):
    c = kit.cand
    facts = {
        ("DEVA", 2025): [c(1, "Revenues", 300 * M), c(11, "Revenues", 300 * M)],
        ("DEVA", 2024): [c(34, "Revenues", 250 * M, fy=2024)],
        ("DEVB", 2025): [c(2, "Revenues", 200 * M)],
        ("DEVE", 2023): [c(31, "Revenues", 100 * M, fy=2023)],
        ("DEVE", 2024): [c(32, "Revenues", 110 * M, fy=2024)],
        ("DEVE", 2025): [c(33, "Revenues", 121 * M)],
    }
    filings = {
        ("DEVE", kit.ACC_ORIG, 2023): [c(41, "Revenues", 100 * M, fy=2023)],
        ("DEVE", kit.ACC_LATEST, 2023): [c(42, "Revenues", 104 * M, fy=2023)],
    }
    return kit.FakeStore(facts, filings)


def _items(got):
    return [(i["name"], i["status"], i["classes"]) for i in got["items"]]


@pytest.mark.criterion("C5")
def test_trend_reads_each_row_year(kit):
    # oracle: requirement 10 — trend row i reads facts_for(DEVE, row fy): 31, 32, 33.
    store = _store(kit)
    got = resolve_hx_case(kit.cross_period("t1", "trend"), store)
    assert got["kind"] == "cross_period"
    assert got["status"] == "verified"
    assert _items(got) == [("rows[0]", "verified", [[31]]), ("rows[1]", "verified", [[32]]),
                           ("rows[2]", "verified", [[33]])]
    assert got["classes"] == [[31], [32], [33]]
    assert Counter(store.calls) == Counter({("facts_for", "DEVE", 2023): 1,
                                            ("facts_for", "DEVE", 2024): 1,
                                            ("facts_for", "DEVE", 2025): 1})


@pytest.mark.criterion("C5")
def test_cagr_verified_and_off(kit):
    # oracle: S4 — (121e6 / 100e6) ** (1 / (2025 - 2023)) - 1 = 0.1 = "10%" (half_unit
    # 0.005); "12%": |0.1 - 0.12| = 0.02 > max(0.005, 1.2e-4) -> arith_mismatch.
    got = resolve_hx_case(kit.cross_period("g1", "cagr"), _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[31], [33]]
    off = kit.cross_period("g2", "cagr", expected="12%")
    assert resolve_hx_case(off, _store(kit))["status"] == "arith_mismatch"


@pytest.mark.criterion("C5")
def test_point_in_time_reads_both_filings_for_the_case_year(kit):
    # oracle: S1 and requirement 10 — the original 100 is in the FY2023 filing (41), the
    # recast 104 in the later one (42); both read through facts_in_filing(DEVE, acc, 2023).
    store = _store(kit)
    got = resolve_hx_case(kit.cross_period("p1", "point_in_time"), store)
    assert got["status"] == "verified"
    assert _items(got) == [("as_originally_reported", "verified", [[41]]),
                           ("latest", "verified", [[42]])]
    assert Counter(store.calls) == Counter({
        ("facts_in_filing", "DEVE", kit.ACC_ORIG, 2023): 1,
        ("facts_in_filing", "DEVE", kit.ACC_LATEST, 2023): 1})


@pytest.mark.criterion("C5")
def test_point_in_time_value_only_in_the_other_filing_is_not_found(kit):
    # oracle: S1 — 104 is the latest value; written as originally reported it is not in
    # the original filing -> not_found on that item.
    case = kit.cross_period("p2", "point_in_time")
    case["as_originally_reported"]["expected"] = "104"
    got = resolve_hx_case(case, _store(kit))
    assert got["status"] == "not_found"
    assert _items(got)[0] == ("as_originally_reported", "not_found", [])
    assert got["classes"] == [[], [42]]


@pytest.mark.criterion("C5")
def test_compound_items_are_prefixed_and_fetched_once(kit):
    # oracle: requirement 10 — ranking DEVA 300 (1, 11), DEVB 200 (2); dependent DEVA trend
    # FY2024 250 (34), FY2025 300 (1, 11). facts_for(DEVA, 2025) serves both parts, once.
    store = _store(kit)
    got = resolve_hx_case(kit.compound("k1"), store)
    assert got["kind"] == "compound"
    assert got["status"] == "verified"
    assert _items(got) == [("ranking.rows[0]", "verified", [[1, 11]]),
                           ("ranking.rows[1]", "verified", [[2]]),
                           ("dependent.rows[0]", "verified", [[34]]),
                           ("dependent.rows[1]", "verified", [[1, 11]])]
    assert got["classes"] == [[1, 11], [2], [34], [1, 11]]
    assert Counter(store.calls)[("facts_for", "DEVA", 2025)] == 1


@pytest.mark.criterion("C5")
def test_compound_ranking_failure_comes_first(kit):
    # oracle: requirement 12 — first non-verified item in item order: ranking.rows[1]
    # (DEVB 250 absent) before dependent.rows[0] (DEVA FY2024 999 absent, also not_found).
    case = kit.compound("k2")
    case["ranking"]["rows"][1]["expected"] = "250"
    case["dependent"]["rows"][0]["expected"] = "999"
    got = resolve_hx_case(case, _store(kit))
    assert got["status"] == "not_found"
    assert [i["name"] for i in got["items"] if i["status"] != "verified"] == [
        "ranking.rows[1]", "dependent.rows[0]"]


@pytest.mark.criterion("C5")
def test_compound_dependent_arithmetic_and_text_dependent(kit):
    # oracle: requirement 11 — dependent sum over DEVA 300 + DEVB 200 = 500; written 600 is
    # off by 100e6 -> arith_mismatch. A text dependent adds no items.
    dep = {"kind": "cross_filer", "subtype": "sum", "fy": 2025, "unit": "USD",
           "universe": ["DEVA", "DEVB"],
           "rows": [kit.row("DEVA", "300"), kit.row("DEVB", "200")],
           "expected": "600", "scale": "millions"}
    got = resolve_hx_case(kit.compound("k3", dependent=dep), _store(kit))
    assert got["status"] == "arith_mismatch"
    text_dep = {"kind": "text", "ticker": "DEVA", "fy": 2025, "reference_answer": "r",
                "key_points": ["k"], "passages": [{"accession": kit.ACC_TEXT, "quote": "q"}]}
    got = resolve_hx_case(kit.compound("k4", dependent=text_dep), _store(kit))
    assert got["status"] == "verified"
    assert [i["name"] for i in got["items"]] == ["ranking.rows[0]", "ranking.rows[1]"]


@pytest.mark.criterion("C5")
def test_text_and_honesty_are_skipped_without_touching_the_store(kit):
    # oracle: requirement 12 — text and honesty: status skipped, no items, no store call.
    for case in (kit.text("tx"), kit.honesty("h1", "ambiguous"),
                 kit.honesty("h2", "false_premise"), kit.honesty("h3", "decline")):
        store = _store(kit)
        got = resolve_hx_case(case, store)
        assert got["id"] == case["id"]
        assert got["kind"] == case["kind"]
        assert got["status"] == "skipped"
        assert got["items"] == []
        assert got["classes"] == []
        assert store.calls == []


_ALPHA = "abcdefghijklmnopqrstuvwxyz"


@pytest.mark.criterion("C5")
@given(secret=st.text(alphabet=_ALPHA, min_size=8, max_size=16),
       pick=st.integers(min_value=0, max_value=3))
def test_results_never_carry_texts_amounts_accessions_or_tickers(kit, secret, pick):
    # oracle: requirement 12 — the result holds ids, kinds, statuses, positional names and
    # fact ids only.
    tick = "TKQZA"
    store = kit.FakeStore({(tick, 2025): [kit.cand(7, "Revenues", 987654321.0)]},
                          {(tick, "0000000009-25-000009", 2024):
                               [kit.cand(8, "Revenues", 123456789.0, fy=2024)]})
    cases = [
        {"id": "z", "question": f"Q-{secret}", "note": f"N-{secret}", "kind": "cross_filer",
         "subtype": "screen_top", "fy": 2025, "unit": "USD", "universe": [tick, "DEVB"],
         "rows": [{"ticker": tick, "expected": "987,654,321"}]},
        {"id": "z", "question": f"Q-{secret}", "kind": "cross_period", "subtype": "trend",
         "ticker": tick, "unit": "USD",
         "rows": [{"fy": 2025, "expected": "987,654,321"},
                  {"fy": 2024, "expected": "123,456,789",
                   "accession": "0000000009-25-000009"}]},
        {"id": "z", "question": f"Q-{secret}", "kind": "text", "ticker": tick, "fy": 2025,
         "reference_answer": f"A-{secret}", "key_points": [f"K-{secret}"],
         "passages": [{"accession": "0000000009-25-000009", "quote": f"P-{secret}"}]},
        {"id": "z", "question": f"Q-{secret}", "kind": "honesty", "subtype": "false_premise",
         "tickers": [tick], "correction": f"C-{secret}"},
    ]
    got = resolve_hx_case(cases[pick], store)
    assert got["id"] == "z"
    assert got["status"] in ("verified", "skipped")
    blob = json.dumps(got)
    texts = [f"{p}-{secret}" for p in ("Q", "N", "A", "K", "P", "C")]
    for leak in (*texts, "987,654,321", "987654321", "123,456,789", "123456789",
                 "0000000009-25-000009", tick):
        assert leak not in blob
