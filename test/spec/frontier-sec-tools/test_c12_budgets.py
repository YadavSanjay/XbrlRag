"""C12: the budgets stop the tools, not the answer, and a hit budget is reported."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import AAA_10K, ARCHIVES, html_doc

FIND = [("find_company", {"query": "AAA"})]
READ = [("read_filing", {"cik": "1", "accession": AAA_10K})]


def _calc(n: int) -> list[tuple[str, dict]]:
    return [("calculate", {"expression": str(i)}) for i in range(n)]


@pytest.mark.criterion("C12")
def test_a_case_within_budget_is_answered(make_arm, make_case):
    # oracle: requirement 25 — stop says how it ended
    arm, adapter, _ = make_arm([FIND])
    answer = arm.answer(make_case())
    assert answer.agent["stop"] == "answered"
    assert answer.agent["budget_hit"] is False
    assert answer.agent["steps"] == 2
    assert adapter.params[-1]["tool_choice"] == "auto"


@pytest.mark.criterion("C12")
def test_the_step_budget_ends_in_one_call_without_tools(make_arm, make_case, reply):
    # oracle: requirement 22 — two tool turns, then one forced final call
    arm, adapter, _ = make_arm([FIND] * 9, max_steps=2, final=reply(120))
    answer = arm.answer(make_case())
    assert adapter.calls == 3
    assert [p["tool_choice"] for p in adapter.params] == ["auto", "auto", "none"]
    assert answer.agent["stop"] == "steps"
    assert answer.agent["budget_hit"] is True
    assert answer.agent["steps"] == 3
    assert answer.numeric_values == [120.0]
    assert answer.refused is False


@pytest.mark.criterion("C12")
def test_the_final_call_may_decline(make_arm, make_case, reply):
    # oracle: requirement 24 — the final call decides, and the arm adds nothing
    arm, _, _ = make_arm([FIND] * 9, max_steps=1,
                         final=reply(None, refused=True, text="Not enough steps."))
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.numeric_values == []
    assert answer.agent["stop"] == "steps"


@pytest.mark.criterion("C12")
def test_calls_past_the_budget_are_answered_and_not_run(make_arm, make_case):
    # oracle: requirement 22 — a provider requires an answer to every call id
    arm, adapter, _ = make_arm([_calc(5)], max_tool_calls=2)
    answer = arm.answer(make_case())
    tool_texts = adapter.texts(1, "tool")
    assert len(tool_texts) == 5
    assert [json.loads(t).get("source_id") for t in tool_texts] == ["S1", "S2", None, None, None]
    assert all(json.loads(t)["error"] == "budget" for t in tool_texts[2:])
    assert answer.agent["tool_calls"] == 2
    assert answer.agent["by_tool"] == {"calculate": 2}
    assert answer.agent["stop"] == "tool_calls"
    assert adapter.params[-1]["tool_choice"] == "none"
    assert adapter.calls == 2


@pytest.mark.criterion("C12")
def test_the_cost_budget_is_checked_after_each_turn(make_arm, make_case, turn_cost):
    # oracle: requirement 22 — one turn's cost passes the cap, so the next call has no tools
    arm, adapter, _ = make_arm([FIND] * 9, max_cost_usd=0.99 * turn_cost(1000, 500))
    answer = arm.answer(make_case())
    assert adapter.calls == 2
    assert adapter.params[-1]["tool_choice"] == "none"
    assert answer.agent["stop"] == "cost"
    assert answer.agent["budget_hit"] is True


@pytest.mark.criterion("C12")
def test_under_the_cost_budget_the_tools_stay_on(make_arm, make_case, turn_cost):
    # oracle: requirement 22 — three turns of cost, a cap of ten
    arm, adapter, _ = make_arm([FIND, FIND], max_cost_usd=10 * turn_cost(1000, 500))
    answer = arm.answer(make_case())
    assert adapter.calls == 3
    assert answer.agent["stop"] == "answered"


@pytest.mark.criterion("C12")
def test_a_result_past_the_window_never_reaches_the_model(make_arm, make_case, fake_sec):
    # oracle: requirement 22 — the arm stays under the tokens at which the price doubles
    probe, probe_adapter, _ = make_arm()
    probe.answer(make_case())
    base_chars = sum(len(str(m.get("content", ""))) for m in probe_adapter.messages(0))

    sec = fake_sec()
    marker = "MARKER" + "x" * 30000
    sec.pages[f"{ARCHIVES}/1/000000000124000001/aaa-2023.htm"] = (
        200, html_doc(marker).encode("utf-8"))
    # Room for 4,000 tokens above the first prompt: 16,000 characters. The page is 20,000.
    arm, adapter, _ = make_arm([READ, FIND], transport=sec,
                               max_input_tokens=base_chars // 4 + 4000)
    answer = arm.answer(make_case())
    tool_text = adapter.texts(1, "tool")[0]
    assert "MARKER" not in tool_text
    assert json.loads(tool_text)["error"] == "window"
    assert answer.agent["stop"] == "window"
    assert adapter.params[-1]["tool_choice"] == "none"
    assert adapter.calls == 2


@pytest.mark.criterion("C12")
def test_a_tool_call_on_the_final_turn_is_a_refusal(make_arm, make_case):
    # oracle: requirement 22 — a provider that ignores tool_choice="none"
    arm, adapter, _ = make_arm([FIND] * 9, max_steps=1, final_calls=FIND)
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.text
    assert answer.error is None
    assert answer.agent["stop"] == "steps"
    assert adapter.calls == 2


@pytest.mark.criterion("C12")
def test_a_budget_stop_adds_a_note_the_model_can_read(make_arm, make_case):
    # oracle: requirement 22 — the final call is told tools are over
    arm, adapter, _ = make_arm([FIND] * 9, max_steps=1)
    arm.answer(make_case())
    assert adapter.messages(1)[-1]["role"] == "user"
    assert "0" in adapter.messages(1)[-1]["content"]


@pytest.mark.criterion("C12")
@given(
    turns=st.lists(st.integers(0, 4), max_size=12),
    max_steps=st.integers(1, 6),
    max_tool_calls=st.integers(1, 10),
)
def test_the_bounds_hold_for_any_script(make_arm, make_case, turns, max_steps, max_tool_calls):
    # oracle: requirement 22 — model calls and calls run stay within their limits
    script = [_calc(n) if n else "plain final text 5" for n in turns]
    arm, adapter, _ = make_arm(script, max_steps=max_steps, max_tool_calls=max_tool_calls)
    answer = arm.answer(make_case())
    assert answer.error is None
    assert 1 <= adapter.calls <= max_steps + 1
    assert answer.agent["steps"] == adapter.calls
    assert answer.agent["tool_calls"] <= max_tool_calls
    assert sum(answer.agent["by_tool"].values()) == answer.agent["tool_calls"]
    assert answer.agent["budget_hit"] == (answer.agent["stop"] != "answered")
