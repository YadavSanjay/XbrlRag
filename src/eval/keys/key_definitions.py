# The stated definitions of the definitional v2 key families: Debt, DeferredRevenue, and the
# parent attribution of net and comprehensive income. Spec: docs/specs/key-definitions.md.
# Pure functions over FSDS tables and SEC companyfacts documents; no network, no database.

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from src.eval.keys.core import AnswerKey
from src.eval.keys.statement_witness import (
    StatementRule,
    WitnessBlocked,
    WitnessLine,
    select_lines,
)
from src.eval.sec_reference.fsds import (
    FsdsTables,
    fsds_period,
    fsds_uom,
    num_values,
    statement_lines,
    submission,
)

# Requirement 1: family -> definition id.
DEFINITIONS: dict[str, str] = {
    "Debt": "debt_total",
    "DeferredRevenue": "deferred_revenue_total",
    "NetIncome": "net_income_parent",
    "ComprehensiveIncome": "comprehensive_income_parent",
}
# The composite keys' concepts (requirements 2 and 3).
DEBT_CONCEPT = "s4:debt_total"
DEFERRED_REVENUE_CONCEPT = "s4:deferred_revenue_total"
DEFERRED_REVENUE_PRESENTED = "s4:deferred_revenue_presented"
KEY_POLICIES = ("S1", "S4", "S6")
# Statuses of a defined key; the blocking ones stop a build (requirement 6).
DEFINED_STATUSES = ("defined", "no_fsds", "not_presented", "component_missing",
                    "component_mismatch")
BLOCKING_STATUSES = frozenset({"component_missing", "component_mismatch"})
# The shapes whose cases carry a defined key's readings (requirement 5).
READING_SHAPES = frozenset({"lookup", "trend"})

# Requirement 2 step 5: the debt note totals, in preference order.
_DEBT_NOTE_TAGS = ("DebtLongtermAndShorttermCombinedAmount", "LongTermDebt")
# Requirement 3 step 2: the deferred revenue totals and non-current parts, in preference order.
_DR_TOTAL_TAGS = ("ContractWithCustomerLiability", "DeferredRevenue")
_DR_NONCURRENT_TAGS = ("ContractWithCustomerLiabilityNoncurrent", "DeferredRevenueNoncurrent")

# Requirement 4: the parent and consolidated elements of each family.
PARENT_ELEMENTS: dict[str, tuple[str, ...]] = {
    "NetIncome": ("us-gaap:NetIncomeLoss", "ifrs-full:ProfitLossAttributableToOwnersOfParent"),
    "ComprehensiveIncome": ("us-gaap:ComprehensiveIncomeNetOfTax",
                            "ifrs-full:ComprehensiveIncomeAttributableToOwnersOfParent"),
}
CONSOLIDATED_ELEMENTS: dict[str, tuple[str, ...]] = {
    "NetIncome": (
        "us-gaap:ProfitLoss",
        "us-gaap:NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest",
        "ifrs-full:ProfitLoss"),
    "ComprehensiveIncome": (
        "us-gaap:ComprehensiveIncomeNetOfTaxIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:ComprehensiveIncome"),
}
_NCI_REASON = "substitute:includes_noncontrolling_interests"

_SAME_REL = 1e-9
_TOTAL_CURRENT_LIABILITIES = re.compile("total current liabilities", re.IGNORECASE)
_LEASE = re.compile("lease", re.IGNORECASE)


class DefinedComponent(TypedDict):
    """One input of a defined key (requirement 2, step 3).

    Fields: ``concept`` (str), ``value`` (float), ``source`` (``"statement"`` for a presented
    line, ``"note"`` for a filing fact on no statement line), ``section`` (``"current"``,
    ``"noncurrent"`` or ``"note"``), ``plabel`` (str | None), ``stmt`` (str | None),
    ``report`` (int | None), ``line`` (int | None), ``lease_inclusive`` (bool).
    """

    concept: str
    value: float
    source: str
    section: str
    plabel: str | None
    stmt: str | None
    report: int | None
    line: int | None
    lease_inclusive: bool


