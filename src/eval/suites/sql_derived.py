"""WS-9 / E2 — generate a labelled suite from the analyst-SQL template spec.

``scripts/xbrl/analyst_queries.sql`` is authoritative, human-validated SQL: analyst
question → canonical query, "validated against 60 filings". Running it *is* the
ground truth. Fanning its parameterised form (``configs/eval/sql_derived_templates.yaml``)
across the corpus turns 29 reference queries into hundreds of labelled cases with
**zero human labelling** — the leverage no external benchmark can match, because
their correct answer lives in a PDF while ours lives in a queryable store.

Four properties make the output trustworthy:

* **Skip-on-empty** — a parameter combination with no rows is dropped, so the suite
  auto-sizes to whatever the corpus actually holds. No hand-maintained exclusions.
* **Skip-on-ambiguity** — ``require_unique`` templates emit nothing when the SQL
  resolves more than one row. Ambiguous ground truth is worse than no ground truth
  (design doc §15).
* **Fact-id labelling** — every case records the exact ``fact_id``(s) its value came
  from, enabling grounding precision. A case whose fact ids come out empty is
  dropped rather than shipped unlabelled.
* **Paraphrase groups** — the same ground truth phrased several ways, so routing
  non-determinism becomes a measurable metric instead of a rumour.

Values are the **raw store values**. ``analyst_queries.sql`` divides by 1e9 for
human display; a ``lookup`` step resolves the unscaled fact, so no display scaling
is applied here.
"""

from __future__ import annotations

import collections.abc
import datetime
import hashlib
import itertools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.eval.core.case import KIND_BREAKDOWN, MULTI_ROW_KINDS, EvalCase, EvalSuite
from src.eval.core.graders.numeric import close_enough
from src.logging_config import get_logger
from src.numerics.restatement import (
    breakdown_filings,
    current_column,
    latest_dimensional_restatement,
    latest_restatement,
)
from src.retriever.xbrl.family_resolution import ROLE_COMPONENT, ROLE_PRIMARY, row_role
from src.retriever.xbrl.xbrl_factstore_breakdown import breakdown_total, choose_breakdown
from src.retriever.xbrl.xbrl_factstore_lookup import most_precise_consistent

logger = get_logger(__name__)

SUITE_NAME = "sql_derived"
DEFAULT_SPEC_PATH = "configs/eval/sql_derived_templates.yaml"

# XBRL member labels carry a "[Member]" marker. It appears mid-string too once
# several dimension labels are aggregated ("Commercial Paper [Member] and Level 2"),
# so strip every occurrence, not just a trailing one.
_MEMBER_MARKER = re.compile(r"\s*\[Member\]")

# Which hierarchy level a breakdown is graded at (see collapse_hierarchy).
LEVEL_REPORTED = "reported"  # keep the parent: the level the filer reports
LEVEL_LEAF = "leaf"  # keep the children

# Facts equivalent to a given fact: same filing, concept, period, dimension and value.
# `IS NOT DISTINCT FROM` (not `=`) so NULL dimensions/periods match each other.
_EQUIVALENCE_SQL = """
    SELECT f1.fact_id AS anchor,
           array_agg(f2.fact_id ORDER BY f2.fact_id) AS members
    FROM   mv_financial_facts f1
    JOIN   mv_financial_facts f2
             ON  f2.cik            = f1.cik
            AND  f2.accession_no   = f1.accession_no
            AND  f2.concept        = f1.concept
            AND  f2.value          IS NOT DISTINCT FROM f1.value
            AND  f2.period_start   IS NOT DISTINCT FROM f1.period_start
            AND  f2.period_end     IS NOT DISTINCT FROM f1.period_end
            AND  f2.segment_member IS NOT DISTINCT FROM f1.segment_member
            AND  f2.unit           IS NOT DISTINCT FROM f1.unit
    WHERE  f1.fact_id = ANY(%(ids)s)
    GROUP  BY f1.fact_id
"""

# Every fact sharing a fact's filing, concept, period, member and unit, whatever its
# value, with its XBRL precision (NULL = exact): the candidates for a rounded duplicate.
_PRECISION_SQL = """
    SELECT DISTINCT f2.fact_id, f2.value,
           CASE WHEN x.is_decimals_infinity THEN NULL ELSE x.decimals_value::int END
               AS decimals
    FROM   mv_financial_facts f1
    JOIN   mv_financial_facts f2
             ON  f2.cik            = f1.cik
            AND  f2.accession_no   = f1.accession_no
            AND  f2.concept        = f1.concept
            AND  f2.period_start   IS NOT DISTINCT FROM f1.period_start
            AND  f2.period_end     IS NOT DISTINCT FROM f1.period_end
            AND  f2.segment_member IS NOT DISTINCT FROM f1.segment_member
            AND  f2.unit           IS NOT DISTINCT FROM f1.unit
    JOIN   fact x ON x.fact_id = f2.fact_id
    WHERE  f1.fact_id = %(id)s
    ORDER  BY f2.fact_id
"""

# A row's role as ``family_resolution.row_role`` reads it: ``concept_role``, else the one
# its rank implies (50-99 is the component band).
_ROLE_SQL = (
    "COALESCE(NULLIF({a}.concept_role, ''),"
    " CASE WHEN {a}.concept_pref_rank BETWEEN 50 AND 99"
    " THEN 'component' ELSE 'primary' END)"
)

# The lookup's candidate order (``family_resolution.candidate_order``), key by key, with
# the reporting-currency tie-break right after the period (docs/specs/unit-aware-lookup.md):
# a tie at the latest period goes to the unit the filing also tags for earlier periods,
# not a 20-F convenience translation. One definition, so the single-fact templates and
# the lookup cannot drift apart again (docs/specs/concept-family-coverage.md R2).
LOOKUP_ORDER_KEYS: tuple[tuple[str, str], ...] = (
    ("role", "CASE WHEN " + _ROLE_SQL + " = 'primary' THEN 0 ELSE 1 END"),
    ("period", "{a}.period_end DESC NULLS LAST"),
    (
        "reporting_currency",
        "NOT EXISTS ("
        " SELECT 1 FROM mv_financial_facts p"
        " WHERE p.cik = {a}.cik AND p.fiscal_year = {a}.fiscal_year"
        " AND p.concept_family = {a}.concept_family AND p.unit = {a}.unit"
        " AND p.segment_axis IS NULL AND p.period_end < {a}.period_end)",
    ),
    ("presented", "NOT COALESCE({a}.presented, FALSE)"),
    ("rank", "{a}.concept_pref_rank NULLS LAST"),
    ("valued", "{a}.value IS NULL"),
    (
        "precision",
        "(SELECT CASE WHEN x.is_decimals_infinity THEN NULL ELSE x.decimals_value::int END"
        " FROM fact x WHERE x.fact_id = {a}.fact_id) DESC NULLS FIRST",
    ),
    ("fact_id", "{a}.fact_id DESC"),
)

