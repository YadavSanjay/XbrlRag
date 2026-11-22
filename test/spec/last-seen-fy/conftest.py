"""Fixtures for last-seen-fy: the registry rows, and a store to run the real SQL against.

The statement under test is read from ``scripts/ddl/populate/entity_master.sql`` and run as
written on an in-memory SQLite database. SQLite stands in for Postgres at the database
boundary, so the test executes the project's own SQL instead of reading its text.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
POPULATE_SQL = ROOT / "scripts" / "ddl" / "populate" / "entity_master.sql"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_db.py"

_SCHEMA = """
CREATE TABLE entity_master (
    cik TEXT PRIMARY KEY, trading_symbol TEXT, company_name TEXT, exchange TEXT, sic_code INT,
    filer_category TEXT, fye_month INT, fye_day INT, last_seen_fy INT,
    last_seen_accession TEXT, gics_sector TEXT, industry TEXT
);
CREATE TABLE filing_registry (
    cik TEXT NOT NULL, accession_no TEXT NOT NULL, form_type TEXT NOT NULL,
    fiscal_year INT NOT NULL, fiscal_period TEXT NOT NULL, filed_date TEXT NOT NULL,
    period_end TEXT NOT NULL, is_amendment INT, superseded_by TEXT
);
"""

# (ticker, cik, entity_master.last_seen_fy before the fix). Read from the live xbrl_db,
# read-only, on 2026-10-30.
FILERS: tuple[tuple[str, str, int], ...] = (
    ("AAPL", "0000320193", 2026),
    ("CRM", "0001108524", 2025),
    ("HD", "0000354950", 2025),
    ("MSFT", "0000789019", 2026),
    ("NVDA", "0001045810", 2027),
    ("TSLA", "0001318605", 2026),
    ("TSM", "0001046179", 2025),
)

# (ticker, accession, form, label, period, period end, filed): filing_registry on the same
# day, for the latest annual filings and the 10-Qs after them. Public SEC filing metadata.
REGISTRY: tuple[tuple[str, str, str, int, str, str, str], ...] = (
    ("AAPL", "0000320193-24-000123", "10-K", 2024, "FY", "2024-09-28", "2024-11-01"),
    ("AAPL", "0000320193-25-000079", "10-K", 2025, "FY", "2025-09-27", "2025-10-31"),
    ("AAPL", "0000320193-26-000013", "10-Q", 2026, "Q1", "2026-03-28", "2026-05-01"),
    ("AAPL", "0000320193-26-000020", "10-Q", 2026, "Q2", "2026-06-27", "2026-07-31"),
    ("CRM", "0001108524-24-000005", "10-K", 2024, "FY", "2024-01-31", "2024-03-06"),
    ("CRM", "0001108524-25-000006", "10-K", 2025, "FY", "2025-01-31", "2025-03-05"),
    ("CRM", "0001108524-26-000060", "10-K", 2026, "FY", "2026-01-31", "2026-03-02"),
    ("HD", "0000354950-24-000062", "10-K", 2023, "FY", "2024-01-28", "2024-03-13"),
    ("HD", "0000354950-25-000085", "10-K", 2024, "FY", "2025-02-02", "2025-03-21"),
    ("HD", "0001628280-26-019436", "10-K", 2025, "FY", "2026-02-01", "2026-03-18"),
    ("MSFT", "0000950170-25-100235", "10-K", 2025, "FY", "2025-06-30", "2025-07-30"),
    ("MSFT", "0001193125-26-191507", "10-Q", 2026, "Q1", "2026-03-31", "2026-04-29"),
    ("MSFT", "0001193125-26-323660", "10-K", 2026, "FY", "2026-06-30", "2026-07-29"),
    ("NVDA", "0001045810-25-000023", "10-K", 2025, "FY", "2025-01-26", "2025-02-26"),
    ("NVDA", "0001045810-26-000021", "10-K", 2026, "FY", "2026-01-25", "2026-02-25"),
    ("NVDA", "0001045810-26-000052", "10-Q", 2027, "Q2", "2026-04-26", "2026-05-20"),
    ("NVDA", "0001045810-26-000075", "10-Q", 2027, "Q3", "2026-07-26", "2026-08-26"),
    ("TSLA", "0001628280-25-003063", "10-K", 2024, "FY", "2024-12-31", "2025-01-30"),
    ("TSLA", "0001104659-26-053166", "10-K/A", 2025, "FY", "2025-12-31", "2026-04-30"),
    ("TSLA", "0001628280-26-003952", "10-K", 2025, "FY", "2025-12-31", "2026-01-29"),
    ("TSLA", "0001628280-26-026673", "10-Q", 2026, "Q1", "2026-03-31", "2026-04-23"),
    ("TSM", "0001193125-25-083423", "20-F", 2024, "FY", "2024-12-31", "2025-04-17"),
    ("TSM", "0001628280-26-025362", "20-F", 2025, "FY", "2025-12-31", "2026-04-16"),
)


def source_lines(text: str) -> str:
    """``text`` without its ``--`` comments."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def has_sql(chunk: str) -> bool:
    """True if ``chunk`` holds anything but comments and whitespace."""
    return bool(source_lines(chunk).strip())


