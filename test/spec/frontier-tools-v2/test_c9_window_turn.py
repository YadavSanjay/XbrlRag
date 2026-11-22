"""C9: a turn whose reported prompt crossed the window closes the tools."""

from __future__ import annotations

import json

import pytest
from ftv2_payloads import TICKERS_URL
from hypothesis import example, given, settings
from hypothesis import strategies as st

WINDOW = 50_000
CALLS = [("find_company", {"query": "AAA"}), ("calculate", {"expression": "1+1"})]


def _tool_results(adapter, call: int) -> list[dict]:
    return [json.loads(t) for t in adapter.texts(call, "tool")]


@pytest.mark.criterion("C9")
def test_calls_of_a_turn_past_the_window_are_not_run(make_arm, make_case):
    # oracle: the scripted gateway reports 60,000 prompt tokens against a 50,000 window on a
    # turn asking for two tools; the transport counts the find_company execution's request
    arm, adapter, sec = make_arm([CALLS, CALLS], usage=[60_000], max_input_tokens=WINDOW)
    answer = arm.answer(make_case())
    results = _tool_results(adapter, 1)
    assert [r.get("error") for r in results] == ["window", "window"]
    assert TICKERS_URL not in sec.calls
    assert answer.agent["sources"] == []
    assert adapter.calls == 2
    assert adapter.params[1]["tool_choice"] == "none"
    assert answer.agent["stop"] == "window"


@pytest.mark.criterion("C9")
def test_a_turn_below_the_window_runs_its_calls(make_arm, make_case):
    # oracle: requirement 9 — "above" max_input_tokens: a turn reporting 40,000 of 50,000 runs
    # both calls; the same script at 60,000 runs none
    arm, adapter, sec = make_arm([CALLS], usage=[40_000], max_input_tokens=WINDOW)
    answer = arm.answer(make_case())
    assert [r.get("source_id") for r in _tool_results(adapter, 1)] == ["S1", "S2"]
    assert TICKERS_URL in sec.calls
    assert answer.agent["stop"] == "answered"
    arm, adapter, sec = make_arm([CALLS], usage=[60_000], max_input_tokens=WINDOW)
    answer = arm.answer(make_case())
    assert TICKERS_URL not in sec.calls
    assert answer.agent["stop"] == "window"


@pytest.mark.criterion("C9")
@settings(max_examples=20)
@given(prompt=st.one_of(st.integers(1, WINDOW - 5_000), st.integers(WINDOW + 1, 3 * WINDOW)))
@example(prompt=WINDOW + 1)
def test_the_reported_count_alone_decides(make_arm, make_case, prompt):
    # oracle: requirement 9 — above WINDOW the calls get window errors, run nothing and the
    # next turn is final with stop "window"; at or well below it (5,000 tokens of room for the
    # small results) they run and the case is answered
    arm, adapter, sec = make_arm([CALLS], usage=[prompt], max_input_tokens=WINDOW)
    answer = arm.answer(make_case())
    results = _tool_results(adapter, 1)
    if prompt > WINDOW:
        assert [r.get("error") for r in results] == ["window", "window"]
        assert TICKERS_URL not in sec.calls
        assert adapter.params[1]["tool_choice"] == "none"
        assert answer.agent["stop"] == "window"
    else:
        assert [r.get("source_id") for r in results] == ["S1", "S2"]
        assert adapter.params[1]["tool_choice"] == "auto"
        assert answer.agent["stop"] == "answered"
