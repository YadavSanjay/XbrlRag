"""Concept-family membership, roles and the candidate order of a lookup.

See docs/specs/concept-family-coverage.md: which elements answer a family for a filer
(same measure, stated substitute, never a different metric), which candidate fact answers,
and how a per-share unit is shown.

``family_member`` reads the committed mapping (``committed_mapping_rows`` of
``src/ingest/util/generate_concept_mapping.py``: the declared families, the reviewed
override file and ``configs/concept_mapping/concept_family_coverage.yaml``), the same rows
the generator writes for the database load, so the two cannot drift apart. The data-driven
``auto_override`` rows of layer-0 are not part of it.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.ingest.util.generate_concept_mapping import (
    ROLE_COMPONENT,
    ROLE_PRIMARY,
    ROLE_SUBSTITUTE,
    committed_mapping_rows,
    default_role,
    load_family_coverage,
)
from src.numerics.stated_numbers import display_unit

__all__ = [
    "ROLE_COMPONENT",
    "ROLE_PRIMARY",
    "ROLE_SUBSTITUTE",
    "candidate_order",
    "display_unit",
    "family_member",
    "family_parts",
    "measure_column",
    "row_role",
    "same_measure",
]

# Standard taxonomies by namespace: the version or date in the URI never matters.
_STANDARD_NAMESPACES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"http://fasb\.org/us-gaap/[^/]+"), "us-gaap"),
    (re.compile(r"http://fasb\.org/srt/[^/]+"), "srt"),
    (re.compile(r"http://xbrl\.sec\.gov/dei/[^/]+"), "dei"),
    (re.compile(r"https?://xbrl\.ifrs\.org/taxonomy/[^/]+/ifrs-full"), "ifrs-full"),
)
# Other SEC taxonomies: standard, but never a family member and never an extension.
_OTHER_STANDARD_NAMESPACE = re.compile(r"http://xbrl\.sec\.gov/(?:country|exch|ecd|stpr)/")
_PREFIXES = frozenset({"us-gaap", "srt", "dei", "ifrs-full", "ext"})
_EXTENSION_PREFIX = "ext"

_COMPONENT_BAND = range(50, 100)


def _short_form(concept: str) -> str | None:
    """``<prefix>:<LocalName>`` for a store-form or prefix-form concept, else None."""
    namespace, sep, local = concept.rpartition(":")
    if not sep or not namespace or not local:
        return None
    if namespace in _PREFIXES:
        return concept
    for pattern, prefix in _STANDARD_NAMESPACES:
        if pattern.fullmatch(namespace):
            return f"{prefix}:{local}"
    if _OTHER_STANDARD_NAMESPACE.match(namespace):
        return None
    return f"{_EXTENSION_PREFIX}:{local}"


@functools.lru_cache(maxsize=1)
def _mapping() -> dict[tuple[str, str, str], dict[str, Any]]:
    """The committed rows keyed by ``(family, short-form qname, cik or "")``."""
    return {
        (r["concept_family"], r["qname"], r["cik_override"] or ""): r
        for r in committed_mapping_rows()
    }


def family_member(family: str, cik: str, concept: str) -> dict[str, Any] | None:
    """The committed mapping's entry for a concept in a family, for one filer.

    A filer's cik-scoped row wins over the generic row of the same QName, as in the
    view's join. An extension is a member only through a cik-scoped row.

    Args:
        family: The concept family, e.g. ``"InterestExpense"``.
        cik: The filer's 10-digit, zero-padded CIK.
        concept: ``<namespace>:<LocalName>`` as the fact store holds it, or a prefix form
            (``us-gaap:``, ``srt:``, ``dei:``, ``ifrs-full:``, ``ext:``).

    Returns:
        ``{"rank", "role", "substitution"}``, or None when the concept is not a member.
    """
    qname = _short_form(concept)
    if qname is None:
        return None
    rows = _mapping()
    row = rows.get((family, qname, cik))
    if row is None and not qname.startswith(_EXTENSION_PREFIX + ":"):
        row = rows.get((family, qname, ""))
    if row is None:
        return None
    role = row.get("concept_role") or default_role(row["preference_rank"])
    substitution = None
    if role == ROLE_SUBSTITUTE:
        substitution = {"code": row["substitution_code"], "note": row["substitution_note"]}
    return {"rank": row["preference_rank"], "role": role, "substitution": substitution}


@functools.lru_cache(maxsize=1)
def _coverage() -> dict[str, Any]:
    return load_family_coverage()


def same_measure(family: str, cik: str, concept: str) -> bool:
    """Whether a member is the family's own measure for the filer, at any rank.

    True for a primary at rank 1, a primary the coverage file lists under
    ``same_measure`` (the decided same-measure elements), or a cik-scoped primary of
    the filer. A near-miss primary (a current part, a total including noncontrolling
    interests) is not, and neither is a substitute or a component.

    Args:
        family: The concept family.
        cik: The filer's 10-digit CIK.
        concept: The concept, store or prefix form.

    Returns:
        True when an answer with the concept earns the preferred-concept credit.
    """
    member = family_member(family, cik, concept)
    if member is None or member["role"] != ROLE_PRIMARY:
        return False
    if member["rank"] == 1:
        return True
    qname = _short_form(concept)
    coverage = _coverage()
    if qname in coverage["same_measure"].get(family, ()):
        return True
    return any(
        e["cik"] == cik and e["family"] == family and e["concept"] == qname
        and e["role"] == ROLE_PRIMARY
        for e in coverage["filers"]
    )


def family_parts(family: str) -> tuple[str, ...]:
    """The families a family is the sum of, from the coverage file's ``parts``.

    A lookup never adds them: for a filer that reports the parts and no total, it
    refuses and names each part (docs/design/XbrlRag_Concept_Decisions.md, D45).

    Args:
        family: The concept family, e.g. ``"SellingGeneralAndAdministrative"``.

    Returns:
        The part families in their declared order; empty when none are declared.
    """
    return tuple(_coverage()["parts"].get(family, ()))


def row_role(row: Mapping[str, Any]) -> str:
    """A candidate row's role: ``concept_role``, or the one its rank implies."""
    role = row.get("concept_role")
    if role:
        return str(role)
    rank = row.get("concept_pref_rank")
    return ROLE_COMPONENT if rank is not None and rank in _COMPONENT_BAND else ROLE_PRIMARY