class DefinedKey(TypedDict):
    """A key built under a stated definition (requirements 2 and 3).

    Fields: ``family`` (str), ``definition`` (str), ``status`` (str), ``basis`` (str | None),
    ``value`` (float | None), ``components`` (list[DefinedComponent]), ``key``
    (``AnswerKey`` | None), ``readings`` (list[dict]), ``reason`` (str | None).
    """

    family: str
    definition: str
    status: str
    basis: str | None
    value: float | None
    components: list[DefinedComponent]
    key: AnswerKey | None
    readings: list[dict[str, Any]]
    reason: str | None


# --------------------------------------------------------------------------- helpers


def _same(a: float | None, b: float | None) -> bool:
    """Whether two amounts are the same (1e-9 relative: both carry the filed value)."""
    if a is None or b is None:
        return False
    return abs(a - b) <= _SAME_REL * max(abs(a), abs(b), 1.0)


class _DisagreeingFactsError(ValueError):
    """One filing lists a fact more than once with different values."""


def _filing_fact(company_facts: Mapping[str, Any], concept: str, adsh: str, period_end: str,
                 unit: str) -> float | None:
    """The instant value of ``concept`` that filing ``adsh`` tags at ``period_end``, or None.

    A companyfacts document may list one fact under several frames; they must carry one value.

    Raises:
        _DisagreeingFactsError: The filing's entries for the fact are not all the same value.
    """
    taxonomy, _sep, tag = concept.partition(":")
    node = company_facts.get("facts", {}).get(taxonomy, {}).get(tag) or {}
    values = [float(e["val"]) for e in node.get("units", {}).get(unit, [])
              if e.get("accn") == adsh and e.get("end") == period_end and "start" not in e
              and e.get("val") is not None]
    if not values:
        return None
    if not all(_same(v, values[0]) for v in values):
        raise _DisagreeingFactsError(f"{concept}: filing {adsh} lists {sorted(set(values))} at "
                                f"{period_end}")
    return values[0]


def _first_fact(company_facts: Mapping[str, Any], tags: Sequence[str], adsh: str,
                period_end: str, unit: str) -> tuple[str, float] | None:
    """The first of the us-gaap ``tags`` the filing tags at ``period_end``, with its value."""
    for tag in tags:
        value = _filing_fact(company_facts, f"us-gaap:{tag}", adsh, period_end, unit)
        if value is not None:
            return f"us-gaap:{tag}", value
    return None


def _empty(family: str, status: str, reason: str | None = None,
           components: list[DefinedComponent] | None = None) -> DefinedKey:
    return {"family": family, "definition": DEFINITIONS[family], "status": status,
            "basis": None, "value": None, "components": components or [], "key": None,
            "readings": [], "reason": reason}


def _section_end(tables: FsdsTables, adsh: str) -> dict[int, int]:
    """Report -> the line number of its first ``total current liabilities`` line."""
    out: dict[int, int] = {}
    for ln in statement_lines(tables, adsh):
        if ln["report"] not in out and _TOTAL_CURRENT_LIABILITIES.search(ln["plabel"]):
            out[ln["report"]] = ln["line"]
    return out


def _components(tables: FsdsTables, lines: Sequence[WitnessLine], company_facts: Mapping[str, Any],
                adsh: str, period_end: str, unit: str
                ) -> tuple[list[DefinedComponent], str | None, str | None]:
    """The components of the selected lines, or a blocking status and its reason.

    Returns:
        ``(components, status, reason)``; ``status`` is None when every component is valued.
    """
    ends = _section_end(tables, adsh)
    out: list[DefinedComponent] = []
    for ln in lines:
        end = ends.get(ln["report"])
        section = "current" if end is not None and ln["line"] < end else "noncurrent"
        lease = bool(_LEASE.search(ln["plabel"]) or _LEASE.search(ln["tag"]))
        if ln["concept"].startswith("ext:"):
            value = float(ln["value"])
        else:
            fact = _filing_fact(company_facts, ln["concept"], adsh, period_end, unit)
            if fact is None:
                return out, "component_missing", f"{ln['concept']}: no filing fact"
            if not _same(fact, float(ln["value"])):
                return out, "component_mismatch", (
                    f"{ln['concept']}: companyfacts {fact} against statement {ln['value']}")
            value = fact
        out.append({"concept": ln["concept"], "value": value, "source": "statement",
                    "section": section, "plabel": ln["plabel"], "stmt": ln["stmt"],
                    "report": ln["report"], "line": ln["line"], "lease_inclusive": lease})
    return out, None, None


