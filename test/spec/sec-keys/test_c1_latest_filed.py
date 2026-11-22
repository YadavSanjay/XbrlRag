"""C1: ``latest_filed_key`` keeps the entry with the latest ``filed`` (S1).

Oracle: requirements 4-5 of docs/specs/sec-keys.md, read off the September filer's fixture
(``fixtures/companyfacts_CIK0000900001.json``) by hand.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyAmbiguousError, KeyNotFoundError, latest_filed_key


@pytest.mark.criterion("C1")
def test_fy2022_revenue_is_the_recast_value_with_full_provenance(kit):
    # oracle: fixture — FY2022 (2021-09-26..2022-09-24) is reported by A22 (320.0bn, filed
    # 2022-10-28, fy 2022), A23 (320.0bn, 2023-11-03) and A24 (321.5bn, 2024-11-01, fy 2024).
    # The latest filed is A24's recast 321_500_000_000; a pick by fy == 2022 would give 320bn.
    doc = kit.companyfacts(kit.SEP_CIK)
    key = latest_filed_key(doc, "us-gaap:Revenues", "USD", *kit.SEP_FY22)
    assert set(key) == kit.KEY_FIELDS
    assert key["value"] == 321_500_000_000
    assert key["value"] != 320_000_000_000
    assert key["accession"] == kit.A24
    assert key["filed"] == "2024-11-01"
    assert key["form"] == "10-K"
    assert key["concept"] == "us-gaap:Revenues"
    assert key["unit"] == "USD"
    assert key["cik"] == kit.SEP_CIK
    assert (key["period_start"], key["period_end"]) == kit.SEP_FY22
    assert key["decimals"] is None  # SEC payloads carry no decimals: unknown, never 0
    assert key["fiscal_year"] is None and key["calendar_year"] is None
    assert key["key_source"] == "sec_companyfacts"
    assert key["selection"] == "latest_filed"
    assert key["policies"] == ["S1", "S6"]
    assert key["status"] == "single-source"
    assert key["verified_by"] == []


@pytest.mark.criterion("C1")
def test_instant_matches_only_entries_without_start(kit):
    # oracle: fixture — Assets at 2023-09-30: A23 350bn (2023-11-03), A24 351bn (2024-11-01).
    # Cash at 2024-09-28: instant 30_000_000_000, duration 999_000_000 (same end).
    doc = kit.companyfacts(kit.SEP_CIK)
    assets = latest_filed_key(doc, "us-gaap:Assets", "USD", None, "2023-09-30")
    assert assets["value"] == 351_000_000_000
    assert assets["accession"] == kit.A24
    assert assets["period_start"] is None
    cash_i = latest_filed_key(doc, "us-gaap:CashAndCashEquivalentsAtCarryingValue", "USD",
                              None, "2024-09-28")
    assert cash_i["value"] == 30_000_000_000
    cash_d = latest_filed_key(doc, "us-gaap:CashAndCashEquivalentsAtCarryingValue", "USD",
                              *kit.SEP_FY24)
    assert cash_d["value"] == 999_000_000


@pytest.mark.criterion("C1")
def test_duration_period_queried_as_instant_is_not_found(kit):
    # oracle: requirement 4 — Revenues has only duration entries ending 2024-09-28, so an
    # instant query for that end has no candidate
    doc = kit.companyfacts(kit.SEP_CIK)
    with pytest.raises(KeyNotFoundError):
        latest_filed_key(doc, "us-gaap:Revenues", "USD", None, "2024-09-28")


@pytest.mark.criterion("C1")
def test_nil_is_skipped_never_zero(kit):
    # oracle: fixture — InventoryNet at 2023-09-30: A23 6_000_000_000 (2023-11-03) and a nil
    # A24 (2024-11-01); the nil is dropped, so A23's value is the key. Goodwill is nil only.
    doc = kit.companyfacts(kit.SEP_CIK)
    inv = latest_filed_key(doc, "us-gaap:InventoryNet", "USD", None, "2023-09-30")
    assert inv["value"] == 6_000_000_000
    assert inv["accession"] == kit.A23
    with pytest.raises(KeyNotFoundError):
        latest_filed_key(doc, "us-gaap:Goodwill", "USD", None, "2024-09-28")


@pytest.mark.criterion("C1")
def test_same_day_ties(kit):
    # oracle: fixture — R&D FY2024: A24 (10-K) and A24A (10-K/A) both 30bn, both filed
    # 2024-11-01: equal values, so the greater accession A24A. CostOfRevenue FY2024: 200bn vs
    # 201bn on the same day: ambiguous.
    doc = kit.companyfacts(kit.SEP_CIK)
    rnd = latest_filed_key(doc, "us-gaap:ResearchAndDevelopmentExpense", "USD", *kit.SEP_FY24)
    assert rnd["value"] == 30_000_000_000
    assert rnd["accession"] == kit.A24A
    assert rnd["form"] == "10-K/A"
    with pytest.raises(KeyAmbiguousError):
        latest_filed_key(doc, "us-gaap:CostOfRevenue", "USD", *kit.SEP_FY24)


@pytest.mark.criterion("C1")
def test_missing_concept_and_malformed_concept(kit):
    # oracle: requirements 3-4 — an absent concept is KeyNotFoundError; no ":" is ValueError
    doc = kit.companyfacts(kit.SEP_CIK)
    with pytest.raises(KeyNotFoundError):
        latest_filed_key(doc, "us-gaap:NoSuchConcept", "USD", *kit.SEP_FY24)
    with pytest.raises(ValueError):
        latest_filed_key(doc, "Revenues", "USD", *kit.SEP_FY24)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(seed=st.integers(min_value=0, max_value=10_000),
       earlier_val=st.integers(min_value=-10**12, max_value=10**13))
def test_order_and_earlier_filings_do_not_change_the_key(kit, seed, earlier_val):
    # oracle: requirement 5 — the key depends only on the latest filed date, never on entry
    # order; an entry filed before 2024-11-01 cannot win. Expected: A24's 321_500_000_000.
    doc = kit.companyfacts(kit.SEP_CIK)
    entries = kit.units_of(doc, "us-gaap", "Revenues")["USD"]
    start, end = kit.SEP_FY22
    entries.append({"start": start, "end": end, "val": earlier_val,
                    "accn": "0000900001-21-000099", "fy": 2022, "fp": "FY", "form": "10-K",
                    "filed": "2021-01-15"})
    random.Random(seed).shuffle(entries)
    key = latest_filed_key(doc, "us-gaap:Revenues", "USD", start, end)
    assert key["value"] == 321_500_000_000
    assert key["accession"] == kit.A24
    assert key["filed"] == "2024-11-01"
