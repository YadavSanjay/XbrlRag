"""C2: a breakdown is the latest splitting filing's members (docs/specs/set-b-dimensional-s1.md,
R2).

Store rows are built from SEC FSDS ``num`` rows (conftest). The rule applied by hand: per concept
family and axis kind (segment, product, geography), the latest-filed filing — the year's own or a
later-labelled 10-K — that reports the year's undimensioned total and a single-dimension member
on the kind's axes at the year's period (dimensional-s1 R5's ``breakdown_filings``) gives every
single-dimension member it reports on those axes at that period. Partition sums are hand
arithmetic over FSDS values.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.pg_store import breakdown_candidates

REV = "Revenues"
OI = "OperatingIncomeLoss"
PRODUCTS = ("Automotive", "Compute", "DataCenter", "Gaming", "Networking", "OEMAndOther",
            "ProfessionalVisualization")
COUNTRIES_26 = ("US", "TW", "ChinaIncludingHongKong", "OtherCountries")


def _ids(cands):
    return sorted(int(c["fact_id"]) for c in cands)


def _rows_of(kit, acc, fy, segments):
    return [kit.find(acc, REV, s, fy) for s in segments]


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C2")
def test_fy2024_breakdown_is_the_fy2026_10ks_recast_split(kit):
    # oracle: FSDS num of 0001045810-26-000021 at the FY2024 period — its product split
    # (Compute 38,950 and Networking 8,575 split out of Data Center 47,525) and its four
    # countries; hand sums: 38,950 + 8,575 + 10,447 + 1,553 + 1,091 + 306 = 60,922 and
    # 31,533 + 14,912 + 12,330 + 2,147 = 60,922, the total
    want = (_rows_of(kit, kit.NVDA_26, 2024, [f"ProductOrService={p};" for p in PRODUCTS])
            + _rows_of(kit, kit.NVDA_26, 2024, [f"Geographical={g};" for g in COUNTRIES_26]))
    got = breakdown_candidates(kit.store_rows("NVDA"), 2024)
    assert _ids(got) == _ids(want)
    by = kit.by_id(got)
    parts = [by[kit.find(kit.NVDA_26, REV, f"ProductOrService={p};", 2024)["fact_id"]]["value"]
             for p in PRODUCTS if p != "DataCenter"]
    # amounts tagged in millions: six members, half a million each at most
    assert sum(parts) == pytest.approx(60_922e6, abs=3e6)
    compute = by[kit.find(kit.NVDA_26, REV, "ProductOrService=Compute;", 2024)["fact_id"]]
    assert compute["member_label"] == "Compute"
    # amounts tagged in millions: half a million either way
    assert compute["value"] == pytest.approx(38_950e6, abs=5e5)
    assert all(c["dimensional"] for c in got)


@pytest.mark.criterion("C2")
def test_fy2025_breakdown_drops_the_member_the_recast_dropped(kit):
    # oracle: FSDS num — the FY2026 10-K splits FY2025 revenue into four countries
    # (77,482 + 23,600 + 25,048 + 4,367 = 130,497); the FY2025 10-K's Singapore (23,684) and
    # its other country values are not the breakdown
    want = (_rows_of(kit, kit.NVDA_26, 2025, [f"ProductOrService={p};" for p in PRODUCTS])
            + _rows_of(kit, kit.NVDA_26, 2025, [f"Geographical={g};" for g in COUNTRIES_26]))
    got = breakdown_candidates(kit.store_rows("NVDA"), 2025)
    assert _ids(got) == _ids(want)
    assert kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2025)["fact_id"] not in kit.by_id(got)
    geo = [c["value"] for c in got if c["member_label"] in
           ("United States", "Taiwan", "China Including Hong Kong", "Other Countries")]
    # amounts tagged in millions: four members, half a million each at most
    assert sum(geo) == pytest.approx(130_497e6, abs=2e6)


@pytest.mark.criterion("C2")
def test_fy2023_breakdown_is_the_fy2025_10ks_split(kit):
    # oracle: FSDS num (dimensional-s1 fixtures) — the FY2026 10-K does not report FY2023, so
    # the FY2025 10-K is the latest that splits it: five countries (Singapore 2,288 split out
    # of the FY2023 10-K's Other countries 5,911) and seven products; the segment split is
    # cells only (no single-dimension member) and gives no breakdown
    want = (_rows_of(kit, kit.NVDA_25, 2023, [f"ProductOrService={p};" for p in PRODUCTS])
            + _rows_of(kit, kit.NVDA_25, 2023,
                       [f"Geographical={g};" for g in COUNTRIES_26 + ("SG",)]))
    got = breakdown_candidates(kit.store_rows("NVDA"), 2023)
    assert _ids(got) == _ids(want)
    sg = kit.by_id(got)[kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2023)["fact_id"]]
    # amounts tagged in millions: half a million either way
    assert sg["value"] == pytest.approx(2_288e6, abs=5e5)


@pytest.mark.criterion("C2")
def test_intel_fy2022_takes_the_latest_splitting_filing(kit):
    # oracle: FSDS num (dimensional-s1 fixtures), Decision D3 — operating income: the FY2024 10-K
    # reports the FY2022 total (2,334) and three single-dimension segments (CCG 8,207, DCAI
    # 4,198, NEX 1,532; the rest are cells), so it is the latest splitting filing, although its
    # members do not partition the total; revenue: only its five countries (its segment and
    # product revenue are cells)
    oi = [kit.find(kit.INTC_24, OI, f"BusinessSegments={s};", 2022)
          for s in ("ClientComputingGroup", "DatacenterAndAI", "NetworkAndEdge")]
    rev = [kit.find(kit.INTC_24, "RevenueFromContractWithCustomerExcludingAssessedTax",
                    f"Geographical={g};", 2022)
           for g in ("US", "TW", "SG", "ChinaIncludingHongKong", "OtherCountries")]
    got = breakdown_candidates(kit.store_rows("INTC"), 2022)
    assert _ids(got) == _ids(oi + rev)
    # amounts tagged in millions: three members, half a million each at most
    assert sum(c["value"] for c in got if c["concept"].endswith(OI)) == \
        pytest.approx(13_937e6, abs=1.5e6)


@pytest.mark.criterion("C2")
def test_no_own_column_no_breakdown(kit):
    # oracle: requirement 2 — NVIDIA FY2026 has no own column in the excerpt (no row at its
    # period), Intel FY2023 neither: no breakdown candidates
    assert breakdown_candidates(kit.store_rows("NVDA"), 2026) == []
    assert breakdown_candidates(kit.store_rows("INTC"), 2023) == []
    assert breakdown_candidates(kit.store_rows("NVDA"), 2024) != []


# ------------------------------------------------------------------------------ properties
_AXES = {"segment": ("StatementBusinessSegmentsAxis",), "product": ("ProductOrServiceAxis",),
         "geography": ("StatementGeographicalAxis", "GeographicalAreasAxis")}


def _hand_rule(kit, ticker, fy, rows):
    """The rule by hand: per family and axis kind, the latest filing with the year's total and
    a single-dimension member on the axes at the year's period; all such members of it."""
    start, end = kit.PERIOD[(ticker, kit.DDATE[fy])]
    period = (start, kit.next_day(end))
    own = kit.OWN[(ticker, fy)]
    if not any(r["accession_no"] == own and (r["period_start"], r["period_end"]) == period
               for r in rows):
        return set()
    want = set()
    for fam in {r["concept_family"] for r in rows}:
        at = [r for r in rows if r["concept_family"] == fam
              and (r["period_start"], r["period_end"]) == period
              and (r["accession_no"] == own or r["fiscal_year"] > fy)]
        if not any(r["accession_no"] == own and kit.dims_of(r) == () for r in at):
            continue  # the own column has no total of the family: nothing to split
        for axes in _AXES.values():
            def member(r, axes=axes):
                d = kit.dims_of(r)
                return len(d) == 1 and d[0][0] in axes
            filings = {r["accession_no"] for r in at if kit.dims_of(r) == ()} \
                & {r["accession_no"] for r in at if member(r)}
            if not filings:
                continue
            win = max(filings, key=lambda a: (kit.DEI[a]["filed"], a))
            want |= {r["fact_id"] for r in at if r["accession_no"] == win and member(r)}
    return want


