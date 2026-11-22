"""C15: the arm reads none of our data: no fact store, no Qdrant, no ``xbrl_*``."""

from __future__ import annotations

import ast
from pathlib import Path

import psycopg2
import pytest
import qdrant_client

from src.eval.sec_tools.toolbox import tool_specs

ROOT = Path(__file__).resolve().parents[3]
START = [
    "src.eval.systems.frontier_sec_tools",
    "src.eval.sec_tools.client",
    "src.eval.sec_tools.toolbox",
]
# Shared layers the arm is meant to use; the walk does not follow them.
NOT_FOLLOWED = ("src.llm", "src.observability", "src.logging_config", "prompts")
FORBIDDEN = (
    "psycopg2",
    "qdrant_client",
    "src.db",
    "src.retriever",
    "src.vectorstores",
    "src.embeddings",
    "src.pipelines",
    "src.ingest",
    "src.eval.systems.long_context",
    "src.eval.keys.sec_live",
)
FIND = [("find_company", {"query": "AAA"})]


def _starts(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _file(module: str) -> Path | None:
    path = ROOT.joinpath(*module.split("."))
    if path.with_suffix(".py").exists():
        return path.with_suffix(".py")
    return None  # a package: its __init__ does not count (requirement 27)


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _walk() -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    queue = list(START)
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _file(module)
        if path is None:
            continue
        seen[module] = _imports(path)
        queue.extend(
            name for name in seen[module]
            if name.startswith("src.") and not _starts(name, NOT_FOLLOWED)
        )
    return seen


@pytest.mark.criterion("C15")
def test_the_import_walk_finds_none_of_the_listed_modules():
    # oracle: requirement 27's list; the module list comes from the source files themselves
    assert [s.name for s in tool_specs()]
    walked = _walk()
    assert set(START) <= set(walked)
    for module, imports in walked.items():
        bad = sorted(name for name in imports if _starts(name, FORBIDDEN))
        assert bad == [], f"{module} imports {bad}"


@pytest.mark.criterion("C15")
def test_a_run_opens_no_database_or_qdrant_connection(make_arm, make_case, monkeypatch):
    # oracle: requirement 27 — tripwires on the two drivers, the third-party boundary
    def tripped(*_args, **_kwargs):
        raise AssertionError("the arm opened a backend connection")

    monkeypatch.setattr(psycopg2, "connect", tripped)
    monkeypatch.setattr(qdrant_client.QdrantClient, "__init__", tripped)
    arm, adapter, _ = make_arm([FIND])
    answer = arm.answer(make_case())
    assert answer.error is None
    assert adapter.calls == 2


@pytest.mark.criterion("C15")
@pytest.mark.metamorphic
def test_the_backends_do_not_change_the_answer(make_arm, make_case, monkeypatch):
    # oracle: requirement 27 — the same script with every endpoint moved gives the same answer
    first, _, _ = make_arm([FIND])
    before = first.answer(make_case())
    for key in ("XBRL_DB_HOST", "QDRANT_HOST", "RAGINFER_URL"):
        monkeypatch.setenv(key, "203.0.113.1")
    second, _, _ = make_arm([FIND])
    after = second.answer(make_case())
    assert before.numeric_values == after.numeric_values == [120.0]
    assert before.chunk_ids == after.chunk_ids
    assert before.agent["sources"][0]["urls"] == after.agent["sources"][0]["urls"]
