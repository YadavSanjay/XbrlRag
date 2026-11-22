"""C3: ``build_pool_v2``.

Oracle: requirement 3 of docs/specs/heldout-draw-v2.md and, for the five P CIKs,
pre-registration v0 section 3 (quoted in aprime-draw requirement 4). Constituents, CIK maps,
corpus sets and lists are synthetic and chosen by hand; each expected candidate, exclusion,
warning and count is worked out by hand from the requirement's rules, as commented below.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.v2 import build_pool_v2


def _c(symbol, sector):
    return {"symbol": symbol, "name": f"Synthetic {symbol}", "sector": sector}


CONSTITUENTS = [
    _c("FINQ", "Financials"),                  # sector
    _c("REIQ", "Real Estate"),                 # sector
    _c("UTLQ", "Utilities"),                   # sector (new in v2)
    _c("CORQ", "Industrials"),                 # corpus
    _c("W1Q", "Information Technology"),       # wave1
    _c("W2Q", "Energy"),                       # wave2
    _c("LOQ", "Industrials"),                  # left_out, by symbol
    _c("loq.b", "Industrials"),                # LOQ-B: left_out by CIK (LOQ's CIK)
    _c("GOOQ", "Communication Services"),      # kept: "GOOQ" < "GOOQL"
    _c("GOOQL", "Communication Services"),     # duplicate_cik
    _c("EMR", "Industrials"),                  # a v0 P filer: no prospective rule -> candidate D
    _c("SWQ", "Information Technology"),       # candidate S
    _c("HLTQ", "Health Care"),                 # candidate D
]
CIKS = {
    "FINQ": "0000000901",
    "REIQ": "0000000902",
    "UTLQ": "0000000903",
    "CORQ": "0000000777",
    "W1Q": "0000000611",
    "W2Q": "0000000622",
    "LOQ": "0000000633",
    "LOQ-B": "0000000633",
    "GOOQ": "0000000500",
    "GOOQL": "0000000500",
    "EMR": "0000032604",
    "SWQ": "0000000300",
    "HLTQ": "0000000100",
}
CORPUS = {"777"}  # unpadded on purpose: matches "0000000777"
# MISSQA and ZZQ are not constituents and not in CIKS: warnings, not errors
LISTS = {"wave1": ["W1Q", "MISSQA"], "wave2": ["W2Q"], "left_out": ["LOQ", "ZZQ"]}

EXPECTED = {
    # sorted by cik: 0000000100 < 0000000300 < 0000000500 < 0000032604
    "candidates": [
        {"symbol": "HLTQ", "name": "Synthetic HLTQ", "sector": "Health Care",
         "cik": "0000000100", "stratum": "D"},
        {"symbol": "SWQ", "name": "Synthetic SWQ", "sector": "Information Technology",
         "cik": "0000000300", "stratum": "S"},
        {"symbol": "GOOQ", "name": "Synthetic GOOQ", "sector": "Communication Services",
         "cik": "0000000500", "stratum": "S"},
        {"symbol": "EMR", "name": "Synthetic EMR", "sector": "Industrials",
         "cik": "0000032604", "stratum": "D"},
    ],
    # sorted by symbol: CORQ < FINQ < GOOQL < LOQ < LOQ-B < REIQ < UTLQ < W1Q < W2Q
    "excluded": [
        {"symbol": "CORQ", "cik": "0000000777", "reason": "corpus"},
        {"symbol": "FINQ", "cik": "0000000901", "reason": "sector"},
        {"symbol": "GOOQL", "cik": "0000000500", "reason": "duplicate_cik"},
        {"symbol": "LOQ", "cik": "0000000633", "reason": "left_out"},
        {"symbol": "LOQ-B", "cik": "0000000633", "reason": "left_out"},
        {"symbol": "REIQ", "cik": "0000000902", "reason": "sector"},
        {"symbol": "UTLQ", "cik": "0000000903", "reason": "sector"},
        {"symbol": "W1Q", "cik": "0000000611", "reason": "wave1"},
        {"symbol": "W2Q", "cik": "0000000622", "reason": "wave2"},
    ],
    # sorted by (list, symbol): "left_out" < "wave1"
    "warnings": [
        {"list": "left_out", "symbol": "ZZQ", "reason": "not_in_constituents"},
        {"list": "wave1", "symbol": "MISSQA", "reason": "not_in_constituents"},
    ],
    # 13 constituents = 2 S + 2 D + 9 excluded; 2 warnings
    "counts": {"constituents": 13, "S": 2, "D": 2, "excluded": 9, "warnings": 2},
}


def _lists(lists):
    return {k: list(v) for k, v in lists.items()}


@pytest.mark.criterion("C3")
def test_one_constituent_per_reason_full_result():
    # oracle: requirement 3 — reasons sector/corpus/wave1/wave2/left_out/duplicate_cik, a
    # share class matched by CIK, no prospective rule, warnings for unlisted symbols; the
    # full hand-worked result is EXPECTED above
    got = build_pool_v2([dict(c) for c in CONSTITUENTS], dict(CIKS), set(CORPUS),
                        _lists(LISTS))
    assert got == EXPECTED


@pytest.mark.criterion("C3")
def test_counts_add_up():
    # oracle: requirement 3 — S + D + excluded covers every constituent exactly once
    # (2 + 2 + 9 = 13), and warnings counts the warning records
    pool = build_pool_v2([dict(c) for c in CONSTITUENTS], dict(CIKS), set(CORPUS),
                         _lists(LISTS))
    counts = pool["counts"]
    assert counts["constituents"] == 13
    assert counts["S"] + counts["D"] + counts["excluded"] == counts["constituents"]
    assert counts["excluded"] == len(pool["excluded"])
    assert counts["warnings"] == len(pool["warnings"]) == 2


@pytest.mark.criterion("C3")
def test_precedence(kit):
    # oracle: requirement 3 — first reason wins: a Financials filer listed in wave2 is
    # "sector"; QCOM (P CIK) in corpus and in wave1 is "corpus"; PREQ in wave1 that left_out
    # also matches by CIK (via PREQ-A) is "wave1"; PREQ-A is not a constituent -> a warning
    cons = [_c("BNKQ", "Financials"), _c("QCOM", "Information Technology"),
            _c("PREQ", "Materials"), _c("OILQ", "Energy")]
    ciks = {"BNKQ": "0000000911", "QCOM": kit.P_CIKS["QCOM"], "PREQ": "0000000644",
            "PREQ-A": "0000000644", "OILQ": "0000000412"}
    lists = {"wave1": ["QCOM", "PREQ"], "wave2": ["BNKQ"], "left_out": ["PREQ-A"]}
    pool = build_pool_v2(cons, ciks, {kit.P_CIKS["QCOM"]}, lists)
    assert pool["excluded"] == [
        {"symbol": "BNKQ", "cik": "0000000911", "reason": "sector"},
        {"symbol": "PREQ", "cik": "0000000644", "reason": "wave1"},
        {"symbol": "QCOM", "cik": "0000804328", "reason": "corpus"},
    ]
    assert [c["symbol"] for c in pool["candidates"]] == ["OILQ"]
    assert pool["warnings"] == [
        {"list": "left_out", "symbol": "PREQ-A", "reason": "not_in_constituents"}]
    assert pool["counts"] == {"constituents": 4, "S": 0, "D": 1, "excluded": 3,
                              "warnings": 1}


_UNRELATED = {"wave1": ["AQX"], "wave2": ["BQX"], "left_out": ["CQX"]}


@pytest.mark.criterion("C3")
def test_v0_p_filers_are_ordinary_candidates(kit):
    # oracle: requirement 3 has no prospective rule — the five P filers (v0 section 3),
    # absent from corpus and lists, are all candidates, sorted by cik:
    # EMR 0000032604 < AAPL 0000320193 < QCOM 0000804328 < SBUX 0000829224 < DIS 0001744489
    sectors = {"AAPL": "Information Technology", "QCOM": "Information Technology",
               "EMR": "Industrials", "DIS": "Communication Services",
               "SBUX": "Consumer Discretionary"}
    cons = [_c(s, sec) for s, sec in sectors.items()]
    pool = build_pool_v2(cons, dict(kit.P_CIKS), set(), _lists(_UNRELATED))
    assert [(c["symbol"], c["stratum"]) for c in pool["candidates"]] == [
        ("EMR", "D"), ("AAPL", "S"), ("QCOM", "S"), ("SBUX", "D"), ("DIS", "S")]
    assert pool["excluded"] == []
    assert pool["counts"] == {"constituents": 5, "S": 3, "D": 2, "excluded": 0,
                              "warnings": 3}


@pytest.mark.criterion("C3")
def test_strata_per_sector_with_utilities_excluded(kit):
    # oracle: requirement 3 — one filer per GICS sector: Financials, Real Estate, Utilities
    # are "sector"; of the 8 left, IT and Communication Services are S, the other 6 are D
    cons = [_c(f"SQ{i}", sec) for i, sec in enumerate(kit.GICS)]
    ciks = {f"SQ{i}": str(5000 + i).zfill(10) for i in range(len(kit.GICS))}
    pool = build_pool_v2(cons, ciks, set(), _lists(_UNRELATED))
    got = {c["sector"]: c["stratum"] for c in pool["candidates"]}
    assert got == {
        "Communication Services": "S", "Consumer Discretionary": "D",
        "Consumer Staples": "D", "Energy": "D", "Health Care": "D", "Industrials": "D",
        "Information Technology": "S", "Materials": "D",
    }
    assert sorted(e["symbol"] for e in pool["excluded"]) == ["SQ10", "SQ4", "SQ9"]
    assert {e["reason"] for e in pool["excluded"]} == {"sector"}
    assert pool["counts"] == {"constituents": 11, "S": 2, "D": 6, "excluded": 3,
                              "warnings": 3}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("bad_sector", ["Tech", "utilities", "Healthcare", ""])
def test_unknown_sector_raises_naming_symbol(bad_sector):
    # oracle: requirement 3 (aprime-draw requirement 4) — a sector not spelled as one of the
    # 11 GICS names raises ValueError naming the symbol
    cons = [_c("OKQ", "Energy"), _c("BADQX", bad_sector)]
    ciks = {"OKQ": "0000000011", "BADQX": "0000000012"}
    with pytest.raises(ValueError) as err:
        build_pool_v2(cons, ciks, set(), _lists(_UNRELATED))
    assert "BADQX" in str(err.value)


@pytest.mark.criterion("C3")
def test_missing_ciks_raise_one_error_naming_each():
    # oracle: requirement 3 — one ValueError naming every constituent missing from ciks
    cons = [_c("HAVQ", "Energy"), _c("MISSQA", "Energy"), _c("MISSQB", "Materials")]
    with pytest.raises(ValueError) as err:
        build_pool_v2(cons, {"HAVQ": "0000000021"}, set(), _lists(_UNRELATED))
    assert "MISSQA" in str(err.value)
    assert "MISSQB" in str(err.value)


@pytest.mark.criterion("C3")
def test_unpadded_corpus_cik_still_matches():
    # oracle: requirement 3 — corpus_ciks compared as cik10, so "4242" matches "0000004242"
    cons = [_c("PADQ", "Energy"), _c("KEEPQ", "Energy")]
    ciks = {"PADQ": "0000004242", "KEEPQ": "0000004243"}
    pool = build_pool_v2(cons, ciks, {"4242"}, _lists(_UNRELATED))
    assert pool["excluded"] == [{"symbol": "PADQ", "cik": "0000004242", "reason": "corpus"}]
    assert [c["symbol"] for c in pool["candidates"]] == ["KEEPQ"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(perm=st.permutations(list(range(len(CONSTITUENTS)))))
def test_shuffled_constituents_give_identical_result(perm):
    # oracle: requirement 3 — "The result doesn't depend on the order of constituents";
    # every permutation gives the hand-worked EXPECTED
    shuffled = [dict(CONSTITUENTS[i]) for i in perm]
    assert build_pool_v2(shuffled, dict(CIKS), set(CORPUS), _lists(LISTS)) == EXPECTED
