"""Fixtures for period-disclosure (docs/specs/period-disclosure.md).

One I/O boundary is faked, nothing else: the database under `XbrlFactstoreScreen`
(`psycopg2.connect`). The fake holds the rows of `mv_geographic_facts` and
`mv_segment_matrix` read from xbrl_db (read-only, 2026-11-07, `fixtures/store_rows.json`) and
answers a view query the way Postgres would for the parts of the WHERE clause these tests
depend on: a parameter named like a row column is an equality filter (`fy` is
`fiscal_year <= fy`, a `%...%` parameter is `lower(segment_label) LIKE`), a parameter or
quoted literal naming a duration kind filters `duration_kind`, and `DISTINCT ON (ticker)`
keeps each filer's first row by `fiscal_year DESC, value DESC`. The publish-manifest probe
answers "absent", `entity_master` the roster, any other query no rows.

The SEC excerpts (`fixtures/companyfacts_excerpts.json`, `fsds_num.json`,
`ixbrl_excerpts.json`) carry each source's URL, SHA-256 and retrieval date; the tests read
their expected values from them.
"""

from __future__ import annotations

import copy
import json
import re
import types
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

FIXTURES = Path(__file__).parent / "fixtures"
KINDS = ("quarter", "half", "nine_month", "annual", "other")
_NUMERIC = ("value", "pct_of_total", "pct_of_axis_total")


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _decimalize(row: dict) -> dict:
    out = dict(row)
    for k in _NUMERIC:
        if out.get(k) is not None:
            out[k] = Decimal(out[k])
    return out


STORE = _load("store_rows.json")
FACTS = {r["fact_id"]: _decimalize(r) for r in STORE["mv_financial_facts"]}
GEO_ROWS = [_decimalize(r) for r in STORE["mv_geographic_facts"]]
# The segment SQL selects pct_of_axis_total AS pct_of_total.
SEG_ROWS = [
    {**{k: v for k, v in _decimalize(r).items() if k != "pct_of_axis_total"},
     "pct_of_total": _decimalize(r)["pct_of_axis_total"]}
    for r in STORE["mv_segment_matrix"]
]


def fact(fact_id: int) -> dict:
    """A stored `mv_financial_facts` row (value as Decimal), as a fresh copy."""
    return copy.deepcopy(FACTS[fact_id])


# --------------------------------------------------------------------------- SEC excerpts
COMPANYFACTS = _load("companyfacts_excerpts.json")
FSDS = _load("fsds_num.json")
IXBRL = _load("ixbrl_excerpts.json")


def cf_entry(accn: str, concept: str, start: str | None, end: str) -> dict:
    """The companyfacts entry of one accession, concept and period."""
    hits = [e["entry"] for e in COMPANYFACTS["entries"]
            if e["concept"] == concept and e["entry"]["accn"] == accn
            and e["entry"].get("start") == start and e["entry"]["end"] == end]
    assert len(hits) == 1, (accn, concept, start, end, hits)
    return hits[0]


def fsds_value(adsh: str, tag: str, ddate: str, qtrs: int, segments: str = "") -> int:
    """The FSDS `num` value (an integer amount) of one line."""
    hits = []
    for item in FSDS["lines"]:
        f = item["line"].split("\t")
        if (f[0], f[1], f[3], f[4], f[6]) == (adsh, tag, ddate, str(qtrs), segments):
            hits.append(f[8])
    assert len(hits) == 1, (adsh, tag, ddate, qtrs, segments, hits)
    return int(Decimal(hits[0]))


