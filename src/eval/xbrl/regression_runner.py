"""WS-2 / B10 — wire the prompt×model regression matrix to the live pipeline.

``src.eval.regression.prompt_matrix`` is infra-agnostic: it takes a
``run_fn(arm, query) -> response`` and a ``scorer(query, response) -> {..}``. This
module supplies both for the XBRL flow — a ``run_fn`` that builds a pipeline with an
arm's per-stage prompt/model overrides, and A3's numeric-tolerance grader as the
scorer — so a prompt edit or model swap that regresses answer accuracy is caught
*before merge* against the committed golden set.

The gate (:func:`regression_gate`) fails when a baseline arm's ``correct`` mean drops
below a threshold; ``scripts/eval/run_regression_matrix.py`` is the CI entry point.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.eval.regression.prompt_matrix import Arm, MatrixResult, PromptMatrixRunner
from src.eval.xbrl.golden import GoldenCase, numeric_tolerance_scorer

# Arm stage name -> XbrlRetrievalPipeline constructor kwarg (they match 1:1).
_STAGE_PARAM = {
    "query_decomposer": "query_decomposer",
    "plan_generator": "plan_generator",
    "synthesizer": "synthesizer",
    "faithfulness_verifier": "faithfulness_verifier",
}


def _build_stage(stage: str, kwargs: dict) -> Any:
    """Construct one overridden stage component from its arm kwargs."""
    if stage == "query_decomposer":
        from src.retriever.xbrl.query_decomposer import QueryDecomposer
        return QueryDecomposer(**kwargs)
    if stage == "plan_generator":
        from src.retriever.xbrl.plan_generator import PlanGenerator
        return PlanGenerator(**kwargs)
    if stage == "synthesizer":
        from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
        return XbrlRagSynthesizer(**kwargs)
    if stage == "faithfulness_verifier":
        from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
        return XbrlFaithfulnessVerifier(**kwargs)
    raise ValueError(f"regression matrix cannot override stage {stage!r} "
                     f"(supported: {sorted(_STAGE_PARAM)})")


def build_pipeline_for_arm(arm: Arm, *, persist_audit: bool = False) -> Any:
    """A pipeline with this arm's per-stage overrides applied; other stages default."""
    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
    overrides: dict = {}
    for stage in set(list(arm.stage_prompts) + list(arm.stage_models)):
        kwargs = arm.stage_kwargs(stage)
        overrides[_STAGE_PARAM[stage]] = _build_stage(stage, kwargs)
    return XbrlRetrievalPipeline(persist_audit=persist_audit, **overrides)


def make_run_fn(*, persist_audit: bool = False) -> Callable[[Arm, str], Any]:
    """A ``run_fn`` that builds one pipeline per arm (reused across queries) and
    runs ``generate``. Lazy — no pipeline is constructed until first called, so this
    module imports without live backends."""
    cache: dict[str, Any] = {}

    def run_fn(arm: Arm, query: str) -> Any:
        if arm.label not in cache:
            cache[arm.label] = build_pipeline_for_arm(arm, persist_audit=persist_audit)
        return cache[arm.label].generate(query)

    return run_fn


def run_regression(
    arms: list[Arm],
    cases: list[GoldenCase],
    *,
    run_fn: Callable[[Arm, str], Any] | None = None,
) -> MatrixResult:
    """Run every arm over the golden queries, scored by the numeric-tolerance grader."""
    scorer = numeric_tolerance_scorer(cases)
    queries = [c.query for c in cases]
    runner = PromptMatrixRunner(arms, run_fn or make_run_fn(), scorer)
    return runner.run(queries)


@dataclass
class GateResult:
    passed: bool
    baseline_arm: str
    score: float
    min_score: float
    detail: dict[str, Any]


def regression_gate(
    result: MatrixResult,
    baseline_arm: str,
    *,
    score_key: str = "correct",
    min_score: float = 0.8,
) -> GateResult:
    """Fail when the baseline arm's mean ``score_key`` falls below ``min_score`` —
    the CI regression gate."""
    summ = {s.arm: s for s in result.summaries}
    base = summ.get(baseline_arm)
    score = float(base.scores.get(score_key, 0.0)) if base else 0.0
    detail = {
        "arms": {s.arm: {"scores": s.scores, "errors": s.errors, "n": s.n}
                 for s in result.summaries},
    }
    return GateResult(
        passed=(base is not None and score >= min_score),
        baseline_arm=baseline_arm,
        score=score,
        min_score=min_score,
        detail=detail,
    )
