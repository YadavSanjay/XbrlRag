"""``run_xbrl`` answers through the halt boundary (docs/specs/xbrlrag-run-halt.md).

Every serving surface (BFF ``/api/query``, its SSE stream, ``/api/v1/answer`` and the
MCP tools over the v1 router) calls ``run_xbrl``; a run halt must reach them as the
halted "not answered" body, never as an exception that would become a 500.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.api import pipelines
from src.llm.run_budget import CircuitBreakerOpen


class _HaltingPipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def generate(self, query: str, user_id: str | None = None) -> Any:
        self.calls.append((query, user_id))
        raise CircuitBreakerOpen("3 consecutive LLM failures")


def test_run_xbrl_turns_a_halt_into_the_halted_body(monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = _HaltingPipeline()
    monkeypatch.setattr(pipelines, "get_xbrl_pipeline", lambda: stand_in)

    answer, query_id = pipelines.run_xbrl("q", user_id="u")

    assert stand_in.calls == [("q", "u")]
    assert answer["metadata"]["halted"] is True
    assert answer["metadata"]["halt_reason"] == "CircuitBreakerOpen"
    assert answer["unanswered_parts"] == [{
        "question_fragment": "q",
        "reason": "run halted (CircuitBreakerOpen): 3 consecutive LLM failures",
    }]
    assert query_id is None  # no trace was persisted by the stand-in
