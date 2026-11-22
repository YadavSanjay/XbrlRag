"""Tests for the H6 dev-time subagents (WS-8 / H6).

Validates every committed `.claude/agents/*.md` (frontmatter shape, tool names,
name↔filename) and keeps the SUBAGENTS.md roster in sync with the files on disk —
the same "docs can't drift from reality" contract as the feature manifest (H5).
"""

from __future__ import annotations

import glob
import os

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AGENTS_DIR = os.path.join(REPO_ROOT, ".claude", "agents")
SUBAGENTS_DOC = os.path.join(REPO_ROOT, "docs", "harness", "SUBAGENTS.md")

KNOWN_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Grep",
    "Glob",
    "Bash",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
}
READ_ONLY_AGENTS = {"xbrl-answer-evaluator", "xbrl-subsystem-explorer"}


def _agent_files():
    return sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md")))


def _parse_frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert text.startswith("---"), f"{path}: no frontmatter"
    _, fm, _body = text.split("---", 2)
    return yaml.safe_load(fm)


def test_agents_present():
    names = {os.path.splitext(os.path.basename(p))[0] for p in _agent_files()}
    # the H3 evaluator plus the two H6 workers must exist
    assert {
        "xbrl-answer-evaluator",
        "xbrl-subsystem-explorer",
        "parallel-refactor-worker",
    } <= names


@pytest.mark.parametrize("path", _agent_files())
def test_agent_frontmatter_valid(path):
    fm = _parse_frontmatter(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    assert fm.get("name") == stem, f"{path}: name must equal filename"
    assert fm.get("description", "").strip(), f"{path}: missing description"
    tools = [t.strip() for t in str(fm.get("tools", "")).split(",") if t.strip()]
    assert tools, f"{path}: no tools"
    bad = set(tools) - KNOWN_TOOLS
    assert not bad, f"{path}: unknown tools {bad}"


@pytest.mark.parametrize("path", _agent_files())
def test_read_only_agents_have_no_write(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem not in READ_ONLY_AGENTS:
        return
    fm = _parse_frontmatter(path)
    tools = {t.strip() for t in str(fm.get("tools", "")).split(",")}
    assert not (
        {"Edit", "Write", "NotebookEdit"} & tools
    ), f"{stem} is declared read-only but has an edit tool"


def test_roster_doc_lists_every_agent():
    with open(SUBAGENTS_DOC, encoding="utf-8") as fh:
        doc = fh.read()
    for path in _agent_files():
        stem = os.path.splitext(os.path.basename(path))[0]
        assert stem in doc, f"{stem} missing from docs/harness/SUBAGENTS.md roster"