_YEARS = [("NVDA", 2023), ("NVDA", 2024), ("NVDA", 2025), ("INTC", 2022)]


@pytest.mark.criterion("C2")
@given(year=st.sampled_from(_YEARS), keep=st.lists(st.booleans(), min_size=4, max_size=4),
       order=st.randoms(use_true_random=False))
def test_any_later_filings_any_order_give_the_hand_rule(kit, year, keep, order):
    # oracle: the rule by hand (_hand_rule) over the FSDS rows — dropping later 10-Ks moves the
    # breakdown to the latest remaining splitting filing (down to the own one); row order never
    # matters; every candidate is a single-dimension member of one filing per family and kind
    ticker, fy = year
    own = kit.OWN[year]
    kept = {own} | {a for a, k in zip(kit.FILINGS[ticker], keep, strict=False) if k}
    rows = kit.store_rows(ticker, kept)
    order.shuffle(rows)
    got = breakdown_candidates(rows, fy)
    assert set(_ids(got)) == _hand_rule(kit, ticker, fy, rows)
    assert got == sorted(got, key=lambda c: c["fact_id"])
    assert set(_ids(got))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(year=st.sampled_from(_YEARS))
def test_other_periods_and_non_canonical_rows_change_nothing(kit, year):
    # oracle: metamorphic — a non-canonical copy of every row (any filing, filed later) and the
    # rows of other years' periods never change the year's breakdown (hand rule as above)
    ticker, fy = year
    rows = kit.store_rows(ticker)
    base = set(_ids(breakdown_candidates(rows, fy)))
    assert base == _hand_rule(kit, ticker, fy, rows)
    junk = [dict(r, fact_id=r["fact_id"] + 1_000_000, is_canonical_filing=False,
                 accession_no="0000000000-26-999999", filed_date="2026-12-31",
                 fiscal_year=2027) for r in rows]
    assert set(_ids(breakdown_candidates(rows + junk, fy))) == base
