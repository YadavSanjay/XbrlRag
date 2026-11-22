"""Reference rules for the dimensional views' geography codes and shares of total.

docs/specs/segment-geo-views.md. These pure functions state what
`scripts/ddl/mv/32_mv_geographic_facts.sql` (`geo_code`, `geo_name`, `geo_type`,
`pct_of_total`) and `scripts/ddl/mv/31_mv_segment_matrix.sql` (`pct_of_axis_total`) compute;
the views mirror them in SQL and `test/integration/test_segment_geo_views_live.py` checks the
two agree on real filings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

# Namespace prefix -> the views' QName prefix; the rule `mv_financial_facts` applies to
# `segment_axis` / `segment_member` (scripts/ddl/mv/10_mv_financial_facts.sql). Any other
# namespace stays as written.
_NAMESPACE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("http://fasb.org/us-gaap/", "us-gaap"),
    ("http://fasb.org/srt/", "srt"),
    ("http://xbrl.sec.gov/dei/", "dei"),
    ("http://xbrl.sec.gov/ecd/", "ecd"),
    ("http://xbrl.sec.gov/country/", "country"),
    ("http://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
    ("https://xbrl.ifrs.org/taxonomy/", "ifrs-full"),
)

_GEO_FIELDS = ("iso_code", "geo_name", "geo_type")


def is_consolidated(dimensions: Iterable[Mapping[str, Any]]) -> bool:
    """Return True when a context carries no non-default dimension.

    Args:
        dimensions: The context's dimension entries, each with `axis`, `member` (None for a
            typed dimension) and `is_default`.

    Returns:
        True for a context with only default entries (or none); False when any entry is an
        explicit non-default member or a typed dimension.
    """
    # A typed dimension has no default member, so it is a part even if marked default.
    return all(d.get("is_default") is True and d.get("member") is not None for d in dimensions)


def view_qname(qname: str) -> str:
    """Return a `namespace:local` QName (split at the last `:`) in the views' QName form.

    Args:
        qname: A URI-form QName such as `http://xbrl.sec.gov/country/2024:CN`.

    Returns:
        `country:CN` for a standard namespace; the input unchanged for any other namespace or
        a string without a `:`.
    """
    namespace, sep, local = qname.rpartition(":")
    if not sep:
        return qname
    for uri_prefix, prefix in _NAMESPACE_PREFIXES:
        if namespace.startswith(uri_prefix):
            return f"{prefix}:{local}"
    return qname


def _blank(value: Any) -> bool:
    return value is None or value == ""


def resolve_geography(
    segment_member: str,
    segment_label: str | None,
    normalization: Iterable[Mapping[str, Any]],
) -> dict[str, str | None]:
    """Return a geographic member's code, name and type from `geography_normalization` rows.

    Args:
        segment_member: The member in the views' QName form (`country:CN`, or `uri:local` for
            an extension member).
        segment_label: The member's label, the name used when no row names it.
        normalization: Rows with `member_qname` (URI form), `iso_code`, `geo_name`, `geo_type`.

    Returns:
        A dict with exactly `geo_code`, `geo_name` and `geo_type`.
    """
    matches = {
        tuple(row.get(k) for k in _GEO_FIELDS)
        for row in normalization
        if row.get("member_qname") is not None
        and view_qname(row["member_qname"]) == segment_member
    }
    if len(matches) != 1:
        # No row, or rows that disagree: unmapped, never a code derived from the QName text.
        return {"geo_code": None, "geo_name": segment_label, "geo_type": None}
    iso_code, geo_name, geo_type = next(iter(matches))
    return {
        "geo_code": None if _blank(iso_code) else iso_code,
        "geo_name": segment_label if _blank(geo_name) else geo_name,
        "geo_type": geo_type,
    }


def _total_key(fact: Mapping[str, Any]) -> tuple[Any, ...]:
    return (fact["accession_no"], fact["concept"], fact["unit"], fact["period_start"],
            fact["period_end"])


def _precision(decimals: int | None) -> float:
    # decimals None is INF, the highest precision.
    return float("inf") if decimals is None else float(decimals)


def shares_of_total(facts: Iterable[Mapping[str, Any]]) -> dict[int, float | None]:
    """Return each non-consolidated fact's share of its consolidated total.

    Args:
        facts: Facts with `fact_id`, `accession_no`, `concept`, `unit`, `period_start`,
            `period_end`, `value` (Decimal or None), `decimals` (int or None for INF) and
            `dimensions`.

    Returns:
        fact_id -> value / total for every non-consolidated fact; None when there is no total,
        the total is zero or the fact is nil.
    """
    parts: list[Mapping[str, Any]] = []
    candidates: dict[tuple[Any, ...], list[tuple[float, Decimal]]] = {}
    for fact in facts:
        if not is_consolidated(fact["dimensions"]):
            parts.append(fact)
        elif fact["value"] is not None:
            candidates.setdefault(_total_key(fact), []).append(
                (_precision(fact["decimals"]), Decimal(fact["value"])))

    totals: dict[tuple[Any, ...], Decimal | None] = {}
    for key, rows in candidates.items():
        best = max(p for p, _ in rows)
        values = {v for p, v in rows if p == best}
        totals[key] = next(iter(values)) if len(values) == 1 else None

    shares: dict[int, float | None] = {}
    for fact in parts:
        total = totals.get(_total_key(fact))
        value = fact["value"]
        if value is None or total is None or total == 0:
            shares[fact["fact_id"]] = None
        else:
            shares[fact["fact_id"]] = float(Decimal(value) / total)
    return shares
