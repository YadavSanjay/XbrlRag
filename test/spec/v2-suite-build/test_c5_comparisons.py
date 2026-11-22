"""C5: comparisons (growth, difference, CAGR, trend) read one filing where it reports every
period (S1), else each period's own key, and follow S4's definitions and "not meaningful".

Oracle: requirement 9 of docs/specs/v2-suite-build.md, S1, S4; values read off the conftest
payloads by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import key_intent


def _key(kit, intent, docs=None):
    docs = kit.companyfacts_docs() if docs is None else docs
    return key_intent(intent, kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs))


def _cmp(op, t, years, m):
    return Intent("compute", operation=op, tickers=[t], fiscal_years=years, metrics=[m])


def _trend(t, m):
    return Intent("trend", tickers=[t], fiscal_years=[2022, 2023, 2024], metrics=[m])


@pytest.mark.criterion("C5")
def test_growth_reads_both_years_from_the_latest_filing(kit):
    # oracle: payload — AAA FY2023 revenue's key is A24's 111bn; A24 also reports FY2022 as
    # 102bn, so growth = (111 - 102) / 102 from one filing
    keyed = _key(kit, _cmp("yoy_growth", "AAA", [2022, 2023], "Revenue"))
    # arithmetic on keyed integers; rel 1e-12 allows only float rounding
    assert keyed["value"] == pytest.approx(9 / 102, rel=1e-12)
    assert keyed["shape"] == "compute.yoy_growth"
    assert keyed["definition"] == "growth"
    assert keyed["same_filing"] is True
    assert keyed["not_meaningful"] is False
    assert [k["accession"] for k in keyed["inputs"]] == [kit.A24, kit.A24]
    assert [(k["period_start"], k["period_end"]) for k in keyed["inputs"]] == [kit.P22, kit.P23]
    assert "S4" in keyed["policies"]


@pytest.mark.criterion("C5")
def test_growth_ignores_a_later_amendment_of_the_prior_year(kit):
    # oracle: payload — BBB FY2024 revenue 220bn from B24, which reports FY2023 as 205bn.
    # FY2023's own latest key is B23A's 206bn; one filing wins: (220 - 205) / 205
    keyed = _key(kit, _cmp("yoy_growth", "BBB", [2023, 2024], "Revenue"))
    assert keyed["value"] == pytest.approx(15 / 205, rel=1e-12)  # float rounding only
    assert keyed["value"] != pytest.approx(14 / 206, rel=1e-6)  # not the mixed-basis growth
    assert [k["accession"] for k in keyed["inputs"]] == [kit.B24, kit.B24]


@pytest.mark.criterion("C5")
def test_growth_from_a_negative_base_is_not_meaningful(kit):
    # oracle: payload, S4 — AAA net income FY2022 -5bn, FY2023 8bn (both in A24):
    # prior <= 0, so not meaningful, value (8 - (-5)) / |-5| = 13 / 5
    keyed = _key(kit, _cmp("yoy_growth", "AAA", [2022, 2023], "NetIncome"))
    assert keyed["not_meaningful"] is True
    assert keyed["value"] == pytest.approx(13 / 5, rel=1e-12)  # float rounding only


@pytest.mark.criterion("C5")
def test_cagr(kit):
    # oracle: S4 — CAGR only when both ends are positive. Net income FY2022 is -5bn: not
    # meaningful, value None. Revenue FY2022 102bn (A24), FY2024 121bn: (121/102)^(1/2) - 1
    nm = _key(kit, _cmp("cagr", "AAA", [2022, 2024], "NetIncome"))
    assert nm["not_meaningful"] is True
    assert nm["value"] is None
    assert nm["definition"] == "cagr"
    rev = _key(kit, _cmp("cagr", "AAA", [2022, 2024], "Revenue"))
    assert rev["not_meaningful"] is False
    assert rev["value"] == pytest.approx((121 / 102) ** 0.5 - 1, rel=1e-12)  # rounding only
    assert rev["same_filing"] is True


@pytest.mark.criterion("C5")
def test_difference(kit):
    # oracle: payload — A24 reports FY2022 102bn and FY2024 121bn: 19bn
    keyed = _key(kit, _cmp("difference", "AAA", [2022, 2024], "Revenue"))
    assert keyed["value"] == 19 * kit.BN
    assert keyed["definition"] == "difference"
    assert keyed["not_meaningful"] is False


@pytest.mark.criterion("C5")
def test_trend_falls_back_to_own_keys_when_one_filing_misses_a_period(kit):
    # oracle: payload — total assets: A24 reports 2024-09-28 (350bn) and 2023-09-30 (321bn)
    # but not 2022-09-24, so every year uses its own latest key: 2022-09-24 is A23's 301bn
    keyed = _key(kit, _trend("AAA", "TotalAssets"))
    assert keyed["same_filing"] is False
    assert keyed["value"] is None
    assert keyed["rows"] == [
        {"entity": "FY2022", "fiscal_year": 2022, "value": 301 * kit.BN,
         "period_end": "2022-09-24"},
        {"entity": "FY2023", "fiscal_year": 2023, "value": 321 * kit.BN,
         "period_end": "2023-09-30"},
        {"entity": "FY2024", "fiscal_year": 2024, "value": 350 * kit.BN,
         "period_end": "2024-09-28"},
    ]
    assert [k["accession"] for k in keyed["inputs"]] == [kit.A23, kit.A24, kit.A24]


def _scaled(kit, k):
    docs = kit.companyfacts_docs()
    for e in docs[kit.AAA_CIK]["facts"]["us-gaap"]["Revenues"]["units"]["USD"]:
        e["val"] = e["val"] * k
    return docs


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(k=st.integers(min_value=2, max_value=1000))
def test_scaling_the_metric(kit, k):
    # oracle: S4 — growth and CAGR are scale-free; a difference and a trend scale with the
    # values. Base values: revenue FY2022 102bn, FY2023 111bn, FY2024 121bn (all from A24)
    docs = _scaled(kit, k)
    growth = _key(kit, _cmp("yoy_growth", "AAA", [2023, 2024], "Revenue"), docs)
    assert growth["value"] == pytest.approx(10 / 111, rel=1e-12)  # float rounding only
    cagr = _key(kit, _cmp("cagr", "AAA", [2022, 2024], "Revenue"), docs)
    assert cagr["value"] == pytest.approx((121 / 102) ** 0.5 - 1, rel=1e-12)  # rounding only
    diff = _key(kit, _cmp("difference", "AAA", [2022, 2024], "Revenue"), docs)
    assert diff["value"] == 19 * kit.BN * k
    trend = _key(kit, _trend("AAA", "Revenue"), docs)
    assert [r["value"] for r in trend["rows"]] == [102 * kit.BN * k, 111 * kit.BN * k,
                                                    121 * kit.BN * k]
    assert trend["same_filing"] is True
