"""Unit tests for the grader-v9-number-reading wiring outside the locked spec tests.

Covers what the spec leaves to the implementer (docs/specs/grader-v9-number-reading.md):
``graded_answer`` chaining ``unexempt_values``, ``CaseResult.judge_skipped`` surviving the
checkpoint payload, and ``finish_graded`` / ``run_graded_many`` recording skips per report.
Fakes sit only at the I/O boundaries: the judge's provider behind a real ``LLMGateway`` and
the system under test. No network, database or paid call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.eval.core.answer_parsing import synthesis_failure
from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.graders.dispatch import grade_case, graded_answer
from src.eval.core.graders.judge import ClaimJudge
from src.eval.core.judge_wiring import finish_graded, graded_runner, run_graded, run_graded_many
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.runner import RunnerConfig
from src.eval.core.system import SystemAnswer
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

_PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

# The synthesizer's fixed fallback summaries (grader-synthesis-error requirement 1).
_E = ("This answer could not be completed: the synthesis step returned a response that was not "
      "valid JSON. The retrieved facts are preserved in execution_result; please retry the "
      "query.")
_R = ("This answer could not be completed: the model declined to answer the synthesis step. "
      "The retrieved facts are preserved in execution_result.")


class _Adapter(ProviderAdapter):
    """A judge provider marking every claim right and every key point covered."""

    def __init__(self) -> None:
        self.provider = "fake"
        self.calls = 0

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.calls += 1
        user = next(m["content"] for m in params["messages"] if m["role"] == "user")
        payload, _ = json.JSONDecoder().raw_decode(user, user.index("{"))
        return json.dumps({
            "claims": [{"id": c["id"], "correct": True, "supported": True}
                       for c in payload["claims"]],
            "key_points": [{"index": p["index"], "covered": True}
                           for p in payload["key_points"]],
        })

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        return ParsedResponse(content=raw, finish_reason="stop", prompt_tokens=1000,
                              completion_tokens=200, reasoning_tokens=0,
                              cached_prompt_tokens=0)


class _System:
    """A system under test answering from a prepared map."""

    def __init__(self, answers: dict[str, SystemAnswer], name: str = "fake") -> None:
        self.name = name
        self._answers = answers

    def answer(self, case: EvalCase) -> SystemAnswer:
        prepared = self._answers.get(case.id) or SystemAnswer(text="")
        return SystemAnswer(**{**prepared.__dict__})


@pytest.fixture(autouse=True)
def _no_audit_sink():
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


def _judge() -> tuple[ClaimJudge, _Adapter]:
    adapter = _Adapter()
    gateway = LLMGateway(stage="eval_judge", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in _PROVIDERS})
    return ClaimJudge(model="claude-opus-5-5", generator_model="gpt-5.6-sol",
                      gateway=gateway), adapter


def _text(case_id: str) -> EvalCase:
    return EvalCase.from_dict({"id": case_id, "question": f"Why did {case_id} change?",
                               "kind": "narrative", "expected_text": "Reference.",
                               "key_points": ["p0"]})


def _lookup(case_id: str) -> EvalCase:
    return EvalCase.from_dict({"id": case_id, "question": "Revenue?", "kind": "lookup",
                               "ticker": "DEVA", "fy": 2025, "expected_value": 100.0})


def _cfg() -> RunnerConfig:
    return RunnerConfig(max_workers=1, progress_every=0)


def test_the_fallback_texts_here_are_the_synthesizers():
    assert synthesis_failure(_E) == "error"
    assert synthesis_failure(_R) == "refusal"


# --- graded_answer chains unexempt_values --------------------------------------------------


def test_graded_answer_drops_rank_and_day_count_copies():
    text = "DEVA ranked 1st with $500 million over 364 days."
    answer = SystemAnswer(text=text, numeric_values=[1.0, 500e6, 364.0])
    assert graded_answer(answer).numeric_values == [500e6]
    # The stored answer is untouched.
    assert answer.numeric_values == [1.0, 500e6, 364.0]


def test_graded_answer_returns_the_same_answer_when_nothing_drops():
    answer = SystemAnswer(text="Revenue was $500 million.", numeric_values=[500e6])
    assert graded_answer(answer) is answer


def test_graded_answer_chains_with_the_date_part_rule():
    # "31" is a date part, "3" a rank; both stored copies go, the amount stays.
    text = "On December 31, 2024 DEVA ranked 3rd with $5 million."
    answer = SystemAnswer(text=text, numeric_values=[31.0, 3.0, 5e6])
    assert graded_answer(answer).numeric_values == [5e6]


# --- CaseResult.judge_skipped survives the checkpoint --------------------------------------


def _result(case: EvalCase, answer: SystemAnswer) -> CaseResult:
    return CaseResult.build(case, answer, grade_case(case, answer))


def test_judge_skipped_round_trips_through_the_checkpoint_payload():
    case = _text("t1")
    result = _result(case, SystemAnswer(text=_E))
    result.judge_skipped = "synthesis_error"
    payload = json.loads(json.dumps(checkpoint_payload(result)))
    assert payload["judge_skipped"] == "synthesis_error"
    assert CaseResult.from_dict(payload, case).judge_skipped == "synthesis_error"


def test_an_old_payload_without_the_field_restores_as_not_skipped():
    case = _text("t1")
    payload = checkpoint_payload(_result(case, SystemAnswer(text="Fine.")))
    payload.pop("judge_skipped")
    assert CaseResult.from_dict(payload, case).judge_skipped is None


# --- finish_graded and the runner record skips per report ----------------------------------


def test_finish_graded_reads_skips_from_restored_results():
    # A resumed run: results restored from the checkpoint carry their reason; the totals
    # come from the results, not from the runner's own calls.
    judge, adapter = _judge()
    restored = [_result(_text(cid), SystemAnswer(text=t)) for cid, t in
                (("t2", _R), ("t1", _E), ("t3", "Fine."))]
    restored[0].judge_skipped = "synthesis_refusal"
    restored[1].judge_skipped = "synthesis_error"
    report = RunReport(run_id="r", suite="s", system="fake", results=restored)
    finish_graded([report], graded_runner(None, judge), [r.case for r in restored],
                  judge=judge)
    assert report.config["judge_skipped"] == {"t1": "synthesis_error",
                                              "t2": "synthesis_refusal"}
    assert list(report.config["judge_skipped"]) == ["t1", "t2"]
    assert adapter.calls == 0


def test_without_a_judge_nothing_is_recorded():
    cases = [_text("t1"), _text("t2")]
    answers = {"t1": SystemAnswer(text=_E), "t2": SystemAnswer(text="Fine.")}
    report = run_graded(cases, _System(answers), config=_cfg(), metamorphic=False)
    assert "judge_skipped" not in report.config
    assert all(r.judge_skipped is None for r in report.results)


def test_a_case_that_is_not_text_graded_is_not_listed():
    judge, adapter = _judge()
    cases = [_lookup("l1"), _text("t1")]
    answers = {"l1": SystemAnswer(text=_E), "t1": SystemAnswer(text="Fine.")}
    report = run_graded(cases, _System(answers), judge=judge, generator_profile="deepseek",
                        config=_cfg(), metamorphic=False)
    assert report.config["judge_skipped"] == {}
    assert adapter.calls == 1


def test_a_skipped_case_is_charged_nothing():
    judge, _adapter = _judge()
    cases = [_text("t1"), _text("t2")]
    answers = {"t1": SystemAnswer(text=_R), "t2": SystemAnswer(text="x", error="Boom()")}
    report = run_graded(cases, _System(answers), judge=judge, generator_profile="deepseek",
                        config=_cfg(), metamorphic=False)
    assert {r.case.id: r.judge_cost_usd for r in report.results} == {"t1": 0.0, "t2": 0.0}
    assert report.config["judge_cost_usd"] == 0.0
    assert report.config["judge_skipped"] == {"t1": "synthesis_refusal", "t2": "answer_error"}
    assert report.config["judge_summary"]["n_judged"] == 0


def test_each_system_records_its_own_skips():
    judge, adapter = _judge()
    cases = [_text("t1"), _text("t2")]
    a = _System({"t1": SystemAnswer(text=_E), "t2": SystemAnswer(text="Fine.")}, name="a")
    b = _System({"t1": SystemAnswer(text="Fine."), "t2": SystemAnswer(text=_R)}, name="b")
    reports = run_graded_many(cases, [a, b], judge=judge, generator_profile="deepseek",
                              config=_cfg(), metamorphic=False)
    assert [r.config["judge_skipped"] for r in reports] == [
        {"t1": "synthesis_error"}, {"t2": "synthesis_refusal"}]
    assert adapter.calls == 2
