"""Fakes and builders for the frontier-tools-v2 spec tests (docs/specs/frontier-tools-v2.md).

The fakes sit at the I/O boundaries only: an ``HttpTransport`` holding the hand-built SEC
payloads of ``ftv2_payloads``, and a provider adapter behind a real ``LLMGateway`` that plays a
script of turns with scripted usage. The arm, the toolbox, ``analyze_rows``, the SEC client and
its snapshot are real. Fixtures are session-scoped factories so hypothesis tests can use them.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.keys.base import HttpTransport
from src.eval.sec_tools.client import SecApiClient, SecSnapshot
from src.eval.sec_tools.toolbox import SecToolbox, ToolOutcome
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.tools import ToolCall
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

# The gate runs in importlib mode, so this directory is not on the path by itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ftv2_payloads import AAA_CIK, AS_OF, MODEL, sec_pages

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")


class FakeSec(HttpTransport):
    """Answers from a page table, 404 for anything else, and records every request."""

    def __init__(self, pages: dict[str, tuple[int, bytes]] | None = None) -> None:
        self.pages = sec_pages() if pages is None else dict(pages)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        with self._lock:
            self.calls.append(url)
        return self.pages.get(url, (404, b"not found"))


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


@pytest.fixture(scope="session")
def fake_sec():
    """The ``FakeSec`` class."""
    return FakeSec


@pytest.fixture(scope="session")
def make_toolbox(tmp_path_factory):
    """Factory: ``(toolbox, transport)`` on a new snapshot directory."""

    def make(
        transport: FakeSec | None = None,
        *,
        as_of: str = AS_OF,
        withheld: tuple[str, ...] = (),
        max_result_chars: int = 40000,
    ):
        transport = transport if transport is not None else FakeSec()
        snapshot = SecSnapshot(tmp_path_factory.mktemp("snapshot"), as_of=as_of, frozen=False)
        client = SecApiClient(snapshot, transport=transport,
                              identity="Spec Tester spec@example.com", sleep=lambda _s: None)
        toolbox = SecToolbox(client, as_of=as_of, withheld_ciks=withheld,
                             max_result_chars=max_result_chars)
        return toolbox, transport

    return make


@pytest.fixture(scope="session")
def source():
    """Factory: a successful earlier result holding *rows*, as ``analyze_rows`` reads it."""

    def make(rows: list[dict]) -> ToolOutcome:
        return ToolOutcome({"rows": rows[:1], "total": len(rows)},
                           ["https://data.sec.gov/x.json"], rows=rows)

    return make


# ----------------------------------------------------------------------------------
# The provider boundary.
# ----------------------------------------------------------------------------------


def answer_json(value: Any = 120, *, cited: list[str] | None = None) -> str:
    return json.dumps({
        "value": value, "unit": "USD", "rows": [], "refused": False, "hedged": False,
        "cited_chunks": cited if cited is not None else [], "statements": [],
        "text": f"The value was {value}.",
    })


class ScriptedAdapter(ProviderAdapter):
    """Plays a script of turns; each is reply text or a list of ``(tool, arguments)`` calls.

    ``usage[i]`` is the ``prompt_tokens`` the provider reports for call *i* (else
    ``prompt_tokens``). When the script runs out, or on a ``tool_choice="none"`` request, the
    adapter replies with *final*.
    """

    def __init__(
        self,
        script: list[Any] | None = None,
        *,
        final: str | None = None,
        usage: list[int] | None = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
    ) -> None:
        self.provider = "fake"
        self.script = list(script or [])
        self.final = final if final is not None else answer_json()
        self.usage = list(usage or [])
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.params: list[dict] = []
        self.calls = 0
        self._parsed = 0
        self._ids = 0
        self._lock = threading.Lock()

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        raise AssertionError("the arm must call complete_tools, not complete")

    def build_tool_params(self, **kwargs: Any) -> dict:
        return json.loads(json.dumps({
            **kwargs,
            "tools": [
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in kwargs["tools"]
            ],
        }))

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        raise AssertionError("the arm must call complete_tools, not complete")

    def invoke(self, client: Any, params: dict) -> Any:
        with self._lock:
            self.calls += 1
            self.params.append(params)
        if params.get("tool_choice") == "none":
            return self.final
        with self._lock:
            return self.script.pop(0) if self.script else self.final

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        with self._lock:
            index = self._parsed
            self._parsed += 1
            calls: list[ToolCall] = []
            if isinstance(raw, list):
                for name, arguments in raw:
                    self._ids += 1
                    calls.append(ToolCall(f"call-{self._ids}", name, arguments))
        text = raw if isinstance(raw, str) else ""
        prompt = self.usage[index] if index < len(self.usage) else self.prompt_tokens
        message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in calls],
        }
        return ParsedResponse(
            content=text,
            finish_reason="stop",
            prompt_tokens=prompt,
            completion_tokens=self.completion_tokens,
            tool_calls=calls,
            assistant_message=message,
        )

    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])

    def texts(self, call: int, role: str) -> list[str]:
        return [str(m.get("content", "")) for m in self.messages(call) if m.get("role") == role]


def real_gateway(adapter: ProviderAdapter) -> LLMGateway:
    return LLMGateway(
        stage="frontier_sec_tools",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=RunBudget(),
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
        max_attempts=1,
    )


@pytest.fixture(scope="session")
def make_arm(make_toolbox):
    """Factory: ``(arm, adapter, transport)``."""

    def make(
        script: list[Any] | None = None,
        *,
        transport: FakeSec | None = None,
        final: str | None = None,
        usage: list[int] | None = None,
        prompt_tokens: int = 1000,
        **arm_kwargs: Any,
    ):
        adapter = ScriptedAdapter(script, final=final, usage=usage, prompt_tokens=prompt_tokens)
        toolbox, sec = make_toolbox(transport)
        arm_kwargs.setdefault("universes", {"covered": [{"ticker": "AAA", "cik": AAA_CIK}],
                                            "groups": {"letters": ["AAA", "BBB"]},
                                            "as_of": AS_OF})
        arm_kwargs.setdefault("model", MODEL)
        arm_kwargs.setdefault("max_cost_usd", 1e9)
        arm = FrontierSecToolsSystem(gateway=real_gateway(adapter), toolbox=toolbox,
                                     **arm_kwargs)
        return arm, adapter, sec

    return make


@pytest.fixture(scope="session")
def make_case():
    """Factory: an eval case."""

    def make(**kwargs: Any) -> EvalCase:
        fields: dict[str, Any] = {
            "id": "case-1",
            "question": "What was Alpha Holdings' revenue in fiscal 2023?",
            "tags": ["split:dev"],
        }
        fields.update(kwargs)
        return EvalCase(**fields)

    return make


@pytest.fixture(scope="session")
def reply():
    """Factory: the arm's final reply, as text."""
    return answer_json
