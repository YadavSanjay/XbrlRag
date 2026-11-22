"""C1: S1 answers a restated period; the original on request (docs/specs/restatement-s1.md).

Point cases are the report's hand-checked restatements (split-adjusted EPS, recast cash flow,
reclassified receivables, recast D&A). Expected values are FSDS ``num`` rows of the named
accessions (checked against the fixture when the suite loads); store rows come from
companyfacts, an independent extraction of the same filings.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyNotFoundError, fiscal_year_key
from src.retriever.xbrl.restatement import current_column, latest_restatement

# Per-share values to the cent (half a cent either way); amounts are tagged in millions
# (decimals -6), so half a million either way.
CENT = 0.005
MILLION = 5e5

# ticker, tag, unit, family, fy, S1 value, S1 accession, original value, original accession, tol
CASES = [
    ("NVDA", "EarningsPerShareDiluted", "USD/shares", "EarningsPerShareDiluted", 2023,
     0.17, "0001045810-25-000023", 1.74, "0001045810-23-000017", CENT),
    ("WMT", "EarningsPerShareDiluted", "USD/shares", "EarningsPerShareDiluted", 2023,
     1.42, "0000104169-25-000021", 4.27, "0000104169-23-000020", CENT),
    ("AVGO", "EarningsPerShareDiluted", "USD/shares", "EarningsPerShareDiluted", 2022,
     2.65, "0001730168-24-000139", 26.53, "0001730168-22-000118", CENT),
    ("INTC", "NetCashProvidedByUsedInFinancingActivities", "USD", "CashFromFinancing", 2022,
     1_115e6, "0000050863-25-000009", 1_361e6, "0000050863-23-000006", MILLION),
    ("AMD", "AccountsReceivableNetCurrent", "USD", "AccountsReceivable", 2023,
     4_323e6, "0000002488-25-000012", 5_376e6, "0000002488-24-000012", MILLION),
    ("HD", "DepreciationDepletionAndAmortization", "USD", "DepreciationAndAmortization", 2023,
     3_061e6, "0001628280-26-019436", 3_247e6, "0000354950-24-000062", MILLION),
]
IDS = [f"{c[0]}-FY{c[4]}" for c in CASES]
SCREEN_CASES = [c for c in CASES if c[0] in ("INTC", "HD", "AMD")]

# Every filer-concept-year of the fixtures the key/product agreement is checked on.
GRID = [(c[0], c[1], c[2], fy) for c in CASES for fy in range(2022, 2027)] + [
    ("INTC", "CommonStockDividendsPerShareDeclared", "USD/shares", fy) for fy in (2024, 2025)
] + [("COST", "Goodwill", "USD", fy) for fy in range(2023, 2027)] + [
    ("KO", "BusinessExitCosts1", "USD", fy) for fy in range(2023, 2026)
] + [("QCOM", "RestructuringAndRelatedCostIncurredCost", "USD", fy) for fy in (2023, 2024, 2025)]


def _s1(rows, fy):
    return latest_restatement(current_column(rows, fy), rows)


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_latest_report_answers_and_the_original_on_request(kit, case):
    # oracle: FSDS num rows of the restating and the original accession (e.g. NVDA
    # 0001045810-25-000023 EarningsPerShareDiluted ddate 20230131 qtrs 4 = 0.17, and
    # 0001045810-23-000017 = 1.74); S1 picks the latest filed of the restating filings
    ticker, tag, _unit, _family, fy, s1, s1_acc, orig, orig_acc, tol = case
    rows = kit.store_rows(ticker, [tag])
    answer = _s1(rows, fy)
    assert kit.accessions(answer) == {s1_acc}
    assert [v for v in kit.values(answer)] == [pytest.approx(s1, abs=tol)]
    original = current_column(rows, fy, as_originally_reported=True)
    assert kit.accessions(original) == {orig_acc}
    assert [v for v in kit.values(original)] == [pytest.approx(orig, abs=tol)]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_lookup_answers_s1_and_the_original_on_request(kit, case):
    # oracle: as above (FSDS num of the named accessions); requirement 3 — the citation is the
    # answering row's accession
    ticker, _tag, _unit, family, fy, s1, s1_acc, orig, orig_acc, tol = case
    rows = kit.store_rows(ticker)
    result = kit.run_lookup(rows, ticker, fy, family)
    assert result.status.value == "OK", result.resolution_notes
    assert result.value == pytest.approx(s1, abs=tol)
    assert result.citation.accession_no == s1_acc
    original = kit.run_lookup(rows, ticker, fy, family, as_originally_reported=True)
    assert original.status.value == "OK", original.resolution_notes
    assert original.value == pytest.approx(orig, abs=tol)
    assert original.citation.accession_no == orig_acc


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", SCREEN_CASES, ids=[f"{c[0]}-FY{c[4]}" for c in SCREEN_CASES])
def test_the_flat_screen_ranks_the_restated_value(kit, case):
    # oracle: FSDS num of the restating accession; requirement 4 — the row keeps the requested
    # fiscal year and cites the restating filing
    ticker, _tag, _unit, family, fy, s1, s1_acc, _orig, _orig_acc, tol = case
    result = kit.run_screen(kit.store_rows(ticker), fy, family)
    assert result.status.value == "OK", result.resolution_notes
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["ticker"] == ticker
    assert row["fiscal_year"] == fy
    assert row["value"] == pytest.approx(s1, abs=tol)
    assert row["citation"]["accession_no"] == s1_acc


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C1")
@given(case=st.sampled_from(CASES), data=st.data())
def test_older_filings_and_row_order_never_change_the_answer(kit, case, data):
    # oracle: metamorphic — S1 depends only on the latest filing that reports the period:
    # dropping filings filed before it (other than the year's own), adding an older filing with
    # other values, or permuting the rows leaves the FSDS-checked answer unchanged
    ticker, tag, _unit, _family, fy, s1, s1_acc, _orig, orig_acc, tol = case
    rows = kit.store_rows(ticker, [tag])
    answer_filed = next(r["filed_date"] for r in rows if r["accession_no"] == s1_acc)
    older = sorted({r["accession_no"] for r in rows
                    if r["filed_date"] < answer_filed and r["accession_no"] != orig_acc})
    dropped = set(data.draw(st.lists(st.sampled_from(older), unique=True))) if older else set()
    kept = [r for r in rows if r["accession_no"] not in dropped]
    fake = [dict(r, accession_no="0000000001-20-000001", filed_date="2020-01-02",
                 fiscal_year=fy + 1, value=r["value"] * 3 if r["value"] else 1.0,
                 fact_id=r["fact_id"] + 5_000_000)
            for r in rows if r["accession_no"] == orig_acc]
    mixed = data.draw(st.permutations(kept + fake))
    answer = _s1(mixed, fy)
    assert kit.accessions(answer) == {s1_acc}
    assert [v for v in kit.values(answer)] == [pytest.approx(s1, abs=tol)]


@pytest.mark.criterion("C1")
@given(item=st.sampled_from(GRID))
def test_the_product_and_the_key_agree_on_the_same_filings(kit, item):
    # oracle: metamorphic — the product's S1 over the store and fiscal_year_key over the same
    # filings' companyfacts entries name one accession and one value, or both refuse
    ticker, tag, unit, fy = item
    rows = kit.store_rows(ticker, [tag])
    answer = _s1(rows, fy)
    try:
        key = fiscal_year_key(kit.companyfacts(ticker, corpus_only=True), f"us-gaap:{tag}",
                              unit, fy)
    except KeyNotFoundError:
        key = None
    if key is None:
        assert answer == []
    else:
        assert kit.accessions(answer) == {key["accession"]}
        assert [v for v in kit.values(answer)] == [pytest.approx(key["value"], abs=1e-9)]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@pytest.mark.parametrize("case", SCREEN_CASES, ids=[f"{c[0]}-FY{c[4]}" for c in SCREEN_CASES])
def test_the_screen_and_the_lookup_agree(kit, case):
    # oracle: metamorphic — one period, one answer: the screen's row and the lookup cite the
    # same accession and value
    ticker, _tag, _unit, family, fy, _s1v, _s1_acc, _orig, _orig_acc, tol = case
    rows = kit.store_rows(ticker)
    looked = kit.run_lookup(rows, ticker, fy, family)
    screened = kit.run_screen(rows, fy, family)
    assert looked.status.value == "OK" and screened.rows, (looked.resolution_notes,
                                                           screened.resolution_notes)
    assert screened.rows[0]["citation"]["accession_no"] == looked.citation.accession_no
    assert screened.rows[0]["value"] == pytest.approx(looked.value, abs=tol)
