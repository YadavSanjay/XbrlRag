"""frontier_sec_tools: a budget hit stays reported when the final no-tools turn fails.

When a budget is hit the arm makes one last call without tools. If that call raises (an
empty reply, say), the case ends as ``error``, but the budget was still hit, so the record
keeps ``budget`` and ``budget_hit`` and ``agent_summary`` counts it under that budget.

The only fake is the LLM gateway (the provider boundary); the arm, the toolbox (its
``calculate`` tool touches no network) and ``agent_summary`` are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.keys.base import HttpTransport
from src.eval.sec_tools.client import SecApiClient, SecSnapshot
from src.eval.sec_tools.toolbox import SecToolbox
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem, agent_summary
from src.llm.gateway.adapters.base import LLMEmptyReplyError, ParsedResponse
from src.llm.gateway.tools import ToolCall
from src.observability.trace_context import get_audit_sink, set_audit_sink

_MODEL = "gpt-5.6-sol"
_AS_OF = "2024-06-30"
_FINAL = json.dumps({"value": 5, "unit": "USD", "rows": [], "refused": False,
                     "hedged": False, "cited_chunks": [], "statements": [],
                     "text": "It is 5."})


class _NoSec(HttpTransport):
    """The SEC boundary; the tests only run ``calculate``, so any request fails them."""

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        raise AssertionError(f"unexpected SEC request: {url}")


class _FakeGateway:
    """Calls ``calculate`` on every tool turn; the final (``tool_choice="none"``) turn
    raises *final_error*, or answers when it is None. *first_error* fails the first call."""

    def __init__(self, final_error: BaseException | None = None,
                 first_error: BaseException | None = None) -> None:
        self.final_error = final_error
        self.first_error = first_error
        self.choices: list[str] = []

    def complete_tools(self, *, tool_choice: str, **_: Any) -> ParsedResponse:
        self.choices.append(tool_choice)
        if self.first_error is not None and len(self.choices) == 1:
            raise self.first_error
        if tool_choice == "none":
            if self.final_error is not None:
                raise self.final_error
            return ParsedResponse(content=_FINAL, finish_reason="stop", prompt_tokens=100,
                                  completion_tokens=10)
        n = len(self.choices)
        call = ToolCall(f"call-{n}", "calculate", {"expression": f"{n} + 1"})
        return ParsedResponse(content="", finish_reason="tool_calls", prompt_tokens=100,
                              completion_tokens=10, tool_calls=[call])


def _empty_reply() -> LLMEmptyReplyError:
    usage = ParsedResponse(content="", finish_reason="stop", prompt_tokens=100,
                           completion_tokens=0)
    return LLMEmptyReplyError("empty reply", usage)


@pytest.fixture(autouse=True)
def _no_audit_sink():
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


def _arm(tmp_path: Path, gateway: _FakeGateway, **budgets: Any) -> FrontierSecToolsSystem:
    snapshot = SecSnapshot(tmp_path / "snapshot", as_of=_AS_OF)
    client = SecApiClient(snapshot, transport=_NoSec(), identity="Unit Tester unit@example.com",
                          sleep=lambda _s: None)
    toolbox = SecToolbox(client, as_of=_AS_OF, withheld_ciks=())
    # The arm only calls ``complete_tools``; the fake stands in for the real gateway.
    return FrontierSecToolsSystem(
        model=_MODEL,
        gateway=gateway,  # type: ignore[arg-type]  # duck-typed fake at the LLM boundary
        toolbox=toolbox,
        universes={"covered": [], "groups": {}},
        **budgets,
    )


def _case() -> EvalCase:
    return EvalCase(id="case-1", question="What is 2 + 3?", tags=["split:dev"])


@pytest.mark.parametrize(("budgets", "hit"), [
    ({"max_steps": 2}, "steps"),
    ({"max_tool_calls": 1}, "tool_calls"),
])
def test_a_hit_budget_is_kept_when_the_final_turn_fails(tmp_path, budgets, hit):
    gateway = _FakeGateway(final_error=_empty_reply())
    answer = _arm(tmp_path, gateway, **budgets).answer(_case())
    assert gateway.choices[-1] == "none"  # the final no-tools turn was made, and failed
    assert answer.error is not None
    assert answer.agent["stop"] == "error"
    assert answer.agent["budget"] == hit
    assert answer.agent["budget_hit"] is True
    summary = agent_summary([answer.agent])
    assert summary["budget_hit"] == {hit: 1}
    assert summary["answered"] == 0


def test_a_hit_budget_with_a_good_final_turn_is_unchanged(tmp_path):
    gateway = _FakeGateway()
    answer = _arm(tmp_path, gateway, max_steps=2).answer(_case())
    assert answer.error is None
    assert answer.agent["stop"] == "steps"
    assert answer.agent["budget"] == "steps"
    assert answer.agent["budget_hit"] is True
    assert agent_summary([answer.agent])["budget_hit"] == {"steps": 1}


def test_an_error_before_any_budget_is_not_a_hit(tmp_path):
    gateway = _FakeGateway(first_error=_empty_reply())
    answer = _arm(tmp_path, gateway, max_steps=2).answer(_case())
    assert answer.error is not None
    assert answer.agent["stop"] == "error"
    assert answer.agent["budget"] is None
    assert answer.agent["budget_hit"] is False
    assert agent_summary([answer.agent])["budget_hit"] == {}


def test_agent_summary_keys_older_records_by_stop():
    # A record from before ``budget`` existed is counted under its stop reason.
    records = [{"stop": "cost", "budget_hit": True},
               {"stop": "error", "budget": "steps", "budget_hit": True},
               {"stop": "error", "budget_hit": False}]
    assert agent_summary(records)["budget_hit"] == {"cost": 1, "steps": 1}
