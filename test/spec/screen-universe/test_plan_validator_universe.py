"""Spec tests for screen-universe C7: `PlanValidator` checks a screen step's `universe`
(docs/specs/screen-universe.md, Requirement 1). Pure; the valid group ids are those of the
committed `configs/eval/v2/groups.yaml`.
"""

from __future__ import annotations

import string
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_validator import PlanValidator

GROUPS_FILE = Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2" / "groups.yaml"
GROUP_IDS = sorted(yaml.safe_load(GROUPS_FILE.read_text(encoding="utf-8"))["groups"])
VALID = {"covered", *GROUP_IDS}
ABSENT = "<absent>"


def _plan(universe):
    step = {"id": "s1", "op": "screen", "fy": 2023, "dimension": "NONE", "metric_kind": "value",
            "order": "desc", "concept_family": "NetIncome", "top_n": 3}
    if universe != ABSENT:
        step["universe"] = universe
    return {"steps": [step]}


def _universe_errors(universe):
    return [e for e in PlanValidator().validate(_plan(universe)) if "universe" in e]


@pytest.mark.criterion("C7")
def test_universe_values_valid_and_invalid():
    # oracle: Requirement 1 — absent, null, "covered" and the committed group ids are valid;
    # anything else, a non-string included, is one error naming the step, `universe` and
    # the value
    assert GROUP_IDS == ["big_tech", "retailers", "semiconductors"]
    for ok in (ABSENT, None, "covered", "semiconductors", "retailers", "big_tech"):
        assert _universe_errors(ok) == [], ok
    for bad, shown in (("megacaps", "megacaps"), ("Covered", "Covered"), ("", ""),
                       (7, "7"), (["semiconductors"], "semiconductors"), (True, "True")):
        errors = _universe_errors(bad)
        assert len(errors) == 1, (bad, errors)
        assert shown in errors[0] and "s1" in errors[0] and "0" in errors[0]


@pytest.mark.criterion("C7")
@given(st.text(alphabet=string.ascii_letters + string.digits + "_- ", max_size=24))
def test_any_unknown_universe_string_is_rejected(value):
    # oracle: Requirement 1 written out — every string outside the valid set is one error
    # that contains `universe` and the value; the valid set gives none
    errors = _universe_errors(value)
    if value in VALID:
        assert errors == []
        errors = _universe_errors(value + "_x")
        value += "_x"
    assert len(errors) == 1
    assert value in errors[0]
