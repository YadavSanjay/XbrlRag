"""C3: ``as_reported_key`` (S1 "as originally reported") reads report forms only.

Oracle: requirement 2 of docs/specs/sec-keys-report-forms.md and sec-keys requirement 6,
values read off the conftest payload by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, as_reported_key

NI = "us-gaap:NetIncomeLoss"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"


@pytest.mark.criterion("C3")
def test_earlier_earnings_release_is_not_the_original(kit):
    # oracle: payload — FY2024 capex: 8-K E25 11_300 (2025-01-22, a week before the 10-K),
    # A24 11_339 (2025-01-29), 8-K E25L 11_340. The earliest report form is A24.
    doc = kit.companyfacts()
    key = as_reported_key(doc, CAPEX, "USD", *kit.FY24)
    assert set(key) == kit.KEY_FIELDS
    assert key["value"] == 11_339 * kit.MN
    assert key["accession"] == kit.A24
    assert key["form"] == "10-K"
    assert key["selection"] == "as_reported"


@pytest.mark.criterion("C3")
def test_accession_of_a_non_report_is_not_found(kit):
    # oracle: requirement 2 — the 8-K E25 and the proxy P25 are absent, so asking for their
    # values is KeyNotFoundError; the 10-K's own accession still answers (A24: 11_339,
    # A23 for FY2023 net income: 14_997)
    doc = kit.companyfacts()
    with pytest.raises(KeyNotFoundError):
        as_reported_key(doc, CAPEX, "USD", *kit.FY24, accession=kit.E25)
    with pytest.raises(KeyNotFoundError):
        as_reported_key(doc, NI, "USD", *kit.FY23, accession=kit.P25)
    own = as_reported_key(doc, CAPEX, "USD", *kit.FY24, accession=kit.A24)
    assert own["value"] == 11_339 * kit.MN
    ni23 = as_reported_key(doc, NI, "USD", *kit.FY23, accession=kit.A23)
    assert ni23["value"] == 14_997 * kit.MN


@pytest.mark.criterion("C3")
def test_proxy_only_period_has_no_original(kit):
    # oracle: payload — FY2021 net income is only in the proxy P25; FY2022's earliest report
    # is A23 (12_556), not the proxy's 12_583
    doc = kit.companyfacts()
    with pytest.raises(KeyNotFoundError):
        as_reported_key(doc, NI, "USD", *kit.FY21)
    fy22 = as_reported_key(doc, NI, "USD", *kit.FY22)
    assert fy22["value"] == 12_556 * kit.MN
    assert fy22["accession"] == kit.A23


PROBES = [
    (NI, "FY21", None), (NI, "FY22", None), (NI, "FY23", None), (NI, "FY24", None),
    (NI, "FY23", "A23"), (NI, "FY23", "A24"), (NI, "FY23", "P25"), (NI, "FY24", "P26"),
    (CAPEX, "FY24", None), (CAPEX, "FY24", "E25"), (CAPEX, "FY24", "A24"),
    ("us-gaap:Revenues", "FY24", None), ("us-gaap:Revenues", "FY24", "E25S"),
    ("us-gaap:OperatingIncomeLoss", "FY24", "K26"), ("us-gaap:ProfitLoss", "FY23", None),
]


def _all(kit, doc):
    return [kit.outcome(lambda c=c, p=p, a=a: as_reported_key(
        doc, c, "USD", *getattr(kit, p), accession=None if a is None else getattr(kit, a)))
        for c, p, a in PROBES]


@pytest.mark.criterion("C3")
@given(data=st.data())
def test_adding_non_report_entries_changes_no_original(kit, data):
    # oracle: metamorphic, requirement 2 — appending non-report entries leaves every probe's
    # key or error unchanged, with and without an accession
    noise = data.draw(kit.noise_entries())
    before = _all(kit, kit.companyfacts())
    after = _all(kit, kit.add_noise(kit.companyfacts(), noise))
    assert after == before
