"""C1: ``validate_hx`` warns per kind against pre-registration Amendment 1.1's targets.

Oracle: the Amendment 1.1 table (docs/eval/heldout_v2/preregistration.md; restated in
docs/specs/hx-kind-targets.md): cross_filer 24, cross_period 15, compound 18, honesty 9,
text 9. The targets live in this spec's conftest, written from that table, never read from src.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import validate_hx


def _by_kind(warnings, kinds):
    """Each warning keyed by the one kind it names; fails if a warning names 0 or 2+ kinds."""
    out = {}
    for w in warnings:
        named = [k for k in kinds if k in w]
        assert len(named) == 1, "a warning names exactly one kind"
        assert named[0] not in out, "one warning per kind"
        out[named[0]] = w
    return out


def _ints(warning):
    return sorted(int(n) for n in re.findall(r"\d+", warning))


@pytest.mark.criterion("C1")
def test_the_amendment_mix_gives_no_warning(kit):
    # oracle: Amendment 1.1 table — 24 + 15 + 18 + 9 + 9 = 75, every kind at its target.
    got = validate_hx(kit.cases_for(kit.TARGETS), kit.DEV, kit.HELD)
    assert got["errors"] == []
    assert got["warnings"] == []
    assert got["mix"]["kind"] == {"cross_filer": 24, "cross_period": 15, "compound": 18,
                                  "honesty": 9, "text": 9}
    assert got["mix"]["total"] == 75


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("delta", [-1, 1])
@pytest.mark.parametrize("kind", ["cross_filer", "cross_period", "compound", "honesty", "text"])
def test_one_kind_off_its_target_gives_exactly_one_warning(kit, kind, delta):
    # oracle: requirement 2 with Amendment 1.1's table — only the changed kind is off target;
    # its warning's integers are its count (target + delta) and its target.
    counts = dict(kit.TARGETS)
    counts[kind] += delta
    got = validate_hx(kit.cases_for(counts), kit.DEV, kit.HELD)
    assert got["errors"] == []
    warned = _by_kind(got["warnings"], kit.KINDS)
    assert list(warned) == [kind]
    target = kit.TARGETS[kind]
    assert _ints(warned[kind]) == sorted([target + delta, target])


@pytest.mark.criterion("C1")
def test_the_old_fifteen_each_mix_warns_for_four_kinds(kit):
    # oracle: Amendment 1.1 "Was 15 / Now" column — 15 is off target for every kind but
    # cross_period (15 -> 15); hand-listed (count, target) per warned kind.
    got = validate_hx(kit.cases_for({k: 15 for k in kit.KINDS}), kit.DEV, kit.HELD)
    assert got["errors"] == []
    warned = _by_kind(got["warnings"], kit.KINDS)
    assert set(warned) == {"cross_filer", "compound", "honesty", "text"}
    assert _ints(warned["cross_filer"]) == [15, 24]
    assert _ints(warned["compound"]) == [15, 18]
    assert _ints(warned["honesty"]) == [9, 15]
    assert _ints(warned["text"]) == [9, 15]


@pytest.mark.criterion("C1")
def test_a_kind_with_no_cases_warns_with_count_zero_and_its_target(kit):
    # oracle: requirement 2 — one text case: text 1 vs 9, every other kind 0 vs its target.
    got = validate_hx([kit.make("text", "t1")], kit.DEV, kit.HELD)
    warned = _by_kind(got["warnings"], kit.KINDS)
    assert set(warned) == set(kit.KINDS)
    assert _ints(warned["cross_filer"]) == [0, 24]
    assert _ints(warned["cross_period"]) == [0, 15]
    assert _ints(warned["compound"]) == [0, 18]
    assert _ints(warned["honesty"]) == [0, 9]
    assert _ints(warned["text"]) == [1, 9]


@pytest.mark.criterion("C1")
def test_no_warning_carries_a_case_value(kit):
    # oracle: requirement 3 (hx-authoring requirement 8) — warnings carry kind and counts only;
    # the 15-each mix warns for four kinds, none of which may echo an id, ticker or question.
    got = validate_hx(kit.cases_for({k: 15 for k in kit.KINDS}), kit.DEV, kit.HELD)
    assert len(got["warnings"]) == 4
    for warning in got["warnings"]:
        for leak in ("text-0", "honesty-1", "DEVA", "DEVE", "Synthetic", "question", "?"):
            assert leak not in warning


@pytest.mark.criterion("C1")
@given(counts=st.fixed_dictionaries(
    {k: st.integers(min_value=0, max_value=30)
     for k in ["cross_filer", "cross_period", "compound", "honesty", "text"]}))
def test_random_counts_warn_exactly_for_kinds_off_target(kit, counts):
    # oracle: requirement 2 with Amendment 1.1's table — warned kinds are exactly those whose
    # count differs from its target; each warning's integers are that count and that target.
    got = validate_hx(kit.cases_for(counts), kit.DEV, kit.HELD)
    assert got["errors"] == []
    assert got["mix"]["total"] == sum(counts.values())
    warned = _by_kind(got["warnings"], kit.KINDS)
    assert set(warned) == {k for k in kit.KINDS if counts[k] != kit.TARGETS[k]}
    for kind, warning in warned.items():
        assert _ints(warning) == sorted([counts[kind], kit.TARGETS[kind]])
