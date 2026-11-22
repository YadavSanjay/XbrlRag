"""plan_executor — executes a typed `{"steps": [...]}` plan (design doc §2.2).

Walks the plan's steps in order and dispatches each by `op`:

    for step in plan.steps:
        if step.op == "lookup":     SQL on the fact store
        elif step.op == "compute":  calculator_tool(expr, bindings)
        elif step.op == "narrative": existing RAG branch              # next step
    collect step results

This implements the **branching/orchestration skeleton**, the deterministic
`lookup` branch (delegated to `XbrlFactstoreLookup`), and the `compute` branch.
`compute` is driven by `self.compute_fn` (type `ComputeFn`), which defaults to
`_default_compute` — a thin wrapper around the sandboxed `XbrlCalculatorTool` —
but can be overridden wholesale via the `compute_fn` constructor param (e.g. in
tests). The `narrative` branch is intentionally stubbed here — it returns a
`NOT_IMPLEMENTED` step result unless a `narrative_fn` is injected — so it can be
filled in next without touching the orchestration logic.

`compute` steps reference earlier step ids in their `expr`; the executor exposes
resolved numeric values through `bindings` (step_id -> value), which
`XbrlCalculatorTool.evaluate()` consumes. `lookup` values populate `bindings`
automatically as each step resolves.
"""
from __future__ import annotations

import ast
import collections.abc
import datetime
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.cagr import cagr_value, cagr_years
from src.retriever.xbrl.screen_aggregate import aggregate_screen_rows
from src.retriever.xbrl.step_period import aggregate_period, compute_period, lookup_period
from src.retriever.xbrl.xbrl_calculator_tool import (
    CalculatorError,
    XbrlCalculatorTool,
    expr_unit,
)
from src.retriever.xbrl.xbrl_factstore_lookup import (
    FactLookupResult,
    LookupStatus,
    XbrlFactstoreLookup,
    _norm_label,
    dimensions_match_ignoring_neutral,
)
from src.retriever.xbrl.year_length_answers import compared_year_lengths

logger = get_logger(__name__)


class StepStatus(Enum):
    OK = "OK"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    # A compute step with no meaningful value — a percentage change from a base <= 0,
    # reported "n/m" (docs/specs/pct-change-nm.md). Its `error` carries the reason.
    NOT_MEANINGFUL = "NOT_MEANINGFUL"
    ERROR = "ERROR"


class ExecutionStatus(Enum):
    OK = "OK"            # every step resolved cleanly
    PARTIAL = "PARTIAL"  # at least one step OK, but some failed/ambiguous/stubbed
    ERROR = "ERROR"      # the plan itself was malformed, or no step resolved


# A lookup step's LookupStatus maps onto a StepStatus 1:1.
_LOOKUP_TO_STEP_STATUS = {
    LookupStatus.OK: StepStatus.OK,
    LookupStatus.AMBIGUOUS: StepStatus.AMBIGUOUS,
    LookupStatus.NOT_FOUND: StepStatus.NOT_FOUND,
    LookupStatus.UNRESOLVED_DIMENSION: StepStatus.NOT_FOUND,
    LookupStatus.ERROR: StepStatus.ERROR,
}


@dataclass
class StepResult:
    step_id: str | None
    op: str
    status: StepStatus
    value: float | None = None
    unit: str | None = None
    citation: dict | None = None
    confidence: float = 0.0
    linked_chunk_ids: list[str] = field(default_factory=list)
    # A `screen` step has no single scalar value — it resolves to a ranked list
    # of per-company rows, each with its own value + fact_id + citation.
    rows: list[dict] = field(default_factory=list)
    # Screen only: filers left out because they have no figure for the requested
    # fiscal year, so the answer can say who has not reported it yet.
    excluded: list[dict] = field(default_factory=list)
    # Screen only: the universe ranked and every member not ranked, with why
    # (docs/specs/screen-universe.md); the pipeline states it in the answer.
    disclosure: dict | None = None
    error: str | None = None
    # Full underlying result object for the relevant branch (e.g. FactLookupResult).
    detail: Any = None
    # Intersection lookups only (docs/specs/multidim-recovery.md): the sibling steps this
    # one merged, and the hints it carried. The plan never names this step, so the
    # synthesizer learns what it is from these.
    derived_from: list[str] | None = None
    dimensions: dict | None = None
    # A lookup derived from its components (docs/specs/derived-gross-profit.md): no single
    # fact is the value, so the audited facts it rests on are named per input.
    component_fact_ids: dict | None = None
    # A lookup answered by a stated substitute (docs/specs/concept-family-coverage.md):
    # {"code", "note", "concept"}; the synthesizer states the note with the number.
    substitution: dict | None = None
    # Screen only: the size cap that trimmed the ranked rows, as the screen reports it
    # (docs/specs/screen-full-universe.md); an aggregate refuses trimmed rows.
    row_cap: dict | None = None
    # Aggregate only (docs/specs/screen-aggregates.md): the whole
    # `aggregate_screen_rows` result (fn, n, members, fact_ids, universe, exclusions ...).
    aggregate: dict | None = None
    # A Debt or DeferredRevenue lookup (docs/specs/s4-lookup-deferred-revenue-debt.md): the
    # S4 definition it follows ({"concept", "status", "basis", "disclosure", "readings",
    # "parts"}); the synthesizer states the disclosure, and every part's and reading's
    # fact id resolves to this step through ``component_fact_ids``.
    definition: dict | None = None
    # Cagr only (docs/specs/plan-cagr-and-ratios.md): {start, end, start_fy, end_fy, years,
    # start_value, end_value, fact_ids}, whenever start and end name earlier lookups.
    cagr: dict | None = None
    # An OK compute or cagr comparing periods of different lengths, or a growth screen
    # whose ranked members compare such years (docs/specs/period-year-length-answers.md):
    # `compared_year_lengths` / `screen_year_lengths` entries, each with its note.
    year_lengths: list[dict] = field(default_factory=list)
    # An OK lookup, compute or aggregate: its fiscal year and period, derived in code from
    # the facts it cites (`step_period`, docs/specs/verifier-period-evidence.md); the
    # verifier shows it as evidence.
    period: dict | None = None
    # A fourth-quarter lookup (docs/specs/q4-lookup.md): the lookup's ``q4`` record
    # (status ``filed`` or ``derived``, basis, fact_ids, stated period, note); the
    # synthesizer states its note with the number.
    q4: dict | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "step_id": self.step_id,
            "op": self.op,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "citation": self.citation,
            "confidence": round(self.confidence, 3),
            "linked_chunk_ids": self.linked_chunk_ids,
            "error": self.error,
        }
        if self.rows:
            d["rows"] = self.rows
        if self.excluded:
            d["excluded"] = self.excluded
        if self.disclosure is not None:
            d["disclosure"] = self.disclosure
        if self.derived_from is not None:
            d["derived_from"] = self.derived_from
            d["dimensions"] = self.dimensions
        if self.component_fact_ids:
            d["component_fact_ids"] = self.component_fact_ids
        if self.substitution:
            d["substitution"] = self.substitution
        if self.row_cap:
            d["row_cap"] = self.row_cap
        if self.aggregate is not None:
            d["aggregate"] = self.aggregate
        if self.definition is not None:
            d["definition"] = self.definition
        if self.cagr is not None:
            d["cagr"] = self.cagr
        if self.year_lengths:
            d["year_lengths"] = self.year_lengths
        if self.period is not None:
            d["period"] = self.period
        if self.q4 is not None:
            d["q4"] = self.q4
        return d


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    steps: list[StepResult] = field(default_factory=list)
    bindings: dict[str, float | None] = field(default_factory=dict)
    error: str | None = None

    def step(self, step_id: str) -> StepResult | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "bindings": self.bindings,
            "error": self.error,
        }


