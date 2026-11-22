"""C10-C12: SEC keys identify the year by period end (requirement 10).

Oracle: the CRM values are the two filings' raw iXBRL values; the labels are Salesforce's own.
The synthetic documents' values are read off the conftest by hand.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, annual_filing_labels, fiscal_year_key
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, key_intent


def _key(doc, concept, unit, year):
    """``fiscal_year_key``, or ``None`` when no filing is labelled the year."""
    try:
        return fiscal_year_key(doc, concept, unit, year)
    except KeyNotFoundError:
        return None


def _keyed(kit, intent, doc=None):
    """``key_intent`` over the CRM document, or ``None`` when the intent is dropped."""
    doc = kit.crm_companyfacts() if doc is None else doc
    try:
        return key_intent(intent, kit.crm_inventory(), {"covered": ["CRM"]},
                          kit.FakeSecSource(doc), kit.NoFactStore())
    except IntentDroppedError:
        return None


def _pit(year, filing_year):
    return Intent("point_in_time", tickers=["CRM"], fiscal_years=[year], filing_year=filing_year,
                  metrics=["Revenue"])


# ------------------------------------------------------------------- C10: the colliding filer


@pytest.mark.criterion("C10")
def test_crm_labels_by_filing(kit):
    # oracle: Salesforce's labels; the 10-Q is not an annual filing
    labels = annual_filing_labels(kit.crm_companyfacts())
    assert labels == {kit.C23: 2023, kit.C24: 2024, kit.C25: 2025, kit.C26: 2026}


@pytest.mark.criterion("C10")
def test_crm_revenue_each_year(kit):
    # oracle: raw iXBRL — year ending 2025-01-31 is 37,895M, year ending 2026-01-31 is 41,525M.
    # FY2025's latest report is the January-2026 filing's comparative (S1), same value
    doc = kit.crm_companyfacts()
    fy25, fy26 = _key(doc, kit.REV, "USD", 2025), _key(doc, kit.REV, "USD", 2026)
    assert fy25 is not None and fy26 is not None
    assert (fy25["period_start"], fy25["period_end"]) == kit.Y25
    assert fy25["value"] == 37_895 * kit.MN
    assert fy25["fiscal_year"] == 2025
    assert (fy26["period_start"], fy26["period_end"]) == kit.Y26
    assert fy26["value"] == 41_525 * kit.MN
    assert fy26["accession"] == kit.C26
    assert fy26["fiscal_year"] == 2026
    assert fy26["policies"] == ["S1", "S2", "S6"]


@pytest.mark.criterion("C10")
def test_crm_total_assets_each_year(kit):
    # oracle: raw iXBRL — 102,928M at 2025-01-31 and 112,305M at 2026-01-31
    doc = kit.crm_companyfacts()
    fy25, fy26 = _key(doc, kit.ASSETS, "USD", 2025), _key(doc, kit.ASSETS, "USD", 2026)
    assert fy25 is not None and fy26 is not None
    assert (fy25["period_start"], fy25["period_end"]) == (None, "2025-01-31")
    assert fy25["value"] == 102_928 * kit.MN
    assert (fy26["period_start"], fy26["period_end"]) == (None, "2026-01-31")
    assert fy26["value"] == 112_305 * kit.MN


@pytest.mark.criterion("C10")
def test_no_year_past_the_last_filing(kit):
    # oracle: requirement 10 — 2026 exists, and no filing is labelled 2027
    doc = kit.crm_companyfacts()
    assert _key(doc, kit.REV, "USD", 2026) is not None
    assert _key(doc, kit.REV, "USD", 2027) is None


@pytest.mark.criterion("C10")
@given(seed=st.integers(min_value=0, max_value=1000))
def test_entry_order_does_not_matter(kit, seed):
    # oracle: requirement 10 — a filing's period comes from its entries' dates, never from
    # their order in the payload; metamorphic
    doc = kit.crm_companyfacts()
    random.Random(seed).shuffle(kit.units(doc, kit.REV))
    random.Random(seed + 1).shuffle(kit.units(doc, kit.ASSETS))
    assert annual_filing_labels(doc) == {kit.C23: 2023, kit.C24: 2024, kit.C25: 2025,
                                         kit.C26: 2026}
    key = _key(doc, kit.REV, "USD", 2026)
    assert key is not None and key["value"] == 41_525 * kit.MN


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
def test_a_repeated_slip_takes_the_next_year(kit):
    # oracle: requirement 2 — a next 10-K with fy 2025 again, year ending 2027-01-31, is 2027
    doc = kit.crm_companyfacts()
    next_year = "0001108524-27-000001"
    kit.units(doc, kit.REV).append(kit.entry(("2026-02-01", "2027-01-31"), 45_000 * kit.MN,
                                             next_year, 2025, "2027-03-01"))
    assert annual_filing_labels(doc)[next_year] == 2027
    key = _key(doc, kit.REV, "USD", 2027)
    assert key is not None and key["value"] == 45_000 * kit.MN


# ------------------------------------------------------------------- C11: every other filer


@pytest.mark.criterion("C11")
def test_a_cover_only_amendment_is_absent_and_moves_nothing(kit):
    # oracle: requirement 10 — a Part III 10-K/A carries only dei instants dated after the year
    # end. Read as a period it would collide and shift every later year by one
    doc = kit.december_doc([2023, 2024, 2025])
    amendment = "0000900201-99-000002"
    cover = kit.entry((None, "2025-03-31"), 500 * kit.MN, amendment, 2024, "2025-04-30",
                      form="10-K/A")
    doc["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [cover]}}}
    labels = annual_filing_labels(doc)
    assert amendment not in labels
    assert sorted(labels.values()) == [2023, 2024, 2025]


@pytest.mark.criterion("C11")
def test_a_filing_with_only_stale_instants_moves_nothing(kit):
    # oracle: requirement 10 — the shape of a real 20-F in companyfacts: fy 2025, one dei instant
    # and one srt instant dated inside an earlier year. No annual duration, so no period
    doc = kit.december_doc([2023, 2024])
    sparse = "0000900201-98-000001"
    srt = kit.entry((None, "2024-06-05"), 1 * kit.MN, sparse, 2025, "2026-04-16", form="20-F")
    doc["facts"]["srt"] = {"SomeInstant": {"units": {"USD": [srt]}}}
    labels = annual_filing_labels(doc)
    assert sparse not in labels
    assert sorted(labels.values()) == [2023, 2024]
    # An absent filing's fy still selects its own entries, as today.
    assert _key(doc, "srt:SomeInstant", "USD", 2025)["period_end"] == "2024-06-05"


@pytest.mark.criterion("C11")
def test_a_duration_ending_after_filing_sets_no_period(kit):
    # oracle: requirement 10 — a forward-looking annual duration in a 10-K filed 2025-02-20
    doc = kit.december_doc([2023, 2024])
    latest = "0000900201-01-000001"
    kit.units(doc, "us-gaap:Revenues").append(
        kit.entry(("2025-01-01", "2025-12-31"), 1 * kit.MN, latest, 2024, "2025-02-20"))
    assert annual_filing_labels(doc) == {"0000900201-00-000001": 2023, latest: 2024}


@pytest.mark.criterion("C11")
@given(fys=st.lists(st.integers(min_value=2010, max_value=2025), min_size=1, max_size=6,
                    unique=True).map(sorted))
def test_labels_equal_fy_without_a_collision(kit, fys):
    # oracle: requirement 10 — metamorphic: with no collision labels are exactly fy, and each
    # year's key reads the year ending in it
    doc = kit.december_doc(fys)
    labels = annual_filing_labels(doc)
    assert labels == {f"0000900201-{i:02d}-000001": fy for i, fy in enumerate(fys)}
    for i, fy in enumerate(fys):
        key = _key(doc, "us-gaap:Revenues", "USD", fy)
        assert key["period_end"] == f"{fy}-12-31"
        assert key["value"] == (100 + i) * kit.MN


# ------------------------------------------------------------------------- C12: the suites


@pytest.mark.criterion("C12")
def test_lookup_of_fiscal_2026(kit):
    # oracle: raw iXBRL — 41,525M, from the January-2026 filing
    keyed = _keyed(kit, Intent("lookup", tickers=["CRM"], fiscal_years=[2026],
                               metrics=["Revenue"]))
    assert keyed is not None
    assert keyed["value"] == 41_525 * kit.MN
    assert keyed["inputs"][0]["accession"] == kit.C26


@pytest.mark.criterion("C12")
def test_point_in_time_reads_the_filing_labelled_f(kit):
    # oracle: raw iXBRL — the fiscal-2026 filing's comparatives, filed under its own label, are
    # 37,895M for FY2025 and 34,857M for FY2024; the fiscal-2025 filing also reports FY2024
    fy25_in_26 = _keyed(kit, _pit(2025, 2026))
    assert fy25_in_26 is not None
    assert fy25_in_26["value"] == 37_895 * kit.MN
    assert fy25_in_26["inputs"][0]["accession"] == kit.C26
    assert fy25_in_26["inputs"][0]["selection"] == "as_reported"
    assert (fy25_in_26["inputs"][0]["period_start"],
            fy25_in_26["inputs"][0]["period_end"]) == kit.Y25
    fy24_in_26 = _keyed(kit, _pit(2024, 2026))
    assert fy24_in_26 is not None
    assert fy24_in_26["value"] == 34_857 * kit.MN
    assert fy24_in_26["inputs"][0]["accession"] == kit.C26
    fy24_in_25 = _keyed(kit, _pit(2024, 2025))
    assert fy24_in_25 is not None
    assert fy24_in_25["inputs"][0]["accession"] == kit.C25


@pytest.mark.criterion("C12")
@given(v=st.integers(min_value=30_000, max_value=50_000))
def test_a_recast_comparative_moves_the_right_readings(kit, v):
    # oracle: S1 — metamorphic: recast FY2024 to v in the January-2026 filing. The lookup and
    # the reading in the fiscal-2026 filing move to v; the fiscal-2025 filing's does not
    doc = kit.crm_companyfacts()
    for e in kit.units(doc, kit.REV):
        if e["accn"] == kit.C26 and e["end"] == kit.Y24[1]:
            e["val"] = v * kit.MN
    lookup = _keyed(kit, Intent("lookup", tickers=["CRM"], fiscal_years=[2024],
                                metrics=["Revenue"]), doc)
    earlier, later = _keyed(kit, _pit(2024, 2025), doc), _keyed(kit, _pit(2024, 2026), doc)
    assert lookup is not None and lookup["value"] == v * kit.MN
    assert earlier is not None and earlier["value"] == 34_857 * kit.MN
    assert later is not None and later["value"] == v * kit.MN
