"""Canonical numeric-tolerance + structured extraction for XBRL answers.

The single source of truth for (a) how close a produced number must be to a
labelled expected value, and (b) how to pull the *audited* number(s), rows and
citations out of an :class:`XbrlRagResponse` — from the structured
``execution_result`` steps, **never** from the LLM's prose.

Moved here from ``src.eval.xbrl.numeric`` by WS-9 / E1 and extended with
``screen`` row extraction and fact-id extraction (§7.3 of the design doc).
``src.eval.xbrl.numeric`` re-exports every public name, so the WS-2 golden
grader and ``test/integration/conftest.py`` keep working unchanged — the "one
implementation" property WS-2 established is preserved.

Every helper accepts an ``XbrlRagResponse`` object *or* its ``model_dump()`` dict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, List, Optional


def close_enough(actual: float, expected: float, rel: float = 1e-6) -> bool:
    """True if *actual* is within relative tolerance *rel* of *expected*.

    Facts come from the same store, so a headline-fact match is near-exact; a tiny
    relative tolerance absorbs float formatting only. Computed answers pass a looser
    ``rel`` (e.g. 1e-3 / 1e-2). A zero expected value is matched by an absolute
    band (< 1.0) since a relative tolerance is undefined at 0."""
    if expected == 0:
        return abs(actual) < 1.0
    return abs(actual - expected) / abs(expected) <= rel


def matches_any(actual: float, targets: list[float], rel: float = 1e-6) -> bool:
    """True if *actual* is within *rel* of any accepted target form."""
    return any(close_enough(actual, t, rel) for t in targets)


def match_reading(value: float, targets: Sequence[float],
                  readings: Sequence[Mapping[str, Any]], rel_tol: float) -> str | None:
    """Which accepted reading a produced value matches, each within ``rel_tol`` on its own.

    Spec: docs/specs/shares-outstanding-reading.md, requirement 6.

    Args:
        value: A produced number.
        targets: The key's accepted forms (``EvalCase.targets()``).
        readings: Alternate readings, each with ``reading`` and ``value``.
        rel_tol: The case's relative tolerance; never widened.

    Returns:
        ``"key"`` when a target matches; else the first matching reading's name; else
        ``None``.
    """
    if any(close_enough(value, t, rel_tol) for t in targets):
        return "key"
    for reading in readings:
        expected = reading.get("value")
        if isinstance(expected, (int, float)) and not isinstance(expected, bool) and (
                close_enough(value, float(expected), rel_tol)):
            return str(reading.get("reading"))
    return None


# ----------------------------------------------------------------------
# Structured extraction from an XbrlRagResponse (object or dict)
# ----------------------------------------------------------------------


def _execution_result(resp: Any) -> dict:
    er = getattr(resp, "execution_result", None)
    if er is None and isinstance(resp, dict):
        er = resp.get("execution_result")
    return er if isinstance(er, dict) else {}


def execution_steps(execution_result: Any) -> list[dict]:
    """Every step of an execution result, including decomposed sub-answers'.

    See docs/specs/grader-nested-results.md.

    Args:
        execution_result: An ``ExecutionResult`` dict, possibly decomposed into
            ``{"subqueries": {id: result}}``.

    Returns:
        The top-level steps, then each sub-question's, in order.
    """
    if not isinstance(execution_result, dict):
        return []
    steps = execution_result.get("steps")
    out: list[dict] = list(steps) if isinstance(steps, list) else []
    subqueries = execution_result.get("subqueries")
    if isinstance(subqueries, dict):
        for sub in subqueries.values():
            out.extend(execution_steps(sub))
    return out


def component_fact_ids(value: Any) -> list[Any]:
    """The fact ids a ``component_fact_ids`` value lists, one array level flattened.

    Args:
        value: The JSONB value: an object, an array or a scalar.

    Returns:
        The ids in order; an input's alternatives each appear.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return [value]
    out: list[Any] = []
    for item in items:
        out.extend(item if isinstance(item, (list, tuple)) else [item])
    return out


def _steps(resp: Any) -> list[dict]:
    return execution_steps(_execution_result(resp))


def _values_for_op(resp: Any, op: str) -> list[float]:
    return [
        s["value"]
        for s in _steps(resp)
        if s.get("op") == op
        and s.get("status") == "OK"
        and isinstance(s.get("value"), (int, float))
    ]


def lookup_step_values(resp: Any) -> list[float]:
    """All numeric values resolved by OK ``lookup`` steps."""
    return _values_for_op(resp, "lookup")


