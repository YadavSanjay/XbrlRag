"""Corpus check: our fact store's values against SEC companyfacts (test strategy, area B).

Every non-extension, non-dimensional fact in ``mv_financial_facts`` whose filer has a cached
companyfacts document is matched to companyfacts on accession, taxonomy concept, unit and
period, and the two values are compared at the fact's own XBRL ``decimals`` (the approved
number-comparison rule, docs/design/XbrlRag_Test_Strategy.md). Disagreements must be listed in
the reviewed, shrink-only ``companyfacts_exceptions.yaml`` next to this module.

Read-only and offline: the database is ``xbrl_db`` through ``src.db.conn``, and companyfacts
comes only from the on-disk cache (``data/sec_companyfacts/``, or
``XBRLRAG_COMPANYFACTS_DIR``); a filer without a cached document is reported, never fetched.

Run the report (prints counts, the disagreements and any drift from the exceptions file)::

    conda run -n py312 python -m test.corpus.companyfacts_check

The matching reuses ``src.eval.sec_check`` (taxonomy key, the stored end-date convention).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

from src.eval.sec_check.core import taxonomy_key
from src.eval.sec_check.pg_store import record_from_row

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS_PATH = Path(__file__).with_name("companyfacts_exceptions.yaml")

# Units companyfacts files under one key: ``pure`` and ``Rate`` are one alias group.
_PURE_UNITS = frozenset({"pure", "Rate"})

# Relative tolerance for a fact without integer decimals (``INF`` or none): exact.
_REL_TOL = 1e-9

# Statement timeout for the one read query.
_STATEMENT_TIMEOUT_MS = 300_000

# The non-dimensional facts of the given filers. ``segment_axis`` is NULL exactly when the
# context carries no non-default dimension (the view fills it from the first such one).
_FACTS_SQL = """
SELECT fact_id, ticker, cik, accession_no, concept, unit, period_start, period_end, value,
       FALSE AS dimensional, decimals_value, raw_value, document_type
  FROM mv_financial_facts
 WHERE cik = ANY(%s)
   AND segment_axis IS NULL
   AND NOT is_extended
"""

UNCOVERED_STATUSES = ("not_in_companyfacts", "other_accession_only")



def companyfacts_dir() -> Path:
    """The companyfacts cache folder (``XBRLRAG_COMPANYFACTS_DIR`` wins)."""
    env = os.environ.get("XBRLRAG_COMPANYFACTS_DIR")
    return Path(env) if env else REPO_ROOT / "data" / "sec_companyfacts"


def cached_ciks(cache_dir: Path) -> set[str]:
    """The 10-digit CIKs with a cached ``CIK##########.json``."""
    return {p.stem[3:] for p in cache_dir.glob("CIK*.json") if p.stem[3:].isdigit()}


def companyfacts_unit(unit: str) -> str:
    """Our unit string as companyfacts names it.

    The fact store writes a per-share unit as ``shares*USD`` (the divide is lost in the
    view's unit string); companyfacts writes ``USD/shares``.
    """
    num, star, den = unit.partition("*")
    if star and num == "shares" and den != "shares":
        return f"{den}/shares"
    return unit


