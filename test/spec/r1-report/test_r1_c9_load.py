"""C9: an arm is loaded from a stored run through a SELECT-only reader (faked here)."""

from __future__ import annotations

import json

import pytest

from src.eval.core.r1_report import R1Refused, build_r1, load_arm

FIELDS = ("case_id", "tags", "value_correct", "confident_wrong", "grounding_precision",
          "cost_usd", "latency_ms", "error")


def _db_case(row, case_id, shape="lookup", **kw):
    """A stored case row: the requirement-2 fields plus columns the report ignores."""
    r = row(case_id, shape, **kw)
    r.update({"kind": "lookup", "grade": "CORRECT", "question": "q?", "ticker": "AAA"})
    return r


@pytest.mark.criterion("C9")
def test_load_maps_the_run_and_cases(row, fake_reader):
    # oracle: requirement 11, field by field from the stored rows
    run = {"run_id": "eval-111", "suite": "xbrl_v2_dev", "system": "xbrlrag_full",
           "split": "dev", "config": json.dumps({"split": "dev", "service_tier": "flex"})}
    cases = [_db_case(row, "a1", gp=1.0, cost=0.25, latency=12.0),
             _db_case(row, "a2", "screen.top", vc=False, cw=True)]
    cases[1]["tags"] = None
    got = load_arm("X", "eval-111", fake_reader({"eval-111": run}, {"eval-111": cases}))
    assert got["label"] == "X"
    assert got["run_id"] == "eval-111"
    assert got["system"] == "xbrlrag_full"
    assert got["suite"] == "xbrl_v2_dev"
    assert got["split"] == "dev"
    assert got["config"] == {"split": "dev", "service_tier": "flex"}
    assert len(got["cases"]) == 2
    first = got["cases"][0]
    assert {k: first[k] for k in FIELDS} == {k: cases[0][k] for k in FIELDS}
    assert got["cases"][1]["tags"] == []
    assert got["cases"][1]["confident_wrong"] is True


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(("row_split", "config", "expected"), [
    ("validation", {"split": "validation"}, "validation"),
    (None, {"split": "heldout_filers"}, "heldout_filers"),
    (None, None, "legacy"),
    ("", {}, "legacy"),
])
def test_split_fallback(row, fake_reader, row_split, config, expected):
    # oracle: requirement 11, the row's split, else the config's, else legacy
    run = {"run_id": "r", "suite": "s", "system": "x", "split": row_split, "config": config}
    got = load_arm("X", "r", fake_reader({"r": run}, {"r": [_db_case(row, "a1")]}))
    assert got["split"] == expected
    assert got["config"] == (config or {})


@pytest.mark.criterion("C9")
def test_unknown_or_empty_run_refuses(row, fake_reader):
    # oracle: requirement 11, an unknown run id or a run without case rows
    reader = fake_reader({"r": {"run_id": "r", "suite": "s", "system": "x", "split": "dev",
                                "config": {}}}, {"r": []})
    with pytest.raises(R1Refused):
        load_arm("X", "nope", reader)
    with pytest.raises(R1Refused):
        load_arm("X", "r", reader)


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_loaded_arms_report_like_hand_built_arms(scenario, fake_reader):
    # oracle: requirement 2 and 11; the expected value is build_r1 over the hand-built arms
    arms = scenario()
    runs, cases = {}, {}
    for a in arms:
        runs[a["run_id"]] = {"run_id": a["run_id"], "suite": a["suite"], "system": a["system"],
                             "split": a["split"], "config": json.dumps(a["config"])}
        cases[a["run_id"]] = [dict(c, kind="x", grade="CORRECT") for c in a["cases"]]
    reader = fake_reader(runs, cases)
    loaded = [load_arm(a["label"], a["run_id"], reader) for a in arms]
    expected = build_r1(arms, "X", "F", oracle="O")
    got = build_r1(loaded, "X", "F", oracle="O")
    assert got["groups"] == expected["groups"]
    assert got["comparisons"] == expected["comparisons"]
    assert got["bar"] == expected["bar"]
    assert got["case_level"] == expected["case_level"]
