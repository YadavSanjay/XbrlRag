"""Fixtures for segment-lookup-screens (docs/specs/segment-lookup-screens.md).

The database is the I/O boundary. `psycopg2.connect` is replaced by a fake store that serves
fact records (committed excerpts of the corpus store, or synthetic ones built here) the way
the views serve them, following the spec's "Fake database contracts" (Notes):

* a query is answered from the first view named after a FROM (joins are not emulated);
* a parameter counts only when its ``%(name)s`` placeholder is in the SQL text;
* a query the contract does not cover raises ``LookupError`` naming it.

A record is a dict with the columns of ``mv_financial_facts`` (the fact's first non-default
dimension as ``segment_axis`` / ``segment_member`` / ``segment_label``, and
``has_multiple_dimensions``), plus ``dimension_labels`` / ``dimensions`` for a cell, plus
``geo_member`` / ``geo_code`` / ``geo_name`` / ``geo_type`` when the fact is a row of
``mv_geographic_facts`` (``is_geographic_row``).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import FactLookupResult, XbrlFactstoreLookup
from src.retriever.xbrl.xbrl_factstore_screen import ScreenResult, XbrlFactstoreScreen

FIXTURES = Path(__file__).parent / "fixtures"
# The gate runs in importlib mode, so this directory is not on the path by itself; the
# tests import the sibling helpers sls_rules and sls_store.
sys.path.insert(0, str(Path(__file__).resolve().parent))

_VIEWS = (
    "mv_financial_facts_multidim", "mv_financial_facts", "mv_segment_matrix",
    "mv_geographic_facts", "mv_derived_metrics", "concept_mapping", "dimension_catalog",
    "entity_master", "filing_registry",
)
_FROM = re.compile(r"\bFROM\s+([a-z_]+)", re.IGNORECASE)
_IS_NULL = re.compile(r"\bsegment_axis\s+IS\s+NULL\b", re.IGNORECASE)
_IS_NOT_NULL = re.compile(r"\bsegment_axis\s+IS\s+NOT\s+NULL\b", re.IGNORECASE)
_USD = re.compile(r"\bunit\s*=\s*'USD'", re.IGNORECASE)
_SINGLE_DIM = re.compile(
    r"has_multiple_dimensions\s*(=\s*FALSE|IS\s+FALSE|IS\s+NOT\s+TRUE)"
    r"|NOT\s+(\w+\.)?has_multiple_dimensions",
    re.IGNORECASE,
)
_FY_LE = re.compile(r"fiscal_year\s*<=\s*%\(fy\)s")
_FY_EQ = re.compile(r"fiscal_year\s*=\s*%\(fy\)s")


def load_fixture(name: str) -> dict:
    """A committed fixture of this spec, parsed."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ matching helpers
def _view(sql: str) -> str:
    for name in _FROM.findall(sql):
        if name.lower() in _VIEWS:
            return name.lower()
    raise LookupError(f"fake DB: no known view in query: {' '.join(sql.split())[:300]}")


def _has(sql: str, name: str) -> bool:
    return f"%({name})s" in sql


def _like(value: str | None, pattern: str, *, ci: bool = False) -> bool:
    """SQL LIKE (backslash escapes), case-insensitive for ILIKE."""
    if value is None:
        return False
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    flags = re.DOTALL | (re.IGNORECASE if ci else 0)
    return re.fullmatch("".join(out), value, flags) is not None


def _flat_compact(label: str | None) -> str | None:
    """The compact label as today's flat lookup SQL computes it before its LIKE."""
    if label is None:
        return None
    s = label.lower().replace("[member]", " ").replace("&", " and ")
    return re.sub(r"[^a-z0-9]", "", s)


def _multidim_compact(label: str) -> str:
    """The label form today's multi-dimension hint predicate compares."""
    return re.sub(r"\[member\]|[\[\]\s]", "", label.lower())


def _in(value: Any, wanted: Any) -> bool:
    if isinstance(wanted, (list, tuple, set, frozenset)):
        return value in wanted
    return value == wanted


def _local(axis: str | None) -> str | None:
    return re.split(r"[:}]", axis)[-1] if axis else None


def _common_filters(sql: str, params: Mapping[str, Any], r: dict) -> bool:
    if _FY_LE.search(sql) and not r["fiscal_year"] <= params["fy"]:
        return False
    if _FY_EQ.search(sql) and r["fiscal_year"] != params["fy"]:
        return False
    for key in ("cik", "ciks"):
        if _has(sql, key) and not _in(r["cik"], params[key]):
            return False
    for key in ("ticker", "tickers"):
        if _has(sql, key) and not _in(r["ticker"], params[key]):
            return False
    for key in ("concept_family", "fiscal_period", "period_type", "duration_kind"):
        if _has(sql, key) and r.get(key) != params[key]:
            return False
    if _has(sql, "pit_accession") and r["accession_no"] != params["pit_accession"]:
        return False
    if _has(sql, "fact_ids") and r["fact_id"] not in set(params["fact_ids"]):
        return False
    if _IS_NULL.search(sql) and r.get("segment_axis") is not None:
        return False
    if _IS_NOT_NULL.search(sql) and r.get("segment_axis") is None:
        return False
    if _USD.search(sql) and r.get("unit") != "USD":
        return False
    if _SINGLE_DIM.search(sql) and r.get("has_multiple_dimensions"):
        return False
    axes = set(params["segment_axes"]) if _has(sql, "segment_axes") else None
    if axes is not None and _local(r.get("segment_axis")) not in axes:
        return False
    return True