def _quantize(x: Decimal, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-decimals)
    return (x / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q


def _to_decimal(x: float) -> Decimal:
    """``x`` normalised to 15 significant figures (binary float noise)."""
    return Decimal(f"{x:.15g}")


def agree_at_decimals(ours: float, sec: float, decimals: int | None) -> bool:
    """Whether two values agree at the fact's XBRL precision.

    Args:
        ours: Our value.
        sec: The companyfacts value.
        decimals: Our fact's ``decimals``; None for ``INF`` or no decimals (exact).

    Returns:
        True when both round to the same multiple of ``10**-decimals`` (half away from
        zero), or, with no integer decimals, when they agree to a relative 1e-9.
    """
    if decimals is None:
        return abs(ours - sec) <= _REL_TOL * max(abs(ours), abs(sec), 1.0)
    return _quantize(_to_decimal(ours), decimals) == _quantize(_to_decimal(sec), decimals)


@dataclass
class Fact:
    """One of our facts, in companyfacts' date convention (end moved back one day)."""

    fact_id: int
    ticker: str
    cik: str
    accession_no: str
    concept: str
    unit: str
    period_start: str | None
    period_end: str
    value: float
    decimals: int | None
    raw_value: str | None = None
    document_type: str | None = None

    @property
    def short_concept(self) -> str:
        """``us-gaap:Revenues``-style concept (taxonomy key and local name)."""
        namespace, _, local = self.concept.rpartition(":")
        return f"{taxonomy_key(namespace)}:{local}"

    def key(self) -> tuple:
        """The exceptions-file key: stable across reloads (no fact id)."""
        return (self.accession_no, self.short_concept, self.unit, self.period_start,
                self.period_end, self.decimals, _fmt(self.value))


@dataclass
class Disagreement:
    """A fact whose companyfacts entry for the same accession disagrees."""

    fact: Fact
    sec_values: list[float]
    other_accessions: list[dict] = field(default_factory=list)


@dataclass
class CheckResult:
    """The outcome of one corpus pass."""

    status_counts: Counter = field(default_factory=Counter)
    disagreements: list[Disagreement] = field(default_factory=list)
    filers_checked: list[str] = field(default_factory=list)
    filers_uncached: list[str] = field(default_factory=list)
    uncovered: Counter = field(default_factory=Counter)
    agree_only_at_decimals: int = 0

    @property
    def compared(self) -> int:
        """Facts compared against a companyfacts value for their own accession."""
        return self.status_counts["agree"] + self.status_counts["disagree"]


def _fmt(value: float) -> str:
    """A stable text form of a value for the key (15 significant figures)."""
    return format(_to_decimal(value).normalize(), "f")


def entries_for(fact: Fact, company_facts: dict) -> list[dict]:
    """The companyfacts entries for the fact's concept, unit and period, any accession."""
    namespace, _, local = fact.concept.rpartition(":")
    taxonomy = taxonomy_key(namespace)
    concept = ((company_facts.get("facts") or {}).get(taxonomy) or {}).get(local)
    if not concept:
        return []
    unit = companyfacts_unit(fact.unit)
    accepted = _PURE_UNITS if unit in _PURE_UNITS else frozenset({unit})
    # companyfacts writes a one-day duration (an event date: a split, a settlement) with
    # no start, like an instant.
    starts = {fact.period_start}
    if fact.period_start == fact.period_end:
        starts.add(None)
    out: list[dict] = []
    for unit_key, entries in (concept.get("units") or {}).items():
        if unit_key not in accepted:
            continue
        out.extend(e for e in entries
                   if e.get("end") == fact.period_end and e.get("start") in starts)
    return out


def compare_fact(fact: Fact, company_facts: dict) -> tuple[str, Disagreement | None]:
    """Compares one fact with companyfacts.

    Returns:
        ``(status, disagreement)``. Status is ``agree`` (exactly), ``agree_at_decimals``
        (only once both are rounded at the fact's decimals: a rounded duplicate), ``disagree``,
        ``not_in_companyfacts`` (no entry for the concept, unit and period at all) or
        ``other_accession_only`` (companyfacts files the value under another accession
        only, its de-duplication). Only ``disagree`` carries a disagreement.
    """
    entries = entries_for(fact, company_facts)
    if not entries:
        return "not_in_companyfacts", None
    ours = [e for e in entries if e.get("accn") == fact.accession_no]
    if not ours:
        return "other_accession_only", None
    if any(agree_at_decimals(fact.value, float(e["val"]), None) for e in ours):
        return "agree", None
    if any(agree_at_decimals(fact.value, float(e["val"]), fact.decimals) for e in ours):
        return "agree_at_decimals", None
    others = [{"accession_no": e["accn"], "value": float(e["val"]), "form": e.get("form")}
              for e in entries if e.get("accn") != fact.accession_no]
    return "disagree", Disagreement(fact, [float(e["val"]) for e in ours], others)


def fact_from_row(row: tuple) -> Fact | None:
    """A ``Fact`` from a ``_FACTS_SQL`` row; None for a nil fact."""
    record = record_from_row(row[:10])
    if record is None:
        return None
    decimals = row[10]
    return Fact(
        fact_id=record["fact_id"], ticker=record["ticker"], cik=record["cik"],
        accession_no=record["accession_no"], concept=record["concept"], unit=record["unit"],
        period_start=record["period_start"], period_end=record["period_end"],
        value=record["value"], decimals=None if decimals is None else int(decimals),
        raw_value=row[11], document_type=row[12],
    )


def read_facts(conn: Any, ciks: Iterable[str]) -> list[Fact]:
    """Reads the in-scope facts of the given filers (read-only)."""
    with conn.cursor() as cur:
        cur.execute(_FACTS_SQL, (sorted(ciks),))
        rows = cur.fetchall()
    facts = [fact_from_row(r) for r in rows]
    return [f for f in facts if f is not None and taxonomy_key(f.concept.rpartition(":")[0])]


def connect_readonly() -> Any:
    """A read-only, autocommit connection to the configured ``xbrl_db``."""
    from src.db.conn import connect

    conn = connect(connect_timeout=10, options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}")
    conn.set_session(readonly=True, autocommit=True)
    return conn


def db_filers(conn: Any) -> dict[str, str]:
    """``{cik: ticker}`` of every filer in ``mv_financial_facts``."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT cik, ticker FROM mv_financial_facts")
        return {cik: ticker for cik, ticker in cur.fetchall()}


def run_check(conn: Any, cache_dir: Path) -> CheckResult:
    """Runs the whole corpus pass."""
    filers = db_filers(conn)
    cached = cached_ciks(cache_dir)
    result = CheckResult(
        filers_checked=sorted(filers[c] for c in filers if c in cached),
        filers_uncached=sorted(filers[c] for c in filers if c not in cached),
    )
    in_scope = [c for c in filers if c in cached]
    docs: dict[str, dict] = {}
    for fact in read_facts(conn, in_scope):
        if fact.cik not in docs:
            path = cache_dir / f"CIK{fact.cik}.json"
            docs[fact.cik] = json.loads(path.read_bytes())
        status, dis = compare_fact(fact, docs[fact.cik])
        if status == "agree_at_decimals":
            result.agree_only_at_decimals += 1
            status = "agree"
        result.status_counts[status] += 1
        if status in UNCOVERED_STATUSES:
            result.uncovered[(fact.ticker, fact.accession_no, status)] += 1
        if dis is not None:
            result.disagreements.append(dis)
    result.disagreements.sort(key=lambda d: (d.fact.ticker, d.fact.accession_no,
                                             d.fact.short_concept, d.fact.period_end))
    return result


def load_exceptions(path: Path = EXCEPTIONS_PATH) -> list[dict]:
    """The reviewed exceptions, each with the key fields and a category."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("exceptions") or [])


def load_uncovered(path: Path = EXCEPTIONS_PATH) -> dict[tuple, int]:
    """The reviewed uncovered counts, ``{(ticker, accession, status): count}``."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {(str(e["ticker"]), str(e["accession_no"]), str(e["status"])): int(e["count"])
            for e in data.get("uncovered") or []}


def uncovered_drift(result: CheckResult, reviewed: dict[tuple, int]) -> list[str]:
    """One line per (ticker, accession, status) whose count differs from the reviewed one."""
    keys = sorted(set(reviewed) | set(result.uncovered))
    return [f"{k}: found {result.uncovered.get(k, 0)}, reviewed {reviewed.get(k, 0)}"
            for k in keys if result.uncovered.get(k, 0) != reviewed.get(k, 0)]


def exception_key(entry: dict) -> tuple:
    """The key of an exceptions-file entry, comparable with ``Fact.key()``."""
    decimals = entry.get("decimals")
    return (str(entry["accession_no"]), str(entry["concept"]), str(entry["unit"]),
            entry.get("period_start"), str(entry["period_end"]),
            None if decimals is None else int(decimals), _fmt(float(entry["ours"])))


def drift(result: CheckResult, exceptions: list[dict]) -> tuple[list[Disagreement], list[dict]]:
    """``(unlisted disagreements, stale exception entries)``."""
    listed = {exception_key(e): e for e in exceptions}
    found = {d.fact.key(): d for d in result.disagreements}
    new = [d for k, d in found.items() if k not in listed]
    stale = [e for k, e in listed.items() if k not in found]
    return new, stale


def describe(d: Disagreement) -> str:
    """One line per disagreement, for test failures and the report."""
    f = d.fact
    return (f"{f.ticker} {f.accession_no} {f.short_concept} {f.unit} "
            f"{f.period_start or ''}..{f.period_end} dec={f.decimals} "
            f"ours={_fmt(f.value)} sec={[_fmt(v) for v in d.sec_values]} fact_id={f.fact_id}")


def main() -> int:
    """Prints the report; exits 1 on drift from the exceptions file."""
    cache_dir = companyfacts_dir()
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            print("database:", cur.fetchone()[0])
        result = run_check(conn, cache_dir)
    finally:
        conn.close()
    print("companyfacts cache:", cache_dir)
    print(f"filers checked ({len(result.filers_checked)}):", ", ".join(result.filers_checked))
    print(f"filers without cached companyfacts ({len(result.filers_uncached)}):",
          ", ".join(result.filers_uncached))
    print("statuses:", dict(result.status_counts))
    print(f"compared: {result.compared}  agree: {result.status_counts['agree']} "
          f"(of which only at decimals: {result.agree_only_at_decimals})  "
          f"disagree: {result.status_counts['disagree']}")
    for k, n in sorted(result.uncovered.items()):
        print("  uncovered", k, n)
    for d in result.disagreements:
        print("  ", describe(d))
    new, stale = drift(result, load_exceptions())
    print(f"unlisted disagreements: {len(new)}; stale exceptions: {len(stale)}")
    for d in new:
        print("  NEW  ", describe(d))
    for e in stale:
        print("  STALE", e)
    gaps = uncovered_drift(result, load_uncovered())
    for line in gaps:
        print("  UNCOVERED DRIFT", line)
    return 1 if new or stale or gaps else 0


if __name__ == "__main__":
    sys.exit(main())
