"""C2: the own column, exactly (docs/specs/set-b-restatement.md, requirement 1).

No earlier column stands in for a year whose own filing lacks the period (restatement-s1 G1),
and the cover-page share count comes from the canonical 10-K, never a Part III-only 10-K/A.
Dates are the DEI ``DocumentPeriodEndDate`` of each filing's iXBRL instance (``dei.json``).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.pg_store import key_candidates

COVER = "EntityCommonStockSharesOutstanding"

# Filer-years with a canonical FY own filing in the fixtures, for the properties.
FILER_YEARS = ([("NVDA", fy) for fy in range(2022, 2027)]
               + [("AMD", fy) for fy in range(2022, 2026)]
               + [("AVGO", fy) for fy in range(2022, 2026)]
               + [("TSLA", fy) for fy in range(2022, 2026)]
               + [("COST", fy) for fy in (2024, 2025, 2026)]
               + [("INTC", fy) for fy in (2023, 2024, 2025)])


def _own_filing(kit, ticker, fy):
    own = {a for a, d in kit.DEI.items() if d["ticker"] == ticker
           and int(d["fiscal_year_focus"]) == fy and d["fiscal_period_focus"] == "FY"
           and a not in kit.PART_III}
    assert len(own) == 1, (ticker, fy, own)
    return own.pop()


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C2")
def test_no_prior_column_stands_in_for_a_missing_year(kit):
    # oracle: companyfacts and FSDS hold no INTC FY2025 dividends-declared entry ending
    # 2025-12-27 (INTC 10-K 0000050863-26-000011 DEI period end) and no COST FY2025 goodwill entry
    # ending 2025-08-31 (0000909832-25-000101), only prior-year columns (restatement-s1 C4);
    # COST's FY2026 10-K reports goodwill $994M at its own period end 2026-08-30
    intc = key_candidates(kit.store_rows("INTC"), 2025)
    assert kit.flat(intc, "CommonStockDividendsPerShareDeclared") == []
    assert kit.flat(intc, "NetIncomeLoss"), "the FY2025 own column exists"
    cost_rows = kit.store_rows("COST")
    assert kit.flat(key_candidates(cost_rows, 2025), "Goodwill") == []
    fy26 = kit.flat(key_candidates(cost_rows, 2026), "Goodwill")
    assert fy26
    for c in fy26:
        assert c["period_end"] == "2026-08-30"
        assert c["period_start"] is None
        # amounts tagged in millions: half a million either way
        assert c["value"] == pytest.approx(994e6, abs=5e5)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("ticker,fy,tenk,tenka,end,shares", [
    ("AMD", 2025, "0000002488-26-000018", "0000002488-26-000021", "2026-01-30", 1_630_410_843),
    ("TSLA", 2025, "0001628280-26-003952", "0001104659-26-053166", "2026-01-23", 3_752_431_984),
], ids=["AMD-FY2025", "TSLA-FY2025"])
def test_the_cover_share_count_comes_from_the_canonical_10k(kit, ticker, fy, tenk, tenka, end,
                                                             shares):
    # oracle: companyfacts dei:EntityCommonStockSharesOutstanding entries — the 10-K and its
    # Part III-only 10-K/A report the same count at the cover date (after the period end, on or
    # before the 10-K's filing date); filing_registry holds the 10-K/A superseded by the 10-K
    rows = kit.store_rows(ticker)
    cands = kit.flat(key_candidates(rows, fy), COVER)
    want = sorted(r["fact_id"] for r in kit.rows_of(rows, COVER, tenk, end))
    not_want = {r["fact_id"] for r in kit.rows_of(rows, COVER, tenka, end)}
    assert want and not_want
    assert sorted(c["fact_id"] for c in cands) == want
    for c in cands:
        assert c["value"] == shares  # exact: a share count
        assert c["period_end"] == end
        assert c["unit"] == "shares"


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C2")
@given(item=st.sampled_from(FILER_YEARS))
def test_every_flat_candidate_ends_on_the_own_filings_period_end(kit, item):
    # oracle: DEI DocumentPeriodEndDate of the year's canonical own filing (the iXBRL instance);
    # only the cover share count may end after it
    ticker, fy = item
    dpe = kit.DEI[_own_filing(kit, ticker, fy)]["document_period_end"]
    cands = [c for c in key_candidates(kit.store_rows(ticker), fy) if not c["dimensional"]]
    assert cands
    for c in cands:
        if kit.local(c["concept"]) == COVER:
            assert c["period_end"] > dpe
        else:
            assert c["period_end"] == dpe


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(item=st.sampled_from(FILER_YEARS))
def test_without_the_own_filing_there_is_no_flat_candidate(kit, item):
    # oracle: metamorphic — S1 replaces the own column's periods, it never invents a year: with
    # the own filing's rows removed, later filings' comparatives do not answer for it
    ticker, fy = item
    rows = kit.store_rows(ticker)
    assert [c for c in key_candidates(rows, fy) if not c["dimensional"]]
    own = _own_filing(kit, ticker, fy)
    without = [r for r in rows if r["accession_no"] != own]
    assert [c for c in key_candidates(without, fy) if not c["dimensional"]] == []
