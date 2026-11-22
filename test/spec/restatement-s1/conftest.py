"""Fixtures for the restatement-s1 spec tests (docs/specs/restatement-s1.md).

Real excerpts, committed under ``fixtures/`` (provenance in each file and in the spec's Notes):

- ``companyfacts.json``: SEC companyfacts entries (report forms, filed from 2021-06-01) of 11
  filers for the concepts used here, plus NetIncomeLoss, Assets and the two cover facts;
- ``dei.json``: the DEI of every corpus filing of those filers, from its iXBRL instance;
- ``fsds.json``: FSDS sub/num rows of the oracle filings.

Store rows (``mv_financial_facts`` shape) are built from the companyfacts entries of corpus
filings, as the spec's Notes describe. The database is the only I/O boundary: ``FakeConn``
replaces ``psycopg2.connect`` and follows the spec's fake-database contract. Nothing here
reads the network, the current date or anything outside this directory.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPANYFACTS: dict[str, dict] = json.loads((FIXTURES / "companyfacts.json").read_text("utf-8"))
DEI: dict[str, dict] = json.loads((FIXTURES / "dei.json").read_text("utf-8"))
FSDS: dict[str, Any] = json.loads((FIXTURES / "fsds.json").read_text("utf-8"))

CIK = {t: f"{doc['cik']:010d}" for t, doc in COMPANYFACTS.items()}

# The store's concept family of each tag used here (concept_mapping, rank 1 for these filers).
FAMILY = {
    "EarningsPerShareDiluted": "EarningsPerShareDiluted",
    "NetCashProvidedByUsedInFinancingActivities": "CashFromFinancing",
    "AccountsReceivableNetCurrent": "AccountsReceivable",
    "DepreciationDepletionAndAmortization": "DepreciationAndAmortization",
    "CommonStockDividendsPerShareDeclared": "DividendsPerShare",
    "Goodwill": "Goodwill",
    "BusinessExitCosts1": "RestructuringCharges",
    "RestructuringAndRelatedCostIncurredCost": "RestructuringCharges",
    "NetIncomeLoss": "NetIncome",
    "Assets": "TotalAssets",
    "EntityCommonStockSharesOutstanding": "SharesOutstanding",
}
INSTANT_FAMILIES = {"AccountsReceivable", "Goodwill", "TotalAssets", "SharesOutstanding"}

# The corpus's Part III-only 10-K/As (report N5b): each carries only the two cover facts and
# filing_registry holds it superseded_by its 10-K (annual-amendment-handling C3).
PART_III = {
    "0000002488-26-000021": "0000002488-26-000018",   # AMD FY2025, filed the 10-K's day
    "0001193125-24-093194": "0001628280-24-006850",   # MRK FY2023
    "0001104659-25-042659": "0001628280-25-003063",   # TSLA FY2024
    "0001104659-26-053166": "0001628280-26-003952",   # TSLA FY2025
}
# TSLA's 2012 full-statement 10-K/A (538 companyfacts entries, balance sheet and income
# statement re-filed) and the 10-K it amends; outside the corpus window.
TSLA_2012_10K = "0001193125-12-081990"
TSLA_2012_10KA = "0001193125-12-137560"

# The synthetic restating amendment (not a real filing): TSLA FY2025 re-filed after its Part III
# 10-K/A, in the 2012 one's shape (Assets and NetIncomeLoss re-filed), net income changed.
SYNTH_10KA = "0001318605-26-900001"
SYNTH_FILED = "2026-06-01"
SYNTH_NET_INCOME_DELTA = -100_000_000

DEI_NAMESPACE = "http://xbrl.sec.gov/dei/2024"
ONE_DAY = dt.timedelta(days=1)


def iso(value: Any) -> str | None:
    """A date or ISO string as an ISO date string."""
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)[:10]


def next_day(value: str) -> str:
    """The store's next-midnight end of an inclusive SEC date."""
    return (dt.date.fromisoformat(value) + ONE_DAY).isoformat()