def _limit(sql: str, params: Mapping[str, Any], rows: list) -> list:
    if _has(sql, "limit") and params.get("limit") is not None:
        return rows[: int(params["limit"])]
    return rows


# ------------------------------------------------------------------ row shapes
_FLAT_KEYS = (
    "fact_id", "cik", "ticker", "accession_no", "fiscal_year", "fiscal_period", "concept",
    "concept_family", "concept_pref_rank", "value", "unit", "period_type", "period_start",
    "period_end", "duration_kind", "is_current_period", "segment_axis", "segment_member",
    "segment_label", "has_multiple_dimensions", "section_path",
)


def _flat_row(r: dict) -> dict:
    row = {k: r.get(k) for k in _FLAT_KEYS}
    row.update({
        "concept_role": None, "substitution_code": None, "substitution_note": None,
        "presented": False, "html_anchor": None, "page_num": None, "linked_chunk_ids": [],
        "decimals": None, "is_canonical_filing": True, "pct_of_total": None,
    })
    return row


def _multidim_row(r: dict, params: Mapping[str, Any], sql: str) -> dict:
    rank = None
    if _has(sql, "concept_qnames"):
        qnames = list(params["concept_qnames"])
        rank = list(params["concept_ranks"])[qnames.index(r["concept"])]
    row = _flat_row(r)
    row.update({
        "concept_family": None, "concept_pref_rank": rank, "segment_axis": None,
        "segment_member": None, "segment_label": None,
        "dimension_labels": dict(r["dimension_labels"]), "dimensions": dict(r["dimensions"]),
        "dimension_count": len(r["dimension_labels"]),
    })
    return row


def _segment_row(r: dict) -> dict:
    row = _flat_row(r)
    row.update({"pct_of_axis_total": None, "segment_rank": None})
    return row


def _geographic_row(r: dict) -> dict:
    row = _flat_row(r)
    row.update({k: r.get(k) for k in ("geo_member", "geo_code", "geo_name", "geo_type")})
    return row


