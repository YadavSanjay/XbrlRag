"""C1: the report groups of a case come from its shape tag, else its H-X kind tag."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import case_groups

CF = {"cross_filer", "line1", "all"}
CP = {"cross_period", "line1", "all"}
CO = {"compound", "line1", "all"}
SFN_LC = {"single_filer_numeric", "lookup_compute", "all"}
SFN = {"single_filer_numeric", "all"}

KNOWN_SHAPES = {
    "screen.top": CF, "screen.bottom": CF, "screen.sum": CF, "screen.median": CF,
    "peer_relative": CF,
    "trend": CP, "point_in_time": CP, "compute.cagr": CP | {"lookup_compute"},
    "compound.rank_by_ratio": CO, "compound.leader_growth": CO,
    "lookup": SFN_LC, "compute.yoy_growth": SFN_LC, "compute.difference": SFN_LC,
    "compute.ratio": SFN_LC, "dimensional": SFN, "breakdown": SFN,
}
LINE1_KINDS = {"cross_filer", "cross_period", "compound"}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("tags", "expected"), [
    (["split:dev", "shape:screen.top"], CF),
    (["shape:screen.sum"], CF),
    (["shape:peer_relative"], CF),
    (["shape:trend"], CP),
    (["shape:point_in_time"], CP),
    (["shape:compute.cagr"], CP | {"lookup_compute"}),
    (["shape:compute.yoy_growth"], SFN_LC),
    (["shape:compute.ratio"], SFN_LC),
    (["shape:lookup"], SFN_LC),
    (["shape:dimensional"], SFN),
    (["shape:breakdown"], SFN),
    (["shape:compound.rank_by_ratio", "compound"], CO),
    (["split:external", "cross_filer", "screen_top"], CF),
    (["split:external", "cross_period", "cagr"], CP),
    (["split:external", "compound"], CO),
    (["split:external", "honesty", "decline"], {"all"}),
    (["shape:narrative.summary"], {"all"}),
    ([], {"all"}),
])
def test_groups_follow_the_shape_table(tags, expected):
    # oracle: requirement 1 table, the pre-registration §3 "Kinds, by suite shape"
    assert case_groups(tags) == frozenset(expected)
    assert isinstance(case_groups(tags), frozenset)


@pytest.mark.criterion("C1")
def test_shape_tag_overrides_hx_tag():
    # oracle: requirement 1, "the first shape: tag decides; other tags are then ignored"
    assert case_groups(["cross_filer", "shape:lookup"]) == frozenset(SFN_LC)
    assert case_groups(["shape:trend", "compound"]) == frozenset(CP)


_UNRELATED = st.sampled_from(["split:dev", "intent:i-1", "family:terse", "kind:x",
                              "key_status:verified", "template:t1", "set-x", "shapes:lookup"])


@pytest.mark.criterion("C1")
@given(shape=st.sampled_from(sorted(KNOWN_SHAPES)), extra=st.lists(_UNRELATED, max_size=5),
       data=st.data())
def test_groups_invariants_and_tag_order(shape, extra, data):
    # oracle: requirement 1 (line1 is the union of the three kinds; the kinds partition;
    # `all` always); the table above, built from the spec, is the expected value
    tags = [f"shape:{shape}", *extra]
    shuffled = data.draw(st.permutations(tags))
    groups = case_groups(shuffled)
    assert groups == frozenset(KNOWN_SHAPES[shape])
    assert "all" in groups
    assert ("line1" in groups) == bool(groups & LINE1_KINDS)
    exclusive = [g for g in ("cross_filer", "cross_period", "compound", "single_filer_numeric")
                 if g in groups]
    assert len(exclusive) == 1


@pytest.mark.criterion("C1")
@given(prefix=st.sampled_from(["screen.", "compound."]),
       suffix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12))
def test_new_screen_and_compound_shapes_keep_their_kind(prefix, suffix):
    # oracle: requirement 1, `screen.<anything>` and `compound.<anything>` rows
    expected = CF if prefix == "screen." else CO
    assert case_groups([f"shape:{prefix}{suffix}"]) == frozenset(expected)
