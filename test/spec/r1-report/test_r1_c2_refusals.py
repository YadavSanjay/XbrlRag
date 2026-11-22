"""C2: arms that are not one pre-registered comparison are refused before anything is computed."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import R1Refused, build_r1

SPLITS = ["dev", "validation", "heldout_filers", "external", "temporal", "legacy"]


def _pair(row, arm, **kw_f):
    x = arm("X", [row("a1"), row("a2", "screen.top")])
    f = arm("F", [row("a1"), row("a2", "screen.top")], **kw_f)
    return x, f


@pytest.mark.criterion("C2")
def test_refusal_is_a_value_error(row, arm):
    # oracle: requirement 3, R1Refused is a ValueError; differing suites refuse
    x, f = _pair(row, arm, suite="xbrl_v2_validation")
    with pytest.raises(ValueError):
        build_r1([x, f], "X", "F")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")


@pytest.mark.criterion("C2")
def test_label_errors_refuse(row, arm):
    # oracle: requirement 3, first bullet
    x, f = _pair(row, arm)
    dup = arm("X", [row("a1"), row("a2", "screen.top")])
    with pytest.raises(R1Refused):
        build_r1([x, dup], "X", "X")
    with pytest.raises(R1Refused):
        build_r1([x, f], "Z", "F")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "Z")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "X")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F", oracle="Z")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F", oracle="X")


@pytest.mark.criterion("C2")
def test_split_disagreements_refuse(row, arm):
    # oracle: requirement 3, an arm's split is one value and the arms' splits agree
    x, f = _pair(row, arm, split="validation")
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")
    x, f = _pair(row, arm, config={"split": "validation"})
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")
    x = arm("X", [row("a1"), row("a2", "screen.top")])
    f = arm("F", [row("a1"), row("a2", "screen.top", split="validation")])
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")


@pytest.mark.criterion("C2")
def test_duplicate_case_and_mismatched_kinds_refuse(row, arm):
    # oracle: requirement 3, a repeated case id; two arms classifying one case differently
    x = arm("X", [row("a1"), row("a1")])
    f = arm("F", [row("a1")])
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")
    x = arm("X", [row("a1", "lookup")])
    f = arm("F", [row("a1", "screen.top")])
    with pytest.raises(R1Refused):
        build_r1([x, f], "X", "F")


@pytest.mark.criterion("C2")
def test_heldout_refusal_names_no_case(row, arm):
    # oracle: requirement 3, "the refusal message names no case id"
    secret = "hf-zq7-secret-case"
    x = arm("X", [row(secret, split="heldout_filers"), row(secret, split="heldout_filers")],
            split="heldout_filers")
    f = arm("F", [row(secret, split="heldout_filers")], split="heldout_filers")
    with pytest.raises(R1Refused) as exc:
        build_r1([x, f], "X", "F")
    assert secret not in str(exc.value)
    x = arm("X", [row(secret, "lookup", split="heldout_filers")], split="heldout_filers")
    f = arm("F", [row(secret, "screen.top", split="heldout_filers")], split="heldout_filers")
    with pytest.raises(R1Refused) as exc:
        build_r1([x, f], "X", "F")
    assert secret not in str(exc.value)


@pytest.mark.criterion("C2")
@given(a=st.sampled_from(SPLITS), b=st.sampled_from(SPLITS))
def test_split_equality_decides_refusal(row, arm, a, b):
    # oracle: requirement 3; equal splits and suites with consistent rows are one comparison
    x = arm("X", [row("a1", split=a), row("a2", "screen.top", split=a)], split=a)
    f = arm("F", [row("a1", split=b), row("a2", "screen.top", split=b)], split=b)
    if a != b:
        with pytest.raises(R1Refused):
            build_r1([x, f], "X", "F")
    else:
        r1 = build_r1([x, f], "X", "F")
        assert r1["split"] == a
        assert r1["suite"] == "xbrl_v2_dev"