# Optional injected handlers for the not-yet-implemented branches:
#   compute_fn(step, bindings) -> StepResult
#   narrative_fn(step) -> StepResult
#   screen_fn(step) -> StepResult   (cross-filer screen; returns a rows list)
#   breakdown_fn(step) -> StepResult (one filer's partition by axis; rows list)
# Families a filer may not tag, derived from two that it does: family ->
# ((key, minuend family), (key, subtrahend family)). The keys name the inputs in
# component_fact_ids, as mv_derived_metrics does (docs/specs/derived-gross-profit.md).
DERIVED_LOOKUP_FAMILIES: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "GrossProfit": (("revenue", "Revenue"), ("cost_of_revenue", "CostOfRevenue")),
}

# Revenue sub-families a filer may not tag -> the word a member hint must contain for the
# lookup to be retried as Revenue (docs/specs/revenue-subfamily-fallback.md).
SUBFAMILY_WORDS: dict[str, str] = {"ServiceRevenue": "service", "ProductRevenue": "product"}

# Two members the planner glued into one hint ("Family of Apps and Service, Other") are
# split at this separator, at most this many variants per step
# (docs/specs/glued-hint-split.md).
_GLUE_SEPARATOR = re.compile(r"\s+and\s+", re.IGNORECASE)
MAX_GLUED_VARIANTS = 4

ComputeFn = Callable[[dict, dict[str, float | None]], StepResult]
NarrativeFn = Callable[[dict], StepResult]
ScreenFn = Callable[[dict], StepResult]
BreakdownFn = Callable[[dict], StepResult]


def defined_fact_ids(definition: dict | None) -> dict | None:
    """The fact ids a defined lookup rests on and discloses, by name (None when none).

    Each part is ``part_<n>``; each reading's facts are ``<reading>`` (one) or
    ``<reading>_<n>`` (several). A defined answer's citation is its first part.

    Args:
        definition: A lookup result's ``definition``, or None.

    Returns:
        ``{name: fact_id}`` for an answered definition, else None.
    """
    if not definition or definition.get("status") != "defined":
        return None
    out: dict[str, Any] = {}
    for n, part in enumerate(definition.get("parts") or [], start=1):
        out[f"part_{n}"] = part.get("fact_id")
    for reading in definition.get("readings") or []:
        ids = list(reading.get("fact_ids") or [])
        name = str(reading.get("reading"))
        if len(ids) == 1:
            out[name] = ids[0]
        else:
            for n, fid in enumerate(ids, start=1):
                out[f"{name}_{n}"] = fid
    return out or None


def q4_fact_ids(q4: dict | None) -> dict | None:
    """The fact ids a derived fourth quarter rests on, by name (None when none).

    A derived Q4 (docs/specs/q4-lookup.md) is the year minus the nine months: it cites
    the year's fact, and both resolve to its step as ``q4_year`` and ``q4_nine_month``.
    A filed Q4 is its own citation.

    Args:
        q4: A lookup result's ``q4`` record, or None.

    Returns:
        ``{"q4_year": id, "q4_nine_month": id}`` for a derived Q4, else None.
    """
    if not q4 or q4.get("status") != "derived":
        return None
    ids = list(q4.get("fact_ids") or [])
    if len(ids) != 2:
        return None
    return {"q4_year": ids[0], "q4_nine_month": ids[1]}


