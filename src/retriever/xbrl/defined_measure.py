# The S4 defined measures of Debt and DeferredRevenue, built from a filing's own facts.
# Spec: docs/specs/s4-lookup-deferred-revenue-debt.md. Pure functions over the rows the caller
# reads from the fact store (the filing's balance-sheet lines and its tagged note facts); no
# database, no network. The definition is the key side's (docs/specs/key-definitions.md
# requirements 2 and 3, src/eval/keys/key_definitions.py): on the same facts the two agree.

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, TypedDict

from src.eval.keys.statement_witness import StatementRule, line_is_candidate


class MeasureLine(TypedDict):
    """One line of the filing's balance sheet, in presentation order (requirement 2).

    Fields: ``fact_id`` (int | None; None only for a line with no fact, such as a header),
    ``concept`` (``<taxonomy>:<local>``, ``ext:<local>`` for an extension), ``label`` (the
    line's presented label), ``value`` (float | None; None for a nil fact or no fact),
    ``unit`` (str | None), ``decimals`` (int | str | None; None or ``"INF"`` is exact).
    """

    fact_id: int | None
    concept: str
    label: str
    value: float | None
    unit: str | None
    decimals: int | str | None


class NoteFact(TypedDict):
    """One tagged fact of the filing at the balance-sheet date, on or off a line (requirement 2).

    Fields: ``fact_id`` (int), ``concept`` (str), ``value`` (float | None; None is nil),
    ``unit`` (str | None), ``decimals`` (int | str | None).
    """

    fact_id: int
    concept: str
    value: float | None
    unit: str | None
    decimals: int | str | None


class MeasurePart(TypedDict):
    """One fact the defined value adds up (requirement 7).

    Fields: ``fact_id`` (int), ``concept`` (str), ``value`` (float), ``section``
    (``"current"``, ``"noncurrent"`` or ``"note"``), ``label`` (str | None; None for a note).
    """

    fact_id: int
    concept: str
    value: float
    section: str
    label: str | None


class DefinedMeasure(TypedDict):
    """The S4 value of a Debt or DeferredRevenue lookup, with its parts (requirement 7).

    Fields: ``family`` (str), ``definition`` (``debt_total`` | ``deferred_revenue_total``),
    ``concept`` (``s4:debt_total`` | ``s4:deferred_revenue_total``), ``status`` (``defined``
    | ``not_presented`` | ``unit_mismatch`` | ``conflicting_facts``), ``basis`` (str | None),
    ``value`` (float | None), ``unit`` (str), ``decimals`` (int | None), ``parts``
    (list[MeasurePart]), ``fact_ids`` (list[int]), ``readings`` (list[dict]: ``reading``,
    ``value``, ``concept``, ``fact_ids``), ``disclosure`` (str), ``reason`` (str | None).
    """

    family: str
    definition: str
    concept: str
    status: str
    basis: str | None
    value: float | None
    unit: str
    decimals: int | None
    parts: list[MeasurePart]
    fact_ids: list[int]
    readings: list[dict[str, Any]]
    disclosure: str
    reason: str | None


# Requirement 1: family -> (definition, concept, the definition's name in a disclosure).
DEFINED_FAMILIES: dict[str, tuple[str, str, str]] = {
    "Debt": ("debt_total", "s4:debt_total", "total debt"),
    "DeferredRevenue": ("deferred_revenue_total", "s4:deferred_revenue_total",
                        "total deferred revenue"),
}
DEFERRED_REVENUE_PRESENTED = "s4:deferred_revenue_presented"
# Requirement 5 step 3 and requirement 6: the note concepts read, in preference order.
DEBT_NOTE_TOTALS = ("us-gaap:DebtLongtermAndShorttermCombinedAmount", "us-gaap:LongTermDebt")
DR_NOTE_TOTALS = ("us-gaap:ContractWithCustomerLiability", "us-gaap:DeferredRevenue")
DR_NOTE_NONCURRENT = ("us-gaap:ContractWithCustomerLiabilityNoncurrent",
                      "us-gaap:DeferredRevenueNoncurrent")
