# Grounding precision for v2 cases: a cited fact is the right fact when it resolves, through the
# fact store, to the identity of one of the answer key's inputs or disclosed readings.
# Spec: docs/specs/v2-grounding.md.

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from src.eval.core.case import EvalCase
from src.eval.core.graders.v2 import v2_subkind
from src.eval.core.report import CaseResult
from src.eval.sec_check.base import FactRecord, FactStore
from src.eval.sec_check.core import taxonomy_key
from src.eval.suites.v2_sources import companyfacts_unit
from src.logging_config import get_logger

logger = get_logger(__name__)

_DEFINED_PREFIX = "s4:"
_REL_FLOOR = 1e-9


def _key_inputs(case: EvalCase) -> list[dict[str, Any]]:
    """The key's inputs when the case is v2-grounded (requirement 1), else ``[]``."""
    if case.expected_fact_ids or not isinstance(case.key, dict):
        return []
    inputs = case.key.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        return []
    return [i for i in inputs if isinstance(i, dict)]


def _int_or_none(value: Any) -> int | None:
    """An integer ``decimals`` (or CIK) as an int; None for null, ``"INF"`` or junk."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _start_or_none(value: Any) -> str | None:
    """A period start as written; None for an instant (null or empty)."""
    return str(value) if value else None


def _point_in_time(case: EvalCase) -> bool:
    """Whether the case names the filing (requirement 3's ``bind_accession``)."""
    return "shape:point_in_time" in case.tags or v2_subkind(case) == "point_in_time"


def _readings(case: EvalCase) -> Iterable[Any]:
    """The disclosed readings in requirement 3's order."""
    assert isinstance(case.key, dict)
    yield from case.key.get("alternates") or []
    yield from case.alt_readings or []
    for row in case.expected_rows or []:
        if isinstance(row, dict):
            yield from row.get("alt_readings") or []


def v2_grounding_slots(case: EvalCase) -> list[dict[str, Any]]:
    """The key slots, then the reading slots, of a v2-grounded case (requirement 3).

    Each slot is a dict with the keys ``role``, ``slot``, ``cik``, ``concept``, ``unit``,
    ``period_start``, ``period_end``, ``value``, ``decimals``, ``accession``,
    ``bind_accession`` and ``defined``.

    Args:
        case: Any eval case.

    Returns:
        The slots; ``[]`` for a case that is not v2-grounded.
    """
    inputs = _key_inputs(case)
    if not inputs:
        return []
    bind = _point_in_time(case)
    slots: list[dict[str, Any]] = []
    for k, i in enumerate(inputs):
        cik = _int_or_none(i.get("cik"))
        if cik is None:
            cik = _int_or_none(case.cik)
        concept = str(i.get("concept"))
        slots.append({
            "role": "key", "slot": k, "cik": cik, "concept": concept, "unit": i.get("unit"),
            "period_start": _start_or_none(i.get("period_start")),
            "period_end": i.get("period_end"), "value": float(i["value"]),
            "decimals": _int_or_none(i.get("decimals")), "accession": i.get("accession"),
            "bind_accession": bind, "defined": concept.startswith(_DEFINED_PREFIX),
        })
    key_slots = list(slots)
    for r in _readings(case):
        if not isinstance(r, dict):
            continue
        if r.get("value") is None or not r.get("concept") or not r.get("period_end"):
            continue
        cik = _int_or_none(r.get("cik"))
        if cik is None:
            cik = _int_or_none(case.cik)
        if cik is None:
            cik = key_slots[0]["cik"]
        end = r["period_end"]
        if len(key_slots) == 1:
            serves: int | None = 0
        else:
            serves = next((s["slot"] for s in key_slots
                           if s["cik"] == cik and s["period_end"] == end), None)
        slots.append({
            "role": "reading", "slot": serves, "cik": cik, "concept": str(r["concept"]),
            "unit": None, "period_start": None, "period_end": end, "value": float(r["value"]),
            "decimals": _int_or_none(r.get("decimals")), "accession": r.get("accession"),
            "bind_accession": False, "defined": False,
        })
    return slots


def _normalised(fact: FactRecord) -> dict[str, Any]:
    """A fact record in companyfacts form (requirement 4)."""
    concept = str(fact["concept"])
    namespace, sep, local = concept.rpartition(":")
    taxonomy = taxonomy_key(namespace) if sep else None
    return {
        "cik": _int_or_none(fact["cik"]),
        "concept": f"{taxonomy}:{local}" if taxonomy is not None else concept,
        "unit": companyfacts_unit(str(fact["unit"])),
        "period_start": _start_or_none(fact["period_start"]),
        "period_end": fact["period_end"],
        "value": float(fact["value"]),
        "accession": fact["accession_no"],
        "dimensional": bool(fact["dimensional"]),
    }


def agree(value: float, key: float, decimals: int | None) -> bool:
    """Whether a value agrees with the key's at the key's decimals (requirement 5).

    Args:
        value: The cited fact's value.
        key: The key's value.
        decimals: The key's integer decimals, or None.

    Returns:
        True when ``|value - key|`` is within half a unit of the decimals (or 1e-9 relative).
    """
    half_unit = 0.5 * 10.0 ** (-decimals) if decimals is not None else 0.0
    return abs(value - key) <= max(half_unit, _REL_FLOOR * max(abs(value), abs(key), 1.0))


def _same_frame(fact: dict[str, Any], slot: dict[str, Any]) -> bool:
    """Same filer, unit and period as a key slot (the unit in companyfacts form)."""
    unit = companyfacts_unit(str(slot["unit"])) if slot["unit"] is not None else None
    return (not fact["dimensional"] and fact["cik"] == slot["cik"] and fact["unit"] == unit
            and fact["period_start"] == slot["period_start"]
            and fact["period_end"] == slot["period_end"])


def _matches(fact: dict[str, Any], slot: dict[str, Any]) -> bool:
    """Whether a normalised fact matches an ordinary key slot or a reading slot (rule 6)."""
    if fact["dimensional"] or fact["cik"] != slot["cik"]:
        return False
    if fact["concept"] != slot["concept"] or fact["period_end"] != slot["period_end"]:
        return False
    if slot["role"] == "key":
        if not _same_frame(fact, slot):
            return False
        if slot["bind_accession"] and fact["accession"] != slot["accession"]:
            return False
    return agree(fact["value"], slot["value"], slot["decimals"])


def _distinct(fact_ids: Iterable[Any]) -> list[int]:
    """The distinct cited ids, first occurrence first."""
    seen: dict[int, None] = {}
    for f in fact_ids:
        seen.setdefault(int(f), None)
    return list(seen)


def grade_v2_grounding(
    case: EvalCase, fact_ids: Sequence[int], facts: Mapping[int, FactRecord]
) -> dict[str, Any] | None:
    """Grades which cited facts are the key's facts (requirements 4 to 10).

    Args:
        case: The case.
        fact_ids: The answer's cited fact ids, in order.
        facts: The fact store's records of (some of) those ids.

    Returns:
        ``{"precision", "recall", "right", "wrong", "unresolved", "evidenced"}`` (the last
        four tuples of ints); ``None`` for a case that is not v2-grounded.
    """
    slots = v2_grounding_slots(case)
    if not slots:
        return None
    key_slots = [s for s in slots if s["role"] == "key"]
    plain = [s for s in slots if not s["defined"]]  # ordinary key slots and readings
    cited = _distinct(fact_ids)
    resolved = {f: _normalised(facts[f]) for f in cited if f in facts}

    right: set[int] = set()
    evidenced: set[int] = set()
    claimed: set[int] = set()
    for f, fact in resolved.items():
        for s in plain:
            if _matches(fact, s):
                right.add(f)
                claimed.add(f)
                if s["slot"] is not None:
                    evidenced.add(s["slot"])
    for s in key_slots:
        if not s["defined"]:
            continue
        frame = [f for f, fact in resolved.items()
                 if f not in claimed and _same_frame(fact, s)]
        through = [f for f in frame if agree(resolved[f]["value"], s["value"], s["decimals"])]
        if not through and frame and agree(sum(resolved[f]["value"] for f in frame),
                                           s["value"], s["decimals"]):
            through = frame
        if through:
            right.update(through)
            evidenced.add(s["slot"])

    return {
        "precision": (sum(1 for f in cited if f in right) / len(cited)) if cited else None,
        "recall": len(evidenced) / len(key_slots),
        "right": tuple(f for f in cited if f in right),
        "wrong": tuple(f for f in cited if f not in right),
        "unresolved": tuple(f for f in cited if f not in facts),
        "evidenced": tuple(sorted(evidenced)),
    }


def _cited(result: CaseResult) -> list[int]:
    """The fact ids a graded result's ``produced`` carries (missing reads as ``[]``)."""
    produced = result.grade.produced if isinstance(result.grade.produced, dict) else {}
    return _distinct(produced.get("fact_ids") or [])


def attach_v2_grounding(results: Sequence[CaseResult], store: FactStore) -> int:
    """Sets the grounding fields of every v2-grounded result (requirement 12).

    Args:
        results: Graded results, live or rebuilt from stored payloads.
        store: Where cited fact ids are resolved.

    Returns:
        The number of v2-grounded results.
    """
    return _attach(results, store)["n_cases"]


def _attach(results: Sequence[CaseResult], store: FactStore) -> dict[str, int]:
    """``attach_v2_grounding``, returning the counts the CLIs log and record."""
    grounded = [r for r in results if _key_inputs(r.case)]
    wanted = sorted({f for r in grounded for f in _cited(r)})
    # Read every record before setting any field, so a store failure leaves all unchanged.
    facts = {int(k): v for k, v in store.facts(wanted).items()} if wanted else {}
    unresolved = 0
    citations = 0
    for r in grounded:
        cited = _cited(r)
        g = grade_v2_grounding(r.case, cited, facts)
        assert g is not None  # grounded cases are v2-grounded by construction
        r.grade.grounding_precision = g["precision"]
        r.grade.grounding_recall = g["recall"]
        unresolved += len(g["unresolved"])
        citations += len(cited)
    if grounded:
        logger.info("v2 grounding: %d cases, %d citations, %d unresolved fact ids",
                    len(grounded), citations, unresolved)
    return {"n_cases": len(grounded), "n_citations": citations, "n_unresolved": unresolved}


def ground_results(
    results: Sequence[CaseResult],
    store: Callable[[], FactStore],
    *,
    run_fingerprint: str | None = None,
    store_fingerprint: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Attaches v2 grounding for a run, live or re-graded (requirement 14).

    Nothing is set, and the status says why, when no result is v2-grounded, when
    ``store_fingerprint`` is given and differs from ``run_fingerprint`` (fact ids are store
    row ids, so a run on another corpus state cannot be resolved), or when the store cannot
    be read. A run graded without a store keeps ``None``; it never gets ``0.0``.

    Args:
        results: The graded results.
        store: Builds the fact store; called only when there is something to resolve.
        run_fingerprint: The corpus fingerprint the run's answers were produced on.
        store_fingerprint: Reads the store's current fingerprint; no check when None.

    Returns:
        ``{"status": ...}`` with ``attached`` (plus the counts of ``_attach`` and the
        store's fingerprint when checked), ``not_applicable``, ``fingerprint_mismatch`` or
        ``store_unavailable`` (plus a ``reason``).
    """
    if not any(_key_inputs(r.case) for r in results):
        return {"status": "not_applicable"}
    checked: dict[str, Any] = {}
    if store_fingerprint is not None:
        current = store_fingerprint()
        checked = {"store_fingerprint": current}
        if current != run_fingerprint:
            reason = (f"run corpus_fingerprint {run_fingerprint!r} is not the fact store's "
                      f"{current!r}; cited fact ids cannot be resolved")
            logger.warning("v2 grounding not attached: %s", reason)
            return {"status": "fingerprint_mismatch", "reason": reason, **checked}
    fact_store = None
    try:
        fact_store = store()
        counts = _attach(results, fact_store)
    except Exception as exc:  # isolation point: a run without a store keeps None
        logger.warning("v2 grounding not attached: fact store unavailable: %s", exc)
        return {"status": "store_unavailable", "reason": f"{type(exc).__name__}: {exc}",
                **checked}
    finally:
        close = getattr(fact_store, "close", None)
        if callable(close):
            close()
    return {"status": "attached", **counts, **checked}
