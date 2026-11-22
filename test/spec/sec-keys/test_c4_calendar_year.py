"""C4: ``calendar_year_key`` reads calendar keys from SEC frames (S2).

Oracle: requirement 8 of docs/specs/sec-keys.md, read off the frames fixtures by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, calendar_year_key, fiscal_year_key


@pytest.mark.criterion("C4")
def test_june_filer_calendar_2024_is_its_fiscal_2025(kit):
    # oracle: fixture — the CY2024 frame's row for CIK 900002 is 2024-07-01..2025-06-30,
    # 281_724_000_000, accn J25; its fiscal 2024 (companyfacts) ends 2024-06-30 instead
    key = calendar_year_key(kit.revenue_frame(), kit.JUN_CIK, "USD")
    assert set(key) == kit.KEY_FIELDS
    assert key["value"] == 281_724_000_000
    assert (key["period_start"], key["period_end"]) == ("2024-07-01", "2025-06-30")
    assert key["accession"] == kit.J25
    assert key["calendar_year"] == 2024
    assert key["fiscal_year"] is None
    assert key["concept"] == "us-gaap:Revenues"
    assert key["unit"] == "USD"
    assert key["cik"] == kit.JUN_CIK
    assert key["filed"] is None and key["form"] is None
    assert key["decimals"] is None
    assert key["key_source"] == "sec_frames"
    assert key["selection"] == "sec_frame"
    assert key["policies"] == ["S1", "S2", "S6"]
    assert key["status"] == "single-source"
    assert key["verified_by"] == []
    fiscal = fiscal_year_key(kit.companyfacts(kit.JUN_CIK), "us-gaap:Revenues", "USD", 2024)
    assert fiscal["period_end"] != key["period_end"]


@pytest.mark.criterion("C4")
def test_sep_filer_row_and_unpadded_cik(kit):
    # oracle: fixture — CIK 900001's CY2024 row is its FY2024, 360_000_000_000, accn A24;
    # padding of the requested CIK is ignored
    key = calendar_year_key(kit.revenue_frame(), "900001", "USD")
    assert key["value"] == 360_000_000_000
    assert (key["period_start"], key["period_end"]) == kit.SEP_FY24
    assert key["accession"] == kit.A24
    assert key["cik"] == kit.SEP_CIK


@pytest.mark.criterion("C4")
def test_instant_frame(kit):
    # oracle: fixture — CY2024Q4I assets: CIK 900004 ends 2024-12-31 at 80_000_000_000
    key = calendar_year_key(kit.assets_frame(), kit.CAL_CIK, "USD")
    assert key["period_start"] is None
    assert key["period_end"] == "2024-12-31"
    assert key["value"] == 80_000_000_000
    assert key["calendar_year"] == 2024
    assert key["concept"] == "us-gaap:Assets"


@pytest.mark.criterion("C4")
def test_absent_cik_bad_ccp_and_nil_row(kit):
    # oracle: requirement 8 — the TWD filer is not in the frame; "CY2024Q1" is neither CY####
    # nor CY####Q4I; a nil row is not a value
    with pytest.raises(KeyNotFoundError):
        calendar_year_key(kit.revenue_frame(), kit.TWD_CIK, "USD")
    bad = kit.revenue_frame()
    bad["ccp"] = "CY2024Q1"
    with pytest.raises(ValueError):
        calendar_year_key(bad, kit.SEP_CIK, "USD")
    nil = kit.revenue_frame()
    nil["data"][2]["val"] = None
    with pytest.raises(KeyNotFoundError):
        calendar_year_key(nil, kit.CAL_CIK, "USD")


@pytest.mark.criterion("C4")
@given(rows=st.lists(
    st.tuples(st.integers(min_value=1, max_value=9_999_999),
              st.integers(min_value=-10**12, max_value=10**13),
              st.integers(min_value=0, max_value=27)),
    min_size=1, max_size=6, unique_by=lambda r: r[0]))
def test_generated_frames_each_cik_gets_its_row(kit, rows):
    # oracle: requirement 8 — each CIK's key copies its own row's val, start, end and accn
    data = [{"accn": f"{cik:010d}-25-{i:06d}", "cik": cik, "entityName": f"Filer {cik}",
             "loc": "US-NY", "start": f"2024-01-{day + 1:02d}", "end": f"2024-12-{day + 1:02d}",
             "val": val} for i, (cik, val, day) in enumerate(rows)]
    frame = {"taxonomy": "us-gaap", "tag": "Revenues", "ccp": "CY2024", "uom": "USD",
             "label": "Revenues", "description": "", "pts": len(data), "data": data}
    for row in data:
        key = calendar_year_key(frame, str(row["cik"]), "USD")
        assert key["value"] == row["val"]
        assert key["period_start"] == row["start"]
        assert key["period_end"] == row["end"]
        assert key["accession"] == row["accn"]
        assert key["cik"] == f"{row['cik']:010d}"
