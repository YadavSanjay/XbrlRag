"""Fixtures for screen-reporting-currency (docs/specs/screen-reporting-currency.md).

The database is the I/O boundary: `fake_screen` replaces `psycopg2.connect` with a fake
store that follows the spec's "Fake database contract" (Notes):

- a query mentioning `entity_master` gets the roster (`cik`, `ticker`);
- a query on `mv_financial_facts` with a USD predicate (`unit\\s*=\\s*'USD'`) gets one
  current-period USD row per filer at the latest fiscal year <= `params["fy"]`;
- any other query on `mv_financial_facts` gets every observation row of each filer at
  that year, in every unit and period;
- the `to_regclass` publish-manifest probe gets `(False,)`, so the publish gate is inert.

A store is ``{ticker: {"cik": str, "fiscal_year": int, "obs": [observation, ...]}}``;
an observation is ``(unit, period_end, value, fact_id, is_current)``.
"""

from __future__ import annotations

import re

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

_USD_PREDICATE = re.compile(r"unit\s*=\s*'USD'")

TSM_CIK = "0001046179"
TSM_CONCEPT = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
USD_CONCEPT = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"

# TSM FY2022 Capex, canonical filing 0001193125-23-107214 (spec Notes, xbrl_db 2026-11-04):
# facts 80149-80152 as (unit, period_end, value, fact_id, is_current).
TSM_CAPEX_FY2022 = [
    ("TWD", "2021-01-01", 507_238_700_000.0, 80149, False),
    ("TWD", "2022-01-01", 839_195_700_000.0, 80150, False),
    ("TWD", "2023-01-01", 1_082_672_100_000.0, 80151, True),
    ("USD", "2023-01-01", 35_231_800_000.0, 80152, True),
]

# The other filers' FY2022 capex in USD, from the baseline traces (spec Notes). Their
# period ends are synthetic next-midnight fiscal year-ends; the rule only compares them.
USD_CAPEX_FY2022 = {
    "AMZN": ("0000000001", "2023-01-01", 63_645_000_000.0, 1001),
    "GOOGL": ("0000000002", "2023-01-01", 31_485_000_000.0, 1002),
    "META": ("0000000003", "2023-01-01", 31_431_000_000.0, 1003),
    "INTC": ("0000000004", "2023-01-01", 24_844_000_000.0, 1004),
    "MSFT": ("0000000005", "2022-07-01", 23_886_000_000.0, 1005),
}


def _capex_store() -> dict:
    store = {
        t: {"cik": cik, "fiscal_year": 2022, "concept": USD_CONCEPT,
            "obs": [("USD", end, value, fid, True)]}
        for t, (cik, end, value, fid) in USD_CAPEX_FY2022.items()
    }
    store["TSM"] = {"cik": TSM_CIK, "fiscal_year": 2022, "concept": TSM_CONCEPT,
                    "obs": list(TSM_CAPEX_FY2022)}
    return store


@pytest.fixture(scope="session")
def tsmc_capex_observations() -> list[tuple[str, str]]:
    """TSM FY2022 capex as ``(unit, period_end)`` pairs (facts 80149-80152)."""
    return [(u, e) for u, e, _, _, _ in TSM_CAPEX_FY2022]


@pytest.fixture(scope="session")
def capex_store():
    """A factory for a fresh copy of the C3 store (six filers, FY2022 capex)."""
    return _capex_store


# --------------------------------------------------------------------------- fake DB
def _observation_row(ticker: str, entry: dict, ob: tuple, fiscal_year: int) -> dict:
    unit, end, value, fid, current = ob
    return {
        "ticker": ticker, "cik": entry["cik"], "fiscal_year": fiscal_year,
        "fiscal_period": "FY", "unit": unit, "period_end": end, "value": value,
        "concept": entry.get("concept", USD_CONCEPT), "concept_pref_rank": 1,
        "fact_id": fid, "is_current_period": current, "section_path": None,
        "html_anchor": None, "page_num": None, "accession_no": None,
        "segment_axis": None, "pct_of_total": None,
    }


def _wanted(params: dict, ticker: str, cik: str) -> bool:
    """A per-filer query (a ticker or cik parameter) gets only that filer's rows."""
    for key, own in (("ticker", ticker), ("tickers", ticker), ("cik", cik), ("ciks", cik)):
        if key in params:
            got = params[key]
            if isinstance(got, (list, tuple, set, frozenset)):
                if own not in got:
                    return False
            elif got != own:
                return False
    return True


class _Cursor:
    def __init__(self, store: dict, as_dict: bool):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        params = dict(params or {}) if isinstance(params, dict) else {}
        if "to_regclass" in sql:  # publish_manifest probe: absent, so the gate is inert
            self._rows = [(False,)]
            return
        fy = params.get("fy")
        rows: list[dict] = []
        if "entity_master" in sql:
            rows = [{"cik": e["cik"], "ticker": t} for t, e in sorted(self._store.items())]
        elif "mv_financial_facts" in sql:
            usd_only = bool(_USD_PREDICATE.search(sql))
            for ticker, entry in sorted(self._store.items()):
                year = entry["fiscal_year"]
                if fy is not None and year > fy:
                    continue
                if not _wanted(params, ticker, entry["cik"]):
                    continue
                if usd_only:
                    current = [ob for ob in entry["obs"] if ob[0] == "USD" and ob[4]]
                    if current:
                        best = max(current, key=lambda ob: ob[1])
                        rows.append(_observation_row(ticker, entry, best, year))
                else:
                    rows.extend(_observation_row(ticker, entry, ob, year)
                                for ob in entry["obs"])
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
    def __init__(self, store: dict):
        self._store = store

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_screen_currency_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _run_screen(store: dict, fy: int = 2022, top_n=None):
    step = {"id": "s1", "op": "screen", "concept_family": "Capex", "fy": fy,
            "dimension": "NONE", "metric_kind": "value", "order": "desc", "top_n": top_n}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(store))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


@pytest.fixture(scope="session")
def fake_screen():
    """Run a flat FY capex screen over a fake store: ``fake_screen(store, fy, top_n)``."""
    return _run_screen