def local(qname: Any) -> str:
    """The local name of a QName (after the last ':')."""
    return str(qname).rsplit(":", 1)[-1]


# ----------------------------------------------------------------------------- FSDS oracle
def fsds_value(adsh: str, tag: str, ddate: str, qtrs: int) -> float:
    """The FSDS ``num`` value of one filing, tag, month-end ``ddate`` (YYYYMMDD) and ``qtrs``."""
    found = {float(r["value"]) for r in FSDS["num"]
             if r["adsh"] == adsh and r["tag"] == tag and r["ddate"] == ddate
             and int(r["qtrs"]) == qtrs}
    assert len(found) == 1, (adsh, tag, ddate, qtrs, found)
    return found.pop()


# The oracle points the tests quote as literals, checked against FSDS when the suite loads, so
# a regenerated fixture that drifts from a literal fails loudly.
ORACLE_POINTS = [
    ("0001045810-25-000023", "EarningsPerShareDiluted", "20230131", 4, 0.17),
    ("0001045810-25-000023", "EarningsPerShareDiluted", "20240131", 4, 1.19),
    ("0001045810-25-000023", "EarningsPerShareDiluted", "20250131", 4, 2.94),
    ("0001045810-23-000017", "EarningsPerShareDiluted", "20230131", 4, 1.74),
    ("0001045810-24-000029", "EarningsPerShareDiluted", "20240131", 4, 11.93),
    ("0000104169-24-000056", "EarningsPerShareDiluted", "20230131", 4, 1.42),
    ("0000104169-24-000056", "EarningsPerShareDiluted", "20240131", 4, 1.91),
    ("0000104169-25-000021", "EarningsPerShareDiluted", "20230131", 4, 1.42),
    ("0000104169-23-000020", "EarningsPerShareDiluted", "20230131", 4, 4.27),
    ("0001730168-24-000139", "EarningsPerShareDiluted", "20221031", 4, 2.65),
    ("0001730168-22-000118", "EarningsPerShareDiluted", "20221031", 4, 26.53),
    ("0000050863-24-000010", "NetCashProvidedByUsedInFinancingActivities", "20221231", 4,
     1_115_000_000.0),
    ("0000050863-24-000010", "NetCashProvidedByUsedInFinancingActivities", "20231231", 4,
     8_505_000_000.0),
    ("0000050863-25-000009", "NetCashProvidedByUsedInFinancingActivities", "20221231", 4,
     1_115_000_000.0),
    ("0000050863-23-000006", "NetCashProvidedByUsedInFinancingActivities", "20221231", 4,
     1_361_000_000.0),
    ("0000002488-25-000012", "AccountsReceivableNetCurrent", "20231231", 0, 4_323_000_000.0),
    ("0000002488-24-000012", "AccountsReceivableNetCurrent", "20231231", 0, 5_376_000_000.0),
    ("0000354950-25-000085", "DepreciationDepletionAndAmortization", "20240131", 4,
     3_061_000_000.0),
    ("0000354950-24-000062", "DepreciationDepletionAndAmortization", "20240131", 4,
     3_247_000_000.0),
    ("0001628280-26-019436", "DepreciationDepletionAndAmortization", "20240131", 4,
     3_061_000_000.0),
]
for _adsh, _tag, _ddate, _qtrs, _value in ORACLE_POINTS:
    assert fsds_value(_adsh, _tag, _ddate, _qtrs) == pytest.approx(_value, abs=1e-9), _adsh


# ----------------------------------------------------------------------------- companyfacts
def companyfacts(ticker: str, scenario: str | None = None, corpus_only: bool = False) -> dict:
    """A fresh copy of one filer's companyfacts excerpt.

    Args:
        ticker: The filer.
        scenario: ``"restating_10ka"`` adds the synthetic restating TSLA amendment.
        corpus_only: Keep only entries of corpus filings (those in ``dei.json``), the
            filings the store holds.
    """
    doc = copy.deepcopy(COMPANYFACTS[ticker])
    if scenario == "restating_10ka":
        _add_synthetic_amendment(doc)
    if corpus_only:
        for concepts in doc["facts"].values():
            for node in concepts.values():
                for unit in list(node["units"]):
                    node["units"][unit] = [e for e in node["units"][unit]
                                           if e["accn"] in DEI or e["accn"] == SYNTH_10KA]
    return doc


