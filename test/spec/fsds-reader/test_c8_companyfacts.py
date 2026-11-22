"""C8: for standard tags the reader agrees with SEC's companyfacts API.

Oracle: SEC companyfacts (https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json),
SEC's other extraction of the same filings, committed as an excerpt of the fixture
accessions' entries. Agreement is relative 1e-9: FSDS keeps four decimals, companyfacts the
XBRL value, and every amount here is a whole number or a two-decimal per-share figure.
"""

from __future__ import annotations

import pytest

from src.eval.sec_reference.fsds import fsds_period, fsds_uom, load_tables, num_values, period_value

# tolerance: FSDS values are the filed amounts (at most 4 decimals); rel 1e-12 absorbs only
# binary floating-point representation, so a wrong figure can never pass.
REL = 1e-12

TAXONOMY = {"us-gaap": "us-gaap", "ifrs": "ifrs-full", "srt": "srt", "dei": "dei"}


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def _entries(kit, cik: str, version: str, tag: str):
    tax = TAXONOMY[version.split("/")[0]]
    return kit.companyfacts["facts_by_cik"].get(cik, {}).get(tax, {}).get(tag, {})


@pytest.mark.criterion("C8")
def test_aapl_net_sales_and_eps_agree(kit):
    # oracle: companyfacts AAPL us-gaap RevenueFromContractWithCustomerExcludingAssessedTax, USD,
    # accn 0000320193-24-000123, 2023-10-01..2024-09-28: 391,035,000,000; EarningsPerShareBasic
    # USD/shares 6.11 (both also hand-checked on the rendered income statement)
    tables = load_tables(kit.dirs)
    rev = "RevenueFromContractWithCustomerExcludingAssessedTax"
    units = _entries(kit, "320193", "us-gaap/2024", rev)
    cf = [
        e for e in units["USD"]
        if e["accn"] == kit.AAPL_24 and e.get("start") == "2023-10-01" and e["end"] == "2024-09-28"
    ]
    assert [e["val"] for e in cf] == [391035000000]
    assert period_value(tables, kit.AAPL_24, "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "2023-10-01", "2024-09-28", "USD") == cf[0]["val"]
    eps = [e for e in _entries(kit, "320193", "us-gaap/2024", "EarningsPerShareBasic")["USD/shares"]
           if e["accn"] == kit.AAPL_24 and e["end"] == "2024-09-28"]
    assert [e["val"] for e in eps] == [6.11]
    fy24 = ("2023-10-01", "2024-09-28")
    got = period_value(tables, kit.AAPL_24, "EarningsPerShareBasic", *fy24, "USD/shares")
    assert got == pytest.approx(6.11, rel=REL)


@pytest.mark.criterion("C8")
def test_tsm_ifrs_revenue_agrees_in_twd(kit):
    # oracle: companyfacts TSM ifrs-full Revenue, TWD, accn 0001193125-25-083423, 2024:
    # 2,894,307,700,000 (hand-checked: NT$2,894,307.7 million on the 20-F)
    tables = load_tables(kit.dirs)
    twd = [e for e in _entries(kit, "1046179", "ifrs/2024", "Revenue")["TWD"]
           if e["accn"] == kit.TSM_24 and e.get("start") == "2024-01-01"
           and e["end"] == "2024-12-31"]
    assert [e["val"] for e in twd] == [2894307700000]
    got = period_value(tables, kit.TSM_24, "Revenue", "2024-01-01", "2024-12-31", "TWD")
    assert got == twd[0]["val"]


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_every_consolidated_standard_row_has_an_agreeing_companyfacts_entry(kit):
    # oracle: companyfacts excerpt — every non-dimensional fact of a filing is listed there under
    # its accession; the row set is fixed independently by conftest.raw_rows
    tables = load_tables(kit.dirs)
    ciks = {r["adsh"]: r["cik"] for r in kit.raw_rows("sub")}
    with_cf = set(kit.companyfacts["facts_by_cik"])
    expected_rows = [
        r for r in kit.raw_rows("num")
        if not r["segments"] and not r["coreg"] and r["value"] and r["version"] != r["adsh"]
        and ciks[r["adsh"]] in with_cf
    ]
    assert len(expected_rows) == 385  # consolidated standard rows of the filers companyfacts covers
    checked = 0
    for adsh in sorted({r["adsh"] for r in expected_rows}):
        cik = ciks[adsh]
        for v in num_values(tables, adsh):
            if v["custom"] or v["value"] is None:
                continue
            units = _entries(kit, cik, v["version"], v["tag"])
            matches = [
                e for unit, es in units.items() if fsds_uom(unit) == v["uom"]
                for e in es
                if e["accn"] == adsh
                and fsds_period(e.get("start"), e["end"]) == (v["ddate"], v["qtrs"])
            ]
            assert matches, v
            assert any(_agree(float(e["val"]), v["value"]) for e in matches), (v, matches)
            checked += 1
    assert checked == len(expected_rows)


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_period_value_reproduces_each_matching_companyfacts_entry(kit):
    # oracle: companyfacts excerpt values; an entry whose period SEC's num carries must come
    # back from period_value with the same value
    tables = load_tables(kit.dirs)
    raw_keys = {
        (r["adsh"], r["tag"], r["ddate"], r["qtrs"], r["uom"])
        for r in kit.raw_rows("num") if not r["segments"] and not r["coreg"] and r["value"]
    }
    n = 0
    for _cik, taxes in kit.companyfacts["facts_by_cik"].items():
        for _tax, tags in taxes.items():
            for tag, units in tags.items():
                for unit, entries in units.items():
                    for e in entries:
                        ddate, qtrs = fsds_period(e.get("start"), e["end"])
                        key = (e["accn"], tag, ddate.replace("-", ""), str(qtrs), fsds_uom(unit))
                        if key not in raw_keys:
                            continue
                        got = period_value(tables, e["accn"], tag, e.get("start"), e["end"], unit)
                        assert got is not None and _agree(got, float(e["val"])), (tag, e)
                        n += 1
    assert n > 300
