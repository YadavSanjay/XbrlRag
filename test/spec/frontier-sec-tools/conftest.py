"""Fakes and builders for the frontier-sec-tools spec tests (docs/specs/frontier-sec-tools.md).

The fakes sit at the I/O boundaries and nowhere else: a provider adapter or SDK client behind a
real ``LLMGateway``, and an ``HttpTransport`` holding hand-built SEC payloads. The arm, the
toolbox, the client, the snapshot, the prompt registry and the answer parser are real.

AAA, BBB, CCC and HOLD are synthetic, in the SEC's published JSON shapes. HOLD plays a held-out
filer. Fixtures are session-scoped factories so hypothesis-driven tests can use them.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.keys.base import HttpTransport
from src.eval.sec_tools.client import SecApiClient, SecSnapshot
from src.eval.sec_tools.toolbox import SecToolbox
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.llm.config import MODEL_PRICING
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.gateway.tools import ToolCall
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

# The gate runs in importlib mode, so this directory is not on the path by itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sec_payloads import (
    AAA_CIK,
    AS_OF,
    FTS_URL,
    MODEL,
    fts_body,
    sec_pages,
)

PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")


class FakeSec(HttpTransport):
    """Answers from a page table, 404 for anything else, and records every request."""

    def __init__(self, pages: dict[str, tuple[int, bytes]] | None = None) -> None:
        self.pages = sec_pages() if pages is None else dict(pages)
        self.script: dict[str, list[tuple[int, bytes]]] = {}
        self.error: BaseException | None = None
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]

    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        with self._lock:
            self.calls.append((url, dict(headers)))
            if self.error is not None:
                raise self.error
            queued = self.script.get(url)
            if queued:
                return queued.pop(0)
        if url.startswith(FTS_URL):
            return 200, fts_body()
        return self.pages.get(url, (404, b"not found"))


class FakeTime:
    """A shared monotonic clock whose ``sleep`` advances it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)
            self.now += seconds


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous_sink = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous_sink)


@pytest.fixture
def offline_env(monkeypatch):
    """Points every backend at a closed local port, with dummy API keys."""
    for key, value in {
        "QDRANT_HOST": "127.0.0.1",
        "QDRANT_PORT": "9",
        "RAGINFER_URL": "http://127.0.0.1:9",
        "XBRL_DB_HOST": "127.0.0.1",
        "XBRL_DB_PORT": "9",
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
        "OPENAI_API_KEY": "sk-spec-offline",
        "ANTHROPIC_API_KEY": "sk-ant-spec-offline",
    }.items():
        monkeypatch.setenv(key, value)
    yield


@pytest.fixture(scope="session")
def fake_sec():
    """Factory: a ``FakeSec``, on the hand-built pages unless given others."""
    return FakeSec


@pytest.fixture(scope="session")
def make_client(tmp_path_factory):
    """Factory: ``(client, snapshot, transport)`` on a new snapshot directory."""

    def make(
        transport: FakeSec | None = None,
        *,
        as_of: str = AS_OF,
        frozen: bool = False,
        root: Any = None,
        **client_kwargs: Any,
    ):
        transport = transport if transport is not None else FakeSec()
        root = root if root is not None else tmp_path_factory.mktemp("snapshot")
        snapshot = SecSnapshot(root, as_of=as_of, frozen=frozen)
        client_kwargs.setdefault("identity", "Spec Tester spec@example.com")
        client_kwargs.setdefault("sleep", lambda _s: None)
        client = SecApiClient(snapshot, transport=transport, **client_kwargs)
        return client, snapshot, transport

    return make


@pytest.fixture(scope="session")
def make_toolbox(make_client):
    """Factory: ``(toolbox, transport)``."""

    def make(
        transport: FakeSec | None = None,
        *,
        as_of: str = AS_OF,
        frozen: bool = False,
        withheld: tuple[str, ...] = (),
        max_result_chars: int = 40000,
    ):
        client, _, sec = make_client(transport, as_of=as_of, frozen=frozen)
        toolbox = SecToolbox(
            client, as_of=as_of, withheld_ciks=withheld, max_result_chars=max_result_chars
        )
        return toolbox, sec

    return make


# ----------------------------------------------------------------------------------
# The provider boundary.
# ----------------------------------------------------------------------------------


def answer_json(
    value: Any = 120,
    *,
    cited: list[str] | None = None,
    statements: list[dict] | None = None,
    refused: bool = False,
    hedged: bool = False,
    text: str = "Revenue was 120.",
) -> str:
    return json.dumps({
        "value": value, "unit": "USD", "rows": [], "refused": refused, "hedged": hedged,
        "cited_chunks": cited if cited is not None else [],
        "statements": statements if statements is not None else [],
        "text": text,
    })