def compute_step_values(resp: Any) -> list[float]:
    """All numeric values produced by OK ``compute`` steps."""
    return _values_for_op(resp, "compute")


def all_numeric_values(resp: Any) -> list[float]:
    """Every audited number in the response — OK ``lookup`` + ``compute`` steps.

    A grader compares the labelled expected value against this set (the plan may
    resolve the headline lookup and/or a derived compute; either can carry the
    answer)."""
    return lookup_step_values(resp) + compute_step_values(resp) + breakdown_row_values(resp)


def breakdown_row_values(resp: Any) -> list[float]:
    """Component values of OK ``breakdown`` steps (docs/specs/breakdown-op.md).

    A breakdown step resolves to the members partitioning one filer's total, as rows;
    each row's value is an audited number the answer may state."""
    return [
        float(row["value"])
        for s in _steps(resp)
        if s.get("op") == "breakdown" and s.get("status") == "OK"
        for row in s.get("rows") or []
        if isinstance(row, dict) and isinstance(row.get("value"), (int, float))
    ]


def screen_rows(resp: Any) -> list[dict]:
    """Ranked rows from OK ``screen`` steps, in plan order.

    A screen step has no single scalar value — it resolves to a cross-filer ranked
    list, each row carrying its own ``value`` and ``fact_id``
    (``xbrl_factstore_screen``). Grading these needs the rows, not a number."""
    rows: list[dict] = []
    for s in _steps(resp):
        if s.get("op") == "screen" and s.get("status") == "OK":
            for row in s.get("rows") or []:
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def _as_fact_id(value: Any) -> int | None:
    """Coerce a fact id to int, or None if it isn't one.

    ``answer_details[].fact_ids`` mixes real fact ids with *step ids* (the
    synthesizer response format allows either), and ids arrive as both int and
    str. Only genuine numeric fact ids are grounding evidence."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def all_fact_ids(resp: Any) -> list[int]:
    """Every audited ``fact_id`` the response's OK steps rest on, de-duplicated.

    Sources, in the order the executor writes them: a lookup/screen step's
    ``citation.fact_id``, a compute step's ``component_fact_ids``, and each screen
    row's own ``fact_id``/``citation.fact_id``. Read from ``execution_result``
    only — the prose is not evidence."""
    out: list[int] = []
    seen = set()

    def _add(value: Any) -> None:
        fid = _as_fact_id(value)
        if fid is not None and fid not in seen:
            seen.add(fid)
            out.append(fid)

    for s in _steps(resp):
        if s.get("status") != "OK":
            continue
        _add((s.get("citation") or {}).get("fact_id"))
        for fid in component_fact_ids(s.get("component_fact_ids")):
            _add(fid)
        for row in s.get("rows") or []:
            if isinstance(row, dict):
                _add(row.get("fact_id"))
                _add((row.get("citation") or {}).get("fact_id"))
                # A derived-metric screen row has no single fact_id — a ratio has no
                # one fact — so it carries a synthetic row id and puts the audited
                # facts in per-row `component_fact_ids`
                # (xbrl_factstore_screen._derived_rows). Missing these read a
                # correctly-cited ranking as uncited.
                for fid in component_fact_ids(row.get("component_fact_ids")):
                    _add(fid)
    return out


def has_fact_citation(resp: Any) -> bool:
    """True if any OK step carries a real ``fact_id`` provenance — a lookup/screen
    step's ``citation.fact_id``, or a compute step's ``component_fact_ids``. This is
    the "numbers are audited, not generated" invariant the grader enforces."""
    for s in _steps(resp):
        if s.get("status") != "OK":
            continue
        citation = s.get("citation") or {}
        if citation.get("fact_id") is not None:
            return True
        if s.get("component_fact_ids"):
            return True
        # screen steps carry per-row fact_ids — or, for a derived metric, per-row
        # component_fact_ids instead of a single id.
        for row in s.get("rows") or []:
            if isinstance(row, dict) and (
                row.get("fact_id") is not None
                or (row.get("citation") or {}).get("fact_id") is not None
                or row.get("component_fact_ids")
            ):
                return True
    return False


def narrative_chunk_ids(resp: Any) -> list[str]:
    """Chunk ids retrieved by the narrative branch, de-duplicated in rank order."""
    chunks = getattr(resp, "narrative_chunks", None)
    if chunks is None and isinstance(resp, dict):
        chunks = resp.get("narrative_chunks")
    out: list[str] = []
    seen = set()
    for c in chunks or []:
        cid = c.get("chunk_id") if isinstance(c, dict) else None
        if cid is not None and str(cid) not in seen:
            seen.add(str(cid))
            out.append(str(cid))
    return out
