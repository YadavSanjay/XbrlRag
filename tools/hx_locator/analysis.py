"""What the filings themselves say: values per filer, year and family, read from raw iXBRL.

Everything here reads the filer's own tagged facts (``ixbrl.parse_documents``), never the
fact store. The S1 rule is applied on those raw facts, the same way the evaluation keys
do (docs/design/XbrlRag_Eval_Semantics.md):

* **As originally reported**: the fact in the year's own annual report, in the column of
  that fiscal year (a duration ending at the report's period end, about a year long, or
  an instant at that end).
* **Latest**: the same concept, unit and dimensions, for the same period, in the most
  recently filed later annual report that states it; else the original.

A fact's family is the view's own rule (``families.family_of``), so families match what
``mv_financial_facts`` shows; the raw concept is always shown beside it.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree  # type: ignore[import-untyped]  # lxml ships no stubs

from tools.hx_locator.corpus import Corpus, Filing
from tools.hx_locator.families import family_members, family_of, shadowing
from tools.hx_locator.ixbrl import RawFact, parse_documents

PERIOD_TOLERANCE_DAYS = 7
ANNUAL_DAYS = (340, 380)
_LINK = "http://www.xbrl.org/2003/linkbase"
_XLINK = "http://www.w3.org/1999/xlink"
_LABEL_ROLES = ("http://www.xbrl.org/2003/role/label",
                "http://www.xbrl.org/2003/role/terseLabel",
                "http://www.xbrl.org/2003/role/verboseLabel")
_BLOCK_TAGS = frozenset({"p", "li", "td", "div", "h1", "h2", "h3", "h4", "h5", "h6"})
_MIN_BLOCK_CHARS = 30
_SNIPPET_CHARS = 700


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _near(a: str | None, b: str | None, days: int = PERIOD_TOLERANCE_DAYS) -> bool:
    da, db = _date(a), _date(b)
    return da is not None and db is not None and abs((da - db).days) <= days


def _length(fact: RawFact) -> int | None:
    s, e = _date(fact.context.start), _date(fact.context.end)
    return (e - s).days if s and e else None


def in_own_column(fact: RawFact, period_start: str, period_end: str) -> bool:
    """Whether a fact reports the fiscal year ``[period_start, period_end]``.

    A duration must end within a week of the year's end and be about a year long (or
    start within a week of the year's start); an instant must sit within a week of the
    year's end.
    """
    ctx = fact.context
    if not _near(ctx.end, period_end):
        return False
    if ctx.instant:
        return True
    length = _length(fact)
    if length is not None and ANNUAL_DAYS[0] <= length <= ANNUAL_DAYS[1]:
        return True
    return _near(ctx.start, period_start)


def half_unit(fact: RawFact) -> float:
    """Half the last digit a fact's ``decimals`` claims (0 for ``INF`` or none)."""
    try:
        return 0.5 * 10 ** (-int(fact.decimals or ""))
    except ValueError:
        return 0.0


def within_precision(a: RawFact, b: RawFact) -> bool:
    """Whether two values differ only by rounding (within the coarser half unit)."""
    if a.value is None or b.value is None:
        return a.value == b.value
    return abs(a.value - b.value) <= max(half_unit(a), half_unit(b)) + 1e-9 * abs(a.value)


def identity(fact: RawFact) -> tuple[str, str | None, tuple[tuple[str, str], ...], bool]:
    """What makes two facts "the same fact" across filings: concept, unit, dims, kind."""
    return (fact.concept, fact.unit, fact.context.dims, fact.context.instant)


@dataclass
class Observation:
    """One value of a fact identity in one filing."""

    filing: Filing
    fact: RawFact
    also_at: list[str]

    def as_dict(self) -> dict[str, Any]:
        """JSON form."""
        f, x = self.filing, self.fact
        return {
            "accession": f.accession, "filing_key": f.key, "form": f.form,
            "filing_date": f.filing_date, "fiscal_year_label": f.fiscal_year,
            "fact_id": x.fact_id, "document": x.document, "also_at": self.also_at,
            "value": x.value, "text": x.text, "nil": x.nil, "decimals": x.decimals,
            "scale": x.scale, "sign": x.sign, "unit": x.unit,
            "period_start": x.context.start, "period_end": x.context.end,
            "instant": x.context.instant,
            "viewer_url": f.sec_viewer_url(x.document),
            "local_url": f"/doc/{f.key}/{x.document}#{x.fact_id}",
        }


