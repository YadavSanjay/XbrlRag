# The fact-store rows the S4 defined measures read: a filing's balance-sheet lines and its
# tagged facts at the balance-sheet date. Spec: docs/specs/s4-lookup-deferred-revenue-debt.md
# (Wiring). Shared by the flat lookup and the flat screen, so both read one definition from
# the same rows; `defined_measure` itself stays pure.

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.eval.keys.statement_witness import (
    StatementRule,
    line_is_candidate,
    load_statement_rules,
)
from src.retriever.xbrl.defined_measure import (
    DEFINED_FAMILIES,
    NOTE_CONCEPTS,
    MeasureLine,
    NoteFact,
)

# The locked statement-line rules (the key side's); read, never copied.
STATEMENT_LINES_PATH = (Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2"
                        / "statement_lines.yaml")

# Standard-taxonomy namespace prefixes -> the prefix a ``MeasureLine`` concept carries; any
# other namespace is the filer's extension (``ext:``), as the key side names FSDS lines.
_PREFIXES = (
    ("http://fasb.org/us-gaap/", "us-gaap"),
    ("http://fasb.org/srt/", "srt"),
    ("http://xbrl.sec.gov/dei/", "dei"),
    ("http://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
    ("https://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
)
# Elements whose presence marks a role as the balance sheet.
_BALANCE_SHEET_MARKERS = frozenset({
    "us-gaap:LiabilitiesAndStockholdersEquity", "us-gaap:Liabilities", "us-gaap:Assets",
    "ifrs-full:EquityAndLiabilities", "ifrs-full:Liabilities", "ifrs-full:Assets",
})
# The current-liabilities total, by element: the line that ends the current section
# (requirement 3) when no stored label of it reads "total current liabilities".
_CURRENT_LIABILITIES_TOTALS = frozenset({"us-gaap:LiabilitiesCurrent",
                                         "ifrs-full:CurrentLiabilities"})
_TOTAL_CURRENT_LIABILITIES = re.compile("total current liabilities", re.IGNORECASE)
TOTAL_CURRENT_LIABILITIES_LABEL = "Total current liabilities"
# Label roles a line may be presented under, in the order tried. The fact store keeps no
# preferred-label role per arc, so every English label of the element is a reading of it.
_LABEL_ROLES = (
    "http://www.xbrl.org/2003/role/terseLabel",
    "http://www.xbrl.org/2003/role/label",
    "http://www.xbrl.org/2003/role/totalLabel",
    "http://www.xbrl.org/2003/role/verboseLabel",
)

_STANDARD_ROLE = "http://www.xbrl.org/2003/role/label"

Fetch = Callable[[Any, str, dict], list[dict]]


@lru_cache(maxsize=1)
def defined_rules() -> dict[str, StatementRule]:
    """The ``Debt`` and ``DeferredRevenue`` rules of ``statement_lines.yaml`` (loaded once).

    Returns:
        family -> rule, for the families ``defined_measure`` answers.
    """
    rules = load_statement_rules(STATEMENT_LINES_PATH)
    return {family: rules[family] for family in DEFINED_FAMILIES}


def concept_name(qname: str) -> str:
    """A stored ``<namespace URI>:<local>`` concept as ``<prefix>:<local>`` (``ext`` if custom).

    Args:
        qname: The concept as the fact store keeps it.

    Returns:
        e.g. ``us-gaap:CommercialPaper`` or ``ext:CurrentPortionOfDebtAndFinanceLeases``.
    """
    namespace, _sep, local = qname.rpartition(":")
    for uri, prefix in _PREFIXES:
        if namespace.startswith(uri):
            return f"{prefix}:{local}"
    return f"ext:{local}"


@dataclass
class FilingRows:
    """One filing's rows at one balance-sheet date, before a family's labels are chosen.

    Attributes:
        accession_no: The filing.
        period_end: The stored (next-midnight) balance-sheet date.
        lines: Balance-sheet lines in presentation order: ``concept``, ``labels`` (stored
            English labels, the filer's own first), ``fact`` (the chosen fact row or None).
        notes: The note-concept facts at the date (rounded duplicates collapsed).
        facts: fact_id -> the fact row (for citations).
        role: The balance-sheet role definition read, or None when none was found.
    """

    accession_no: str
    period_end: Any
    lines: list[dict[str, Any]] = field(default_factory=list)
    notes: list[NoteFact] = field(default_factory=list)
    facts: dict[int, dict[str, Any]] = field(default_factory=dict)
    role: str | None = None


def _decimals_rank(decimals: Any) -> float:
    """Precision order of a fact: exact (None / INF) first, then more decimals."""
    if decimals is None or (isinstance(decimals, str) and decimals.upper() == "INF"):
        return float("inf")
    return float(decimals)


def _line_fact(facts: Sequence[dict[str, Any]], unit: str) -> dict[str, Any] | None:
    """The fact a line shows: the answer's unit, a valued fact, the most precise, then id."""
    if not facts:
        return None
    return min(facts, key=lambda f: (f.get("unit") != unit, f.get("value") is None,
                                     -_decimals_rank(f.get("decimals")),
                                     int(f["fact_id"])))


def _collapse_rounded(facts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One fact per concept and unit when the others are it rounded (rounded-duplicates)."""
    from src.retriever.xbrl.xbrl_factstore_lookup import most_precise_consistent

    groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for f in facts:
        groups.setdefault((f["concept"], f.get("unit")), []).append(f)
    out: list[dict[str, Any]] = []
    for group in groups.values():
        valued = [f for f in group if f.get("value") is not None]
        if len(valued) > 1:
            win = most_precise_consistent(
                [(float(f["value"]), None if f.get("decimals") is None else int(f["decimals"]))
                 for f in valued])
            if win is not None:
                out.append(valued[win])
                continue
        out.extend(valued or group[:1])
    return out


def _balance_sheet_role(rows: Sequence[dict[str, Any]]) -> str | None:
    """The balance-sheet role among a filing's statement roles: it presents a balance-sheet
    total; the lowest sort code first (the face statement before a restated copy)."""
    roles: dict[str, set[str]] = {}
    for r in rows:
        roles.setdefault(r["role_definition"], set()).add(r["concept"])
    found = sorted(role for role, concepts in roles.items()
                   if concepts & _BALANCE_SHEET_MARKERS)
    return found[0] if found else None


def read_filing_rows(conn: Any, fetch: Fetch, filings: Iterable[tuple[str, Any, str]],
                     unit: str) -> dict[tuple[str, Any], FilingRows]:
    """Reads each filing's balance-sheet lines and note facts at a balance-sheet date.

    Three parameterized queries over all filings at once: the presentation network's
    statement roles, the filings' undimensioned instant facts, and the stored labels.

    Args:
        conn: An open connection.
        fetch: ``(conn, sql, params) -> rows`` (the caller's traced fetch).
        filings: ``(accession_no, stored period_end, cik)`` per filing and date.
        unit: The answer's unit (picks a line's fact when it has several).

    Returns:
        ``(accession_no, period_end)`` -> its rows; a filing with no balance-sheet role
        has no lines.
    """
    wanted = {(acc, pe): cik for acc, pe, cik in filings if acc and pe is not None}
    if not wanted:
        return {}
    accessions = sorted({acc for acc, _pe in wanted})
    presentation = fetch(conn, """
        SELECT pn.accession_no, pn.role_definition, pn.tree_sequence,
               q.namespace || ':' || q.local_name AS qname
        FROM   mv_presentation_network pn
        JOIN   element e ON e.element_id = pn.child_element_id
        JOIN   qname q   ON q.qname_id = e.qname_id
        WHERE  pn.accession_no = ANY(%(accessions)s)
          AND  pn.role_definition LIKE '%% - Statement - %%'
          AND  pn.role_definition NOT ILIKE '%%parenthetical%%'
        ORDER  BY pn.accession_no, pn.role_definition, pn.tree_sequence
    """, {"accessions": accessions})
    facts = fetch(conn, """
        SELECT f.fact_id, f.accession_no, f.concept AS qname, f.value, f.unit, f.period_end,
               f.html_anchor, f.page_num, f.section_path,
               (SELECT CASE WHEN x.is_decimals_infinity THEN NULL
                            ELSE x.decimals_value::int END
                  FROM fact x WHERE x.fact_id = f.fact_id) AS decimals
        FROM   mv_financial_facts f
        WHERE  f.accession_no = ANY(%(accessions)s)
          AND  f.period_type = 'instant'
          AND  f.segment_axis IS NULL
        ORDER  BY f.fact_id
    """, {"accessions": accessions})

    by_filing: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for f in facts:
        key = (f["accession_no"], f["period_end"])
        if key in wanted:
            by_filing.setdefault(key, []).append({**f, "concept": concept_name(f["qname"])})
    statements: dict[str, list[dict[str, Any]]] = {}
    for r in presentation:
        statements.setdefault(r["accession_no"], []).append(
            {**r, "concept": concept_name(r["qname"])})

    qnames = sorted({f["qname"] for rows in by_filing.values() for f in rows})
    labels = _labels(conn, fetch, qnames, sorted(set(wanted.values()))) if qnames else {}

    out: dict[tuple[str, Any], FilingRows] = {}
    for (acc, pe), cik in wanted.items():
        rows = by_filing.get((acc, pe), [])
        result = FilingRows(acc, pe, facts={int(f["fact_id"]): f for f in rows})
        by_concept: dict[str, list[dict[str, Any]]] = {}
        for f in rows:
            by_concept.setdefault(f["concept"], []).append(f)
        stmt = statements.get(acc, [])
        result.role = _balance_sheet_role(stmt)
        seen: set[tuple[Any, str]] = set()
        for r in stmt:
            if r["role_definition"] != result.role:
                continue
            key = (r["tree_sequence"], r["qname"])
            if key in seen:
                continue
            seen.add(key)
            result.lines.append({
                "concept": r["concept"], "qname": r["qname"],
                "labels": _ordered_labels(labels.get(r["qname"], []), cik),
                "standard_label": (None if r["concept"].startswith("ext:")
                                   else _standard_label(labels.get(r["qname"], []))),
                "fact": _line_fact(by_concept.get(r["concept"], []), unit),
            })
        result.notes = [
            {"fact_id": int(f["fact_id"]), "concept": f["concept"],
             "value": None if f["value"] is None else float(f["value"]), "unit": f["unit"],
             "decimals": f["decimals"]}
            for f in _collapse_rounded([f for f in rows if f["concept"] in NOTE_CONCEPTS])
        ]
        out[(acc, pe)] = result
    return out


def _labels(conn: Any, fetch: Fetch, qnames: Sequence[str],
            ciks: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """The stored English labels of the concepts: shared ones and the filers' own."""
    rows = fetch(conn, """
        SELECT concept, cik_override, label_role, label
        FROM   concept_labels
        WHERE  concept = ANY(%(qnames)s)
          AND  lower(xml_lang) LIKE 'en%%'
          AND  label_role = ANY(%(roles)s)
          AND  (cik_override IS NULL OR cik_override = ANY(%(ciks)s))
    """, {"qnames": list(qnames), "roles": list(_LABEL_ROLES), "ciks": list(ciks)})
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["concept"], []).append(r)
    return out


def _ordered_labels(rows: Sequence[Mapping[str, Any]], cik: str) -> list[str]:
    """The filer's own labels first, then the shared ones; each by ``_LABEL_ROLES``."""
    own = [r for r in rows if r.get("cik_override") == cik]
    shared = [r for r in rows if r.get("cik_override") is None]
    out: list[str] = []
    for group in (own, shared):
        for role in _LABEL_ROLES:
            for r in group:
                if r.get("label_role") == role and r["label"] not in out:
                    out.append(str(r["label"]))
    return out


def _standard_label(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """A standard element's shared standard label (role ``label``), or None."""
    return next((str(r["label"]) for r in rows
                 if r.get("cik_override") is None and r.get("label_role") == _STANDARD_ROLE),
                None)


def _excluded(rule: StatementRule, label: str) -> bool:
    """Whether a label matches one of the rule's exclusions."""
    return any(re.search(p, label, re.IGNORECASE) for p in rule["exclude"])


def measure_lines(filing: FilingRows, rule: StatementRule) -> list[MeasureLine]:
    """The filing's lines as ``MeasureLine`` rows for one family's rule.

    The fact store keeps no preferred label per presentation arc, and a standard
    element's stored labels are shared across filers (the first filing loaded wrote
    them). So a line is read under the first stored label that meets the rule, unless
    the element's standard label is one the rule excludes (an operating or finance lease,
    a deferred tax, ...): that is the element's own definition and no other reading
    admits it. An extension element's labels are the filer's own. A line meeting the rule
    under no label is read under one naming the current-liabilities total, which places
    the others (requirement 3), else under its first label.

    Args:
        filing: ``read_filing_rows``'s rows of one filing and date.
        rule: The family's statement-line rule.

    Returns:
        Every line in presentation order.
    """
    out: list[MeasureLine] = []
    for ln in filing.lines:
        concept = ln["concept"]
        labels = ln["labels"]
        standard = ln.get("standard_label")
        vetoed = standard is not None and _excluded(rule, standard)
        label = None if vetoed else next(
            (t for t in labels if line_is_candidate(rule, t, concept)), None)
        if label is None:
            label = next((t for t in labels if _TOTAL_CURRENT_LIABILITIES.search(t)), None)
        if label is None and concept in _CURRENT_LIABILITIES_TOTALS:
            label = TOTAL_CURRENT_LIABILITIES_LABEL
        if label is None:
            label = standard or (labels[0] if labels else concept.split(":", 1)[-1])
        fact = ln["fact"]
        out.append({
            "fact_id": None if fact is None else int(fact["fact_id"]),
            "concept": concept,
            "label": label,
            "value": None if fact is None or fact["value"] is None else float(fact["value"]),
            "unit": None if fact is None else fact["unit"],
            "decimals": None if fact is None else fact["decimals"],
        })
    return out
