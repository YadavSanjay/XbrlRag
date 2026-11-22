"""Fixtures for no-default-year: a fake database at the psycopg2 boundary and mention pools.

The extractor's only reads on these paths are ``fiscal_calendar`` (date bounds and the written-
date reading). The fake returns no row, so dates stay None; the tests check the fiscal year,
the period and the ambiguity flag only.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest
from hypothesis import strategies as st

from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany, ResolvedPeriod

CIK = "0000000001"
BOUNDS = [2018, 2035]

# Pools for a "yearless digit mention" (docs/specs/no-default-year.md, Acceptance criteria).
# Hand-checked: no token is a year form of extract_years_in_range (no 19xx/20xx, no two-digit
# number, no apostrophe, no dd-dd or dd/dd pair), and no word is a relative or prior-year
# phrase, a month name or "calendar".
PERIOD_TOKENS = ["Q1", "Q2", "Q3", "Q4", "H1", "H2", "1st quarter", "2nd quarter",
                 "3rd quarter", "4th quarter", "first 9 months", "6 months"]
FORM_TOKENS = ["10-K", "10-Q", "8-K"]
NEUTRAL_WORDS = ["the", "results", "reported", "filers", "for", "of", "in", "total", "top",
                 "segment", "revenue", "quarterly", "figures"]
# Counts that are not years: one digit, 100-1899, or 2100 and above.
COUNTS = st.one_of(st.integers(min_value=0, max_value=9),
                   st.integers(min_value=100, max_value=1899),
                   st.integers(min_value=2100, max_value=99999)).map(str)
NUMERIC = st.one_of(st.sampled_from(PERIOD_TOKENS + FORM_TOKENS), COUNTS)
WORD = st.sampled_from(NEUTRAL_WORDS)


@st.composite
def yearless_digit_mentions(draw: Any) -> str:
    """A mention with at least one digit that writes no year: numeric tokens, each followed by
    at least one neutral word, so no two numeric tokens touch."""
    parts: list[str] = []
    if draw(st.booleans()):
        parts.append(draw(WORD))
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        parts.append(draw(NUMERIC))
        parts.extend(draw(st.lists(WORD, min_size=1, max_size=2)))
    if draw(st.booleans()):
        parts.pop()  # may end on the numeric token itself
        if not any(ch.isdigit() for ch in " ".join(parts)):
            parts.append(draw(NUMERIC))
    return " ".join(parts)


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


def resolve(mentions: list[str], anchor: ResolvedCompany | None) -> list[ResolvedPeriod]:
    """Resolves mentions through ``EntityExtractor.resolve_periods`` on the fake database."""
    extractor = EntityExtractor(db_conn_params={"dbname": "unused"}, company_aliases={})
    companies = [anchor] if anchor is not None else []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(psycopg2, "connect", lambda **_: FakeConnection())
        return extractor.resolve_periods(mentions, companies)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The helpers, as one namespace (spec directories share a conftest module name)."""
    return SimpleNamespace(company=company, resolve=resolve, bounds=BOUNDS, cik=CIK,
                           yearless=yearless_digit_mentions())
