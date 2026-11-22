"""Fixtures for the s4-screen-defined-members spec tests (docs/specs/s4-screen-defined-members.md).

Committed SEC excerpts under ``fixtures/`` (each file's SHA-256 in ``fixtures/provenance.json``,
checked when this module loads):

- ``fsds/<quarter>/{sub,pre,num,tag}.tsv``: byte copies of SEC Financial Statement Data Sets
  lines (the raw quarterly zips named in the provenance) of three FY2025 10-Ks: PG, KO, QCOM.
  ``pre`` holds every balance-sheet line of each filing;
- ``companyfacts_excerpt.json``: every us-gaap USD instant entry SEC's companyfacts API lists
  for those filings (notes included).

The database is the only I/O boundary: ``FakeConn`` replaces ``psycopg2.connect`` and serves
the screen's reads from a store built from these excerpts:

- facts (``mv_financial_facts``): one row per companyfacts tag at the filing's balance-sheet
  date, value from companyfacts, stored ``period_end`` the next day (the view's next-midnight
  end), exact (``decimals`` None), a fixed ``fact_id`` per filing and tag;
- the flat query's candidate rows: those facts whose concept is a member of the family's
  mapping for the filer (``family_member``, the committed mapping: rank and role), with
  ``presented`` from FSDS ``pre``;
- the balance sheet (``mv_presentation_network``): FSDS ``pre`` lines in (report, line) order;
- labels (``concept_labels``): each line's FSDS ``plabel`` as the filer's own label, the label
  the key side reads.

Nothing in ``src`` is faked; nothing here reads the network, the current date or ``data/``.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg2
import pytest

from src.eval.keys.key_definitions import debt_key, member_status
from src.eval.keys.statement_witness import load_statement_rules
from src.eval.sec_reference.fsds import load_tables
from src.retriever.xbrl.family_resolution import family_member
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO = HERE.parents[2]
LIVE_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.yaml"
PROVENANCE = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


for _rel, _digest in PROVENANCE["files"].items():
    assert _sha(FIXTURES / _rel) == _digest, f"fixture {_rel} differs from its provenance"

COMPANYFACTS = json.loads((FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8"))
FSDS_DIRS = sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())

# ticker -> (10-digit CIK, accession, balance-sheet date (SEC, inclusive), fiscal-year label)
FILERS: dict[str, tuple[str, str, str, int]] = {
    f["ticker"]: (f"{f['cik']:010d}", f["accession"], f["balance_sheet_date"],
                  f["fiscal_year_label"])
    for f in PROVENANCE["filings"]
}
FY = 2025
M = 1_000_000
G = "http://fasb.org/us-gaap/2025:"   # the store form of a us-gaap concept
TERSE = "http://www.xbrl.org/2003/role/terseLabel"


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


def _next_day(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso) + dt.timedelta(days=1)


# ----------------------------------------------------------------------------- FSDS
_TABLES: list[dict] = []


def fsds_tables() -> dict:
    """The fixture FSDS tables (loaded once); callers copy before editing."""
    if not _TABLES:
        _TABLES.append(load_tables(FSDS_DIRS))
    return _TABLES[0]


def tables_copy() -> dict:
    return {k: [dict(r) for r in v] for k, v in fsds_tables().items()}


def bs_lines(tables: Mapping[str, list], ticker: str) -> list[dict]:
    """The filing's balance-sheet ``pre`` rows (not parenthetical), in (report, line) order."""
    adsh = FILERS[ticker][1]
    rows = [r for r in tables["pre"]
            if r["adsh"] == adsh and r["stmt"] == "BS" and r["inpth"] == "0"]
    return sorted(rows, key=lambda r: (int(r["report"]), int(r["line"])))


def fsds_value(ticker: str, tag: str) -> float:
    """The FSDS ``num`` value of a filing's tag at its balance-sheet month end."""
    _cik, adsh, end, _fy = FILERS[ticker]
    ddate = end[:7].replace("-", "")
    found = {float(r["value"]) for r in fsds_tables()["num"]
             if r["adsh"] == adsh and r["tag"] == tag and r["ddate"].startswith(ddate)
             and r["qtrs"] == "0" and r["segments"] == "" and r["coreg"] == ""}
    if not found:  # a 52/53-week year ends in the next month's FSDS ddate
        nxt = (dt.date.fromisoformat(end) + dt.timedelta(days=7)).strftime("%Y%m")
        found = {float(r["value"]) for r in fsds_tables()["num"]
                 if r["adsh"] == adsh and r["tag"] == tag and r["ddate"].startswith(nxt)
                 and r["qtrs"] == "0" and r["segments"] == "" and r["coreg"] == ""}
    assert len(found) == 1, (ticker, tag, found)
    return found.pop()