class Library:
    """Parsed filings, labels and text blocks, cached per filing (thread-safe)."""

    def __init__(self, corpus: Corpus) -> None:
        """Wraps an indexed corpus."""
        self.corpus = corpus
        self._facts: dict[str, list[RawFact]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._blocks: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._filing_locks: dict[str, threading.Lock] = {}

    def _filing_lock(self, key: str) -> threading.Lock:
        with self._lock:
            return self._filing_locks.setdefault(key, threading.Lock())

    def facts(self, filing: Filing) -> list[RawFact]:
        """Every tagged fact of a filing."""
        if filing.key not in self._facts:
            with self._filing_lock(filing.key):
                if filing.key not in self._facts:
                    paths = [filing.raw_dir / d for d in filing.ixbrl_documents]
                    self._facts[filing.key] = parse_documents(paths)
        return self._facts[filing.key]

    def labels(self, filing: Filing) -> dict[str, str]:
        """The filer's standard labels, ``{concept QName: label}``, from its label linkbase."""
        if filing.key not in self._labels:
            with self._filing_lock(filing.key):
                if filing.key not in self._labels:
                    self._labels[filing.key] = _read_labels(filing.raw_dir)
        return self._labels[filing.key]

    def blocks(self, filing: Filing) -> list[dict[str, Any]]:
        """The filing's text blocks with their section path, in document order."""
        if filing.key not in self._blocks:
            with self._filing_lock(filing.key):
                if filing.key not in self._blocks:
                    self._blocks[filing.key] = _read_blocks(filing)
        return self._blocks[filing.key]

    # ------------------------------------------------------------------ observations

    def own_column(self, filing: Filing) -> list[Observation]:
        """The filing's facts in its own fiscal-year column, one per identity."""
        return _dedupe(filing, [f for f in self.facts(filing)
                                if in_own_column(f, filing.period_start, filing.period_end)])

    def restatements(self, ticker: str, fiscal_year: int, original: RawFact,
                     own: Filing) -> list[Observation]:
        """Every later annual report that states the same fact for the same period.

        Args:
            ticker: The filer.
            fiscal_year: The year of the original.
            original: The original fact.
            own: The year's own filing.

        Returns:
            Observations, most recently filed first.
        """
        key = identity(original)
        out: list[Observation] = []
        for later in self.corpus.for_ticker(ticker):
            is_later = later.key != own.key and (
                later.fiscal_year > fiscal_year
                or (later.fiscal_year == fiscal_year and later.filing_date > own.filing_date))
            if not is_later:
                continue
            same = [f for f in self.facts(later)
                    if identity(f) == key and _near(f.context.end, original.context.end)
                    and (original.context.instant
                         or _near(f.context.start, original.context.start))]
            if same:
                out.extend(_dedupe(later, same))
        return sorted(out, key=lambda o: o.filing.filing_date, reverse=True)

    def s1(self, ticker: str, fiscal_year: int,
           match: Any) -> tuple[Filing | None, list[dict[str, Any]]]:
        """Original and latest of every own-column fact ``match(fact)`` accepts.

        Args:
            ticker: The filer.
            fiscal_year: The fiscal year.
            match: A predicate over ``RawFact``.

        Returns:
            ``(own filing, rows)``; each row has ``original``, ``latest``,
            ``restatements`` (every later statement) and ``restated``.
        """
        own = self.corpus.own_filing(ticker, fiscal_year)
        if own is None:
            return None, []
        rows = []
        for obs in self.own_column(own):
            if not match(obs.fact):
                continue
            later = self.restatements(ticker, fiscal_year, obs.fact, own)
            latest = later[0] if later else obs
            changed = latest.fact.value != obs.fact.value
            restated = changed and not within_precision(obs.fact, latest.fact)
            rows.append({
                "_fact": obs.fact,
                "original": obs.as_dict(),
                "latest": latest.as_dict(),
                "restatements": [o.as_dict() for o in later],
                "restated": restated,
                "precision_only": changed and not restated,
            })
        return own, rows


def _dedupe(filing: Filing, facts: list[RawFact]) -> list[Observation]:
    """One observation per identity; the other element ids are kept in ``also_at``."""
    seen: dict[Any, Observation] = {}
    for f in facts:
        k = identity(f)
        if k in seen:
            if f.fact_id:
                seen[k].also_at.append(f.fact_id)
        else:
            seen[k] = Observation(filing, f, [])
    return list(seen.values())


def _read_labels(raw_dir: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for lab in sorted(raw_dir.glob("*_lab.xml")):
        try:
            root = etree.parse(str(lab), etree.XMLParser(huge_tree=True, no_network=True,
                                                          resolve_entities=False)).getroot()
        except etree.XMLSyntaxError:
            continue
        locs: dict[str, str] = {}
        for loc in root.iter(f"{{{_LINK}}}loc"):
            href = loc.get(f"{{{_XLINK}}}href", "")
            frag = href.rsplit("#", 1)[-1]
            locs[loc.get(f"{{{_XLINK}}}label", "")] = frag.replace("_", ":", 1)
        texts: dict[str, dict[str, str]] = {}
        for el in root.iter(f"{{{_LINK}}}label"):
            texts.setdefault(el.get(f"{{{_XLINK}}}label", ""), {})[
                el.get(f"{{{_XLINK}}}role", "")] = (el.text or "").strip()
        for arc in root.iter(f"{{{_LINK}}}labelArc"):
            concept = locs.get(arc.get(f"{{{_XLINK}}}from", ""))
            by_role = texts.get(arc.get(f"{{{_XLINK}}}to", ""), {})
            if not concept or concept in labels:
                continue
            for role in _LABEL_ROLES:
                if by_role.get(role):
                    labels[concept] = by_role[role]
                    break
    return labels


def _local(tag: object) -> str:
    # The HTML parser keeps prefixed names such as "ix:header" as the whole tag.
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _read_blocks(filing: Filing) -> list[dict[str, Any]]:
    """Text blocks (leaf paragraphs, cells, list items) with their section path.

    Sections come from the canonical HTML's section tree (Item 1A, Item 7 ...) when the
    canonical file exists; a block's section is the last section anchor met before it.
    """
    path = filing.canonical_path or (filing.raw_dir / filing.primary_document)
    anchors: dict[str, str] = {}
    if filing.canonical_path is not None:
        from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import build_section_tree

        try:
            tree = build_section_tree(filing.canonical_path, filing.accession)
            anchors = {n.html_anchor: n.section_path for n in tree.nodes.values()
                       if n.html_anchor}
        except Exception:  # isolation point: a filing without a usable ToC still searches
            anchors = {}
    parser = etree.HTMLParser(huge_tree=True, recover=True, no_network=True)
    root = etree.parse(str(path), parser).getroot()
    section = "(before the first section)"
    blocks: list[dict[str, Any]] = []
    for _event, el in etree.iterwalk(root, events=("start",)):
        el_id = el.get("id") or el.get("name")
        if el_id and el_id in anchors:
            section = anchors[el_id]
        if _local(el.tag).lower() not in _BLOCK_TAGS:
            continue
        if any(_local(c.tag).lower() in _BLOCK_TAGS for c in el.iterdescendants()):
            continue
        text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
        if len(text) >= _MIN_BLOCK_CHARS:
            blocks.append({"index": len(blocks), "section": section, "text": text})
    return blocks


# ---------------------------------------------------------------------- the queries


def _family_match(family: str, cik: int, member: str | None,
                  dimensional: bool) -> Any:
    def match(f: RawFact) -> bool:
        if not f.numeric or f.value is None:
            return False
        if dimensional or member:
            if not f.context.dims:
                return False
            if member and not any(member.lower() in m.lower() for _, m in f.context.dims):
                return False
        elif f.context.dims:
            return False
        row = family_of(f.concept, cik)
        return row is not None and row["concept_family"] == family
    return match


def _s1_rows(lib: Library, ticker: str, fy: int, match: Any) -> tuple[Filing | None,
                                                                       list[dict[str, Any]]]:
    """``Library.s1`` rows with the concept, label, family, rank, role and dimensions."""
    own, rows = lib.s1(ticker, fy, match)
    if own is None:
        return None, []
    labels = lib.labels(own)
    for row in rows:
        fact: RawFact = row.pop("_fact")
        fam = family_of(fact.concept, own.cik)
        row["concept"] = fact.concept
        row["label"] = labels.get(fact.concept, "")
        row["family"] = fam["concept_family"] if fam else None
        row["rank"] = int(fam["preference_rank"]) if fam else None
        row["role"] = (fam.get("concept_role") or "") if fam else ""
        row["dims"] = [list(d) for d in fact.context.dims]
    rows.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 999, r["concept"],
                             r["dims"]))
    return own, rows


