"""Spec tests for period-disclosure C5: Q4 is FY minus the nine-month YTD, on one basis.

docs/specs/period-disclosure.md, requirement R5. MSFT's FY2026 10-K (0001193125-26-323660)
and Q3 10-Q (0001193125-26-191507); the expected Q4 is computed by hand from FSDS `num`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.period_disclosure import derive_q4

KEYS = {"status", "value", "unit", "period_start", "period_end", "days", "decimals",
        "fact_ids", "accessions", "note", "reason"}
TAG = "RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.mark.criterion("C5")
def test_msft_fy2026_q4_revenue(fx):
    # oracle: FSDS num: qtrs 4 at 2026-06-30 = 331,839,000,000 (10-K); qtrs 3 at 2026-03-31 =
    # 241,832,000,000 (10-Q); both from 2025-07-01 (companyfacts); by hand 90,007,000,000,
    # 2026-04-01..2026-06-30 = 91 days
    fy = fx.fsds_value("0001193125-26-323660", TAG, "20260630", 4)
    nm = fx.fsds_value("0001193125-26-191507", TAG, "20260331", 3)
    assert fx.cf_entry("0001193125-26-191507", "us-gaap:" + TAG, "2025-07-01",
                       "2026-03-31")["val"] == nm
    annual, nine = fx.fact(105742), fx.fact(52104)
    assert (annual["value"], nine["value"]) == (fy, nm)
    out = derive_q4(annual, nine)
    assert set(out) == KEYS
    assert out["status"] == "derived"
    assert out["value"] == fy - nm == 90007000000
    assert out["unit"] == "USD"
    assert (out["period_start"], out["period_end"], out["days"]) == ("2026-04-01", "2026-06-30", 91)
    assert out["decimals"] == -6
    assert tuple(out["fact_ids"]) == (105742, 52104)
    assert tuple(out["accessions"]) == ("0001193125-26-323660", "0001193125-26-191507")
    assert "derived" in out["note"].lower()
    assert out["reason"] is None


def _refused(out: dict) -> None:
    assert out["status"] == "refused"
    assert out["value"] is None
    assert isinstance(out["reason"], str) and out["reason"].strip()


@pytest.mark.criterion("C5")
def test_discrete_q3_quarter_is_not_a_nine_month_basis(fx):
    # oracle: FSDS qtrs 1 for fact 50640 (82,886M): a quarter, so FY - it is not Q4 (R5.4)
    assert fx.fsds_value("0001193125-26-191507", TAG, "20260331", 1) == fx.fact(50640)["value"]
    _refused(derive_q4(fx.fact(105742), fx.fact(50640)))


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("change", ["start", "unit", "concept", "nil_annual", "nil_nine"])
def test_mixed_bases_are_refused(fx, change):
    # oracle: R5 conditions 1-3 by hand
    annual, nine = fx.fact(105742), fx.fact(52104)
    if change == "start":
        nine["period_start"] = "2025-07-02"
    elif change == "unit":
        nine["unit"] = "EUR"
    elif change == "concept":
        nine["concept"] = "http://fasb.org/us-gaap/2025:Revenues"
    elif change == "nil_annual":
        annual["value"] = None
    else:
        nine["value"] = None
    _refused(derive_q4(annual, nine))


def _pair(start: dt.date, fy_days: int, nm_days: int, fy_val: int, nm_val: int,
          fy_dec, nm_dec) -> tuple[dict, dict]:
    common = {"concept": "t:Revenue", "unit": "USD", "period_start": start.isoformat()}
    annual = {**common, "fact_id": 1, "value": Decimal(fy_val), "decimals": fy_dec,
              "period_end": (start + dt.timedelta(days=fy_days)).isoformat(),
              "accession_no": "0000000001-26-000002"}
    nine = {**common, "fact_id": 2, "value": Decimal(nm_val), "decimals": nm_dec,
            "period_end": (start + dt.timedelta(days=nm_days)).isoformat(),
            "accession_no": "0000000001-26-000001"}
    return annual, nine


_starts = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31))
_vals = st.integers(min_value=-10**12, max_value=10**12)
_dec = st.one_of(st.none(), st.integers(min_value=-9, max_value=2))


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(start=_starts, fy_days=st.integers(min_value=350, max_value=380),
       nm_days=st.integers(min_value=252, max_value=280), fy_val=_vals, nm_val=_vals,
       fy_dec=_dec, nm_dec=_dec, k=st.integers(min_value=2, max_value=50))
def test_derived_q4_adds_back_to_the_year(start, fy_days, nm_days, fy_val, nm_val, fy_dec,
                                          nm_dec, k):
    # oracle: R5 by hand: Q4 + 9M = FY when the remainder is 84-119 days, else refused;
    # scaling both values scales Q4; decimals is the coarser (smaller) of the two
    annual, nine = _pair(start, fy_days, nm_days, fy_val, nm_val, fy_dec, nm_dec)
    out = derive_q4(annual, nine)
    remainder = fy_days - nm_days
    if 84 <= remainder <= 119:
        assert out["status"] == "derived"
        assert out["value"] + nine["value"] == annual["value"]
        assert out["days"] == remainder
        known = [d for d in (fy_dec, nm_dec) if d is not None]
        assert out["decimals"] == (min(known) if known else None)
        a2, n2 = _pair(start, fy_days, nm_days, fy_val * k, nm_val * k, fy_dec, nm_dec)
        assert derive_q4(a2, n2)["value"] == out["value"] * k
    else:
        _refused(out)


@pytest.mark.criterion("C5")
@given(start=_starts, fy_days=st.integers(min_value=1, max_value=800),
       nm_days=st.integers(min_value=1, max_value=800))
def test_only_annual_and_nine_month_lengths_derive(start, fy_days, nm_days):
    # oracle: R5.4/5 with the band table: annual 350-380, nine months 252-280, Q4 84-119
    annual, nine = _pair(start, fy_days, nm_days, 1000, 600, -6, -6)
    ok = 350 <= fy_days <= 380 and 252 <= nm_days <= 280 and 84 <= fy_days - nm_days <= 119
    out = derive_q4(annual, nine)
    if ok:
        assert out["status"] == "derived" and out["value"] == 400
    else:
        _refused(out)
