"""FSDS reader, oracle helpers and candidate-row builder for the ifrs-family-meaning tests.

Spec: docs/specs/ifrs-family-meaning.md. The fixtures are SEC's Financial Statement Data
Sets rows of TSMC's three 20-Fs (byte-for-byte; provenance in ``fixtures/provenance.json``).

``fsds_value`` and ``presented_tag`` are the tests' oracle for "what the filer filed and
shows": a plain split on ``\\n`` and ``\\t`` (SEC readme), written here without any reader
under test. ``candidate_rows`` turns a filing's undimensioned annual facts into the rows the
lookup's SQL returns after the change: the facts whose family (``fact_family`` over the
committed mapping) is the looked-up one, with rank, role and substitution from
``family_member``. ``lookup`` runs the real ``XbrlFactstoreLookup._build_result`` on them;
nothing in ``src`` is faked.
"""

from __future__ import annotations

import datetime as dt
import functools
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from src.ingest.util.generate_concept_mapping import committed_mapping_rows, fact_family
from src.retriever.xbrl.family_resolution import family_member
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

FIXTURES = Path(__file__).parent / "fixtures"

TSM = "0001046179"
# CIKs with no cik-scoped or reviewed entry in the committed mapping: "another IFRS filer".
OTHER_CIKS = ("0000000001", "0001234567", "0009999999")
CIKS = (TSM, *OTHER_CIKS)


class Filing(NamedTuple):
    quarter: str
    adsh: str
    ifrs_date: str  # the IFRS taxonomy date of the filing's namespace
    ext_ns: str  # TSMC's extension namespace in that filing


FILINGS: dict[int, Filing] = {
    2023: Filing("2024q2", "0001193125-24-099840", "2023-03-23", "http://www.tsmc.com/20231231"),
    2024: Filing("2025q2", "0001193125-25-083423", "2024-03-27", "http://www.tsmc.com/20241231"),
    2025: Filing("2026q2", "0001628280-26-025362", "2025-03-27", "http://www.tsmc.com/20251231"),
}
YEARS = tuple(FILINGS)

# Namespaces another IFRS filer could use (R6).
IFRS_DATES = ("2022-03-24", "2023-03-23", "2024-03-27", "2025-03-27")
EXT_NAMESPACES = ("http://www.tsmc.com/20241231", "http://www.example.com/20241231",
                  "http://www.otherifrsfiler.com.tw/20251231")

# The store's decimals (read-only, 2026-11-07, `fact.decimals_value` for every TSMC fact of
# these tags in the three filings): -5 for the monetary facts, 2 for EPS. Other facts carry
# None (exact); that only affects tie-breaks between duplicates of one concept.
STORE_DECIMALS = {
    **dict.fromkeys(
        ("ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "Equity",
         "EquityAttributableToOwnersOfParent", "ComprehensiveIncome",
         "ComprehensiveIncomeAttributableToOwnersOfParent", "SalesAndMarketingExpense",
         "GeneralAndAdministrativeExpense", "OperatingExpenseExcludingCostOfSales",
         "FinanceIncome", "RevenueFromInterest", "InvestmentsIncomeInterest",
         "InvestmentIncomeInterest", "OtherIncome", "Revenue",
         "RevenueFromContractsWithCustomers"),
        -5,
    ),
    "BasicEarningsLossPerShare": 2,
    "DilutedEarningsLossPerShare": 2,
}
STATEMENTS = frozenset({"BS", "IS", "CF", "EQ", "CI"})
INSTANT_FAMILIES = frozenset({"StockholdersEquity"})
PER_SHARE_FORMS = ("shares*{c}", "{c}*shares", "{c}/shares")


def half_unit(tag: str) -> float:
    """Half a unit of the tag's decimals: the tolerance of a value as filed."""
    return 0.5 * 10.0 ** (-STORE_DECIMALS[tag])


@functools.cache
def table(quarter: str, name: str) -> tuple[dict[str, str], ...]:
    """Every row of one fixture table, as dicts keyed by SEC's header."""
    text = (FIXTURES / quarter / f"{name}.tsv").read_bytes().decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    header = lines[0].split("\t")
    return tuple(dict(zip(header, ln.split("\t"), strict=True)) for ln in lines[1:])


def submission(fy: int) -> dict[str, str]:
    """The filing's ``sub`` row."""
    f = FILINGS[fy]
    return next(r for r in table(f.quarter, "sub") if r["adsh"] == f.adsh)


def fsds_value(fy: int, tag: str, uom: str = "TWD") -> float:
    """The filing's undimensioned annual value of ``tag`` in ``uom`` at its fiscal-period end.

    Raises AssertionError unless exactly one value is filed.
    """
    f = FILINGS[fy]
    end = submission(fy)["period"]
    values = {
        float(r["value"]) for r in table(f.quarter, "num")
        if r["adsh"] == f.adsh and r["tag"] == tag and r["uom"] == uom and r["ddate"] == end
        and r["qtrs"] in ("0", "4") and r["value"] != ""
    }
    assert len(values) == 1, (fy, tag, uom, values)
    return values.pop()


def presented_tag(fy: int, stmt: str, label: str) -> str:
    """The tag of the line the filer presents on ``stmt`` under ``label`` (case-folded)."""
    f = FILINGS[fy]
    tags = {
        r["tag"] for r in table(f.quarter, "pre")
        if r["adsh"] == f.adsh and r["stmt"] == stmt and r["inpth"] == "0"
        and r["plabel"].casefold() == label.casefold()
    }
    assert len(tags) == 1, (fy, stmt, label, tags)
    return tags.pop()