def filing_summary(f: Filing) -> dict[str, Any]:
    """JSON summary of a filing."""
    return {"ticker": f.ticker, "cik": f.cik, "fiscal_year": f.fiscal_year,
            "label_source": f.label_source, "dei_year": f.dei_year,
            "folder_year": f.folder_year, "form": f.form,
            "accession": f.accession, "filing_key": f.key, "filing_date": f.filing_date,
            "period_start": f.period_start, "period_end": f.period_end,
            "viewer_url": f.sec_viewer_url(), "index_url": f.sec_index_url(),
            "local_url": f"/doc/{f.key}/{f.primary_document}",
            "has_sections": f.canonical_path is not None}


def family_values(lib: Library, ticker: str, fy: int, family: str,
                  member: str | None = None, dimensional: bool = False) -> dict[str, Any]:
    """Every concept of ``family`` the filer tagged for ``fy``, original and latest.

    Args:
        lib: The library.
        ticker: The filer.
        fy: The fiscal year.
        family: The concept family.
        member: A member text to filter dimensional facts by (substring).
        dimensional: Show dimensional facts (members) instead of undimensioned ones.

    Returns:
        ``{filing, members (the family's mapped QNames), rows}``; ``filing`` is None when
        the year was not filed.
    """
    own = lib.corpus.own_filing(ticker, fy)
    if own is None:
        return {"filing": None, "members": [], "shadowed": {}, "rows": []}
    own, rows = _s1_rows(lib, ticker, fy, _family_match(family, own.cik, member, dimensional))
    assert own is not None
    return {"filing": filing_summary(own), "members": family_members(family, own.cik),
            "shadowed": shadowing(family, own.cik), "rows": rows}


