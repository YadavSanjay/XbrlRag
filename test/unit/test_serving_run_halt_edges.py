"""A run halt raised inside the XBRL pipeline reaches every serving edge as the halted body.

Test strategy area K (docs/design/XbrlRag_Test_Strategy.md); contract in
docs/specs/xbrlrag-run-halt.md and src/api/CLAUDE.md: a ``RunHalt`` becomes the defined
"not answered: run halted" answer (``metadata.halted``, no statements) on the BFF
``/api/query``, its SSE stream, ``/api/v1/answer`` and the MCP ``answer`` tool. It is never
swallowed into an error body or a 500, and never a 200 carrying a partial answer. Any other
exception keeps each surface's own error contract (BFF ``error`` field, v1 500).

The pipeline is faked at its boundary (``get_xbrl_pipeline``); routes, ``run_xbrl`` and the
MCP dispatch are real.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api import pipelines
from src.api.main import app
from src.llm.run_budget import (
    CircuitBreakerOpen,
    FlexWaitExceeded,
    RunBudgetExceeded,
    RunHalt,
    RunStopped,
)
from src.mcp_server.server import mcp

HALTS = [RunBudgetExceeded, CircuitBreakerOpen, FlexWaitExceeded, RunStopped]
QUESTION = "What was NVDA revenue in FY2026?"

client = TestClient(app)


class _RaisingPipeline:
    """Stands in for XbrlRetrievalPipeline: ``generate`` raises *exc*."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    def generate(self, query: str, user_id: str | None = None) -> Any:
        self.calls += 1
        raise self._exc


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch):
    def install(exc: Exception) -> _RaisingPipeline:
        fake = _RaisingPipeline(exc)
        monkeypatch.setattr(pipelines, "get_xbrl_pipeline", lambda: fake)
        return fake
    return install


def _assert_halted(answer: dict, halt: type[RunHalt]) -> None:
    assert answer["metadata"]["halted"] is True
    assert answer["metadata"]["halt_reason"] == halt.__name__
    assert answer["metadata"]["fully_answered"] is False
    # Not a partial answer: no statements, no summary.
    assert answer["answer_details"] == []
    assert answer["answer_summary"] == ""
    assert [p["question_fragment"] for p in answer["unanswered_parts"]] == [QUESTION]
    assert answer["unanswered_parts"][0]["reason"].startswith(f"run halted ({halt.__name__})")


def _sse_events(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


def _mcp_answer(query: str) -> dict:
    content, _ = asyncio.run(mcp.call_tool("answer", {"query": query}))
    return json.loads(content[0].text)


def test_every_runhalt_subclass_is_covered():
    assert set(RunHalt.__subclasses__()) == set(HALTS)


@pytest.mark.parametrize("halt", HALTS)
@pytest.mark.parametrize("mode", ["auto", "xbrl"])
def test_bff_query_returns_the_halted_body(pipeline, halt, mode):
    fake = pipeline(halt("stop"))

    resp = client.post("/api/query", json={"query": QUESTION, "mode": mode})

    assert fake.calls == 1
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None  # the halt is an answer, not a swallowed error
    _assert_halted(body["answer"], halt)


@pytest.mark.parametrize("halt", HALTS)
def test_bff_stream_ends_with_the_halted_body(pipeline, halt):
    pipeline(halt("stop"))

    resp = client.get("/api/query/stream", params={"query": QUESTION, "mode": "xbrl"})

    assert resp.status_code == 200
    result = _sse_events(resp.text)[-1]
    assert result["kind"] == "result"
    assert "error" not in result
    _assert_halted(result["answer"], halt)


@pytest.mark.parametrize("halt", HALTS)
def test_v1_answer_returns_the_halted_body(pipeline, halt):
    pipeline(halt("stop"))

    resp = client.post("/api/v1/answer", json={"query": QUESTION})

    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"]["halted"] is True
    assert body["metadata"]["halt_reason"] == halt.__name__
    assert body["answer_details"] == [] and body["answer_summary"] == ""


@pytest.mark.parametrize("halt", HALTS)
def test_mcp_answer_returns_the_halted_body(pipeline, halt):
    pipeline(halt("stop"))

    out = _mcp_answer(QUESTION)

    assert out.get("error") is None and "status_code" not in out
    assert out["metadata"]["halted"] is True
    assert out["metadata"]["halt_reason"] == halt.__name__
    assert out["answer_details"] == [] and out["answer_summary"] == ""


# A non-halt failure is not dressed up as a halt: each surface keeps its error contract.


def test_bff_reports_other_failures_in_its_error_field(pipeline):
    pipeline(RuntimeError("db down"))
    body = client.post("/api/query", json={"query": QUESTION, "mode": "xbrl"}).json()
    assert body["error"] == "RuntimeError: db down"
    assert body["answer"] == {}


def test_v1_reports_other_failures_as_500(pipeline):
    pipeline(RuntimeError("db down"))
    resp = client.post("/api/v1/answer", json={"query": QUESTION})
    assert resp.status_code == 500
    assert "RuntimeError" in resp.json()["detail"]


def test_mcp_reports_other_failures_as_an_error_payload(pipeline):
    pipeline(RuntimeError("db down"))
    out = _mcp_answer(QUESTION)
    assert out["status_code"] == 500
    assert "RuntimeError" in out["error"]