def _note_component(concept: str, value: float) -> DefinedComponent:
    return {"concept": concept, "value": value, "source": "note", "section": "note",
            "plabel": None, "stmt": None, "report": None, "line": None,
            "lease_inclusive": False}


def _iso(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd:
        return None
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()


def _key(sub: Mapping[str, str], concept: str, value: float,
         components: Sequence[DefinedComponent], cik: str, fiscal_year: int, adsh: str,
         period_end: str, unit: str) -> AnswerKey:
    """The composite ``AnswerKey`` of requirement 2 step 4."""
    sources = sorted({"sec_fsds" if c["concept"].startswith("ext:") else "sec_companyfacts"
                      for c in components})
    return {"value": value, "unit": unit, "decimals": None, "concept": concept, "cik": cik,
            "period_start": None, "period_end": period_end, "fiscal_year": fiscal_year,
            "calendar_year": None, "accession": adsh, "filed": _iso(sub.get("filed")),
            "form": sub.get("form") or None, "key_source": "+".join(sources),
            "selection": "statement_sum", "policies": list(KEY_POLICIES),
            "status": "single-source", "verified_by": []}


def _reading(reading: str, value: float, concept: str, period_end: str,
             adsh: str) -> dict[str, Any]:
    return {"reading": reading, "value": value, "concept": concept, "period_end": period_end,
            "accession": adsh}


# --------------------------------------------------------------------------- definitions


def key_definition(family: str) -> str | None:
    """The definition id of a family, or None for a family without one (requirement 1).

    Args:
        family: A metric family id.

    Returns:
        ``debt_total``, ``deferred_revenue_total``, ``net_income_parent``,
        ``comprehensive_income_parent``, or None.
    """
    return DEFINITIONS.get(family)


def debt_key(tables: FsdsTables, rules: Mapping[str, StatementRule], company_facts: dict,
             cik: str, fiscal_year: int, adsh: str, period_end: str, unit: str) -> DefinedKey:
    """Total debt as the balance sheet of ``adsh`` presents it (requirement 2).

    Args:
        tables: FSDS tables holding the filing.
        rules: Statement-line rules holding ``Debt``.
        company_facts: The filer's SEC companyfacts document.
        cik: 10-digit CIK.
        fiscal_year: The filer's label of the year.
        adsh: The annual filing read.
        period_end: The balance-sheet date.
        unit: The unit, e.g. ``USD``.

    Returns:
        The defined key; ``component_mismatch`` also when one filing lists a fact it reads
        more than once with different values.
    """
    try:
        return _debt(tables, rules, company_facts, cik, fiscal_year, adsh, period_end, unit)
    except _DisagreeingFactsError as exc:
        return _empty("Debt", "component_mismatch", f"disagreeing filing facts: {exc}")


def _debt(tables: FsdsTables, rules: Mapping[str, StatementRule], company_facts: dict,
          cik: str, fiscal_year: int, adsh: str, period_end: str, unit: str) -> DefinedKey:
    """``debt_key``'s steps; raises ``_DisagreeingFactsError`` on duplicate facts that disagree."""
    family = "Debt"
    sub = submission(tables, adsh)
    if sub is None:
        return _empty(family, "no_fsds")
    lines = select_lines(tables, rules[family], adsh, None, period_end, unit)
    if not lines:
        return _empty(family, "not_presented")
    components, blocked, reason = _components(tables, lines, company_facts, adsh, period_end,
                                              unit)
    if blocked is not None:
        return _empty(family, blocked, reason, components)
    value = float(sum(c["value"] for c in components))
    readings: list[dict[str, Any]] = []
    if not any(c["section"] == "current" for c in components):
        note = _first_fact(company_facts, _DEBT_NOTE_TAGS, adsh, period_end, unit)
        if note is not None and note[1] > value and not _same(note[1], value):
            readings.append(_reading("note_total", note[1], note[0], period_end, adsh))
    return {"family": family, "definition": DEFINITIONS[family], "status": "defined",
            "basis": "presented", "value": value, "components": components,
            "key": _key(sub, DEBT_CONCEPT, value, components, cik, fiscal_year, adsh,
                        period_end, unit),
            "readings": readings, "reason": None}


def deferred_revenue_key(tables: FsdsTables, rules: Mapping[str, StatementRule],
                         company_facts: dict, cik: str, fiscal_year: int, adsh: str,
                         period_end: str, unit: str) -> DefinedKey:
    """Total deferred revenue, current plus non-current (requirement 3).

    Args:
        tables: FSDS tables holding the filing.
        rules: Statement-line rules holding ``DeferredRevenue``.
        company_facts: The filer's SEC companyfacts document.
        cik: 10-digit CIK.
        fiscal_year: The filer's label of the year.
        adsh: The annual filing read.
        period_end: The balance-sheet date.
        unit: The unit, e.g. ``USD``.

    Returns:
        The defined key; ``component_mismatch`` also when one filing lists a fact it reads
        more than once with different values.
    """
    try:
        return _deferred(tables, rules, company_facts, cik, fiscal_year, adsh, period_end,
                         unit)
    except _DisagreeingFactsError as exc:
        return _empty("DeferredRevenue", "component_mismatch",
                      f"disagreeing filing facts: {exc}")


def _deferred(tables: FsdsTables, rules: Mapping[str, StatementRule], company_facts: dict,
              cik: str, fiscal_year: int, adsh: str, period_end: str, unit: str) -> DefinedKey:
    """``deferred_revenue_key``'s steps; raises ``_DisagreeingFactsError`` like ``_debt``."""
    family = "DeferredRevenue"
    sub = submission(tables, adsh)
    if sub is None:
        return _empty(family, "no_fsds")
    lines = select_lines(tables, rules[family], adsh, None, period_end, unit)
    readings: list[dict[str, Any]] = []
    if not lines:
        total = _first_fact(company_facts, _DR_TOTAL_TAGS, adsh, period_end, unit)
        if total is None:
            return _empty(family, "not_presented")
        components = [_note_component(*total)]
        value, basis = total[1], "note_total"
    else:
        components, blocked, reason = _components(tables, lines, company_facts, adsh,
                                                  period_end, unit)
        if blocked is not None:
            return _empty(family, blocked, reason, components)
        sections = {c["section"] for c in components}
        presented = float(sum(c["value"] for c in components))
        if sections != {"current"}:
            value, basis = presented, "presented_total"
        else:
            current = presented
            total = _first_fact(company_facts, _DR_TOTAL_TAGS, adsh, period_end, unit)
            noncurrent = _first_fact(company_facts, _DR_NONCURRENT_TAGS, adsh, period_end,
                                     unit)
            if total is not None and (total[1] >= current or _same(total[1], current)):
                components.append(_note_component(*total))
                value, basis = total[1], "note_total"
            elif noncurrent is not None:
                components.append(_note_component(*noncurrent))
                value, basis = current + noncurrent[1], "current_plus_note_noncurrent"
            else:
                value, basis = current, "no_noncurrent_reported"
            if not _same(value, current):
                shown = (components[0]["concept"] if len(lines) == 1
                         else DEFERRED_REVENUE_PRESENTED)
                readings.append(_reading("current_presented", current, shown, period_end,
                                         adsh))
    return {"family": family, "definition": DEFINITIONS[family], "status": "defined",
            "basis": basis, "value": value, "components": components,
            "key": _key(sub, DEFERRED_REVENUE_CONCEPT, value, components, cik, fiscal_year,
                        adsh, period_end, unit),
            "readings": readings, "reason": None}


# --------------------------------------------------------------------------- attribution


def _concept_values(tables: FsdsTables, adsh: str, concept: str, period_start: str | None,
                    period_end: str, unit: str) -> set[float]:
    """The consolidated FSDS values of ``concept`` (its taxonomy too) for one period."""
    taxonomy, _sep, tag = concept.partition(":")
    prefix = "ifrs" if taxonomy == "ifrs-full" else taxonomy
    ddate, qtrs = fsds_period(period_start, period_end)
    uom = fsds_uom(unit)
    out: set[float] = set()
    for v in num_values(tables, adsh, tag, consolidated=True):
        if v["custom"] or v["version"].split("/", 1)[0] != prefix:
            continue
        if (v["ddate"], v["qtrs"], v["uom"]) != (ddate, qtrs, uom) or v["value"] is None:
            continue
        out.add(v["value"])
    return out


def attribution_status(tables: FsdsTables, family: str, adsh: str, period_start: str | None,
                       period_end: str, unit: str, concept: str) -> str:
    """Whether a net or comprehensive income concept is the parent's figure (requirement 4).

    Args:
        tables: FSDS tables holding the filing.
        family: ``NetIncome`` or ``ComprehensiveIncome``.
        adsh: The annual filing read.
        period_start: ISO start of the period.
        period_end: ISO end of the period.
        unit: The unit.
        concept: The key's or line's concept, ``"<taxonomy>:<tag>"``.

    Returns:
        ``parent``, ``consolidated_is_parent`` or ``includes_noncontrolling_interests``.

    Raises:
        ValueError: Another family, or a concept that is neither the parent's nor the
            consolidated element of the family.
    """
    if family not in PARENT_ELEMENTS:
        raise ValueError(f"family {family!r} has no parent attribution")
    if concept in PARENT_ELEMENTS[family]:
        return "parent"
    if concept not in CONSOLIDATED_ELEMENTS[family]:
        raise ValueError(f"{concept} is neither a parent nor a consolidated {family} element")
    own = _concept_values(tables, adsh, concept, period_start, period_end, unit)
    for parent in PARENT_ELEMENTS[family]:
        for value in _concept_values(tables, adsh, parent, period_start, period_end, unit):
            if not any(_same(value, o) for o in own):
                return "includes_noncontrolling_interests"
    return "consolidated_is_parent"


def attribution_member(status: str, concept: str, value: float | None) -> dict[str, Any]:
    """A screen member's status from its attribution (requirement 6).

    Args:
        status: An ``attribution_status`` result.
        concept: The member's concept.
        value: The member's value.

    Returns:
        ``{"status", "reason", "concept", "value"}``.

    Raises:
        ValueError: ``status`` is not an ``attribution_status`` result.
    """
    if status in ("parent", "consolidated_is_parent"):
        return {"status": "ranked", "reason": None, "concept": concept, "value": value}
    if status == "includes_noncontrolling_interests":
        return {"status": "definition_excluded", "reason": _NCI_REASON, "concept": concept,
                "value": value}
    raise ValueError(f"unknown attribution status {status!r}")


def member_status(defined: DefinedKey) -> dict[str, Any]:
    """A screen member's status from its defined key (requirement 6).

    Args:
        defined: A ``debt_key`` or ``deferred_revenue_key`` result.

    Returns:
        ``{"status", "reason", "concept", "value"}``.

    Raises:
        WitnessBlocked: The defined key is blocking.
        ValueError: An unknown status.
    """
    status = defined["status"]
    if status == "defined":
        key = defined["key"]
        concept = key["concept"] if key is not None else None
        return {"status": "ranked", "reason": None, "concept": concept,
                "value": defined["value"]}
    if status == "no_fsds":
        return {"status": "held", "reason": "no_fsds", "concept": None, "value": None}
    if status == "not_presented":
        return {"status": "metric_not_tagged", "reason": None, "concept": None, "value": None}
    if status in BLOCKING_STATUSES:
        raise WitnessBlocked(f"{defined['family']}: {status}: {defined['reason']}")
    raise ValueError(f"unknown defined-key status {status!r}")


def case_readings(defined: DefinedKey, shape: str) -> list[dict[str, Any]]:
    """The alternate readings a v2 case of ``shape`` carries (requirement 5).

    Args:
        defined: A defined key.
        shape: The case's v2 shape (``lookup``, ``trend``, ``screen.top``, ...).

    Returns:
        The readings, in order; empty for a shape that carries none.
    """
    if shape not in READING_SHAPES:
        return []
    return [dict(r) for r in defined["readings"]]