# Template SQL macros, expanded when a template is parsed. Unexpanded they are invalid
# SQL, so a misspelt macro fails the template loudly instead of dropping the order.
_MACRO = re.compile(r"@(LOOKUP_ORDER|ANSWERABLE)\((\w+)\)")


def lookup_order_sql(alias: str) -> str:
    """The ORDER BY terms that pick the fact a lookup answers with.

    Args:
        alias: The ``mv_financial_facts`` alias the terms refer to.

    Returns:
        Comma-separated ORDER BY terms, best candidate first.
    """
    return ",\n             ".join(term.format(a=alias) for _, term in LOOKUP_ORDER_KEYS)


def answerable_sql(alias: str) -> str:
    """A predicate keeping the rows that may answer: a component never does.

    Args:
        alias: The ``mv_financial_facts`` alias the predicate refers to.

    Returns:
        A boolean SQL expression.
    """
    return _ROLE_SQL.format(a=alias) + " <> 'component'"


def expand_sql_macros(sql: str) -> str:
    """Expand ``@LOOKUP_ORDER(alias)`` and ``@ANSWERABLE(alias)`` in template SQL.

    Args:
        sql: A template's SQL.

    Returns:
        The SQL with every macro replaced.
    """

    def _sub(match: re.Match[str]) -> str:
        name, alias = match.group(1), match.group(2)
        return lookup_order_sql(alias) if name == "LOOKUP_ORDER" else answerable_sql(alias)

    return _MACRO.sub(_sub, sql)


def s1_lookup_pick(
    rows: Sequence[collections.abc.Mapping[str, Any]], fy: int
) -> dict | None:
    """The row a v1 single-fact lookup case keys for fiscal year ``fy`` (rule S1).

    docs/specs/suite-templates-s1.md, requirement 1: the own column of the filing labelled
    ``fy`` as the latest later-labelled canonical filing restates it, components and nil
    rows left out, in the v1 lookup order with the fy filing's reporting currency first.

    Args:
        rows: ``mv_financial_facts`` rows of one filer's measure, from every filing.
        fy: The fiscal year asked for.

    Returns:
        The picked row as a dict, or None when the year has no own column.
    """
    flat = [r for r in rows if r.get("segment_axis") is None]
    candidates = [
        r
        for r in latest_restatement(current_column(flat, fy, "FY"), flat)
        if r.get("value") is not None and row_role(r) != ROLE_COMPONENT
    ]
    if not candidates:
        return None
    currencies = _reporting_currencies(flat, fy)

    def _key(row: dict) -> tuple[Any, ...]:
        end = _iso_day(row.get("period_end"))
        rank = row.get("concept_pref_rank")
        decimals = row.get("decimals")
        return (
            0 if row_role(row) == ROLE_PRIMARY else 1,
            (1, 0) if end is None else (0, -end.toordinal()),
            0 if (row.get("concept_family"), row.get("unit")) in currencies else 1,
            0 if row.get("presented") else 1,
            (1, 0) if rank is None else (0, rank),
            1 if row.get("value") is None else 0,
            (0, 0) if decimals is None else (1, -int(decimals)),
            -int(row.get("fact_id") or 0),
        )

    return min(candidates, key=_key)


def s1_yoy_pick(
    rows: Sequence[collections.abc.Mapping[str, Any]], fy: int
) -> dict | None:
    """The year-over-year growth a v1 YoY case keys for fiscal year ``fy`` (rule S1).

    docs/specs/yoy-growth-s1.md, requirement 1: ``s1_lookup_pick`` of ``fy`` and of
    ``fy - 1``, as the product's two lookups answer them, and their growth.

    Args:
        rows: ``mv_financial_facts`` rows of one filer's measure, from every filing.
        fy: The fiscal year of the current side.

    Returns:
        ``value``, ``fy_prior``, ``fact_id_current``, ``fact_id_prior``, ``unit``,
        ``not_meaningful``, ``current`` and ``prior``; None when there is no growth to key.
    """
    current = s1_lookup_pick(rows, fy)
    prior = s1_lookup_pick(rows, fy - 1)
    if current is None or prior is None:
        return None
    if current.get("unit") != prior.get("unit"):
        # The compute refuses two currencies (docs/specs/units-currency-integrity.md).
        return None
    base = prior["value"]
    if base == 0:
        return None
    return {
        "value": (current["value"] - base) / abs(base),
        "fy_prior": fy - 1,
        "fact_id_current": current.get("fact_id"),
        "fact_id_prior": prior.get("fact_id"),
        "unit": current.get("unit"),
        # A change from a base <= 0 is "n/m" (docs/specs/pct-change-nm.md).
        "not_meaningful": base <= 0,
        "current": dict(current),
        "prior": dict(prior),
    }


def _iso_day(value: Any) -> datetime.date | None:
    """A date, datetime or ISO string as a ``date``; ``None`` stays ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _reporting_currencies(
    rows: Sequence[collections.abc.Mapping[str, Any]], fy: int
) -> set[Any]:
    """The units the canonical filing labelled ``fy``/``FY`` also tags before its own end.

    A unit with an earlier-period row of the same family in the own filing is a reporting
    currency; a 20-F's USD convenience translation of the current year only is not
    (docs/specs/suite-templates-s1.md, requirement 1).

    Args:
        rows: Rows of one filer's measure without a segment.
        fy: The fiscal year asked for.

    Returns:
        The reporting ``(concept_family, unit)`` pairs.
    """
    own = [
        r
        for r in rows
        if r.get("fiscal_year") == fy
        and r.get("fiscal_period") == "FY"
        and r.get("is_canonical_filing")
    ]
    out: set[Any] = set()
    for r in own:
        dpe, end = _iso_day(r.get("document_period_end")), _iso_day(r.get("period_end"))
        if dpe is not None and end is not None and end < dpe + datetime.timedelta(days=1):
            out.add((r.get("concept_family"), r.get("unit")))
    return out


def s1_dimensional_pick(
    rows: Sequence[collections.abc.Mapping[str, Any]], fy: int
) -> list[dict]:
    """The rows a v1 dimensional lookup case keys for fiscal year ``fy`` (rule S1).

    docs/specs/dimensional-s1.md, requirements 2-3: the rows of the year's own column that
    the template's SQL marks ``matches`` (the member or member set asked for), each as the
    latest later-labelled canonical filing with the same dimensions restates it.

    Args:
        rows: One filer's rows of the measure from every filing (flat single-dimension or
            multidim rows), with the S1 columns, ``dimensions`` for multidim rows and a
            boolean ``matches``.
        fy: The fiscal year asked for.

    Returns:
        The answering rows (one per matched fact); empty when the year has no own column.
    """
    asked = [r for r in current_column(rows, fy, "FY") if r.get("matches")]
    return latest_dimensional_restatement(asked, rows)


# The template key ``pick`` names one of these: the row a single-fact case keys, or for
# ``s1_breakdown`` the filing a breakdown case keys (docs/specs/dimensional-s1.md R5).
PICK_S1_LOOKUP = "s1_lookup"
PICK_S1_YOY = "s1_yoy"
PICK_S1_DIMENSIONAL = "s1_dimensional"
PICK_S1_BREAKDOWN = "s1_breakdown"
PICKS = (PICK_S1_LOOKUP, PICK_S1_YOY, PICK_S1_DIMENSIONAL, PICK_S1_BREAKDOWN)
# The filer's rows of a breakdown's measure from every filing, totals and members, with
# the columns `breakdown_filings` and `breakdown_total` read.
_BREAKDOWN_FILER_ROWS_SQL = """
    SELECT f.fact_id, f.cik, f.accession_no, f.fiscal_year, f.fiscal_period, f.filed_date,
           f.is_amendment, f.is_canonical_filing, f.document_period_end, f.concept,
           f.concept_pref_rank, f.value, f.unit, f.period_start, f.period_end,
           f.segment_axis, f.segment_member, f.has_multiple_dimensions
    FROM   mv_financial_facts f
    WHERE  f.ticker = %(ticker)s AND f.concept_family = %(concept_family)s
      AND  f.period_type = 'duration' AND f.duration_kind = 'annual'
