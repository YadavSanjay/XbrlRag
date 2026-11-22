"""Branch dispatcher for the XBRL hybrid query pipeline (design doc §2.2).

Consumes the `IntentRouterResult` produced by `intent_router + entity_extractor`
(§2.2.2) and decides which downstream branch handles the query:

- **FACT_BRANCH_DIRECT** — `intent_router` already produced a 1-step `plan`
  (the trivial-lookup fast path); hand straight to `plan_executor`.
- **NARRATIVE_BRANCH** — existing hybrid Qdrant + Jina-rerank RAG path.
- **PLAN_GENERATOR** — `NUMERIC_COMPUTE` / `HYBRID`, or a `NUMERIC_LOOKUP` that
  needs a dimensional slice (§2.2.3).

Per §2.2.1's "Failure mode" column, ambiguous company/period resolution
degrades to `NARRATIVE_BRANCH` rather than failing outright — `decided.degraded`
is set so callers can log/surface that fallback.

`plan_generator`, `plan_executor`, and the narrative RAG branch are NOT part of
this module (see §2.2.3 / `src/retriever/base_retriever.py`) — this dispatcher
only decides *which* of them to call and hands them the resolved
`IntentRouterResult`. Callers inject the actual branch implementations via the
`*_fn` constructor params; if a handler isn't supplied, `execute()` returns the
`DispatchDecision` without running it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from src.logging_config import get_logger
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter, IntentRouterResult

logger = get_logger(__name__)


class BRANCH(Enum):
    FACT_BRANCH_DIRECT = "FACT_BRANCH_DIRECT"
    NARRATIVE_BRANCH = "NARRATIVE_BRANCH"
    PLAN_GENERATOR = "PLAN_GENERATOR"


@dataclass
class DispatchDecision:
    branch: BRANCH
    reason: str
    intent_result: IntentRouterResult
    degraded: bool = False  # True if ambiguous resolution forced a NARRATIVE fallback

    def to_dict(self) -> dict:
        return {
            "branch": self.branch.value,
            "reason": self.reason,
            "degraded": self.degraded,
            "intent_result": self.intent_result.to_dict(),
        }


# Branch handler signatures:
#   fact_branch_fn(plan: dict, result: IntentRouterResult) -> Any
#   narrative_branch_fn(query: str, result: IntentRouterResult) -> Any
#   plan_generator_fn(query: str, result: IntentRouterResult) -> Any
FactBranchFn = Callable[[dict, IntentRouterResult], Any]
NarrativeBranchFn = Callable[[str, IntentRouterResult], Any]
PlanGeneratorFn = Callable[[str, IntentRouterResult], Any]


class BranchDispatcher:
    """Routes a query to the fact/narrative/plan-generator branch.

    Example:
        >>> dispatcher = BranchDispatcher(
        ...     intent_router=IntentRouter(entity_extractor=EntityExtractor.from_env()),
        ...     narrative_branch_fn=lambda q, r: existing_rag_pipeline.run(q),
        ... )
        >>> decision = dispatcher.decide("What was Apple's revenue in fiscal 2023?")
        >>> result = dispatcher.execute("What was Apple's revenue in fiscal 2023?", decision)
    """

    def __init__(
        self,
        intent_router: IntentRouter,
        fact_branch_fn: FactBranchFn | None = None,
        narrative_branch_fn: NarrativeBranchFn | None = None,
        plan_generator_fn: PlanGeneratorFn | None = None,
    ):
        self.intent_router = intent_router
        self.fact_branch_fn = fact_branch_fn
        self.narrative_branch_fn = narrative_branch_fn
        self.plan_generator_fn = plan_generator_fn

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def decide(self, query: str) -> DispatchDecision:
        """Run the intent router and decide the branch for `query`."""
        result = self.intent_router.route(query)
        return self.decide_from_result(query, result)

    def decide_from_result(self, query: str, result: IntentRouterResult) -> DispatchDecision:
        """Decide the branch given an already-computed `IntentRouterResult`."""
        # 1. Fast-path plan ready (covers NARRATIVE and trivial NUMERIC_LOOKUP)
        if result.plan is not None:
            return DispatchDecision(
                branch=BRANCH.FACT_BRANCH_DIRECT if result.intent != XBRL_INTENT.NARRATIVE else BRANCH.NARRATIVE_BRANCH,
                reason="intent_router fast-path plan ready",
                intent_result=result,
            )

        # 2. Pure narrative without a fast-path plan (shouldn't normally happen,
        #    since _try_fast_path_plan always emits one for NARRATIVE, but
        #    handle defensively)
        if result.intent == XBRL_INTENT.NARRATIVE:
            return DispatchDecision(
                branch=BRANCH.NARRATIVE_BRANCH,
                reason="intent classified as NARRATIVE",
                intent_result=result,
            )

        # 2b. Cross-filer screen ("which companies…") -> plan_generator (a `screen`
        #     step). A screen legitimately names no single company, so it must
        #     bypass the "no company -> degrade to narrative" check in step 3.
        if result.intent == XBRL_INTENT.NUMERIC_SCREEN:
            return DispatchDecision(
                branch=BRANCH.PLAN_GENERATOR,
                reason="intent=NUMERIC_SCREEN requires a cross-filer screen plan",
                intent_result=result,
            )

        # 3. Ambiguous entity/period resolution -> degrade to narrative (safe fallback, §2.2.1)
        unresolved_reasons = self._unresolved_reasons(result)
        if unresolved_reasons:
            return DispatchDecision(
                branch=BRANCH.NARRATIVE_BRANCH,
                reason="ambiguous entity/period resolution - degrading to narrative: "
                       + "; ".join(unresolved_reasons),
                intent_result=result,
                degraded=True,
            )

        # 4. NUMERIC_COMPUTE, HYBRID, or NUMERIC_LOOKUP needing a dimensional slice
        return DispatchDecision(
            branch=BRANCH.PLAN_GENERATOR,
            reason=f"intent={result.intent.value} requires a multi-step plan",
            intent_result=result,
        )

    @staticmethod
    def _unresolved_reasons(result: IntentRouterResult) -> list[str]:
        reasons: list[str] = []

        if not result.companies:
            reasons.append("no company could be extracted from the query")
        for c in result.companies:
            if c.is_ambiguous:
                reason = f"company mention '{c.mention}' could not be resolved to a CIK"
                if c.candidates:
                    reason += f" ({c.clarification()})"
                reasons.append(reason)

        for p in result.periods:
            if p.ambiguous:
                reasons.append(f"period mention '{p.mention}': {p.reason}")

        return reasons

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, query: str, decision: DispatchDecision | None = None) -> Any:
        """Run the branch handler selected by `decide()`.

        If the relevant `*_fn` handler was not supplied at construction time,
        returns the `DispatchDecision` itself rather than raising, so callers
        can inspect routing behavior before the downstream branches exist.
        """
        decision = decision or self.decide(query)

        if decision.branch == BRANCH.FACT_BRANCH_DIRECT:
            if self.fact_branch_fn is None:
                logger.info("FACT_BRANCH_DIRECT selected but no fact_branch_fn configured; returning decision only")
                return decision
            return self.fact_branch_fn(decision.intent_result.plan, decision.intent_result)

        if decision.branch == BRANCH.NARRATIVE_BRANCH:
            if self.narrative_branch_fn is None:
                logger.info("NARRATIVE_BRANCH selected but no narrative_branch_fn configured; returning decision only")
                return decision
            return self.narrative_branch_fn(query, decision.intent_result)

        if decision.branch == BRANCH.PLAN_GENERATOR:
            if self.plan_generator_fn is None:
                logger.info("PLAN_GENERATOR selected but no plan_generator_fn configured; returning decision only")
                return decision
            return self.plan_generator_fn(query, decision.intent_result)

        raise ValueError(f"Unhandled branch: {decision.branch}")


if __name__ == "__main__":
    from src.retriever.xbrl.entity_extractor import EntityExtractor

    dispatcher = BranchDispatcher(
        intent_router=IntentRouter(entity_extractor=EntityExtractor.from_env())
    )

    for q in [
        "What was Apple's revenue in fiscal 2023?",
        "What was NVIDIA's Data Center revenue last year?",
        "What was the year-over-year growth in Google's R&D expense from FY2022 to FY2023?",
        "What are the main risk factors Amazon discusses related to its logistics network?",
    ]:
        decision = dispatcher.decide(q)
        print(q)
        print(f"  -> branch={decision.branch.value}, degraded={decision.degraded}, reason={decision.reason}")
