"""Unit tests for the range reading in extract_year_util (docs/specs/fiscal-year-range.md).

The locked spec tests cover the range forms; these pin what the spec leaves to the
implementation: the split-year forms keep their reading, ``overlong_year_ranges`` reports what
the resolver turns ambiguous, and an endpoint outside the bounds is not expanded.
"""

from __future__ import annotations

from src.retriever.extract_year_util import (
    MAX_RANGE_YEARS,
    extract_years_in_range,
    overlong_year_ranges,
)

BOUNDS = [2018, 2035]


def test_split_year_forms_keep_their_reading():
    assert extract_years_in_range("FY21-23", year_range=BOUNDS) == [2021, 2022, 2023]
    assert extract_years_in_range("FY22–23", year_range=BOUNDS) == [2022, 2023]
    assert extract_years_in_range("2021/22", year_range=BOUNDS) == [2021, 2022]


def test_overlong_year_ranges_reports_only_ranges_over_the_cap():
    assert MAX_RANGE_YEARS == 10
    assert overlong_year_ranges("fiscal 2018 through fiscal 2030", BOUNDS) == [(2018, 2030)]
    assert overlong_year_ranges("from fiscal 2030 to fiscal 2018", BOUNDS) == [(2018, 2030)]
    assert overlong_year_ranges("fiscal 2018 through fiscal 2027", BOUNDS) == []
    assert overlong_year_ranges("fiscal 2018 and fiscal 2030", BOUNDS) == []


def test_a_range_with_an_endpoint_out_of_bounds_is_not_expanded():
    assert extract_years_in_range("2015 to 2020", year_range=BOUNDS) == [2020]


def test_an_iso_date_is_not_a_range():
    assert extract_years_in_range("as of 2023-12-31", year_range=BOUNDS) == [2023]
    assert overlong_year_ranges("as of 2023-12-31") == []