class _Cursor:
    def __init__(self, db: _FakeDb, as_dict: bool):
        self._db = db
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        self._rows = self._db.answer(sql, params, self._as_dict)

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
    def __init__(self, db: _FakeDb):
        self._db = db

    def cursor(self, cursor_factory=None):
        return _Cursor(self._db, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_segment_lookup_screens_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _FakeDb:
    """Serves `records` as the views would. `kind` is "lookup" or "screen"."""

    def __init__(self, kind: str, records: Sequence[dict], roster: Sequence[str] = (),
                 concept_mapping: Sequence[dict] = (),
                 catalog: Mapping[str, Sequence[dict]] | None = None):
        self.kind = kind
        self.records = [dict(r) for r in records]
        self.roster = list(roster)
        self.concept_mapping = list(concept_mapping)
        self.catalog = dict(catalog or {})

    def answer(self, sql: str, params: Any, as_dict: bool) -> list:
        if "to_regclass('public.publish_manifest')" in sql or "pg_attribute" in sql:
            return [(False,)]
        view = _view(sql)
        p: Mapping[str, Any] = params if isinstance(params, Mapping) else {}
        if view == "concept_mapping":
            return self._concept_mapping(params, as_dict)
        if view == "dimension_catalog":
            axes = set(p.get("axes") or [])
            labels = sorted({c["member_label"] for c in self.catalog.get(p.get("cik"), [])
                             if _local(c["axis_qname"]) in axes})
            return [{"member_label": lbl} if as_dict else (lbl,) for lbl in labels]
        if view == "filing_registry":
            return []
        if view == "entity_master":
            ciks = {r["ticker"]: r["cik"] for r in self.records}
            rows = [{"cik": ciks.get(t, f"9{i:09d}"), "ticker": t}
                    for i, t in enumerate(sorted(self.roster))]
            return rows if as_dict else [(r["cik"], r["ticker"]) for r in rows]
        if view == "mv_derived_metrics":
            return []
        rows = self._view_rows(view, sql, p)
        return rows if as_dict else [tuple(r.values()) for r in rows]

    def _concept_mapping(self, params: Any, as_dict: bool) -> list:
        family, cik = params[0], params[1]
        rows = [m for m in self.concept_mapping
                if family == "Revenue" and not m.get("is_deprecated")
                and m["cik_override"] in (None, cik)]
        rows.sort(key=lambda m: (m["cik_override"] != cik, m["preference_rank"]))
        out = [(m["qname"], m["preference_rank"], None, None, None) for m in rows]
        if as_dict:
            keys = ("qname", "preference_rank", "concept_role", "substitution_code",
                    "substitution_note")
            return [dict(zip(keys, t, strict=True)) for t in out]
        return out

    def _view_rows(self, view: str, sql: str, p: Mapping[str, Any]) -> list[dict]:
        recs = [r for r in self.records if _common_filters(sql, p, r)]
        if view == "mv_financial_facts_multidim":
            recs = [r for r in recs if r.get("dimension_labels")]
            if _has(sql, "concept_qnames"):
                recs = [r for r in recs if r["concept"] in list(p["concept_qnames"])]
            elif _has(sql, "concept_like"):
                recs = [r for r in recs if _like(r["concept"], p["concept_like"], ci=True)]
            i = 0
            while _has(sql, f"hint_{i}"):
                pat = p[f"hint_{i}"]
                recs = [r for r in recs if any(_like(_multidim_compact(v), pat)
                                               for v in r["dimension_labels"].values())]
                i += 1
            qnames = list(p["concept_qnames"]) if _has(sql, "concept_qnames") else []
            recs.sort(key=lambda r: (r["fact_id"],), reverse=True)
            recs.sort(key=lambda r: qnames.index(r["concept"]) if r["concept"] in qnames else 0)
            recs.sort(key=lambda r: r["period_end"] or "", reverse=True)
            return _limit(sql, p, [_multidim_row(r, p, sql) for r in recs])

        if view == "mv_segment_matrix":
            recs = [r for r in recs if r.get("segment_axis") and r.get("is_current_period")
                    and r.get("period_type") == "duration"]
            shape = _segment_row
        elif view == "mv_geographic_facts":
            recs = [r for r in recs if r.get("is_geographic_row")]
            if _has(sql, "geo_code"):
                recs = [r for r in recs if r.get("geo_code") == p["geo_code"]]
            shape = _geographic_row
        else:
            shape = _flat_row
        if _has(sql, "axis_like"):
            recs = [r for r in recs if _like(r.get("segment_axis"), p["axis_like"])]
        if _has(sql, "member_qname"):
            recs = [r for r in recs if r.get("segment_member") == p["member_qname"]]
        if _has(sql, "member_like"):
            if view == "mv_financial_facts" and self.kind == "lookup":
                recs = [r for r in recs if _like(_flat_compact(r.get("segment_label")),
                                                 p["member_like"])]
            else:
                recs = [r for r in recs
                        if _like((r.get("segment_label") or "").lower(), p["member_like"])]

        if re.search(r"DISTINCT\s+ON\s*\(\s*ticker\s*\)", sql, re.IGNORECASE):
            best: dict[str, dict] = {}
            by_value = re.search(r"\bvalue\s+DESC\b", sql, re.IGNORECASE) is not None
            for r in recs:
                if by_value:
                    value = r["value"] if r["value"] is not None else float("-inf")
                    key = (r["fiscal_year"], value)
                else:
                    rank = r.get("concept_pref_rank")
                    key = (r["fiscal_year"], r["period_end"] or "",
                           -(rank if rank is not None else 10**9), r["fact_id"])
                cur = best.get(r["ticker"])
                if cur is None or key > cur[0]:
                    best[r["ticker"]] = (key, r)
            recs = [best[t][1] for t in sorted(best)]
        else:
            recs.sort(key=lambda r: r["fact_id"], reverse=True)
            recs.sort(key=lambda r: r.get("concept_pref_rank") if r.get("concept_pref_rank")
                      is not None else 10**9)
            recs.sort(key=lambda r: r["period_end"] or "", reverse=True)
        return _limit(sql, p, [shape(r) for r in recs])


def _run_lookup(records: Sequence[dict], step: dict, concept_mapping: Sequence[dict],
                catalog: Mapping[str, Sequence[dict]] | None = None) -> FactLookupResult:
    db = _FakeDb("lookup", records, concept_mapping=concept_mapping, catalog=catalog)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(db))
        return XbrlFactstoreLookup(db_conn_params={}).lookup(step)


def _run_screen(records: Sequence[dict], roster: Sequence[str], step: dict) -> ScreenResult:
    db = _FakeDb("screen", records, roster=roster)
    full = {"id": "s1", "op": "screen", "concept_family": "Revenue", "metric_kind": "value",
            "order": "desc", "top_n": None, **step}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(db))
        return XbrlFactstoreScreen(db_conn_params={}).screen(full)


@pytest.fixture(scope="session")
def run_lookup() -> Callable[..., FactLookupResult]:
    """`run_lookup(records, step, concept_mapping, catalog=None)` over the fake store."""
    return _run_lookup


@pytest.fixture(scope="session")
def run_screen() -> Callable[..., ScreenResult]:
    """`run_screen(records, roster, step)`: a screen step (defaults filled) over the fake."""
    return _run_screen


@pytest.fixture(scope="session")
def lookup_fixture() -> dict:
    return load_fixture("lookup_lly_hd.json")


@pytest.fixture(scope="session")
def segment_fixture() -> dict:
    return load_fixture("screen_segment_fy2024.json")


@pytest.fixture(scope="session")
def geographic_fixture() -> dict:
    return load_fixture("screen_geographic_fy2024.json")