# ----------------------------------------------------------------------------- store
def _facts(ticker: str) -> list[dict]:
    """The filing's facts at its balance-sheet date, one per companyfacts tag."""
    cik, adsh, end, _fy = FILERS[ticker]
    doc = COMPANYFACTS["facts_by_cik"][str(int(cik))]["facts"]["us-gaap"]
    base = 1_000_000 * (1 + sorted(FILERS).index(ticker))
    out = []
    for n, tag in enumerate(sorted(doc)):
        own = [e for e in doc[tag]["units"]["USD"] if e["accn"] == adsh and e["end"] == end]
        if not own:
            continue
        vals = {e["val"] for e in own}
        assert len(vals) == 1, (ticker, tag, vals)
        fid = base + n
        out.append({"fact_id": fid, "accession_no": adsh, "qname": G + tag,
                    "value": float(own[0]["val"]), "unit": "USD", "period_end": _next_day(end),
                    "html_anchor": f"#f{fid}", "page_num": 1, "section_path": "Balance Sheet",
                    "decimals": None})
    return out


class Store:
    """The fake store: per filer its facts, balance-sheet lines and labels; editable copies."""

    def __init__(self, tickers: Sequence[str] = tuple(sorted(FILERS)), family: str = "Debt",
                 member_family: str = "Debt"):
        self.family = family                  # the concept_family the rows carry
        self.member_family = member_family    # the mapping their roles come from
        self.tickers = list(tickers)
        self.tables = tables_copy()
        self.facts = {t: _facts(t) for t in self.tickers}
        self.unit_override: dict[tuple[str, str], str] = {}
        self.no_accession: set[str] = set()
        self.fiscal_year: dict[str, int] = {}
        self.row_filter: Callable[[dict], bool] = lambda r: True
        self.flat_order: Callable[[list[dict]], list[dict]] = lambda rows: rows

    # edits ------------------------------------------------------------------------
    def drop_lines(self, ticker: str, tags: set[str]) -> None:
        """Removes balance-sheet lines (the facts stay, as notes facts)."""
        adsh = FILERS[ticker][1]
        self.tables["pre"] = [r for r in self.tables["pre"]
                              if not (r["adsh"] == adsh and r["tag"] in tags)]

    # reads -------------------------------------------------------------------------
    def presented(self, ticker: str) -> set[str]:
        return {r["tag"] for r in bs_lines(self.tables, ticker)}

    def candidate_rows(self) -> list[dict]:
        """The flat query's rows: every family-member fact of each filer's filing."""
        rows = []
        for t in self.tickers:
            cik, adsh, _end, fy = FILERS[t]
            shown = self.presented(t)
            for f in self.facts[t]:
                member = family_member(self.member_family, cik, f["qname"])
                if member is None:
                    continue
                row = {
                    "ticker": t, "cik": cik, "fiscal_year": self.fiscal_year.get(t, fy),
                    "fiscal_period": "FY", "value": f["value"],
                    "unit": self.unit_override.get((t, _local(f["qname"])), "USD"),
                    "period_end": f["period_end"], "period_start": None,
                    "section_path": f["section_path"], "html_anchor": f["html_anchor"],
                    "page_num": f["page_num"],
                    "accession_no": None if t in self.no_accession else adsh,
                    "fact_id": f["fact_id"], "concept": f["qname"],
                    "concept_family": self.family, "concept_pref_rank": member["rank"],
                    "concept_role": member["role"], "presented": _local(f["qname"]) in shown,
                    "segment_axis": None, "segment_member": None, "decimals": None,
                    "is_canonical_filing": True, "is_current_period": True,
                    "period_type": "instant",
                }
                if self.row_filter(row):
                    rows.append(row)
        return rows

    def presentation(self, accessions: Sequence[str]) -> list[dict]:
        out = []
        for t in self.tickers:
            if FILERS[t][1] not in accessions:
                continue
            for r in bs_lines(self.tables, t):
                out.append({"accession_no": r["adsh"],
                            "role_definition": f"{int(r['report']):07d} - Statement - "
                                               "CONSOLIDATED BALANCE SHEET",
                            "tree_sequence": int(r["line"]), "qname": G + r["tag"]})
        return out

    def filing_facts(self, accessions: Sequence[str]) -> list[dict]:
        out = []
        for t in self.tickers:
            if FILERS[t][1] in accessions:
                for f in self.facts[t]:
                    unit = self.unit_override.get((t, _local(f["qname"])), "USD")
                    out.append({**f, "unit": unit})
        return out

    def labels(self, qnames: Sequence[str]) -> list[dict]:
        out = []
        wanted = set(qnames)
        for t in self.tickers:
            for r in bs_lines(self.tables, t):
                if G + r["tag"] in wanted:
                    out.append({"concept": G + r["tag"], "cik_override": FILERS[t][0],
                                "label_role": TERSE, "label": r["plabel"]})
        return out


