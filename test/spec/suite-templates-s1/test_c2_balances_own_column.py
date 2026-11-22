"""C2: balances and the exact own column (docs/specs/suite-templates-s1.md, requirement 1).

aq-5a's measures: a year-end balance is the fy filing's own column (ending exactly on its DEI
``DocumentPeriodEndDate``) as the latest later-labelled report restates it; a filing without
that column has no pick, never a prior-year comparative.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.sql_derived import s1_lookup_pick

TOL = 5e4  # half of $0.1M: LLY tags $78,714.9M (decimals -5), the later 10-K $78,715M

GRID = [("LLY", "TotalAssets", fy) for fy in (2023, 2024, 2025)] + [
    ("COST", "Goodwill", fy) for fy in (2023, 2024, 2025, 2026)]


@pytest.mark.criterion("C2")
def test_a_restated_balance_is_the_later_reports(kit):
    # oracle: FSDS num — 0000059478-26-000013 reports $78,715M at 2024-12-31, the FY2024 10-K
    # 0000059478-25-000067 reported $78,714.9M; DEI end of the FY2024 10-K is 2024-12-31
    pick = s1_lookup_pick(kit.store_rows("LLY", ["TotalAssets"]), 2024)
    assert pick is not None
    assert pick["accession_no"] == "0000059478-26-000013"
    assert pick["value"] == pytest.approx(78_715_000_000.0, abs=TOL)  # tagged in millions
    assert pick["value"] != pytest.approx(78_714_900_000.0, abs=TOL)
    assert kit.iso(pick["period_end"]) == "2025-01-01"


@pytest.mark.criterion("C2")
def test_no_own_column_means_no_pick(kit):
    # oracle: DEI of 0000909832-25-000101 ends 2025-08-31; its companyfacts entries tag goodwill
    # only at 2023-09-03 and 2024-09-01 ($994M, the stale column; FSDS has no goodwill row)
    assert kit.DEI["0000909832-25-000101"]["document_period_end"] == "2025-08-31"
    assert s1_lookup_pick(kit.store_rows("COST", ["Goodwill"]), 2025) is None


@pytest.mark.criterion("C2")
def test_the_next_year_answers_on_its_own_end(kit):
    # oracle: companyfacts entry of 0000909832-26-000093 at its DEI end 2026-08-30, $994M
    # (that 10-K is in no cached FSDS quarter)
    pick = s1_lookup_pick(kit.store_rows("COST", ["Goodwill"]), 2026)
    assert pick is not None
    assert pick["accession_no"] == "0000909832-26-000093"
    assert pick["value"] == pytest.approx(994_000_000.0, abs=TOL)  # tagged in millions
    assert kit.iso(pick["period_end"]) == "2026-08-31"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40)
@given(st.sampled_from(GRID))
def test_every_pick_ends_on_the_fy_filings_own_end(kit, item):
    # oracle: DEI DocumentPeriodEndDate of the fy filing (exact day), S1 by hand
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    pick = s1_lookup_pick(rows, fy)
    own = kit.own_end(ticker, fy)
    has_own = any(r["accession_no"] == kit.own_filing(ticker, fy)
                  and kit.iso(r["period_end"]) == own for r in rows)
    if not has_own:
        assert pick is None
        return
    assert pick is not None
    assert kit.iso(pick["period_end"]) == own
    assert kit.s1_holds(pick, rows, fy)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40)
@given(st.sampled_from(GRID + [("INTC", "CashFromFinancing", 2023), ("META", "Capex", 2024)]))
def test_removing_the_own_column_never_falls_back(kit, item):
    # oracle: requirement 1 — no own column, no pick, whatever other filings report
    ticker, family, fy = item
    own_accn, own = kit.own_filing(ticker, fy), kit.own_end(ticker, fy)
    rows = [r for r in kit.store_rows(ticker, [family])
            if not (r["accession_no"] == own_accn and kit.iso(r["period_end"]) == own)]
    assert s1_lookup_pick(rows, fy) is None
