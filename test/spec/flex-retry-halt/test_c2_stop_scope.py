"""C2: ``RunStopScope`` / ``run_stop_requested`` follow requirement 2."""

from __future__ import annotations

import contextvars
import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import RunStopScope, run_stop_requested


@pytest.mark.criterion("C2")
def test_scope_follows_its_event():
    # oracle: requirement 2 — False with no scope; the event's state inside; False after
    assert run_stop_requested() is False
    stop = threading.Event()
    scope = RunStopScope(stop)
    with scope as entered:
        assert entered is scope
        assert run_stop_requested() is False
        stop.set()
        assert run_stop_requested() is True
    assert run_stop_requested() is False
    assert stop.is_set()


@pytest.mark.criterion("C2")
def test_innermost_scope_decides_and_exit_restores():
    # oracle: requirement 2 — the innermost active scope's event decides; leaving it restores
    # the outer scope
    outer, inner = threading.Event(), threading.Event()
    outer.set()
    with RunStopScope(outer):
        assert run_stop_requested() is True
        with RunStopScope(inner):
            assert run_stop_requested() is False
            inner.set()
            assert run_stop_requested() is True
            inner.clear()
            assert run_stop_requested() is False
        assert run_stop_requested() is True
    assert run_stop_requested() is False


@pytest.mark.criterion("C2")
def test_scope_is_per_thread():
    # oracle: requirement 2 — a context variable: a thread started inside a scope whose event
    # is set has no scope of its own
    stop = threading.Event()
    stop.set()
    seen: list[object] = []

    def worker() -> None:
        try:
            seen.append(run_stop_requested())
        except BaseException as exc:  # record, so the main thread asserts on it
            seen.append(exc)

    with RunStopScope(stop):
        assert run_stop_requested() is True
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=10)
    assert seen == [False]


@pytest.mark.criterion("C2")
@given(states=st.lists(st.booleans(), min_size=1, max_size=6))
def test_property_innermost_event(states):
    # oracle: requirement 2 — for any stack of scopes, the innermost event's state; each exit
    # restores the one below; False once all are left
    events = []
    for state in states:
        event = threading.Event()
        if state:
            event.set()
        events.append(event)
    scopes = [RunStopScope(e) for e in events]

    def body() -> None:
        for depth, scope in enumerate(scopes):
            scope.__enter__()
            assert run_stop_requested() is states[depth]
        for depth in range(len(scopes) - 1, -1, -1):
            scopes[depth].__exit__(None, None, None)
            expected = states[depth - 1] if depth > 0 else False
            assert run_stop_requested() is expected

    # A copied context, so a failing example cannot leave a scope behind for later tests.
    contextvars.copy_context().run(body)
    assert run_stop_requested() is False
