"""C2: ``as_reported_key`` gives the value as originally reported, or as one accession has it.

Oracle: requirement 6 of docs/specs/sec-keys.md, read off the September filer's fixture.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, as_reported_key

# FY2022 revenue per accession, from the fixture: (accession, filed, value).
FY22_BY_ACCESSION = [
    ("0000900001-22-000010", "2022-10-28", 320_000_000_000),
    ("0000900001-23-000020", "2023-11-03", 320_000_000_000),
    ("0000900001-24-000030", "2024-11-01", 321_500_000_000),
]


@pytest.mark.criterion("C2")
def test_fy2022_as_originally_reported(kit):
    # oracle: fixture — the earliest filed FY2022 entry is A22 (2022-10-28), 320_000_000_000
    doc = kit.companyfacts(kit.SEP_CIK)
    key = as_reported_key(doc, "us-gaap:Revenues", "USD", *kit.SEP_FY22)
    assert set(key) == kit.KEY_FIELDS
    assert key["value"] == 320_000_000_000
    assert key["accession"] == kit.A22
    assert key["filed"] == "2022-10-28"
    assert key["selection"] == "as_reported"
    assert key["policies"] == ["S1", "S6"]
    assert key["key_source"] == "sec_companyfacts"
    assert key["status"] == "single-source"


@pytest.mark.criterion("C2")
def test_given_accession(kit):
    # oracle: fixture — A24 reports FY2022 as 321_500_000_000; the 10-Q Q25 does not report
    # FY2022 at all
    doc = kit.companyfacts(kit.SEP_CIK)
    key = as_reported_key(doc, "us-gaap:Revenues", "USD", *kit.SEP_FY22, accession=kit.A24)
    assert key["value"] == 321_500_000_000
    assert key["accession"] == kit.A24
    assert key["selection"] == "as_reported"
    with pytest.raises(KeyNotFoundError):
        as_reported_key(doc, "us-gaap:Revenues", "USD", *kit.SEP_FY22, accession=kit.Q25)


@pytest.mark.criterion("C2")
def test_earliest_same_day_tie_takes_smallest_accession(kit):
    # oracle: requirement 6 — R&D FY2024 is reported only on 2024-11-01, by A24 and A24A with
    # equal values; the as-reported tie resolves to the smaller accession, A24
    doc = kit.companyfacts(kit.SEP_CIK)
    key = as_reported_key(doc, "us-gaap:ResearchAndDevelopmentExpense", "USD", *kit.SEP_FY24)
    assert key["value"] == 30_000_000_000
    assert key["accession"] == kit.A24


@pytest.mark.criterion("C2")
@given(row=st.sampled_from(FY22_BY_ACCESSION))
def test_every_accession_reporting_the_period(kit, row):
    # oracle: FY22_BY_ACCESSION, copied from the fixture by hand
    accession, filed, value = row
    doc = kit.companyfacts(kit.SEP_CIK)
    key = as_reported_key(doc, "us-gaap:Revenues", "USD", *kit.SEP_FY22, accession=accession)
    assert key["value"] == value
    assert key["accession"] == accession
    assert key["filed"] == filed
    assert key["selection"] == "as_reported"
