"""Plan validator (design doc §2.2.3, point 3).

Pure-Python, no LLM, no DB required. Runs against the JSON `{"steps": [...]}`
plan produced by `plan_generator` before it is handed off to `plan_executor`.
The prompt's own "RULES & VALIDATION" section
(prompts/templates/xbrl/xbrl_query_plan_generator.txt) is necessary but not
sufficient — LLMs still drift — so this module re-checks the structural
invariants in Python:

- every `step.id` is unique, lowercase, <= 16 chars
- every `compute.expr` references only step ids defined *earlier* in the plan
  (rejects cycles/forward-refs) and contains only `+ - * / ( )` and identifiers
- `lookup.dimensions` is exactly `"NONE"` or a `{axis: member}` object
- `lookup.concept_family` is one of the known CONCEPT_FAMILIES (catches
  hallucinated families before they hit the resolver)
- required fields are present per `op` type
- step count is within the prompt's stated bound (<= 50)

`validate()` returns a list of human-readable error strings (empty = valid),
suitable for both programmatic checks and feeding back to the LLM as
"Your previous response had error: ...".
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, List, Optional, Set

from src.retriever.xbrl.cagr import cagr_years
from src.retriever.xbrl.named_company_screen import member_list
from src.retriever.xbrl.screen_disclosure import COVERED as COVERED_UNIVERSE
from src.retriever.xbrl.xbrl_factstore_screen import (
    DERIVED_METRIC_ALIASES,
    DERIVED_METRICS,
    resolve_derived_metric,
    universe_groups,
)

VALID_OPS = {"lookup", "compute", "narrative", "screen", "breakdown", "aggregate", "cagr"}
# `aggregate` fns over a screen's rows (docs/specs/screen-aggregates.md).
AGGREGATE_FNS = ("sum", "median", "mean", "count")
# `breakdown` axis kinds (docs/specs/breakdown-op.md).
BREAKDOWN_AXES = {"segment", "product", "geography"}

# Fiscal periods a lookup/screen step may target. 'FY' (default) is annual;
# Q1-Q4 select a 10-Q quarter. `ytd` (bool) picks the cumulative year-to-date
# figure for Q2/Q3 rather than the discrete quarter.
VALID_FISCAL_PERIODS = {"FY", "Q1", "Q2", "Q3", "Q4"}

# Cross-filer screen metric kinds. "value"/"pct_of_total" screen a concept_family;
# "derived:<MetricName>" screens a pre-computed row in mv_derived_metrics.
SCREEN_METRIC_KINDS = {"value", "pct_of_total"}
SCREEN_ORDERS = {"asc", "desc"}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Optional lookup `unit`: an ISO 4217 currency code (docs/specs/unit-aware-lookup.md).
ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
COMPUTE_EXPR_ALLOWED_RE = re.compile(r"^[\w\s.+\-*/()]+$")
IDENTIFIER_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")

DEFAULT_MAX_STEPS = 50

# Mirrors the CONCEPT_FAMILIES taxonomy in
# prompts/templates/xbrl/xbrl_query_plan_generator.txt — keep in sync.
# In production this can be overridden with the live set of
# `concept_family` values from `concept_mapping` (scripts/ddl/layer_0/concept_mapping.sql).
KNOWN_CONCEPT_FAMILIES: set[str] = {
    # Income statement
    "Revenue", "ProductRevenue", "ServiceRevenue", "CostOfRevenue", "GrossProfit",
    "OperatingExpenses", "ResearchAndDevelopment", "SellingGeneralAndAdministrative",
    "SellingAndMarketing", "GeneralAndAdministrative", "RestructuringCharges",
    "DepreciationAndAmortization", "OperatingIncome", "InterestExpense",
    "InterestIncome", "OtherNonOperatingIncome", "PretaxIncome", "TaxExpense",
    "IncomeFromContinuingOperations", "IncomeFromDiscontinuedOperations",
    "NetIncome", "NetIncomeToCommon", "ComprehensiveIncome",
    # Per-share & share counts
    "EarningsPerShareBasic", "EarningsPerShareDiluted", "WeightedAverageSharesBasic",
    "WeightedAverageSharesDiluted", "DividendsPerShare", "SharesOutstanding",
    # Balance sheet
    "TotalAssets", "CurrentAssets", "CashAndEquivalents", "ShortTermInvestments",
    "AccountsReceivable", "Inventory", "PropertyPlantAndEquipment", "Goodwill",
    "IntangibleAssets", "TotalLiabilities", "CurrentLiabilities", "AccountsPayable",
    "DeferredRevenue", "Debt", "StockholdersEquity", "RetainedEarnings",
    # Cash flow statement
    "CashFromOperations", "CashFromInvesting", "CashFromFinancing", "Capex",
    "StockRepurchases", "DividendsPaid", "DebtIssued", "DebtRepaid",
    "ShareBasedCompensation", "FreeCashFlow",
    # Ratios, rates & disclosure metrics
    "ConcentrationRiskPercentage", "EffectiveTaxRate", "NumberOfSegments", "EmployeeCount",
}


class PlanValidator:
    """Validates a `{"steps": [...]}` plan dict produced by `plan_generator`.

    Example:
        >>> validator = PlanValidator()
        >>> errors = validator.validate({"steps": [...]})
        >>> if errors:
        ...     feedback = "Your previous response had error: " + "; ".join(errors)
    """

    def __init__(
        self,
        known_concept_families: Iterable[str] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ):
        self.known_concept_families = set(known_concept_families) if known_concept_families is not None else KNOWN_CONCEPT_FAMILIES
        self.max_steps = max_steps

    def validate(self, plan: Any) -> list[str]:
        """Return a list of validation error strings (empty list = valid)."""
        if not isinstance(plan, dict):
            return ["plan must be a JSON object"]
        if "error" in plan:
            # plan_generator's own error contract - not this validator's concern.
            return []
        if "steps" not in plan:
            return ["plan must contain a 'steps' array (or an 'error' object)"]

        steps = plan["steps"]
        if not isinstance(steps, list) or not steps:
            return ["'steps' must be a non-empty array"]

        errors: list[str] = []
        if len(steps) > self.max_steps:
            errors.append(f"plan has {len(steps)} steps, exceeds max of {self.max_steps}")

        seen_ids: set[str] = set()
        # Earlier step id -> its op, so an `aggregate` can check that `of` is a screen.
        seen_ops: dict[str, Any] = {}
        # Earlier step id -> its dict, so a `cagr` can derive its years from its inputs
        # (docs/specs/plan-cagr-and-ratios.md, Requirement 3).
        seen_steps: dict[str, dict] = {}
        for index, step in enumerate(steps):
            errors.extend(self._validate_step(step, index, seen_ids, seen_ops, seen_steps))
            if isinstance(step, dict):
                sid = step.get("id")
                if isinstance(sid, str):
                    seen_ids.add(sid)
                    seen_ops.setdefault(sid, step.get("op"))
                    seen_steps.setdefault(sid, step)

        return errors

    # ------------------------------------------------------------------
    # Per-step validation
    # ------------------------------------------------------------------

    def _validate_step(
        self,
        step: Any,
        index: int,
        seen_ids_so_far: set[str],
        seen_ops: Mapping[str, Any] | None = None,
        seen_steps: Mapping[str, dict] | None = None,
    ) -> list[str]:
        if not isinstance(step, dict):
            return [f"step[{index}] is not an object"]

        errors: list[str] = []
        sid = step.get("id")

        if not isinstance(sid, str) or not sid:
            errors.append(f"step[{index}] is missing a valid 'id'")
        elif sid in seen_ids_so_far:
            errors.append(f"step[{index}] id '{sid}' is not unique")
        elif not STEP_ID_RE.match(sid):
            errors.append(f"step[{index}] id '{sid}' must be lowercase alphanumeric/underscore, max 16 chars, starting with a letter")

        op = step.get("op")
        if op not in VALID_OPS:
            errors.append(f"step[{index}] (id={sid!r}) has invalid 'op' {op!r}; must be one of {sorted(VALID_OPS)}")
            return errors  # remaining checks depend on a known op

        if op == "lookup":
            errors.extend(self._validate_lookup(step, index, sid))
        elif op == "compute":
            errors.extend(self._validate_compute(step, index, sid, seen_ids_so_far))
        elif op == "narrative":
            errors.extend(self._validate_narrative(step, index, sid))
        elif op == "screen":
            errors.extend(self._validate_screen(step, index, sid))
        elif op == "breakdown":
            errors.extend(self._validate_breakdown(step, index, sid))
        elif op == "aggregate":
            errors.extend(self._validate_aggregate(step, index, sid, seen_ops or {}))
        elif op == "cagr":
            errors.extend(self._validate_cagr(step, index, sid, seen_steps or {}))

        return errors

    @staticmethod
    def _validate_cagr(
        step: dict, index: int, sid: Any, seen_steps: Mapping[str, dict]
    ) -> list[str]:
        """A `cagr` annualizes the growth between two earlier lookups of one metric.

        See docs/specs/plan-cagr-and-ratios.md, Requirement 3: `start` and `end` each name
        an earlier lookup (one error per field); the years must be derivable from their
        fiscal years and period kinds; both inputs share `cik` and `concept_family`; the
        step carries no `years` key (the years are derived, never planned).

        Args:
            step: The cagr step.
            index: Its position in the plan.
            sid: Its id.
            seen_steps: Earlier step id to step dict.

        Returns:
            The step's errors.
        """
        errors: list[str] = []
        inputs: dict[str, dict] = {}
        for field in ("start", "end"):
            ref = step.get(field)
            earlier = seen_steps.get(ref) if isinstance(ref, str) else None
            if earlier is not None and earlier.get("op") == "lookup":
                inputs[field] = earlier
                continue
            if not isinstance(ref, str) or not ref:
                why = "is missing"
            elif earlier is None:
                why = "names no earlier step"
            else:
                why = f"names a {earlier.get('op')!r} step, not a lookup"
            errors.append(
                f"step[{index}] (id={sid!r}, op=cagr) {field!r} {ref!r} {why}; it must be "
                f"the id of an earlier lookup step"
            )
        if len(inputs) == 2:
            start, end = inputs["start"], inputs["end"]
            if cagr_years(start, end) is None:
                errors.append(
                    f"step[{index}] (id={sid!r}, op=cagr) the years between start "
                    f"{start.get('id')!r} (fy={start.get('fy')!r}) and end {end.get('id')!r} "
                    f"(fy={end.get('fy')!r}) cannot be derived: both need an integer fy, the "
                    f"end's later, and the same fiscal_period and ytd"
                )
            start_cik, end_cik = start.get("cik"), end.get("cik")
            if start_cik is not None and end_cik is not None and str(start_cik) != str(end_cik):
                errors.append(
                    f"step[{index}] (id={sid!r}, op=cagr) start and end have a different "
                    f"cik ({start_cik!r} vs {end_cik!r}); a CAGR is of one company"
                )
            if start.get("concept_family") != end.get("concept_family"):
                errors.append(
                    f"step[{index}] (id={sid!r}, op=cagr) start and end have a different "
                    f"concept_family ({start.get('concept_family')!r} vs "
                    f"{end.get('concept_family')!r}); a CAGR is of one metric"
                )
        if "years" in step:
            errors.append(
                f"step[{index}] (id={sid!r}, op=cagr) remove 'years': the years are derived "
                f"from the start and end steps' fy, never planned"
            )
        return errors

    @staticmethod
    def _validate_aggregate(
        step: dict, index: int, sid: Any, seen_ops: Mapping[str, Any]
    ) -> list[str]:
        """An `aggregate` sums (or takes the median, mean or count of) a screen's rows.

        See docs/specs/screen-aggregates.md, Requirement 5: `of` must be the id of an
        earlier `screen` step and `fn` one of AGGREGATE_FNS; each is at most one error.

        Args:
            step: The aggregate step.
            index: Its position in the plan.
            sid: Its id.
            seen_ops: Earlier step id to op.

        Returns:
            The step's errors.
        """
        errors: list[str] = []
        of = step.get("of")
        if not isinstance(of, str) or seen_ops.get(of) != "screen":
            if not isinstance(of, str) or not of:
                why = "is missing"
            elif of not in seen_ops:
                why = "names no earlier step"
            else:
                why = f"names a {seen_ops.get(of)!r} step, not a screen"
            errors.append(
                f"step[{index}] (id={sid!r}, op=aggregate) 'of' {of!r} {why}; it must be "
                f"the id of an earlier screen step"
            )
        fn = step.get("fn")
        if fn not in AGGREGATE_FNS:
            errors.append(
                f"step[{index}] (id={sid!r}, op=aggregate) 'fn' must be one of "
                f"{list(AGGREGATE_FNS)}, got {fn!r}"
            )
        return errors

    def _validate_lookup(self, step: dict, index: int, sid: Any) -> list[str]:
        errors: list[str] = []

        for field in ("cik", "fy", "concept_family", "dimensions", "section_hint"):
            if field not in step:
                errors.append(f"step[{index}] (id={sid!r}, op=lookup) missing required field '{field}'")

        concept_family = step.get("concept_family")
        if concept_family is not None and self.known_concept_families and concept_family not in self.known_concept_families:
            errors.append(
                f"step[{index}] (id={sid!r}) concept_family {concept_family!r} is not a "
                f"recognized CONCEPT_FAMILIES value"
            )

        dimensions = step.get("dimensions")
        if "dimensions" in step and dimensions != "NONE" and not isinstance(dimensions, dict):
            errors.append(
                f"step[{index}] (id={sid!r}) 'dimensions' must be the string \"NONE\" or "
                f"an {{axis: member}} object, got {dimensions!r}"
            )

        fy = step.get("fy")
        if fy is not None and not isinstance(fy, int):
            errors.append(f"step[{index}] (id={sid!r}) 'fy' must be an integer, got {fy!r}")

        unit = step.get("unit")
        if unit is not None and not (isinstance(unit, str) and ISO_CURRENCY_RE.match(unit)):
            errors.append(
                f"step[{index}] (id={sid!r}) 'unit' must be an ISO 4217 code like \"USD\", "
                f"got {unit!r}"
            )

        errors.extend(self._validate_period(step, index, sid))
        return errors

    @staticmethod
    def _validate_period(step: dict, index: int, sid: Any) -> list[str]:
        """Optional fiscal_period / ytd / point-in-time fields (lookup + screen)."""
        errors: list[str] = []
        fp = step.get("fiscal_period")
        if fp is not None and str(fp).upper() not in VALID_FISCAL_PERIODS:
            errors.append(
                f"step[{index}] (id={sid!r}) 'fiscal_period' must be one of "
                f"{sorted(VALID_FISCAL_PERIODS)}, got {fp!r}"
            )
        ytd = step.get("ytd")
        if ytd is not None and not isinstance(ytd, bool):
            errors.append(f"step[{index}] (id={sid!r}) 'ytd' must be a boolean, got {ytd!r}")
        aor = step.get("as_originally_reported")
        if aor is not None and not isinstance(aor, bool):
            errors.append(f"step[{index}] (id={sid!r}) 'as_originally_reported' must be a boolean, got {aor!r}")
        as_of = step.get("as_of")
        if as_of is not None and not (isinstance(as_of, str) and ISO_DATE_RE.match(as_of)):
            errors.append(f"step[{index}] (id={sid!r}) 'as_of' must be an ISO date 'YYYY-MM-DD', got {as_of!r}")
        return errors

    def _validate_compute(self, step: dict, index: int, sid: Any, seen_ids_so_far: set[str]) -> list[str]:
        errors: list[str] = []

        expr = step.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            errors.append(f"step[{index}] (id={sid!r}, op=compute) missing/invalid 'expr'")
            return errors

        if not COMPUTE_EXPR_ALLOWED_RE.match(expr):
            errors.append(
                f"step[{index}] (id={sid!r}) expr {expr!r} contains characters outside "
                f"the allowed set (identifiers, digits, '.', '+', '-', '*', '/', '(', ')')"
            )

        referenced_ids = set(IDENTIFIER_RE.findall(expr))
        forward_or_undefined = referenced_ids - seen_ids_so_far
        if forward_or_undefined:
            errors.append(
                f"step[{index}] (id={sid!r}) expr {expr!r} references undefined or "
                f"forward-referenced step ids: {sorted(forward_or_undefined)}"
            )

        return errors

    def _validate_narrative(self, step: dict, index: int, sid: Any) -> list[str]:
        errors: list[str] = []
        if "section_hint" not in step:
            errors.append(f"step[{index}] (id={sid!r}, op=narrative) missing required field 'section_hint'")
        return errors

    def _validate_breakdown(self, step: dict, index: int, sid: Any) -> list[str]:
        """A single-filer `breakdown` splits one metric by segment, product or geography.

        Required: cik, fy, concept_family, axis (one of BREAKDOWN_AXES).
        """
        errors: list[str] = []
        for field in ("cik", "fy", "concept_family", "axis"):
            if field not in step:
                errors.append(
                    f"step[{index}] (id={sid!r}, op=breakdown) missing required field '{field}'"
                )
        fy = step.get("fy")
        if fy is not None and not isinstance(fy, int):
            errors.append(f"step[{index}] (id={sid!r}) 'fy' must be an integer, got {fy!r}")
        axis = step.get("axis")
        if "axis" in step and axis not in BREAKDOWN_AXES:
            errors.append(
                f"step[{index}] (id={sid!r}) 'axis' must be one of {sorted(BREAKDOWN_AXES)}, "
                f"got {axis!r}"
            )
        concept_family = step.get("concept_family")
        if (
            concept_family is not None
            and self.known_concept_families
            and concept_family not in self.known_concept_families
        ):
            errors.append(
                f"step[{index}] (id={sid!r}) concept_family {concept_family!r} is not a "
                f"recognized CONCEPT_FAMILIES value"
            )
        errors.extend(self._validate_period(step, index, sid))
        return errors

    def _validate_screen(self, step: dict, index: int, sid: Any) -> list[str]:
        """A cross-filer `screen` ranks a metric across all filers (no cik).

        Required: fy, dimension, metric_kind, order. `concept_family` is required
        for value/pct_of_total kinds (and must be a known family) but optional for
        a `derived:<Metric>` kind (the metric name selects the mv_derived_metrics row).
        """
        errors: list[str] = []

        for field in ("fy", "dimension", "metric_kind", "order"):
            if field not in step:
                errors.append(f"step[{index}] (id={sid!r}, op=screen) missing required field '{field}'")

        fy = step.get("fy")
        if fy is not None and not isinstance(fy, int):
            errors.append(f"step[{index}] (id={sid!r}) 'fy' must be an integer, got {fy!r}")

        dimensions = step.get("dimension")
        if "dimension" in step and dimensions != "NONE" and not isinstance(dimensions, dict):
            errors.append(
                f"step[{index}] (id={sid!r}) 'dimension' must be the string \"NONE\" or "
                f"an {{axis: member}} object, got {dimensions!r}"
            )

        order = step.get("order")
        if order is not None and str(order).lower() not in SCREEN_ORDERS:
            errors.append(f"step[{index}] (id={sid!r}) 'order' must be one of {sorted(SCREEN_ORDERS)}, got {order!r}")

        metric_kind = step.get("metric_kind")
        is_derived = isinstance(metric_kind, str) and metric_kind.startswith("derived:")
        if metric_kind is not None and not is_derived and metric_kind not in SCREEN_METRIC_KINDS:
            errors.append(
                f"step[{index}] (id={sid!r}) 'metric_kind' must be one of "
                f"{sorted(SCREEN_METRIC_KINDS)} or 'derived:<MetricName>', got {metric_kind!r}"
            )
        if is_derived:
            # A name the screen cannot resolve returns no rows; rejecting it here lets
            # the planner retry (docs/specs/screen-metric-names.md).
            planned = str(metric_kind).split(":", 1)[1].strip()
            if resolve_derived_metric(planned, DERIVED_METRICS, DERIVED_METRIC_ALIASES) is None:
                errors.append(
                    f"step[{index}] (id={sid!r}) unknown derived metric {planned!r}; "
                    f"use one of {sorted(DERIVED_METRICS)}"
                )

        concept_family = step.get("concept_family")
        if not is_derived:
            if concept_family is None:
                errors.append(f"step[{index}] (id={sid!r}, op=screen) missing required field 'concept_family'")
            elif self.known_concept_families and concept_family not in self.known_concept_families:
                errors.append(
                    f"step[{index}] (id={sid!r}) concept_family {concept_family!r} is not a "
                    f"recognized CONCEPT_FAMILIES value"
                )

        top_n = step.get("top_n")
        if top_n is not None and not isinstance(top_n, int):
            errors.append(f"step[{index}] (id={sid!r}) 'top_n' must be an integer or null, got {top_n!r}")

        # The universe: absent/null/"covered", a named group (docs/specs/screen-universe.md)
        # or a member list of two or more tickers (docs/specs/named-company-screen.md R2).
        universe = step.get("universe")
        if isinstance(universe, list):
            if member_list(universe) is None:
                errors.append(
                    f"step[{index}] (id={sid!r}) 'universe' as a list must hold two or more "
                    f"distinct non-blank ticker strings, got {universe!r}"
                )
        elif universe is not None:
            valid = [COVERED_UNIVERSE, *sorted(universe_groups())]
            if not isinstance(universe, str) or universe not in valid:
                errors.append(
                    f"step[{index}] (id={sid!r}) 'universe' must be one of {valid} or null, "
                    f"got {universe!r}"
                )

        errors.extend(self._validate_period(step, index, sid))
        return errors
