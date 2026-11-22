"""C4: single-filer keys — lookup (S1 latest filed), point-in-time (as reported in the
filing labelled F), ratio (S4, computed here from keyed inputs) and the reporting currency.

Oracle: requirement 9 of docs/specs/v2-suite-build.md, values read off the conftest
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


def _lookup(t, y, m):
    return Intent("lookup", tickers=[t], fiscal_years=[y], metrics=[m])


def _pit(t, y, f, m):
    return Intent("point_in_time", tickers=[t], fiscal_years=[y], filing_year=f, metrics=[m])


@pytest.mark.criterion("C4")
def test_lookup_is_the_latest_filed_recast(kit):
    # oracle: payload — AAA FY2022 revenue (2021-09-26..2022-09-24) is 100bn in A22 and A23
    # and 102bn in A24 (filed 2024-11-01, the latest): the key is 102bn from A24
    keyed = _key(kit, _lookup("AAA", 2022, "Revenue"))
    assert set(keyed) == kit.KEYED_FIELDS
    assert keyed["shape"] == "lookup"
    assert keyed["intent"] == _lookup("AAA", 2022, "Revenue")
    assert keyed["value"] == 102 * kit.BN
    assert keyed["rows"] is None
    assert keyed["not_meaningful"] is False
    assert keyed["definition"] is None and keyed["same_filing"] is None
    assert keyed["disclosures"] is None and keyed["subject"] is None
    (k,) = keyed["inputs"]
    assert set(k) == kit.KEY_FIELDS
    assert k["accession"] == kit.A24
    assert (k["period_start"], k["period_end"]) == kit.P22
    assert k["concept"] == "us-gaap:Revenues" and k["unit"] == "USD"
    assert k["fiscal_year"] == 2022
    assert keyed["key_source"] == "sec_companyfacts"
    assert keyed["policies"] == ["S1", "S2", "S6"]


@pytest.mark.criterion("C4")
def test_point_in_time_reads_the_filing_labelled_f(kit):
    # oracle: payload — the FY2023 10-K (A23, the only fy=2023 annual filing) reports FY2022
    # revenue as 100bn; the FY2024 10-K (A24) as 102bn
    in_23 = _key(kit, _pit("AAA", 2022, 2023, "Revenue"))
    assert in_23["value"] == 100 * kit.BN
    assert in_23["inputs"][0]["accession"] == kit.A23
    assert in_23["inputs"][0]["selection"] == "as_reported"
    assert (in_23["inputs"][0]["period_start"], in_23["inputs"][0]["period_end"]) == kit.P22
    in_24 = _key(kit, _pit("AAA", 2022, 2024, "Revenue"))
    assert in_24["value"] == 102 * kit.BN
    assert in_24["inputs"][0]["accession"] == kit.A24


@pytest.mark.criterion("C4")
def test_late_amendment_wins_the_lookup_not_the_point_in_time(kit):
    # oracle: payload — BBB FY2023 revenue: B23 200bn (2024-02-15), B24 205bn (2025-02-14),
    # B23A 206bn (10-K/A, 2025-03-01, latest). The lookup is 206bn; the FY2024 10-K (the
    # only fy=2024 filing, B24) reports 205bn
    lookup = _key(kit, _lookup("BBB", 2023, "Revenue"))
    assert lookup["value"] == 206 * kit.BN
    assert lookup["inputs"][0]["accession"] == kit.B23A
    assert lookup["inputs"][0]["form"] == "10-K/A"
    pit = _key(kit, _pit("BBB", 2023, 2024, "Revenue"))
    assert pit["value"] == 205 * kit.BN
    assert pit["inputs"][0]["accession"] == kit.B24


@pytest.mark.criterion("C4")
def test_twd_filer_is_keyed_in_its_reporting_currency(kit):
    # oracle: payload, S6 — TWX's 20-F reports 2_894_307_699_000 TWD; never converted or scaled
    keyed = _key(kit, _lookup("TWX", 2024, "Revenue"))
    assert keyed["value"] == 2_894_307_699_000
    assert keyed["inputs"][0]["unit"] == "TWD"
    assert keyed["inputs"][0]["concept"] == "ifrs-full:Revenue"


@pytest.mark.criterion("C4")
def test_ratio_is_computed_from_keyed_inputs(kit):
    # oracle: payload, S4 net margin = net income / revenue: AAA FY2024 12bn / 121bn
    intent = Intent("compute", operation="ratio", tickers=["AAA"], fiscal_years=[2024],
                    metrics=["NetIncome", "Revenue"])
    keyed = _key(kit, intent)
    # exact quotient of two keyed integers; rel 1e-12 allows only float rounding
    assert keyed["value"] == pytest.approx(12 / 121, rel=1e-12)
    assert keyed["shape"] == "compute.ratio"
    assert keyed["definition"] == "net_margin"
    assert "S4" in keyed["policies"]
    assert [k["concept"] for k in keyed["inputs"]] == ["us-gaap:NetIncomeLoss",
                                                       "us-gaap:Revenues"]
    assert [k["value"] for k in keyed["inputs"]] == [12 * kit.BN, 121 * kit.BN]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(v=st.integers(min_value=1, max_value=10**13))
def test_a_later_recast_moves_the_lookup_not_the_point_in_time(kit, v):
    # oracle: requirement 9 — a 10-K filed after A24 that reports FY2022 at v becomes the
    # latest filed (S1), while the filing labelled 2023 is still A23 (100bn)
    docs = kit.companyfacts_docs()
    start, end = kit.P22
    docs[kit.AAA_CIK]["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": start, "end": end, "val": v, "accn": "0000900101-25-000099", "fy": 2025,
         "fp": "FY", "form": "10-K", "filed": "2025-10-30"})
    assert _key(kit, _lookup("AAA", 2022, "Revenue"), docs)["value"] == v
    assert _key(kit, _pit("AAA", 2022, 2023, "Revenue"), docs)["value"] == 100 * kit.BN