class PlanExecutor:
    """Executes a validated plan, dispatching each step by `op`.

    Example:
        >>> executor = PlanExecutor(factstore_lookup=XbrlFactstoreLookup.from_env())
        >>> result = executor.execute(plan)
        >>> result.bindings["tot26"]
    """

    def __init__(
        self,
        factstore_lookup: XbrlFactstoreLookup,
        compute_fn: ComputeFn | None = None,
        narrative_fn: NarrativeFn | None = None,
        screen_fn: ScreenFn | None = None,
        breakdown_fn: BreakdownFn | None = None,
    ):
        self.factstore_lookup = factstore_lookup
        self._calculator = XbrlCalculatorTool()
        self.compute_fn = compute_fn or self._default_compute
        self.narrative_fn = narrative_fn
        self.screen_fn = screen_fn
        self.breakdown_fn = breakdown_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, plan: dict) -> ExecutionResult:
        """Run every step of `plan` and collect results."""
        if not isinstance(plan, dict):
            return ExecutionResult(ExecutionStatus.ERROR, error="plan is not an object")
        if "error" in plan:
            return ExecutionResult(ExecutionStatus.ERROR, error=str(plan.get("error")))
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            return ExecutionResult(ExecutionStatus.ERROR, error="plan has no 'steps'")

        bindings: dict[str, float | None] = {}
        results: list[StepResult] = []
        # step_id -> provenance dict, accumulated as steps resolve, so a later
        # `compute` step's lineage can name the source fact(s) behind each input.
        provenance: dict[str, dict] = {}
        # step_id -> result, so a compute sees its inputs' status and unit, not only
        # their values (docs/specs/units-currency-integrity.md).
        resolved: dict[str, StepResult] = {}
        # step_id -> plan step, earlier steps only, so a cagr derives its years from its
        # inputs' planned periods (docs/specs/plan-cagr-and-ratios.md).
        planned: dict[str, dict] = {}

        for step in steps:
            result = self._execute_step(step, bindings, resolved, planned)
            if isinstance(step, dict):
                attach_period(step, result, resolved)
            results.append(result)
            if result.step_id is not None:
                bindings[result.step_id] = result.value
                resolved[result.step_id] = result
            if isinstance(step, dict) and isinstance(step.get("id"), str):
                planned.setdefault(step["id"], step)
            self._record_and_emit_lineage(step, result, bindings, provenance)

        # A question about one cell ("revenue for Asia Pacific and Subscriptions") the
        # planner split into one lookup per qualifier: try the cell itself.
        for merged in intersection_steps(steps, results):
            cell = self._execute_intersection(merged)
            if cell is None:
                continue
            attach_period(merged, cell, resolved)
            results.append(cell)
            bindings[merged["id"]] = cell.value
            self._record_and_emit_lineage(merged, cell, bindings, provenance)

        return ExecutionResult(
            status=self._overall_status(results),
            steps=results,
            bindings=bindings,
        )

    # ------------------------------------------------------------------
    # Numeric lineage (design doc R1 / §4.5) — audit trail for every number
    # ------------------------------------------------------------------

    def _record_and_emit_lineage(
        self,
        step: dict,
        result: StepResult,
        bindings: dict[str, float | None],
        provenance: dict[str, dict],
    ) -> None:
        """Record *result*'s provenance and emit its ``numeric_lineage`` event.

        Fires for both ``lookup`` and ``compute`` steps, on success and failure,
        so every number we might surface — and every arithmetic combination of
        them — is reconstructable after the fact from the event stream.
        """
        fact_id = result.citation.get("fact_id") if isinstance(result.citation, dict) else None
        if result.step_id is not None:
            provenance[result.step_id] = {
                "op": result.op,
                "status": result.status.value,
                "value": result.value,
                "fact_id": fact_id,
            }

        if result.op == "lookup":
            detail = result.detail
            notes = list(getattr(detail, "resolution_notes", []) or [])
            log_event(
                "numeric_lineage", op="lookup", step_id=result.step_id,
                status=result.status.value, value=result.value, unit=result.unit,
                fact_id=fact_id, confidence=round(result.confidence, 3),
                cik=step.get("cik"), fy=step.get("fy"),
                concept_family=step.get("concept_family"), dimensions=step.get("dimensions"),
                resolution_notes=notes, error=result.error,
            )
        elif result.op == "compute":
            expr = step.get("expr")
            ref_ids = XbrlCalculatorTool.referenced_step_ids(expr) if isinstance(expr, str) else []
            log_event(
                "numeric_lineage", op="compute", step_id=result.step_id,
                status=result.status.value, expr=expr, result=result.value,
                bindings={rid: bindings.get(rid) for rid in ref_ids},
                source_fact_ids={rid: provenance.get(rid, {}).get("fact_id") for rid in ref_ids},
                source_steps={rid: provenance.get(rid, {}).get("op") for rid in ref_ids},
                year_lengths=result.year_lengths, error=result.error,
            )
        elif result.op == "cagr":
            record = result.cagr or {}
            log_event(
                "numeric_lineage", op="cagr", step_id=result.step_id,
                status=result.status.value, start=step.get("start"), end=step.get("end"),
                years=record.get("years"), start_value=record.get("start_value"),
                end_value=record.get("end_value"), result=result.value, unit=result.unit,
                source_fact_ids=list(record.get("fact_ids") or []),
                year_lengths=result.year_lengths, error=result.error,
            )
        elif result.op == "aggregate":
            agg = result.aggregate or {}
            log_event(
                "numeric_lineage", op="aggregate", step_id=result.step_id,
                status=result.status.value, fn=step.get("fn"), of=step.get("of"),
                result=result.value, unit=result.unit, n=agg.get("n"),
                source_fact_ids=list(agg.get("fact_ids") or []), error=result.error,
            )

    # ------------------------------------------------------------------
    # Per-op dispatch
    # ------------------------------------------------------------------

    def _execute_step(
        self,
        step: dict,
        bindings: dict[str, float | None],
        resolved: Mapping[str, StepResult] | None = None,
        planned: Mapping[str, dict] | None = None,
    ) -> StepResult:
        op = step.get("op")
        step_id = step.get("id")

        if op == "lookup":
            result = self._execute_lookup(step)
            if (
                result.status is StepStatus.NOT_FOUND
                and step.get("concept_family") in DERIVED_LOOKUP_FAMILIES
            ):
                return self._derive_lookup(step, result)
            if result.status is StepStatus.NOT_FOUND:
                retry = subfamily_fallback_step(step)
                if retry is not None:
                    result = self._retry_as_revenue(retry, result)
            if result.status is StepStatus.NOT_FOUND:
                result = self._retry_split_hints(step, result)
            return result
        if op == "compute":
            return self._execute_compute(step, bindings, resolved or {}, planned or {})
        if op == "narrative":
            return self._execute_narrative(step)
        if op == "screen":
            return self._execute_screen(step)
        if op == "breakdown":
            return self._execute_breakdown(step)
        if op == "aggregate":
            return self._execute_aggregate(step, resolved or {})
        if op == "cagr":
            return self._execute_cagr(step, resolved or {}, planned or {})

        return StepResult(step_id, op or "?", StepStatus.ERROR, error=f"unknown op {op!r}")

    def _execute_lookup(self, step: dict) -> StepResult:
        lookup_result: FactLookupResult = self.factstore_lookup.lookup(step)
        status = _LOOKUP_TO_STEP_STATUS.get(lookup_result.status, StepStatus.ERROR)
        return StepResult(
            step_id=lookup_result.step_id,
            op="lookup",
            status=status,
            value=lookup_result.value,
            unit=lookup_result.unit,
            citation=lookup_result.citation.to_dict() if lookup_result.citation else None,
            confidence=lookup_result.confidence,
            linked_chunk_ids=lookup_result.linked_chunk_ids,
            error=lookup_result.error,
            detail=lookup_result,
            substitution=lookup_result.substitution,
            definition=lookup_result.definition,
            component_fact_ids=(
                defined_fact_ids(lookup_result.definition) or q4_fact_ids(lookup_result.q4)
            ),
            q4=lookup_result.q4,
        )

    def _derive_lookup(self, step: dict, not_found: StepResult) -> StepResult:
        """Derive a family the filer does not tag from the components it does.

        Meta, Alphabet and Amazon tag no GrossProfit, only Revenue and CostOfRevenue
        (docs/specs/derived-gross-profit.md). Each component is the same step with its own
        concept_family. The difference is used only when both resolve OK in one unit; a
        value is never guessed from one component or an ambiguous one.

        Args:
            step: The NOT_FOUND lookup step.
            not_found: Its result, returned unchanged when derivation is not possible.

        Returns:
            The derived OK result, or ``not_found``.
        """
        step_id = step.get("id")
        (plus_key, plus_family), (minus_key, minus_family) = DERIVED_LOOKUP_FAMILIES[
            step["concept_family"]
        ]
        parts = {
            key: self._execute_lookup(
                {**step, "id": f"{step_id}.{key}", "concept_family": family}
            )
            for key, family in ((plus_key, plus_family), (minus_key, minus_family))
        }
        fact_ids = {key: (r.citation or {}).get("fact_id") for key, r in parts.items()}
        units = {r.unit for r in parts.values()}
        accepted = (
            all(r.status is StepStatus.OK and r.value is not None for r in parts.values())
            and len(units) == 1
            and None not in units
        )
        log_event(
            "decision", decision="derived_family_fallback", step_id=step_id,
            concept_family=step["concept_family"],
            components={k: r.status.value for k, r in parts.items()},
            component_fact_ids=fact_ids, accepted=accepted,
        )
        if not accepted:
            return not_found
        value = self._calculator.evaluate(
            "plus - minus", {"plus": parts[plus_key].value, "minus": parts[minus_key].value}
        )
        return StepResult(
            step_id=step_id,
            op="lookup",
            status=StepStatus.OK,
            value=value,
            unit=units.pop(),
            citation=None,
            confidence=min(r.confidence for r in parts.values()),
            derived_from=[f"{step_id}.{plus_key}", f"{step_id}.{minus_key}"],
            component_fact_ids=fact_ids,
        )

    def _retry_as_revenue(self, retry: dict, not_found: StepResult) -> StepResult:
        """Run a sub-family lookup's Revenue retry; keep it only when it is OK.

        Args:
            retry: The step from `subfamily_fallback_step`.
            not_found: The sub-family lookup's result, returned when the retry is not OK.

        Returns:
            The retry's OK result, or ``not_found``.
        """
        retried = self._execute_lookup(retry)
        accepted = retried.status is StepStatus.OK
        log_event(
            "decision", decision="subfamily_fallback", step_id=retry.get("id"),
            dimensions=retry.get("dimensions"), status=retried.status.value,
            accepted=accepted, fact_id=(retried.citation or {}).get("fact_id"),
        )
        return retried if accepted else not_found

    def _retry_split_hints(self, step: dict, not_found: StepResult) -> StepResult:
        """Retry a NOT_FOUND lookup with a glued "X and Y" hint split in two.

        Each variant from `split_glued_hints` is one lookup; the first that is OK and
        whose best candidate matches the variant's hints one-to-one is kept
        (docs/specs/glued-hint-split.md).

        Args:
            step: The NOT_FOUND lookup step.
            not_found: Its result, returned when no variant is accepted.

        Returns:
            The accepted variant's result, or ``not_found``.
        """
        for variant in split_glued_hints(step.get("dimensions")):
            hints = list(variant.values())
            retried = self._execute_lookup({**step, "dimensions": variant})
            detail: FactLookupResult = retried.detail
            best = detail.candidates[0] if detail.candidates else None
            accepted = (
                retried.status is StepStatus.OK
                and best is not None
                and dimensions_match_ignoring_neutral(best.dimension_labels, hints)
            )
            log_event(
                "decision", decision="glued_hint_split", step_id=step.get("id"),
                hints=hints, status=retried.status.value, accepted=accepted,
                fact_id=best.fact_id if best is not None else None,
            )
            if accepted:
                return retried
        return not_found

    def _execute_intersection(self, merged: dict) -> StepResult | None:
        """Run one merged sibling lookup; keep it only on an exact one-to-one match.

        Args:
            merged: A step from `intersection_steps`.

        Returns:
            The step result to append, or None when the merged lookup is rejected.
        """
        hints = list(merged["dimensions"].values())
        result = self._execute_lookup(merged)
        detail: FactLookupResult = result.detail
        best = detail.candidates[0] if detail.candidates else None
        accepted = (
            result.status is StepStatus.OK
            and best is not None
            and dimensions_match_ignoring_neutral(best.dimension_labels, hints)
        )
        log_event(
            "decision", decision="intersection_fallback", step_id=merged["id"],
            hints=hints, status=result.status.value, accepted=accepted,
            fact_id=best.fact_id if best is not None else None,
        )
        if not accepted:
            return None
        result.derived_from = merged["id"].split("+")
        result.dimensions = merged["dimensions"]
        return result

    def _execute_compute(
        self,
        step: dict,
        bindings: dict[str, float | None],
        resolved: Mapping[str, StepResult],
        planned: Mapping[str, dict] | None = None,
    ) -> StepResult:
        """Run a compute step over OK inputs of one coherent unit.

        See docs/specs/units-currency-integrity.md. In order: an input step that is not
        OK makes the step ERROR (a lookup's AMBIGUOUS value is a guess, never an
        operand); then the inputs' units must combine (`expr_unit`: no two currencies,
        no sum of different units), else ERROR; then `compute_fn` (n/m, evaluate). An OK
        result carries the expression's unit.

        Args:
            step: The compute step.
            bindings: Step id to value of every earlier step.
            resolved: Step id to result of every earlier step.
            planned: Step id to plan step of every earlier step; an OK result's
                ``year_lengths`` compares its lookup inputs' cited periods
                (docs/specs/period-year-length-answers.md).

        Returns:
            The step's result.
        """
        step_id = step.get("id")
        expr = step.get("expr")
        ref_ids = XbrlCalculatorTool.referenced_step_ids(expr) if isinstance(expr, str) else []
        not_ok = [
            f"{rid} ({resolved[rid].status.value})"
            for rid in ref_ids
            if rid in resolved and resolved[rid].status is not StepStatus.OK
        ]
        if not_ok:
            error = (
                "input step(s) did not resolve OK, so have no value to use: "
                + ", ".join(not_ok)
            )
            logger.warning(f"compute step {step_id!r} refused: {error}")
            return StepResult(step_id=step_id, op="compute", status=StepStatus.ERROR, error=error)
        try:
            unit = expr_unit(
                str(expr) if expr is not None else "",
                {rid: resolved[rid].unit for rid in ref_ids if rid in resolved},
            )
        except CalculatorError as e:
            logger.warning(f"compute step {step_id!r} refused on units: {e}")
            return StepResult(step_id=step_id, op="compute", status=StepStatus.ERROR, error=str(e))
        result = self.compute_fn(step, bindings)
        if result.status is StepStatus.OK and result.unit is None:
            result.unit = unit
        if result.status is StepStatus.OK:
            result.year_lengths = compared_year_lengths(
                year_length_inputs(ref_ids, resolved, planned or {})
            )
        return result

    def _default_compute(self, step: dict, bindings: dict[str, float | None]) -> StepResult:
        """Default `compute_fn`: the sandboxed `XbrlCalculatorTool`."""
        step_id = step.get("id")
        expr = step.get("expr")
        # Checked before evaluate: (new - old) / old with old <= 0 evaluates to a
        # number with a misleading sign, which the synthesizer would then state.
        reason = self._calculator.not_meaningful_reason(expr, bindings)
        if reason is not None:
            logger.info(f"compute step {step_id!r} not meaningful: {reason}")
            return StepResult(
                step_id=step_id, op="compute", status=StepStatus.NOT_MEANINGFUL, error=reason
            )
        try:
            value = self._calculator.evaluate(expr, bindings)
        except CalculatorError as e:
            logger.warning(f"compute step {step_id!r} failed: {e}")
            return StepResult(step_id=step_id, op="compute", status=StepStatus.ERROR, error=str(e))

        return StepResult(step_id=step_id, op="compute", status=StepStatus.OK, value=value)

    @staticmethod
    def _execute_aggregate(step: dict, resolved: Mapping[str, StepResult]) -> StepResult:
        """Aggregate the rows of the earlier screen step that ``of`` names.

        See docs/specs/screen-aggregates.md, Requirements 7-8. The value is computed in
        code by `aggregate_screen_rows`; the step carries its whole result under
        ``aggregate``.

        Args:
            step: The aggregate step (``id``, ``of``, ``fn``).
            resolved: Step id to result of every earlier step.

        Returns:
            The step's result; ERROR naming ``of`` when it names no earlier step.
        """
        step_id = step.get("id")
        of = step.get("of")
        source = resolved.get(of) if isinstance(of, str) else None
        if source is None:
            return StepResult(
                step_id=step_id, op="aggregate", status=StepStatus.ERROR,
                error=f"aggregate 'of' {of!r} names no earlier step",
            )
        agg = aggregate_screen_rows(str(step.get("fn")), source.to_dict())
        status = StepStatus(agg["status"])
        return StepResult(
            step_id=step_id,
            op="aggregate",
            status=status,
            value=agg["value"] if status is StepStatus.OK else None,
            unit=agg["unit"],
            confidence=source.confidence if status is StepStatus.OK else 0.0,
            error=agg["error"],
            aggregate=agg,
        )

    @staticmethod
    def _execute_cagr(
        step: dict, resolved: Mapping[str, StepResult], planned: Mapping[str, dict]
    ) -> StepResult:
        """Annualize the growth between the earlier lookups ``start`` and ``end`` names.

        See docs/specs/plan-cagr-and-ratios.md, Requirement 4. In order: each input must be
        an earlier lookup; each must be OK (an AMBIGUOUS value is a guess, never an
        operand); one unit; one cik and concept_family; years derivable from the plan
        steps' periods (``cagr_years``); then ``cagr_value``. An OK value is ``pure``.

        Args:
            step: The cagr step (``id``, ``start``, ``end``).
            resolved: Step id to result of every earlier step.
            planned: Step id to plan step of every earlier step.

        Returns:
            The step's result, with the ``cagr`` record when both inputs are lookups.
        """
        step_id = step.get("id")

        def refuse(error: str, record: dict | None = None) -> StepResult:
            logger.warning(f"cagr step {step_id!r} refused: {error}")
            return StepResult(step_id=step_id, op="cagr", status=StepStatus.ERROR,
                              error=error, cagr=record)

        inputs: dict[str, tuple[dict, StepResult]] = {}
        for field_name in ("start", "end"):
            ref = step.get(field_name)
            plan_step = planned.get(ref) if isinstance(ref, str) else None
            result = resolved.get(ref) if isinstance(ref, str) else None
            if plan_step is None or result is None or plan_step.get("op") != "lookup":
                return refuse(f"cagr {field_name!r} {ref!r} names no earlier lookup step")
            inputs[field_name] = (plan_step, result)

        (start_step, start), (end_step, end) = inputs["start"], inputs["end"]
        years = cagr_years(start_step, end_step)

        def fact_id(result: StepResult) -> Any:
            return result.citation.get("fact_id") if isinstance(result.citation, dict) else None

        record = {
            "start": step.get("start"), "end": step.get("end"),
            "start_fy": start_step.get("fy"), "end_fy": end_step.get("fy"),
            "years": years, "start_value": start.value, "end_value": end.value,
            "fact_ids": [fact_id(start), fact_id(end)],
        }
        not_ok = [f"{r.step_id} ({r.status.value})" for r in (start, end)
                  if r.status is not StepStatus.OK]
        if not_ok:
            return refuse("input step(s) did not resolve OK, so have no value to use: "
                          + ", ".join(not_ok), record)
        if start.unit is None or end.unit is None or start.unit != end.unit:
            return refuse(f"cagr inputs must be in one unit, got start {start.unit!r} and "
                          f"end {end.unit!r} (no currency conversion)", record)
        for key in ("cik", "concept_family"):
            a, b = start_step.get(key), end_step.get(key)
            if (str(a) if a is not None else None) != (str(b) if b is not None else None):
                return refuse(f"cagr inputs differ in {key} ({a!r} vs {b!r})", record)
        if years is None:
            return refuse(
                f"cagr years cannot be derived from start fy {start_step.get('fy')!r} and "
                f"end fy {end_step.get('fy')!r} (the end must be later, in the same "
                f"fiscal_period and ytd)", record)

        outcome = cagr_value(end.value, start.value, years)
        status = StepStatus(outcome["status"])
        ok = status is StepStatus.OK
        if not ok:
            logger.info(f"cagr step {step_id!r} {status.value}: {outcome['error']}")
        refs = [str(step.get("start")), str(step.get("end"))]
        return StepResult(
            step_id=step_id, op="cagr", status=status,
            value=outcome["value"] if ok else None, unit="pure" if ok else None,
            confidence=min(start.confidence, end.confidence) if ok else 0.0,
            error=outcome["error"], cagr=record,
            year_lengths=(
                compared_year_lengths(year_length_inputs(refs, resolved, planned)) if ok else []
            ),
        )

    def _execute_narrative(self, step: dict) -> StepResult:
        if self.narrative_fn is not None:
            return self.narrative_fn(step)
        # Stub: narrative branch reuses the existing hybrid RAG path (next workstream).
        return StepResult(
            step_id=step.get("id"), op="narrative", status=StepStatus.NOT_IMPLEMENTED,
            error="narrative branch not implemented yet (RAG path pending)",
        )

    def _execute_screen(self, step: dict) -> StepResult:
        if self.screen_fn is not None:
            return self.screen_fn(step)
        return StepResult(
            step_id=step.get("id"), op="screen", status=StepStatus.NOT_IMPLEMENTED,
            error="screen branch not wired (inject screen_fn)",
        )

    def _execute_breakdown(self, step: dict) -> StepResult:
        if self.breakdown_fn is not None:
            return self.breakdown_fn(step)
        return StepResult(
            step_id=step.get("id"), op="breakdown", status=StepStatus.NOT_IMPLEMENTED,
            error="breakdown branch not wired (inject breakdown_fn)",
        )

    @staticmethod
    def _overall_status(results: list[StepResult]) -> ExecutionStatus:
        if not results:
            return ExecutionStatus.ERROR

        has_ok = any(r.status == StepStatus.OK for r in results)
        has_fail = any(
            r.status in (
                StepStatus.ERROR, StepStatus.NOT_FOUND, StepStatus.AMBIGUOUS,
                StepStatus.NOT_MEANINGFUL,
            )
            for r in results
        )
        # A NOT_IMPLEMENTED step (currently only the narrative stub) means the
        # plan is not yet fully answerable, so it keeps the run out of "OK".
        has_stub = any(r.status == StepStatus.NOT_IMPLEMENTED for r in results)

        if not has_fail and not has_stub:
            return ExecutionStatus.OK
        if has_ok or has_stub:
            return ExecutionStatus.PARTIAL
        return ExecutionStatus.ERROR


