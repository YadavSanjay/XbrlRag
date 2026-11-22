"""C8: the Markdown table and bar lines."""

from __future__ import annotations

import copy
import json

import pytest

from src.eval.core.r1_report import build_r1, render_markdown


def _cells(md: str, label: str) -> list[str]:
    """The cells after the label of the body row that begins with ``label``."""
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0] == label:
            return cells[1:]
    raise AssertionError(f"no row {label!r} in:\n{md}")


def _header(md: str) -> list[str]:
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0] == "":
            return cells[1:]
    raise AssertionError(f"no header row in:\n{md}")


@pytest.fixture()
def scenario_r1(scenario):
    return lambda: build_r1(scenario(), "X", "F", oracle="O")


@pytest.mark.criterion("C8")
def test_header_and_accuracy_cells(scenario_r1):
    # oracle: requirement 10 and the C3 hand counts (line1: X 6/8, F 3/7, O 1/2;
    # compound: F has n = 0 -> "-")
    r1 = scenario_r1()
    md = render_markdown(r1)
    assert _header(md) == ["X", "F", "O"]
    assert _cells(md, "value accuracy, pooled") == ["75.0% (6/8)", "42.9% (3/7)", "50.0% (1/2)"]
    assert _cells(md, "value accuracy, cross-filer") == ["80.0% (4/5)", "20.0% (1/5)", "-"]
    assert _cells(md, "value accuracy, compound")[1] == "-"
    assert _cells(md, "value accuracy, lookup + compute") == ["50.0% (2/4)", "75.0% (3/4)",
                                                              "100.0% (4/4)"]
    assert _cells(md, "confident-wrong") == ["16.7% (2/12)", "9.1% (1/11)", "0.0% (0/5)"]


@pytest.mark.criterion("C8")
def test_grounding_tier_and_concurrency_cells(scenario_r1):
    # oracle: requirement 10; X grounding over `all` is 0.625 (C3); configs in conftest.py
    r1 = scenario_r1()
    md = render_markdown(r1)
    assert _cells(md, "grounding precision") == ["62.5%", "not applicable", "not applicable"]
    assert _cells(md, "service tier") == ["flex", "standard", "not recorded"]
    conc = _cells(md, "concurrency")
    assert "8" in conc[0]
    assert "4" in conc[1]
    assert "not recorded" in conc[2]


@pytest.mark.criterion("C8")
def test_bar_lines(scenario_r1):
    # oracle: requirement 10; line 1 p = 0.625 (C4), every line of this scenario misses
    # except line 2, which is withdrawn
    r1 = scenario_r1()
    md = render_markdown(r1)
    assert f"{0.625:.3g}" in md
    assert "withdrawn" in md
    assert "missed" in md
    assert r1["bar"]["met"] is False


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_render_is_pure_and_deterministic(scenario_r1):
    # oracle: requirement 10 (a function of r1 alone); the expected value is the input
    r1 = scenario_r1()
    before = copy.deepcopy(r1)
    first = render_markdown(r1)
    second = render_markdown(r1)
    assert _header(first) == ["X", "F", "O"]
    assert first == second
    assert r1 == before
    assert render_markdown(json.loads(json.dumps(r1))) == first