"""
# The keys of `s1_yoy_pick` an ``s1_yoy`` template's ``expect`` names as columns.
_YOY_PICK_COLUMNS = (
    "value", "fy_prior", "fact_id_current", "fact_id_prior", "unit", "not_meaningful"
)
# A predicate on the year or period label, which a pick template's SQL must not have.
_YEAR_PREDICATE = re.compile(
    r"\bfiscal_(?:year|period)\s*(?:=|<>|!=|<|>|\bIN\b|\bBETWEEN\b|\bIS\b)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------


@dataclass
class ParamSpec:
    """One template parameter: a fixed list, a corpus reference, or a SQL enumeration."""

    name: str
    values: list[Any] | None = None
    labels: dict[Any, str] = field(default_factory=dict)
    from_sql: str | None = None
    corpus_ref: str | None = None

    @classmethod
    def parse(cls, name: str, raw: Any) -> ParamSpec:
        if isinstance(raw, str) and raw.startswith("corpus."):
            return cls(name=name, corpus_ref=raw.split(".", 1)[1])
        if isinstance(raw, list):
            return cls(name=name, values=list(raw))
        if isinstance(raw, dict):
            if raw.get("from_sql"):
                return cls(name=name, from_sql=raw["from_sql"])
            return cls(
                name=name,
                values=list(raw.get("values", [])),
                labels=dict(raw.get("labels", {})),
            )
        raise ValueError(f"param {name!r}: unsupported spec {raw!r}")

    @property
    def is_dynamic(self) -> bool:
        return self.from_sql is not None


@dataclass
class Template:
    id: str
    source: str
    kind: str
    sql: str
    nl: list[str]
    params: dict[str, ParamSpec] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    rel_tol: float = 1e-4
    alt_scale: list[float] = field(default_factory=list)
    top_n: int | None = None
    min_rows: int = 1
    order_sensitive: bool = True
    require_unique: bool = False
    require_citation: bool = True
    post_sort: dict[str, str] | None = None
    ticker: str | None = None  # templates pinned to one filer (e.g. TSM)
    concept: str | None = None  # templates pinned to one concept (e.g. 1a Revenue)
    # Breakdowns only: collapse mixed hierarchy levels to ONE level so the case has a
    # single correct answer. "reported" keeps the parent (the level the filer reports
    # and the pipeline answers at); "leaf" keeps the children. See collapse_hierarchy.
    hierarchy_level: str = LEVEL_REPORTED
    # A scalar case whose expected value is exactly 0 is close to unfalsifiable:
    # close_enough() matches a zero target with an absolute band, so almost any
    # answer passes. Such a case inflates accuracy without testing anything.
    allow_zero: bool = False
    # Per-(ticker, fiscal year) quota overriding the tier's max_cases_per_stratum.
    max_per_stratum: int | None = None
    # A Python pick over the SQL's rows (PICKS): the SQL returns the measure's rows from
    # every filing and the pick selects the year's one row (docs/specs/suite-templates-s1.md).
    pick: str | None = None
    # s1_breakdown only: the axis local names whose single-dimension facts are members.
    axes: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: dict) -> Template:
        pick = raw.get("pick")
        if pick is not None and pick not in PICKS:
            raise ValueError(f"template {raw.get('id')!r}: unknown pick {pick!r}")
        if pick == PICK_S1_BREAKDOWN and not (raw.get("axes") and raw.get("concept")):
            raise ValueError(
                f"template {raw.get('id')!r}: an s1_breakdown template names its axes "
                "and concept"
            )
        if pick is not None and _YEAR_PREDICATE.search(raw["sql"]):
            # A year filter would cut the later filings' comparatives that restate the
            # year and the 10-Q rows; the pick selects the year.
            raise ValueError(
                f"template {raw.get('id')!r}: a pick template's SQL must not restrict "
                "fiscal_year or fiscal_period"
            )
        return cls(
            id=raw["id"],
            source=raw.get("source", ""),
            kind=raw.get("kind", "lookup"),
            sql=expand_sql_macros(raw["sql"]),
            nl=list(raw.get("nl", [])),
            params={
                k: ParamSpec.parse(k, v) for k, v in (raw.get("params") or {}).items()
            },
            expect=dict(raw.get("expect") or {}),
            tags=list(raw.get("tags", [])),
            rel_tol=float(raw.get("rel_tol", 1e-4)),
            alt_scale=[float(s) for s in raw.get("alt_scale", [])],
            top_n=raw.get("top_n"),
            min_rows=int(raw.get("min_rows", 1)),
            order_sensitive=bool(raw.get("order_sensitive", True)),
            require_unique=bool(raw.get("require_unique", False)),
            require_citation=bool(raw.get("require_citation", True)),
            post_sort=raw.get("post_sort"),
            ticker=raw.get("ticker"),
            concept=raw.get("concept"),
            allow_zero=bool(raw.get("allow_zero", False)),
            max_per_stratum=raw.get("max_per_stratum"),
            hierarchy_level=str(raw.get("hierarchy_level", LEVEL_REPORTED)),
            pick=pick,
            axes=[str(a) for a in raw.get("axes", [])],
        )


@dataclass
class BuildStats:
    """Why cases did or didn't get generated — printed after every build so a
    template that silently produces nothing is visible, not invisible."""

    emitted: int = 0
    skipped_empty: int = 0
    skipped_ambiguous: int = 0
    skipped_no_facts: int = 0
    skipped_too_few_rows: int = 0
    skipped_zero_value: int = 0
    skipped_capped: int = 0
    skipped_no_partition: int = 0  # breakdown: no member set sums to the total
    dropped_aggregate_rows: int = 0
    sql_errors: int = 0
    precise_substitutions: int = 0  # expected fact moved to its more precise duplicate
    per_template: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "emitted": self.emitted,
            "skipped_empty": self.skipped_empty,
            "skipped_ambiguous": self.skipped_ambiguous,
            "skipped_no_facts": self.skipped_no_facts,
            "skipped_too_few_rows": self.skipped_too_few_rows,
            "skipped_zero_value": self.skipped_zero_value,
            "skipped_capped": self.skipped_capped,
            "skipped_no_partition": self.skipped_no_partition,
            "dropped_aggregate_rows": self.dropped_aggregate_rows,
            "sql_errors": self.sql_errors,
            "precise_substitutions": self.precise_substitutions,
            "per_template": self.per_template,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fact_ids_from_json(value: Any) -> list[int]:
    """``component_fact_ids`` is JSONB — an object ({revenue: id, cogs: id}) or an array."""
    out: list[int] = []
    if isinstance(value, dict):
        candidates: Iterable[Any] = value.values()
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        return out
    for item in candidates:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            out.append(int(item))
    return out


def fact_id_groups_from_json(value: Any) -> list[list[int]]:
    """One group of fact ids per required input of ``component_fact_ids``.

    See docs/specs/alternative-inputs-grounding.md.

    Args:
        value: The JSONB value: an object or an array; a nested array lists
            alternatives for one input.

    Returns:
        The groups, in order; skipped entries produce no group.
    """
    if isinstance(value, dict):
        entries: Iterable[Any] = value.values()
    elif isinstance(value, (list, tuple)):
        entries = value
    else:
        return []
    groups: list[list[int]] = []
    for entry in entries:
        members = entry if isinstance(entry, (list, tuple)) else [entry]
        ids = _fact_ids_from_json(list(members))  # one input: its alternatives
        if ids:
            groups.append(ids)
    return groups


def group_classes(
    groups: Sequence[Sequence[int]],
    classes: collections.abc.Mapping[int, Sequence[int]],
) -> list[list[int]]:
    """One citation class per input group: the union of its members' classes.

    Args:
        groups: Fact ids per required input.
        classes: Fact id → its equivalence class.

    Returns:
        A sorted class per group, in order.
    """
    return [
        sorted({fid for member in group for fid in classes.get(member, [member])})
        for group in groups
    ]


def _find_summing_subset(
    target: float, values: list[float], rel_tol: float
) -> list[int] | None:
    """Indices of a subset of *values* (size >= 2) that sums to *target*, else None.

    Size >= 2 matters: a single sibling equal to the candidate is a duplicate, not a
    child, and treating it as one would remove a real component.
    """
    n = len(values)
    if n < 2:
        return None
    # Sets are tiny (a filer tags a handful of members per axis); exhaustive search is
    # fine, but cap it rather than hang the build on a pathological axis.
    order = sorted(range(n), key=lambda i: -abs(values[i]))[:14]

    found: list[int] | None = None

    def walk(pos: int, chosen: list[int], total: float) -> bool:
        nonlocal found
        if len(chosen) >= 2 and close_enough(total, target, rel_tol):
            found = list(chosen)
            return True
        if pos >= len(order):
            return False
        # All component values share a sign, so a partial sum past the target can
        # only grow — prune it.
        if abs(total) > abs(target) * (1 + rel_tol):
            return False
        for k in range(pos, len(order)):
            i = order[k]
            chosen.append(i)
            if walk(k + 1, chosen, total + values[i]):
                return True
            chosen.pop()
        return False

    walk(0, [], 0.0)
    return found


def collapse_hierarchy(
    rows: list[dict[str, Any]],
    *,
    level: str = LEVEL_REPORTED,
    rel_tol: float = 1e-4,
) -> list[dict[str, Any]]:
    """Reduce a breakdown to ONE hierarchy level, so the case has one right answer.

    A filer may tag both an aggregate and its children on the same axis — Apple's
    ``Product`` is iPhone + Mac + iPad + Wearables; NVIDIA's ``Data Center`` is
    Compute + Networking. Keeping both makes "break down revenue by segment"
    ambiguous: two equally correct answers exist and the case cannot discriminate.

    ``level="reported"`` (default) keeps the **parent** and drops its children — the
    level the filer actually reports, and the level the pipeline answers at. NVIDIA
    FY2025 keeps Data Center 115,186M rather than demanding Compute 102,196M +
    Networking 12,990M, which sum to exactly that.

    ``level="leaf"`` keeps the children instead. Retained because it is a defensible
    reading of the same question, and switching is a one-word change.

    Membership is classified against the FULL member set in one pass, not by
    iteratively shrinking it. With A = B + C and B = D + E, dropping just B and C
    would orphan D and E, which then double-count against A. Judged against the whole
    set, D and E are still children (of B) and so are not reported-level members.

    Exact duplicate rows (same entity and value) are collapsed first — AAPL FY2024
    lists ``Service`` twice under distinct member QNames.
    """
    deduped: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = (str(row.get("entity")), round(float(row.get("value", 0.0)), 4))
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    values = [float(r["value"]) for r in deduped]
    n = len(values)
    is_parent = [False] * n  # equals the sum of >=2 other members
    is_child = [False] * n  # participates in a subset summing to another member

    for i in range(n):
        others = [j for j in range(n) if j != i]
        subset = _find_summing_subset(values[i], [values[j] for j in others], rel_tol)
        if subset is None:
            continue
        is_parent[i] = True
        for k in subset:
            is_child[others[k]] = True

    if level == LEVEL_REPORTED:
        return [r for k, r in enumerate(deduped) if not is_child[k]]
    return [r for k, r in enumerate(deduped) if not is_parent[k]]


def drop_aggregate_rows(
    rows: list[dict[str, Any]], *, rel_tol: float = 1e-4
) -> list[dict[str, Any]]:
    """Back-compat alias for the leaf-level collapse."""
    return collapse_hierarchy(rows, level=LEVEL_LEAF, rel_tol=rel_tol)


def clean_member_label(label: str) -> str:
    """``'Data Center [Member]'`` → ``'Data Center'`` — questions must read naturally."""
    return _MEMBER_MARKER.sub("", str(label)).strip()


def case_key(bound: dict) -> str:
    """A stable, collision-free id fragment for one parameter combination.

    Multi-dimensional combos are long, and truncating them silently collapsed
    distinct cases onto one id (and thus onto one another in the suite). Keep a
    readable prefix but disambiguate with a hash of the full value."""
    if not bound:
        return "all"
    full = "-".join(f"{k}={bound[k]}" for k in sorted(bound))
    slug = re.sub(r"[^A-Za-z0-9_.=-]+", "_", full)
    if len(slug) <= 60:
        return slug
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:52]}-{digest}"


def select_stratified(
    combos: Sequence[dict], per_stratum: int | None, *, ticker: str | None = None
) -> list[dict]:
    """Keep at most *per_stratum* combos per (ticker, fiscal year), chosen by a fixed hash.

    Within a stratum the combos whose selection key has the smallest SHA-1 digest are
    kept, so the choice depends only on that stratum's contents, never on enumeration
    order or on other strata (docs/specs/stable-case-selection.md).

    Args:
        combos: Bound parameter combinations, in enumeration order.
        per_stratum: Maximum combos kept per stratum; ``None`` keeps all.
        ticker: The template's pinned ticker, for combos that bind none.

    Returns:
        The kept combos, in their input order.

    Raises:
        ValueError: If *per_stratum* is less than 1.
    """
    if per_stratum is None:
        return list(combos)
    if per_stratum < 1:
        raise ValueError(f"per_stratum must be >= 1, got {per_stratum}")

    def selection_key(combo: dict) -> str:
        full = "-".join(f"{k}={combo[k]}" for k in sorted(combo))
        return hashlib.sha1(full.encode("utf-8")).hexdigest()

    strata: dict[tuple[Any, Any], list[int]] = {}
    for i, combo in enumerate(combos):
        stratum = (combo.get("ticker", ticker), combo.get("fy"))
        strata.setdefault(stratum, []).append(i)
    keep: set[int] = set()
    for members in strata.values():
        ranked = sorted(members, key=lambda i: (selection_key(combos[i]), i))
        keep.update(ranked[:per_stratum])
    return [combo for i, combo in enumerate(combos) if i in keep]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class SqlDerivedBuilder:
    """Turns the template spec + a live fact store into an :class:`EvalSuite`."""

    def __init__(
        self, spec: dict, conn: Any, *, company_names: dict[str, str] | None = None
    ):
        self.spec = spec
        self.conn = conn
        self.version = str(spec.get("version", "v1"))
        self.suite_name = spec.get("suite_name", SUITE_NAME)
        self.corpus = spec.get("corpus", {})
        self.tiers = spec.get("tiers", {})
        self.templates = [Template.parse(t) for t in spec.get("templates", [])]
        self.company_names = (
            company_names if company_names is not None else self._load_names()
        )
        self.stats = BuildStats()
        self._class_cache: dict[int, list[int]] = {}

    # -- data access -------------------------------------------------------
    def _load_names(self) -> dict[str, str]:
        """Ticker → the name a question uses, so entity resolution is really exercised.

        The spec's ``corpus.company_names`` wins; entity_master's EDGAR legal name is
        the fallback for anything not listed there."""
        names: dict[str, str] = {}
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT trading_symbol, company_name FROM entity_master "
                    "WHERE trading_symbol IS NOT NULL"
                )
                names = {str(t).upper(): str(n) for t, n in cur.fetchall() if n}
        except Exception:
            logger.warning("entity_master unavailable; falling back to spec names")
        for ticker, name in (self.corpus.get("company_names") or {}).items():
            names[str(ticker).upper()] = str(name)
        return names

    def _query(self, sql: str, params: dict) -> tuple[list[str], list[tuple]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchall()

    def _precise_duplicate(
        self, fact_ids: Sequence[int], value: float
    ) -> tuple[int, float] | None:
        """The more precise duplicate of a single expected fact, if the filing has one.

        A filing tags one number again at lower precision ("$20.8 billion" next to
        20,832 million). The lookup answers with the most precise of such consistent
        duplicates (docs/specs/rounded-duplicates.md), so the ground truth must too, or
        a correct answer grades as a confident wrong one.

        Args:
            fact_ids: The case's expected fact ids; only a single fact is considered.
            value: The case's expected value; it must be that fact's own value.

        Returns:
            ``(fact_id, value)`` of the more precise duplicate, or None to keep the case.
        """
        if len(fact_ids) != 1:
            return None
        anchor = int(fact_ids[0])
        try:
            cols, rows = self._query(_PRECISION_SQL, {"id": anchor})
        except Exception as exc:
            self.conn.rollback()
            logger.warning("precise-duplicate lookup failed for fact %s (%s)", anchor, exc)
            return None
        if not {"fact_id", "value", "decimals"} <= set(cols):
            return None
        i_id, i_value, i_dec = (cols.index(c) for c in ("fact_id", "value", "decimals"))
        facts = [
            (int(r[i_id]), float(r[i_value]), r[i_dec]) for r in rows if r[i_value] is not None
        ]
        own = next((v for fid, v, _ in facts if fid == anchor), None)
        if own is None or not close_enough(own, value, 1e-9):
            return None  # the case's value is not this fact's own value (scaled, derived)
        win = most_precise_consistent([(v, d) for _, v, d in facts])
        if win is None or facts[win][1] == own:
            return None
        return facts[win][0], facts[win][1]

    def fact_equivalence_classes(self, fact_ids: Sequence[int]) -> list[list[int]]:
        """For each fact, every fact_id carrying the SAME value for the same concept,
        period and dimension within the same filing.

        A filing tags one number on several rows (different presentation roles) — AAPL
        FY2026 revenue is facts 15488, 15811 and 16207. Grading a citation against a
        single surrogate id therefore measured which duplicate the pipeline happened to
        pick, not whether it sourced the answer correctly. Results are cached because
        the same facts recur across paraphrases and templates.
        """
        wanted = [int(f) for f in fact_ids]
        missing = [f for f in wanted if f not in self._class_cache]
        if missing:
            try:
                _, rows = self._query(_EQUIVALENCE_SQL, {"ids": missing})
                for anchor, members in rows:
                    self._class_cache[int(anchor)] = sorted(int(m) for m in members)
            except Exception as exc:
                self.conn.rollback()
                logger.warning(
                    "fact equivalence lookup failed (%s); using singletons", exc
                )
            for f in missing:  # anything unresolved is its own class
                self._class_cache.setdefault(f, [f])
        return [self._class_cache[f] for f in wanted]

    # -- parameter expansion ----------------------------------------------
    def _static_values(self, spec: ParamSpec, tier: dict) -> list[Any]:
        if spec.corpus_ref:
            override = tier.get(spec.corpus_ref)
            return list(
                override
                if override is not None
                else self.corpus.get(spec.corpus_ref, [])
            )
        return list(spec.values or [])

    def _dynamic_values(self, spec: ParamSpec, bound: dict) -> list[Any]:
        try:
            _, rows = self._query(spec.from_sql or "", bound)
        except Exception as exc:
            self.conn.rollback()
            logger.debug(
                "param %s enumeration failed for %s: %s", spec.name, bound, exc
            )
            return []
        return [r[0] for r in rows if r and r[0] is not None]

    def _combinations(self, template: Template, tier: dict) -> Iterable[dict]:
        """Cartesian product of the static params, then per-combo dynamic enumeration.

        Dynamic params (``from_sql``) depend on the static ones already bound — a
        segment member only exists for a given (ticker, fy) — so they cannot be part
        of a single global product.
        """
        static = {n: s for n, s in template.params.items() if not s.is_dynamic}
        dynamic = {n: s for n, s in template.params.items() if s.is_dynamic}

        names = list(static)
        value_lists = [self._static_values(static[n], tier) for n in names]
        if any(len(v) == 0 for v in value_lists):
            return

        for combo in itertools.product(*value_lists) if names else [()]:
            bound = dict(zip(names, combo))
            if not dynamic:
                yield bound
                continue
            for dname, dspec in dynamic.items():
                for dvalue in self._dynamic_values(dspec, bound):
                    yield {**bound, dname: dvalue}

    # -- case construction -------------------------------------------------
    def _render_context(self, template: Template, bound: dict, extra: dict) -> dict:
        ticker = bound.get("ticker") or template.ticker
        ctx: dict[str, Any] = dict(bound)
        ctx.update(extra)
        if ticker:
            ctx["ticker"] = ticker
            ctx["company"] = self.company_names.get(str(ticker).upper(), str(ticker))
        # `<param>_label` from the spec's label map, falling back to the raw value.
        for name, spec in template.params.items():
            if name in bound:
                ctx[f"{name}_label"] = spec.labels.get(bound[name], str(bound[name]))
        if "segment" in ctx:
            ctx["segment"] = clean_member_label(ctx["segment"])
        if "combo" in ctx:
            concept_part, _, dim_part = str(ctx["combo"]).partition(" :: ")
            ctx["concept_label"] = _humanise_concept(concept_part)
            ctx["dim_label"] = clean_member_label(dim_part)
        return ctx

    def _collect_fact_ids(
        self, template: Template, cols: list[str], rows: Sequence[tuple]
    ) -> list[int]:
        """Every expected fact id, flat and de-duplicated, in first-seen order."""
        out: list[int] = []
        for group in self._collect_fact_id_groups(template, cols, rows):
            out.extend(fid for fid in group if fid not in out)
        return out

    def _collect_fact_id_groups(
        self, template: Template, cols: list[str], rows: Sequence[tuple]
    ) -> list[list[int]]:
        """The expected fact ids grouped by required input.

        A plain ``fact_ids`` column gives one-element groups; a ``fact_ids_json``
        column gives one group per input, where a nested array lists alternatives
        (docs/specs/alternative-inputs-grounding.md). Duplicate groups are dropped.
        """
        idx = {c: i for i, c in enumerate(cols)}
        groups: list[list[int]] = []

        def _add(group: list[int]) -> None:
            if group and group not in groups:
                groups.append(group)

        spec = template.expect.get("fact_ids")
        columns = [spec] if isinstance(spec, str) else list(spec or [])
        json_col = template.expect.get("fact_ids_json")
        for row in rows:
            for col in columns:
                value = row[idx[col]] if col in idx else None
                if isinstance(value, int) and not isinstance(value, bool):
                    _add([value])
            if json_col and json_col in idx:
                for group in fact_id_groups_from_json(row[idx[json_col]]):
                    _add(group)
        return groups

    def _fact_id_classes(
        self, template: Template, cols: list[str], rows: Sequence[tuple]
    ) -> list[list[int]]:
        """One citation class per required input: its alternatives' equivalence classes."""
        groups = self._collect_fact_id_groups(template, cols, rows)
        flat = [fid for group in groups for fid in group]
        equivalents = dict(zip(flat, self.fact_equivalence_classes(flat), strict=True))
        return group_classes(groups, equivalents)

    def _surviving_rows(
        self, template: Template, cols: list[str], rows: list[tuple]
    ) -> list[tuple]:
        """The source rows that actually become expected_rows, after aggregate-dropping
        and ``top_n`` truncation — so fact ids are collected from exactly those."""
        row_spec = template.expect.get("rows") or {}
        e_col, v_col = row_spec.get("entity"), row_spec.get("value")
        idx = {c: i for i, c in enumerate(cols)}
        if e_col not in idx or v_col not in idx:
            return rows

        keyed = []
        for row in rows:
            value = _to_float(row[idx[v_col]])
            entity = row[idx[e_col]]
            if value is not None and entity is not None:
                keyed.append(
                    ({"entity": clean_member_label(entity), "value": value}, row)
                )

        entries = [k[0] for k in keyed]
        if template.kind == KIND_BREAKDOWN:
            kept = collapse_hierarchy(
                entries, level=template.hierarchy_level, rel_tol=template.rel_tol
            )
            keep_keys = {(e["entity"], e["value"]) for e in kept}
            keyed = [k for k in keyed if (k[0]["entity"], k[0]["value"]) in keep_keys]
        if template.top_n:
            keyed = keyed[: template.top_n]
        return [k[1] for k in keyed]

    def _partition_rows(
        self, template: Template, cols: list[str], rows: Sequence[tuple]
    ) -> list[tuple] | None:
        """The source rows forming the breakdown's partition, value-descending.

        Args:
            template: A breakdown template whose ``expect`` names ``rows`` and ``total``.
            cols: Result column names.
            rows: Member rows; each carries the same ``total``.

        Returns:
            The chosen member rows (value-descending), then the chosen reconciling-line
            row if any; None when `choose_breakdown` finds no breakdown.
        """
        idx = {c: i for i, c in enumerate(cols)}
        row_spec = template.expect.get("rows") or {}
        e_col, v_col = row_spec.get("entity"), row_spec.get("value")
        t_col = template.expect.get("total")
        if e_col not in idx or v_col not in idx or t_col not in idx:
            return None
        usable = [
            r for r in rows if r[idx[e_col]] is not None and _to_float(r[idx[v_col]]) is not None
        ]
        if not usable:
            return None
        total = _to_float(usable[0][idx[t_col]])
        # Optional structure (docs/specs/breakdown-reconciling-items.md, C4): a boolean
        # column marking reconciling-line rows, the member's local name and its parents.
        line_col = template.expect.get("line")
        member_col = template.expect.get("member")
        parents_col = template.expect.get("parents")
        member_rows: list[tuple] = []
        line_rows: list[tuple] = []
        for r in usable:
            is_line = line_col in idx and bool(r[idx[line_col]])
            (line_rows if is_line else member_rows).append(r)
        members = [
            {
                "label": clean_member_label(r[idx[e_col]]),
                "value": _to_float(r[idx[v_col]]),
                "member": (str(r[idx[member_col]] or "") if member_col in idx else ""),
                "parents": (list(r[idx[parents_col]] or []) if parents_col in idx else []),
            }
            for r in member_rows
        ]
        lines = [
            {"label": clean_member_label(r[idx[e_col]]), "value": _to_float(r[idx[v_col]])}
            for r in line_rows
        ]
        chosen = choose_breakdown(members, total, lines, rel_tol=template.rel_tol)
        if chosen is None:
            return None
        picked = sorted(
            (member_rows[i] for i in chosen["members"]),
            key=lambda r: -float(_to_float(r[idx[v_col]]) or 0.0),
        )
        if chosen["line"] is not None:
            picked.append(line_rows[chosen["line"]])
        return picked

    def _build_case(
        self,
        template: Template,
        bound: dict,
        cols: list[str],
        rows: list[tuple],
        nl_template: str,
        paraphrase_index: int,
    ) -> EvalCase | None:
        idx = {c: i for i, c in enumerate(cols)}
        extra: dict[str, Any] = {}
        for key, col in (template.expect.get("extra") or {}).items():
            if col in idx and rows:
                extra[key] = rows[0][idx[col]]

        ctx = self._render_context(template, bound, extra)
        try:
            question = nl_template.format(**ctx)
        except KeyError as exc:
            logger.warning(
                "template %s: question placeholder %s unbound", template.id, exc
            )
            return None

        # For a ranked/multi-row case the ground truth is only the rows that survive
        # `top_n` (and aggregate-dropping). Collecting fact ids from ALL source rows
        # would demand citations for rows the answer was never asked to return — an
        # 11-filer screen truncated to top 5 marked the pipeline "missing 6 facts".
        # A breakdown whose template supplies the total is "the breakdown" by the one
        # rule the pipeline also uses: the finest member set summing to the total
        # (docs/specs/breakdown-op.md). No such set, no case — ambiguous ground truth
        # is worse than none.
        partition = None
        if template.kind == KIND_BREAKDOWN and template.expect.get("total"):
            partition = self._partition_rows(template, cols, rows)
            if partition is None:
                self.stats.skipped_no_partition += 1
                return None

        source_rows = rows
        if partition is not None:
            source_rows = partition
        elif template.kind in MULTI_ROW_KINDS:
            source_rows = self._surviving_rows(template, cols, rows)
        fact_ids = self._collect_fact_ids(template, cols, source_rows)
        if not fact_ids:
            self.stats.skipped_no_facts += 1
            return None

        combo_key = case_key(bound)
        case_id = f"{template.id}:{combo_key}"
        if paraphrase_index:
            case_id = f"{case_id}#p{paraphrase_index}"

        # expect.not_meaningful names a boolean column: true marks a case whose right
        # answer is "n/m" (a percentage change from a base <= 0).
        nm_col = template.expect.get("not_meaningful")
        not_meaningful = bool(
            nm_col and nm_col in idx and rows and rows[0][idx[nm_col]]
        )

        unit_col = template.expect.get("unit")
        unit = rows[0][idx[unit_col]] if unit_col and unit_col in idx and rows else None

        common: dict[str, Any] = dict(
            id=case_id,
            fact_id_classes=self._fact_id_classes(template, cols, source_rows),
            question=question,
            suite=f"{self.suite_name}_{self.version}",
            kind=template.kind,
            expected_fact_ids=fact_ids,
            rel_tol=template.rel_tol,
            require_citation=template.require_citation,
            origin=template.source or template.id,
            paraphrase_group=f"{template.id}:{combo_key}",
            ticker=str(bound.get("ticker") or template.ticker or "") or None,
            fy=(int(bound["fy"]) if "fy" in bound else None),
            concept=str(
                bound.get("concept") or bound.get("metric") or template.concept or ""
            )
            or None,
            unit=(str(unit) if unit else None),
            tags=list(template.tags),
        )

        if template.kind in MULTI_ROW_KINDS:
            row_spec = template.expect.get("rows") or {}
            e_col, v_col = row_spec.get("entity"), row_spec.get("value")
            if e_col not in idx or v_col not in idx:
                logger.warning(
                    "template %s: rows spec references unknown column", template.id
                )
                return None
            if partition is not None:
                expected_rows = [
                    {"entity": clean_member_label(r[idx[e_col]]), "value": float(r[idx[v_col]])}
                    for r in partition
                ]
                return EvalCase(
                    expected_rows=expected_rows,
                    top_n=None,
                    order_sensitive=False,
                    **common,
                )
            expected_rows = []
            for row in rows:
                value = _to_float(row[idx[v_col]])
                entity = row[idx[e_col]]
                if value is None or entity is None:
                    continue
                expected_rows.append(
                    {"entity": clean_member_label(entity), "value": value}
                )
            if template.kind == KIND_BREAKDOWN:
                before = len(expected_rows)
                expected_rows = collapse_hierarchy(
                    expected_rows,
                    level=template.hierarchy_level,
                    rel_tol=template.rel_tol,
                )
                self.stats.dropped_aggregate_rows += before - len(expected_rows)
            if len(expected_rows) < template.min_rows:
                self.stats.skipped_too_few_rows += 1
                return None
            if template.top_n:
                expected_rows = expected_rows[: template.top_n]
            return EvalCase(
                expected_rows=expected_rows,
                top_n=template.top_n,
                order_sensitive=template.order_sensitive,
                **common,
            )

        value_col = template.expect.get("value")
        value = (
            _to_float(rows[0][idx[value_col]]) if value_col in idx and rows else None
        )
        if value is None:
            self.stats.skipped_empty += 1
            return None
        if value == 0 and not template.allow_zero:
            # close_enough() matches a zero target with an absolute band, so almost
            # any answer would pass. Such a case would inflate accuracy, not test.
            self.stats.skipped_zero_value += 1
            return None
        precise = self._precise_duplicate(fact_ids, value)
        if precise is not None:
            fact_id, value = precise
            common.update(
                expected_fact_ids=[fact_id],
                fact_id_classes=self.fact_equivalence_classes([fact_id]),
            )
            self.stats.precise_substitutions += 1
        alts = [value * scale for scale in template.alt_scale]
        return EvalCase(
            expected_value=value,
            alt_values=alts,
            expect_not_meaningful=not_meaningful,
            **common,
        )

    # -- public API --------------------------------------------------------
    def _assert_period_scoped(self) -> None:
        """Every template must constrain ``fiscal_period``.

        A question phrased "in fiscal YYYY" means the ANNUAL figure. Without this
        predicate the SQL happily returns a 10-Q quarterly or year-to-date row and
        labels it as the annual answer — which is exactly what happened for FY2026,
        where only NVDA has a 10-K: 75 cases carried a sub-annual number as ground
        truth and scored the pipeline's correct refusal as a failure. That is 18% of
        the suite silently mis-measured, so this is enforced rather than trusted. A
        template with a ``pick`` is scoped by the pick (``s1_lookup_pick`` reads the
        filing labelled ``fy``/``FY``), not by its SQL.
        """
        offenders = [
            t.id for t in self.templates if t.pick is None and "fiscal_period" not in t.sql
        ]
        if offenders:
            raise ValueError(
                "template(s) do not constrain fiscal_period, so ground truth may be a "
                f"quarterly figure labelled as annual: {offenders}"
            )

    def _cases_for(
        self, template: Template, bound: dict, n_paraphrases: int
    ) -> list[EvalCase]:
        """Run *template* for one parameter combination and build its paraphrase cases.

        Args:
            template: The template to run.
            bound: The bound parameter combination.
            n_paraphrases: How many of the template's phrasings to emit.

        Returns:
            The cases this combination produced; empty when it was skipped.
        """
        try:
            if template.pick == PICK_S1_BREAKDOWN:
                cols, rows = self._breakdown_rows(template, bound)
            else:
                cols, rows = self._query(template.sql, bound)
        except Exception as exc:
            self.conn.rollback()
            self.stats.sql_errors += 1
            logger.warning("template %s SQL failed for %s: %s", template.id, bound, exc)
            return []

        if template.pick == PICK_S1_LOOKUP:
            picked = s1_lookup_pick([dict(zip(cols, r)) for r in rows], int(bound["fy"]))
            rows = [] if picked is None else [tuple(picked.get(c) for c in cols)]
        elif template.pick == PICK_S1_YOY:
            # The growth of the two S1 lookups (docs/specs/yoy-growth-s1.md); the case
            # reads the pick's own keys, so they become the columns.
            growth = s1_yoy_pick([dict(zip(cols, r)) for r in rows], int(bound["fy"]))
            cols = list(_YOY_PICK_COLUMNS)
            rows = [] if growth is None else [tuple(growth[c] for c in cols)]
        elif template.pick == PICK_S1_DIMENSIONAL:
            answer = s1_dimensional_pick([dict(zip(cols, r)) for r in rows], int(bound["fy"]))
            rows = [tuple(a.get(c) for c in cols) for a in answer]
        if not rows:
            self.stats.skipped_empty += 1
            return []
        if template.require_unique and len(rows) > 1:
            # Ambiguous ground truth is worse than none — drop it.
            self.stats.skipped_ambiguous += 1
            return []
        if template.post_sort:
            rows = _sort_rows(cols, rows, template.post_sort)

        out = []
        for p_index, nl in enumerate(template.nl[:n_paraphrases]):
            case = self._build_case(template, bound, cols, list(rows), nl, p_index)
            if case is not None:
                out.append(case)
        return out

    def _breakdown_rows(
        self, template: Template, bound: dict
    ) -> tuple[list[str], list[tuple]]:
        """A breakdown template's rows from the filing whose split the case keys.

        docs/specs/dimensional-s1.md, requirement 5, as the pipeline's breakdown step
        applies it: the filings of `breakdown_filings`, latest first; for each, the
        template's SQL over that filing's total (``total_fact_id``, from
        `breakdown_total`) and members on ``axes``; the first whose rows make a breakdown
        (`_partition_rows`) answers, else the latest filing's rows (no case).

        Args:
            template: An ``s1_breakdown`` template.
            bound: The bound parameters (``ticker`` unless the template pins one, ``fy``).

        Returns:
            The column names and rows; empty rows when no filing splits the year.
        """
        fy = int(bound["fy"])
        ticker = bound.get("ticker") or template.ticker
        names, raw = self._query(
            _BREAKDOWN_FILER_ROWS_SQL, {"ticker": ticker, "concept_family": template.concept}
        )
        filer_rows = [dict(zip(names, r, strict=True)) for r in raw]
        first: tuple[list[str], list[tuple]] | None = None
        for accession in breakdown_filings(filer_rows, fy, template.axes):
            total = breakdown_total(filer_rows, accession, fy)
            if total is None:
                continue
            cols, rows = self._query(
                template.sql,
                {**bound, "total_fact_id": total["fact_id"], "axes": list(template.axes)},
            )
            if first is None:
                first = (cols, rows)
            if self._partition_rows(template, cols, rows) is not None:
                return cols, rows
        return first if first is not None else ([], [])

    def build(self, tier_name: str = "standard") -> EvalSuite:
        self._assert_period_scoped()
        tier = dict(self.tiers.get(tier_name) or {})
        n_paraphrases = int(tier.get("paraphrases", 1))
        stratum_quota = tier.get("max_cases_per_stratum")
        if stratum_quota is not None and "max_cases_per_template" in tier:
            # The two caps interact order-dependently — the defect the per-stratum
            # quota exists to remove (docs/specs/stable-case-selection.md).
            raise ValueError(
                f"tier {tier_name!r} sets both max_cases_per_stratum and "
                "max_cases_per_template; use one"
            )
        cap = int(tier.get("max_cases_per_template", 10_000))
        self.stats = BuildStats()
        cases: list[EvalCase] = []

        for template in self.templates:
            quota = (
                template.max_per_stratum
                if template.max_per_stratum is not None
                else stratum_quota
            )
            if quota is None:
                produced = 0
                for bound in self._combinations(template, tier):
                    if produced >= cap:
                        self.stats.skipped_capped += 1
                        break
                    combo_cases = self._cases_for(template, bound, n_paraphrases)
                    cases.extend(combo_cases)
                    produced += len(combo_cases)
            else:
                candidates = []
                for bound in self._combinations(template, tier):
                    combo_cases = self._cases_for(template, bound, n_paraphrases)
                    if combo_cases:
                        candidates.append((bound, combo_cases))
                kept = select_stratified(
                    [bound for bound, _ in candidates],
                    int(quota),
                    ticker=template.ticker,
                )
                kept_ids = {id(bound) for bound in kept}
                self.stats.skipped_capped += len(candidates) - len(kept)
                produced = 0
                for bound, combo_cases in candidates:
                    if id(bound) in kept_ids:
                        cases.extend(combo_cases)
                        produced += len(combo_cases)
            self.stats.emitted += produced
            self.stats.per_template[template.id] = produced
            if produced == 0:
                logger.warning("template %s produced NO cases", template.id)

        from src.eval.core.fingerprint import compute_fingerprint

        return EvalSuite(
            name=self.suite_name,
            version=self.version,
            cases=cases,
            generated_at=datetime.datetime.now(datetime.UTC).isoformat(),
            corpus_fingerprint=compute_fingerprint(self.conn),
            description=(
                f"Ground truth derived from scripts/xbrl/analyst_queries.sql via "
                f"configs/eval/sql_derived_templates.yaml (tier={tier_name}). "
                f"Regenerate with scripts/eval/build_sql_derived_suite.py."
            ),
        )