def first_reference_order(expr: str) -> list[str]:
    """The step ids *expr* references, in the order it first references them.

    Args:
        expr: A compute step's arithmetic expression.

    Returns:
        ``XbrlCalculatorTool.referenced_step_ids(expr)`` reordered by first position in
        *expr*; ``[]`` for an empty or unparseable expression.
    """
    ids = set(XbrlCalculatorTool.referenced_step_ids(expr))
    if not ids:
        return []
    names = [n for n in ast.walk(ast.parse(expr, mode="eval"))
             if isinstance(n, ast.Name) and n.id in ids]
    names.sort(key=lambda n: (n.lineno, n.col_offset))
    return list(dict.fromkeys(n.id for n in names))


def attach_period(step: dict, result: StepResult, resolved: Mapping[str, StepResult]) -> None:
    """Sets ``result.period`` for an OK lookup, compute or aggregate.

    See docs/specs/verifier-period-evidence.md, Wiring 1: a lookup's from its plan step and
    cited fact (none cited, as for a derived lookup, leaves only the plan's fiscal year); a
    compute's from its referenced steps' own periods in first-reference order; an
    aggregate's from the screen it aggregates. A non-OK step gets none.

    Args:
        step: The plan step *result* answers.
        result: Its result, updated in place.
        resolved: Step id to result of every earlier step.
    """
    if result.status is not StepStatus.OK:
        result.period = None
        return
    if result.op == "lookup":
        result.period = lookup_period(step, *cited_period(result))
    elif result.op == "compute":
        expr = step.get("expr")
        ref_ids = first_reference_order(expr) if isinstance(expr, str) else []
        result.period = compute_period([
            (rid, resolved[rid].period if rid in resolved else None) for rid in ref_ids
        ])
    elif result.op == "aggregate":
        of = step.get("of")
        source = resolved.get(of) if isinstance(of, str) else None
        result.period = aggregate_period(
            source.to_dict() if source is not None else None, result.aggregate or {}
        )


