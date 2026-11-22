"""C2: ``build_pool``.

Oracle: requirement 4 of docs/specs/aprime-draw.md and, for the five P CIKs,
pre-registration v0 section 3 (quoted in requirement 4). Constituents, CIK maps and corpus
sets are synthetic and chosen by hand; each expected candidate, exclusion and count is
worked out by hand from the requirement's rules below.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.core import build_pool


def _c(symbol, sector, name=None):
    return {"symbol": symbol, "name": name or f"Synthetic {symbol}", "sector": sector}


# One constituent per exclusion reason, plus candidates in each stratum.
CONSTITUENTS = [
    _c("FINQ", "Financials"),                    # sector
    _c("REIQ", "Real Estate"),                   # sector
    _c("CORQ", "Industrials"),                   # corpus
    _c("AAPL", "Information Technology"),        # prospective (P CIK)
    _c("GOOQ", "Communication Services"),        # kept: "GOOQ" < "GOOQL"
    _c("GOOQL", "Communication Services"),       # duplicate_cik
    _c("swq.b", "Information Technology"),       # candidate S, symbol normalized to SWQ-B
    _c("ENRQ", "Energy"),                        # candidate D
    _c("HLTQ", "Health Care"),                   # candidate D
]
CIKS = {
    "FINQ": "0000000901",
    "REIQ": "0000000902",
    "CORQ": "0000000777",
    "AAPL": "0000320193",
    "GOOQ": "0000000500",
    "GOOQL": "0000000500",
    "SWQ-B": "0000000300",
    "ENRQ": "0000000200",
    "HLTQ": "0000000100",
}
# corpus given unpadded: "777" must match "0000000777" (requirement 4, CIKs)
CORPUS = {"777"}

EXPECTED = {
    # sorted by cik: 100 < 200 < 300 < 500
    "candidates": [
        {"symbol": "HLTQ", "name": "Synthetic HLTQ", "sector": "Health Care",
         "cik": "0000000100", "stratum": "D"},
        {"symbol": "ENRQ", "name": "Synthetic ENRQ", "sector": "Energy",
         "cik": "0000000200", "stratum": "D"},
        {"symbol": "SWQ-B", "name": "Synthetic swq.b", "sector": "Information Technology",
         "cik": "0000000300", "stratum": "S"},
        {"symbol": "GOOQ", "name": "Synthetic GOOQ", "sector": "Communication Services",
         "cik": "0000000500", "stratum": "S"},
    ],
    # sorted by symbol: AAPL < CORQ < FINQ < GOOQL < REIQ
    "excluded": [
        {"symbol": "AAPL", "cik": "0000320193", "reason": "prospective"},
        {"symbol": "CORQ", "cik": "0000000777", "reason": "corpus"},
        {"symbol": "FINQ", "cik": "0000000901", "reason": "sector"},
        {"symbol": "GOOQL", "cik": "0000000500", "reason": "duplicate_cik"},
        {"symbol": "REIQ", "cik": "0000000902", "reason": "sector"},
    ],
    # 9 constituents = 2 S + 2 D + 5 excluded
    "counts": {"constituents": 9, "S": 2, "D": 2, "excluded": 5},
}


@pytest.mark.criterion("C2")
def test_one_constituent_per_reason_full_result():
    # oracle: requirement 4 — reasons in order sector/corpus/prospective/duplicate_cik;
    # candidates sorted by cik, exclusions by symbol; the full expected result is above
    assert build_pool([dict(c) for c in CONSTITUENTS], dict(CIKS), set(CORPUS)) == EXPECTED


@pytest.mark.criterion("C2")
def test_counts_add_up():
    # oracle: requirement 4 — counts.constituents = n; S + D + excluded covers every
    # constituent exactly once (2 + 2 + 5 = 9)
    pool = build_pool([dict(c) for c in CONSTITUENTS], dict(CIKS), set(CORPUS))
    counts = pool["counts"]
    assert counts["constituents"] == 9
    assert counts["S"] + counts["D"] + counts["excluded"] == counts["constituents"]
    assert counts["S"] == sum(1 for c in pool["candidates"] if c["stratum"] == "S")
    assert counts["D"] == sum(1 for c in pool["candidates"] if c["stratum"] == "D")
    assert counts["excluded"] == len(pool["excluded"])


@pytest.mark.criterion("C2")
def test_precedence_sector_before_corpus_and_corpus_before_prospective(kit):
    # oracle: requirement 4 — the first reason that applies wins: a Financials filer in
    # corpus_ciks is "sector"; a corpus filer that is also a P filer (QCOM's P CIK, v0 §3)
    # is "corpus"; the remaining Energy filer is the only candidate
    cons = [_c("BNKQ", "Financials"), _c("QCOM", "Information Technology"),
            _c("OILQ", "Energy")]
    ciks = {"BNKQ": "0000000911", "QCOM": kit.P_CIKS["QCOM"], "OILQ": "0000000412"}
    corpus = {"0000000911", kit.P_CIKS["QCOM"]}
    pool = build_pool(cons, ciks, corpus)
    assert pool["excluded"] == [
        {"symbol": "BNKQ", "cik": "0000000911", "reason": "sector"},
        {"symbol": "QCOM", "cik": "0000804328", "reason": "corpus"},
    ]
    assert [c["symbol"] for c in pool["candidates"]] == ["OILQ"]
    assert pool["counts"] == {"constituents": 3, "S": 0, "D": 1, "excluded": 2}


@pytest.mark.criterion("C2")
def test_five_p_ciks_each_excluded_as_prospective(kit):
    # oracle: pre-registration v0 §3 via requirement 4 — AAPL 0000320193, QCOM 0000804328,
    # EMR 0000032604, DIS 0001744489, SBUX 0000829224 are each "prospective"; the synthetic
    # Utilities filer stays as the sole D candidate
    sectors = {"AAPL": "Information Technology", "QCOM": "Information Technology",
               "EMR": "Industrials", "DIS": "Communication Services",
               "SBUX": "Consumer Discretionary"}
    cons = [_c(s, sec) for s, sec in sectors.items()] + [_c("UTLQ", "Utilities")]
    ciks = dict(kit.P_CIKS)
    ciks["UTLQ"] = "0000000123"
    pool = build_pool(cons, ciks, set())
    assert pool["excluded"] == [
        {"symbol": "AAPL", "cik": "0000320193", "reason": "prospective"},
        {"symbol": "DIS", "cik": "0001744489", "reason": "prospective"},
        {"symbol": "EMR", "cik": "0000032604", "reason": "prospective"},
        {"symbol": "QCOM", "cik": "0000804328", "reason": "prospective"},
        {"symbol": "SBUX", "cik": "0000829224", "reason": "prospective"},
    ]
    assert pool["candidates"] == [
        {"symbol": "UTLQ", "name": "Synthetic UTLQ", "sector": "Utilities",
         "cik": "0000000123", "stratum": "D"},
    ]


@pytest.mark.criterion("C2")
def test_share_class_pair_keeps_symbol_that_sorts_first():
    # oracle: requirement 4 reason 4 — same CIK, the normalized symbol that sorts first is
    # kept: "HHQ-A" < "HHQ-B" (normalized from "hhq.b"/"HHQ/A"), so HHQ-B is duplicate_cik
    cons = [_c("hhq.b", "Materials"), _c("HHQ/A", "Materials")]
    ciks = {"HHQ-A": "0000000640", "HHQ-B": "0000000640"}
    pool = build_pool(cons, ciks, set())
    assert [c["symbol"] for c in pool["candidates"]] == ["HHQ-A"]
    assert pool["excluded"] == [
        {"symbol": "HHQ-B", "cik": "0000000640", "reason": "duplicate_cik"}]


@pytest.mark.criterion("C2")
def test_strata_assigned_per_sector(kit):
    # oracle: requirement 4 — stratum "S" for Information Technology and Communication
    # Services, "D" for every other sector left (Financials/Real Estate are excluded);
    # nine non-excluded GICS sectors -> 2 S + 7 D
    kept = [s for s in kit.GICS if s not in ("Financials", "Real Estate")]
    cons = [_c(f"SQ{i}", sec) for i, sec in enumerate(kept)]
    ciks = {f"SQ{i}": str(1000 + i).zfill(10) for i in range(len(kept))}
    pool = build_pool(cons, ciks, set())
    got = {c["sector"]: c["stratum"] for c in pool["candidates"]}
    assert got == {
        "Communication Services": "S", "Consumer Discretionary": "D",
        "Consumer Staples": "D", "Energy": "D", "Health Care": "D", "Industrials": "D",
        "Information Technology": "S", "Materials": "D", "Utilities": "D",
    }
    assert pool["counts"] == {"constituents": 9, "S": 2, "D": 7, "excluded": 0}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad_sector", ["Tech", "information technology", "Healthcare", ""])
def test_unknown_sector_raises_naming_symbol(bad_sector):
    # oracle: requirement 4 — a sector not spelled exactly as one of the 11 GICS names
    # raises ValueError naming the symbol
    cons = [_c("OKQ", "Energy"), _c("BADQX", bad_sector)]
    ciks = {"OKQ": "0000000011", "BADQX": "0000000012"}
    with pytest.raises(ValueError) as err:
        build_pool(cons, ciks, set())
    assert "BADQX" in str(err.value)


@pytest.mark.criterion("C2")
def test_missing_ciks_raise_one_error_naming_each():
    # oracle: requirement 4 — one ValueError naming every symbol missing from ciks
    cons = [_c("HAVQ", "Energy"), _c("MISSQA", "Energy"), _c("MISSQB", "Utilities")]
    ciks = {"HAVQ": "0000000021"}
    with pytest.raises(ValueError) as err:
        build_pool(cons, ciks, set())
    assert "MISSQA" in str(err.value)
    assert "MISSQB" in str(err.value)


@pytest.mark.criterion("C2")
def test_unpadded_corpus_cik_still_matches():
    # oracle: requirement 4 — corpus_ciks compared as 10-digit zero-padded strings, so
    # "4242" matches "0000004242"
    cons = [_c("PADQ", "Energy"), _c("KEEPQ", "Energy")]
    ciks = {"PADQ": "0000004242", "KEEPQ": "0000004243"}
    pool = build_pool(cons, ciks, {"4242"})
    assert pool["excluded"] == [{"symbol": "PADQ", "cik": "0000004242", "reason": "corpus"}]
    assert [c["symbol"] for c in pool["candidates"]] == ["KEEPQ"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(perm=st.permutations(list(range(len(CONSTITUENTS)))))
def test_shuffled_constituents_give_identical_result(perm):
    # oracle: requirement 4 — "The result doesn't depend on the order of constituents";
    # every permutation gives the hand-worked EXPECTED result
    shuffled = [dict(CONSTITUENTS[i]) for i in perm]
    assert build_pool(shuffled, dict(CIKS), set(CORPUS)) == EXPECTED
