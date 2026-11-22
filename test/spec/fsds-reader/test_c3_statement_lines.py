"""C3: ``statement_lines`` and ``is_custom`` read a filing's presentation rows.

Oracle: SEC readme (pre: report, line, stmt, inpth, tag, version "The taxonomy identifier if
the tag is a standard tag, otherwise adsh", plabel, negating; tag: custom "1 if tag is
custom (version=adsh)"), the committed rows, and the rendered statements checked by hand
in our local EDGAR copies (data/storage/sec_edgar/<ticker>/<fy>/<form>/).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_reference.fsds import is_custom, load_tables, statement_lines


@pytest.mark.criterion("C3")
def test_msft_extension_line_is_custom(kit):
    # oracle: hand check — MSFT FY2024 10-K (msft-20240630.htm), cash flows statement, line
    # "Depreciation, amortization, and other" printed 22,287 for 2024, tagged
    # msft:DepreciationAmortizationAndOther; SEC writes version = the accession (readme).
    tables = load_tables(kit.dirs)
    lines = statement_lines(tables, kit.MSFT_24, "CF")
    dep = [ln for ln in lines if ln["tag"] == "DepreciationAmortizationAndOther"]
    assert len(dep) == 1
    assert dep[0]["custom"] is True
    assert dep[0]["version"] == kit.MSFT_24
    assert dep[0]["plabel"] == "Depreciation, amortization, and other"
    assert (dep[0]["report"], dep[0]["line"]) == (6, 4)
    assert dep[0]["negating"] is False
    assert dep[0]["inpth"] is False
    tag_row = [r for r in kit.raw_rows("tag")
               if r["tag"] == "DepreciationAmortizationAndOther" and r["version"] == kit.MSFT_24]
    assert [r["custom"] for r in tag_row] == ["1"]


@pytest.mark.criterion("C3")
def test_avgo_income_statement_order_and_extensions(kit):
    # oracle: hand check — AVGO FY2024 10-K (avgo-20241103.htm), consolidated statements of
    # operations: "Total net revenue", then cost lines "Cost of products sold" (9,797),
    # "Cost of subscriptions and services", amortization, restructuring, "Total cost of
    # revenue"; the four cost lines are avgo: extensions, the total is us-gaap:CostOfRevenue.
    tables = load_tables(kit.dirs)
    lines = statement_lines(tables, kit.AVGO_24, "IS")
    assert [ln["line"] for ln in lines] == sorted(ln["line"] for ln in lines)
    head = [(ln["line"], ln["tag"], ln["custom"]) for ln in lines[:6]]
    assert head == [
        (7, "RevenueFromContractWithCustomerExcludingAssessedTax", False),
        (9, "CostofProductsSold", True),
        (10, "CostofSubscriptionsandServices", True),
        (11, "Amortizationofacquisitionrelatedintangibleassetscostofproductssold", True),
        (12, "RestructuringChargesCostOfProductsSold", True),
        (13, "CostOfRevenue", False),
    ]
    assert lines[1]["plabel"] == "Cost of products sold"
    assert len(lines) == 26
    assert sum(ln["custom"] for ln in lines) == 5


@pytest.mark.criterion("C3")
def test_orcl_custom_revenue_lines_and_tsm_ifrs_versions(kit):
    # oracle: hand check — ORCL FY2025 10-K (orcl-20250531.htm), income statements: first line
    # "Cloud services and license support" 44,029 (orcl: extension), "Total revenues" 57,399
    # (us-gaap). TSM 2024 20-F: standard lines carry SEC's IFRS version "ifrs/2024".
    tables = load_tables(kit.dirs)
    orcl = statement_lines(tables, kit.ORCL_25, "IS")
    first = (orcl[0]["line"], orcl[0]["tag"], orcl[0]["custom"])
    assert first == (2, "CloudServicesAndLicenseSupportRevenue", True)
    total = [ln for ln in orcl if ln["plabel"] == "Total revenues"]
    assert [(ln["tag"], ln["version"], ln["custom"]) for ln in total] == [
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap/2024", False)]
    tsm = statement_lines(tables, kit.TSM_24, "IS")
    rev = [ln for ln in tsm if ln["tag"] == "Revenue"]
    got = [(ln["version"], ln["custom"], ln["plabel"]) for ln in rev]
    assert got == [("ifrs/2024", False, "NET REVENUE")]
    custom = {ln["tag"] for ln in tsm if ln["custom"]}
    assert custom >= {"InvestmentsIncomeInterest", "NetForeignExchangeGainLoss"}


@pytest.mark.criterion("C3")
def test_ceg_parenthetical_line_is_kept_and_flagged(kit):
    # oracle: hand check — CEG FY2023 10-K (ceg-20231231.htm): "Total assets (a)" on the
    # balance sheet (report 5) and again on the parenthetical page (report 6, footnote (a)
    # "Our consolidated assets include $3,355 million ..."); SEC flags the latter inpth=1.
    tables = load_tables(kit.dirs)
    bs = statement_lines(tables, kit.CEG_23, "BS")
    assert [(ln["report"], ln["line"], ln["inpth"]) for ln in bs] == [(5, 24, False), (6, 18, True)]
    assert all(ln["tag"] == "Assets" and ln["stmt"] == "BS" for ln in bs)


@pytest.mark.criterion("C3")
def test_negating_flags_come_from_the_rows(kit):
    # oracle: hand check — AAPL FY2024 cash flows show "Other" (2,266) and "Payments for
    # acquisition of property, plant and equipment" (9,447) in parentheses; SEC negating=1.
    tables = load_tables(kit.dirs)
    cf = statement_lines(tables, kit.AAPL_24, "CF")
    assert [(ln["tag"], ln["negating"]) for ln in cf] == [
        ("OtherNoncashIncomeExpense", True),
        ("PaymentsToAcquirePropertyPlantAndEquipment", True),
    ]


@pytest.mark.criterion("C3")
def test_every_fixture_line_matches_its_raw_row(kit):
    # oracle: readme — custom iff version == adsh, agreeing with tag.custom; flags "1"/"0";
    # sorted by (report, line); compared with conftest.raw_rows parsed independently
    tables = load_tables(kit.dirs)
    custom_flag = {(r["tag"], r["version"]): r["custom"] for r in kit.raw_rows("tag")}
    raw = kit.raw_rows("pre")
    adshs = sorted({r["adsh"] for r in raw})
    assert len(adshs) == 9
    for adsh in adshs:
        got = statement_lines(tables, adsh)
        want = sorted(
            (r for r in raw if r["adsh"] == adsh),
            key=lambda r: (int(r["report"]), int(r["line"])),
        )
        assert len(got) == len(want)
        for g, w in zip(got, want, strict=True):
            assert g["adsh"] == adsh
            assert (g["report"], g["line"]) == (int(w["report"]), int(w["line"]))
            for k in ("stmt", "tag", "version", "plabel"):
                assert g[k] == w[k]
            assert g["inpth"] is (w["inpth"] == "1")
            assert g["negating"] is (w["negating"] == "1")
            assert g["custom"] is (w["version"] == adsh)
            assert g["custom"] is (custom_flag[(w["tag"], w["version"])] == "1")


@pytest.mark.criterion("C3")
@given(version=st.text(max_size=25), adsh=st.from_regex(r"\d{10}-\d{2}-\d{6}", fullmatch=True))
def test_is_custom_is_version_equals_adsh(version, adsh):
    # oracle: readme tag.custom "1 if tag is custom (version=adsh)"
    assert is_custom(adsh, adsh) is True
    assert is_custom(version, adsh) is (version == adsh)
    assert is_custom("us-gaap/2024", adsh) is False


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(stmt=st.sampled_from(["IS", "CF", "BS", "EQ"]), which=st.integers(0, 8))
def test_stmt_filter_is_the_matching_subset(kit, stmt, which):
    # oracle: requirement 4 — filtering by stmt keeps exactly the unfiltered lines of that stmt
    tables = load_tables(kit.dirs)
    adsh = sorted({r["adsh"] for r in kit.raw_rows("pre")})[which]
    every = statement_lines(tables, adsh)
    assert every  # each fixture accession has statement lines
    assert statement_lines(tables, adsh, stmt) == [ln for ln in every if ln["stmt"] == stmt]