def _add_synthetic_amendment(doc: dict) -> None:
    """Re-file TSLA's FY2025 10-K statement and cover entries as the synthetic 10-K/A."""
    tenk = "0001628280-26-003952"
    for taxonomy, tag in (("us-gaap", "Assets"), ("us-gaap", "NetIncomeLoss"),
                          ("dei", "EntityCommonStockSharesOutstanding")):
        node = doc["facts"][taxonomy][tag]
        for entries in node["units"].values():
            copies = []
            for e in entries:
                if e["accn"] != tenk:
                    continue
                c = {k: v for k, v in e.items() if k != "frame"}
                c.update(accn=SYNTH_10KA, form="10-K/A", filed=SYNTH_FILED)
                if tag == "NetIncomeLoss" and c.get("end") == "2025-12-31" \
                        and c.get("start") == "2025-01-01":
                    c["val"] = c["val"] + SYNTH_NET_INCOME_DELTA
                copies.append(c)
            entries.extend(copies)


def entries(doc: dict, tag: str, unit: str, taxonomy: str = "us-gaap") -> list[dict]:
    """The entries of one concept and unit in a companyfacts document."""
    return doc["facts"][taxonomy][tag]["units"][unit]


def entry_value(ticker: str, tag: str, accn: str, end: str, unit: str = "USD") -> float:
    """The value one filing reports for one period end in the excerpt."""
    tax = "dei" if tag.startswith("Entity") else "us-gaap"
    vals = {e["val"] for e in entries(COMPANYFACTS[ticker], tag, unit, tax)
            if e["accn"] == accn and e["end"] == end}
    assert len(vals) == 1, (ticker, tag, accn, end, vals)
    return float(vals.pop())


# ----------------------------------------------------------------------------- store rows
def _duration_kind(start: str | None, stored_end: str) -> str:
    """mv_financial_facts.duration_kind: by the stored length in days."""
    if start is None:
        return "instant"
    days = (dt.date.fromisoformat(stored_end) - dt.date.fromisoformat(start)).days
    if days <= 100:
        return "quarter"
    if days <= 190:
        return "half"
    if days <= 285:
        return "nine_month"
    return "annual"


def _dei(accn: str) -> dict:
    if accn == SYNTH_10KA:
        base = dict(DEI["0001628280-26-003952"])
        base.update(form="10-K/A", filed=SYNTH_FILED, document_type="10-K/A")
        return base
    return DEI[accn]


def store_rows(ticker: str, tags: Iterable[str] | None = None,
               scenario: str | None = None) -> list[dict]:
    """The store's rows of one filer, one per companyfacts entry of a corpus filing.

    Args:
        ticker: The filer.
        tags: Keep only these tags (local names); all when ``None``.
        scenario: As in ``companyfacts``.

    Returns:
        Fresh dicts (callers may change them).
    """
    key = (ticker, None if tags is None else tuple(sorted(tags)), scenario)
    if key not in _ROWS:
        _ROWS[key] = _build_rows(ticker, tags, scenario)
    return [dict(r, linked_chunk_ids=[]) for r in _ROWS[key]]


_ROWS: dict[tuple, list[dict]] = {}


