"""C2: the dimensional lookups follow S1 (docs/specs/dimensional-s1.md R3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

OP = "OperatingIncomeLoss"
REV_I = "RevenueFromContractWithCustomerExcludingAssessedTax"
NOTEBOOK = {"segment": "Client Computing Group",
            "srt:ConsolidatedEntitiesAxis": "Operating Segments", "product": "Notebook"}
CCG_NOTEBOOK = ("BusinessSegments=ClientComputingGroup;ConsolidatedEntities=OperatingSegments;"
                "ProductOrService=Notebook;")


def _ok(result, value, accession):
    assert result.status.value == "OK", (result.status, result.resolution_notes)
    assert result.value == pytest.approx(value, abs=5e5)  # millions precision (decimals -6)
    assert result.citation.accession_no == accession


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("ticker,family,dims,latest,original", [
    # oracle: FSDS num - Intel's CCG notebook revenue FY2022: 18,783 in the FY2022 10-K,
    # 18,781 in the FY2023 10-K (same three dimensions); the FY2024 10-K does not report it
    ("INTC", "Revenue", NOTEBOOK, (18_781e6, "0000050863-24-000010"),
     (18_783e6, "0000050863-23-000006")),
    # oracle: FSDS num - NVIDIA Graphics revenue FY2023, 11,906 in all three 10-Ks; the
    # FY2025 10-K names the member GraphicsSegmentMember, so the FY2024 10-K is the latest
    # report of the FY2023 10-K's GraphicsMember x OperatingSegmentsMember fact
    ("NVDA", "Revenue", {"segment": "Graphics"}, (11_906e6, "0001045810-24-000029"),
     (11_906e6, "0001045810-23-000017")),
], ids=["INTC-3-dimensions", "NVDA-one-hint-neutral-member"])
def test_multidimensional_lookup_answers_the_latest_report(kit, ticker, family, dims, latest,
                                                           original):
    rows = kit.store_rows(ticker)
    fy = kit.YEAR[ticker]
    _ok(kit.run_lookup(rows, ticker, fy, family, dims), *latest)
    _ok(kit.run_lookup(rows, ticker, fy, family, dims, as_originally_reported=True), *original)


@pytest.mark.criterion("C2")
def test_one_dimension_lookup_is_never_restated_by_a_cell(kit):
    # oracle: FSDS num - Intel "All other" operating income FY2022: -5,614 (FY2022 10-K),
    # -5,977 (FY2023 10-K, single dimension); the FY2024 10-K's 1,174 is a cell with
    # ConsolidationItemsAxis, another fact (requirement 3c)
    rows = kit.store_rows("INTC")
    _ok(kit.run_lookup(rows, "INTC", 2022, "OperatingIncome", {"segment": "All Other"}),
        -5_977e6, kit.INTC_FY23)


def _hand(kit, ticker, accessions, tag, segments):
    """By hand: the latest of the given filings that reports the own fact's identity."""
    rows = kit.store_rows(ticker, accessions=accessions)
    hits = []
    for a in accessions:
        try:
            hits.append(kit.find(rows, a, tag, segments))
        except AssertionError:
            continue
    best = max(hits, key=lambda r: (r["filed_date"], r["accession_no"]))
    return best["value"], best["accession_no"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(["notebook", "all-other"]), st.lists(st.booleans(), min_size=2,
                                                             max_size=2))
def test_any_set_of_later_reports(kit, case, keep):
    # oracle: the latest of the stored filings with the same fact identity, by hand from
    # the FSDS segments text (requirements 2-3)
    later = [a for a, k in zip((kit.INTC_FY23, kit.INTC_FY24), keep, strict=True) if k]
    accs = [kit.INTC_OWN, *later]
    rows = kit.store_rows("INTC", accessions=accs)
    if case == "notebook":
        got = kit.run_lookup(rows, "INTC", 2022, "Revenue", NOTEBOOK)
        want = _hand(kit, "INTC", accs, REV_I, CCG_NOTEBOOK)
    else:
        got = kit.run_lookup(rows, "INTC", 2022, "OperatingIncome", {"segment": "All Other"})
        want = _hand(kit, "INTC", accs, OP, "BusinessSegments=AllOtherSegments;")
    _ok(got, *want)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_a_later_recast_moves_the_answer_and_an_unrelated_later_filing_does_not(kit):
    # oracle: requirement 3 - a later canonical 10-K (synthetic, label 2025) reporting the
    # same three dimensions with another value answers; one without the fact changes nothing
    rows = kit.store_rows("INTC")
    nb = kit.find(rows, kit.INTC_FY23, REV_I, CCG_NOTEBOOK)
    recast = kit.synthetic_later_filing(rows, kit.INTC_FY23,
                                        lambda r: r["fact_id"] == nb["fact_id"],
                                        change={nb["fact_id"]: 18_000e6})
    _ok(kit.run_lookup(rows + recast, "INTC", 2022, "Revenue", NOTEBOOK),
        18_000e6, kit.SYNTH_ACCESSION)
    unrelated = kit.synthetic_later_filing(rows, kit.INTC_FY23,
                                           lambda r: r["fact_id"] != nb["fact_id"])
    _ok(kit.run_lookup(rows + unrelated, "INTC", 2022, "Revenue", NOTEBOOK),
        18_781e6, kit.INTC_FY23)
    # the original on request ignores both
    _ok(kit.run_lookup(rows + recast, "INTC", 2022, "Revenue", NOTEBOOK,
                       as_originally_reported=True), 18_783e6, kit.INTC_OWN)
