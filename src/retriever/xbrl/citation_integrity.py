"""Deterministic citation checks on XBRL answer statements.

Every id a statement cites must resolve to this query's evidence (rule 1), and every number it
states must match a value of the evidence it cites (rule 3); see
docs/specs/citation-integrity.md. No LLM, no database. The faithfulness verifier runs both
checks before its LLM call and drops a failing statement.

An id *resolves* when, as a string, it equals: for a ``fact_ids`` entry, the
``citation.fact_id`` or ``step_id`` of a non-``narrative`` step, the ``fact_id`` of a
``screen``/``breakdown`` row, a value of a step's ``component_fact_ids``, or an id a
``screen``/``breakdown`` row's ``component_fact_ids`` lists (resolving to that row,
docs/specs/citation-row-components.md); for a ``chunk_ids`` entry, the ``chunk_id`` of a
retrieved narrative chunk.

A statement citing a ``screen`` (its step id or a row) may also state the screen's counts and
the cited rows' ranks (docs/specs/verifier-derived-disclosures.md, Requirement 3).

Numbers are read by ``src.numerics.stated_numbers``, which the eval grader shares.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from src.numerics.stated_numbers import (
    PERCENT_POINT_STEP,
    StatedNumber,
    ratio_step,
    read_stated_numbers,
    reported_step,
    within_precision,
)
from src.retriever.xbrl.screen_evidence import screen_count_values

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class _Candidate:
    """One value a stated number may match.

    Attributes:
        value: The candidate value (unsigned comparison; percent candidates in points).
        rule_step: The candidate's own precision.
        percent: Whether it matches percent-stated numbers (True) or plain ones (False).
    """

    value: float
    rule_step: float
    percent: bool


def evidence_index(execution_result: dict | None) -> dict[str, list[dict]]:
    """Maps every resolvable fact id to the evidence records it resolves to.

    A record is a step dict, or a ``screen``/``breakdown`` row dict carrying the row's
    ``op``. One id can resolve to several records (a lookup's fact id that is also a screen
    row's).

    Args:
        execution_result: The query's ``ExecutionResult.to_dict()``.

    Returns:
        ``{id as str: [record, ...]}``.
    """
    index: dict[str, list[dict]] = {}

    def add(key: object, record: dict) -> None:
        if key is None:
            return
        records = index.setdefault(str(key), [])
        if not any(r is record for r in records):
            records.append(record)

    for step in (execution_result or {}).get("steps", []) or []:
        if step.get("op") == "narrative":
            continue
        add(step.get("step_id"), step)
        add((step.get("citation") or {}).get("fact_id"), step)
        for component in (step.get("component_fact_ids") or {}).values():
            add(component, step)
        for row in step.get("rows", []) or []:
            record = {**row, "op": step.get("op")}
            add(row.get("fact_id"), record)
            for component in row_component_ids(row):
                add(component, record)
    return index


def row_component_ids(row: dict) -> list[str]:
    """The fact ids a ``screen``/``breakdown`` row's ``component_fact_ids`` lists, as strings.

    An object lists its values and an array its items, in order; an item that is itself an
    array lists each of its items (one level); ``None`` lists nothing
    (docs/specs/citation-row-components.md, Requirement 1).

    Args:
        row: One row of a ``screen`` or ``breakdown`` step.

    Returns:
        The listed ids as strings, in order; ``[]`` when the row has none.
    """
    value = row.get("component_fact_ids")
    if value is None:
        return []
    if isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    ids: list[str] = []
    for item in items:
        for fid in item if isinstance(item, (list, tuple)) else [item]:
            if fid is not None:
                ids.append(str(fid))
    return ids


def retrieved_chunks(narrative_chunks: Iterable[dict]) -> dict[str, dict]:
    """Maps each retrieved chunk's id (as str) to the chunk."""
    return {str(c.get("chunk_id")): c for c in narrative_chunks if c.get("chunk_id") is not None}


def unresolved_citation_ids(
    detail: dict, execution_result: dict, narrative_chunks: list[dict]
) -> list[str]:
    """Returns the ids a statement cites that resolve to no evidence of this query.

    Args:
        detail: One ``answer_details`` entry (``statement``, ``fact_ids``, ``chunk_ids``).
        execution_result: The query's ``ExecutionResult.to_dict()`` (``steps``).
        narrative_chunks: The chunks the query retrieved.

    Returns:
        The unresolved ids as strings, ``fact_ids`` before ``chunk_ids``, each in citation
        order.
    """
    facts = evidence_index(execution_result)
    chunks = retrieved_chunks(narrative_chunks)
    unresolved = [str(f) for f in detail.get("fact_ids") or [] if str(f) not in facts]
    unresolved += [str(c) for c in detail.get("chunk_ids") or [] if str(c) not in chunks]
    return unresolved


def stated_numbers(statement: str) -> list[dict]:
    """Returns the numbers a statement states, skipping exempt ones (years, dates, ranks ...).

    Args:
        statement: One answer statement.

    Returns:
        ``{"text", "value", "step", "percent"}`` per stated number, in statement order.
    """
    return [n.to_dict() for n in read_stated_numbers(statement)]


def unmatched_numbers(
    detail: dict, execution_result: dict, plan: dict, narrative_chunks: list[dict]
) -> list[dict]:
    """Returns the stated numbers of a statement that match none of its cited evidence.

    Args:
        detail: One ``answer_details`` entry.
        execution_result: The query's ``ExecutionResult.to_dict()``.
        plan: The query's plan (``steps`` with ``id`` and, for compute, ``expr``).
        narrative_chunks: The chunks the query retrieved.

    Returns:
        The ``stated_numbers`` entries of ``detail["statement"]`` that match nothing.
    """
    stated = read_stated_numbers(str(detail.get("statement") or ""))
    if not stated:
        return []
    candidates = _cited_candidates(detail, execution_result, plan, narrative_chunks)
    return [n.to_dict() for n in stated if not _matches_any(n, candidates)]


def _matches_any(stated: StatedNumber, candidates: list[_Candidate]) -> bool:
    return any(c.percent == stated.percent and within_precision(stated, c.value, c.rule_step)
               for c in candidates)


def _cited_candidates(detail: dict, execution_result: dict, plan: dict,
                      narrative_chunks: list[dict]) -> list[_Candidate]:
    """The values a statement's citations make available to its numbers."""
    facts = evidence_index(execution_result)
    steps_by_id = {str(s.get("step_id")): s
                   for s in (execution_result or {}).get("steps", []) or []
                   if s.get("op") != "narrative" and s.get("step_id") is not None}
    exprs = {str(s.get("id")): str(s.get("expr") or "")
             for s in (plan or {}).get("steps", []) or [] if s.get("id") is not None}
    # An aggregate step's source screen (docs/specs/screen-aggregates.md): a statement citing
    # the aggregate may also state the values of the screen rows it covers.
    aggregate_of = {str(s.get("id")): str(s.get("of"))
                    for s in (plan or {}).get("steps", []) or []
                    if s.get("id") is not None and s.get("op") == "aggregate"
                    and s.get("of") is not None}
    # A cagr step's two inputs (docs/specs/plan-cagr-and-ratios.md, Requirement 10): a
    # statement citing the cagr may state them, as a compute's inputs.
    cagr_inputs = {str(s.get("id")): [str(s.get(k)) for k in ("start", "end")
                                      if s.get(k) is not None]
                   for s in (plan or {}).get("steps", []) or []
                   if s.get("id") is not None and s.get("op") == "cagr"}

    records: list[dict] = []

    def add(record: dict) -> None:
        if any(r is record for r in records):
            return
        records.append(record)
        if record.get("op") == "compute":
            expr = exprs.get(str(record.get("step_id"))) or str(record.get("expr") or "")
            for name in _IDENTIFIER.findall(expr):
                if name in steps_by_id:
                    add(steps_by_id[name])
        if record.get("op") == "aggregate":
            source = aggregate_of.get(str(record.get("step_id")))
            if source in steps_by_id:
                add(steps_by_id[source])
        if record.get("op") == "cagr":
            own_record = record.get("cagr")
            own: dict = own_record if isinstance(own_record, dict) else {}
            inputs = cagr_inputs.get(str(record.get("step_id"))) or [
                str(own[k]) for k in ("start", "end") if own.get(k) is not None]
            for name in inputs:
                if name in steps_by_id:
                    add(steps_by_id[name])
        for row in record.get("rows", []) or []:
            add({**row, "op": record.get("op")})

    for fid in detail.get("fact_ids") or []:
        for record in facts.get(str(fid), []):
            add(record)

    candidates: list[_Candidate] = []
    for record in records:
        candidates.extend(_record_candidates(record))
    candidates += _screen_candidates(detail, execution_result)
    chunks = retrieved_chunks(narrative_chunks)
    for cid in detail.get("chunk_ids") or []:
        chunk = chunks.get(str(cid))
        if chunk is None:
            continue
        for n in read_stated_numbers(str(chunk.get("text") or "")):
            candidates.append(_Candidate(n.value, n.step, n.percent))
    return candidates


def _record_candidates(record: dict) -> list[_Candidate]:
    """Candidates of one step or row: its value, and a row's share of total."""
    out: list[_Candidate] = []
    value = _number(record.get("value"))
    if value is not None:
        if record.get("op") in ("compute", "aggregate", "cagr"):
            plain_step = ratio_step(value)
        else:
            plain_step = reported_step(record.get("unit"), record.get("decimals"))
        out.append(_Candidate(abs(value), plain_step, False))
        out += _percent_candidates(value)
    share = _number(record.get("pct_of_total"))
    if share is not None:
        out += _percent_candidates(share)
    out += _definition_candidates(record)
    if record.get("op") == "aggregate":
        out += _aggregate_count_candidates(record)
    return out


def _definition_candidates(record: dict) -> list[_Candidate]:
    """A defined lookup's parts and disclosed readings, each stated at its own precision.

    See docs/specs/s4-lookup-deferred-revenue-debt.md (Wiring): the parts' and readings'
    fact ids resolve to the step, so a statement citing them may state their values.
    """
    definition = record.get("definition")
    if not isinstance(definition, dict) or definition.get("status") != "defined":
        return []
    step = reported_step(record.get("unit"), record.get("decimals"))
    out: list[_Candidate] = []
    for item in [*(definition.get("parts") or []), *(definition.get("readings") or [])]:
        value = _number(item.get("value")) if isinstance(item, dict) else None
        if value is not None:
            out.append(_Candidate(abs(value), step, False))
    return out


def _aggregate_count_candidates(record: dict) -> list[_Candidate]:
    """An aggregate step's counts, stated exactly: rows aggregated and the group's size.

    See docs/specs/screen-aggregates.md, Requirement 10 ("the 3 ranked retailers ... the
    group has 4 companies").
    """
    aggregate = record.get("aggregate")
    if not isinstance(aggregate, dict):
        return []
    out: list[_Candidate] = []
    for key in ("n", "universe_members"):
        count = _number(aggregate.get(key))
        if count is not None:
            out.append(_Candidate(abs(count), 0.0, False))
    return out


def _screen_candidates(detail: dict, execution_result: dict | None) -> list[_Candidate]:
    """A cited screen's counts and the cited rows' ranks, each stated exactly.

    See docs/specs/verifier-derived-disclosures.md, Requirement 3: a statement cites a screen
    when it cites the screen's step id or a row's fact id. Every cited screen's
    ``screen_count_values`` are candidates, and so is the rank of every row cited by its own
    fact id or one of its component ids (docs/specs/citation-row-components.md; citing the
    step id makes no rank a candidate).

    Args:
        detail: One ``answer_details`` entry.
        execution_result: The query's ``ExecutionResult.to_dict()``.

    Returns:
        Plain (non-percent) candidates with no precision allowance.
    """
    cited = {str(f) for f in detail.get("fact_ids") or []}
    if not cited:
        return []
    out: list[_Candidate] = []
    for step in (execution_result or {}).get("steps", []) or []:
        if step.get("op") != "screen":
            continue
        rows = step.get("rows", []) or []
        cited_rows = [r for r in rows if _row_ids(r) & cited]
        by_step = step.get("step_id") is not None and str(step.get("step_id")) in cited
        if not cited_rows and not by_step:
            continue
        out += [_Candidate(float(n), 0.0, False) for n in screen_count_values(step)]
        for row in cited_rows:
            rank = row.get("rank")
            if isinstance(rank, int) and not isinstance(rank, bool):
                out.append(_Candidate(float(abs(rank)), 0.0, False))
    return out


def _row_ids(row: dict) -> set[str]:
    """Every id citing *row*: its ``fact_id`` and the ids its ``component_fact_ids`` lists."""
    ids = set(row_component_ids(row))
    if row.get("fact_id") is not None:
        ids.add(str(row.get("fact_id")))
    return ids


def _percent_candidates(value: float) -> list[_Candidate]:
    """A value read as a percent: tried as a fraction (x100) and as points."""
    return [_Candidate(100 * abs(value), PERCENT_POINT_STEP, True),
            _Candidate(abs(value), PERCENT_POINT_STEP, True)]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