def _build_rows(ticker: str, tags: Iterable[str] | None, scenario: str | None) -> list[dict]:
    doc = companyfacts(ticker, scenario)
    wanted = None if tags is None else set(tags)
    canonical_off = set(PART_III)
    if scenario == "restating_10ka":
        canonical_off |= {"0001628280-26-003952"}   # superseded by the restating amendment
    rows: list[dict] = []
    seq = 0
    for taxonomy, concepts in sorted(doc["facts"].items()):
        for tag, node in sorted(concepts.items()):
            if wanted is not None and tag not in wanted:
                continue
            for unit, es in sorted(node["units"].items()):
                for e in es:
                    accn = e["accn"]
                    if accn not in DEI and accn != SYNTH_10KA:
                        continue
                    d = _dei(accn)
                    seq += 1
                    if taxonomy == "dei":
                        namespace = DEI_NAMESPACE
                    else:
                        namespace = d["us_gaap_namespace"] or "http://fasb.org/us-gaap/2025"
                    start = e.get("start")
                    end = next_day(e["end"])
                    family = FAMILY.get(tag)
                    rows.append({
                        "fact_id": 10_000_000 * (1 + sorted(CIK).index(ticker)) + seq,
                        "cik": CIK[ticker], "ticker": ticker, "accession_no": accn,
                        "fiscal_year": int(d["fiscal_year_focus"]),
                        "fiscal_period": d["fiscal_period_focus"],
                        "document_type": d["document_type"], "form_type": d["form"],
                        "filed_date": d["filed"], "is_amendment": d["form"].endswith("/A"),
                        "is_canonical_filing": accn not in canonical_off,
                        "document_period_end": d["document_period_end"],
                        "concept": f"{namespace}:{tag}", "concept_family": family,
                        "concept_pref_rank": 1, "concept_role": None,
                        "substitution_code": None, "substitution_note": None,
                        "presented": False,
                        "value": None if e["val"] is None else float(e["val"]),
                        "unit": "shares*USD" if unit == "USD/shares" else unit,
                        "period_type": "duration" if start else "instant",
                        "period_start": start, "period_end": end,
                        "duration_kind": _duration_kind(start, end),
                        "is_current_period": e["end"] == d["document_period_end"],
                        "segment_axis": None, "segment_member": None, "segment_label": None,
                        "dimension_labels": None, "section_path": None, "html_anchor": None,
                        "page_num": None, "linked_chunk_ids": [], "decimals": None,
                        "pct_of_total": None,
                    })
    return rows


def registry_rows(tickers: Iterable[str], scenario: str | None = None) -> list[dict]:
    """filing_registry rows of the corpus filings of these filers."""
    out = []
    accessions = [a for a, d in DEI.items() if d["ticker"] in set(tickers)]
    if scenario == "restating_10ka" and "TSLA" in set(tickers):
        accessions.append(SYNTH_10KA)
    for accn in accessions:
        d = _dei(accn)
        superseded = PART_III.get(accn)
        if scenario == "restating_10ka":
            if accn in ("0001628280-26-003952", "0001104659-26-053166"):
                superseded = SYNTH_10KA
        out.append({
            "accession_no": accn, "cik": d["cik"], "ticker": d["ticker"],
            "fiscal_year": int(d["fiscal_year_focus"]),
            "fiscal_period": d["fiscal_period_focus"], "form_type": d["form"],
            "filed_date": d["filed"], "is_amendment": d["form"].endswith("/A"),
            "superseded_by": superseded,
        })
    return sorted(out, key=lambda r: (r["filed_date"], r["accession_no"]))


# ----------------------------------------------------------------------------- fake database
_USD_LITERAL = re.compile(r"unit\s*=\s*'USD'")
# Equality parameters the fake honours (spec Notes, fake-database contract).
_EQ_PARAMS = {"cik": "cik", "ticker": "ticker", "concept_family": "concept_family",
              "period_type": "period_type", "duration_kind": "duration_kind",
              "fiscal_period": "fiscal_period"}


