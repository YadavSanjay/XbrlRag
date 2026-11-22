"""C2: ``latest_filed_key`` (S1) reads report forms only; other entries are as if absent.

Oracle: requirement 2 of docs/specs/sec-keys-report-forms.md and sec-keys requirement 5,
values read off the conftest payload by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, UnitMismatchError, latest_filed_key

NI = "us-gaap:NetIncomeLoss"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"


@pytest.mark.criterion("C2")
def test_later_proxies_do_not_replace_the_10k(kit):
    # oracle: payload — FY2023 net income: A23 14_997 (2024-01-29), A24 14_974 (2025-01-29),
    # proxies P25 14_997 (2025-04-20) and P26 14_999 (2026-10-19). Among report forms the
    # latest is A24's recast 14_974.
    doc = kit.companyfacts()
    key = latest_filed_key(doc, NI, "USD", *kit.FY23)
    assert set(key) == kit.KEY_FIELDS
    assert key["value"] == 14_974 * kit.MN
    assert key["accession"] == kit.A24
    assert key["form"] == "10-K"
    assert key["filed"] == "2025-01-29"
    assert key["selection"] == "latest_filed"
    assert key["policies"] == ["S1", "S6"]


@pytest.mark.criterion("C2")
def test_proxy_information_statement_and_formless_entries_are_ignored(kit):
    # oracle: payload — FY2024 net income: A24 7_091 (2025-01-29); the PRE 14C (7_100,
    # 2026-03-02), a form-less entry (1, 2026-05-01) and the proxy P26 (7_130, 2026-10-19)
    # are all later, and none is a report form
    doc = kit.companyfacts()
    key = latest_filed_key(doc, NI, "USD", *kit.FY24)
    assert key["value"] == 7_091 * kit.MN
    assert key["accession"] == kit.A24
    assert key["form"] == "10-K"


@pytest.mark.criterion("C2")
def test_later_8k_does_not_replace_the_10k(kit):
    # oracle: payload — FY2024 capex: A24 11_339 (2025-01-29); 8-K E25L 11_340 (2025-07-23)
    doc = kit.companyfacts()
    key = latest_filed_key(doc, CAPEX, "USD", *kit.FY24)
    assert key["value"] == 11_339 * kit.MN
    assert key["accession"] == kit.A24


@pytest.mark.criterion("C2")
def test_same_day_8k_is_no_tie(kit):
    # oracle: payload — FY2024 revenue: A24 97_690 and 8-K E25S 97_700, both 2025-01-29.
    # With the 8-K absent there is one candidate, so no KeyAmbiguousError.
    doc = kit.companyfacts()
    kind, key = kit.outcome(lambda: latest_filed_key(doc, "us-gaap:Revenues", "USD", *kit.FY24))
    assert kind == "key", f"expected a key, got {key}"
    assert key["value"] == 97_690 * kit.MN
    assert key["accession"] == kit.A24


@pytest.mark.criterion("C2")
def test_later_transition_report_is_ignored_and_10q_still_counts(kit):
    # oracle: payload — FY2024 operating income: A24 7_076; 10-KT K26 7_000 (2026-02-10).
    # The Q1 FY2025 revenue quarter exists only in the 10-Q Q25 (19_335), a report form.
    doc = kit.companyfacts()
    op = latest_filed_key(doc, "us-gaap:OperatingIncomeLoss", "USD", *kit.FY24)
    assert op["value"] == 7_076 * kit.MN
    assert op["accession"] == kit.A24
    q1 = latest_filed_key(doc, "us-gaap:Revenues", "USD", *kit.Q1_25)
    assert q1["value"] == 19_335 * kit.MN
    assert q1["accession"] == kit.Q25
    assert q1["form"] == "10-Q"


@pytest.mark.criterion("C2")
def test_period_concept_or_unit_only_in_non_reports_has_no_key(kit):
    # oracle: requirement 2 — FY2021 net income is only in proxy P25; ecd:CoActuallyPaidCompAmt
    # is only in proxies (concept absent); revenue in EUR is only in 8-K E25S while the concept
    # has USD report entries (unit absent -> UnitMismatchError)
    doc = kit.companyfacts()
    with pytest.raises(KeyNotFoundError):
        latest_filed_key(doc, NI, "USD", *kit.FY21)
    with pytest.raises(KeyNotFoundError):
        latest_filed_key(doc, "ecd:CoActuallyPaidCompAmt", "USD", *kit.FY24)
    with pytest.raises(UnitMismatchError):
        latest_filed_key(doc, "us-gaap:Revenues", "EUR", *kit.FY24)


PROBES = [
    (NI, "USD", "FY21"), (NI, "USD", "FY22"), (NI, "USD", "FY23"), (NI, "USD", "FY24"),
    (CAPEX, "USD", "FY23"), (CAPEX, "USD", "FY24"),
    ("us-gaap:Revenues", "USD", "FY24"), ("us-gaap:Revenues", "USD", "Q1_25"),
    ("us-gaap:Revenues", "EUR", "FY24"), ("us-gaap:Revenues", "JPY", "FY24"),
    ("us-gaap:OperatingIncomeLoss", "USD", "FY24"),
    ("us-gaap:StockholdersEquity", "USD", "END24"), ("us-gaap:StockholdersEquity", "USD", "FY24"),
    ("us-gaap:ProfitLoss", "USD", "FY23"), ("ecd:CoActuallyPaidCompAmt", "USD", "FY23"),
    ("us-gaap:Goodwill", "USD", "END24"),
]


def _all(kit, doc):
    return [kit.outcome(lambda c=c, u=u, p=p: latest_filed_key(doc, c, u, *getattr(kit, p)))
            for c, u, p in PROBES]


@pytest.mark.criterion("C2")
@given(data=st.data())
def test_adding_non_report_entries_changes_no_key(kit, data):
    # oracle: metamorphic, requirement 2 — the builder behaves as on the payload with every
    # non-report entry removed, so appending more of them (any form but the eight, or none;
    # any period, value, date, concept and unit) leaves every probe's key or error unchanged
    noise = data.draw(kit.noise_entries())
    before = _all(kit, kit.companyfacts())
    after = _all(kit, kit.add_noise(kit.companyfacts(), noise))
    assert after == before