def presented_tags(fy: int) -> frozenset[str]:
    """Tags that are lines of one of the filing's primary statements."""
    f = FILINGS[fy]
    return frozenset(
        r["tag"] for r in table(f.quarter, "pre")
        if r["adsh"] == f.adsh and r["stmt"] in STATEMENTS and r["inpth"] == "0"
    )


def tag_text(name: str, version: str) -> str:
    """The standard label and definition of a taxonomy element, from the fixtures' ``tag``."""
    for f in FILINGS.values():
        for r in table(f.quarter, "tag"):
            if r["tag"] == name and r["version"] == version:
                return f"{r['tlabel']} :: {r['doc']}"
    raise AssertionError(f"no tag row for {name} {version}")


def _short(r: dict[str, str], adsh: str) -> str | None:
    if r["version"] == adsh:
        return "ext:" + r["tag"]
    prefix = _PREFIXES.get(r["version"].split("/", 1)[0])
    return None if prefix is None else f"{prefix}:{r['tag']}"


_PREFIXES = {"ifrs": "ifrs-full", "us-gaap": "us-gaap", "srt": "srt", "dei": "dei"}


def _store_concept(short: str, tag: str, ifrs_date: str, ext_ns: str) -> str:
    prefix = short.split(":", 1)[0]
    if prefix == "ifrs-full":
        return f"https://xbrl.ifrs.org/taxonomy/{ifrs_date}/ifrs-full:{tag}"
    if prefix == "ext":
        return f"{ext_ns}:{tag}"
    return short  # us-gaap / srt / dei: the prefix form is accepted as well


@functools.cache
def mapping_rows() -> tuple[dict[str, Any], ...]:
    return tuple(committed_mapping_rows())


@functools.cache
def _family_of(cik: str, short: str) -> str | None:
    row = fact_family(mapping_rows(), cik, short)
    return None if row is None else row["concept_family"]


def _plus_one_day(yyyymmdd: str) -> dt.date:
    return dt.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:])) + dt.timedelta(1)


def candidate_rows(fy: int, family: str, cik: str = TSM, ifrs_date: str | None = None,
                   ext_ns: str | None = None,
                   per_share: str = "shares*{c}") -> list[dict[str, Any]]:
    """The filing's facts the lookup's SQL returns for ``family`` (see the module doc)."""
    f = FILINGS[fy]
    ifrs_date = ifrs_date or f.ifrs_date
    ext_ns = ext_ns or f.ext_ns
    per_share_tags = {
        r["tag"] for r in table(f.quarter, "tag") if r["datatype"] == "perShare"
    }
    shown = presented_tags(fy)
    want = "instant" if family in INSTANT_FAMILIES else "duration"
    out: list[dict[str, Any]] = []
    for i, r in enumerate(table(f.quarter, "num")):
        if r["adsh"] != f.adsh or r["value"] == "" or r["qtrs"] not in ("0", "4"):
            continue
        period_type = "instant" if r["qtrs"] == "0" else "duration"
        if period_type != want:
            continue
        short = _short(r, f.adsh)
        if short is None or _family_of(cik, short) != family:
            continue
        concept = _store_concept(short, r["tag"], ifrs_date, ext_ns)
        m = family_member(family, cik, concept)
        if m is None:
            continue
        end = _plus_one_day(r["ddate"])
        start = None if period_type == "instant" else end.replace(year=end.year - 1)
        unit = per_share.format(c=r["uom"]) if r["tag"] in per_share_tags else r["uom"]
        sub = m["substitution"] or {}
        out.append({
            "fact_id": fy * 100_000 + i, "value": float(r["value"]), "unit": unit,
            "period_type": period_type,
            "period_start": None if start is None else start.isoformat(),
            "period_end": end.isoformat(), "concept": concept, "concept_family": family,
            "concept_pref_rank": m["rank"], "concept_role": m["role"],
            "substitution_code": sub.get("code"), "substitution_note": sub.get("note"),
            "presented": r["tag"] in shown, "decimals": STORE_DECIMALS.get(r["tag"]),
            "segment_axis": None, "segment_member": None, "segment_label": None,
            "html_anchor": None, "page_num": None, "section_path": None,
            "accession_no": f.adsh, "linked_chunk_ids": [],
        })
    return out


def lookup(fy: int, family: str, rows: Sequence[dict[str, Any]] | None = None,
           unit: str | None = None, cik: str = TSM):
    """``_build_result`` for an undimensioned FY lookup of ``family`` on the filing."""
    rows = candidate_rows(fy, family, cik) if rows is None else list(rows)
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions("NONE", notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "cik": cik, "fy": fy, "concept_family": family,
            "dimensions": "NONE", "section_hint": None}
    if unit is not None:
        step["unit"] = unit
    period_type = "instant" if family in INSTANT_FAMILIES else "duration"
    return lk._build_result("s1", step, cik, fy, family, period_type, specs, rows, notes)


def answer_tag(result: Any, rows: Sequence[dict[str, Any]]) -> str | None:
    """The local name of the concept of the row the result cites, or None."""
    if result.citation is None:
        return None
    fid = result.citation.fact_id
    row = next((r for r in rows if r["fact_id"] == fid), None)
    return None if row is None else row["concept"].rsplit(":", 1)[1]


def variant_rows(fy: int, family: str, cik: str, ifrs_date: str, ext_ns: str,
                 rnd: random.Random, per_share: str = "shares*{c}") -> list[dict[str, Any]]:
    """``candidate_rows`` under another filer's identity, shuffled, with relabelled fact ids."""
    rows = candidate_rows(fy, family, cik, ifrs_date, ext_ns, per_share)
    ids = [r["fact_id"] for r in rows]
    rnd.shuffle(ids)
    rows = [{**r, "fact_id": new} for r, new in zip(rows, ids, strict=True)]
    rnd.shuffle(rows)
    return rows
