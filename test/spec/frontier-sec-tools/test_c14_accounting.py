"""C14: cost, latency, the record, failures and RunHalt."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.system import SystemAnswer
from src.eval.systems.frontier_sec_tools import agent_summary
from src.llm.gateway import LLMRefusalError
from src.llm.run_budget import RunBudget, RunBudgetExceeded, RunHalt
from src.observability.trace_context import current_trace

FIND = [("find_company", {"query": "AAA"})]


@pytest.mark.criterion("C14")
def test_the_cost_covers_every_turn(make_arm, make_case, turn_cost):
    # oracle: the closed form at the pricing table's rates, three calls
    arm, _, _ = make_arm([FIND, FIND])
    answer = arm.answer(make_case())
    # tolerance: float sum of a few rate products
    assert answer.cost_usd == pytest.approx(3 * turn_cost(1000, 500), rel=1e-9)


@pytest.mark.criterion("C14")
def test_each_answer_has_its_own_trace(make_arm, make_case):
    # oracle: requirement 26
    arm, _, _ = make_arm()
    first, second = arm.answer(make_case()), arm.answer(make_case())
    assert first.query_id and second.query_id
    assert first.query_id != second.query_id
    assert first.latency_ms >= 0


@pytest.mark.criterion("C14")
def test_the_record(make_arm, make_case):
    # oracle: requirement 25 — one success and one bad call, counted by hand
    arm, _, _ = make_arm([FIND, [("list_filings", {"cik": "abc"})]])
    answer = arm.answer(make_case())
    record = answer.agent
    assert record["steps"] == 3
    assert record["tool_calls"] == 2
    assert record["tool_errors"] == 1
    assert record["snapshot_misses"] == 0
    assert record["withheld"] == 0
    assert record["by_tool"] == {"find_company": 1, "list_filings": 1}
    assert record["tool_ms"] >= 0
    assert record["stop"] == "answered"
    assert record["budget_hit"] is False
    assert record["unresolved_citations"] == []
    source = record["sources"][0]
    assert source["id"] == "S1"
    assert source["tool"] == "find_company"
    assert source["arguments"] == {"query": "AAA"}
    assert source["urls"] == ["https://www.sec.gov/files/company_tickers.json"]
    assert source["chars"] > 0
    assert len(record["sources"]) == 1


@pytest.mark.criterion("C14")
def test_a_frozen_miss_is_counted(make_arm, make_case):
    # oracle: requirement 25 — a frozen run says what it could not see
    arm, _, _ = make_arm([FIND], frozen=True)
    answer = arm.answer(make_case())
    assert answer.agent["snapshot_misses"] == 1
    assert answer.agent["tool_errors"] == 1
    assert answer.error is None


@pytest.mark.criterion("C14")
def test_the_record_is_persisted(make_arm, make_case):
    # oracle: requirement 25 — through to_dict, JSON-safe
    arm, _, _ = make_arm([FIND])
    answer = arm.answer(make_case())
    payload = json.loads(json.dumps(answer.to_dict()))
    assert payload["agent"]["steps"] == 2
    assert payload["agent"]["sources"][0]["id"] == "S1"


@pytest.mark.criterion("C14")
def test_an_answer_with_no_record_is_unchanged(make_arm, make_case):
    # oracle: requirement 25 — no other arm's payload moves; the arm's own has one
    arm, _, _ = make_arm()
    assert "agent" in arm.answer(make_case()).to_dict()
    assert "agent" not in SystemAnswer(text="x").to_dict()


@pytest.mark.criterion("C14")
def test_each_tool_call_is_on_the_trace(make_arm, make_case):
    # oracle: requirement 25 — through the arm's answer, so the events go to the real trace
    captured: list = []

    def watch(params):
        trace = current_trace()
        captured.extend(e for e in trace.events if e["kind"] == "tool_call")
        return "done 5"

    arm, _, _ = make_arm([FIND, watch])
    arm.answer(make_case())
    assert len(captured) == 1
    event = captured[0]
    assert event["tool"] == "find_company"
    assert event["arguments"] == {"query": "AAA"}
    assert event["source_id"] == "S1"
    assert event["chars"] > 0
    assert event["latency_ms"] >= 0


@pytest.mark.criterion("C14")
def test_a_provider_error_is_an_error_and_keeps_the_record(make_arm, make_case):
    # oracle: requirements 25, 26 — the second turn fails, after a tool ran
    arm, _, _ = make_arm([FIND], errors={1: KeyError("broken")})
    answer = arm.answer(make_case())
    assert answer.error
    assert not answer.error.startswith("excluded: ")
    assert answer.agent["tool_calls"] == 1


@pytest.mark.criterion("C14")
def test_a_provider_refusal_is_a_refusal(make_arm, make_case):
    # oracle: requirement 26
    arm, _, _ = make_arm(errors={0: LLMRefusalError("declined")})
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.error is None


@pytest.mark.criterion("C14")
def test_run_halt_propagates_from_the_first_call(make_arm, make_case):
    # oracle: requirement 26 and the repo's RAG rules — a budget of no calls stops the run
    arm, adapter, _ = make_arm(run_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        arm.answer(make_case())
    assert adapter.calls == 0


@pytest.mark.criterion("C14")
def test_run_halt_propagates_from_a_later_call(make_arm, make_case):
    # oracle: the same, one turn later
    arm, adapter, _ = make_arm([FIND], run_budget=RunBudget(max_llm_calls=1))
    with pytest.raises(RunHalt):
        arm.answer(make_case())
    assert adapter.calls == 1


@pytest.mark.criterion("C14")
def test_run_halt_propagates_from_a_tool(make_arm, make_case, fake_sec):
    # oracle: requirement 16 — no broad except swallows it
    sec = fake_sec()
    sec.error = RunBudgetExceeded("stop")
    arm, _, _ = make_arm([FIND], transport=sec)
    with pytest.raises(RunBudgetExceeded):
        arm.answer(make_case())


@pytest.mark.criterion("C14")
def test_agent_summary(make_arm):
    # oracle: hand-built records, summed by hand
    records = [
        {"stop": "answered", "steps": 4, "tool_calls": 3, "tool_errors": 1, "snapshot_misses": 0,
         "unresolved_citations": ["S9"], "budget_hit": False},
        {"stop": "cost", "steps": 9, "tool_calls": 8, "tool_errors": 0, "snapshot_misses": 2,
         "unresolved_citations": [], "budget_hit": True},
        {"stop": "steps", "steps": 21, "tool_calls": 40, "tool_errors": 2, "snapshot_misses": 0,
         "unresolved_citations": ["S1", "S2"], "budget_hit": True},
    ]
    assert agent_summary(records) == {
        "cases": 3,
        "answered": 1,
        "budget_hit": {"cost": 1, "steps": 1},
        "tool_calls": 51,
        "tool_errors": 3,
        "snapshot_misses": 2,
        "unresolved_citations": 3,
        "steps_max": 21,
        # re-spec (frontier-tools-v2 requirement 7): no record has the field, so 0
        "prompt_tokens_max": 0,
    }


@pytest.mark.criterion("C14")
def test_agent_summary_of_nothing():
    # oracle: an empty run
    assert agent_summary([]) == {
        "cases": 0, "answered": 0, "budget_hit": {}, "tool_calls": 0, "tool_errors": 0,
        "snapshot_misses": 0, "unresolved_citations": 0, "steps_max": 0,
        # re-spec (frontier-tools-v2 requirement 7)
        "prompt_tokens_max": 0,
    }


@pytest.mark.criterion("C14")
@given(prompt=st.integers(1, 10**5), completion=st.integers(1, 10**4), turns=st.integers(0, 4))
def test_doubling_tokens_doubles_the_cost(make_arm, make_case, prompt, completion, turns):
    # oracle: cost is linear in tokens at fixed rates. Re-lock: both arms get a budget no draw
    # reaches, so the $2 default cannot stop the doubled run after fewer turns.
    single, _, _ = make_arm([FIND] * turns, prompt_tokens=prompt, completion_tokens=completion,
                            max_cost_usd=1e9)
    double, _, _ = make_arm(
        [FIND] * turns, prompt_tokens=2 * prompt, completion_tokens=2 * completion,
        max_cost_usd=1e9,
    )
    low = single.answer(make_case()).cost_usd
    high = double.answer(make_case()).cost_usd
    # tolerance: float products of the same rates
    assert high == pytest.approx(2 * low, rel=1e-9)
    assert low > 0
