"""Fixtures for the screen-full-universe spec tests (docs/specs/screen-full-universe.md).

The database is the I/O boundary: `psycopg2.connect` is replaced by a fake store that
answers
  * the publish-manifest probe (`to_regclass`) with "absent", so the publish gate is inert;
  * any SQL mentioning `entity_master` with the corpus roster (`cik`, `ticker`);
  * any other SQL the way Postgres answers the screen's per-view
    `SELECT DISTINCT ON (ticker) ... ORDER BY ticker, fiscal_year DESC, ... LIMIT n`:
    one row per filer, the row of its latest fiscal year <= `params["fy"]`, in ticker
    order, truncated to the query's LIMIT when the SQL has one (`LIMIT %(name)s` with the
    parameter's value, or a literal; `LIMIT NULL` means no limit, as in Postgres).

Honouring LIMIT is the point of this fake: it is what the database does at this boundary.
Every other predicate is ignored. Rows carry the columns of all four views.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, XbrlFactstoreScreen

# ticker -> [(fiscal_year, value)]
Store = Mapping[str, Sequence[tuple[int, float]]]

_LIMIT = re.compile(r"\bLIMIT\s+(?:%\((\w+)\)s|(\d+))", re.IGNORECASE)

# The step fields that select each of the screen's four views.
VIEWS: dict[str, dict[str, Any]] = {
    "flat": {"dimension": "NONE", "metric_kind": "value", "concept_family": "Revenue"},
    "derived": {"dimension": "NONE", "metric_kind": "derived:NetMarginPct",
                "concept_family": "Revenue"},
    "geographic": {"dimension": {"geography": "China"}, "metric_kind": "value",
                   "concept_family": "Revenue"},
    "segment": {"dimension": {"segment": "cloud"}, "metric_kind": "value",
                "concept_family": "Revenue"},
}


class _Cursor:
    def __init__(self, store: Store, roster: Sequence[str], as_dict: bool):
        self._store = store
        self._roster = roster
        self._as_dict = as_dict
        self._rows: list = []
        everyone = sorted(set(store) | set(roster))
        self._cik = {t: f"{i + 1:010d}" for i, t in enumerate(everyone)}

    def execute(self, sql, params=None):
        params = params if isinstance(params, Mapping) else {}
        if "to_regclass" in sql:
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            roster = [{"cik": self._cik[t], "ticker": t} for t in self._roster]
            self._rows = roster if self._as_dict else [(r["cik"], r["ticker"]) for r in roster]
            return
        fy = params.get("fy")
        rows = []
        for ticker in sorted(self._store):
            eligible = [e for e in self._store[ticker] if fy is None or e[0] <= fy]
            if not eligible:
                continue
            year, value = max(eligible)
            rows.append({
                "ticker": ticker, "cik": self._cik[ticker], "fiscal_year": year,
                "fiscal_period": "FY", "value": value, "unit": "USD",
                "period_end": dt.date(year + 1, 1, 1), "pct_of_total": None,
                "section_path": None, "html_anchor": None, "page_num": None,
                "accession_no": None, "fact_id": 1000 + len(rows),
                "concept": "us-gaap:Revenues", "concept_pref_rank": 1,
                "geo_code": "CN", "geo_name": "China", "segment_label": "Cloud",
                "metric_name": "NetMarginPct", "formula": "NetIncome / Revenue",
                "component_fact_ids": {},
            })
        match = _LIMIT.search(sql)
        if match:
            limit = params.get(match.group(1)) if match.group(1) else int(match.group(2))
            if limit is not None:
                rows = rows[: int(limit)]
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, store: Store, roster: Sequence[str]):
        self._store = store
        self._roster = roster

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, self._roster, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_screen_full_universe_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _run_screen(store: Store, roster: Sequence[str], fy: int, view: str = "flat",
                top_n: int | None = None, order: str = "desc",
                **screen_kwargs: Any) -> ScreenResult:
    step: dict[str, Any] = {"id": "s1", "op": "screen", "fy": fy, "order": order,
                            "top_n": top_n, **VIEWS[view]}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda **kw: _Conn(store, list(roster)))
        return XbrlFactstoreScreen(db_conn_params={}, **screen_kwargs).screen(step)


@pytest.fixture(scope="session")
def run_screen() -> Callable[..., ScreenResult]:
    """`run_screen(store, roster, fy, view="flat", top_n=None, order="desc", **kwargs)`:
    a screen over the fake store; `kwargs` go to `XbrlFactstoreScreen` (e.g. row_limit).
    Session-scoped, so it is safe inside hypothesis tests."""
    return _run_screen
