"""C4: ``later_annual_reports`` lists the original annual reports labelled after a year.

Oracle: requirement 3 of docs/specs/key-fallback-witness.md; FEXT's three 10-Ks (conftest
FILINGS, labels written by hand) and, for the property, generated filers whose originals and
amendments are written in the test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.v2_witness import later_annual_reports

AMEND = "0000810002-24-000099"  # a made-up FY2023 10-K/A of FEXT, filed after the original


@pytest.mark.criterion("C4")
def test_fext_reports_after_each_year(kit):
    # oracle: FEXT 10-Ks labelled 2022, 2023, 2024 (conftest FILINGS): the later ones, the
    # latest label first
    cf, labels = kit.companyfacts("FEXT"), kit.labels("FEXT")
    assert later_annual_reports(cf, labels, 2022) == [kit.FE24, kit.FE23]
    assert later_annual_reports(cf, labels, 2023) == [kit.FE24]
    assert later_annual_reports(cf, labels, 2024) == []
    assert later_annual_reports(cf, labels, 2021) == [kit.FE24, kit.FE23, kit.FE22]


@pytest.mark.criterion("C4")
def test_a_later_amendment_does_not_replace_the_original(kit):
    # oracle: requirement 3: the original report of a label (least by (is an amendment,
    # filed, accession)); the 10-K/A labelled 2023 is filed 2024-06-03, after the original
    cf, labels = kit.companyfacts("FEXT"), dict(kit.labels("FEXT"))
    cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": "2023-01-01", "end": "2023-12-31", "val": 12_000_000_000, "accn": AMEND,
         "fy": 2023, "fp": "FY", "form": "10-K/A", "filed": "2024-06-03"})
    labels[AMEND] = 2023
    assert later_annual_reports(cf, labels, 2022) == [kit.FE24, kit.FE23]


def _filer(years, amended):
    """A made-up filer (CIK 810008): an original 10-K per year, filed in February of the next
    year, and for each year in ``amended`` a 10-K/A filed in June."""
    entries, labels, originals = [], {}, {}
    for y in sorted(years):
        orig = f"0000810008-{(y + 1) % 100:02d}-{y:06d}"
        originals[y] = orig
        labels[orig] = y
        entries.append({"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 1000 + y,
                        "accn": orig, "fy": y, "fp": "FY", "form": "10-K",
                        "filed": f"{y + 1}-02-15"})
        if y in amended:
            amd = f"0000810008-{(y + 1) % 100:02d}-{900000 + y:06d}"
            labels[amd] = y
            entries.append({"start": f"{y}-01-01", "end": f"{y}-12-31", "val": 1001 + y,
                            "accn": amd, "fy": y, "fp": "FY", "form": "10-K/A",
                            "filed": f"{y + 1}-06-01"})
    cf = {"cik": 810008, "entityName": "Gen", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": entries}}}}}
    return cf, labels, originals


@pytest.mark.criterion("C4")
@given(years=st.sets(st.integers(2015, 2026), min_size=1, max_size=8),
       data=st.data())
def test_originals_above_the_year_latest_first(years, data):
    # oracle: requirement 3 restated: the original of each label above the year, by label
    # descending, whatever amendments are filed later
    amended = data.draw(st.sets(st.sampled_from(sorted(years))))
    year = data.draw(st.integers(2014, 2027))
    cf, labels, originals = _filer(years, amended)
    want = [originals[y] for y in sorted(years, reverse=True) if y > year]
    assert later_annual_reports(cf, labels, year) == want
