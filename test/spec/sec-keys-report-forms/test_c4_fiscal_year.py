"""C4: ``fiscal_year_key`` (S2 then S1) reads report forms only.

Oracle: requirement 2 of docs/specs/sec-keys-report-forms.md and sec-keys requirement 7,
values read off the conftest payload by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, fiscal_year_key

NI = "us-gaap:NetIncomeLoss"


@pytest.mark.criterion("C4")
def test_net_income_years_come_from_the_10k_not_the_proxies(kit):
    # oracle: payload — FY2023 (labelled by A23, fy 2023): latest report A24's recast 14_974,
    # not P26's 14_999. FY2024 (labelled by A24): A24's 7_091, not P26's 7_130.
    doc = kit.companyfacts()
    fy23 = fiscal_year_key(doc, NI, "USD", 2023)
    assert set(fy23) == kit.KEY_FIELDS
    assert (fy23["period_start"], fy23["period_end"]) == kit.FY23
    assert fy23["value"] == 14_974 * kit.MN
    assert fy23["accession"] == kit.A24
    assert fy23["form"] == "10-K"
    assert fy23["fiscal_year"] == 2023
    assert fy23["policies"] == ["S1", "S2", "S6"]
    fy24 = fiscal_year_key(doc, NI, "USD", 2024)
    assert (fy24["period_start"], fy24["period_end"]) == kit.FY24
    assert fy24["value"] == 7_091 * kit.MN
    assert fy24["accession"] == kit.A24


@pytest.mark.criterion("C4")
def test_same_day_8k_does_not_make_the_year_ambiguous(kit):
    # oracle: payload — FY2024 revenue: A24 97_690; the same-day 8-K E25S (97_700) is absent
    doc = kit.companyfacts()
    kind, key = kit.outcome(lambda: fiscal_year_key(doc, "us-gaap:Revenues", "USD", 2024))
    assert kind == "key", f"expected a key, got {key}"
    assert key["value"] == 97_690 * kit.MN
    assert key["accession"] == kit.A24


@pytest.mark.criterion("C4")
def test_an_8k_duration_does_not_make_an_instant_concept_a_duration(kit):
    # oracle: requirement 2 (last item) — StockholdersEquity's report entries are all instants;
    # only 8-K E25 tags a 2024 duration. So it is an instant concept: FY2024 is A24's
    # year-end balance 72_913 at 2024-12-31.
    doc = kit.companyfacts()
    kind, key = kit.outcome(
        lambda: fiscal_year_key(doc, "us-gaap:StockholdersEquity", "USD", 2024))
    assert kind == "key", f"expected a key, got {key}"
    assert key["period_start"] is None
    assert key["period_end"] == "2024-12-31"
    assert key["value"] == 72_913 * kit.MN
    assert key["accession"] == kit.A24


@pytest.mark.criterion("C4")
def test_nil_in_the_report_and_value_only_in_a_proxy_has_no_key(kit):
    # oracle: payload — FY2023 ProfitLoss is nil in A23 and 15_001 only in proxy P25: no
    # usable report entry, so KeyNotFoundError (a nil is never 0, a proxy never a fallback)
    doc = kit.companyfacts()
    with pytest.raises(KeyNotFoundError):
        fiscal_year_key(doc, "us-gaap:ProfitLoss", "USD", 2023)


PROBES = [
    (NI, 2021), (NI, 2022), (NI, 2023), (NI, 2024), (NI, 2025),
    ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 2024),
    ("us-gaap:Revenues", 2023), ("us-gaap:Revenues", 2024),
    ("us-gaap:OperatingIncomeLoss", 2024), ("us-gaap:OperatingIncomeLoss", 2025),
    ("us-gaap:StockholdersEquity", 2023), ("us-gaap:StockholdersEquity", 2024),
    ("us-gaap:ProfitLoss", 2023), ("ecd:CoActuallyPaidCompAmt", 2024),
]


def _all(kit, doc):
    return [kit.outcome(lambda c=c, y=y: fiscal_year_key(doc, c, "USD", y)) for c, y in PROBES]


@pytest.mark.criterion("C4")
@given(data=st.data())
def test_adding_non_report_entries_changes_no_fiscal_key(kit, data):
    # oracle: metamorphic, requirement 2 — period discovery, the duration test and the S1
    # value all ignore non-report entries, so appending them leaves every probe unchanged
    noise = data.draw(kit.noise_entries())
    before = _all(kit, kit.companyfacts())
    after = _all(kit, kit.add_noise(kit.companyfacts(), noise))
    assert after == before
