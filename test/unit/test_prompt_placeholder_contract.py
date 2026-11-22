"""Prompt placeholders match the keyword arguments their call sites pass.

Test strategy area K (docs/design/XbrlRag_Test_Strategy.md); .claude/rules/rag.md:
"placeholders match the ``.format(...)`` arguments exactly". For every stage of the XBRL
prompt registry, the placeholders of its resolved (default-version) templates are compared
with the keyword names its call sites pass, read from the source by AST. Either side
drifting fails here: a template gaining or losing a placeholder, or a call site gaining or
losing a keyword. A template a call site uses raw (``.system.template``, never formatted)
is recorded as such, and must then have no ``.format`` call.
"""

from __future__ import annotations

import ast
import string
from pathlib import Path

import pytest

from prompts.prompt_registry import get_xbrl_registry

REPO = Path(__file__).resolve().parents[2]
RAW = "raw"

# stage -> part -> (source files that render that part, receiver attribute names).
# ``_stage_llm.py`` renders the system prompt for every StageLLM arm.
_STAGE_LLM = "src/eval/systems/_stage_llm.py"
CALL_SITES: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]] | str]] = {
    "query_decomposer": {
        "system": RAW,
        "user": (("src/retriever/xbrl/query_decomposer.py",), ("user",)),
    },
    "intent_router": {
        "system": (("src/retriever/xbrl/intent_router.py",), ("system",)),
        "user": (("src/retriever/xbrl/intent_router.py",), ("user",)),
    },
    "plan_generator": {
        "system": RAW,
        "user": (("src/retriever/xbrl/plan_generator.py",), ("user",)),
    },
    "synthesizer": {
        "system": (("src/retriever/xbrl/llm_xbrl_synthesizer.py",), ("system",)),
        "user": (("src/retriever/xbrl/llm_xbrl_synthesizer.py",), ("user",)),
    },
    "faithfulness_verifier": {
        "system": (("src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py",), ("system",)),
        "user": (("src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py",), ("user",)),
    },
    "closed_book": {
        "system": ((_STAGE_LLM,), ("system",)),
        "user": (("src/eval/systems/closed_book.py",), ("user",)),
    },
    "naive_rag": {
        "system": ((_STAGE_LLM,), ("system",)),
        "user": (("src/eval/systems/naive_rag.py",), ("user",)),
    },
    "html_rag": {
        "system": ((_STAGE_LLM,), ("system",)),
        "user": (("src/eval/systems/html_rag.py",), ("user",)),
    },
    # The long-context user prompt is split at its cache-point line into two templates.
    "long_context": {
        "system": ((_STAGE_LLM,), ("system",)),
        "user": (("src/eval/systems/long_context.py",),
                 ("_filing_template", "_question_template")),
    },
    "long_context_multi": {
        "system": ((_STAGE_LLM,), ("system",)),
        "user": (("src/eval/systems/long_context_arms.py",),
                 ("_filing_template", "_question_template")),
    },
    "frontier_sec_tools": {
        "system": (("src/eval/systems/frontier_sec_tools.py",), ("system",)),
        "user": (("src/eval/systems/frontier_sec_tools.py",), ("user",)),
    },
}

# Files whose stage name appears as a literal, proving the table points at the right code.
_STAGE_FILE = {
    "query_decomposer": "src/retriever/xbrl/query_decomposer.py",
    "intent_router": "src/retriever/xbrl/intent_router.py",
    "plan_generator": "src/retriever/xbrl/plan_generator.py",
    "synthesizer": "src/retriever/xbrl/llm_xbrl_synthesizer.py",
    "faithfulness_verifier": "src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py",
    "closed_book": "src/eval/systems/closed_book.py",
    "naive_rag": "src/eval/systems/naive_rag.py",
    "html_rag": "src/eval/systems/html_rag.py",
    "long_context": "src/eval/systems/long_context.py",
    "long_context_multi": "src/eval/systems/long_context_arms.py",
    "frontier_sec_tools": "src/eval/systems/frontier_sec_tools.py",
}

_RECEIVERS = {"system", "user", "_filing_template", "_question_template"}
# Rendered from the eval registry (prompts/templates/eval), not the XBRL one.
_OTHER_REGISTRY_SITES = {"src/eval/core/graders/judge.py"}


def _format_calls(path: str) -> list[tuple[str, frozenset[str]]]:
    """``(receiver attribute, keyword names)`` for each ``<x>.<receiver>.format(...)`` call."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in _RECEIVERS):
            assert all(k.arg for k in node.keywords), f"{path}: **kwargs hides the names"
            assert not node.args, f"{path}: positional format arguments"
            calls.append((node.func.value.attr, frozenset(k.arg for k in node.keywords)))
    return calls


def _raw_template_uses(path: str) -> int:
    """Count of ``<x>.system.template`` reads in *path*."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "template"
        and isinstance(node.value, ast.Attribute) and node.value.attr == "system"
    )


def _placeholders(text: str) -> set[str]:
    """Field names ``str.format`` would substitute (raises on unbalanced braces)."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_every_registry_stage_has_its_call_sites_listed():
    assert set(get_xbrl_registry().stages()) == set(CALL_SITES)


@pytest.mark.parametrize("stage", sorted(_STAGE_FILE))
def test_listed_file_is_the_stage_owner(stage):
    assert f'"{stage}"' in (REPO / _STAGE_FILE[stage]).read_text(encoding="utf-8")


def test_no_unlisted_format_call_site():
    listed = {f for parts in CALL_SITES.values() for spec in parts.values()
              if spec != RAW for f in spec[0]}
    found = {
        p.relative_to(REPO).as_posix()
        for root in ("src", "scripts", "prompts")
        for p in (REPO / root).rglob("*.py")
        if _format_calls(p.relative_to(REPO).as_posix())
    }
    assert found - _OTHER_REGISTRY_SITES == listed


@pytest.mark.parametrize("stage", sorted(CALL_SITES))
@pytest.mark.parametrize("part", ["system", "user"])
def test_template_placeholders_equal_call_site_kwargs(stage, part):
    prompt = get_xbrl_registry().resolve(stage)
    text = getattr(prompt, part).template
    spec = CALL_SITES[stage][part]

    if spec == RAW:
        owner = _STAGE_FILE[stage]
        assert _raw_template_uses(owner) >= 1, f"{owner} no longer reads the {part} raw"
        assert not [c for c in _format_calls(owner) if c[0] == part], (
            f"{owner} formats the {part} template it is listed as using raw")
        return

    files, receivers = spec
    calls = [c for f in files for c in _format_calls(f) if c[0] in receivers]
    assert calls, f"{stage}.{part}: no call site found in {files}"
    if receivers == ("_filing_template", "_question_template"):
        # Each half is formatted with its own keyword; together they cover the template.
        passed = set().union(*(kw for _, kw in calls))
        for _, kw in calls:
            assert len(kw) == 1
    else:
        kwargs = {kw for _, kw in calls}
        assert len(kwargs) == 1, f"{stage}.{part}: call sites disagree: {kwargs}"
        passed = set(next(iter(kwargs)))
    assert _placeholders(text) == passed, f"{prompt.ref}/{part}"
