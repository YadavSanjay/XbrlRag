"""Unit tests for the prompt/model regression matrix runner."""
from src.eval.regression import (
    Arm,
    PromptMatrixRunner,
    structural_scorer,
    version_model_matrix,
)


def test_arm_stage_kwargs_maps_to_constructor_params():
    arm = Arm(
        label="x",
        stage_prompts={"synthesizer": {"version": "v2", "provider": "anthropic", "series": "claude-4"}},
        stage_models={"synthesizer": "gpt-5"},
    )
    assert arm.stage_kwargs("synthesizer") == {
        "prompt_version": "v2",
        "prompt_provider": "anthropic",
        "prompt_series": "claude-4",
        "model": "gpt-5",
    }
    assert arm.stage_kwargs("intent_router") == {}  # untouched stage -> defaults


def test_version_model_matrix_builds_cartesian():
    arms = version_model_matrix("synthesizer", versions=["v1", "v2"], models=["gpt-5-mini", "gpt-5"])
    assert len(arms) == 4
    labels = {a.label for a in arms}
    assert "synthesizer@v1+gpt-5-mini" in labels
    assert "synthesizer@v2+gpt-5" in labels
    a = next(a for a in arms if a.label == "synthesizer@v2+gpt-5")
    assert a.stage_kwargs("synthesizer") == {"prompt_version": "v2", "model": "gpt-5"}


def test_structural_scorer_reads_object_and_dict():
    class _Resp:
        answer_summary = "Revenue was $10B"
        metadata = {"fully_answered": True}

    assert structural_scorer("q", _Resp()) == {"answered": 1.0, "fully_answered": 1.0, "parse_error": 0.0}
    degraded = {"answer_summary": "could not be completed", "metadata": {"fully_answered": False, "synthesis_error": {}}}
    s = structural_scorer("q", degraded)
    assert s == {"answered": 1.0, "fully_answered": 0.0, "parse_error": 1.0}


def test_runner_aggregates_scores_per_arm():
    arms = version_model_matrix("synthesizer", versions=["v1", "v2"], models=["gpt-5-mini"])

    def run_fn(arm, query):
        # v1 "fully answers", v2 degrades — a stand-in for a regression.
        if "v1" in arm.label:
            return {"answer_summary": "ok", "metadata": {"fully_answered": True}}
        return {"answer_summary": "caveat", "metadata": {"fully_answered": False, "synthesis_error": {}}}

    result = PromptMatrixRunner(arms, run_fn, structural_scorer).run(["q1", "q2"])
    assert len(result.records) == 4
    by_arm = {s.arm: s for s in result.summaries}
    assert by_arm["synthesizer@v1+gpt-5-mini"].scores["fully_answered"] == 1.0
    assert by_arm["synthesizer@v2+gpt-5-mini"].scores["fully_answered"] == 0.0
    assert by_arm["synthesizer@v2+gpt-5-mini"].scores["parse_error"] == 1.0
    assert all(s.errors == 0 for s in result.summaries)


def test_runner_records_errors_without_sinking_matrix():
    arms = [Arm(label="boom"), Arm(label="ok")]

    def run_fn(arm, query):
        if arm.label == "boom":
            raise RuntimeError("stage exploded")
        return {"answer_summary": "fine", "metadata": {"fully_answered": True}}

    result = PromptMatrixRunner(arms, run_fn, structural_scorer).run(["q1"])
    summ = {s.arm: s for s in result.summaries}
    assert summ["boom"].errors == 1
    assert summ["boom"].scores == {}
    assert summ["ok"].errors == 0
    assert summ["ok"].scores["answered"] == 1.0