NOTE_CONCEPTS = (*DEBT_NOTE_TOTALS, *DR_NOTE_TOTALS, *DR_NOTE_NONCURRENT)

# Requirement 8: basis -> its phrase in the disclosure.
_BASIS_PHRASES = {
    "presented": "the sum of its presented borrowing lines",
    "presented_total": "the balance-sheet lines as presented",
    "note_total": "the filing's tagged total",
    "current_plus_note_noncurrent": "the presented current line plus the tagged non-current "
                                    "amount",
    "no_noncurrent_reported": "the presented current line (the filing reports no non-current "
                              "amount)",
}
_READING_PHRASES = {
    "note_total": "the debt note's tagged total",
    "current_presented": "the presented current amount",
}

_SAME_REL = 1e-9
_TOTAL_CURRENT_LIABILITIES = re.compile("total current liabilities", re.IGNORECASE)


class _ConflictingFactsError(ValueError):
    """A note concept read has several facts in the answer's unit that are not the same."""


def _same(a: float, b: float) -> bool:
    """Whether two amounts are the same (1e-9 relative, as key-definitions)."""
    return abs(a - b) <= _SAME_REL * max(abs(a), abs(b), 1.0)


def _finite_decimals(decimals: int | str | None) -> int | None:
    """A fact's decimals as an int, None when exact (``INF`` or None, never 0)."""
    if decimals is None or isinstance(decimals, bool):
        return None
    if isinstance(decimals, int):
        return decimals
    text = str(decimals).strip()
    if text.upper() == "INF" or not text:
        return None
    return int(text)


def _note_value(notes: Sequence[NoteFact], concept: str, unit: str) -> MeasurePart | None:
    """The concept's valued note fact in ``unit`` as a part; equal duplicates count once.

    Requirement 4: a nil or a fact in another unit is absent, never 0; the first of equal
    duplicates is cited.

    Raises:
        _ConflictingFactsError: Two such facts are not the same.
    """
    found: list[MeasurePart] = []
    for n in notes:
        value = n["value"]
        if n["concept"] == concept and n["unit"] == unit and value is not None:
            found.append({"fact_id": int(n["fact_id"]), "concept": concept,
                          "value": float(value), "section": "note", "label": None})
    if not found:
        return None
    first = found[0]
    for other in found[1:]:
        if not _same(other["value"], first["value"]):
            raise _ConflictingFactsError(
                f"{concept}: facts {first['fact_id']} and {other['fact_id']} carry "
                f"{first['value']} and {other['value']} {unit}")
    return first


def _first_note(notes: Sequence[NoteFact], concepts: Sequence[str],
                unit: str) -> MeasurePart | None:
    """The first of ``concepts`` the filing tags (later ones are not read)."""
    for concept in concepts:
        fact = _note_value(notes, concept, unit)
        if fact is not None:
            return fact
    return None


def _selected(lines: Sequence[MeasureLine],
              rule: StatementRule) -> list[tuple[MeasurePart, str | None]]:
    """The selected lines as parts with their units, each part with its section (req. 3).

    Raises:
        ValueError: A valued line carries no fact id.
    """
    end = next((i for i, ln in enumerate(lines)
                if _TOTAL_CURRENT_LIABILITIES.search(ln["label"] or "")), None)
    out: list[tuple[MeasurePart, str | None]] = []
    seen: set[str] = set()
    for i, ln in enumerate(lines):
        value, fact_id = ln["value"], ln["fact_id"]
        if value is None:
            continue
        if fact_id is None:
            raise ValueError(f"line {ln['label']!r} ({ln['concept']}) has a value but no "
                             f"fact id")
        if ln["concept"] in seen or not line_is_candidate(rule, ln["label"] or "",
                                                         ln["concept"]):
            continue
        seen.add(ln["concept"])
        section = "current" if end is not None and i < end else "noncurrent"
        out.append(({"fact_id": int(fact_id), "concept": ln["concept"], "value": float(value),
                     "section": section, "label": ln["label"]}, ln["unit"]))
    for pattern in rule["prefer"]:
        hits = [(p, u) for p, u in out if re.search(pattern, p["label"] or "", re.IGNORECASE)]
        if hits:
            return hits
    return out


