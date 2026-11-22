"""C4: no stale comparative (docs/specs/restatement-s1.md, requirements 1, 3 and 7).

When the requested year's own column has no fact for the concept, neither the product nor
the key may present a prior-year comparative of the same filing as that year. The date
oracle is each filing's DEI DocumentPeriodEndDate from its iXBRL instance (exact day); the
companyfacts and FSDS excerpts hold no entry at that end for the stale cases.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, fiscal_year_key
from src.retriever.xbrl.restatement import current_column, latest_restatement

MILLION = 5e5  # amounts tagged in millions (decimals -6)

# ticker, tag, unit, family, fiscal year, the filing labelled that year, the stale value served
STALE = [
    ("INTC", "CommonStockDividendsPerShareDeclared", "USD/shares", "DividendsPerShare", 2025,
     "0000050863-26-000011", 0.38),
    ("COST", "Goodwill", "USD", "Goodwill", 2025, "0000909832-25-000101", 994e6),
    ("KO", "BusinessExitCosts1", "USD", "RestructuringCharges", 2025, "0001628280-26-010047",
     32e6),
    ("QCOM", "RestructuringAndRelatedCostIncurredCost", "USD", "RestructuringCharges", 2024,
     "0000804328-24-000075", 712e6),
]
# The same kind of ask where the year's own column exists: ticker, tag, unit, family, fiscal
# year, its filing, value (companyfacts = FSDS), DEI period end
CONTROL = [
    ("INTC", "NetCashProvidedByUsedInFinancingActivities", "USD", "CashFromFinancing", 2025,
     "0000050863-26-000011", 11_587e6, "2025-12-27"),
    ("COST", "Goodwill", "USD", "Goodwill", 2026, "0000909832-26-000093", 994e6, "2026-08-30"),
]
# Filer-concept-years whose own column exists, for the removal property.
OWN = [
    ("INTC", "NetCashProvidedByUsedInFinancingActivities", "USD", 2023),
    ("INTC", "NetCashProvidedByUsedInFinancingActivities", "USD", 2025),
    ("NVDA", "EarningsPerShareDiluted", "USD/shares", 2024),
    ("WMT", "EarningsPerShareDiluted", "USD/shares", 2025),
    ("AMD", "AccountsReceivableNetCurrent", "USD", 2024),
    ("HD", "DepreciationDepletionAndAmortization", "USD", 2024),
    ("COST", "Goodwill", "USD", 2024),
    ("QCOM", "RestructuringAndRelatedCostIncurredCost", "USD", 2023),
]
GRID = [(t, tag, fy) for t, tag, _u, fy in OWN] + [(s[0], s[1], s[4]) for s in STALE] + [
    ("AVGO", "EarningsPerShareDiluted", fy) for fy in (2022, 2023, 2024, 2025)
] + [("KO", "BusinessExitCosts1", fy) for fy in (2023, 2024, 2025)]


def _own_filing(kit, ticker: str, fy: int) -> str:
    """The corpus 10-K labelled ``fy`` (DEI), from dei.json."""
    found = [a for a, d in kit.DEI.items()
             if d["ticker"] == ticker and d["form"] == "10-K" and int(d["fiscal_year_focus"]) == fy]
    assert len(found) == 1, (ticker, fy, found)
    return found[0]


def _key(kit, ticker, tag, unit, fy):
    try:
        return fiscal_year_key(kit.companyfacts(ticker), f"us-gaap:{tag}", unit, fy)
    except KeyNotFoundError:
        return None


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C4")
def test_an_empty_year_has_no_own_column_and_a_full_one_does(kit):
    # oracle: DEI DocumentPeriodEndDate of each filing (exact day) and companyfacts — the stale
    # filings tag the concept only at earlier ends; the controls end on the DEI date
    for ticker, tag, _unit, _family, fy, _filing, _stale in STALE:
        assert current_column(kit.store_rows(ticker, [tag]), fy) == [], (ticker, tag, fy)
    for ticker, tag, _unit, _family, fy, filing, value, end in CONTROL:
        own = current_column(kit.store_rows(ticker, [tag]), fy)
        assert kit.accessions(own) == {filing}
        assert {kit.iso(r["period_end"]) for r in own} == {kit.next_day(end)}
        assert kit.DEI[filing]["document_period_end"] == end
        assert [v for v in kit.values(own)] == [pytest.approx(value, abs=MILLION)]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("stale", STALE, ids=[f"{s[0]}-FY{s[4]}" for s in STALE])
def test_the_lookup_refuses_an_empty_year(kit, stale):
    # oracle: report G1 — the value served today (e.g. INTC $0.38) is FY2024's comparative in
    # the FY2025 10-K; the year itself reports none, so NOT_FOUND
    ticker, _tag, _unit, family, fy, _filing, served = stale
    result = kit.run_lookup(kit.store_rows(ticker), ticker, fy, family)
    assert result.status.value == "NOT_FOUND", (result.value, result.resolution_notes)
    assert result.value is None
    assert any(str(fy) in note for note in result.resolution_notes)
    assert result.value != pytest.approx(served, rel=1e-9)


@pytest.mark.criterion("C4")
def test_the_key_refuses_an_empty_year_and_keys_a_full_one_on_its_end(kit):
    # oracle: DEI period end (exact day) — fiscal_year_key keys only the labelled filing's own
    # end; the controls' key period ends on the DEI date with the companyfacts value
    for ticker, tag, unit, _family, fy, _filing, _stale in STALE:
        assert _key(kit, ticker, tag, unit, fy) is None, (ticker, tag, fy)
    for ticker, tag, unit, _family, fy, filing, value, end in CONTROL:
        key = _key(kit, ticker, tag, unit, fy)
        assert key is not None
        assert key["period_end"] == end == kit.DEI[filing]["document_period_end"]
        assert key["value"] == pytest.approx(value, abs=MILLION)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("control", CONTROL, ids=[f"{c[0]}-FY{c[4]}" for c in CONTROL])
def test_the_lookup_answers_a_full_year_and_refuses_its_empty_neighbour(kit, control):
    # oracle: companyfacts = FSDS value at the DEI end for the control; the paired stale ask
    # of the same filer refuses (requirement 3)
    ticker, _tag, _unit, family, fy, filing, value, _end = control
    rows = kit.store_rows(ticker)
    full = kit.run_lookup(rows, ticker, fy, family)
    assert full.status.value == "OK", full.resolution_notes
    assert full.value == pytest.approx(value, abs=MILLION)
    assert full.citation.accession_no == filing
    stale = next(s for s in STALE if s[0] == ticker)
    empty = kit.run_lookup(rows, ticker, stale[4], stale[3])
    assert empty.status.value == "NOT_FOUND", (empty.value, empty.resolution_notes)


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C4")
@given(item=st.sampled_from(GRID))
def test_every_own_column_row_ends_exactly_on_the_dei_date(kit, item):
    # oracle: DEI DocumentPeriodEndDate (exact day) and companyfacts — the own column is
    # non-empty exactly when the labelled 10-K has an entry ending on that date, and then every
    # row ends there (stored as the next midnight)
    ticker, tag, fy = item
    filing = _own_filing(kit, ticker, fy)
    end = kit.DEI[filing]["document_period_end"]
    tagged = any(e["accn"] == filing and e["end"] == end and e["val"] is not None
                 for es in kit.COMPANYFACTS[ticker]["facts"]["us-gaap"][tag]["units"].values()
                 for e in es)
    own = current_column(kit.store_rows(ticker, [tag]), fy)
    assert bool(own) == tagged
    assert {kit.iso(r["period_end"]) for r in own} == ({kit.next_day(end)} if tagged else set())
    assert kit.accessions(own) == ({filing} if tagged else set())


@pytest.mark.criterion("C4")
@given(item=st.sampled_from(OWN), data=st.data())
def test_removing_the_own_column_makes_product_and_key_refuse(kit, item, data):
    # oracle: metamorphic — take away the year's own column (store rows and companyfacts
    # entries of the labelled filing at its DEI end): neither falls back to a comparative
    ticker, tag, unit, fy = item
    filing = _own_filing(kit, ticker, fy)
    end = kit.DEI[filing]["document_period_end"]
    rows = [r for r in kit.store_rows(ticker, [tag])
            if not (r["accession_no"] == filing and kit.iso(r["period_end"]) == kit.next_day(end))]
    rows = data.draw(st.permutations(rows))
    assert latest_restatement(current_column(rows, fy), rows) == []
    doc = kit.companyfacts(ticker)
    for es in doc["facts"]["us-gaap"][tag]["units"].values():
        es[:] = [e for e in es if not (e["accn"] == filing and e["end"] == end)]
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, f"us-gaap:{tag}", unit, fy)
    # and the untouched year still answers on its own end
    intact = current_column(kit.store_rows(ticker, [tag]), fy)
    assert {kit.iso(r["period_end"]) for r in intact} == {kit.next_day(end)}
