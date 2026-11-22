"""Fixtures for last-fiscal-year-phrase: a fake database at the psycopg2 boundary.

The extractor's only read on this path is ``fiscal_calendar`` (date bounds). The fake returns no
row, so dates stay None; the tests check the fiscal year and period only.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany, ResolvedPeriod

CIK = "0000000001"


class FakeCursor:
    """The extractor's ``fiscal_calendar`` read, with no row."""

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        assert re.search(r"\bfiscal_calendar\b", sql)

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


class FakeConnection:
    """Stands in for a psycopg2 connection in the extractor's ``with`` block."""

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor()


def company(last_seen_fy: int | None) -> ResolvedCompany:
    """A synthetic resolved filer (December year end) anchored on ``last_seen_fy``."""
    return ResolvedCompany(mention="Example Co", cik=CIK, ticker="EXCO",
                           company_name="Example Co", fye_month=12, fye_day=31,
                           last_seen_fy=last_seen_fy, resolution="exact_ticker",
                           similarity=1.0)


def resolve(mention: str, anchor: ResolvedCompany | None) -> list[ResolvedPeriod]:
    """Resolves one mention through ``EntityExtractor.resolve_periods`` on the fake database."""
    extractor = EntityExtractor(db_conn_params={"dbname": "unused"}, company_aliases={})
    companies = [anchor] if anchor is not None else []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(psycopg2, "connect", lambda **_: FakeConnection())
        return extractor.resolve_periods([mention], companies)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The helpers, as one namespace (spec directories share a conftest module name)."""
    return SimpleNamespace(company=company, resolve=resolve)