def member_pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """With a member filter: a single-axis fact first, then the best role and rank."""
    order = {"primary": 0, "substitute": 1, "component": 2}
    if not rows:
        return None
    return min(rows, key=lambda r: (len(r["dims"]) != 1, order.get(r["role"], 3),
                                    r["rank"] or 999, r["concept"]))


def headline(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The row the product's lookup would answer with: best role, then lowest rank."""
    order = {"primary": 0, "substitute": 1, "component": 2}
    flat = [r for r in rows if not r["dims"]]
    if not flat:
        return None
    return min(flat, key=lambda r: (order.get(r["role"], 3), r["rank"] or 999, r["concept"]))


def cross_filer(lib: Library, tickers: list[str], fy: int, family: str,
                member: str | None = None) -> dict[str, Any]:
    """One family across filers for one year: values, exclusions and the aggregates.

    Each filer's value is its headline concept's latest (S1) value. A filer is excluded
    as ``not_filed`` (no annual report for the year on disk), ``not_tagged`` (no fact of
    the family) or ``currency`` (a value in another unit than the others' USD).
    """
    out: list[dict[str, Any]] = []
    for t in tickers:
        own = lib.corpus.own_filing(t, fy)
        if own is None:
            out.append({"ticker": t, "exclusion": "not_filed", "rows": []})
            continue
        res = family_values(lib, t, fy, family, member, dimensional=bool(member))
        rows = res["rows"]
        pick = headline(rows) if not member else member_pick(rows)
        entry: dict[str, Any] = {"ticker": t, "filing": res["filing"], "rows": rows,
                                 "pick": pick, "exclusion": None,
                                 "shadowed": res["shadowed"]}
        if pick is None:
            entry["exclusion"] = "not_tagged"
        elif pick["latest"]["unit"] and not str(pick["latest"]["unit"]).startswith("USD") \
                and pick["latest"]["unit"] not in ("shares", "pure"):
            entry["exclusion"] = "currency"
        out.append(entry)
    ranked = [e for e in out if e["exclusion"] is None]
    values = {e["ticker"]: float(e["pick"]["latest"]["value"]) for e in ranked}
    order = sorted(values, key=lambda k: values[k], reverse=True)
    for e in out:
        e["rank"] = (1 + sum(1 for v in values.values() if v > values[e["ticker"]])
                     if e["ticker"] in values else None)
    agg = {
        "count": len(values),
        "sum": sum(values.values()) if values else None,
        "median": statistics.median(values.values()) if values else None,
        "order_desc": order,
        "concepts": sorted({e["pick"]["concept"] for e in ranked}),
    }
    return {"fiscal_year": fy, "family": family, "member": member, "filers": out,
            "aggregates": agg}


def cross_period(lib: Library, ticker: str, years: list[int], family: str,
                 member: str | None = None) -> dict[str, Any]:
    """One filer's family over several years: original and latest per year, and CAGR."""
    per_year = []
    for fy in sorted(years):
        res = family_values(lib, ticker, fy, family, member, dimensional=bool(member))
        pick = headline(res["rows"]) if not member else member_pick(res["rows"])
        per_year.append({"fiscal_year": fy, "filing": res["filing"], "pick": pick,
                         "rows": res["rows"]})
    vals = [(y["fiscal_year"], y["pick"]["latest"]["value"]) for y in per_year
            if y["pick"] and y["pick"]["latest"]["value"] is not None]
    cagr = None
    if len(vals) >= 2 and vals[0][1] > 0 and vals[-1][1] > 0:
        years_n = vals[-1][0] - vals[0][0]
        if years_n > 0:
            cagr = (vals[-1][1] / vals[0][1]) ** (1 / years_n) - 1
    return {"ticker": ticker, "family": family, "member": member, "years": per_year,
            "cagr": cagr, "cagr_from": vals[0][0] if vals else None,
            "cagr_to": vals[-1][0] if vals else None}


def coverage(lib: Library, family: str | None = None) -> dict[str, Any]:
    """Which filer-years are on disk, and (with a family) whether the family is tagged.

    Cells: ``forms`` filed for the year; with ``family`` also ``tagged`` (a headline fact
    exists) and its concept.
    """
    corpus = lib.corpus
    years = corpus.years()
    rows = []
    for t in corpus.tickers():
        cells = {}
        for y in years:
            forms = [f.form for f in corpus.for_ticker(t) if f.fiscal_year == y]
            cell: dict[str, Any] = {"forms": forms}
            if family and forms:
                res = family_values(lib, t, y, family)
                pick = headline(res["rows"])
                cell["tagged"] = pick is not None
                cell["concept"] = pick["concept"] if pick else None
            cells[str(y)] = cell
        rows.append({"ticker": t, "cells": cells})
    return {"years": years, "family": family, "rows": rows}


def concept_search(lib: Library, ticker: str, fy: int, query: str,
                   include_dims: bool = True) -> dict[str, Any]:
    """Own-column facts whose concept or label contains ``query`` (case-insensitive).

    This bypasses the concept mapping: it finds what the filer tagged, mapped or not.
    """
    own = lib.corpus.own_filing(ticker, fy)
    if own is None:
        return {"filing": None, "rows": []}
    q = query.lower().strip()
    labels = lib.labels(own)

    def match(f: RawFact) -> bool:
        if not f.numeric:
            return False
        if f.context.dims and not include_dims:
            return False
        return q in f.concept.lower() or q in labels.get(f.concept, "").lower()

    _, rows = _s1_rows(lib, ticker, fy, match)
    return {"filing": filing_summary(own), "rows": rows[:400], "truncated": len(rows) > 400}


def members(lib: Library, ticker: str, fy: int, family: str) -> dict[str, Any]:
    """The dimensional facts of a family for one year, grouped by axis."""
    res = family_values(lib, ticker, fy, family, dimensional=True)
    axes: dict[str, list[dict[str, Any]]] = {}
    for row in res["rows"]:
        axis = " + ".join(a for a, _ in row["dims"]) or "(none)"
        axes.setdefault(axis, []).append(row)
    return {"filing": res["filing"], "axes": [{"axis": a, "rows": r}
                                              for a, r in sorted(axes.items())]}


def text_search(lib: Library, ticker: str, fy: int, query: str,
                section: str | None = None, limit: int = 60) -> dict[str, Any]:
    """Paragraphs of the year's annual report that contain every word of ``query``.

    Args:
        lib: The library.
        ticker: The filer.
        fy: The fiscal year.
        query: Words to find (all must appear; a quoted phrase must appear as written).
        section: Only blocks whose section path contains this text.
        limit: The most hits returned.

    Returns:
        ``{filing, sections, hits: [{section, text, index}]}``.
    """
    own = lib.corpus.own_filing(ticker, fy)
    if own is None:
        return {"filing": None, "sections": [], "hits": []}
    blocks = lib.blocks(own)
    phrases = [p.lower() for p in re.findall(r'"([^"]+)"', query)]
    words = [w.lower() for w in re.sub(r'"[^"]+"', " ", query).split() if w.strip()]
    hits = []
    for b in blocks:
        if section and section.lower() not in b["section"].lower():
            continue
        low = b["text"].lower()
        if all(p in low for p in phrases) and all(w in low for w in words):
            text = b["text"]
            hits.append({"index": b["index"], "section": b["section"],
                         "text": text if len(text) <= _SNIPPET_CHARS * 3
                         else text[:_SNIPPET_CHARS * 3] + " …"})
            if len(hits) >= limit:
                break
    sections = sorted({b["section"] for b in blocks})
    return {"filing": filing_summary(own), "sections": sections, "hits": hits}