def cited_period(result: StepResult) -> tuple[str | None, str | None]:
    """The stored ``(period_start, period_end)`` of a lookup's cited fact.

    The cited fact is the candidate of the lookup's ``FactLookupResult`` whose
    ``fact_id`` is the citation's (docs/specs/period-year-length-answers.md, 6.1).

    Args:
        result: A step result.

    Returns:
        The cited candidate's stored start and end; ``(None, None)`` when no single
        candidate is cited (a derived or summed lookup, or no lookup detail). A Q4
        lookup gives its fourth quarter (stored form), whichever fact it cites.
    """
    fact_id = result.citation.get("fact_id") if isinstance(result.citation, dict) else None
    detail = result.detail
    if fact_id is None or not isinstance(detail, FactLookupResult):
        return None, None
    if detail.q4 is not None and detail.q4.get("period_end"):
        # A Q4's period is the fourth quarter, not its cited year row's (q4-lookup):
        # the stated end back in the stored (next-midnight) form.
        stored_end = datetime.date.fromisoformat(detail.q4["period_end"]) + datetime.timedelta(
            days=1
        )
        return detail.q4.get("period_start"), stored_end.isoformat()
    for candidate in detail.candidates:
        if str(candidate.fact_id) == str(fact_id):
            return candidate.period_start, candidate.period_end
    return None, None


