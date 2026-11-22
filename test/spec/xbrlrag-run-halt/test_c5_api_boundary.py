"""C5: the serving boundary answers a halted query with the halted body (requirements 4, 6).

``src.api.pipelines.xbrl_answer`` is what ``run_xbrl`` (so the BFF, its stream, ``/api/v1``
and the MCP tools) calls. Point cases use the real pipeline; the property passes a stand-in
pipeline object (an argument, not a patch) that raises a given halt.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.api.pipelines import xbrl_answer
from src.llm.gateway import LLMRefusalError
from src.llm.run_budget import (
    CircuitBreakerOpen,
    FlexWaitExceeded,
    RunBudget,
    RunBudgetExceeded,
    RunStopped,
)

HALTS = (RunBudgetExceeded, CircuitBreakerOpen, FlexWaitExceeded, RunStopped)


def _assert_halted_body(body: dict[str, Any], query: str, halt: BaseException) -> None:
    """Requirement 6's halted answer, field by field (today's ``_halted_response`` shape)."""
    name = type(halt).__name__
    reason = f"run halted ({name}): {halt}"
    assert body["query"] == query
    assert body["answer_summary"] == ""
    assert body["answer_details"] == []
    assert body["unanswered_parts"] == [{"question_fragment": query, "reason": reason}]
    assert body["metadata"]["fully_answered"] is False
    assert body["metadata"]["halted"] is True
    assert body["metadata"]["halt_reason"] == name
    assert body["plan"] == {}
    assert body["execution_result"]["status"] == "ERROR"
    assert body["execution_result"]["error"] == reason
    assert body["narrative_chunks"] == []


class _Raising:
    """A stand-in pipeline whose ``generate`` raises *exc*, recording its arguments."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[tuple[str, str | None]] = []

    def generate(self, query: str, user_id: str | None = None) -> Any:
        self.calls.append((query, user_id))
        raise self.exc


@pytest.mark.criterion("C5")
def test_a_halted_query_gets_the_halted_body(h):
    # oracle: requirement 6 — a cap of 0 halts the first call with RunBudget.check's message
    kit = h.Kit(budget=RunBudget(max_llm_calls=0))
    body = xbrl_answer(kit.pipeline, h.QUESTION, user_id="analyst-1")
    _assert_halted_body(body, h.QUESTION, RunBudgetExceeded("0 LLM calls >= limit 0"))


@pytest.mark.criterion("C5")
def test_an_answer_is_the_pipelines_answer_dumped(h):
    # oracle: requirement 6 — for an unhalted query the body is generate(...).model_dump();
    # the reference is a second, identical kit answering directly
    body = xbrl_answer(h.Kit().pipeline, h.QUESTION)
    reference = h.Kit().pipeline.generate(h.QUESTION).model_dump()
    assert body["answer_summary"] == reference["answer_summary"]
    assert body["answer_details"] == reference["answer_details"]
    assert body["metadata"]["fully_answered"] is True
    assert "halted" not in body["metadata"]


@pytest.mark.criterion("C5")
def test_a_refusal_stays_the_pipelines_declined_answer(h):
    # oracle: requirement 4 — a refusal is not a halt: the pipeline still declines inside, and
    # the boundary passes that answer on
    kit = h.Kit(scripts={"router": [LLMRefusalError("declined (refusal category: cyber)")]})
    body = xbrl_answer(kit.pipeline, h.QUESTION)
    assert body["metadata"]["refused"] is True
    assert body["metadata"]["fully_answered"] is False
    assert "halted" not in body["metadata"]


@pytest.mark.criterion("C5")
def test_other_errors_propagate_and_user_id_reaches_generate():
    # oracle: requirement 6 — only a RunHalt is turned into a body; user_id is passed through
    crashing = _Raising(RuntimeError("database gone"))
    with pytest.raises(RuntimeError, match="database gone"):
        xbrl_answer(crashing, "q", user_id="u-7")
    assert crashing.calls == [("q", "u-7")]


@pytest.mark.criterion("C5")
@given(cls=st.sampled_from(HALTS),
       message=st.text(st.characters(blacklist_categories=("Cs",)), max_size=40),
       query=st.text(st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=40))
def test_any_halt_gives_the_halted_body_with_its_class_and_message(cls, message, query):
    # oracle: requirement 6 — the body is built from the halt's class name and str()
    halt = cls(message)
    stand_in = _Raising(halt)
    body = xbrl_answer(stand_in, query, user_id="u-1")
    _assert_halted_body(body, query, halt)
    assert stand_in.calls == [(query, "u-1")]
