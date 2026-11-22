"""C7: the source-backed builders call the ``SecSource`` seam once, with the right arguments.

Oracle: requirement 10 of docs/specs/sec-keys.md; expected values read off the fixtures.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import calendar_key_from_source, fiscal_key_from_source


@pytest.mark.criterion("C7")
def test_fiscal_key_from_source(kit):
    # oracle: fixture — the September filer's FY2023 key: 2022-09-25..2023-09-30,
    # 340_750_000_000 from A24 (as in C3); one company_facts call with the CIK
    src = kit.FakeSecSource()
    key = fiscal_key_from_source(src, kit.SEP_CIK, "us-gaap:Revenues", "USD", 2023)
    assert src.calls == [("company_facts", kit.SEP_CIK)]
    assert key["value"] == 340_750_000_000
    assert (key["period_start"], key["period_end"]) == kit.SEP_FY23
    assert key["accession"] == kit.A24
    assert key["fiscal_year"] == 2023
    assert key["key_source"] == "sec_companyfacts"


@pytest.mark.criterion("C7")
def test_calendar_key_from_source_duration_and_instant(kit):
    # oracle: fixture — CY2024 revenue for the June filer is 281_724_000_000 (J25); the
    # CY2024Q4I assets for CIK 900004 is 80_000_000_000
    src = kit.FakeSecSource()
    rev = calendar_key_from_source(src, kit.JUN_CIK, "us-gaap:Revenues", "USD", 2024)
    assert src.calls == [("frames", "us-gaap", "Revenues", "USD", "CY2024")]
    assert rev["value"] == 281_724_000_000
    assert rev["accession"] == kit.J25
    assert rev["key_source"] == "sec_frames"
    src2 = kit.FakeSecSource()
    assets = calendar_key_from_source(src2, kit.CAL_CIK, "us-gaap:Assets", "USD", 2024,
                                      instant=True)
    assert src2.calls == [("frames", "us-gaap", "Assets", "USD", "CY2024Q4I")]
    assert assets["value"] == 80_000_000_000
    assert assets["period_start"] is None


@pytest.mark.criterion("C7")
@given(year=st.integers(min_value=2009, max_value=2099), instant=st.booleans())
def test_period_string_for_any_year(kit, year, instant):
    # oracle: requirement 10 — the period is f"CY{year}" or f"CY{year}Q4I"; the fake answers
    # an unknown period with an empty frame, so the builder then raises KeyNotFoundError
    src = kit.FakeSecSource()
    expected = f"CY{year}Q4I" if instant else f"CY{year}"
    expected_ok = year == 2024  # only the CY2024 frames are fixtures
    try:
        calendar_key_from_source(src, kit.CAL_CIK, "us-gaap:Assets" if instant
                                 else "us-gaap:Revenues", "USD", year, instant=instant)
        found = True
    except LookupError:
        found = False
    assert src.calls == [("frames", "us-gaap", "Assets" if instant else "Revenues", "USD",
                          expected)]
    assert found is expected_ok