def ix_fact(accession_no: str, fid: str) -> dict:
    """An iXBRL fact: its scaled value and its context's start and end dates."""
    filing = next(f for f in IXBRL["filings"] if f["accession_no"] == accession_no)
    item = next(x for x in filing["facts"] if x["id"] == fid)
    el, ctx = item["element"], item["context"]
    shown = re.search(r">([^<]*)</ix:nonFraction>", el).group(1)
    scale = int(re.search(r'scale="(-?\d+)"', el).group(1))
    if "fixed-zero" in el:
        number = Decimal(0)
    else:
        number = Decimal(shown.replace(",", ""))
    if 'sign="-"' in el:
        number = -number
    return {
        "value": number * (Decimal(10) ** scale),
        "start": re.search(r"<xbrli:startDate>([^<]+)</xbrli:startDate>", ctx).group(1),
        "end": re.search(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", ctx).group(1),
    }


# --------------------------------------------------------------------------- database fake
def _param_values(params: dict) -> list:
    out: list = []
    for v in (params or {}).values():
        if isinstance(v, (list, tuple, set, frozenset)):
            out.extend(v)
        else:
            out.append(v)
    return out


def _select(table: list[dict], sql: str, params: dict) -> list[dict]:
    rows = [dict(r) for r in copy.deepcopy(table)]
    params = params or {}
    for key, val in params.items():
        if key == "fy":
            rows = [r for r in rows if r["fiscal_year"] <= int(val)]
        elif rows and key in rows[0] and not isinstance(val, (list, tuple)):
            rows = [r for r in rows if r.get(key) == val]
        elif isinstance(val, str) and val.startswith("%") and val.endswith("%"):
            needle = val.strip("%").lower()
            rows = [r for r in rows if needle in str(r.get("segment_label") or "").lower()]
    if "duration_kind" in sql:
        named = {v for v in _param_values(params) if v in KINDS}
        named |= {k for k in KINDS if f"'{k}'" in sql}
        if named:
            rows = [r for r in rows if r.get("duration_kind") in named]
    if re.search(r"DISTINCT\s+ON\s*\(\s*ticker\s*\)", sql, re.I):
        rows.sort(key=lambda r: (r["ticker"], -r["fiscal_year"],
                                 r["value"] is None, -(r["value"] or 0)))
        seen: set = set()
        kept = []
        for r in rows:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                kept.append(r)
        rows = kept
    return rows


class _Cursor:
    def __init__(self, geo: list, seg: list, roster: list, as_dict: bool):
        self._geo, self._seg, self._roster = geo, seg, roster
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:  # publish_manifest probe: absent, so the gate is inert
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            rows = [{"cik": cik, "ticker": t} for t, cik in sorted(self._roster)]
        elif "mv_geographic_facts" in sql:
            rows = _select(self._geo, sql, params)
        elif "mv_segment_matrix" in sql:
            rows = _select(self._seg, sql, params)
        else:
            rows = []
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
    def __init__(self, geo: list, seg: list, roster: list):
        self._geo, self._seg, self._roster = geo, seg, roster

    def cursor(self, cursor_factory=None):
        return _Cursor(self._geo, self._seg, self._roster, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_period_disclosure_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def run_screen(dimension: dict, family: str, fy: int, fiscal_period: str, *, ytd: bool = False,
               geo: list | None = None, seg: list | None = None, order: str = "desc"):
    """A screen over the fake database; the roster is the rows' filers."""
    geo = GEO_ROWS if geo is None else geo
    seg = SEG_ROWS if seg is None else seg
    roster = sorted({(r["ticker"], r["cik"]) for r in [*geo, *seg]})
    step = {"id": "s1", "op": "screen", "concept_family": family, "fy": fy,
            "fiscal_period": fiscal_period, "ytd": ytd, "dimension": dimension,
            "metric_kind": "value", "order": order, "top_n": None}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(geo, seg, roster))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


@pytest.fixture(scope="session")
def fx():
    """Everything the tests share, as one namespace (safe with hypothesis)."""
    return types.SimpleNamespace(
        KINDS=KINDS, fact=fact, GEO_ROWS=GEO_ROWS, SEG_ROWS=SEG_ROWS, run_screen=run_screen,
        cf_entry=cf_entry, fsds_value=fsds_value, ix_fact=ix_fact,
    )
