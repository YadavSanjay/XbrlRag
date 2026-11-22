"""C3: a member's share is its value over the consolidated total of the same concept, unit and
period, not over the sum of the axis (J2).

docs/specs/segment-geo-views.md, Requirement 3. Point cases: Adobe FY2025, Union Pacific
FY2024 and Meta FY2024 against FSDS (an empty `segments` field is the total). Properties:
synthetic filings and the real fixtures under metamorphic changes.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.segment_geo import is_consolidated, shares_of_total

ADBE, ADBE_ACC = "adbe_fy2025_revenues", "0000796343-26-000003"
UNP, UNP_ACC = "unp_fy2024_revenue", "0000100885-25-000042"
META, META_ACC = "meta_fy2024_revenue", "0001326801-25-000017"
PP = 0.001  # tolerance: C3's 0.1 percentage point, as a fraction


def _strip(facts: list[dict]) -> list[dict]:
    return [{k: v for k, v in f.items() if k != "view"} for f in facts]


@pytest.mark.criterion("C3")
def test_adobe_subscription_share_is_of_total_revenue(filing, fsds):
    # oracle: FSDS 2026q1 num.txt, 0000796343-26-000003, Revenues, ddate 20251130, qtrs 4:
    # '' = 23,769,000,000; ProductOrService=SubscriptionRevenue = 22,904,000,000;
    # BusinessSegments=DigitalMedia;ProductOrService=SubscriptionRevenue = 17,389,000,000;
    # BusinessSegments=DigitalMedia = 17,649,000,000. Shares divided here.
    ref = fsds(ADBE_ACC)
    total = ref[""]
    assert total == Decimal("23769000000")
    adbe = filing(ADBE)
    shares = shares_of_total(_strip(adbe.facts))
    sub = float(ref["ProductOrService=SubscriptionRevenue;"] / total)
    assert sub == pytest.approx(22904 / 23769, abs=1e-12)  # 96.36%
    for fid in (171025, 171423):
        assert adbe.by_id(fid)["value"] == ref["ProductOrService=SubscriptionRevenue;"]
        assert shares[fid] == pytest.approx(sub, abs=PP)
    cell = float(ref["BusinessSegments=DigitalMedia;ProductOrService=SubscriptionRevenue;"] / total)
    assert adbe.by_id(171414)["value"] == Decimal("17389000000")
    assert shares[171414] == pytest.approx(cell, abs=PP)  # 73.16%
    dm = float(ref["BusinessSegments=DigitalMedia;"] / total)
    assert shares[171346] == pytest.approx(dm, abs=PP)  # 74.25%
    parts = {f["fact_id"] for f in adbe.facts if f["view"]["segment_axis"] is not None}
    assert set(shares) == parts


@pytest.mark.criterion("C3")
def test_union_pacific_mexico_is_an_eighth_not_all(filing, fsds):
    # oracle: FSDS 2025q1 num.txt, 0000100885-25-000042,
    # RevenueFromContractWithCustomerExcludingAssessedTax, ddate 20241231, qtrs 4:
    # '' = 24,250,000,000; Geographical=MX = 3,000,000,000 (12.37%);
    # BusinessSegments=ReportableSegment = 24,250,000,000 (a part that equals the whole: 100%).
    ref = fsds(UNP_ACC)
    unp = filing(UNP)
    shares = shares_of_total(_strip(unp.facts))
    assert unp.by_id(242744)["value"] == ref["Geographical=MX;"]
    assert shares[242744] == pytest.approx(float(ref["Geographical=MX;"] / ref[""]), abs=PP)
    assert shares[242744] == pytest.approx(3000 / 24250, abs=PP)
    assert shares[242769] == pytest.approx(1.0, abs=PP)


@pytest.mark.criterion("C3")
def test_meta_overlapping_geographies_each_keep_their_own_share(filing, fsds):
    # oracle: FSDS 2025q1 num.txt, 0001326801-25-000017, RevenueFromContractWithCustomer...,
    # ddate 20241231, qtrs 4: '' = 164,501,000,000; USCanada 63,207,000,000; US 59,730,000,000;
    # Europe 38,361M; AsiaPacific 45,009M; RestOfWorld 17,924M (these four sum to the total).
    ref = fsds(META_ACC)
    total = ref[""]
    meta = filing(META)
    shares = shares_of_total(_strip(meta.facts))
    assert shares[40367] == pytest.approx(float(ref["Geographical=USCanada;"] / total), abs=PP)
    assert shares[40382] == pytest.approx(float(ref["Geographical=US;"] / total), abs=PP)
    assert shares[40367] == pytest.approx(0.3842, abs=PP)
    assert shares[40382] == pytest.approx(0.3631, abs=PP)
    four = [40367, 40370, 40373, 40376]  # US & Canada, Europe, Asia-Pacific, Rest of World
    assert sum(ref[f"Geographical={m};"] for m in ("USCanada", "Europe", "AsiaPacific",
                                                   "RestOfWorld")) == total
    assert sum(shares[f] for f in four) == pytest.approx(1.0, abs=PP)
    geo = [f["fact_id"] for f in meta.current()
           if f["view"]["segment_axis"] == "srt:StatementGeographicalAxis"]
    assert sum(shares[f] for f in geo) > 1.3  # US and China overlap the regions; both kept


def _ctx(fid, value, dims=(), unit="USD", start="2024-01-01", end="2025-01-01", decimals=-6,
         concept="us-gaap:Revenues", acc="0000000001-25-000001"):
    return {"fact_id": fid, "accession_no": acc, "concept": concept, "unit": unit,
            "period_start": start, "period_end": end,
            "value": None if value is None else Decimal(value), "decimals": decimals,
            "dimensions": [{"axis": "srt:StatementGeographicalAxis", "member": m,
                            "is_default": False} for m in dims]}


@pytest.mark.criterion("C3")
def test_missing_zero_nil_and_conflicting_totals():
    # oracle: Requirement 3 by hand: 30/120 = 0.25; 164,501M at decimals -6 beats 164.5B at
    # decimals -8 (63,207/164,501 = 0.384235...); equal-precision totals 100 and 101 conflict.
    shares = shares_of_total([
        _ctx(1, 30, ["country:US"]), _ctx(2, 120),
        _ctx(3, 30, ["country:US"], unit="EUR"),                        # no EUR total
        _ctx(4, None, ["country:CN"]),                                  # nil part
        _ctx(5, 7, ["country:US"], start="2023-01-01", end="2024-01-01"),  # no total that year
        _ctx(6, 7, ["country:US"], concept="us-gaap:CostOfRevenue"),    # no total of that concept
    ])
    assert set(shares) == {1, 3, 4, 5, 6}
    assert shares[1] == pytest.approx(0.25, abs=1e-12)
    assert shares[3] is None and shares[4] is None and shares[5] is None and shares[6] is None

    rounded = shares_of_total([_ctx(1, 63207000000, ["x:USCanadaMember"]),
                               _ctx(2, 164500000000, decimals=-8),
                               _ctx(3, 164501000000, decimals=-6)])
    assert rounded == {1: pytest.approx(63207 / 164501, abs=1e-9)}

    assert shares_of_total([_ctx(1, 5, ["country:US"]), _ctx(2, 100), _ctx(3, 101)]) == {1: None}
    assert shares_of_total([_ctx(1, 5, ["country:US"]), _ctx(2, 0)]) == {1: None}
    exact = shares_of_total([_ctx(1, 5, ["country:US"]), _ctx(2, 20, decimals=None),
                             _ctx(3, 21, decimals=-6)])
    assert exact == {1: pytest.approx(0.25, abs=1e-12)}  # INF precision is the highest


_member = st.sampled_from(["country:US", "country:CN", "srt:EuropeMember", "x:RestMember"])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(seed=st.randoms(use_true_random=False),
       extra=st.lists(st.tuples(_member, st.integers(min_value=-10**12, max_value=10**12)),
                      min_size=1, max_size=6),
       k=st.integers(min_value=1, max_value=1000),
       name=st.sampled_from([ADBE, UNP, META]))
def test_other_parts_order_repeats_and_scale_never_change_a_share(filing, seed, extra, k, name):
    # oracle: Requirement 3: a share depends only on its own value and its total. Each change
    # here is one the old sum-of-axis denominator was sensitive to.
    facts = _strip(filing(name).facts)
    base = shares_of_total(facts)
    assert any(v is not None for v in base.values())
    template = next(f for f in facts if not is_consolidated(f["dimensions"]))
    added = [dict(template, fact_id=10**9 + i, value=Decimal(v),
                  dimensions=[{"axis": "srt:StatementGeographicalAxis", "member": m,
                               "is_default": False}])
             for i, (m, v) in enumerate(extra)]
    repeats = [dict(f, fact_id=2 * 10**9 + f["fact_id"]) for f in facts
               if is_consolidated(f["dimensions"])]
    changed = facts + added + repeats
    seed.shuffle(changed)
    after = shares_of_total(changed)
    scaled = shares_of_total([dict(f, value=None if f["value"] is None else f["value"] * k)
                              for f in facts])
    for fid, share in base.items():
        if share is None:
            assert after[fid] is None and scaled[fid] is None
        else:
            # tolerance: float division of the same exact ratio; 1e-12 absorbs rounding only
            assert after[fid] == pytest.approx(share, abs=1e-12)
            assert scaled[fid] == pytest.approx(share, abs=1e-12)
    assert set(after) == set(base) | {f["fact_id"] for f in added}


@pytest.mark.criterion("C3")
@given(total=st.integers(min_value=1, max_value=10**12),
       parts=st.lists(st.tuples(_member, st.integers(min_value=0, max_value=10**12)),
                      min_size=1, max_size=8),
       unit=st.sampled_from(["EUR", "shares", "USD/shares"]))
def test_share_is_value_over_same_unit_total(total, parts, unit):
    # oracle: Requirement 3, the division done here; a part in a unit without a total gets None.
    facts = [_ctx(0, total)] + [_ctx(i + 1, v, [m]) for i, (m, v) in enumerate(parts)]
    facts.append(_ctx(99, 5, ["country:US"], unit=unit))
    shares = shares_of_total(facts)
    assert set(shares) == {i + 1 for i in range(len(parts))} | {99}
    for i, (_, v) in enumerate(parts):
        assert shares[i + 1] == pytest.approx(v / total, rel=1e-12, abs=1e-15)
    assert shares[99] is None
