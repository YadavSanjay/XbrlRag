"""Fixtures for the arm-model-from-profile spec tests (docs/specs/arm-model-from-profile.md).

The spec's tables are in ``amp_tables.py``. Here:

- ``profile_env(profile)``: sets ``XBRLRAG_LLM_PROFILE`` (or clears it, with the legacy switch)
  for a ``with`` block, plus closed-port endpoints and dummy keys, and restores everything
  afterwards (usable inside hypothesis tests, unlike ``monkeypatch``);
- ``build_arm(name)``: one of the four arm classes, built with no model and with fakes at
  the I/O boundaries (resolver, store, gateway); constructing does no I/O;
- ``FakeAdapter`` / ``oracle_arm_with_fakes``: the provider boundary behind a real
  ``LLMGateway``, a ``FilingResolver`` fake and an in-memory ``FilingStore``.

No network, no database, no data/ and no real keys.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.eval.systems.base import FilingResolver
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.eval.systems.long_context import LongContextSystem
from src.eval.systems.long_context_arms import LongContextMultiSystem, LongContextResolvedSystem
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.gateway.adapters.base import ParsedResponse, ProviderAdapter
from src.llm.run_budget import RunBudget
from src.observability.trace_context import get_audit_sink, set_audit_sink

# The gate runs in importlib mode, so this directory is not on the path by itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from amp_tables import LEGACY_ENV, PROFILE_ENV

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
RUN_SUITE = REPO_ROOT / "scripts" / "eval" / "run_suite.py"

_CLEARED = (PROFILE_ENV, LEGACY_ENV, "ARAG_LLM_PROFILE", "ARAG_USE_REPLACEMENT_MODELS")
PROVIDERS = ("openai", "openai_compat", "anthropic", "deepseek", "huggingface")

_OFFLINE = {
    "QDRANT_HOST": "127.0.0.1",
    "QDRANT_PORT": "9",
    "RAGINFER_URL": "http://127.0.0.1:9",
    "RAGINFER_FALLBACK_LOCAL": "false",
    "XBRL_DB_HOST": "127.0.0.1",
    "XBRL_DB_PORT": "9",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
    "OPENAI_API_KEY": "sk-spec-offline",
    "ANTHROPIC_API_KEY": "sk-ant-spec-offline",
    "DEEPSEEK_API_KEY": "sk-ds-spec-offline",
    "XBRLRAG_SEC_AS_OF": "2026-10-01",
    "SEC_EDGAR_IDENTITY": "spec-tests spec@example.invalid",
}

AAA_KEY = "AAA/2024/10-K/canonical.htm"


@pytest.fixture(scope="session", autouse=True)
def _no_audit_sink():
    """Keeps the process-wide audit sink off for the session (the DB boundary)."""
    previous = get_audit_sink()
    set_audit_sink(None)
    try:
        yield
    finally:
        set_audit_sink(previous)


@contextlib.contextmanager
def profile_env(profile: str | None, **extra: str) -> Iterator[None]:
    """Selects *profile* through ``XBRLRAG_LLM_PROFILE`` (None: nothing chosen anywhere)."""
    names = set(_OFFLINE) | set(_CLEARED) | set(extra)
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in _CLEARED:
            os.environ.pop(name, None)
        os.environ.update(_OFFLINE)
        if profile is not None:
            os.environ[PROFILE_ENV] = profile
        os.environ.update(extra)
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="session")
def env():
    """The ``profile_env`` context manager."""
    return profile_env


# ---------------------------------------------------------------------------- fakes


class FakeAdapter(ProviderAdapter):
    """The provider boundary: records each request, answers with a fixed reply."""

    def __init__(self, reply: str) -> None:
        self.provider = "fake"
        self.reply = reply
        self.params: list[dict] = []
        self.stages: list[str] = []

    def get_client(self) -> Any:
        return object()

    def build_params(self, **kwargs: Any) -> dict:
        return kwargs

    def invoke(self, client: Any, params: dict) -> Any:
        self.params.append(params)
        return "raw"

    def parse(self, raw: Any, *, stage: str) -> ParsedResponse:
        self.stages.append(stage)
        return ParsedResponse(
            content=self.reply, finish_reason="stop", prompt_tokens=1000,
            completion_tokens=100, reasoning_tokens=0, cached_prompt_tokens=0,
        )


class FakeResolver(FilingResolver):
    """The filing registry: one fixed key per (ticker, fiscal year)."""

    def __init__(self, keys: dict[tuple[str, int], str]) -> None:
        self.keys = dict(keys)

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        return self.keys.get((ticker, fiscal_year))


class MemoryStore(FilingStore):
    """The filing store, in memory."""

    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = {k: v.encode("utf-8") for k, v in objects.items()}

    def write_bytes(self, key: str, data: bytes) -> None:
        self.objects[key] = bytes(data)

    def read_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        return iter(())

    def localize_dir(self, prefix: str):
        raise RuntimeError("the in-memory store has no local directories")


def _gateway(stage: str, adapter: FakeAdapter) -> LLMGateway:
    return LLMGateway(
        stage=stage, cost_tracker=CostTracker(), rate_limiter=None, run_budget=RunBudget(),
        max_attempts=1, sleep_fn=lambda _s: None, adapters={p: adapter for p in PROVIDERS},
    )


_XHTML = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
    "<body><p>AAA total revenue was 7 dollars.</p></body></html>"
)
_REPLY = json.dumps({"value": 7, "unit": "USD", "rows": [], "refused": False,
                     "hedged": False, "cited_chunks": []})


def _build(name: str, **kwargs: Any):
    """One arm with fakes at its I/O boundaries; *kwargs* go to the constructor."""
    resolver = FakeResolver({("AAA", 2024): AAA_KEY})
    store = MemoryStore({AAA_KEY: _XHTML})
    if name == "long_context_oracle":
        adapter = FakeAdapter(_REPLY)
        arm = LongContextSystem(gateway=_gateway("long_context", adapter), resolver=resolver,
                                store=store, **kwargs)
        return arm, adapter
    if name == "long_context_resolved":
        return LongContextResolvedSystem(resolver=resolver, store=store, **kwargs), None
    if name == "long_context_multi":
        return LongContextMultiSystem(resolver=resolver, store=store, **kwargs), None
    if name == "frontier_sec_tools":
        universes = {"covered": [{"ticker": "AAA", "cik": "0000000001"}],
                     "groups": {"letters": ["AAA"]}, "as_of": "2026-10-01"}
        return FrontierSecToolsSystem(universes=universes, **kwargs), None
    raise AssertionError(f"unknown arm {name!r}")


@pytest.fixture(scope="session")
def build_arm():
    """Factory: ``(arm, adapter or None)`` for one of ``ARM_NAMES``."""
    return _build


@pytest.fixture(scope="session")
def run_suite_module():
    """``scripts/eval/run_suite.py`` imported by path (it is a script, not a package)."""
    name = "run_suite_arm_model_from_profile_under_test"
    spec_ = importlib.util.spec_from_file_location(name, RUN_SUITE)
    module = importlib.util.module_from_spec(spec_)
    sys.modules[name] = module
    spec_.loader.exec_module(module)
    return module