def _matches(row: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    for name, column in _EQ_PARAMS.items():
        if name in params and params[name] is not None and row.get(column) != params[name]:
            return False
    if "tickers" in params and params["tickers"] is not None \
            and row.get("ticker") not in set(params["tickers"]):
        return False
    return not ("ciks" in params and params["ciks"] is not None
                and row.get("cik") not in set(params["ciks"]))


def _screen_pick(rows: Sequence[dict], params: Mapping[str, Any]) -> list[dict]:
    """The flat screen query: per filer, the canonical own-column rows of its latest year <= fy.

    Every candidate row of that year, in ticker order: the screen picks one per filer itself
    (docs/specs/screen-candidate-order.md, requirement 1; rewritten by that spec, which
    replaced the SQL's ``DISTINCT ON`` pick). A single-row-per-filer query is answered the same
    way: the filer's row is among these, and the screen's own choice picks it.
    """
    fy = params.get("fy")
    own: list[dict] = []
    for r in rows:
        if not _matches(r, params) or r["segment_axis"] is not None or r["unit"] != "USD":
            continue
        if not (r["is_canonical_filing"] and r["is_current_period"]):
            continue
        if fy is not None and r["fiscal_year"] > fy:
            continue
        own.append(r)
    latest: dict[str, int] = {}
    for r in own:
        latest[r["ticker"]] = max(latest.get(r["ticker"], r["fiscal_year"]), r["fiscal_year"])
    return sorted((r for r in own if r["fiscal_year"] == latest[r["ticker"]]),
                  key=lambda r: (r["ticker"], r["fact_id"]))


class FakeCursor:
    """A cursor over the fake store (spec Notes, fake-database contract)."""

    def __init__(self, store: FakeStore, as_dict: bool):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        p = params if isinstance(params, Mapping) else {}
        if "to_regclass" in sql:
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            rows = [{"cik": CIK[t], "ticker": t} for t in sorted({r["ticker"]
                                                                  for r in self._store.rows})]
        elif "filing_registry" in sql:
            rows = [dict(r) for r in self._store.registry
                    if "cik" not in p or r["cik"] == p["cik"]]
        elif "mv_financial_facts" in sql:
            if _USD_LITERAL.search(sql):
                rows = [dict(r) for r in _screen_pick(self._store.rows, p)]
            else:
                rows = [dict(r) for r in self._store.rows if _matches(r, p)]
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


class FakeStore:
    """The store's rows and registry for the fake connection."""

    def __init__(self, rows: Sequence[dict], registry: Sequence[dict]):
        self.rows = list(rows)
        self.registry = list(registry)


class FakeConn:
    """A psycopg2 connection over a ``FakeStore``."""

    def __init__(self, store: FakeStore):
        self._store = store

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self) -> dict:
        return {"dbname": "fake_restatement_s1_db"}

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:
        pass


def run_lookup(rows: Sequence[dict], ticker: str, fy: int, family: str,
               registry: Sequence[dict] | None = None, **step: Any):
    """``XbrlFactstoreLookup.lookup`` of one non-dimensional step over the fake store."""
    store = FakeStore(rows, registry if registry is not None else registry_rows([ticker]))
    full = {"id": "s1", "op": "lookup", "cik": CIK[ticker], "ticker": ticker, "fy": fy,
            "concept_family": family, "dimensions": "NONE", **step}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreLookup(db_conn_params={}).lookup(full)


def run_screen(rows: Sequence[dict], fy: int, family: str, top_n: int | None = None):
    """A flat FY ``XbrlFactstoreScreen.screen`` of one concept family over the fake store."""
    tickers = sorted({r["ticker"] for r in rows})
    store = FakeStore(rows, registry_rows(tickers))
    step = {"id": "s1", "op": "screen", "fy": fy, "concept_family": family,
            "dimension": "NONE", "metric_kind": "value", "order": "desc", "top_n": top_n}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


# ----------------------------------------------------------------------------- helpers
def accessions(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """The accessions of a set of rows."""
    return {r["accession_no"] for r in rows}


def values(rows: Iterable[Mapping[str, Any]]) -> set[float]:
    """The distinct values of a set of rows."""
    return {r["value"] for r in rows}


def only(rows: Sequence[Mapping[str, Any]], tag: str) -> list[Mapping[str, Any]]:
    """The rows of one concept (local name)."""
    return [r for r in rows if local(r["concept"]) == tag]


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis).

    Built from this module's globals: every spec directory has a module named ``conftest``,
    so ``sys.modules`` cannot name this one.
    """
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