def _local(concept: str) -> str:
    return concept.split(":", 1)[-1]


def _disclosure(family: str, status: str, basis: str | None,
                readings: Sequence[dict[str, Any]], reason: str | None) -> str:
    """The text the synthesizer states with the answer (requirement 8)."""
    name = DEFINED_FAMILIES[family][2]
    if status == "not_presented":
        return (f"No balance-sheet line meets the {name} definition; the answer is not "
                f"the defined {name}.")
    if status == "unit_mismatch":
        return (f"The {name} lines are not all in the answer's unit, so no {name} is "
                f"given ({reason}).")
    if status == "conflicting_facts":
        return f"The filing tags conflicting {name} facts, so no {name} is given ({reason})."
    text = f"Defined as {name}: {_BASIS_PHRASES[str(basis)]}."
    for r in readings:
        text += (f" Also disclosed, not answered: {_READING_PHRASES[r['reading']]} "
                 f"{_local(r['concept'])} = {r['value']:,.0f}.")
    return text


def _record(family: str, unit: str, status: str, *, basis: str | None = None,
            parts: Sequence[MeasurePart] = (), readings: Sequence[dict[str, Any]] = (),
            reason: str | None = None,
            decimals: dict[int, int | str | None] | None = None) -> DefinedMeasure:
    definition, concept, _name = DEFINED_FAMILIES[family]
    defined = status == "defined"
    kept = list(parts) if defined else []
    finite = [d for d in (_finite_decimals((decimals or {}).get(p["fact_id"])) for p in kept)
              if d is not None]
    return {
        "family": family, "definition": definition, "concept": concept, "status": status,
        "basis": basis if defined else None,
        "value": float(sum(p["value"] for p in kept)) if defined else None,
        "unit": unit, "decimals": min(finite) if finite else None,
        "parts": kept, "fact_ids": [p["fact_id"] for p in kept],
        "readings": [dict(r) for r in readings] if defined else [],
        "disclosure": _disclosure(family, status, basis, readings, reason),
        "reason": None if status in ("defined", "not_presented") else reason,
    }


def defined_measure(family: str, lines: Sequence[MeasureLine], notes: Sequence[NoteFact],
                    rule: StatementRule, unit: str) -> DefinedMeasure | None:
    """The S4 defined measure of ``family`` for one filing and balance-sheet date.

    Args:
        family: The lookup's concept family.
        lines: Every line of the filing's balance sheet, in presentation order.
        notes: The filing's facts at the balance-sheet date (only the note concepts of
            requirements 5 and 6 are read).
        rule: The family's statement-line rule (the key side's).
        unit: The unit of the answer, e.g. ``USD``.

    Returns:
        The measure for ``Debt`` and ``DeferredRevenue``; None for every other family.

    Raises:
        ValueError: A valued line carries no fact id.
    """
    if family not in DEFINED_FAMILIES:
        return None
    decimals: dict[int, int | str | None] = {
        ln["fact_id"]: ln["decimals"] for ln in lines if ln["fact_id"] is not None}
    for n in notes:
        decimals.setdefault(n["fact_id"], n["decimals"])
    chosen = _selected(lines, rule)
    off_unit = [(p, u) for p, u in chosen if u != unit]
    if off_unit:
        reason = "; ".join(f"{p['concept']} in {u}" for p, u in off_unit) + f", not {unit}"
        return _record(family, unit, "unit_mismatch", reason=reason)
    selected = [p for p, _u in chosen]
    try:
        if family == "Debt":
            status, basis, parts, readings = _debt(selected, notes, unit)
        else:
            status, basis, parts, readings = _deferred(selected, notes, unit)
    except _ConflictingFactsError as exc:
        return _record(family, unit, "conflicting_facts", reason=f"conflicting facts: {exc}")
    return _record(family, unit, status, basis=basis, parts=parts, readings=readings,
                   decimals=decimals)