def year_length_inputs(
    ref_ids: list[str],
    resolved: Mapping[str, StepResult],
    planned: Mapping[str, dict],
) -> list[dict[str, Any]]:
    """The ``compared_year_lengths`` inputs of a compute or cagr step.

    Args:
        ref_ids: The step ids the step references, in its order.
        resolved: Step id to result of every earlier step.
        planned: Step id to plan step of every earlier step.

    Returns:
        One input per referenced step that has both a result and a plan step.
    """
    inputs: list[dict[str, Any]] = []
    for rid in ref_ids:
        result, plan_step = resolved.get(rid), planned.get(rid)
        if result is None or plan_step is None:
            continue
        start, end = cited_period(result)
        citation = result.citation if isinstance(result.citation, dict) else {}
        inputs.append({
            "step_id": rid, "op": result.op, "status": result.status.value,
            "cik": plan_step.get("cik"), "concept_family": plan_step.get("concept_family"),
            "fiscal_period": plan_step.get("fiscal_period"), "ytd": plan_step.get("ytd"),
            "fy": plan_step.get("fy"), "fact_id": citation.get("fact_id"),
            "period_start": start, "period_end": end,
        })
    return inputs


def subfamily_fallback_step(step: dict) -> dict | None:
    """The Revenue retry for a sub-family lookup the filer does not tag, or None.

    See docs/specs/revenue-subfamily-fallback.md. Retried only when a member hint names
    the sub-family ("Service, Other" for ServiceRevenue), so the hint still carries the
    qualifier the retry drops: "ServiceRevenue for the Americas" is never retried as
    total Americas revenue.

    Args:
        step: A plan step; never modified.

    Returns:
        A copy of ``step`` with ``concept_family`` set to ``"Revenue"``, or None.
    """
    word = SUBFAMILY_WORDS.get(str(step.get("concept_family")))
    dims = step.get("dimensions")
    if step.get("op") != "lookup" or word is None or not isinstance(dims, dict):
        return None
    if not any(word in str(hint).lower() for hint in dims.values()):
        return None
    return {**step, "concept_family": "Revenue"}