def _sort_rows(
    cols: list[str], rows: Sequence[tuple], post_sort: dict[str, str]
) -> list[tuple]:
    """Rank rows by a column. The reference SQL orders by ticker (for readability);
    a ranked eval case needs them ordered by the metric being ranked."""
    col = post_sort.get("by")
    if col not in cols:
        return list(rows)
    i = cols.index(col)
    reverse = post_sort.get("order", "desc") == "desc"
    return sorted(
        rows,
        key=lambda r: (_to_float(r[i]) is None, _to_float(r[i]) or 0.0),
        reverse=reverse,
    )


def _humanise_concept(qname: str) -> str:
    """``AllocatedShareBasedCompensationExpense`` → ``allocated share based
    compensation expense`` so a generated question reads like English."""
    local = qname.rsplit(":", 1)[-1]
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", local)
    return spaced.replace("_", " ").lower()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def load_spec(path: str = DEFAULT_SPEC_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build(
    *,
    spec_path: str = DEFAULT_SPEC_PATH,
    tier: str = "standard",
    conn: Any = None,
) -> tuple[EvalSuite, BuildStats]:
    """Build the suite against the live fact store."""
    close_after = False
    if conn is None:
        from src.db.conn import connect

        conn = connect()
        close_after = True
    try:
        builder = SqlDerivedBuilder(load_spec(spec_path), conn)
        suite = builder.build(tier)
        return suite, builder.stats
    finally:
        if close_after:
            try:
                conn.close()
            except Exception:
                pass


def load(path: str) -> EvalSuite:
    """Load a previously generated, committed suite file."""
    return EvalSuite.load(path)
