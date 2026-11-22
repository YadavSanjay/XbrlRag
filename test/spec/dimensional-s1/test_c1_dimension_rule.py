"""C1: the S1 rule with every dimension in the group (docs/specs/dimensional-s1.md R1-R2)."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.numerics.restatement import (
    current_column,
    dimension_identity,
    latest_dimensional_restatement,
    latest_restatement,
)

OP = "OperatingIncomeLoss"
REV_I = "RevenueFromContractWithCustomerExcludingAssessedTax"
CCG_NOTEBOOK = ("BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
                "ProductOrService=Notebook;")
CCG_CE = "BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"


def _answer(kit, rows, fy):
    return latest_dimensional_restatement(current_column(rows, fy), rows)


def _pick(kit, answer, tag, segments, ticker):
    """The answer's rows for one measure, by the fixture's own identity (oracle side)."""
    want = tuple(sorted((kit.local(kit.AXIS_QNAME[a]),
                         kit.local(kit.member_qname(ticker, a, m)))
                        for a, m in kit.parse_segments(segments)))
    return [r for r in answer if kit.local(r["concept"]) == tag and kit.dims_of(r) == want]


@pytest.mark.criterion("C1")
def test_identity_of_each_row_shape(kit):
    # oracle: requirement 1, by hand from the FSDS segments text of each fact
    rows = kit.store_rows("INTC")
    cube = kit.find(rows, kit.INTC_OWN, REV_I, CCG_NOTEBOOK)
    assert dimension_identity(kit.multidim_view([cube])[0]) == (
        ("ConsolidatedEntitiesAxis", "OperatingSegmentsMember"),
        ("ProductOrServiceAxis", "NotebookMember"),
        ("StatementBusinessSegmentsAxis", "ClientComputingGroupMember"))
    single = kit.find(rows, kit.INTC_OWN, OP, "BusinessSegments=ClientComputingGroup;")
    assert dimension_identity(kit.flat_view([single])[0]) == (
        ("StatementBusinessSegmentsAxis", "ClientComputingGroupMember"),)
    cell = kit.find(rows, kit.INTC_FY24, OP,
                    "BusinessSegments=AllOtherSegments;ConsolidationItems=OperatingSegments;")
    assert dimension_identity(kit.flat_view([cell])[0]) is None
    total = kit.find(rows, kit.INTC_OWN, OP, "")
    assert dimension_identity(kit.flat_view([total])[0]) == ()
    # a URI-qualified axis (the multidim view's JSONB keys) and a Clark name reduce alike
    clark = dict(kit.multidim_view([cube])[0])
    clark["dimensions"] = {"{http://fasb.org/us-gaap/2022}StatementBusinessSegmentsAxis":
                           "http://www.intel.com/20221231:ClientComputingGroupMember",
                           "http://fasb.org/srt/2022:ProductOrServiceAxis":
                           "http://fasb.org/us-gaap/2022:NotebookMember",
                           "http://fasb.org/srt/2022:ConsolidatedEntitiesAxis":
                           "http://fasb.org/us-gaap/2022:OperatingSegmentsMember"}
    assert dimension_identity(clark) == dimension_identity(kit.multidim_view([cube])[0])


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("ticker,fy,tag,segments,value,accession", [
    # oracle: FSDS num of the named filings; the latest later-labelled filing with the
    # same identity answers (INTC: -24 filed 2024-01-26, -25 filed 2025-01-31)
    ("INTC", 2022, OP, "BusinessSegments=ClientComputingGroup;", 8_207e6,
     "0000050863-25-000009"),
    ("INTC", 2022, OP, "BusinessSegments=AllOtherSegments;", -5_977e6,
     "0000050863-24-000010"),
    ("INTC", 2022, OP, "BusinessSegments=AcceleratedComputingSystemsAndGraphics;",
     -1_716e6, "0000050863-23-000006"),
    ("INTC", 2022, REV_I, CCG_NOTEBOOK, 18_781e6, "0000050863-24-000010"),
    ("INTC", 2022, REV_I, CCG_CE, 31_773e6, "0000050863-24-000010"),
    ("NVDA", 2023, "Revenues", "BusinessSegments=Graphics;ConsolidationItems=OperatingSegments;",
     11_906e6, "0001045810-24-000029"),
    ("NVDA", 2023, "Revenues", "Geographical=TW;", 6_986e6, "0001045810-25-000023"),
    ("NVDA", 2023, "Revenues", "Geographical=OtherCountries;", 3_623e6,
     "0001045810-25-000023"),
], ids=["INTC-CCG-opinc", "INTC-AllOther-not-the-cell", "INTC-AXG-own", "INTC-Notebook-3dim",
        "INTC-CCG-revenue-2dim", "NVDA-Graphics-renamed-later", "NVDA-TW", "NVDA-Other"])
def test_the_latest_report_with_the_same_dimensions_answers(kit, ticker, fy, tag, segments,
                                                            value, accession):
    # oracle: FSDS num (value, abs=5e5: amounts tagged in millions); accession by hand
    rows = _realistic(kit, kit.store_rows(ticker))
    got = _pick(kit, _answer(kit, rows, fy), tag, segments, ticker)
    assert len(got) == 1
    assert got[0]["value"] == pytest.approx(value, abs=5e5)  # millions precision
    assert got[0]["accession_no"] == accession


@pytest.mark.criterion("C1")
def test_a_cell_of_the_flat_view_never_restates_a_member(kit):
    # oracle: requirement 1 - the flat view files Intel's FY2024 10-K cell "All other x
    # Operating segments" (1,174) under All other; its other dimension is unknown there, so
    # the FY2023 10-K's single-dimension -5,977 restates the own -5,614 (FSDS num)
    rows = kit.flat_view(kit.store_rows("INTC", family="OperatingIncome"))
    got = _pick(kit, _answer(kit, rows, 2022), OP, "BusinessSegments=AllOtherSegments;", "INTC")
    assert [(g["accession_no"], g["value"]) for g in got] == [(kit.INTC_FY23, -5_977e6)]
    # a flat-view cell in the current rows is returned unchanged
    cell = kit.flat_view([kit.find(
        kit.store_rows("INTC"), kit.INTC_FY24, OP,
        "BusinessSegments=AllOtherSegments;ConsolidationItems=OperatingSegments;")])[0]
    assert latest_dimensional_restatement([cell], rows) == [cell]


@pytest.mark.criterion("C1")
def test_a_member_only_a_later_filing_reports_is_not_added(kit):
    # oracle: requirement 2 (and D1): NVDA's FY2025 10-K splits Singapore out of "Other
    # countries" for FY2023; the FY2023 10-K's own column has no Singapore member
    rows = kit.store_rows("NVDA")
    answer = _answer(kit, rows, 2023)
    assert _pick(kit, answer, "Revenues", "Geographical=SG;", "NVDA") == []
    # every answering row is a group the own column reports
    own = {(kit.local(r["concept"]), kit.dims_of(r)) for r in current_column(rows, 2023)}
    assert {(kit.local(r["concept"]), kit.dims_of(r)) for r in answer} <= own


def _hand_rule(kit, rows, ticker, fy):
    """S1 by hand: per own-column fact, the latest later filing with the same identity."""
    own_acc = {"INTC": kit.INTC_OWN, "NVDA": kit.NVDA_OWN}[ticker]
    out = set()
    for r in rows:
        if r["accession_no"] != own_acc or not r["is_current_period"]:
            continue
        later = [c for c in rows
                 if c["fiscal_year"] > fy and kit.local(c["concept"]) == kit.local(r["concept"])
                 and kit.dims_of(c) == kit.dims_of(r) and c["unit"] == r["unit"]
                 and c["period_start"] == r["period_start"]
                 and c["period_end"] == r["period_end"]]
        best = max(later, key=lambda c: (c["filed_date"], c["accession_no"])) if later else r
        out.add((best["accession_no"], best["value"]))
    return sorted(out)


def _realistic(kit, full):
    """What the lookup reads: flat-view rows of single facts, multidim-view rows of cells."""
    singles = [r for r in full if not r["has_multiple_dimensions"]]
    return kit.flat_view(singles) + kit.multidim_view(full)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.sampled_from(["INTC", "NVDA"]), st.lists(st.booleans(), min_size=2, max_size=2),
       st.integers(min_value=0, max_value=2**32 - 1))
def test_any_later_filings_and_order(kit, ticker, keep, seed):
    # oracle: the hand rule above over the same rows (requirement 2)
    later = {"INTC": (kit.INTC_FY23, kit.INTC_FY24), "NVDA": (kit.NVDA_FY24, kit.NVDA_FY25)}
    fy = kit.YEAR[ticker]
    own = {"INTC": kit.INTC_OWN, "NVDA": kit.NVDA_OWN}[ticker]
    accs = [own] + [a for a, k in zip(later[ticker], keep, strict=True) if k]
    full = kit.store_rows(ticker, accessions=accs)
    # the answer reads the multidim view's rows for cells and the flat view's for the rest
    rows = kit.flat_view([r for r in full if not r["has_multiple_dimensions"]]) \
        + kit.multidim_view(full)
    random.Random(seed).shuffle(rows)
    got = sorted({(r["accession_no"], r["value"]) for r in _answer(kit, rows, fy)})
    assert got == _hand_rule(kit, full, ticker, fy)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(st.lists(st.booleans(), min_size=2, max_size=2),
       st.integers(min_value=0, max_value=2**32 - 1))
def test_undimensioned_and_single_rows_match_latest_restatement(kit, keep, seed):
    # oracle: requirement 2 - without cells the rule is restatement-s1's latest_restatement
    later = [a for a, k in zip((kit.INTC_FY23, kit.INTC_FY24), keep, strict=True) if k]
    rows = [r for r in kit.flat_view(kit.store_rows("INTC", accessions=[kit.INTC_OWN, *later]))
            if not r["has_multiple_dimensions"]]
    random.Random(seed).shuffle(rows)
    cur = current_column(rows, 2022)
    mine = sorted(r["fact_id"] for r in latest_dimensional_restatement(cur, rows))
    assert mine == sorted(r["fact_id"] for r in latest_restatement(cur, rows))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_a_recasting_later_filing_wins_and_one_without_the_member_does_not(kit):
    # oracle: requirement 2 - a later canonical filing that reports the same identity
    # answers with its value; one that does not report it changes nothing
    rows = kit.store_rows("INTC")
    nb = kit.find(rows, kit.INTC_FY23, REV_I, CCG_NOTEBOOK)
    recast = kit.synthetic_later_filing(rows, kit.INTC_FY23,
                                        lambda r: r["fact_id"] == nb["fact_id"],
                                        change={nb["fact_id"]: 18_000e6})
    got = _pick(kit, _answer(kit, rows + recast, 2022), REV_I, CCG_NOTEBOOK, "INTC")
    assert [(g["accession_no"], g["value"]) for g in got] == [(kit.SYNTH_ACCESSION, 18_000e6)]
    other = kit.synthetic_later_filing(rows, kit.INTC_FY23,
                                       lambda r: r["fact_id"] != nb["fact_id"])
    got = _pick(kit, _answer(kit, rows + other, 2022), REV_I, CCG_NOTEBOOK, "INTC")
    assert [(g["accession_no"], g["value"]) for g in got] == [(kit.INTC_FY23, 18_781e6)]
    # a non-canonical later filing never answers
    hidden = [dict(r, is_canonical_filing=False) for r in recast]
    got = _pick(kit, _answer(kit, rows + hidden, 2022), REV_I, CCG_NOTEBOOK, "INTC")
    assert [(g["accession_no"], g["value"]) for g in got] == [(kit.INTC_FY23, 18_781e6)]
