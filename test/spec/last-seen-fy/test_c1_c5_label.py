"""C1-C5: what the statement in entity_master.sql leaves in last_seen_fy.

Every test runs the file's own UPDATE, unchanged, on SQLite (see conftest.py).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.fiscal_labels import ANNUAL_FORMS, assign_fiscal_labels

CIK = "0000000001"
OTHER_CIK = "0000000002"
# Forms that are not annual under fiscal_labels: requirement 6 of fiscal-label-alignment.
# "10-KT" starts like an annual form, so a LIKE '10-K%' filter is caught here.
NOT_ANNUAL = ("10-Q", "10-Q/A", "10-KT", "10-KT/A", "8-K", "20-FR", "NT 10-K")

years = st.integers(2010, 2035)
annual_filings = st.tuples(st.sampled_from(sorted(ANNUAL_FORMS)), years)
other_filings = st.tuples(st.sampled_from(NOT_ANNUAL), years)
stored_values = st.one_of(st.none(), st.integers(1990, 2099))


def _one_filer(kit, filings, *, stored=2099):
    return kit.open_store([{"cik": CIK, "last_seen_fy": stored}],
                      [(CIK, form, year) for form, year in filings])


# ---------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "ticker, expected",
    [
        ("AAPL", 2025),  # fiscal 2025 ended 2025-09-27; its FY2026 filings here are 10-Qs
        ("NVDA", 2026),  # fiscal 2026 ended 2026-01-25; the 10-Qs after it tag 2027
        ("CRM", 2026),  # fiscal 2026 ended 2026-01-31; its 10-K tags DEI 2025 by mistake
        ("TSLA", 2025),  # a 10-K and a 10-K/A of 2025, then a 10-Q of 2026
        ("HD", 2025),  # fiscal 2025 ended 2026-02-01: not the calendar year of its period end
        ("TSM", 2025),  # a 20-F filer
        ("MSFT", 2026),  # a 10-Q and a 10-K of one label
    ],
)
def test_the_latest_annual_label_of_each_filer(kit, ticker, expected):
    # oracle: each company's own name for its latest annual report in the committed rows
    # (Apple, NVIDIA, Salesforce, Home Depot); filing_registry as read on 2026-10-30
    conn = kit.real_store()
    kit.run_label_update(conn)
    cik = next(c for t, c, _ in kit.FILERS if t == ticker)
    assert kit.last_seen(conn, cik) == expected


@pytest.mark.criterion("C1")
@given(filings=st.lists(st.one_of(annual_filings, other_filings), max_size=12).filter(
    lambda rows: any(form in ANNUAL_FORMS for form, _ in rows)))
def test_equals_the_greatest_annual_label(kit, filings):
    # oracle: the definition, computed in the test
    conn = _one_filer(kit, filings)
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) == max(year for form, year in filings if form in ANNUAL_FORMS)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(deis=st.lists(years, min_size=1, max_size=6))
def test_agrees_with_the_label_rule_on_the_latest_period(kit, deis):
    # oracle: fiscal-label-alignment requirement 2 — labels strictly increase with period
    # end, so the greatest label is the latest period's, whatever the DEI tags say
    ends = [date(2015, 12, 31) + timedelta(days=365 * i) for i in range(len(deis))]
    rows = [{"accession_no": f"a{i}", "form_type": "10-K", "period_end": end,
             "filed_date": end + timedelta(days=60), "dei_fiscal_year": dei}
            for i, (end, dei) in enumerate(zip(ends, deis, strict=True))]
    labels = assign_fiscal_labels(rows)
    conn = _one_filer(kit, [("10-K", labels[r["accession_no"]]) for r in rows])
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) == labels[rows[-1]["accession_no"]]


# ---------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("form", sorted(ANNUAL_FORMS))
def test_every_annual_form_counts(kit, form):
    # oracle: fiscal_labels.ANNUAL_FORMS, the one list of annual forms
    conn = _one_filer(kit, [("10-K", 2020), (form, 2024)])
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) == 2024


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("form", NOT_ANNUAL)
def test_no_other_form_counts(kit, form):
    # oracle: fiscal-label-alignment requirement 6 — these are not annual forms
    conn = _one_filer(kit, [("10-K", 2020), (form, 2024)])
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) == 2020


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(annual=st.lists(annual_filings, min_size=1, max_size=6),
       others=st.lists(other_filings, min_size=1, max_size=6))
def test_a_filing_of_another_form_changes_nothing(kit, annual, others):
    # oracle: metamorphic — adding non-annual filings at any label leaves the result alone
    alone, mixed = _one_filer(kit, annual), _one_filer(kit, annual + others)
    kit.run_label_update(alone)
    kit.run_label_update(mixed)
    assert kit.last_seen(mixed, CIK) == kit.last_seen(alone, CIK)


# ---------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("stored, expected", [(2027, 2026), (2025, 2026), (None, 2026)])
def test_the_stored_value_is_no_floor(kit, stored, expected):
    # oracle: NVDA's stored 2027 must fall and CRM's stored 2025 must rise (both live values)
    conn = _one_filer(kit, [("10-K", 2026), ("10-Q", 2027)], stored=stored)
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) == expected


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(filings=st.lists(st.one_of(annual_filings, other_filings), max_size=8),
       first=stored_values, second=stored_values)
def test_the_stored_value_never_matters_and_a_rerun_is_identical(kit, filings, first, second):
    # oracle: metamorphic — the result depends on the registry alone
    a, b = _one_filer(kit, filings, stored=first), _one_filer(kit, filings, stored=second)
    kit.run_label_update(a)
    kit.run_label_update(b)
    once = kit.last_seen(a, CIK)
    assert kit.last_seen(b, CIK) == once
    kit.run_label_update(a)
    assert kit.last_seen(a, CIK) == once


# ---------------------------------------------------------------------------------- C4


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("stored", [2026, None])
def test_a_filer_with_no_annual_filing_gets_null(kit, stored):
    # oracle: the definition — there is no such year, so no value; 10-Qs do not stand in
    conn = _one_filer(kit, [("10-Q", 2026), ("10-Q", 2026)], stored=stored)
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) is None
    assert conn.execute("SELECT count(*) FROM entity_master").fetchone()[0] == 1


@pytest.mark.criterion("C4")
@given(others=st.lists(other_filings, max_size=6), stored=stored_values)
def test_no_annual_filing_is_null_whatever_else_is_stored(kit, others, stored):
    # oracle: the definition, for any mix of other forms, or no filing at all
    conn = _one_filer(kit, others, stored=stored)
    kit.run_label_update(conn)
    assert kit.last_seen(conn, CIK) is None


# ---------------------------------------------------------------------------------- C5

_ENTITY = {
    "cik": CIK, "trading_symbol": "AAA", "company_name": "Alpha Holdings", "exchange": "NYSE",
    "sic_code": 3674, "filer_category": "Large Accelerated Filer", "fye_month": 9, "fye_day": 27,
    "last_seen_fy": 2026, "last_seen_accession": "0000000001-26-000001",
    "gics_sector": "Information Technology", "industry": "Semiconductors",
}


def _row(conn, cik):
    return dict(conn.execute("SELECT * FROM entity_master WHERE cik = ?", (cik,)).fetchone())


@pytest.mark.criterion("C5")
def test_only_last_seen_fy_moves(kit):
    # oracle: the scope — every other column of the row is as it was, by hand
    conn = kit.open_store([_ENTITY], [(CIK, "10-K", 2025), (CIK, "10-Q", 2026)])
    registry = [tuple(r) for r in conn.execute("SELECT * FROM filing_registry")]
    kit.run_label_update(conn)
    assert _row(conn, CIK) == {**_ENTITY, "last_seen_fy": 2025}
    assert [tuple(r) for r in conn.execute("SELECT * FROM filing_registry")] == registry


@pytest.mark.criterion("C5")
def test_a_filer_reads_only_its_own_filings(kit):
    # oracle: by hand — the other filer's later 10-K is not this filer's
    other = {"cik": OTHER_CIK, "last_seen_fy": 2020}
    conn = kit.open_store([_ENTITY, other], [(CIK, "10-K", 2022), (OTHER_CIK, "10-K", 2025)])
    kit.run_label_update(conn)
    assert (kit.last_seen(conn, CIK), kit.last_seen(conn, OTHER_CIK)) == (2022, 2025)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(mine=st.lists(annual_filings, min_size=1, max_size=5),
       theirs=st.lists(annual_filings, max_size=5))
def test_another_filers_filings_change_nothing_here(kit, mine, theirs):
    # oracle: metamorphic — rows of another cik never reach this one
    alone = kit.open_store([_ENTITY], [(CIK, f, y) for f, y in mine])
    both = kit.open_store([_ENTITY, {"cik": OTHER_CIK}],
                      [(CIK, f, y) for f, y in mine] + [(OTHER_CIK, f, y) for f, y in theirs])
    kit.run_label_update(alone)
    kit.run_label_update(both)
    assert _row(both, CIK) == _row(alone, CIK)
