"""Fixtures for the screen-universe spec tests (docs/specs/screen-universe.md).

The database is the I/O boundary: `psycopg2.connect` is replaced by a fake store that
answers
  * the publish-manifest probe (`to_regclass`) with "absent", so the publish gate is inert;
  * any SQL mentioning `entity_master` with the corpus roster (`cik`, `ticker`);
  * any other SQL the way the screen's per-view queries do: per filer, the row of its
    latest fiscal year <= `params["fy"]` (every filer's latest row when `fy` is missing).

The fake ignores every other SQL predicate (ticker lists included), so restricting the
ranking to the universe has to happen on the rows it returns.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, XbrlFactstoreScreen

# A store: ticker -> [(fiscal_year, value, stored period_end)], the stored end being
# Arelle's next-midnight date (one day after the period's last day).
Store = Mapping[str, Sequence[tuple[int, float, dt.date]]]

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
            year, value, stored_end = max(eligible)
            rows.append({
                "ticker": ticker, "cik": self._cik[ticker], "fiscal_year": year,
                "value": value, "unit": "USD", "period_end": stored_end,
                "section_path": None, "html_anchor": None, "page_num": None,
                "accession_no": None, "fact_id": 1000 + len(rows),
                "concept": "us-gaap:NetIncomeLoss", "concept_pref_rank": 1,
            })
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
        return {"dbname": "fake_screen_universe_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _run_screen(store: Store, roster: Iterable[str], fy: int, top_n: int | None = None,
                **step_fields: Any) -> ScreenResult:
    roster = list(roster)
    step: dict[str, Any] = {"id": "s1", "op": "screen", "fy": fy, "order": "desc",
                            "top_n": top_n, "dimension": "NONE", "metric_kind": "value",
                            "concept_family": "NetIncome"}
    step.update(step_fields)  # e.g. universe=...; omitted, the step has no `universe` key
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda **kw: _Conn(store, roster))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


@pytest.fixture(scope="session")
def run_screen() -> Callable[..., ScreenResult]:
    """`run_screen(store, roster, fy, top_n=None, **step_fields)`: a flat net-income
    screen over the fake store; safe inside hypothesis tests (session-scoped)."""
    return _run_screen
