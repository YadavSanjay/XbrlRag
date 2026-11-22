"""C6: the shape of entity_master.sql. C7: what the consumers get, with their code unchanged.

C6 reads source: the INSERT uses Postgres-only syntax over the Arelle tables, so neither
Postgres-free spec tests nor SQLite can run it. Notes in the spec give the live check.
"""

from __future__ import annotations

import re

import psycopg2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context_arms import filing_targets
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult

CIK = "0000000001"


def _insert_sql(kit) -> str:
    """The INSERT, comments removed, whitespace collapsed."""
    kit.label_update()
    inserts = [s for s in kit.populate_statements() if "INSERT INTO" in kit.source_lines(s)]
    assert len(inserts) == 1
    return " ".join(kit.source_lines(inserts[0]).split())


# ---------------------------------------------------------------------------------- C6


@pytest.mark.criterion("C6")
def test_the_file_is_the_insert_then_the_update(kit):
    # oracle: requirement 2 — the UPDATE runs last, so the INSERT cannot undo it; the file's
    # NOTE: dbq.py splits on every semicolon, so no chunk may be a fragment
    update = kit.label_update()
    statements = kit.populate_statements()
    assert len(statements) == 2
    assert statements[1] == update
    assert "INSERT INTO entity_master" in kit.source_lines(statements[0])
    assert kit.POPULATE_SQL.read_text(encoding="utf-8-sig").count(";") == 2


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_comments_hold_no_semicolon(kit):
    # oracle: metamorphic — removing the comments leaves the same statements
    update = kit.label_update()
    raw = kit.POPULATE_SQL.read_text(encoding="utf-8-sig")
    stripped = kit.source_lines(raw)
    assert raw.count(";") == stripped.count(";")
    assert kit.source_lines(update).strip() in stripped


@pytest.mark.criterion("C6")
def test_the_insert_leaves_last_seen_fy_alone(kit):
    # oracle: requirement 2 — GREATEST is the floor that would hold NVDA at 2027, and a
    # comparison with the stored label would mix a DEI with a label
    sql = _insert_sql(kit)
    columns = re.search(r"INSERT INTO entity_master \((.*?)\)", sql)
    assert columns is not None
    assert "last_seen_fy" not in columns[1]
    tail = sql.split("ON CONFLICT", 1)[1]
    assert "last_seen_fy" not in tail
    assert "GREATEST" not in sql


@pytest.mark.criterion("C6")
def test_the_other_columns_keep_their_source(kit):
    # oracle: requirement 3 — still the row with the highest DEI; identity stays in
    # entity_identity (docs/specs/identity-and-stand-in.md)
    sql = _insert_sql(kit)
    assert re.search(r"ORDER BY cik, \w+ DESC NULLS LAST", sql)
    assert "last_seen_accession = EXCLUDED.last_seen_accession" in sql
    assert "DocumentFiscalYearFocus" in sql
    assert "company_name" not in sql
    assert "trading_symbol" not in sql


@pytest.mark.criterion("C6")
def test_the_update_reads_the_registry_and_no_dei(kit):
    # oracle: requirement 1 — the label, not DEI
    update = kit.source_lines(kit.label_update())
    assert "filing_registry" in update
    assert "DocumentFiscalYearFocus" not in update
    assert not re.search(r"\b(fact|context|accession)\b", update)


@pytest.mark.criterion("C6")
def test_labels_exist_before_the_file_runs(kit):
    # oracle: requirement 4 — bootstrap writes the labels, then runs this file
    kit.label_update()
    text = kit.BOOTSTRAP.read_text(encoding="utf-8")
    assert text.index('id="registries"') < text.index('id="layer0-populate"')


# ---------------------------------------------------------------------------------- C7


def _company(ticker: str, cik: str, value: int | None) -> ResolvedCompany:
    return ResolvedCompany(mention=ticker, cik=cik, ticker=ticker, last_seen_fy=value,
                           resolution="exact_ticker", similarity=1.0)


def _resolve(kit, mention: str, company: ResolvedCompany):
    extractor = EntityExtractor(db_conn_params={"dbname": "unused"}, company_aliases={})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(psycopg2, "connect", lambda **_: kit.FakeConnection())
        return extractor.resolve_periods([mention], [company])[0]


def _no_period(*companies: ResolvedCompany):
    return filing_targets(IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=list(companies), periods=[]))


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("ticker, expected", [("AAPL", 2025), ("NVDA", 2026), ("CRM", 2026)])
def test_most_recent_year_is_the_annual_label(kit, ticker, expected):
    # oracle: each company's latest annual report, as in C1; the extractor's code is unchanged
    conn = kit.real_store()
    kit.run_label_update(conn)
    cik = next(c for t, c, _ in kit.FILERS if t == ticker)
    company = _company(ticker, cik, kit.last_seen(conn, cik))
    period = _resolve(kit, "most recent fiscal year", company)
    assert (period.fiscal_year, period.fiscal_period, period.ambiguous) == (expected, "FY", False)


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("ticker, expected", [("AAPL", 2025), ("NVDA", 2026), ("CRM", 2026)])
def test_a_long_context_arm_with_no_period_names_the_annual_filing(kit, ticker, expected):
    # oracle: as above; filing_targets asks for a label, and only annual filings carry one
    # in the resolver's filter (fiscal_period = 'FY')
    conn = kit.real_store()
    kit.run_label_update(conn)
    cik = next(c for t, c, _ in kit.FILERS if t == ticker)
    company = _company(ticker, cik, kit.last_seen(conn, cik))
    assert _no_period(company) == ([(ticker, expected)], None)


@pytest.mark.criterion("C7")
def test_a_filer_with_no_annual_filing_stays_ambiguous(kit):
    # oracle: the extractor's and the arm's existing handling of a missing year, by hand
    conn = kit.open_store([{"cik": CIK, "last_seen_fy": 2026}], [(CIK, "10-Q", 2026)])
    kit.run_label_update(conn)
    company = _company("AAA", CIK, kit.last_seen(conn, CIK))
    assert _resolve(kit, "latest", company).ambiguous is True
    targets, reason = _no_period(company)
    assert targets == []
    assert reason


@pytest.mark.criterion("C7")
@given(label=st.integers(2018, 2035), quarter_year=st.integers(0, 2))
def test_the_anchor_follows_the_annual_label_for_any_year(kit, label, quarter_year):
    # oracle: the definition — a later 10-Q never moves "most recent fiscal year"
    conn = kit.open_store([{"cik": CIK, "last_seen_fy": label + 1}],
                      [(CIK, "10-K", label), (CIK, "10-Q", label + quarter_year)])
    kit.run_label_update(conn)
    company = _company("AAA", CIK, kit.last_seen(conn, CIK))
    assert _resolve(kit, "most recent fiscal year", company).fiscal_year == label
    assert _no_period(company) == ([("AAA", label)], None)
