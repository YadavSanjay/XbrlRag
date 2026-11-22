"""C7: the new halts are run halts and stay resumable (requirements 1, 10)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import error_origin
from src.llm.run_budget import (
    CircuitBreakerOpen,
    FlexWaitExceeded,
    RunBudgetExceeded,
    RunHalt,
    RunStopped,
)


@pytest.mark.criterion("C7")
def test_halts_are_run_halts_and_transport_errors():
    # oracle: requirements 1 and 10 — RunHalt subclasses, distinct from each other and the
    # existing halts; their repr text is a transport error, as CircuitBreakerOpen's already is
    for cls in (FlexWaitExceeded, RunStopped):
        assert issubclass(cls, RunHalt)
        for other in (CircuitBreakerOpen, RunBudgetExceeded):
            assert not issubclass(cls, other)
    assert not issubclass(FlexWaitExceeded, RunStopped)
    assert not issubclass(RunStopped, FlexWaitExceeded)
    assert error_origin(repr(CircuitBreakerOpen("3 consecutive LLM failures"))) == "transport"
    flex_text = repr(FlexWaitExceeded("trace waited 3330 s on Flex; cap 3600 s"))
    stop_text = repr(RunStopped("the run was stopped"))
    assert flex_text.startswith("FlexWaitExceeded(")
    assert stop_text.startswith("RunStopped(")
    assert error_origin(flex_text) == "transport"
    assert error_origin(stop_text) == "transport"
    assert error_origin("FlexWaitExceeded('x')") == "transport"
    assert error_origin('RunStopped("x")') == "transport"


@pytest.mark.criterion("C7")
@given(message=st.text(alphabet=st.characters(blacklist_categories=("Nd", "Cs")), max_size=80))
def test_property_any_message_is_transport(message):
    # oracle: requirement 10 — with no HTTP status in the message (no digits), the class name
    # decides, whatever the message
    for cls in (FlexWaitExceeded, RunStopped):
        assert error_origin(repr(cls(message))) == "transport"
