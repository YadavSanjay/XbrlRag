"""Prompt × model regression matrix runner.

Design: docs/design/XbrlRag_LLM_Harness.md.

Runs a set of *arms* (each = per-stage prompt-version/provider/series overrides +
per-stage model overrides) across a query set, scores each run with a pluggable
scorer, and aggregates per-arm. This is the mechanism behind "run the old prompt
against the new model and measure the impact": build one arm pinned to
``version=v1`` and another to ``version=v2`` (or one per model), run the same
queries, compare the aggregated scores.

The runner is infra-agnostic — it takes a ``run_fn(arm, query) -> result`` that
the caller wires to the live pipeline, so it is unit-testable without any LLM/DB.
An arm's :meth:`Arm.stage_kwargs` returns exactly the constructor kwargs the XBRL
stages accept (``prompt_version`` / ``prompt_provider`` / ``prompt_series`` /
``model``), so wiring is direct:

    from src.eval.regression import Arm, PromptMatrixRunner, structural_scorer, version_model_matrix
    from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
    from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline

    def run_fn(arm, query):
        synth = XbrlRagSynthesizer(**arm.stage_kwargs("synthesizer"))
        pipeline = XbrlRetrievalPipeline(synthesizer=synth)   # other stages default
        return pipeline.generate(query)

    arms = version_model_matrix("synthesizer", versions=["v1", "v2"], models=["gpt-5-mini", "gpt-5"])
    result = PromptMatrixRunner(arms, run_fn, structural_scorer).run(queries)
    for s in result.summaries:
        print(s.arm, s.scores, f"errors={s.errors}")
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Arm:
    """One configuration in the matrix: per-stage prompt + model overrides."""

    label: str
    # stage -> {"version"?: str, "provider"?: str, "series"?: str}
    stage_prompts: dict[str, dict] = field(default_factory=dict)
    # stage -> model id
    stage_models: dict[str, str] = field(default_factory=dict)

    def stage_kwargs(self, stage: str) -> dict:
        """Constructor kwargs for *stage* implied by this arm (see the XBRL stage
        ``__init__`` signatures: prompt_version/provider/series + model)."""
        kw: dict = {}
        prompt = self.stage_prompts.get(stage) or {}
        if prompt.get("version"):
            kw["prompt_version"] = prompt["version"]
        if prompt.get("provider"):
            kw["prompt_provider"] = prompt["provider"]
        if prompt.get("series"):
            kw["prompt_series"] = prompt["series"]
        model = self.stage_models.get(stage)
        if model:
            kw["model"] = model
        return kw


@dataclass
class RunRecord:
    arm: str
    query: str
    scores: dict[str, float] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ArmSummary:
    arm: str
    n: int
    scores: dict[str, float]  # mean of each numeric score key over non-errored runs
    errors: int


@dataclass
class MatrixResult:
    records: list[RunRecord]
    summaries: list[ArmSummary]


def version_model_matrix(stage: str, versions: list[str], models: list[str]) -> list[Arm]:
    """Cartesian ``{version} × {model}`` arms for a single stage — the core
    "old prompt vs new model" grid."""
    arms: list[Arm] = []
    for ver in versions:
        for model in models:
            arms.append(Arm(
                label=f"{stage}@{ver}+{model}",
                stage_prompts={stage: {"version": ver}},
                stage_models={stage: model},
            ))
    return arms


def structural_scorer(query: str, result: Any) -> dict[str, float]:
    """Cheap, infra-free default scorer over a pipeline response (object or dict).

    Emits ``answered`` / ``fully_answered`` / ``parse_error`` in [0, 1]. Swap in a
    DeepEval/RAGAS-backed scorer for semantic quality.
    """
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    metadata = _get(result, "metadata", {}) or {}
    answer = _get(result, "answer_summary", "") or ""
    return {
        "answered": 1.0 if str(answer).strip() else 0.0,
        "fully_answered": 1.0 if metadata.get("fully_answered") else 0.0,
        # presence of the key signals a synthesis parse-error (value may be {}).
        "parse_error": 1.0 if metadata.get("synthesis_error") is not None else 0.0,
    }


class PromptMatrixRunner:
    """Execute every arm over every query, score, and aggregate per arm."""

    def __init__(
        self,
        arms: list[Arm],
        run_fn: Callable[[Arm, str], Any],
        scorer: Callable[[str, Any], dict[str, float]] = structural_scorer,
    ):
        self.arms = list(arms)
        self.run_fn = run_fn
        self.scorer = scorer

    def run(self, queries: list[str]) -> MatrixResult:
        records: list[RunRecord] = []
        for arm in self.arms:
            for query in queries:
                try:
                    result = self.run_fn(arm, query)
                    scores = self.scorer(query, result)
                    records.append(RunRecord(arm=arm.label, query=query, scores=scores))
                except Exception as exc:  # a failed arm/query must not sink the matrix
                    records.append(RunRecord(arm=arm.label, query=query, scores={}, error=repr(exc)))
        return MatrixResult(records=records, summaries=self._aggregate(records))

    @staticmethod
    def _aggregate(records: list[RunRecord]) -> list[ArmSummary]:
        by_arm: dict[str, list[RunRecord]] = {}
        for r in records:
            by_arm.setdefault(r.arm, []).append(r)

        summaries: list[ArmSummary] = []
        for arm, rs in by_arm.items():
            ok = [r for r in rs if r.error is None]
            errors = sum(1 for r in rs if r.error is not None)
            keys = {k for r in ok for k in r.scores}
            means = {
                k: sum(r.scores.get(k, 0.0) for r in ok) / len(ok)
                for k in keys
            } if ok else {}
            summaries.append(ArmSummary(arm=arm, n=len(rs), scores=means, errors=errors))
        return summaries
