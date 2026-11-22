"""Spec tests for concept-key-integrity: three families hold only their own measure among
their primaries, and the v2 suite inventory never keys from the component band
(docs/specs/concept-key-integrity.md).

Pure: the generated rankings with no taxonomy tree, occurrence counts or overrides, and the
inventory rows through a fake connection; no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.v2_sources import drop_component_rows, read_corpus_inventory
from src.ingest.util.generate_concept_mapping import (
    FAMILY_ANCHOR_QNAMES,
    FAMILY_COMPONENT_QNAMES,
    concept_mapping_rows,
    load_family_overrides,
    reviewed_filer_ranking,
    validate_family_overrides,
)

COMPONENT_BASE = 50  # concept-rank-integrity: the component band starts here
IFRS_BASE = 100  # and the IFRS band, which stays eligible, starts here

G = "us-gaap:"


def _ranking(family, **kwargs):
    """The family's generic ranking from the declared lists alone."""
    overrides = validate_family_overrides({}, FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES)
    rows, _ = concept_mapping_rows({}, {}, overrides, **kwargs)
    return [(r["qname"], r["preference_rank"]) for r in rows
            if r["concept_family"] == family and not r["cik_override"]]


SHARES = [(G + "CommonStockSharesOutstanding", 1), (G + "CommonStockSharesIssued", 50)]
PAYABLES = [(G + "AccountsPayableCurrent", 1), (G + "AccountsPayableTradeCurrent", 2),
            (G + "AccountsPayableAndAccruedLiabilitiesCurrent", 50)]
OTHER_NONOP = [(G + "OtherNonoperatingIncomeExpense", 1), (G + "NonoperatingIncomeExpense", 2),
               (G + "OtherNonoperatingIncome", 3), (G + "OtherNonoperatingExpense", 4)]

# Settings that only change taxonomy descendants and the cap on them; with no tree and no
# counts there are none, so the declared lists alone decide the ranking.
depth_and_cap = st.tuples(st.integers(min_value=0, max_value=3),
                          st.one_of(st.none(), st.integers(min_value=5, max_value=49)))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_shares_outstanding_ranks_only_outstanding_shares_as_primary():
    # oracle: us-gaap definitions — issued shares include treasury shares, outstanding do not
    assert _ranking("SharesOutstanding") == SHARES


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(depth_and_cap)
def test_shares_outstanding_ranking_holds_for_any_depth_and_cap(settings):
    # oracle: the same hand-written ranking; depth and cap touch only descendants (none here)
    max_depth, top_k = settings
    assert _ranking("SharesOutstanding", max_depth=max_depth, top_k=top_k) == SHARES


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_accounts_payable_ranks_the_trade_line_and_not_accrued_liabilities():
    # oracle: us-gaap standard labels — "Accounts Payable, Trade, Current" is accounts
    # payable; "Accounts Payable and Accrued Liabilities, Current" is a broader measure
    assert _ranking("AccountsPayable") == PAYABLES


@pytest.mark.criterion("C2")
@given(st.permutations([G + "AccountsPayableCurrent", G + "AccountsPayableTradeCurrent"]),
       st.integers(min_value=1, max_value=2))
def test_a_filer_ranking_never_promotes_accrued_liabilities(order, k):
    # oracle: concept-rank-integrity requirement 2 — a component is never eligible
    ranking = _ranking("AccountsPayable")
    # Both primaries must be eligible before a filer can prefer them.
    assert set(order) <= {q for q, r in ranking if r < COMPONENT_BASE}
    reviewed = reviewed_filer_ranking(ranking, order[:k])
    assert (G + "AccountsPayableAndAccruedLiabilitiesCurrent", 50) in reviewed
    assert [q for q, _ in reviewed[:k]] == list(order[:k])
    with pytest.raises(ValueError):
        reviewed_filer_ranking(ranking, [G + "AccountsPayableAndAccruedLiabilitiesCurrent"])


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_other_nonoperating_income_ranks_the_other_line_first():
    # oracle: us-gaap standard labels — "Other Nonoperating Income (Expense)" is the family's
    # measure (the v2 lexicon's label); "Nonoperating Income (Expense)" is the total
    assert _ranking("OtherNonOperatingIncome") == OTHER_NONOP


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(depth_and_cap)
def test_other_nonoperating_ranking_holds_for_any_depth_and_cap(settings):
    # oracle: the same hand-written ranking; depth and cap touch only descendants (none here)
    max_depth, top_k = settings
    assert _ranking("OtherNonOperatingIncome", max_depth=max_depth, top_k=top_k) == OTHER_NONOP


# --------------------------------------------------------------------------- C4
US = "http://fasb.org/us-gaap/2024"
IFRS = "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"


def _row(ticker, family, local, rank, ns=US, year=2024, cik="320193", unit="USD"):
    return (ticker, cik, year, family, f"{ns}:{local}", unit, rank)


@pytest.mark.criterion("C4")
def test_drop_component_rows_point_cases():
    # oracle: the band rule by hand — keep rank < 50 and rank >= 100, drop 50..99
    rows = [
        _row("AAPL", "Revenue", "Revenues", 1),
        _row("AMD", "SellingAndMarketing", "MarketingAndAdvertisingExpense", 50),
        _row("AAPL", "Debt", "LongTermDebt", 49),
        _row("ORCL", "Debt", "DebtCurrent", 99),
        _row("TSM", "Revenue", "Revenue", 100, ns=IFRS),
    ]
    assert drop_component_rows(rows) == [
        ("AAPL", "320193", 2024, "Revenue", f"{US}:Revenues", "USD"),
        ("AAPL", "320193", 2024, "Debt", f"{US}:LongTermDebt", "USD"),
        ("TSM", "320193", 2024, "Revenue", f"{IFRS}:Revenue", "USD"),
    ]
    assert drop_component_rows([]) == []
    assert drop_component_rows([_row("AMD", "Debt", "DebtCurrent", 50)]) == []


