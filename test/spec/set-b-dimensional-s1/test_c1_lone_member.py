"""C1: a lone member is answered by its latest report (docs/specs/set-b-dimensional-s1.md, R1).

Store rows are built from SEC FSDS ``num`` rows (conftest). Expected rows are named by hand as
(filing, tag, FSDS ``segments``, year) and resolved to the fixture's fact ids by ``kit.find``;
values are FSDS ``num`` values. The rule applied by hand: a member of the year's own column is
answered by the latest-filed later-labelled 10-K that reports the same concept, the same full
set of dimensions (by local name) and the same period, else by the own filing's row; a member
the own column lacks is never added (dimensional-s1 R2, D1).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.pg_store import key_candidates

REV = "Revenues"
PRODUCTS = ("Automotive", "Compute", "DataCenter", "Gaming", "Networking", "OEMAndOther",
            "ProfessionalVisualization")
CELLS_SEG = ("BusinessSegments=ComputeAndNetworkingSegment;ConsolidationItems=OperatingSegments;",
             "BusinessSegments=GraphicsSegment;ConsolidationItems=OperatingSegments;")


def _ids(cands):
    return sorted(int(c["fact_id"]) for c in cands)


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C1")
def test_fy2025_members_are_answered_by_the_fy2026_10k(kit):
    # oracle: FSDS num of 0001045810-25-000023 (own) and 0001045810-26-000021, the rule by hand
    # — every FY2025 member the FY2026 10-K also reports (product split, four countries, the
    # two segment cells) answers from it; Singapore and the corporate line (absent from the
    # FY2026 10-K) stay on the FY2025 10-K
    kit_rows = kit.store_rows("NVDA")
    dims = kit.dimensional(key_candidates(kit_rows, 2025))
    from_26 = ([kit.find(kit.NVDA_26, REV, f"ProductOrService={p};", 2025) for p in PRODUCTS]
               + [kit.find(kit.NVDA_26, REV, f"Geographical={g};", 2025)
                  for g in ("US", "TW", "ChinaIncludingHongKong", "OtherCountries")]
               + [kit.find(kit.NVDA_26, REV, s, 2025) for s in CELLS_SEG])
    from_25 = [kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2025),
               kit.find(kit.NVDA_25, REV, "ConsolidationItems=CorporateNonSegment;", 2025)]
    assert _ids(dims) == _ids(from_26 + from_25)
    got = kit.by_id(dims)
    # amounts tagged in millions: half a million either way
    assert got[kit.find(kit.NVDA_26, REV, "Geographical=US;", 2025)["fact_id"]]["value"] == \
        pytest.approx(77_482e6, abs=5e5)
    assert got[kit.find(kit.NVDA_26, REV, "Geographical=TW;", 2025)["fact_id"]]["value"] == \
        pytest.approx(23_600e6, abs=5e5)
    assert got[kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2025)["fact_id"]]["value"] == \
        pytest.approx(23_684e6, abs=5e5)
    for c in dims:
        assert (c["period_start"], c["period_end"]) == ("2024-01-29", "2025-01-26")
    assert got[kit.find(kit.NVDA_26, REV, "Geographical=TW;", 2025)["fact_id"]][
        "member_label"] == "Taiwan"


@pytest.mark.criterion("C1")
def test_fy2024_split_members_only_later_filings_report_are_not_candidates(kit):
    # oracle: FSDS num, the rule by hand — the FY2024 10-K reports Data Center (47,525) but not
    # Compute or Networking (first split out by the FY2025 10-K), nor Singapore; the renamed
    # segment cells (ComputeAndNetworking -> ComputeAndNetworkingSegment) stay on the FY2024
    # 10-K; Other countries answers 2,147 from the FY2026 10-K, not 10,245 as first filed
    dims = kit.dimensional(key_candidates(kit.store_rows("NVDA"), 2024))
    ids = set(_ids(dims))
    for p in ("Automotive", "DataCenter", "Gaming", "OEMAndOther", "ProfessionalVisualization"):
        assert kit.find(kit.NVDA_26, REV, f"ProductOrService={p};", 2024)["fact_id"] in ids
    for acc in (kit.NVDA_25, kit.NVDA_26):
        for p in ("Compute", "Networking"):
            assert kit.find(acc, REV, f"ProductOrService={p};", 2024)["fact_id"] not in ids
    assert kit.find(kit.NVDA_25, REV, "Geographical=SG;", 2024)["fact_id"] not in ids
    cell = kit.find(kit.NVDA_24, REV,
                    "BusinessSegments=ComputeAndNetworking;ConsolidationItems=OperatingSegments;",
                    2024)
    assert cell["fact_id"] in ids
    other = kit.by_id(dims)[kit.find(kit.NVDA_26, REV, "Geographical=OtherCountries;",
                                     2024)["fact_id"]]
    # amounts tagged in millions: half a million either way
    assert other["value"] == pytest.approx(2_147e6, abs=5e5)
    assert kit.find(kit.NVDA_24, REV, "Geographical=OtherCountries;", 2024)["fact_id"] not in ids
    assert len(dims) == 12


@pytest.mark.criterion("C1")
def test_a_cell_restates_by_its_full_dimension_set(kit):
    # oracle: FSDS num (dimensional-s1 fixtures), the rule by hand — Intel FY2022 notebook
    # revenue (segment x consolidated entities x product) is 18,781 in the FY2023 10-K (the
    # FY2024 10-K files CCG on ConsolidationItemsAxis, another identity); CCG operating income
    # is 8,207 (FY2024 10-K); "All other" is -5,977 (FY2023 10-K), never the FY2024 10-K's cell
    # "All other x Operating segments" (1,174); NVIDIA FY2023 Graphics cell 11,906 from the
    # FY2024 10-K (the FY2025 10-K renamed the member)
    nb = kit.find(kit.INTC_23, "RevenueFromContractWithCustomerExcludingAssessedTax",
                  "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
                  "ProductOrService=Notebook;", 2022)
    ccg = kit.find(kit.INTC_24, "OperatingIncomeLoss", "BusinessSegments=ClientComputingGroup;",
                   2022)
    other = kit.find(kit.INTC_23, "OperatingIncomeLoss", "BusinessSegments=AllOtherSegments;",
                     2022)
    cell_25 = kit.find(kit.INTC_24, "OperatingIncomeLoss",
                       "BusinessSegments=AllOtherSegments;ConsolidationItems=OperatingSegments;",
                       2022)
    got = kit.by_id(kit.dimensional(key_candidates(kit.store_rows("INTC"), 2022)))
    assert {nb["fact_id"], ccg["fact_id"], other["fact_id"]} <= set(got)
    assert cell_25["fact_id"] not in got
    # amounts tagged in millions: half a million either way
    assert got[nb["fact_id"]]["value"] == pytest.approx(18_781e6, abs=5e5)
    assert got[ccg["fact_id"]]["value"] == pytest.approx(8_207e6, abs=5e5)
    assert got[other["fact_id"]]["value"] == pytest.approx(-5_977e6, abs=5e5)
    # a cell's label joins its cleaned member labels in axis QName order
    # (srt:ConsolidatedEntitiesAxis < srt:ProductOrServiceAxis < us-gaap:StatementBusiness...)
    assert got[nb["fact_id"]]["member_label"] == \
        "Operating Segments | Notebook | Client Computing Group"
    graphics = kit.find(kit.NVDA_24, REV,
                        "BusinessSegments=Graphics;ConsolidationItems=OperatingSegments;", 2023)
    nvda = kit.by_id(kit.dimensional(key_candidates(kit.store_rows("NVDA"), 2023)))
    assert graphics["fact_id"] in nvda
    # amounts tagged in millions: half a million either way
    assert nvda[graphics["fact_id"]]["value"] == pytest.approx(11_906e6, abs=5e5)


# ------------------------------------------------------------------------------ properties
def _hand_rule(kit, ticker, fy, kept, rows):
    """The rule by hand over the fixture: per own-column member, the latest kept later 10-K
    with the same concept, dimensions and period, else the own row."""
    own = kit.OWN[(ticker, fy)]
    start, end = kit.PERIOD[(ticker, kit.DDATE[fy])]
    period = (start, kit.next_day(end))

    def key(r):
        return (kit.local(r["concept"]), kit.dims_of(r), r["period_start"], r["period_end"])

    later = [r for r in rows if r["accession_no"] in kept and r["fiscal_year"] > fy
             and r["segment_axis"] is not None]
    want = set()
    for r in rows:
        if r["accession_no"] != own or r["segment_axis"] is None \
                or (r["period_start"], r["period_end"]) != period:
            continue
        same = [x for x in later if key(x) == key(r)]
        if not same:
            want.add(r["fact_id"])
            continue
        win = max(same, key=lambda x: (x["filed_date"], x["accession_no"]))["accession_no"]
        want |= {x["fact_id"] for x in same if x["accession_no"] == win}
    return want


_YEARS = [("NVDA", 2023), ("NVDA", 2024), ("NVDA", 2025), ("INTC", 2022)]


@pytest.mark.criterion("C1")
@given(year=st.sampled_from(_YEARS), keep=st.lists(st.booleans(), min_size=4, max_size=4),
       order=st.randoms(use_true_random=False))
def test_any_later_filings_any_order_give_the_hand_rule(kit, year, keep, order):
    # oracle: the rule by hand (_hand_rule) over the FSDS rows — for any subset of the later
    # 10-Ks and any row order the lone-member candidates are the hand rule's, and the flat
    # candidates equal those of the flat rows alone (dimensional rows never move them)
    ticker, fy = year
    own = kit.OWN[year]
    kept = {own} | {a for a, k in zip(kit.FILINGS[ticker], keep, strict=False) if k}
    rows = kit.store_rows(ticker, kept)
    order.shuffle(rows)
    cands = key_candidates(rows, fy)
    assert set(_ids(kit.dimensional(cands))) == _hand_rule(kit, ticker, fy, kept, rows)
    flat_rows = [r for r in rows if r["segment_axis"] is None]
    assert _ids(c for c in cands if not c["dimensional"]) == _ids(key_candidates(flat_rows, fy))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(year=st.sampled_from(_YEARS))
def test_earlier_and_non_canonical_filings_change_nothing(kit, year):
    # oracle: metamorphic — rows of a filing labelled before the year, and non-canonical copies
    # of the latest filing's rows, never answer; the hand rule over the canonical rows holds
    ticker, fy = year
    rows = kit.store_rows(ticker)
    base = set(_ids(kit.dimensional(key_candidates(rows, fy))))
    assert base == _hand_rule(kit, ticker, fy, set(kit.FILINGS[ticker]), rows)
    latest = kit.FILINGS[ticker][-1]
    junk = []
    for r in rows:
        if r["accession_no"] == latest:
            junk.append(dict(r, fact_id=r["fact_id"] + 1_000_000, is_canonical_filing=False,
                             filed_date="2026-12-31", value=r["value"] + 1.0))
    earlier = [dict(r, fact_id=r["fact_id"] + 2_000_000, fiscal_year=fy - 1,
                    accession_no="0000000000-20-000001", filed_date="2020-01-01")
               for r in rows if r["accession_no"] == kit.OWN[year]]
    assert set(_ids(kit.dimensional(key_candidates(rows + junk + earlier, fy)))) == base
