"""C3: the segment and geographic screens rank the latest report (dimensional-s1 R4)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

OP = "OperatingIncomeLoss"
CCG = "BusinessSegments=ClientComputingGroup;"
NVDA_SYNTH = "0001045810-26-900001"   # synthetic, not a real filing


def _assert_row(row, value, fact_id, accession, fy, share):
    assert row is not None
    assert row["value"] == pytest.approx(value, abs=5e5)  # millions precision (decimals -6)
    assert row["fact_id"] == str(fact_id)
    assert row["citation"]["accession_no"] == accession
    assert row["fiscal_year"] == fy
    assert row["pct_of_total"] == pytest.approx(share, rel=1e-9)  # a ratio of exact integers


@pytest.mark.criterion("C3")
def test_segment_screen_ranks_the_restated_member(kit):
    # oracle: FSDS num - Intel CCG operating income FY2022 is 8,207 in the FY2024 10-K
    # (0000050863-25-000009), whose consolidated operating income for the year is 2,334
    rows = kit.store_rows()
    res = kit.run_screen(rows, 2022, "OperatingIncome", {"segment": "client computing"})
    fact = kit.find(rows, kit.INTC_FY24, OP, CCG)
    _assert_row(kit.ranked(res, "INTC"), 8_207e6, fact["fact_id"], kit.INTC_FY24, 2022,
                8_207 / 2_334)


@pytest.mark.criterion("C3")
def test_geographic_screen_ranks_the_restated_country_and_never_a_recast_only_member(kit):
    # oracle: FSDS num - NVIDIA Taiwan revenue FY2023 is 6,986 of 26,974 in the FY2025 10-K
    # (0001045810-25-000023), the latest report of the period; Singapore (2,288) appears only
    # in that later filing, never in the FY2023 10-K's own column (requirement 2, D1)
    rows = kit.store_rows()
    res = kit.run_screen(rows, 2023, "Revenue", {"country": "TW"})
    fact = kit.find(rows, kit.NVDA_FY25, "Revenues", "Geographical=TW;")
    _assert_row(kit.ranked(res, "NVDA"), 6_986e6, fact["fact_id"], kit.NVDA_FY25, 2023,
                6_986 / 26_974)
    sg = kit.run_screen(rows, 2023, "Revenue", {"country": "SG"})
    assert kit.ranked(sg, "NVDA") is None
    assert any("NVDA (member_not_found" in n for n in sg.resolution_notes)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.lists(st.booleans(), min_size=2, max_size=2))
def test_segment_screen_for_any_later_reports_agrees_with_the_lookup(kit, keep):
    # oracle: by hand, the latest stored filing reporting CCG's single-dimension fact, its
    # share over that filing's 2,334 total (FSDS num); the one-dimension lookup over the
    # same store answers the same fact (requirement 4)
    later = [a for a, k in zip((kit.INTC_FY23, kit.INTC_FY24), keep, strict=True) if k]
    accs = [kit.INTC_OWN, *later]
    rows = kit.store_rows("INTC", accessions=accs) + kit.store_rows("NVDA")
    best = max((kit.find(rows, a, OP, CCG) for a in accs),
               key=lambda r: (r["filed_date"], r["accession_no"]))
    res = kit.run_screen(rows, 2022, "OperatingIncome", {"segment": "client computing"})
    _assert_row(kit.ranked(res, "INTC"), best["value"], best["fact_id"], best["accession_no"],
                2022, best["value"] / 2_334e6)
    look = kit.run_lookup(rows, "INTC", 2022, "OperatingIncome",
                          {"segment": "Client Computing Group"})
    assert look.status.value == "OK"
    assert look.value == pytest.approx(best["value"], abs=5e5)  # millions precision
    assert look.citation.accession_no == best["accession_no"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_a_later_recast_moves_the_rank_and_its_share_uses_that_filing(kit):
    # oracle: requirement 4 - a later canonical 10-K (synthetic) reporting Taiwan at 7,000
    # and the 26,974 total answers with share 7,000 / 26,974; one reporting only the total
    # leaves the FY2025 10-K's Taiwan row
    rows = kit.store_rows()
    tw = kit.find(rows, kit.NVDA_FY25, "Revenues", "Geographical=TW;")
    total = kit.find(rows, kit.NVDA_FY25, "Revenues", "")
    recast = kit.synthetic_later_filing(
        rows, kit.NVDA_FY25, lambda r: r["fact_id"] in (tw["fact_id"], total["fact_id"]),
        change={tw["fact_id"]: 7_000e6}, accession=NVDA_SYNTH, filed="2026-03-02",
        label=2026)
    res = kit.run_screen(rows + recast, 2023, "Revenue", {"country": "TW"})
    new_tw = next(r for r in recast if r["segment_axis"] is not None)
    _assert_row(kit.ranked(res, "NVDA"), 7_000e6, new_tw["fact_id"], NVDA_SYNTH, 2023,
                7_000 / 26_974)
    only_total = [r for r in recast if r["fact_id"] != new_tw["fact_id"]]
    res = kit.run_screen(rows + only_total, 2023, "Revenue", {"country": "TW"})
    _assert_row(kit.ranked(res, "NVDA"), 6_986e6, tw["fact_id"], kit.NVDA_FY25, 2023,
                6_986 / 26_974)