class ScriptedAdapter(ProviderAdapter):
    """Plays a script of turns; each is reply text, or a list of ``(tool, arguments)`` calls.

    A callable turn gets the request's parameters and returns one of those. When the script
    runs out, or on a ``tool_choice="none"`` request, the adapter replies with *final*, unless
    *final_calls* says the provider ignores ``"none"``.
    """

    def __init__(
        self,
        script: list[Any] | None = None,
        *,
        final: str | None = None,
        final_calls: list[tuple[str, dict]] | None = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        errors: dict[int, BaseException] | None = None,
    ) -> None:
        self.provider = "fake"
        self.script = list(script or [])
        self.final = final if final is not None else answer_json()
        self.final_calls = final_calls
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.errors = dict(errors or {})
        self.params: list[dict] = []
        self.stages: list[str] = []
        self.calls = 0
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
            index = self.calls
            self.calls += 1
            self.params.append(params)
        if index in self.errors:
            raise self.errors[index]
        if params.get("tool_choice") == "none":
            return self.final_calls if self.final_calls is not None else self.final
        with self._lock:
            turn = self.script.pop(0) if self.script else self.final
        return turn(params) if callable(turn) else turn

    def parse_tool_turn(self, raw: Any, *, stage: str) -> ParsedResponse:
        with self._lock:
            self.stages.append(stage)
            calls: list[ToolCall] = []
            if isinstance(raw, list):
                for name, arguments in raw:
                    self._ids += 1
                    calls.append(ToolCall(f"call-{self._ids}", name, arguments))
        text = raw if isinstance(raw, str) else ""
        message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                           for c in calls],
            "provider_state": {"opaque": True},
        }
        return ParsedResponse(
            content=text,
            finish_reason="stop",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            tool_calls=calls,
            assistant_message=message,
        )

    def messages(self, call: int = 0) -> list[dict]:
        return list(self.params[call]["messages"])

    def texts(self, call: int, role: str) -> list[str]:
        return [str(m.get("content", "")) for m in self.messages(call) if m.get("role") == role]


def real_gateway(adapter: ProviderAdapter, run_budget: RunBudget | None = None, **kwargs: Any):
    kwargs.setdefault("max_attempts", 1)
    return LLMGateway(
        stage="frontier_sec_tools",
        cost_tracker=CostTracker(),
        rate_limiter=None,
        run_budget=run_budget if run_budget is not None else RunBudget(),
        sleep_fn=lambda _s: None,
        adapters={p: adapter for p in PROVIDERS},
        **kwargs,
    )


@pytest.fixture(scope="session")
def gateway_on():
    """Factory: a real gateway over a fake adapter."""
    return real_gateway


@pytest.fixture(scope="session")
def scripted():
    """The ``ScriptedAdapter`` class."""
    return ScriptedAdapter


@pytest.fixture(scope="session")
def reply():
    """Factory: the arm's final reply, as text."""
    return answer_json


def call_cost(prompt: int, completion: int, model: str = MODEL) -> float:
    """One call's cost at the pricing table's rates, per 1,000 tokens."""
    rates = MODEL_PRICING[model]
    return (prompt / 1000.0) * rates["input"] + (completion / 1000.0) * rates["output"]


@pytest.fixture(scope="session")
def turn_cost():
    """Factory: one call's cost, by the closed form."""
    return call_cost


@pytest.fixture(scope="session")
def make_arm(make_toolbox):
    """Factory: ``(arm, adapter, transport)``."""

    def make(
        script: list[Any] | None = None,
        *,
        adapter: ProviderAdapter | None = None,
        transport: FakeSec | None = None,
        withheld: tuple[str, ...] = (),
        frozen: bool = False,
        run_budget: RunBudget | None = None,
        final: str | None = None,
        final_calls: list[tuple[str, dict]] | None = None,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        errors: dict[int, BaseException] | None = None,
        **arm_kwargs: Any,
    ):
        if adapter is None:
            adapter = ScriptedAdapter(
                script, final=final, final_calls=final_calls, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, errors=errors,
            )
        toolbox, sec = make_toolbox(transport, withheld=withheld, frozen=frozen)
        arm_kwargs.setdefault("universes", {"covered": [{"ticker": "AAA", "cik": AAA_CIK}],
                                            "groups": {"letters": ["AAA", "BBB"]},
                                            "as_of": AS_OF})
        # The model these tests price, named: arm-model-from-profile makes the default the
        # active profile's frontier model.
        arm_kwargs.setdefault("model", MODEL)
        arm = FrontierSecToolsSystem(
            gateway=real_gateway(adapter, run_budget), toolbox=toolbox, **arm_kwargs
        )
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
def sample_calls() -> Callable[[], list[tuple[str, dict]]]:
    """Factory: one tool call that succeeds on the hand-built SEC."""

    def make() -> list[tuple[str, dict]]:
        return [("find_company", {"query": "AAA"})]

    return make