def populate_statements() -> list[str]:
    """The file's statements, split on every semicolon exactly as ``scripts/dbq.py`` does."""
    text = POPULATE_SQL.read_text(encoding="utf-8-sig")
    return [chunk.strip() for chunk in text.split(";") if has_sql(chunk)]


def label_update() -> str:
    """The UPDATE that sets ``last_seen_fy``, as written in the file."""
    updates = [s for s in populate_statements()
               if source_lines(s).strip().upper().startswith("UPDATE")]
    assert updates, "entity_master.sql has no UPDATE of last_seen_fy"
    assert len(updates) == 1, "entity_master.sql has more than one UPDATE"
    return updates[0]


def open_store(
    entities: Iterable[Mapping[str, Any]], filings: Iterable[tuple[str, str, int]]
) -> sqlite3.Connection:
    """An in-memory store holding the given rows.

    Args:
        entities: ``entity_master`` rows, as column to value.
        filings: ``(cik, form_type, fiscal_year)``, one per filing.

    Returns:
        The open connection.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    for entity in entities:
        columns = ", ".join(entity)
        marks = ", ".join("?" for _ in entity)
        conn.execute(f"INSERT INTO entity_master ({columns}) VALUES ({marks})",
                     tuple(entity.values()))
    for i, (cik, form, year) in enumerate(filings):
        period = "FY" if form[:4] in ("10-K", "20-F", "40-F") else "Q1"
        conn.execute(
            "INSERT INTO filing_registry "
            "VALUES (?, ?, ?, ?, ?, '2026-01-01', '2026-01-01', 0, NULL)",
            (cik, f"acc-{i}", form, year, period),
        )
    return conn


def real_store() -> sqlite3.Connection:
    """The REGISTRY rows, with each filer's stored value from before the fix."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    ciks = {ticker: cik for ticker, cik, _ in FILERS}
    for ticker, cik, stored in FILERS:
        conn.execute(
            "INSERT INTO entity_master (cik, trading_symbol, last_seen_fy) VALUES (?, ?, ?)",
            (cik, ticker, stored),
        )
    for ticker, accession, form, label, period, end, filed in REGISTRY:
        conn.execute(
            "INSERT INTO filing_registry VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (ciks[ticker], accession, form, label, period, filed, end, int(form.endswith("/A"))),
        )
    return conn


def run_label_update(conn: sqlite3.Connection) -> None:
    """Runs the statement from the file, unchanged."""
    conn.execute(label_update())


def last_seen(conn: sqlite3.Connection, cik: str) -> int | None:
    """``last_seen_fy`` of one filer."""
    value: int | None = conn.execute(
        "SELECT last_seen_fy FROM entity_master WHERE cik = ?", (cik,)
    ).fetchone()[0]
    return value


class FakeCursor:
    """The extractor's ``fiscal_calendar`` read, with no row: dates stay None."""

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        assert re.search(r"\bfiscal_calendar\b", sql)

    def fetchone(self) -> None:
        return None


class FakeConnection:
    """Stands in for a psycopg2 connection in the extractor's ``with`` block."""

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor()


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The rows and helpers, as one namespace (spec directories share a conftest module name)."""
    return SimpleNamespace(
        BOOTSTRAP=BOOTSTRAP, FILERS=FILERS, POPULATE_SQL=POPULATE_SQL,
        FakeConnection=FakeConnection, label_update=label_update, last_seen=last_seen,
        open_store=open_store, populate_statements=populate_statements, real_store=real_store,
        run_label_update=run_label_update, source_lines=source_lines,
    )
