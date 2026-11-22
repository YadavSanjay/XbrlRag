"""C7: held-out reports carry aggregates only; dev reports list the case-level detail."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import build_r1, render_markdown


def _arms(row, arm, ids, split="dev", suite="xbrl_v2_dev", extra_tags=(), config=None):
    x, f = [], []
    for i, cid in enumerate(ids):
        tags = [f"split:{split}", "shape:screen.top", *extra_tags]
        x.append(row(cid, vc=True, tags=tags))
        f.append(row(cid, vc=i % 2 == 0, tags=tags))
    return [arm("X", x, split=split, suite=suite, config=config),
            arm("F", f, split=split, suite=suite, config=config)]


IDS = ["hq-7f3a-alpha", "hq-7f3a-bravo", "hq-7f3a-charlie"]


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("trigger", ["heldout_filers", "external", "temporal", "suite",
                                     "set_b_one_case"])
def test_each_heldout_trigger_hides_cases(row, arm, trigger):
    # oracle: requirement 9, the held-out checks of run_report.py (split, suite prefix
    # `heldout` in any letter case, a `set-b` tag on any case)
    if trigger == "suite":
        arms = _arms(row, arm, IDS, split="legacy", suite="HeldOut_x_v1")
    elif trigger == "set_b_one_case":
        arms = _arms(row, arm, IDS, split="legacy")
        arms[1]["cases"][2]["tags"].append("set-b")
    else:
        arms = _arms(row, arm, IDS, split=trigger)
    r1 = build_r1(arms, "X", "F")
    assert r1["heldout"] is True
    assert r1["case_level"] is None
    text = json.dumps(r1, allow_nan=False) + render_markdown(r1)
    for cid in IDS:
        assert cid not in text


@pytest.mark.criterion("C7")
def test_dev_case_level_by_hand(scenario):
    # oracle: hand pairing of the conftest scenario over `all`
    r1 = build_r1(scenario(), "X", "F", oracle="O")
    assert r1["heldout"] is False
    cl = r1["case_level"]
    assert cl["missing"] == {"X": [], "F": ["k1"], "O": []}
    assert cl["excluded"] == {"X": [], "F": [],
                              "O": ["d1", "k1", "p1", "s1", "s2", "s3", "s4"]}
    assert cl["discordant"]["F"] == {"reference_only": ["d1", "l2", "p1", "s1", "s3"],
                                     "other_only": ["c1", "g1"]}
    assert cl["discordant"]["O"] == {"reference_only": ["t1"], "other_only": ["c1", "g1"]}
    json.dumps(r1, allow_nan=False)


@pytest.mark.criterion("C7")
@given(tokens=st.lists(st.text(alphabet="abcdefghjkmnpqrstuvwxyz", min_size=8, max_size=12),
                       min_size=1, max_size=8, unique=True),
       split=st.sampled_from(["heldout_filers", "external", "temporal", "dev"]))
def test_ids_never_leak(row, arm, tokens, split):
    # oracle: requirement 9 (held-out JSON names no case) and 10 (Markdown never does)
    ids = [f"zz{t}" for t in tokens]
    r1 = build_r1(_arms(row, arm, ids, split=split), "X", "F")
    dumped = json.dumps(r1, allow_nan=False)
    md = render_markdown(r1)
    for cid in ids:
        assert cid not in md
        if split != "dev":
            assert cid not in dumped
    assert r1["heldout"] is (split != "dev")
