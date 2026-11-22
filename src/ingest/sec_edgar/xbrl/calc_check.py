"""The sum-of-parts check per XBRL Calculations 1.1 (round-to-nearest), counted per filing.

Spec: docs/specs/calc-check-c11.md. `check_calculations` is the pure reference that the SQL
view `xbrl.mv_sum_of_parts_checks` mirrors; `calc_gate_decision` turns one filing's results
into GATE-B's hold-back decision against the committed, shrink-only exception list read by
`load_calc_exceptions`.

Calculations 1.1 in brief: each fact stands for the closed interval
[v - 10^-d / 2, v + 10^-d / 2] (`decimals` = d; INF is exact); duplicate facts of one
concept in one context are reduced to the intersection of their intervals, and
inconsistent duplicates stop the check; a summation is consistent when the parent's interval
and the weighted sum of its children's intervals intersect.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

SUMMATION_ARCROLES = frozenset({
    "http://www.xbrl.org/2003/arcrole/summation-item",
    "https://xbrl.org/2023/arcrole/summation-item",
})
EXCEPTION_FIELDS = ("accession_no", "link_role", "parent", "period_start", "period_end", "unit")

PASS, FAIL, PARTIAL, INAPPLICABLE = "pass", "fail", "partial", "inapplicable"

# Exact decimal arithmetic: SEC values are at most ~16 digits and decimals within +-20, so
# 100 significant digits never round.
_PRECISION = 100

_ContextKey = tuple[str, str | None, str, frozenset[tuple[str, str]], str]


@dataclass(frozen=True)
class _Group:
    """The facts of one concept at one context key, reduced per Calculations 1.1."""

    state: str  # "nil", "inconsistent" or "value"
    fact_ids: tuple[int, ...]
    low: Decimal | None = None
    high: Decimal | None = None


def _context_key(fact: Mapping[str, Any]) -> _ContextKey:
    dims = frozenset((str(axis), str(member)) for axis, member in fact["dimensions"])
    return (fact["entity"], fact["period_start"], fact["period_end"], dims, fact["unit"])


def _half_unit(decimals: int) -> Decimal:
    """Half of one unit in the last reported digit: 10^-decimals / 2."""
    return Decimal(5).scaleb(-decimals - 1)


def _has_excess_digits(value: Decimal, decimals: int) -> bool:
    scaled = value.scaleb(decimals)
    return scaled != scaled.to_integral_value()


def _reduce_group(facts: list[Mapping[str, Any]]) -> _Group:
    """Reduce one concept's facts at one context key (Requirement 1, Fact groups)."""
    ids = tuple(sorted(int(f["fact_id"]) for f in facts))
    nils = [f for f in facts if f["value"] is None]
    if len(nils) == len(facts):
        return _Group("nil", ids)
    if nils:
        return _Group("inconsistent", ids)
    by_decimals: dict[int | None, Decimal] = {}
    low: Decimal | None = None
    high: Decimal | None = None
    for f in facts:
        value = Decimal(f["value"])
        decimals = f["decimals"]
        if decimals in by_decimals and by_decimals[decimals] != value:
            return _Group("inconsistent", ids)
        by_decimals[decimals] = value
        if decimals is None:
            lo, hi = value, value
        else:
            if _has_excess_digits(value, decimals):
                return _Group("inconsistent", ids)
            half = _half_unit(decimals)
            lo, hi = value - half, value + half
        low = lo if low is None else max(low, lo)
        high = hi if high is None else min(high, hi)
    assert low is not None and high is not None
    if low > high:
        return _Group("inconsistent", ids)
    return _Group("value", ids, low, high)


def _trees(arcs: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, set[Decimal]]]:
    """Summation trees: (link_role, parent) -> child -> the distinct weights of its arcs."""
    trees: dict[tuple[str, str], dict[str, set[Decimal]]] = defaultdict(lambda: defaultdict(set))
    for arc in arcs:
        if arc["arcrole"] not in SUMMATION_ARCROLES:
            continue
        trees[(arc["link_role"], arc["parent"])][arc["child"]].add(Decimal(arc["weight"]))
    return trees


def _sort_key(r: Mapping[str, Any]) -> tuple:
    return (r["link_role"], r["parent"], r["entity"], r["period_start"] or "", r["period_end"],
            sorted(r["dimensions"]), r["unit"])