def split_glued_hints(dimensions: object) -> list[dict]:
    """Variants of a lookup's dimensions with one glued "X and Y" hint split in two.

    See docs/specs/glued-hint-split.md. Each separator in each hint gives one variant,
    with that hint replaced by ``<key>_a`` / ``<key>_b``. Those keys map to no axis,
    so each part matches a member on whichever axis the filer reports it.

    Args:
        dimensions: A lookup step's ``dimensions``; never modified.

    Returns:
        Up to `MAX_GLUED_VARIANTS` variants in hint order, then separator order; ``[]``
        when ``dimensions`` is not a non-empty dict.
    """
    if not isinstance(dimensions, dict) or not dimensions:
        return []
    variants: list[dict] = []
    for key, value in dimensions.items():
        text = str(value)
        left_key, right_key = f"{key}_a", f"{key}_b"
        if left_key in dimensions or right_key in dimensions:
            continue
        for match in _GLUE_SEPARATOR.finditer(text):
            left, right = text[: match.start()].strip(), text[match.end():].strip()
            if not left or not right:
                continue
            variant = {k: v for k, v in dimensions.items() if k != key}
            variant[left_key], variant[right_key] = left, right
            variants.append(variant)
    return variants[:MAX_GLUED_VARIANTS]


def intersection_steps(
    steps: collections.abc.Sequence[dict], results: collections.abc.Sequence[StepResult]
) -> list[dict]:
    """Merged lookups for groups of sibling lookups that did not all resolve.

    See docs/specs/multidim-recovery.md (C4).

    Args:
        steps: The plan's steps, in plan order.
        results: The step results, in the same order.

    Returns:
        One merged lookup step per qualifying group, in the order of its first sibling.
    """
    groups: dict[tuple, list[int]] = {}
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("op") != "lookup":
            continue
        dims = step.get("dimensions")
        if not isinstance(dims, dict) or not dims:
            continue
        key = (
            step.get("cik"),
            step.get("fy"),
            step.get("concept_family"),
            (step.get("fiscal_period") or "FY").upper(),
            bool(step.get("ytd", False)),
        )
        groups.setdefault(key, []).append(i)

    merged: list[dict] = []
    for (cik, fy, family, fiscal_period, ytd), members in groups.items():
        # All siblings resolved: they were separate questions, not one split cell.
        if len(members) < 2 or all(results[i].status is StepStatus.OK for i in members):
            continue
        hints: list[str] = []
        seen: set[str] = set()
        for i in members:
            for value in steps[i]["dimensions"].values():
                hint = str(value).strip()
                if _norm_label(hint) and _norm_label(hint) not in seen:
                    seen.add(_norm_label(hint))
                    hints.append(hint)
        if len(hints) < 2:
            continue
        merged.append({
            "id": "+".join(str(steps[i].get("id")) for i in members),
            "op": "lookup",
            "cik": cik,
            "fy": fy,
            "concept_family": family,
            "fiscal_period": fiscal_period,
            "ytd": ytd,
            # No axis in the keys: the full one-to-one match is the guard, not the axis.
            "dimensions": {f"member_{n}": h for n, h in enumerate(hints, start=1)},
        })
    return merged


if __name__ == "__main__":
    import json

    from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

    # NVIDIA worked example (Plan_Extractor doc §2): Data Center vs total revenue, FY2026.
    plan = {
        "steps": [
            {"id": "dc26", "op": "lookup", "cik": "0001045810", "ticker": "NVDA",
             "fy": 2026, "concept_family": "Revenue",
             "dimensions": {"product": "data center"}, "section_hint": "Disaggregated Revenue"},
            {"id": "tot26", "op": "lookup", "cik": "0001045810", "ticker": "NVDA",
             "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
             "section_hint": "Income Statement"},
            {"id": "share26", "op": "compute", "expr": "dc26 / tot26"},
        ]
    }
    executor = PlanExecutor(factstore_lookup=XbrlFactstoreLookup.from_env())
    print(json.dumps(executor.execute(plan).to_dict(), indent=2, default=str))
