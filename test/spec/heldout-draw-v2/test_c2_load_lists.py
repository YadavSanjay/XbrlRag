"""C2: ``load_exclusion_lists`` and the committed lists.

Oracle: requirement 2 of docs/specs/heldout-draw-v2.md and v2 plan section 4.2 (waves 1 and
2) and 4.4 (left-outs), as quoted in the spec (``kit.WAVE1``, ``kit.WAVE2``,
``kit.LEFT_OUT``). The other YAML documents are written by hand in the test.
"""

from __future__ import annotations

import pytest

from src.eval.aprime.v2 import load_exclusion_lists


@pytest.mark.criterion("C2")
def test_committed_file_holds_the_plan_lists(kit):
    # oracle: requirement 2 / v2 plan 4.2 and 4.4 — wave1 18 filers, wave2 8, left_out 6,
    # exactly the quoted symbols
    got = load_exclusion_lists(str(kit.COMMITTED_LISTS))
    assert set(got) == {"wave1", "wave2", "left_out"}
    assert len(got["wave1"]) == 18
    assert len(got["wave2"]) == 8
    assert len(got["left_out"]) == 6
    assert set(got["wave1"]) == set(kit.WAVE1)
    assert set(got["wave2"]) == set(kit.WAVE2)
    assert set(got["left_out"]) == set(kit.LEFT_OUT)


BLOCK = """\
wave1:
  - csco
  - brk.b
wave2:
  - XOM
left_out:
  - GE
  - SNDK
"""

FLOW = '{"wave1": ["csco", "brk.b"], "wave2": ["XOM"], "left_out": ["GE", "SNDK"]}\n'

COMMENTED = """\
# v2 held-out exclusions (synthetic test copy)
wave1: [csco, brk.b]   # dev wave 1
wave2:
  - XOM                # dev wave 2
left_out:
  - GE
  - SNDK
"""

PARSED = {"wave1": ["CSCO", "BRK-B"], "wave2": ["XOM"], "left_out": ["GE", "SNDK"]}


@pytest.mark.criterion("C2")
def test_yaml_file_loads_to_parsed_lists(tmp_path):
    # oracle: requirement 2 — the file's mapping goes through requirement 1: symbols
    # normalized by hand ("csco" -> CSCO, "brk.b" -> BRK-B), order kept
    path = tmp_path / "lists.yaml"
    path.write_text(BLOCK, encoding="utf-8")
    assert load_exclusion_lists(str(path)) == PARSED


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("- wave1\n- wave2\n- left_out\n", id="document-is-a-list"),
        pytest.param(BLOCK + "wave3:\n  - ABC\n", id="unknown-key"),
        pytest.param("wave1: [CSCO]\nwave2: [XOM]\n", id="missing-key"),
    ],
)
def test_invalid_file_raises(tmp_path, text):
    # oracle: requirement 2 — a non-mapping document raises ValueError, and the mapping is
    # validated by requirement 1 (unknown or missing key)
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_exclusion_lists(str(path))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_block_flow_and_commented_styles_load_identically(tmp_path):
    # oracle: requirement 2 — the same lists written three ways are one YAML mapping, so
    # each loads to the hand-normalized PARSED
    results = []
    for name, text in (("block.yaml", BLOCK), ("flow.yaml", FLOW),
                       ("commented.yaml", COMMENTED)):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        results.append(load_exclusion_lists(str(path)))
    assert results == [PARSED, PARSED, PARSED]
