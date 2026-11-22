"""C4: ``num_values`` types the num rows and keeps only consolidated ones by default.

Oracle: SEC readme (num: segments "Tags used to represent axis and member reporting";
coreg "NULL indicates the consolidated entity"; value may be NULL; ddate yyyymmdd), the
committed rows parsed independently by conftest.raw_rows, and CEG's rendered balance sheet.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import load_tables, num_values


@pytest.mark.criterion("C4")
def test_ceg_total_assets_consolidated_vs_coregistrant(kit):
    # oracle: hand check — CEG FY2023 10-K (ceg-20231231.htm), consolidated balance sheets,
    # "Total assets (a)" at December 31, 2023: Constellation Energy Corporation $50,758 million;
    # Constellation Energy Generation, LLC (the co-registrant) $50,738 million.
    tables = load_tables(kit.dirs)
    cons = [v for v in num_values(tables, kit.CEG_23, "Assets") if v["ddate"] == "2023-12-31"]
    assert [(v["value"], v["qtrs"], v["uom"], v["segments"], v["coreg"]) for v in cons] == [
        (50_758_000_000.0, 0, "USD", None, None)]
    every = [
        v for v in num_values(tables, kit.CEG_23, "Assets", consolidated=False)
        if v["ddate"] == "2023-12-31"
    ]
    co = [v for v in every if v["coreg"] == "ConstellationEnergyGenerationLLC"]
    assert [v["value"] for v in co] == [50_738_000_000.0]
    assert co[0]["segments"] == "LegalEntity=ConstellationEnergyGenerationLLC;"
    assert len(every) > len(cons)


@pytest.mark.criterion("C4")
def test_aapl_net_sales_segments_are_excluded(kit):
    # oracle: hand check — AAPL FY2024 10-K (aapl-20240928.htm), income statement "Total net
    # sales" 391,035 / 383,285 / 394,328 for 2024 / 2023 / 2022; the iPhone, Mac, ... and
    # geographic splits are segment rows (ProductOrService=, BusinessSegments=).
    tables = load_tables(kit.dirs)
    cons = num_values(tables, kit.AAPL_24, "RevenueFromContractWithCustomerExcludingAssessedTax")
    assert sorted((v["ddate"], v["value"]) for v in cons) == [
        ("2022-09-30", 394_328_000_000.0),
        ("2023-09-30", 383_285_000_000.0),
        ("2024-09-30", 391_035_000_000.0),
    ]
    assert all(v["custom"] is False and v["version"] == "us-gaap/2024" for v in cons)
    every = num_values(
        tables, kit.AAPL_24, "RevenueFromContractWithCustomerExcludingAssessedTax",
        consolidated=False,
    )
    assert len(every) == 45  # 3 consolidated + 42 segment rows in the filing's num excerpt
    americas = "BusinessSegments=AmericasSegment;"
    assert any(v["segments"] and americas in v["segments"] for v in every)


@pytest.mark.criterion("C4")
def test_every_fixture_row_is_typed_from_its_raw_row(kit):
    # oracle: readme field formats; raw rows parsed by conftest.raw_rows
    tables = load_tables(kit.dirs)
    raw = kit.raw_rows("num")
    for adsh in sorted({r["adsh"] for r in raw}):
        got = num_values(tables, adsh, consolidated=False)
        want = [r for r in raw if r["adsh"] == adsh]
        assert len(got) == len(want)
        for g, w in zip(got, want, strict=True):
            d = w["ddate"]
            assert g["ddate"] == f"{d[:4]}-{d[4:6]}-{d[6:]}"
            assert g["qtrs"] == int(w["qtrs"])
            for k in ("adsh", "tag", "version", "uom"):
                assert g[k] == w[k]
            assert g["custom"] is (w["version"] == adsh)
            assert g["segments"] == (w["segments"] or None)
            assert g["coreg"] == (w["coreg"] or None)
            assert g["value"] == (float(w["value"]) if w["value"] else None)
        cons = num_values(tables, adsh)
        assert cons == [g for g in got if g["segments"] is None and g["coreg"] is None]


@pytest.mark.criterion("C4")
def test_nil_value_is_none_not_zero(kit):
    # oracle: readme — value "May be NULL"; SEC's CEG FY2023 num.txt has exactly such rows
    # (CommitmentsAndContingencies, 20231231, qtrs 0, empty value): a nil fact, never 0.
    row = [kit.CEG_23, "CommitmentsAndContingencies", "us-gaap/2023", "20231231", "0", "USD",
           "", "", "", ""]
    zero = [kit.CEG_23, "OtherLiabilitiesNoncurrent", "us-gaap/2023", "20231231", "0", "USD",
            "", "", "0.0000", ""]
    with tempfile.TemporaryDirectory() as tmp:
        kit.write_tables(Path(tmp), {
            "sub": (kit.SUB_HEADER, []), "pre": (kit.PRE_HEADER, []), "tag": (kit.TAG_HEADER, []),
            "num": (kit.NUM_HEADER, [row, zero]),
        })
        tables = load_tables([Path(tmp)])
    got = num_values(tables, kit.CEG_23)
    assert [(v["tag"], v["value"]) for v in got] == [
        ("CommitmentsAndContingencies", None), ("OtherLiabilitiesNoncurrent", 0.0)]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(seed=st.integers(0, 100_000), which=st.integers(0, 8))
def test_row_order_does_not_change_the_set(kit, seed, which):
    # oracle: requirement 5 — rows are filtered independently; shuffling the table permutes
    # the result and nothing else
    tables = load_tables(kit.dirs)
    adsh = sorted({r["adsh"] for r in kit.raw_rows("num")})[which]
    base = num_values(tables, adsh, consolidated=False)
    assert base
    shuffled = dict(tables)
    rows = list(tables["num"])
    random.Random(seed).shuffle(rows)
    shuffled["num"] = rows

    def key(v):
        cols = ("tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value")
        return tuple(str(v[k]) for k in cols)

    every = num_values(shuffled, adsh, consolidated=False)
    assert sorted(map(key, every)) == sorted(map(key, base))
    cons = num_values(shuffled, adsh)
    assert sorted(map(key, cons)) == sorted(map(key, num_values(tables, adsh)))
