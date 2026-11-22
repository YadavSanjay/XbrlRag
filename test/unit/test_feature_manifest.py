"""Tests for the H5 feature manifest + scope gate (WS-8 / H5).

Enforces the machine-checkable "done" contract in CI: the manifest is
well-formed, and every item marked ``status: done`` has a check that actually
passes (no claimed feature is fictional or regressed). Also asserts VISION.md
exists and points at the manifest.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "harness"))

import check_features as cf  # noqa: E402


@pytest.fixture(scope="module")
def items():
    return cf.load_manifest()


def test_manifest_schema_valid(items):
    errs = cf.validate_manifest(items)
    assert errs == [], "manifest schema errors:\n" + "\n".join(errs)


def test_ids_unique(items):
    ids = [it["id"] for it in items]
    assert len(ids) == len(set(ids))


def test_every_done_item_is_satisfied(items):
    """The core H5 contract — a done claim must be backed by a passing check."""
    broken = []
    for it in items:
        if it.get("status") != "done":
            continue
        res = cf.eval_item(it)
        if res["satisfied"] is not True:
            failing = [c["detail"] for c in res["checks"] if not c["passed"]]
            broken.append(f"{it['id']}: {failing}")
    assert not broken, "done items with failing checks:\n" + "\n".join(broken)


def test_todo_items_start_failing(items):
    """TODO/deferred items must NOT already be satisfied (else flip to done)."""
    ahead = [
        it["id"]
        for it in items
        if it.get("status") in ("todo", "deferred")
        and not it.get("manual")
        and cf.eval_item(it)["satisfied"] is True
    ]
    assert not ahead, f"todo/deferred items already pass — flip to done: {ahead}"


def test_gate_passes_in_process():
    assert cf.main(["--gate"]) == 0


def test_workstream_coverage(items):
    """Every workstream WS-1..WS-8 has at least one manifest entry."""
    seen = {it.get("workstream") for it in items}
    for n in range(1, 9):
        assert f"WS-{n}" in seen, f"WS-{n} not represented in the manifest"


def test_vision_doc_exists_and_links_manifest():
    vision = os.path.join(REPO_ROOT, "VISION.md")
    assert os.path.isfile(vision)
    with open(vision, encoding="utf-8") as fh:
        text = fh.read()
    assert "feature_manifest.yaml" in text
    assert "non-goal" in text.lower()  # the "decline against" scope list
