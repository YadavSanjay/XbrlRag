"""C2: a run has exactly one split (requirement 2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.run_columns import MixedSplitError, run_split

SPLITS = ["dev", "validation", "heldout_filers", "external", "temporal", "legacy"]


def _cases(make_case, splits):
    """One case per entry; `None` means no split tag."""
    return [make_case(f"c{i}", [f"split:{s}"] if s else ["kind:lookup"])
            for i, s in enumerate(splits)]


@pytest.mark.criterion("C2")
def test_single_split_is_returned(make_case):
    # oracle: requirement 2 — every case dev, so the run is dev
    assert run_split(_cases(make_case, ["dev", "dev", "dev"])) == "dev"
    assert run_split(_cases(make_case, ["heldout_filers"])) == "heldout_filers"


@pytest.mark.criterion("C2")
def test_untagged_and_empty_runs_are_legacy(make_case):
    # oracle: requirement 2 — an untagged case's effective split is legacy; [] is legacy;
    # an explicit legacy tag and an untagged case share the same effective split
    assert run_split(_cases(make_case, [None, None])) == "legacy"
    assert run_split([]) == "legacy"
    assert run_split(_cases(make_case, ["legacy", None])) == "legacy"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("splits,named", [
    (["dev", "validation"], {"dev", "validation"}),
    (["dev", None], {"dev", "legacy"}),
    (["heldout_filers", "dev", "dev"], {"heldout_filers", "dev"}),
])
def test_mixed_splits_raise_naming_them(make_case, splits, named):
    # oracle: requirement 2 — two effective splits raise MixedSplitError, a ValueError,
    # whose message names them
    with pytest.raises(MixedSplitError) as info:
        run_split(_cases(make_case, splits))
    assert isinstance(info.value, ValueError)
    for name in named:
        assert name in str(info.value)


@pytest.mark.criterion("C2")
def test_a_malformed_tag_raises(make_case):
    # oracle: requirement 2 — every case's tags are parsed (requirement 1)
    cases = [make_case("a", ["split:dev"]), make_case("b", ["split:dev", "key_status:maybe"])]
    with pytest.raises(ValueError):
        run_split(cases)


@pytest.mark.criterion("C2")
@given(split=st.one_of(st.none(), st.sampled_from(SPLITS)), n=st.integers(1, 8),
       other=st.sampled_from(SPLITS), at=st.integers(0, 8))
def test_one_split_or_mixed(make_case, split, n, other, at):
    # oracle: requirement 2 — n cases of one effective split give it; inserting one
    # case of a different effective split anywhere raises MixedSplitError
    effective = split or "legacy"
    splits = [split] * n
    assert run_split(_cases(make_case, splits)) == effective
    if other != effective:
        mixed = splits[:at] + [other] + splits[at:]
        with pytest.raises(MixedSplitError):
            run_split(_cases(make_case, mixed))