_Built = tuple[str, str | None, list[MeasurePart], list[dict[str, Any]]]


def _debt(selected: list[MeasurePart], notes: Sequence[NoteFact], unit: str) -> _Built:
    """Requirement 5: the presented sum, and the note total disclosed as a reading."""
    if not selected:
        return "not_presented", None, [], []
    value = float(sum(p["value"] for p in selected))
    readings: list[dict[str, Any]] = []
    if not any(p["section"] == "current" for p in selected):
        note = _first_note(notes, DEBT_NOTE_TOTALS, unit)
        if note is not None:
            total = note["value"]
            if total > value and not _same(total, value):
                readings.append({"reading": "note_total", "value": total,
                                 "concept": note["concept"], "fact_ids": [note["fact_id"]]})
    return "defined", "presented", list(selected), readings


def _deferred(selected: list[MeasurePart], notes: Sequence[NoteFact], unit: str) -> _Built:
    """Requirement 6: key-definitions requirement 3's precedence."""
    if not selected:
        total = _first_note(notes, DR_NOTE_TOTALS, unit)
        if total is None:
            return "not_presented", None, [], []
        return "defined", "note_total", [total], []
    sections = {p["section"] for p in selected}
    if sections != {"current"}:
        return "defined", "presented_total", list(selected), []
    current = float(sum(p["value"] for p in selected))
    total = _first_note(notes, DR_NOTE_TOTALS, unit)
    noncurrent = _first_note(notes, DR_NOTE_NONCURRENT, unit)
    if total is not None and (total["value"] >= current or _same(total["value"], current)):
        parts, value, basis = [total], total["value"], "note_total"
    elif noncurrent is not None:
        parts = [*selected, noncurrent]
        value = current + noncurrent["value"]
        basis = "current_plus_note_noncurrent"
    else:
        parts, value, basis = list(selected), current, "no_noncurrent_reported"
    readings: list[dict[str, Any]] = []
    if not _same(value, current):
        concept = selected[0]["concept"] if len(selected) == 1 else DEFERRED_REVENUE_PRESENTED
        readings.append({"reading": "current_presented", "value": current,
                         "concept": concept, "fact_ids": [p["fact_id"] for p in selected]})
    return "defined", basis, parts, readings


def defined_precedence(defined: DefinedMeasure | None) -> str:
    """Which answer a flat lookup gives (requirement 10).

    Args:
        defined: A ``defined_measure`` result, or None.

    Returns:
        ``defined`` (answer the measure), ``candidate_order`` (today's choice) or ``refuse``.
    """
    if defined is None or defined["status"] == "not_presented":
        return "candidate_order"
    if defined["status"] == "defined":
        return "defined"
    return "refuse"


def screen_member(defined: DefinedMeasure) -> dict[str, Any]:
    """A flat screen member's status from its defined measure (requirement 10).

    Args:
        defined: A ``defined_measure`` result.

    Returns:
        ``{"status", "reason", "concept", "value", "fact_ids"}``.
    """
    status = defined["status"]
    if status == "defined":
        return {"status": "ranked", "reason": None, "concept": defined["concept"],
                "value": defined["value"], "fact_ids": list(defined["fact_ids"])}
    if status == "not_presented":
        return {"status": "metric_not_tagged", "reason": None, "concept": None, "value": None,
                "fact_ids": []}
    return {"status": "excluded", "reason": status, "concept": defined["concept"],
            "value": None, "fact_ids": []}