ranks = st.integers(min_value=1, max_value=150)
row_lists = st.lists(st.tuples(st.sampled_from(["AAPL", "AMD", "TSM"]),
                               st.sampled_from(["Revenue", "Debt", "AccountsPayable"]),
                               st.integers(min_value=2020, max_value=2026), ranks),
                     max_size=12)


@pytest.mark.criterion("C4")
@given(row_lists)
def test_drop_component_rows_keeps_exactly_the_eligible_rows(spec):
    # oracle: the band rule written out by hand on generated rows
    rows = [_row(t, f, "Revenues", r, year=y) for t, f, y, r in spec]
    expected = [row[:6] for row in rows if row[6] < COMPONENT_BASE or row[6] >= IFRS_BASE]
    assert drop_component_rows(rows) == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(row_lists, st.lists(st.integers(min_value=COMPONENT_BASE, max_value=IFRS_BASE - 1),
                           min_size=1, max_size=4))
def test_adding_component_rows_changes_nothing(spec, band):
    # oracle: metamorphic — component-band rows are invisible to the result; one primary row
    # is always present, so the result is never trivially empty
    rows = [_row("AAPL", "Revenue", "Revenues", 1)]
    rows += [_row(t, f, "Revenues", r, year=y) for t, f, y, r in spec]
    extra = [_row("AMD", "Debt", "DebtCurrent", r) for r in band]
    base = drop_component_rows(rows)
    assert base[0] == ("AAPL", "320193", 2024, "Revenue", f"{US}:Revenues", "USD")
    assert drop_component_rows(rows + extra) == base
    assert drop_component_rows(extra + rows) == base


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cursor(self._rows)


@pytest.mark.criterion("C4")
def test_the_inventory_has_no_metric_keyed_from_a_component():
    # oracle: the band rule by hand — AMD's S&M (advertising, 50) and Oracle's Debt
    # (DebtCurrent, 50) are left out; AAPL revenue and debt stay
    rows = [
        _row("AAPL", "Revenue", "Revenues", 1),
        _row("AAPL", "Debt", "LongTermDebtNoncurrent", 2),
        _row("AMD", "SellingAndMarketing", "MarketingAndAdvertisingExpense", 50, cik="2488"),
        _row("ORCL", "Debt", "DebtCurrent", 50, cik="1341439"),
    ]
    try:
        inventory = read_corpus_inventory(_Conn(rows))
    except ValueError:  # a seven-column row the reader cannot take fails this criterion
        inventory = None
    assert inventory == {
        "AAPL": {"cik": "0000320193", "years": {2024: {
            "Revenue": {"concept": "us-gaap:Revenues", "unit": "USD"},
            "Debt": {"concept": "us-gaap:LongTermDebtNoncurrent", "unit": "USD"},
        }}},
    }


# --------------------------------------------------------------------------- C5
VST, CEG, MCD = "0001692819", "0001868275", "0000063908"
FILER_ENTRIES = {
    # oracle: the filers' 10-K figures (spec Notes) — Vistra and Constellation report
    # "Operating revenues" as us-gaap:Revenues, above contract revenue; McDonald's cash-flow
    # D&A add-back is us-gaap:DepreciationAndAmortization, not the smaller DDA amount
    (VST, "Revenue"): G + "Revenues",
    (CEG, "Revenue"): G + "Revenues",
    (MCD, "DepreciationAndAmortization"): G + "DepreciationAndAmortization",
}


def _filer_rows(rows, cik, family):
    return [(r["qname"], r["preference_rank"]) for r in rows
            if r["cik_override"] == cik and r["concept_family"] == family]


@pytest.mark.criterion("C5")
def test_the_committed_file_holds_the_three_filer_entries():
    # oracle: FILER_ENTRIES above (requirement 6)
    filers = load_family_overrides()["filers"]
    for key, preferred in FILER_ENTRIES.items():
        assert filers.get(key) == [preferred]


@pytest.mark.criterion("C5")
def test_each_filer_ranks_its_preferred_concept_first():
    # oracle: concept-families-wave1 requirement 4 by hand — the preferred QName at 1, the
    # other eligible QNames after it in global order, components at their global ranks
    rows, _ = concept_mapping_rows({}, {}, load_family_overrides())
    revenue = _filer_rows(rows, VST, "Revenue")
    assert revenue[:2] == [(G + "Revenues", 1),
                           (G + "RevenueFromContractWithCustomerExcludingAssessedTax", 2)]
    assert _filer_rows(rows, CEG, "Revenue") == revenue
    # concept-family-coverage D4 removes `us-gaap:DepreciationAmortizationAndOther` (it does
    # not exist; that spec's C1 checks it is gone) and adds substitutes from rank 50, so only
    # the reviewed order and the component ranks are pinned here.
    mcd = _filer_rows(rows, MCD, "DepreciationAndAmortization")
    assert mcd[:2] == [(G + "DepreciationAndAmortization", 1),
                       (G + "DepreciationDepletionAndAmortization", 2)]
    assert (G + "Depreciation", 50) in mcd
    assert (G + "AmortizationOfIntangibleAssets", 51) in mcd


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_filer_entries_leave_the_generic_rankings_alone():
    # oracle: metamorphic — dropping the committed file's filer entries changes no generic row
    committed = load_family_overrides()
    assert set(FILER_ENTRIES) <= set(committed["filers"])
    without = {"families": committed["families"], "filers": {}}
    generic = [r for r in concept_mapping_rows({}, {}, committed)[0] if not r["cik_override"]]
    assert generic == concept_mapping_rows({}, {}, without)[0]