def check_calculations(
    facts: Iterable[Mapping[str, Any]],
    arcs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Check every bound summation of one filing per Calculations 1.1 round-to-nearest.

    Spec: docs/specs/calc-check-c11.md (Requirement 1). Pure: no I/O.

    Args:
        facts: Every numeric fact of the filing, as mappings with the keys `fact_id`,
            `concept`, `entity`, `period_start` (None for an instant), `period_end`,
            `dimensions` (iterable of (axis, member) pairs), `unit`, `value` (Decimal, or
            None when nil) and `decimals` (int, or None for INF).
        arcs: The filing's calculation arcs, as mappings with the keys `arcrole`,
            `link_role`, `parent`, `child` and `weight` (Decimal).

    Returns:
        One result dict per (link role, parent, context key) that binds, sorted by key.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        raw: dict[tuple[str, _ContextKey], list[Mapping[str, Any]]] = defaultdict(list)
        keys_by_concept: dict[str, set[_ContextKey]] = defaultdict(set)
        for fact in facts:
            key = _context_key(fact)
            raw[(fact["concept"], key)].append(fact)
            keys_by_concept[fact["concept"]].add(key)
        groups = {k: _reduce_group(v) for k, v in raw.items()}

        results: list[dict[str, Any]] = []
        for (link_role, parent), children in _trees(arcs).items():
            conflicting = any(len(weights) > 1 for weights in children.values())
            for key in keys_by_concept.get(parent, ()):
                result = _check_one(link_role, parent, key, children, groups, conflicting)
                if result is not None:
                    results.append(result)
    results.sort(key=_sort_key)
    return results


def _check_one(
    link_role: str,
    parent: str,
    key: _ContextKey,
    children: Mapping[str, set[Decimal]],
    groups: Mapping[tuple[str, _ContextKey], _Group],
    conflicting: bool,
) -> dict[str, Any] | None:
    """Check one tree at one context key; None when it does not bind."""
    parent_group = groups[(parent, key)]
    if parent_group.state == "nil":
        return None
    child_groups = {c: groups.get((c, key)) for c in children}
    if not any(g is not None and g.state != "nil" for g in child_groups.values()):
        return None
    entity, period_start, period_end, dims, unit = key
    result: dict[str, Any] = {
        "link_role": link_role, "parent": parent, "entity": entity,
        "period_start": period_start, "period_end": period_end, "dimensions": dims,
        "unit": unit, "status": INAPPLICABLE, "dimensional": bool(dims),
        "reported_low": None, "reported_high": None,
        "computed_low": None, "computed_high": None,
        "parent_fact_ids": parent_group.fact_ids,
        "child_fact_ids": tuple(sorted(
            fid for g in child_groups.values() if g is not None for fid in g.fact_ids)),
        "missing_children": tuple(sorted(c for c, g in child_groups.items() if g is None)),
    }
    inconsistent = parent_group.state == "inconsistent" or any(
        g is not None and g.state == "inconsistent" for g in child_groups.values())
    if conflicting or inconsistent:
        return result
    computed_low = Decimal(0)
    computed_high = Decimal(0)
    for child, g in child_groups.items():
        if g is None or g.state != "value":
            continue
        assert g.low is not None and g.high is not None
        (weight,) = children[child]
        lo, hi = weight * g.low, weight * g.high
        if weight < 0:
            lo, hi = hi, lo
        computed_low += lo
        computed_high += hi
    reported_low, reported_high = parent_group.low, parent_group.high
    assert reported_low is not None and reported_high is not None
    consistent = max(reported_low, computed_low) <= min(reported_high, computed_high)
    if consistent:
        status = PASS
    elif result["missing_children"]:
        status = PARTIAL
    else:
        status = FAIL
    result.update(status=status, reported_low=reported_low, reported_high=reported_high,
                  computed_low=computed_low, computed_high=computed_high)
    return result


def is_strict_failure(result: Mapping[str, Any]) -> bool:
    """A strict failure: status `fail` in a context without dimensions (Requirement 2)."""
    return result["status"] == FAIL and not result["dimensional"]


def _key_sort(key: tuple) -> tuple:
    return tuple("" if x is None else x for x in key)


def calc_gate_decision(
    accession_no: str,
    results: Iterable[Mapping[str, Any]],
    exceptions: Collection[tuple],
) -> dict[str, Any]:
    """Decide whether one filing's strict calculation failures hold it back.

    Spec: docs/specs/calc-check-c11.md (Requirement 4). Pure: no I/O.

    Args:
        accession_no: The filing the results belong to.
        results: That filing's results from `check_calculations`.
        exceptions: Known strict failures, as (accession_no, link_role, parent,
            period_start, period_end, unit) tuples, for any filing.

    Returns:
        The decision and its counts.
    """
    excepted_keys = {tuple(k) for k in exceptions}
    strict = excepted = partial = dimensional = inapplicable = 0
    new: set[tuple] = set()
    for r in results:
        status = r["status"]
        if status == PARTIAL:
            partial += 1
        elif status == INAPPLICABLE:
            inapplicable += 1
        elif status == FAIL and r["dimensional"]:
            dimensional += 1
        elif status == FAIL:
            strict += 1
            key = (accession_no, r["link_role"], r["parent"], r["period_start"],
                   r["period_end"], r["unit"])
            if key in excepted_keys:
                excepted += 1
            else:
                new.add(key)
    return {
        "accession_no": accession_no,
        "held_back": bool(new),
        "strict_failures": strict,
        "excepted_failures": excepted,
        "new_failures": tuple(sorted(new, key=_key_sort)),
        "partial": partial,
        "dimensional_failures": dimensional,
        "inapplicable": inapplicable,
    }


def load_calc_exceptions(path: str | Path) -> frozenset[tuple]:
    """Read the committed exception list (CSV) into exception keys.

    Spec: docs/specs/calc-check-c11.md (Requirement 3).

    Args:
        path: The CSV file: header `accession_no,link_role,parent,period_start,
            period_end,unit`; an empty `period_start` is an instant (None).

    Returns:
        Its rows as (accession_no, link_role, parent, period_start, period_end, unit).

    Raises:
        ValueError: The header is not exactly the six columns, or a row has the wrong
            number of fields or an empty required field.
    """
    keys: set[tuple] = set()
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or tuple(header) != EXCEPTION_FIELDS:
            raise ValueError(f"{path}: header must be {','.join(EXCEPTION_FIELDS)}, got {header}")
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(EXCEPTION_FIELDS):
                raise ValueError(f"{path}:{line_no}: expected {len(EXCEPTION_FIELDS)} fields")
            acc, role, parent, start, end, unit = row
            if not all((acc, role, parent, end, unit)):
                raise ValueError(f"{path}:{line_no}: only period_start may be empty")
            keys.add((acc, role, parent, start or None, end, unit))
    return frozenset(keys)