# ----------------------------------------------------------------------------- fake DB
_USD_LITERAL = re.compile(r"unit\s*=\s*'USD'")
_LATER = re.compile(r"fiscal_year\s*>\s*%\(fy\)s")


class FakeCursor:
    """Serves the screen's reads by the table each names (spec Notes, fake database)."""

    def __init__(self, store: Store, as_dict: bool):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        p = params if isinstance(params, Mapping) else {}
        s = self._store
        if "pg_attribute" in sql:              # coverage columns present
            self._rows = [(True,)]
            return
        if "to_regclass" in sql:               # no publish manifest
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            rows = [{"cik": FILERS[t][0], "ticker": t} for t in sorted(s.tickers)]
        elif "mv_presentation_network" in sql:
            rows = s.presentation(p["accessions"])
        elif "concept_labels" in sql:
            rows = s.labels(p["qnames"])
        elif "mv_financial_facts" in sql and "accessions" in p:
            rows = s.filing_facts(p["accessions"])
        elif "mv_financial_facts" in sql:
            rows = [r for r in s.candidate_rows()
                    if r["concept_family"] == p.get("concept_family")
                    and ("tickers" not in p or r["ticker"] in set(p["tickers"]))]
            if _USD_LITERAL.search(sql):        # the flat query: latest year <= fy
                rows = [r for r in rows if r["unit"] == "USD" and r["fiscal_year"] <= p["fy"]]
                latest: dict[str, int] = {}
                for r in rows:
                    latest[r["ticker"]] = max(latest.get(r["ticker"], 0), r["fiscal_year"])
                rows = s.flat_order([r for r in rows
                                     if r["fiscal_year"] == latest[r["ticker"]]])
            elif _LATER.search(sql):            # S1: no later filing in the fixtures
                rows = [r for r in rows if r["fiscal_year"] > p["fy"]]
        else:
            rows = []
        rows = [dict(r) for r in rows]
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
    def __init__(self, store: Store):
        self._store = store

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self) -> dict:
        return {"dbname": "fake_s4_screen_defined_members"}

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:
        pass


def run_screen(store: Store, fy: int = FY, top_n: int | None = None):
    """A flat FY ``XbrlFactstoreScreen.screen`` of the store's family over the covered filers."""
    step = {"id": "s1", "op": "screen", "fy": fy, "concept_family": store.family,
            "dimension": "NONE", "metric_kind": "value", "order": "desc", "top_n": top_n,
            "universe": "covered"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: FakeConn(store))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


def fact_id(ticker: str, tag: str) -> int:
    """The store's fact id of a filing's tag at its balance-sheet date."""
    found = [f["fact_id"] for f in _facts(ticker) if _local(f["qname"]) == tag]
    assert len(found) == 1, (ticker, tag)
    return found[0]


def ranked(result) -> dict[str, float]:
    """Ticker -> value of the ranked rows."""
    return {r["ticker"]: r["value"] for r in result.rows}


def exclusions(result) -> dict[str, str]:
    """Ticker -> reason of the disclosure's exclusions."""
    return {e["ticker"]: e["reason"] for e in (result.disclosure or {}).get("exclusions", [])}


# ----------------------------------------------------------------------------- key side
_RULES: list[dict] = []


def key_member(ticker: str, tables: Mapping[str, list] | None = None) -> dict:
    """The key side's screen member (``member_status`` of ``debt_key``) for a fixture filing."""
    if not _RULES:
        _RULES.append(load_statement_rules(LIVE_RULES))
    cik, adsh, end, fy = FILERS[ticker]
    doc = copy.deepcopy(COMPANYFACTS["facts_by_cik"][str(int(cik))])
    return member_status(debt_key(tables if tables is not None else fsds_tables(), _RULES[0],
                                  doc, cik, fy, adsh, end, "USD"))


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis)."""
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
