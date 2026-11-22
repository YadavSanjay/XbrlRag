"""q4-lookup C1: a Q4 with no filed quarter is FY minus the same label's nine months.

docs/specs/q4-lookup.md R4, R5. MSFT FY2026 revenue from the 10-K and the Q3 10-Q store rows;
the expected Q4 is computed by hand from FSDS `num` (period-disclosure C5's hand value).
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.retriever.xbrl.q4_lookup import select_q4


def _ids(rows: list[dict], accession: str, start: str, end: str) -> set[int]:
    return {r["fact_id"] for r in rows if r["accession_no"] == accession
            and r["period_start"] == start and r["period_end"] == end}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("unit", [None, "USD"])
def test_msft_fy2026_q4_revenue_is_derived(fx, unit):
    # oracle: FSDS num: 10-K qtrs 4 at 20260630 = 331,839,000,000; 10-Q qtrs 3 at 20260331 =
    # 241,832,000,000; by hand 90,007,000,000 over 2026-04-01..2026-06-30 = 91 days
    fy = fx.fsds_value(fx.MSFT_10K, fx.REVENUE, "20260630", 4)
    nm = fx.fsds_value(fx.MSFT_10Q, fx.REVENUE, "20260331", 3)
    rows = fx.store(fx.REVENUE)
    year_ids = _ids(rows, fx.MSFT_10K, "2025-07-01", "2026-07-01")
    nine_ids = _ids(rows, fx.MSFT_10Q, "2025-07-01", "2026-04-01")
    assert {r["value"] for r in rows if r["fact_id"] in year_ids} == {fy}
    assert {r["value"] for r in rows if r["fact_id"] in nine_ids} == {nm}

    out = select_q4(rows, 2026, unit=unit)
    assert set(out) == fx.KEYS
    assert out["status"] == "derived"
    assert out["value"] == fy - nm == 90_007_000_000
    assert out["unit"] == "USD"
    assert (out["period_start"], out["period_end"], out["days"]) == ("2026-04-01", "2026-06-30", 91)
    assert out["decimals"] == -6
    year_id, nine_id = out["fact_ids"]
    assert year_id in year_ids and nine_id in nine_ids
    assert tuple(out["accessions"]) == (fx.MSFT_10K, fx.MSFT_10Q)
    assert out["basis"] == "latest"
    assert "derived" in out["note"].lower()
    assert out["reason"] is None


def _shift_year(row: dict, label: int, fact_id: int, value: int) -> dict:
    """The same row one year earlier, in an earlier-labelled filing (never a restatement)."""
    out = dict(row, fact_id=fact_id, fiscal_year=label, value=value,
               accession_no=row["accession_no"][:-6] + "999999")
    for k in ("period_start", "period_end", "document_period_end", "filed_date"):
        d = dt.date.fromisoformat(out[k])
        out[k] = d.replace(year=d.year - 1).isoformat()
    return out


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data(), earlier=st.booleans(), dimensional=st.booleans(), other_unit=st.booleans(),
       noise=st.integers(min_value=1, max_value=10**12))
def test_row_order_and_unrelated_rows_change_nothing(fx, data, earlier, dimensional, other_unit,
                                                     noise):
    # oracle: R1, R2, R4: only the label's own non-dimensional rows in the unit count, in any
    # order; the C1 hand value 90,007,000,000
    rows = fx.store(fx.REVENUE)
    extra: list[dict] = []
    for i, r in enumerate(rows):
        if earlier:
            extra.append(_shift_year(r, r["fiscal_year"] - 1, 900_000 + i, noise))
        if dimensional:
            extra.append(dict(r, fact_id=910_000 + i, value=noise,
                              segment_axis="srt:ProductOrServiceAxis",
                              segment_member="msft:OtherMember"))
        if other_unit:
            extra.append(dict(r, fact_id=920_000 + i, value=noise, unit="EUR"))
    mixed = data.draw(st.permutations(rows + extra))
    out = select_q4(mixed, 2026, unit="USD")
    assert out["status"] == "derived"
    assert out["value"] == 90_007_000_000
    assert out["basis"] == "latest"
    assert set(out["fact_ids"]) <= {r["fact_id"] for r in rows}


@pytest.mark.criterion("C1")
@given(start=st.dates(min_value=dt.date(2005, 1, 1), max_value=dt.date(2030, 12, 31)),
       fy_days=st.integers(min_value=350, max_value=380),
       q_days=st.integers(min_value=84, max_value=119),
       fy_val=st.integers(min_value=-10**12, max_value=10**12),
       nm_val=st.integers(min_value=-10**12, max_value=10**12))
def test_derived_q4_plus_nine_months_is_the_year(fx, start, fy_days, q_days, fy_val, nm_val):
    # oracle: R5 by hand: inside the bands (annual 350-380, nine months 252-280, remainder
    # 84-119 days) Q4 = FY - 9M, citing (year, nine months)
    nm_days = fy_days - q_days
    assume(252 <= nm_days <= 280)
    year = fx.synth(11, 2024, "FY", start, fy_days, fy_val, "0000000001-24-000010",
                    start + dt.timedelta(days=fy_days + 40))
    nine = fx.synth(7, 2024, "Q3", start, nm_days, nm_val, "0000000001-24-000007",
                    start + dt.timedelta(days=nm_days + 35))
    out = select_q4([nine, year], 2024)
    assert out["status"] == "derived"
    assert out["value"] + nine["value"] == year["value"]
    assert tuple(out["fact_ids"]) == (11, 7)
    assert out["days"] == q_days
    assert out["basis"] == "latest"
