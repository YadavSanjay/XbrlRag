"""Unit tests for the SEC companyfacts check's renderers and the Postgres store's row mapping.

The rendering functions and ``record_from_row`` are pure; the store's DB round trip is
covered by a read-only test that skips when ``xbrl_db`` is unreachable.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.eval.sec_check.pg_store import PostgresFactStore, record_from_row
from src.eval.sec_check.report import (
    console_summary,
    filing_index_url,
    render_hand_check,
    render_markdown,
)

SUITE = Path(__file__).resolve().parents[1] / "eval" / "suites" / "sql_derived_v1.json"


def test_filing_index_url_strips_padding_and_dashes():
    assert filing_index_url("0000320193", "0000320193-24-000123") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/")


def test_record_from_row_normalizes_types():
    # The fact store keeps Arelle's exclusive end (2024-09-29 for an XBRL end of
    # 2024-09-28); the record carries the XBRL date companyfacts reports.
    row = (13253, "AAPL", "320193", "0000320193-24-000123",
           "http://fasb.org/us-gaap/2024:Revenues", "USD", dt.date(2023, 10, 1),
           dt.date(2024, 9, 29), Decimal("391035000000"), False)
    rec = record_from_row(row)
    assert rec == {
        "fact_id": 13253, "ticker": "AAPL", "cik": "0000320193",
        "accession_no": "0000320193-24-000123",
        "concept": "http://fasb.org/us-gaap/2024:Revenues", "unit": "USD",
        "period_start": "2023-10-01", "period_end": "2024-09-28",
        "value": 391035000000.0, "dimensional": False,
    }
    assert isinstance(rec["value"], float)


def test_record_from_row_instant_and_nil():
    instant = (1, "AAPL", "0000320193", "a", "ns:Assets", "USD", None, dt.date(2024, 9, 29),
               Decimal("1.5"), True)
    rec = record_from_row(instant)
    assert rec is not None
    assert rec["period_start"] is None
    assert rec["period_end"] == "2024-09-28"
    assert rec["dimensional"] is True
    nil = instant[:8] + (None, False)
    assert record_from_row(nil) is None


def _report():
    checks = [
        {"case_id": "aq-1a:fy=2024-ticker=AAPL", "kind": "lookup", "status": "match",
         "reason": None, "facts": [{"fact_id": 1, "status": "match", "sec_value": 5.0,
                                    "sign_flip": False, "restatements": []}],
         "items": [{"label": "AAPL", "expected": 5.0, "sec_value": 5.0, "agree": True}]},
        {"case_id": "aq-5a:fy=2024-ticker=MSFT", "kind": "lookup", "status": "mismatch",
         "reason": None,
         "facts": [{"fact_id": 2, "status": "value_mismatch", "sec_value": -7e9,
                    "sign_flip": True,
                    "restatements": [{"accession_no": "0000789019-25-000001",
                                      "value": 6.9e9}]}],
         "items": [{"label": "MSFT", "expected": 7e9, "sec_value": -7e9, "agree": False}]},
    ]
    summary = {"lookup": {"match": 1, "mismatch": 1, "checked": 2, "match_rate": 0.5},
               "all": {"match": 1, "mismatch": 1, "checked": 2, "match_rate": 0.5},
               "fact_status_counts": {"match": 1, "value_mismatch": 1}}
    return {"suite_path": "s.json", "corpus_fingerprint": "pm:x", "git_sha": "abc",
            "run_date": "2026-10-21", "seed": 1, "summary": summary, "hand_checks": [],
            "case_checks": checks}


def test_render_markdown_tables_and_non_matches():
    md = render_markdown(_report())
    assert "| lookup | 2 | 1 | 1 | 0 | 0 | 0 | 2 | 50.0% |" in md
    assert "## Non-match cases (1)" in md
    assert "aq-5a:fy=2024-ticker=MSFT" in md
    assert "aq-1a:fy=2024-ticker=AAPL" not in md.split("## Non-match cases")[1]
    assert "sign flip" in md
    assert "restated in 0000789019-25-000001: 6,900,000,000" in md


def test_render_hand_check_lists_facts_with_index_urls():
    case = {"id": "aq-3d:fy=2024-segment=Europe-ticker=AAPL", "kind": "dimensional",
            "question": "Europe revenue?", "ticker": "AAPL", "fy": 2024,
            "expected_value": 101e9, "unit": "USD", "expected_fact_ids": [9, 10]}
    records = {9: {"fact_id": 9, "ticker": "AAPL", "cik": "0000320193",
                   "accession_no": "0000320193-24-000123",
                   "concept": "http://fasb.org/us-gaap/2024:Revenues", "unit": "USD",
                   "period_start": "2023-10-01", "period_end": "2024-09-28",
                   "value": 101e9, "dimensional": True}}
    md = render_hand_check([case], records)
    assert "Europe revenue?" in md
    assert "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/" in md
    assert "10: not in the fact store" in md
    assert "2023-10-01 to 2024-09-28" in md


def test_console_summary_one_line_per_kind():
    text = console_summary(_report()["summary"])
    lines = text.splitlines()
    assert len(lines) == 3
    assert "match rate 50.0%" in lines[0]
    assert "value_mismatch 1" in lines[-1]


def _db_reachable() -> bool:
    try:
        store = PostgresFactStore()
        try:
            store._query("SELECT 1 FROM mv_financial_facts LIMIT 1", ())
        finally:
            store.close()
        return True
    except Exception:  # isolation point: any connection failure means skip
        return False


@pytest.mark.skipif(not _db_reachable(), reason="xbrl_db not reachable")
def test_store_marks_suite_dimensional_facts():
    # Read-only: a lookup case's fact is not dimensional; a breakdown case's facts are.
    cases = json.loads(SUITE.read_text(encoding="utf-8"))["cases"]
    lookup = next(c for c in cases if c["kind"] == "lookup")
    breakdown = next(c for c in cases if c["kind"] == "breakdown")
    with PostgresFactStore() as store:
        got = store.facts(lookup["expected_fact_ids"] + breakdown["expected_fact_ids"])
    first = got[lookup["expected_fact_ids"][0]]
    assert first["dimensional"] is False
    assert len(first["cik"]) == 10
    assert all(got[i]["dimensional"] for i in breakdown["expected_fact_ids"])
