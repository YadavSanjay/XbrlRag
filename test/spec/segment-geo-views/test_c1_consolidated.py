"""C1: a context is consolidated exactly when it has no non-default member (J8, locked).

docs/specs/segment-geo-views.md, Requirement 1. Point cases: Eli Lilly's FY2023 10-K
(0000059478-24-000065) against FSDS 2024q1, where an empty `segments` field is the
consolidated figure. Properties: every fixture fact's recorded view row, and synthetic contexts.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.segment_geo import is_consolidated, shares_of_total

LLY = "lly_fy2023_revenues"
LLY_ACC = "0000059478-24-000065"
FY2023 = ("2023-01-01", "2024-01-01")  # xbrl_db dates a duration end as the next day
ALL_FILINGS = [
    "adbe_fy2025_revenues", "unp_fy2024_revenue", "meta_fy2024_revenue",
    "tsla_fy2023_revenues", "lly_fy2023_revenues",
]


def _period(f: dict) -> tuple:
    return (f["period_start"], f["period_end"])


@pytest.mark.criterion("C1")
def test_lly_default_only_contexts_are_the_consolidated_total(filing, fsds):
    # oracle: FSDS 2024q1 num.txt for 0000059478-24-000065, Revenues, ddate 20231231, qtrs 4:
    # the row with empty segments is 34,124,100,000 and Geographical=US is 21,791,000,000.
    # XBRL Dimensions 1.0: a default member stands for the absence of the dimension.
    lly = filing(LLY)
    ref = fsds(LLY_ACC)
    assert ref[""] == Decimal("34124100000")
    assert ref["Geographical=US;"] == Decimal("21791000000")

    fy = [f for f in lly.facts if _period(f) == FY2023]
    default_only = [f for f in fy if all(d["is_default"] for d in f["dimensions"])]
    assert default_only, "the fixture holds the consolidated FY2023 revenue"
    for f in default_only:
        assert len(f["dimensions"]) > 0  # Arelle recorded default entries on the context
        assert is_consolidated(f["dimensions"]) is True
        assert f["value"] == ref[""]

    us = lly.by_id(219519)
    assert [(d["axis"], d["member"]) for d in us["dimensions"] if not d["is_default"]] == [
        ("srt:StatementGeographicalAxis", "country:US")]
    assert us["value"] == ref["Geographical=US;"]
    assert is_consolidated(us["dimensions"]) is False

    consolidated_values = {f["value"] for f in fy if is_consolidated(f["dimensions"]) is True}
    assert consolidated_values == {ref[""]}


@pytest.mark.criterion("C1")
def test_typed_dimension_is_never_consolidated_nor_a_total():
    # oracle: Requirement 1 (a typed entry has member None and is never default) and
    # Requirement 3 (only consolidated facts are totals); values chosen by hand: 40/200 = 0.2,
    # 100/200 = 0.5.
    default = {"axis": "srt:ConsolidationItemsAxis", "member": "srt:ConsolidationItemsDomain",
               "is_default": True}
    typed = {"axis": "t:ContractAxis", "member": None, "is_default": False}
    typed_marked_default = {"axis": "t:ContractAxis", "member": None, "is_default": True}
    assert is_consolidated([]) is True
    assert is_consolidated([default]) is True
    assert is_consolidated([default, typed]) is False
    assert is_consolidated([typed_marked_default]) is False

    base = {"accession_no": "0000000001-25-000001", "concept": "us-gaap:Revenues", "unit": "USD",
            "period_start": "2024-01-01", "period_end": "2025-01-01", "decimals": -6}
    member = {**base, "fact_id": 1, "value": Decimal(40),
              "dimensions": [default, {"axis": "srt:StatementGeographicalAxis",
                                       "member": "country:US", "is_default": False}]}
    typed_fact = {**base, "fact_id": 2, "value": Decimal(100), "dimensions": [default, typed]}
    total = {**base, "fact_id": 3, "value": Decimal(200), "dimensions": [default]}

    without_total = shares_of_total([member, typed_fact])
    assert set(without_total) == {1, 2}
    assert without_total[1] is None and without_total[2] is None

    with_total = shares_of_total([member, typed_fact, total])
    assert set(with_total) == {1, 2}
    # tolerance: exact ratios of small integers; abs 1e-12 only absorbs float rounding
    assert with_total[1] == pytest.approx(0.2, abs=1e-12)
    assert with_total[2] == pytest.approx(0.5, abs=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@pytest.mark.parametrize("name", ALL_FILINGS)
def test_view_segment_axis_is_null_exactly_when_consolidated(filing, name):
    # oracle: Requirement 1 locks today's mv_financial_facts rule (investigation J8 found 0
    # mismatches); each fixture fact carries its own view row, recorded from xbrl_db.
    facts = filing(name).facts
    flags = [is_consolidated(f["dimensions"]) for f in facts]
    assert any(flag is True for flag in flags) and any(flag is False for flag in flags)
    for f, flag in zip(facts, flags, strict=True):
        assert flag is (f["view"]["segment_axis"] is None), f["fact_id"]
        assert flag is all(d["is_default"] for d in f["dimensions"]), f["fact_id"]


_axis = st.sampled_from(["srt:StatementGeographicalAxis", "srt:ProductOrServiceAxis",
                         "us-gaap:StatementBusinessSegmentsAxis", "t:CustomAxis"])
_member = st.sampled_from(["country:US", "srt:EuropeMember", "http://x.example/2024:AMember"])
_default_entry = st.builds(lambda a, m: {"axis": a, "member": m, "is_default": True},
                           _axis, _member)
_explicit_entry = st.builds(lambda a, m: {"axis": a, "member": m, "is_default": False},
                            _axis, _member)
_typed_entry = st.builds(lambda a: {"axis": a, "member": None, "is_default": False}, _axis)


@pytest.mark.criterion("C1")
@given(defaults=st.lists(_default_entry, max_size=8),
       extra=st.lists(_default_entry, max_size=8),
       part=st.one_of(_explicit_entry, _typed_entry),
       pos=st.integers(min_value=0, max_value=16))
def test_default_entries_never_matter_and_any_part_breaks_consolidation(defaults, extra, part, pos):
    # oracle: Requirement 1 by construction: default entries are ignored, an explicit or typed
    # entry anywhere in the list makes the context a part.
    assert is_consolidated(defaults) is True
    assert is_consolidated(defaults + extra) is True
    with_part = list(defaults)
    with_part.insert(min(pos, len(with_part)), part)
    assert is_consolidated(with_part) is False
    assert is_consolidated(with_part + extra) is False
