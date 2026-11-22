"""Fixtures for the yoy-growth-s1 spec tests (docs/specs/yoy-growth-s1.md).

Real excerpts, committed under ``fixtures/``: byte-identical copies of suite-templates-s1's
(provenance in each file and in that spec's Notes). The store rows and the fake database
follow suite-templates-s1's Notes, which this spec adopts:

- ``companyfacts.json``: SEC companyfacts entries (report forms, filed from 2021-06-01) of
  INTC, META, LLY, COST and TSM for the tags used here;
- ``dei.json``: the DEI of every corpus filing of those filers, from its iXBRL instance;
- ``fsds.json``: FSDS sub/num rows of the oracle filings.

Store rows (``mv_financial_facts`` shape) are built from the companyfacts entries of corpus
filings, as the spec's Notes describe. The database is the only I/O boundary: ``FakeConn``
follows the spec's fake-database contract. Nothing here reads the network, the current date
or anything outside this directory and the committed template file.
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

import pytest
import yaml

from src.eval.suites.sql_derived import SqlDerivedBuilder

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
TEMPLATES_PATH = HERE.parents[2] / "configs" / "eval" / "sql_derived_templates.yaml"
COMPANYFACTS: dict[str, dict] = json.loads((FIXTURES / "companyfacts.json").read_text("utf-8"))
DEI: dict[str, dict] = json.loads((FIXTURES / "dei.json").read_text("utf-8"))
FSDS: dict[str, Any] = json.loads((FIXTURES / "fsds.json").read_text("utf-8"))

TICKERS = sorted(COMPANYFACTS)
CIK = {t: f"{doc['cik']:010d}" for t, doc in COMPANYFACTS.items()}

# The store's concept family of each tag used here.
FAMILY = {
    "NetCashProvidedByUsedInFinancingActivities": "CashFromFinancing",
    "NetCashProvidedByUsedInInvestingActivities": "CashFromInvesting",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "PaymentsToAcquirePropertyPlantAndEquipment": "Capex",
    "Assets": "TotalAssets",
    "Goodwill": "Goodwill",
    "ProfitLoss": "NetIncome",
}

# The YoY template (requirement 2).
TEMPLATE_IDS = ["aq-2c-yoy-growth"]
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
def fsds_value(adsh: str, tag: str, ddate: str, qtrs: int, uom: str = "USD") -> float:
    """The FSDS ``num`` value of one filing, tag, month-end ``ddate``, ``qtrs`` and unit."""
    found = {float(r["value"]) for r in FSDS["num"]
             if r["adsh"] == adsh and r["tag"] == tag and r["ddate"] == ddate
             and int(r["qtrs"]) == qtrs and r["uom"] == uom}
    assert len(found) == 1, (adsh, tag, ddate, qtrs, uom, found)
    return found.pop()


# The oracle points the tests quote as literals, checked against FSDS when the suite loads, so
# a regenerated fixture that drifts from a literal fails loudly.
FIN = "NetCashProvidedByUsedInFinancingActivities"
INV = "NetCashProvidedByUsedInInvestingActivities"
REV = "RevenueFromContractWithCustomerExcludingAssessedTax"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
NI = "ProfitLoss"
ORACLE_POINTS = [
    # INTC revenue
    ("0000050863-26-000011", REV, "20231231", 4, "USD", 54_228_000_000.0),
    ("0000050863-25-000009", REV, "20221231", 4, "USD", 63_054_000_000.0),
    ("0000050863-26-000011", REV, "20241231", 4, "USD", 53_101_000_000.0),
    ("0000050863-26-000011", REV, "20251231", 4, "USD", 52_853_000_000.0),
    # INTC financing cash flow: FY2022 restated by later 10-Ks
    ("0000050863-26-000011", FIN, "20231231", 4, "USD", 8_505_000_000.0),
    ("0000050863-25-000009", FIN, "20221231", 4, "USD", 1_115_000_000.0),
    ("0000050863-23-000006", FIN, "20221231", 4, "USD", 1_361_000_000.0),
    # META capex: both years restated by later 10-Ks
    ("0001628280-26-003942", CAPEX, "20231231", 4, "USD", 27_045_000_000.0),
    ("0001326801-25-000017", CAPEX, "20221231", 4, "USD", 31_186_000_000.0),
    ("0001326801-24-000012", CAPEX, "20231231", 4, "USD", 27_266_000_000.0),
    ("0001326801-23-000013", CAPEX, "20221231", 4, "USD", 31_431_000_000.0),
    # TSM net income, TWD (reporting currency) and the USD convenience translations
    ("0001628280-26-025362", NI, "20231231", 4, "TWD", 851_027_700_000.0),
    ("0001193125-25-083423", NI, "20221231", 4, "TWD", 993_294_700_000.0),
    ("0001628280-26-025362", NI, "20241231", 4, "TWD", 1_157_523_900_000.0),
    ("0001628280-26-025362", NI, "20251231", 4, "TWD", 1_695_124_900_000.0),
    ("0001193125-24-099840", NI, "20231231", 4, "USD", 27_793_200_000.0),
    ("0001628280-26-025362", NI, "20251231", 4, "USD", 54_036_500_000.0),
]
for _a, _tag, _ddate, _q, _uom, _value in ORACLE_POINTS:
    assert fsds_value(_a, _tag, _ddate, _q, _uom) == pytest.approx(_value, abs=1e-6), _a


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


def store_rows(ticker: str, families: Iterable[str] | None = None) -> list[dict]:
    """The store's rows of one filer, one per companyfacts entry of a corpus filing.

    Args:
        ticker: The filer.
        families: Keep only these concept families; all when ``None``.

    Returns:
        Fresh dicts (callers may change them).
    """
    if ticker not in _ROWS:
        _ROWS[ticker] = _build_rows(ticker)
    wanted = None if families is None else set(families)
    return [dict(r) for r in _ROWS[ticker]
            if wanted is None or r["concept_family"] in wanted]


_ROWS: dict[str, list[dict]] = {}


def _fsds_entries(ticker: str) -> dict:
    """TSM's entries in companyfacts shape, from FSDS ``num`` (calendar years, ``qtrs`` 4).

    companyfacts (retrieved 2026-11-07) holds no entry of TSM's 2026 20-F
    0001628280-26-025362, which the store holds; FSDS has all four 20-Fs. TSM's fiscal year is
    the calendar year, so FSDS's month-end ``ddate`` is the exact end.
    """
    units: dict[str, list[dict]] = {}
    for r in FSDS["num"]:
        if r["adsh"] not in DEI or DEI[r["adsh"]]["ticker"] != ticker or int(r["qtrs"]) != 4:
            continue
        end = dt.date(int(r["ddate"][:4]), int(r["ddate"][4:6]), int(r["ddate"][6:]))
        start = dt.date(end.year, 1, 1)
        units.setdefault(r["uom"], []).append({
            "accn": r["adsh"], "start": start.isoformat(), "end": end.isoformat(),
            "val": float(r["value"])})
    return {"ifrs-full": {"ProfitLoss": {"units": units}}}


# Filers whose store rows come from FSDS instead of companyfacts (see _fsds_entries).
FSDS_ROW_SOURCE = {"TSM"}


def _build_rows(ticker: str) -> list[dict]:
    facts = (_fsds_entries(ticker) if ticker in FSDS_ROW_SOURCE
             else COMPANYFACTS[ticker]["facts"])
    rows: list[dict] = []
    seq = 0
    for taxonomy, concepts in sorted(facts.items()):
        for tag, node in sorted(concepts.items()):
            for unit, es in sorted(node["units"].items()):
                for e in es:
                    accn = e["accn"]
                    if accn not in DEI:
                        continue
                    d = DEI[accn]
                    seq += 1
                    namespace = (d["ifrs_namespace"] if taxonomy == "ifrs-full"
                                 else d["us_gaap_namespace"])
                    start = e.get("start")
                    end = next_day(e["end"])
                    rows.append({
                        "fact_id": 10_000_000 * (1 + TICKERS.index(ticker)) + seq,
                        "cik": CIK[ticker], "ticker": ticker, "accession_no": accn,
                        "fiscal_year": int(d["fiscal_year_focus"]),
                        "fiscal_period": d["fiscal_period_focus"],
                        "document_type": d["document_type"], "form_type": d["form"],
                        "filed_date": d["filed"], "is_amendment": d["form"].endswith("/A"),
                        "is_canonical_filing": True,
                        "document_period_end": d["document_period_end"],
                        "concept": f"{namespace}:{tag}", "concept_family": FAMILY[tag],
                        "concept_pref_rank": 1, "concept_role": None, "presented": False,
                        "value": None if e["val"] is None else float(e["val"]),
                        "unit": unit,
                        "period_type": "duration" if start else "instant",
                        "period_start": start, "period_end": end,
                        "duration_kind": _duration_kind(start, end),
                        "is_current_period": e["end"] == d["document_period_end"],
                        "segment_axis": None, "segment_member": None, "segment_label": None,
                        "decimals": None,
                    })
    return rows


def all_rows() -> list[dict]:
    """Every store row of every fixture filer."""
    return [r for t in TICKERS for r in store_rows(t)]


def own_filing(ticker: str, fy: int) -> str:
    """The corpus annual report labelled ``fy`` (DEI), from dei.json."""
    found = [a for a, d in DEI.items() if d["ticker"] == ticker
             and d["fiscal_period_focus"] == "FY" and int(d["fiscal_year_focus"]) == fy]
    assert len(found) == 1, (ticker, fy, found)
    return found[0]


def own_end(ticker: str, fy: int) -> str:
    """The store end (DEI period end + 1 day) of the filing labelled ``fy``."""
    return next_day(DEI[own_filing(ticker, fy)]["document_period_end"])


def filings_of(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """The accessions of a set of rows."""
    return {r["accession_no"] for r in rows}


def s1_holds(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], fy: int) -> bool:
    """S1 applied by hand to one answering row.

    The row ends on the fy filing's own period end, and no other canonical filing labelled
    later than the fy filing and filed after the row's filing reports the same concept (local
    name), unit and period.
    """
    if iso(row["period_end"]) != own_end(row["ticker"], fy):
        return False
    filed = (row["filed_date"], row["accession_no"])
    for r in rows:
        if (r["ticker"] == row["ticker"] and local(r["concept"]) == local(row["concept"])
                and r["unit"] == row["unit"] and r["segment_axis"] is None
                and iso(r["period_start"]) == iso(row["period_start"])
                and iso(r["period_end"]) == iso(row["period_end"])
                and r["value"] is not None and r["is_canonical_filing"]
                and r["fiscal_year"] > fy
                and (r["filed_date"], r["accession_no"]) > filed):
            return False
    return True


# ----------------------------------------------------------------------------- fake database
_EQ_COLUMNS = ("ticker", "concept_family", "unit", "period_type", "duration_kind")
_EQ = re.compile(r"(?:\b\w+\.)?\b(" + "|".join(_EQ_COLUMNS)
                 + r")\s*=\s*(?:'([^']*)'|%\((\w+)\)s)")


def _equalities(sql: str, params: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """The equality predicates the fake honours (spec Notes, fake-database contract)."""
    out = []
    for column, literal, param in _EQ.findall(sql):
        if param:
            if param not in params:
                continue
            out.append((column, params[param]))
        else:
            out.append((column, literal))
    return out


class FakeCursor:
    """A cursor over the fake store (spec Notes, fake-database contract)."""

    def __init__(self, rows: Sequence[dict]):
        self._store = rows
        self._rows: list[tuple] = []
        self.description: list[tuple] = []

    def _answer(self, cols: list[str], rows: Iterable[Sequence[Any]]) -> None:
        self.description = [(c,) for c in cols]
        self._rows = [tuple(r) for r in rows]

    def execute(self, sql: str, params: Any = None) -> None:
        p = params if isinstance(params, Mapping) else {}
        by_id = {r["fact_id"]: r for r in self._store}
        same = ("accession_no", "concept", "period_start", "period_end", "segment_member",
                "unit")
        if "ids" in p:
            out = []
            for anchor in p["ids"]:
                a = by_id.get(int(anchor))
                if a is None:
                    continue
                members = sorted(r["fact_id"] for r in self._store
                                 if all(r[k] == a[k] for k in same) and r["value"] == a["value"])
                out.append((a["fact_id"], members))
            self._answer(["anchor", "members"], out)
        elif "id" in p:
            a = by_id.get(int(p["id"]))
            out = [] if a is None else sorted(
                (r["fact_id"], r["value"], r["decimals"]) for r in self._store
                if all(r[k] == a[k] for k in same))
            self._answer(["fact_id", "value", "decimals"], out)
        elif "mv_financial_facts" in sql and "publish_manifest" not in sql \
                and "entity_master" not in sql and "count(" not in sql:
            eqs = _equalities(sql, p)
            hits = [r for r in self._store if all(r.get(c) == v for c, v in eqs)]
            cols = list(self._store[0]) if self._store else ["fact_id"]
            self._answer(cols, ([r[c] for c in cols] for r in hits))
        else:
            self._answer(["none"], [])

    def fetchall(self) -> list:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeConn:
    """A psycopg2-like connection over a list of store rows."""

    def __init__(self, rows: Sequence[dict]):
        self._rows = list(rows)

    def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
        return FakeCursor(self._rows)

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------------- builder
def committed_templates() -> list[dict]:
    """The YoY template as committed in configs/eval/sql_derived_templates.yaml."""
    spec = yaml.safe_load(TEMPLATES_PATH.read_text("utf-8"))
    by_id = {t["id"]: t for t in spec["templates"]}
    return [copy.deepcopy(by_id[t]) for t in TEMPLATE_IDS]


def build_suite(rows: Sequence[dict], tickers: Sequence[str] = tuple(TICKERS),
                fiscal_years: Sequence[int] = (2022, 2023, 2024, 2025, 2026),
                paraphrases: int = 1):
    """``SqlDerivedBuilder.build`` of the committed YoY template over the fake store."""
    spec = {
        "version": "v1", "suite_name": "sql_derived",
        "corpus": {"tickers": list(tickers), "fiscal_years": list(fiscal_years)},
        "tiers": {"spec": {"paraphrases": paraphrases}},
        "templates": committed_templates(),
    }
    builder = SqlDerivedBuilder(spec, FakeConn(rows), company_names={t: t for t in tickers})
    return builder.build("spec")


def case_of(suite: Any, template: str, ticker: str, fy: int, concept: str) -> Any:
    """The case of one template and combination, or None."""
    found = [c for c in suite.cases if c.id.split(":", 1)[0] == template
             and c.ticker == ticker and c.fy == fy and c.concept == concept]
    assert len(found) <= 1, [c.id for c in found]
    return found[0] if found else None


@pytest.fixture(scope="session")
def kit():
    """Everything above, for the tests (session-scoped, safe under hypothesis).

    Built from this module's globals: every spec directory has a module named ``conftest``,
    so ``sys.modules`` cannot name this one.
    """
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
