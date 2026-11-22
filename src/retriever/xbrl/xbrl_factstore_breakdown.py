"""Single-filer `breakdown` step — one filer-year's metric split by segment, product or
geography (docs/specs/breakdown-op.md).

Where `XbrlFactstoreScreen` ranks one metric across filers, a breakdown returns the
members of ONE filer's axis that partition its total, each with its own `fact_id`.
The planner used to guess member names and emit one `lookup` per guess; this runs one
deterministic query per filer-year and axis instead.

A filer can tag several alternative partitions on one axis (Apple: Product/Service AND
iPhone, Mac, iPad, Wearables, Services; Amazon: goods/services AND its product lines).
`choose_partition` is the single rule for which one is "the breakdown" — the finest
subset that sums to the undimensioned total — and the eval suite's ground truth uses the
same rule, so the eval measures whether the pipeline returns THE breakdown rather than
which valid split it happened to pick.

`choose_breakdown` extends that rule (docs/specs/breakdown-reconciling-items.md): a member
never counts with its own ancestor on the axis (from the filing's presentation/definition
arcs), eliminations and zeros are never members, and when no member set is exact, every
member plus one reconciling line the filer presents in the same table (or calculation) may
make the total. `choose_partition` is its plain case.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import psycopg2
import psycopg2.extras

from src.logging_config import get_logger
from src.retriever.xbrl.restatement import (
    breakdown_filings,
    current_column,
    dimension_identity,
)
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    XbrlFactstoreLookup,
    publish_gate_clause,
)
from src.retriever.xbrl.xbrl_factstore_screen import (
    ScreenResult,
    ScreenStatus,
    XbrlFactstoreScreen,
)

logger = get_logger(__name__)

# More members than this are not searched (2^20 subsets); the corpus peaks at 12.
MAX_PARTITION_MEMBERS = 20

# Plan `axis` kind -> (view, axis local-name pattern). Geography reads its own view.
_SEGMENT_AXES = {
    "segment": "%StatementBusinessSegmentsAxis",
    "product": "%ProductOrServiceAxis",
}
VALID_AXES = frozenset({"segment", "product", "geography"})
# Plan `axis` kind -> the axis local name whose table presents the reconciling lines.
_AXIS_LOCAL_NAMES = {
    "segment": "StatementBusinessSegmentsAxis",
    "product": "ProductOrServiceAxis",
    "geography": "StatementGeographicalAxis",
}
# Plan `axis` kind -> the axis local names whose single-dimension facts are its members
# (docs/specs/dimensional-s1.md, requirement 5).
BREAKDOWN_AXES = {
    "segment": ("StatementBusinessSegmentsAxis",),
    "product": ("ProductOrServiceAxis",),
    "geography": ("StatementGeographicalAxis", "GeographicalAreasAxis"),
}

_MEMBER_MARKER = re.compile(r"\s*\[Member\]")


def _clean(label: Any) -> str:
    """A member label without its ``[Member]`` marker."""
    return _MEMBER_MARKER.sub("", str(label or "")).strip()


def _str_id(fact_id: Any) -> str | None:
    return None if fact_id is None else str(fact_id)


def choose_partition(
    members: Sequence[tuple[str, float]], total: float | None, rel_tol: float = 1e-4
) -> list[int] | None:
    """Choose the members that partition *total*: the finest subset summing to it.

    Args:
        members: ``(label, value)`` of each member tagged on the axis.
        total: The undimensioned total the partition must sum to.
        rel_tol: Relative tolerance for the sum (``close_enough``).

    Returns:
        Ascending indices into *members* of the largest subset (at least two
        members) summing to *total*, ties to the smallest deviation and then the
        smallest sorted labels; None when no subset qualifies.
    """
    # The plain case of `choose_breakdown` (docs/specs/breakdown-reconciling-items.md, C4):
    # each member is identified by its label, with no parents and no reconciling line.
    plain = [{"label": label, "value": value, "member": "", "parents": []}
             for label, value in members]
    chosen = choose_breakdown(plain, total, rel_tol=rel_tol)
    return None if chosen is None else chosen["members"]


# Consolidation items reconcile segments to the total; they are never a member row.
_CONSOLIDATION_MEMBERS = frozenset({
    "OperatingSegmentsMember",
    "IntersegmentEliminationMember",
    "ConsolidationEliminationsMember",
    "CorporateNonSegmentMember",
    "MaterialReconcilingItemsMember",
    "SegmentReconcilingItemsMember",
})
_CONSOLIDATION_AXIS = "ConsolidationItemsAxis"
_PARENT_ARCS = frozenset({"presentationArc", "definitionArc"})


def _local(name: Any) -> str:
    """``'us-gaap:Revenues'`` or ``'http://...:Revenues'`` -> ``'Revenues'``."""
    return str(name or "").rsplit(":", 1)[-1]


def is_consolidation_item(member: Any) -> bool:
    """Whether a member is a consolidation item (elimination, corporate, segment total).

    Args:
        member: The member's name, prefixed or local.

    Returns:
        True for the six ``ConsolidationItemsAxis`` members of the spec, or any name
        containing ``Elimination`` or ``Intersegment`` (any case).
    """
    name = _local(member)
    low = name.lower()
    return name in _CONSOLIDATION_MEMBERS or "elimination" in low or "intersegment" in low


def _identity(m: dict) -> str:
    return m.get("member") or m["label"]


class _Axis:
    """The eligible distinct members of one axis and their hierarchy (C1)."""

    def __init__(self, members: Sequence[dict]) -> None:
        self.members = members
        seen: set[tuple[Any, Any]] = set()
        self.distinct: list[int] = []
        for i, m in enumerate(members):
            value = m.get("value")
            if value is None or value == 0 or is_consolidation_item(m.get("member")):
                continue
            key = (m["label"], value)
            if key not in seen:
                seen.add(key)
                self.distinct.append(i)
        by_ident: dict[str, list[int]] = {}
        for j, m in enumerate(members):
            by_ident.setdefault(_identity(m), []).append(j)
        self.ident = {i: _identity(members[i]) for i in self.distinct}
        self.ancestors = {i: self._ancestors(i, by_ident) for i in self.distinct}

    def _ancestors(self, i: int, by_ident: dict[str, list[int]]) -> set[str]:
        # Parents, their parents ..., through every member with that identity.
        seen: set[str] = set()
        stack = list(self.members[i].get("parents") or [])
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            for j in by_ident.get(p, []):
                stack.extend(self.members[j].get("parents") or [])
        return seen

    def _compatible(self, i: int, j: int) -> bool:
        a, b = self.ident[i], self.ident[j]
        return a != b and b not in self.ancestors[i] and a not in self.ancestors[j]

    def _usable(self, i: int) -> bool:
        # A member that is its own ancestor (an arc cycle) is in no valid set.
        return self.ident[i] not in self.ancestors[i]

    def valid_sets(self) -> list[tuple[tuple[int, ...], float]]:
        """Every non-empty valid set (ascending indices) with its sum in index order."""
        usable = [i for i in self.distinct if self._usable(i)]
        out: list[tuple[tuple[int, ...], float]] = []

        def walk(start: int, chosen: list[int], subtotal: float) -> None:
            for k in range(start, len(usable)):
                i = usable[k]
                if all(self._compatible(i, j) for j in chosen):
                    chosen.append(i)
                    reached = subtotal + self.members[i]["value"]
                    out.append((tuple(chosen), reached))
                    walk(k + 1, chosen, reached)
                    chosen.pop()

        walk(0, [], 0)
        return out

    def is_complete(self, combo: tuple[int, ...]) -> bool:
        """Whether no further eligible distinct member can join *combo* keeping it valid."""
        inside = set(combo)
        return not any(
            j not in inside and self._usable(j) and all(self._compatible(j, i) for i in combo)
            for j in self.distinct
        )

    def top_level(self) -> list[int]:
        """The distinct eligible members with no ancestor among them."""
        names = set(self.ident.values())
        return [i for i in self.distinct if not (self.ancestors[i] & names)]


def choose_breakdown(
    members: Sequence[dict],
    total: float | None,
    lines: Sequence[dict] = (),
    rel_tol: float = 1e-4,
) -> dict | None:
    """Choose "the breakdown": the finest exact partition, else a complete member set
    plus one reconciling line (docs/specs/breakdown-reconciling-items.md, C1 and C2).

    Args:
        members: ``{"label", "value", "member", "parents"}`` of each member on the axis.
        total: The undimensioned total the breakdown must make.
        lines: ``{"label", "value", ...}`` candidate reconciling lines.
        rel_tol: Relative tolerance for every sum.

    Returns:
        ``{"members": [...], "line": int | None, "set_aside": [[...], ...]}`` with
        ascending indices into *members* (and an index into *lines*), or None.
    """
    if total is None or total <= 0:
        return None
    axis = _Axis(members)
    if len(axis.distinct) > MAX_PARTITION_MEMBERS:
        return None
    tol = rel_tol * total
    valid = axis.valid_sets()

    def labels(combo: tuple[int, ...]) -> list[str]:
        return sorted(members[i]["label"] for i in combo)

    exact = [(c, s) for c, s in valid if len(c) >= 2 and abs(s - total) <= tol]
    if exact:
        # Finest first; ties to the smaller deviation, the sorted labels, the indices.
        exact.sort(key=lambda cs: (-len(cs[0]), abs(cs[1] - total), labels(cs[0]), cs[0]))
        chosen = exact[0][0]
        rest = [c for c, _ in exact[1:] if not set(c) <= set(chosen)]
        aside = [c for c in rest if not any(set(c) < set(o) for o in rest)]
        return {"members": list(chosen), "line": None, "set_aside": [list(c) for c in aside]}

    seen: set[tuple[Any, Any]] = set()
    line_idx: list[int] = []
    for j, ln in enumerate(lines):
        value = ln.get("value")
        if value is None or value == 0 or (ln["label"], value) in seen:
            continue
        seen.add((ln["label"], value))
        line_idx.append(j)
    pairs: list[tuple[tuple, tuple[int, ...], int]] = []
    for c, s in valid:
        if len(c) < 2 or not line_idx or not axis.is_complete(c):
            continue
        for j in line_idx:
            reached = s + lines[j]["value"]
            if abs(reached - total) <= tol:
                key = (-len(c), abs(reached - total), labels(c), lines[j]["label"], j, c)
                pairs.append((key, c, j))
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[0])
    _, chosen, line = pairs[0]
    set_aside: list[list[int]] = []
    for _, c, _ in pairs[1:]:
        if c != chosen and list(c) not in set_aside:
            set_aside.append(list(c))
    return {"members": list(chosen), "line": line, "set_aside": set_aside}


def breakdown_gap(members: Sequence[dict], total: float) -> float:
    """What a breakdown's members leave unexplained (C2).

    Args:
        members: As for `choose_breakdown`.
        total: The undimensioned total.

    Returns:
        *total* minus the distinct eligible members that have no ancestor among them.
    """
    axis = _Axis(members)
    return total - sum(members[i]["value"] for i in axis.top_level())


def member_parents(members: Sequence[str], arcs: Sequence[dict]) -> dict[str, list[str]]:
    """Each member's parents among *members* in the filing's presentation and definition
    arcs (docs/specs/breakdown-reconciling-items.md, C1).

    Args:
        members: Member local names on one axis.
        arcs: ``{"role", "arc", "parent", "child", "weight"}`` relationships of the filing.

    Returns:
        Every name in *members* mapped to its sorted distinct parents (possibly empty).
    """
    names = set(members)
    parents: dict[str, set[str]] = {n: set() for n in members}
    for a in arcs:
        child, parent = a.get("child"), a.get("parent")
        if (a.get("arc") in _PARENT_ARCS and child in names and parent in names
                and parent != child):
            parents[child].add(parent)
    return {n: sorted(p) for n, p in parents.items()}


def reconciling_candidates(
    total: dict,
    member_concepts: set[str],
    axis: str,
    arcs: Sequence[dict],
    facts: Sequence[dict],
) -> list[dict]:
    """The lines a breakdown may use to reach its total, from the filing's own linkbase
    (docs/specs/breakdown-reconciling-items.md, C3).

    Args:
        total: The total fact, ``{"concept", "fact_id", ...}``.
        member_concepts: Local names of the members' concepts.
        axis: The axis local name, e.g. ``StatementBusinessSegmentsAxis``.
        arcs: ``{"role", "arc", "parent", "child", "weight"}`` relationships of the filing.
        facts: The filing's facts in the total's period and unit.

    Returns:
        ``{"label", "value", "fact_id", "unit", "concept"}`` per candidate, in fact order.
    """
    total_concept = _local(total.get("concept"))
    total_id = total.get("fact_id")
    excluded = {_local(c) for c in member_concepts} | {total_concept}
    pres = [a for a in arcs if a.get("arc") == "presentationArc"]
    # The axis's own table: roles presenting both the axis and the total's concept.
    axis_roles = {a.get("role") for a in pres if axis in (a.get("parent"), a.get("child"))}
    total_roles = {a.get("role") for a in pres if a.get("child") == total_concept}
    table_roles = axis_roles & total_roles
    presented = {a.get("child") for a in pres if a.get("role") in table_roles}
    calc: dict[str, float] = {}
    for a in arcs:
        child = a.get("child")
        if (a.get("arc") == "calculationArc" and a.get("parent") == total_concept
                and child is not None):
            weight = a.get("weight")
            calc.setdefault(child, 1.0 if weight is None else float(weight))

    out: list[dict] = []
    for f in facts:
        value = None if f.get("value") is None else float(f["value"])
        if not value or f.get("fact_id") == total_id:
            continue
        concept = _local(f.get("concept"))
        if f.get("axis") is None:
            if concept in excluded:
                continue
            if concept in calc:
                value = calc[concept] * value
            elif concept not in presented:
                continue
        elif not (_local(f.get("axis")) == _CONSOLIDATION_AXIS and concept == total_concept
                  and _local(f.get("member")) != "OperatingSegmentsMember"):
            continue
        out.append({"label": f.get("label"), "value": value, "fact_id": f.get("fact_id"),
                    "unit": f.get("unit"), "concept": f.get("concept")})
    return out


def breakdown_total(
    rows: Sequence[dict],
    accession: str,
    fy: int,
    fiscal_period: str = "FY",
    as_originally_reported: bool = False,
) -> dict | None:
    """One filing's undimensioned total that its breakdown of the year partitions.

    docs/specs/dimensional-s1.md, requirement 5: the filing's undimensioned row at a period
    of the year's own column, in `_query_total`'s order within the filing: the latest
    period, a unit the filing also reports for an earlier period (its reporting currency,
    not a convenience translation), the preferred concept, a valued row, the greatest
    fact id. The eval suite's breakdown templates key the same row.

    Args:
        rows: One filer's rows of the measure, any filing.
        accession: The filing.
        fy: The requested fiscal-year label.
        fiscal_period: The requested fiscal period.
        as_originally_reported: The own column of the original filing.

    Returns:
        The total row, or None when the filing has none at the year's periods.
    """
    own = current_column(rows, fy, fiscal_period, as_originally_reported)
    periods = {(str(r.get("period_start")), str(r.get("period_end")))
               for r in own if dimension_identity(r) == ()}
    filed = [r for r in rows
             if r.get("accession_no") == accession and dimension_identity(r) == ()]
    totals = [r for r in filed
              if (str(r.get("period_start")), str(r.get("period_end"))) in periods]
    if not totals:
        return None

    def order(r: dict) -> tuple:
        # Smallest first, as the SQL's ORDER BY ... LIMIT 1.
        has_prior = any(p.get("unit") == r.get("unit")
                        and str(p.get("period_end")) < str(r.get("period_end"))
                        for p in filed)
        rank = r.get("concept_pref_rank")
        return (not has_prior, rank is None, rank or 0, r.get("value") is None,
                -(r.get("fact_id") or 0))

    latest_end = max(str(r.get("period_end")) for r in totals)
    return min((r for r in totals if str(r.get("period_end")) == latest_end), key=order)


class XbrlFactstoreBreakdown:
    """Resolves a `breakdown` plan step to the partition of one filer's axis."""

    def __init__(self, db_conn_params: dict | None = None) -> None:
        """Store the connection parameters for the fact store.

        Args:
            db_conn_params: Keyword arguments for ``psycopg2.connect``.
        """
        self._db_conn_params = db_conn_params or {}

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> XbrlFactstoreBreakdown:
        """Build from the same ``XBRL_DB_*`` environment as the lookup and screen."""
        screen = XbrlFactstoreScreen.from_env(dotenv_path)
        return cls(db_conn_params=screen._db_conn_params)

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    def breakdown(self, step: dict) -> ScreenResult:
        """Resolve one `breakdown` step.

        A breakdown is one filing's split: the latest filing that reports the year's total
        and a single-dimension split of it on the axis answers, else the next one, down to
        the year's own filing (docs/specs/dimensional-s1.md, requirement 5).

        Args:
            step: ``{"id", "op": "breakdown", "cik", "fy", "concept_family", "axis"}``,
                optionally ``as_originally_reported`` (only the original filing).

        Returns:
            OK with the chosen partition as rows; NOT_FOUND when there is no complete
            partition; ERROR for a malformed step.
        """
        step_id = step.get("id")
        malformed = self._malformed(step)
        if malformed is not None:
            return malformed
        axis = str(step.get("axis")).strip().lower()
        cik, fy = step.get("cik"), step.get("fy")
        concept_family = step.get("concept_family") or "Revenue"
        fiscal_period = (step.get("fiscal_period") or "FY").upper()
        duration_kind = XbrlFactstoreLookup._derive_duration_kind(
            fiscal_period, bool(step.get("ytd", False))
        )
        notes: list[str] = []

        try:
            with self._connect() as conn:
                filer_rows = self._query_filer_rows(conn, cik, concept_family, duration_kind)
                if filer_rows and all("document_period_end" in r for r in filer_rows):
                    return self._breakdown_s1(
                        conn, step, axis, filer_rows, fiscal_period
                    )
                # Rows without the S1 columns (another spec's fake): the FY filing's split.
                total_row = self._query_total(
                    conn, cik, fy, concept_family, fiscal_period, duration_kind
                )
                if total_row is None:
                    notes.append(
                        f"no undimensioned {concept_family} for cik={cik} FY{fy} "
                        f"{fiscal_period}: nothing to partition"
                    )
                    return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes)
                if total_row.get("value") is None:
                    # A filed nil total reports nothing: there is no total to partition
                    # (docs/specs/sign-and-nil.md).
                    notes.append(
                        f"the {concept_family} total for cik={cik} FY{fy} {fiscal_period} "
                        f"is a filed nil (fact {total_row.get('fact_id')}): nothing to "
                        "partition"
                    )
                    return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes)
                unit = total_row.get("unit")
                members = self._query_members(
                    conn, axis, cik, fy, concept_family, fiscal_period, duration_kind, unit
                )
                accession_no = total_row.get("accession_no")
                arcs = self._query_arcs(conn, accession_no) if accession_no else []
                line_facts = self._query_line_facts(conn, total_row)
        except Exception as e:  # pragma: no cover - exercised only with a broken DB
            logger.error("factstore breakdown failed for step %r: %s", step_id, e)
            return ScreenResult(step_id, ScreenStatus.ERROR, error=str(e))

        return self.resolve(step, self._facts(axis, total_row, members, arcs, line_facts))

    def _breakdown_s1(
        self, conn, step: dict, axis: str, rows: list[dict], fiscal_period: str
    ) -> ScreenResult:
        """The breakdown from the latest filing that splits the year (requirement 5).

        Args:
            conn: An open connection.
            step: The breakdown step.
            axis: The plan's axis kind.
            rows: The filer's stored rows of the measure, any filing.
            fiscal_period: ``FY`` or a quarter.

        Returns:
            The first OK result over `breakdown_filings`, every row citing its filing;
            else the latest filing's NOT_FOUND.
        """
        step_id = step.get("id")
        fy = int(step["fy"])
        concept_family = step.get("concept_family") or "Revenue"
        original = bool(step.get("as_originally_reported", False))
        axes = BREAKDOWN_AXES[axis]
        own = current_column(rows, fy, fiscal_period, as_originally_reported=original)
        own_accessions = {r.get("accession_no") for r in own}
        filings = breakdown_filings(rows, fy, axes, fiscal_period, original)
        if not filings:
            notes = [
                f"no filing reports an undimensioned {concept_family} for "
                f"cik={step.get('cik')} FY{fy} {fiscal_period} with a {axis} split: "
                "nothing to partition"
            ]
            return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes)
        first_miss: ScreenResult | None = None
        skipped: list[str] = []
        for accession in filings:
            total_row = breakdown_total(rows, accession, fy, fiscal_period, original)
            if total_row is None:
                continue
            mine = [r for r in rows if r.get("accession_no") == accession]
            members = [
                {"member_label": r.get("segment_label")
                 or _local(r.get("segment_member")),
                 "value": r.get("value"), "fact_id": r.get("fact_id"),
                 "unit": r.get("unit"), "member": r.get("segment_member"),
                 "concept": r.get("concept")}
                for r in sorted(mine, key=lambda r: (-(float(r["value"])
                                                       if r.get("value") is not None
                                                       else float("-inf")),
                                                     str(r.get("fact_id"))))
                if self._is_member(r, axes, total_row)
            ]
            arcs = self._query_arcs(conn, accession)
            line_facts = self._query_line_facts(conn, total_row)
            result = self.resolve(step, self._facts(axis, total_row, members, arcs,
                                                    line_facts))
            if result.status != ScreenStatus.OK:
                if first_miss is None:
                    first_miss = result
                skipped.append(accession)
                continue
            for row in result.rows:
                row["citation"]["accession_no"] = accession
                row["fiscal_year"] = fy
            if accession not in own_accessions:
                result.resolution_notes.append(
                    f"FY{fy} {axis} split as the latest later report states it (S1): "
                    f"{accession}"
                )
            later_skipped = [a for a in skipped if a not in own_accessions]
            if later_skipped:
                result.resolution_notes.append(
                    "later filing(s) skipped, no split of the total on the axis: "
                    + ", ".join(later_skipped)
                )
            return result
        if first_miss is None:  # no listed filing had a total at the year's periods
            notes = [f"no undimensioned {concept_family} total for FY{fy}: nothing to "
                     "break down"]
            return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes)
        return first_miss

    @staticmethod
    def _is_member(row: dict, axes: Sequence[str], total_row: dict) -> bool:
        """Whether a row is a single-dimension member on ``axes`` at the total's period and
        unit."""
        identity = dimension_identity(row)
        return (identity is not None and len(identity) == 1 and identity[0][0] in axes
                and row.get("unit") == total_row.get("unit")
                and str(row.get("period_start")) == str(total_row.get("period_start"))
                and str(row.get("period_end")) == str(total_row.get("period_end")))

    def _query_filer_rows(self, conn, cik, concept_family, duration_kind) -> list[dict]:
        """The filer's stored rows of the measure from every filing, totals and members.

        The DEI period end is read from ``mv_dei_metadata`` as the lookup's S1 path does.
        """
        duration = "AND duration_kind = %(duration_kind)s" if duration_kind else ""
        sql = f"""
            SELECT fact_id, cik, accession_no, fiscal_year, fiscal_period, filed_date,
                   is_amendment, is_canonical_filing, concept, concept_pref_rank, value,
                   unit, period_start, period_end, segment_axis, segment_member,
                   segment_label, has_multiple_dimensions,
                   (SELECT d.document_period_end FROM mv_dei_metadata d
                     WHERE d.accession_no = mv_financial_facts.accession_no LIMIT 1)
                       AS document_period_end
            FROM   mv_financial_facts
            WHERE  cik = %(cik)s
              AND  concept_family = %(concept_family)s
              AND  period_type = 'duration' {duration}
              {publish_gate_clause(conn, "mv_financial_facts")}
        """
        return XbrlFactstoreScreen._fetch(conn, sql, {
            "cik": cik, "concept_family": concept_family, "duration_kind": duration_kind,
        })

    @staticmethod
    def _facts(
        axis: str, total_row: dict, members: list[dict], arcs: list[dict],
        line_facts: list[dict],
    ) -> dict:
        """The `resolve` input for one filer-year-axis, from the rows read for it.

        Args:
            axis: The plan's axis kind.
            total_row: The undimensioned total.
            members: The axis members' rows.
            arcs: The filing's presentation, definition and calculation arcs.
            line_facts: The filing's facts in the total's period and unit.

        Returns:
            ``{"total", "members", "lines"}`` (docs/specs/breakdown-reconciling-items.md).
        """
        total = {"value": total_row.get("value"), "fact_id": total_row.get("fact_id"),
                 "unit": total_row.get("unit"), "concept": _local(total_row.get("concept"))}
        names = [_local(m.get("member")) for m in members if m.get("member")]
        parents = member_parents(names, arcs)
        member_rows = [
            {"label": str(m.get("member_label") or ""), "value": m.get("value"),
             "fact_id": m.get("fact_id"), "unit": m.get("unit"),
             "member": _local(m.get("member")),
             "parents": parents.get(_local(m.get("member")), [])}
            for m in members
        ]
        member_concepts = {_local(m.get("concept")) for m in members if m.get("concept")}
        facts = [
            {"fact_id": f.get("fact_id"), "concept": _local(f.get("concept")),
             "label": (_clean(f.get("segment_label")) if f.get("segment_axis")
                       else f.get("concept_label") or _local(f.get("concept"))),
             "value": f.get("value"), "unit": f.get("unit"),
             "axis": _local(f.get("segment_axis")) if f.get("segment_axis") else None,
             "member": _local(f.get("segment_member")) if f.get("segment_member") else None}
            for f in line_facts
        ]
        lines = reconciling_candidates(
            total, member_concepts, _AXIS_LOCAL_NAMES[axis], arcs, facts
        )
        return {"total": total, "members": member_rows, "lines": lines}

    def resolve(self, step: dict, facts: dict) -> ScreenResult:
        """Resolve one `breakdown` step from the facts read for its filer-year-axis
        (docs/specs/breakdown-reconciling-items.md, C2).

        Args:
            step: ``{"id", "op": "breakdown", "cik", "fy", "concept_family", "axis"}``.
            facts: ``{"total", "members", "lines"}`` for the step's filer-year-axis.

        Returns:
            OK with the member rows and any reconciling row; NOT_FOUND stating the gap;
            ERROR for a malformed step.
        """
        step_id = step.get("id")
        malformed = self._malformed(step)
        if malformed is not None:
            return malformed
        axis = str(step.get("axis")).strip().lower()
        fy = step.get("fy")
        concept_family = step.get("concept_family") or "Revenue"
        total = facts.get("total")
        # Members without a value stay in: the hierarchy walks through them.
        members = [{**m, "value": None if m.get("value") is None else float(m["value"])}
                   for m in facts.get("members") or []]
        lines = [{**ln, "value": None if ln.get("value") is None else float(ln["value"])}
                 for ln in facts.get("lines") or []]
        valued = [m for m in members if m["value"] is not None]
        disclosure: dict[str, Any] = {"total": None, "reconciling": None, "set_aside": [],
                                      "gap": None}
        notes: list[str] = []
        if total is None or total.get("value") is None:
            notes.append(f"no undimensioned {concept_family} total for FY{fy}: "
                         "nothing to break down")
            return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes,
                                disclosure=disclosure)
        total_value = float(total["value"])
        unit = total.get("unit")
        disclosure["total"] = {"value": total_value, "fact_id": _str_id(total.get("fact_id")),
                               "unit": unit}

        chosen = choose_breakdown(members, total_value, lines) if valued else None
        if chosen is None:
            gap = breakdown_gap(members, total_value)
            disclosure["gap"] = gap
            if not valued:
                notes.append(f"no {axis} members tagged for {concept_family} FY{fy}")
            notes.append(
                f"no set of the {len(valued)} {axis} members (with or without one "
                f"reconciling line) makes the total {total_value:,.0f} {unit or ''}: the "
                f"members leave a gap of {gap:,.0f}"
                + ("" if not valued else ": " + ", ".join(
                    f"{_clean(m['label'])} {m['value']:,.0f}" for m in valued))
            )
            return ScreenResult(step_id, ScreenStatus.NOT_FOUND, resolution_notes=notes,
                                disclosure=disclosure)

        picked = [members[i] for i in chosen["members"]]
        rows = [self._row((_clean(m["label"]), m["value"]), m, fy) for m in picked]
        rows.sort(key=lambda r: -r["value"])
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            row["kind"] = "member"
        notes.append(
            f"{axis} breakdown: {len(rows)} of {len(valued)} members partition the "
            f"total {total_value:,.0f} {unit or ''}"
        )
        if chosen["line"] is not None:
            line = lines[chosen["line"]]
            label = _clean(line.get("label"))
            row = self._row((label, line["value"]), line, fy)
            row.update(rank=None, kind="reconciling")
            rows.append(row)
            disclosure["reconciling"] = {"label": label, "value": line["value"],
                                         "fact_id": _str_id(line.get("fact_id"))}
            notes.append(
                f"the members reach the total only with the reconciling line {label} "
                f"{line['value']:,.0f} (fact {row['fact_id']}), which the filer presents "
                "outside the axis"
            )
        for combo in chosen["set_aside"]:
            ordered = sorted((members[i] for i in combo), key=lambda m: -m["value"])
            labels = [_clean(m["label"]) for m in ordered]
            disclosure["set_aside"].append(labels)
            notes.append("another exact split was set aside: " + " + ".join(labels))
        return ScreenResult(step_id, ScreenStatus.OK, rows=rows, resolution_notes=notes,
                            disclosure=disclosure)

    @staticmethod
    def _malformed(step: dict) -> ScreenResult | None:
        """ERROR for a step that is not a breakdown or names an unknown axis."""
        step_id = step.get("id")
        if step.get("op") != "breakdown":
            return ScreenResult(
                step_id, ScreenStatus.ERROR,
                error=f"not a breakdown step: op={step.get('op')!r}",
            )
        axis = str(step.get("axis") or "").strip().lower()
        if axis not in VALID_AXES:
            return ScreenResult(
                step_id, ScreenStatus.ERROR,
                error=f"breakdown axis must be one of {sorted(VALID_AXES)}, got {axis!r}",
            )
        return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _query_total(self, conn, cik, fy, concept_family, fiscal_period, duration_kind):
        # Reporting currency first (the unit the filing also uses for comparatives), so
        # a 20-F's USD convenience translation never becomes the total.
        duration = "AND f.duration_kind = %(duration_kind)s" if duration_kind else ""
        sql = f"""
            SELECT f.value, f.fact_id, f.unit, f.concept, f.cik, f.accession_no,
                   f.period_start, f.period_end
            FROM   mv_financial_facts f
            WHERE  f.cik = %(cik)s AND f.fiscal_year = %(fy)s
              AND  f.concept_family = %(concept_family)s
              AND  f.segment_axis IS NULL AND f.period_type = 'duration'
              AND  f.is_canonical_filing AND f.is_current_period
              AND  f.fiscal_period = %(fiscal_period)s {duration}
              {publish_gate_clause(conn, "f")}
            ORDER  BY f.period_end DESC,
                   NOT EXISTS (
                     SELECT 1 FROM mv_financial_facts p
                      WHERE p.cik = f.cik AND p.fiscal_year = f.fiscal_year
                        AND p.concept_family = f.concept_family AND p.unit = f.unit
                        AND p.segment_axis IS NULL AND p.period_end < f.period_end
                   ),
                   f.concept_pref_rank ASC NULLS LAST, (f.value IS NULL), f.fact_id DESC
            LIMIT  1
        """
        rows = XbrlFactstoreScreen._fetch(conn, sql, {
            "cik": cik, "fy": fy, "concept_family": concept_family,
            "fiscal_period": fiscal_period, "duration_kind": duration_kind,
        })
        return rows[0] if rows else None

    def _query_members(
        self, conn, axis, cik, fy, concept_family, fiscal_period, duration_kind, unit
    ) -> list[dict]:
        params: dict[str, Any] = {
            "cik": cik, "fy": fy, "concept_family": concept_family,
            "fiscal_period": fiscal_period, "duration_kind": duration_kind, "unit": unit,
        }
        duration_g = "AND g.duration_kind = %(duration_kind)s" if duration_kind else ""
        duration_s = "AND s.duration_kind = %(duration_kind)s" if duration_kind else ""
        if axis == "geography":
            sql = f"""
                SELECT COALESCE(m.segment_label, regexp_replace(g.geo_member, '^.*:', ''))
                           AS member_label, g.value, g.fact_id, g.unit,
                       m.segment_member AS member, m.concept
                FROM   mv_geographic_facts g
                -- single-dimension facts only: a region x product cell is not a component
                JOIN   mv_financial_facts m ON m.fact_id = g.fact_id
                                           AND NOT COALESCE(m.has_multiple_dimensions, FALSE)
                WHERE  g.cik = %(cik)s AND g.fiscal_year = %(fy)s
                  AND  g.concept_family = %(concept_family)s
                  AND  g.fiscal_period = %(fiscal_period)s {duration_g}
                  AND  g.unit IS NOT DISTINCT FROM %(unit)s
                  {publish_gate_clause(conn, "g")}
                ORDER  BY g.value DESC, g.fact_id
            """
        else:
            params["axis_like"] = _SEGMENT_AXES[axis]
            sql = f"""
                SELECT s.segment_label AS member_label, s.value, s.fact_id, s.unit,
                       s.segment_member AS member, s.concept
                FROM   mv_segment_matrix s
                JOIN   mv_financial_facts m ON m.fact_id = s.fact_id
                                           AND NOT COALESCE(m.has_multiple_dimensions, FALSE)
                WHERE  s.cik = %(cik)s AND s.fiscal_year = %(fy)s
                  AND  s.concept_family = %(concept_family)s
                  AND  s.segment_axis LIKE %(axis_like)s
                  AND  s.fiscal_period = %(fiscal_period)s {duration_s}
                  AND  s.unit IS NOT DISTINCT FROM %(unit)s
                  {publish_gate_clause(conn, "s")}
                ORDER  BY s.value DESC, s.fact_id
            """
        return XbrlFactstoreScreen._fetch(conn, sql, params)

    def _query_arcs(self, conn, accession_no: str) -> list[dict]:
        """The filing's presentation, definition and calculation arcs, by local name."""
        sql = """
            SELECT ru.uri AS role, aq.local_name AS arc, pq.local_name AS parent,
                   cq.local_name AS child, rel.calculation_weight AS weight
            FROM   accession a
            JOIN   network n       ON n.accession_id = a.accession_id
            JOIN   qname aq        ON aq.qname_id = n.arc_qname_id
            JOIN   uri ru          ON ru.uri_id = n.extended_link_role_uri_id
            JOIN   relationship rel ON rel.network_id = n.network_id
            JOIN   element pe      ON pe.element_id = rel.from_element_id
            JOIN   qname pq        ON pq.qname_id = pe.qname_id
            JOIN   element ce      ON ce.element_id = rel.to_element_id
            JOIN   qname cq        ON cq.qname_id = ce.qname_id
            WHERE  a.filing_accession_number = %(accession_no)s
              AND  aq.local_name IN ('presentationArc', 'definitionArc', 'calculationArc')
            ORDER  BY n.network_id, rel.tree_sequence, rel.relationship_id
        """
        return XbrlFactstoreScreen._fetch(conn, sql, {"accession_no": accession_no})

    def _query_line_facts(self, conn, total_row: dict) -> list[dict]:
        """The total's filing's facts in its period and unit that could reconcile it:
        undimensioned, or single-dimension on ``ConsolidationItemsAxis``."""
        sql = """
            SELECT f.fact_id, f.concept, f.concept_label, f.value, f.unit,
                   f.segment_axis, f.segment_member, f.segment_label
            FROM   mv_financial_facts f
            WHERE  f.cik = %(cik)s AND f.accession_no = %(accession_no)s
              AND  f.unit IS NOT DISTINCT FROM %(unit)s
              AND  f.period_start IS NOT DISTINCT FROM %(period_start)s
              AND  f.period_end IS NOT DISTINCT FROM %(period_end)s
              AND  NOT COALESCE(f.has_multiple_dimensions, FALSE)
              AND  (f.segment_axis IS NULL OR f.segment_axis LIKE '%%:ConsolidationItemsAxis')
            ORDER  BY f.fact_id
        """
        return XbrlFactstoreScreen._fetch(conn, sql, {
            "cik": total_row.get("cik"), "accession_no": total_row.get("accession_no"),
            "unit": total_row.get("unit"),
            "period_start": total_row.get("period_start"),
            "period_end": total_row.get("period_end"),
        })

    @staticmethod
    def _row(labelled: tuple[str, float], raw: dict, fy: Any) -> dict:
        label, value = labelled
        fact_id = raw.get("fact_id")
        citation = Citation(
            fact_id=fact_id, accession_no=None, html_anchor=None, page_num=None,
            section_path=None,
        )
        return {
            "label": label,
            "value": value,
            "unit": raw.get("unit"),
            "fact_id": str(fact_id) if fact_id is not None else None,
            "fiscal_year": fy,
            "citation": citation.to_dict(),
        }
