"""C1: a flat screen picks each filer's fact by the lookup's candidate order.

docs/specs/screen-candidate-order.md, requirement 1. ``select_preferred_concept`` takes the
flat screen query's candidate rows and keeps, per filer, the row ``candidate_order`` puts
first among the rows of the filer's latest fiscal year; a filer whose first answerable row
is a stated substitute, or that has none, is listed apart. Pure: no database.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sco_support import G, store_rows

from src.retriever.xbrl.family_resolution import candidate_order
from src.retriever.xbrl.xbrl_factstore_screen import select_preferred_concept

ONI = "OtherNonOperatingIncome"


def _pick(rows: list[dict]) -> dict:
    kept, apart = select_preferred_concept(rows)
    assert apart == []
    assert len(kept) == 1
    return kept[0]


# --------------------------------------------------------------------------- points
@pytest.mark.criterion("C1")
def test_intel_and_qualcomm_rank_on_their_presented_non_operating_line():
    # oracle: FSDS pre/num of INTC 0000050863-26-000011 (IS "Interest and other, net",
    # NonoperatingIncomeExpense 3,257M) and QCOM 0000804328-25-000085 (IS "Investment and
    # other income, net" 972M); companyfacts: OtherNonoperatingIncomeExpense -416M and 21M,
    # in the notes only. D13: the presented line beats the notes-only rank-1 element.
    intc = _pick(store_rows("INTC", 2025, ONI))
    qcom = _pick(store_rows("QCOM", 2025, ONI))
    assert intc["concept"] == G + "NonoperatingIncomeExpense"
    assert intc["value"] == pytest.approx(3_257e6, abs=0.5)  # exact integers from SEC
    assert qcom["concept"] == G + "NonoperatingIncomeExpense"
    assert qcom["value"] == pytest.approx(972e6, abs=0.5)  # exact integers from SEC


@pytest.mark.criterion("C1")
def test_semiconductor_median_matches_the_presented_lines():
    # oracle: hand median of the seven FY2025 presented lines (FSDS IS lines): INTC 3,257,
    # NVDA 1,034, QCOM 972, AMD 577, AVGO 455, TXN 230, MU -135 ($M) -> 577M; the frozen
    # key of int-b4de130cc08b is 577M, and today's rank-only screen gives 230M.
    rows = [r for t in ("INTC", "NVDA", "QCOM", "AMD", "AVGO", "TXN", "MU")
            for r in store_rows(t, 2025, ONI)]
    kept, apart = select_preferred_concept(rows)
    assert apart == []
    assert [r["ticker"] for r in kept] == ["AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TXN"]
    assert statistics.median(r["value"] for r in kept) == pytest.approx(577e6, abs=0.5)  # exact


def _row(ticker, fy, fact_id, value, concept, rank, *, role="primary", presented=False,
         decimals=None, period_end="2025-02-01"):
    return {"ticker": ticker, "cik": "x", "fiscal_year": fy, "period_end": period_end,
            "concept": G + concept, "concept_pref_rank": rank, "concept_role": role,
            "presented": presented, "decimals": decimals, "fact_id": fact_id,
            "value": value, "unit": "USD"}


@pytest.mark.criterion("C1")
def test_exact_fact_beats_its_rounded_duplicate():
    # oracle: candidate_order rule 6 (finer decimals first; R2 of concept-family-coverage).
    # Walmart's FY2025 balance sheet: cash and cash equivalents 9,037M (decimals -6); the same
    # concept is tagged again as 9.0 billion (decimals -8) with a larger fact id. Today's
    # screen ranks the rounded 9,000M (the dev slice's 37M gap after PG).
    rows = [_row("WMT", 2025, 74001, 9_037e6, "CashAndCashEquivalentsAtCarryingValue", 1,
                 presented=True, decimals=-6),
            _row("WMT", 2025, 74990, 9_000e6, "CashAndCashEquivalentsAtCarryingValue", 1,
                 presented=True, decimals=-8)]
    pick = _pick(rows)
    assert pick["fact_id"] == 74001
    assert pick["value"] == pytest.approx(9_037e6, abs=0.5)  # exact integers


@pytest.mark.criterion("C1")
def test_substitute_first_and_component_only_filers_are_listed_apart():
    # oracle: requirement 1 by hand. TSLA FY2025 D&A: Depreciation (component, rank 50) and
    # its stated substitute DepreciationAmortizationAndImpairment (rank 54, presented): the
    # substitute is first, so TSLA is not ranked and its concept is the substitute's.
    # GOOGL FY2024: Depreciation only (component). AAPL: a presented primary ranks.
    rows = [_row("TSLA", 2025, 11, 5_368e6, "Depreciation", 50, role="component"),
            _row("TSLA", 2025, 12, 6_148e6, "DepreciationAmortizationAndImpairment", 54,
                 role="substitute", presented=True),
            _row("GOOGL", 2024, 21, 15_311e6, "Depreciation", 50, role="component"),
            _row("AAPL", 2025, 31, 11_698e6, "DepreciationDepletionAndAmortization", 1,
                 presented=True)]
    kept, apart = select_preferred_concept(rows)
    assert [r["fact_id"] for r in kept] == [31]
    assert apart == [
        {"ticker": "GOOGL", "fiscal_year": 2024, "concept": G + "Depreciation"},
        {"ticker": "TSLA", "fiscal_year": 2025,
         "concept": G + "DepreciationAmortizationAndImpairment"},
    ]


# --------------------------------------------------------------------------- properties
_ROLES = st.sampled_from(["primary", "primary", "substitute", "component", None])
_RANKS = st.one_of(st.none(), st.integers(1, 6), st.integers(50, 56), st.integers(100, 102))
_ENDS = st.sampled_from(["2024-12-31", "2025-06-30", "2025-12-31"])


@st.composite
def _candidate_rows(draw):
    rows = []
    n = draw(st.integers(1, 12))
    ids = draw(st.lists(st.integers(1, 10_000), min_size=n, max_size=n, unique=True))
    for fact_id in ids:
        rank = draw(_RANKS)
        role = draw(_ROLES)
        if role is None and rank is not None and 50 <= rank < 100:
            role = None  # the role the rank implies: component
        rows.append({
            "ticker": draw(st.sampled_from(["AAA", "BBB", "CCC"])),
            "cik": "x", "fiscal_year": draw(st.sampled_from([2024, 2025])),
            "period_end": draw(_ENDS), "concept": G + f"C{draw(st.integers(0, 5))}",
            "concept_pref_rank": rank, "concept_role": role,
            "presented": draw(st.booleans()),
            "decimals": draw(st.sampled_from([None, -6, -8])),
            "value": draw(st.sampled_from([None, 1.0, 2.0, -3.0])),
            "fact_id": fact_id, "unit": "USD",
        })
    return rows


def _role(row):
    if row.get("concept_role"):
        return row["concept_role"]
    rank = row.get("concept_pref_rank")
    return "component" if rank is not None and 50 <= rank < 100 else "primary"


@pytest.mark.criterion("C1")
@given(_candidate_rows(), st.randoms(use_true_random=False))
def test_screen_choice_is_the_lookup_choice_for_any_candidates(rows, rnd):
    # oracle: the definition (requirement 1) — per ticker, the rows of its latest fiscal year,
    # ordered by candidate_order; a primary first row is kept, else the filer is listed apart
    # with the first answerable row's concept, or (none answerable) the lowest-ranked row's,
    # higher fact id on a tie. Row order never matters.
    kept, apart = select_preferred_concept(rows)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert select_preferred_concept(shuffled) == (kept, apart)

    want_kept, want_apart = [], []
    for t in sorted({r["ticker"] for r in rows}):
        mine = [r for r in rows if r["ticker"] == t]
        year = max(r["fiscal_year"] for r in mine)
        own = [r for r in mine if r["fiscal_year"] == year]
        order = candidate_order(own)
        if order and _role(own[order[0]]) == "primary":
            want_kept.append(own[order[0]])
            continue
        if order:
            first = own[order[0]]
        else:
            first = min(own, key=lambda r: ((1, 0) if r["concept_pref_rank"] is None
                                            else (0, r["concept_pref_rank"]), -r["fact_id"]))
        want_apart.append({"ticker": t, "fiscal_year": year, "concept": first["concept"]})
    assert [id(r) for r in kept] == [id(r) for r in want_kept]
    assert apart == want_apart
