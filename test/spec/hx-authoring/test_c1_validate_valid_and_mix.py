"""C1: ``validate_hx`` accepts valid cases, reports the mix and the per-kind warnings.

Oracle: requirements 2-8 of docs/specs/hx-authoring.md. Every case is built by hand in
the conftest; the expected counts are counted by hand from those builders. The per-kind
targets moved from 15 each to pre-registration Amendment 1.1's mix; they are pinned in
test/spec/hx-kind-targets (docs/specs/hx-kind-targets.md). The tests here hold under both.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import validate_hx


def _one_of_each(kit):
    cases = [kit.make(k, s, f"c{i:02d}") for i, (k, s) in enumerate(kit.SHAPES)]
    cases.append(kit.honesty("c99", "decline", reason="out_of_corpus", tickers=["OUTX"]))
    return cases


@pytest.mark.criterion("C1")
def test_one_valid_case_of_every_shape_has_no_errors_and_the_hand_counted_mix(kit):
    # oracle: requirement 8 — counted by hand from conftest.SHAPES plus one decline /
    # out_of_corpus case naming OUTX (outside both filer sets, allowed there).
    got = validate_hx(_one_of_each(kit), kit.DEV, kit.HELD)
    assert got["errors"] == []
    assert got["mix"]["kind"] == {"cross_filer": 6, "cross_period": 3, "compound": 1,
                                  "honesty": 4, "text": 1}
    assert got["mix"]["subtype"] == {
        "cross_filer.screen_top": 1, "cross_filer.screen_bottom": 1, "cross_filer.count": 1,
        "cross_filer.sum": 1, "cross_filer.median": 1, "cross_filer.peer_relative": 1,
        "cross_period.trend": 1, "cross_period.cagr": 1, "cross_period.point_in_time": 1,
        "honesty.ambiguous": 1, "honesty.false_premise": 1, "honesty.decline": 2,
    }
    # DEVA..DEVD (universes), DEVE (cross_period), OUTX (the decline) -> 6 distinct.
    assert got["mix"]["filers"] == 6
    assert got["mix"]["total"] == 15
    # Every kind is off its target (15 each, or Amendment 1.1's 24/15/18/9/9) -> one
    # warning per kind.
    assert len(got["warnings"]) == 5


@pytest.mark.criterion("C1")
def test_fifteen_of_each_kind_never_warns_for_cross_period(kit):
    # oracle: requirement 8 — a warning only for a kind off its target; cross_period's target
    # is 15 before and after Amendment 1.1, and every warning names the count, 15.
    cases = [kit.make(k, None, f"{k}-{i}") for k in kit.KINDS for i in range(15)]
    got = validate_hx(cases, kit.DEV, kit.HELD)
    assert got["errors"] == []
    for warning in got["warnings"]:
        assert "cross_period" not in warning
        assert "15" in warning
    assert got["mix"]["kind"] == {k: 15 for k in kit.KINDS}
    assert got["mix"]["total"] == 75


@pytest.mark.criterion("C1")
def test_fourteen_of_one_kind_gives_exactly_one_warning_naming_kind_and_count(kit):
    # oracle: requirement 8 — 14 text cases is off text's target (15, or 9 since Amendment
    # 1.1): exactly one warning names text, with its count, and no warning carries anything
    # case-specific (no id, ticker or question).
    cases = [kit.make(k, None, f"{k}-{i}") for k in kit.KINDS
             for i in range(14 if k == "text" else 15)]
    got = validate_hx(cases, kit.DEV, kit.HELD)
    assert got["errors"] == []
    texts = [w for w in got["warnings"] if "text" in w]
    assert len(texts) == 1
    assert "14" in texts[0]
    for warning in got["warnings"]:
        for leak in ("text-0", "DEVA", "Synthetic"):
            assert leak not in warning


@pytest.mark.criterion("C1")
def test_a_kind_with_no_cases_counts_zero_and_warns(kit):
    # oracle: requirement 8 — mix.kind lists all five kinds, 0 included.
    cases = [kit.make("text", None, "t1")]
    got = validate_hx(cases, kit.DEV, kit.HELD)
    assert got["mix"]["kind"] == {"cross_filer": 0, "cross_period": 0, "compound": 0,
                                  "honesty": 0, "text": 1}
    assert len(got["warnings"]) == 5


@pytest.mark.criterion("C1")
def test_overlapping_filer_sets_raise(kit):
    # oracle: requirement 8 — dev and held-out sets that intersect are a data error.
    with pytest.raises(ValueError):
        validate_hx([kit.make("text", None, "t1")], kit.DEV, frozenset({"DEVA", "HOLDA"}))


@pytest.mark.criterion("C1")
@given(picks=st.lists(st.integers(min_value=0, max_value=13), min_size=1, max_size=30))
def test_random_valid_sets_have_no_errors_and_consistent_mix(kit, picks):
    # oracle: requirement 8 — valid cases give no errors; total = len(cases) and the kind
    # counts sum to it, each equal to the count of picks of that kind.
    shapes = [kit.SHAPES[p] for p in picks]
    cases = [kit.make(k, s, f"r{i}") for i, (k, s) in enumerate(shapes)]
    got = validate_hx(cases, kit.DEV, kit.HELD)
    assert got["errors"] == []
    assert got["mix"]["total"] == len(cases)
    assert sum(got["mix"]["kind"].values()) == len(cases)
    for kind in kit.KINDS:
        assert got["mix"]["kind"][kind] == sum(1 for k, _ in shapes if k == kind)