def _ordinal(value: Any) -> int | None:
    """A ``period_end`` (ISO string or date) as a day ordinal; None when missing."""
    if value is None:
        return None
    if isinstance(value, str):
        return dt.date.fromisoformat(value[:10]).toordinal()
    return int(value.toordinal())


def _decimals(value: Any) -> int | None:
    """None for an exact fact (no attribute or ``INF``), else the int."""
    if value is None or (isinstance(value, str) and value.upper() == "INF"):
        return None
    return int(value)


def _order_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """R2's keys in turn: role, later period, presented, rank, valued, precision, fact id."""
    end = _ordinal(row.get("period_end"))
    rank = row.get("concept_pref_rank")
    decimals = _decimals(row.get("decimals"))
    return (
        0 if row_role(row) == ROLE_PRIMARY else 1,
        (1, 0) if end is None else (0, -end),
        0 if row.get("presented") else 1,
        (1, 0) if rank is None else (0, rank),
        1 if row.get("value") is None else 0,
        (0, 0) if decimals is None else (1, -decimals),
        -(row.get("fact_id") or 0),
    )


def candidate_order(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    """The rows that may answer a lookup, best first.

    Component rows are left out. The rest are ordered by role (primary before
    substitute), later ``period_end``, presented before notes-only, lower rank, valued
    before nil, finer ``decimals`` (exact first), then larger ``fact_id``.

    Args:
        rows: Candidate fact rows as the lookup's SQL returns them.

    Returns:
        Indices into ``rows``; component rows are left out.
    """
    answerable = [i for i, r in enumerate(rows) if row_role(r) != ROLE_COMPONENT]
    return sorted(answerable, key=lambda i: _order_key(rows[i]))



def measure_column(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """The row whose filing and date a defined (S4) measure is built from.

    See docs/specs/s4-screen-defined-members.md, requirement 1. The first row
    ``candidate_order`` keeps; when every row is a component, the row with the latest
    ``period_end``, then the lowest ``concept_pref_rank``, then the larger ``fact_id``
    (a missing date or rank last), the order the lookup's query returns its rows in.

    Args:
        rows: Candidate fact rows of one filer, family and year.

    Returns:
        An index into ``rows``, or None when ``rows`` is empty.
    """
    if not rows:
        return None
    order = candidate_order(rows)
    if order:
        return order[0]

    def _component_key(i: int) -> tuple[Any, ...]:
        end = _ordinal(rows[i].get("period_end"))
        rank = rows[i].get("concept_pref_rank")
        return (
            (1, 0) if end is None else (0, -end),
            (1, 0) if rank is None else (0, rank),
            -(rows[i].get("fact_id") or 0),
        )

    return min(range(len(rows)), key=_component_key)
