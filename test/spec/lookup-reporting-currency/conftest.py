"""Fixtures for the lookup-reporting-currency spec tests (docs/specs/lookup-reporting-currency.md).

``fixtures/tsm_rows.json`` holds every non-dimensional ``mv_financial_facts`` row of TSMC for
seven concept families (provenance in the file and in the spec's Notes). The database is the
only I/O boundary: ``FakeConn`` replaces ``psycopg2.connect`` and follows the spec's fake-database
contract. Nothing here reads the network, the current date or anything outside this directory.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DOC = json.loads((FIXTURES / "tsm_rows.json").read_text("utf-8"))

TSM_CIK = "0001046179"
TSM = "TSM"

# Columns the store rows carry that the fixture leaves out (non-dimensional rows).
_FILL = {
    "segment_axis": None, "segment_member": None, "segment_label": None,
    "has_multiple_dimensions": False, "html_anchor": None, "page_num": None,
    "section_path": None,
}


def tsm_rows() -> list[dict]:
    """Fresh copies of the fixture rows, as the store returns them."""
    return [dict(r, cik=TSM_CIK, ticker=TSM, linked_chunk_ids=[], **_FILL) for r in _DOC["rows"]]


# Equality parameters the fake honours (spec Notes, fake-database contract).
_EQ = ("cik", "concept_family", "period_type", "duration_kind")


class FakeCursor:
    """A cursor over the fake store (spec Notes, fake-database contract)."""

    def __init__(self, rows: Sequence[dict], as_dict: bool):
        self._store = rows
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        p = params if isinstance(params, Mapping) else {}
        if "mv_financial_facts" in sql and "pg_attribute" not in sql:
            rows = [dict(r) for r in self._store
                    if all(r.get(k) == p[k] for k in _EQ if k in p)]
        else:
            rows = []
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def mogrify(self, sql: str, params: Any = None) -> bytes:
        return sql.encode("utf-8")

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeConn:
    """A psycopg2 connection over a list of store rows."""

    def __init__(self, rows: Sequence[dict]):
        self._rows = list(rows)

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor(self._rows, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self) -> dict:
        return {"dbname": "fake_lookup_reporting_currency_db"}

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:
        pass


def run_lookup(rows: Sequence[dict], cik: str, ticker: str, fy: int, family: str, **step: Any):
    """``XbrlFactstoreLookup.lookup`` of one non-dimensional FY step over the fake store."""
    full = {"id": "s1", "op": "lookup", "cik": cik, "ticker": ticker, "fy": fy,
            "concept_family": family, "dimensions": "NONE", **step}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(rows))
        return XbrlFactstoreLookup(db_conn_params={}).lookup(full)


def run_tsm(fy: int, family: str, rows: Sequence[dict] | None = None, **step: Any):
    """A TSM lookup over the fixture rows (or the given rows)."""
    return run_lookup(tsm_rows() if rows is None else rows, TSM_CIK, TSM, fy, family, **step)


def rename_unit(rows: Iterable[dict], old: str, new: str, cik: str, ticker: str) -> list[dict]:
    """Rows moved to another filer with currency *old* renamed to *new* (also per share)."""
    out = []
    for r in rows:
        unit = r["unit"]
        if unit == old:
            unit = new
        elif unit == f"shares*{old}":
            unit = f"shares*{new}"
        out.append(dict(r, unit=unit, cik=cik, ticker=ticker))
    return out


def usd_filer_rows(eur_value: float) -> list[dict]:
    """A synthetic USD filer: total assets at three year-ends in three 10-Ks, plus one EUR row.

    Each 10-K (FY2023-FY2025) tags its own year-end and the prior one in USD; the FY2025 10-K
    also tags its own year-end once in EUR (a stray foreign-currency row).
    """
    cik, ticker = "0000999001", "ZZUS"
    usd = {2022: 300e9, 2023: 310e9, 2024: 320e9, 2025: 330e9}
    rows: list[dict] = []
    fid = 900_000
    for fy in (2023, 2024, 2025):
        accn = f"0000999001-{fy + 1 - 2000:02d}-000001"
        for year in (fy - 1, fy):
            fid += 1
            rows.append(_synthetic_row(fid, cik, ticker, accn, fy, year, "USD", usd[year]))
    fid += 1
    rows.append(_synthetic_row(fid, cik, ticker, "0000999001-26-000001", 2025, 2025, "EUR",
                               eur_value))
    return rows


def _synthetic_row(fact_id: int, cik: str, ticker: str, accn: str, fy: int, year: int,
                   unit: str, value: float) -> dict:
    end = dt.date(year + 1, 1, 1).isoformat()   # the store's next-midnight year-end
    return {
        "fact_id": fact_id, "value": value, "unit": unit, "period_type": "instant",
        "period_start": None, "period_end": end, "concept": "us-gaap:Assets",
        "concept_family": "TotalAssets", "concept_pref_rank": 1, "concept_role": "primary",
        "substitution_code": None, "substitution_note": None, "presented": True,
        "accession_no": accn, "fiscal_year": fy, "fiscal_period": "FY",
        "filed_date": f"{fy + 1}-02-15", "is_amendment": False, "is_canonical_filing": True,
        "document_period_end": f"{fy}-12-31", "duration_kind": "instant", "decimals": -6,
        "cik": cik, "ticker": ticker, "linked_chunk_ids": [], **_FILL,
    }


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis)."""
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
