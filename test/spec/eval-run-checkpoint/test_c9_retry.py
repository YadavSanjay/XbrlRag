"""C9: --retry-errors transport redoes only transport errors (requirements 14-15)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import run_checkpointed
from src.eval.core.graders.base import Grade
from src.eval.core.runner import RunnerConfig

TRANSPORT_TEXT = "RateLimitError('Error code: 429 - Resource Unavailable')"
ARM_TEXT = "ValueError('reply has no JSON object')"


def _go(kit, cases, system, store, **kwargs):
    return run_checkpointed(
        cases, [system], store=store, identities=[kit.identity_for(cases, system.name)],
        config=RunnerConfig(max_workers=1), suite="spec_suite", suite_version="v1", **kwargs)


def _first(kit, store, cases, errors):
    (report,) = _go(kit, cases, kit.FakeSystem(errors=errors), store, run_ids=["run-1"])
    return report


@pytest.mark.criterion("C9")
def test_only_the_transport_error_is_redone(kit):
    # oracle: requirement 14 — hand count: t is a transport error, e the arm's own
    cases = [kit.make_case(c) for c in ("ok", "t", "e")]
    store = kit.MemoryStore()
    first = _first(kit, store, cases, {"t": TRANSPORT_TEXT, "e": ARM_TEXT})
    assert first.config["error_origins"] == {"transport": 1, "arm": 1}
    second = kit.FakeSystem()
    (report,) = _go(kit, cases, second, store, resume="run-1", retry_errors="transport")
    assert second.answered == ["t"]
    grades = {r.case.id: r.grade.grade for r in report.results}
    assert grades == {"ok": Grade.CORRECT, "t": Grade.CORRECT, "e": Grade.ERROR}
    assert len(report.results) == 3
    assert report.config["segments"][-1]["retried"] == ["t"]
    assert report.config["error_origins"] == {"transport": 0, "arm": 1}
    assert store.cases["run-1"]["t"]["grade"] == "CORRECT"


@pytest.mark.criterion("C9")
def test_without_retry_errors_nothing_is_redone(kit):
    # oracle: requirement 14 — a finished run resumed plainly answers nothing
    cases = [kit.make_case(c) for c in ("ok", "t", "e")]
    store = kit.MemoryStore()
    _first(kit, store, cases, {"t": TRANSPORT_TEXT, "e": ARM_TEXT})
    second = kit.FakeSystem()
    (report,) = _go(kit, cases, second, store, resume="run-1")
    assert second.answered == []
    assert report.status == "OK"
    assert report.config["error_origins"] == {"transport": 1, "arm": 1}
    assert report.config["segments"][-1]["retried"] == []


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(kinds=st.lists(st.sampled_from(["ok", "transport", "arm"]), min_size=1, max_size=8))
def test_retried_set_is_exactly_the_transport_errors(kit, kinds):
    # oracle: requirement 14 — the transport-tagged ids are what the retry answers
    cases = [kit.make_case(f"c{i}") for i in range(len(kinds))]
    errors = {f"c{i}": TRANSPORT_TEXT if k == "transport" else ARM_TEXT
              for i, k in enumerate(kinds) if k != "ok"}
    store = kit.MemoryStore()
    _first(kit, store, cases, errors)
    second = kit.FakeSystem()
    (report,) = _go(kit, cases, second, store, resume="run-1", retry_errors="transport")
    want = sorted(f"c{i}" for i, k in enumerate(kinds) if k == "transport")
    assert sorted(second.answered) == want
    assert report.config["segments"][-1]["retried"] == want
    assert report.metrics.n_error == kinds.count("arm")
    assert report.metrics.n == len(kinds)
