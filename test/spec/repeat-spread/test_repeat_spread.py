"""Spec tests for repeat-spread: mean ± spread, floor/ceiling and flaky cases over
repeated eval runs (docs/specs/repeat-spread.md). Pure: synthetic case ids, no DB.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.repeats import summarize_repeats

# Floats here are ratios of small integers; 1e-12 only absorbs summation order.
TOL = 1e-12

# C1 point case, worked by hand in docs/specs/repeat-spread.md.
RUN_1 = {"a": True, "b": True, "c": True, "d": False, "e": False}
RUN_2 = {"a": True, "b": True, "c": False, "d": True, "e": False}
RUN_3 = {"a": True, "b": True, "c": True, "d": True, "e": False, "x": True}
RUNS = [RUN_1, RUN_2, RUN_3]
KINDS = {"a": "lookup", "c": "lookup", "b": "dimensional", "d": "dimensional"}


@st.composite
def repeated_runs(draw, min_runs=2):
    """Runs sharing a set of common cases, each with extra cases of its own.

    Extra ids are prefixed with the run index, so none of them is common to every run.
    """
    common = draw(st.lists(st.text("abcdefgh", min_size=1, max_size=3),
                           min_size=1, max_size=8, unique=True))
    n_runs = draw(st.integers(min_value=min_runs, max_value=5))
    runs = []
    for i in range(n_runs):
        run = {cid: draw(st.booleans()) for cid in common}
        for j in range(draw(st.integers(min_value=0, max_value=2))):
            run[f"x{i}_{j}"] = draw(st.booleans())
        runs.append(run)
    return common, runs


def _ns(summary):
    """The summary dict as attributes, recursively through by_kind."""
    fields = dict(summary)
    fields["by_kind"] = {k: _ns(v) for k, v in summary["by_kind"].items()}
    return SimpleNamespace(**fields)


def _summary(runs, kinds=None):
    return _ns(summarize_repeats(runs, kinds))


def _assert_same_summary(a, b):
    """Field-by-field equality; floats within TOL (summation order only)."""
    assert (a.n_runs, a.n_cases, a.flaky_case_ids) == (b.n_runs, b.n_cases, b.flaky_case_ids)
    assert a.accuracies == pytest.approx(b.accuracies, abs=TOL)
    for field in ("mean", "std", "min", "max", "floor", "ceiling", "flip_rate"):
        assert getattr(a, field) == pytest.approx(getattr(b, field), abs=TOL)
    assert set(a.by_kind) == set(b.by_kind)
    for kind in a.by_kind:
        _assert_same_summary(a.by_kind[kind], b.by_kind[kind])


def _accuracies_oracle(common, runs):
    """Each run's fraction passed over the common cases (Requirement 1)."""
    return [sum(run[c] for c in common) / len(common) for run in runs]


# --- C1: the fields, worked by hand ------------------------------------------------


@pytest.mark.criterion("C1")
def test_point_case_counts_and_accuracies():
    # oracle: hand calculation in docs/specs/repeat-spread.md C1 (x is not common)
    s = _summary(RUNS)
    assert s.n_runs == 3
    assert s.n_cases == 5
    assert s.accuracies == pytest.approx((0.6, 0.6, 0.8), abs=TOL)  # ratios of small ints


@pytest.mark.criterion("C1")
def test_point_case_mean_and_sample_std():
    # oracle: mean 2/3; sample std sqrt(((-1/15)^2 + (-1/15)^2 + (2/15)^2) / 2) = sqrt(1/75)
    s = _summary(RUNS)
    assert s.mean == pytest.approx(2 / 3, abs=TOL)  # ratios of small ints
    assert s.std == pytest.approx(math.sqrt(1 / 75), abs=TOL)  # closed form above
    assert s.min == pytest.approx(0.6, abs=TOL)  # ratios of small ints
    assert s.max == pytest.approx(0.8, abs=TOL)  # ratios of small ints


@pytest.mark.criterion("C1")
def test_point_case_floor_ceiling_and_flaky():
    # oracle: hand calculation — a, b pass in every run; c, d in some; e in none
    s = _summary(RUNS)
    assert s.floor == pytest.approx(2 / 5, abs=TOL)  # ratios of small ints
    assert s.ceiling == pytest.approx(4 / 5, abs=TOL)  # ratios of small ints
    assert s.flaky_case_ids == ("c", "d")
    assert s.flip_rate == pytest.approx(2 / 5, abs=TOL)  # ratios of small ints


@pytest.mark.criterion("C1")
@given(repeated_runs())
def test_fields_match_definitions(data):
    # oracle: Requirement 1's definitions, computed directly in the test
    common, runs = data
    s = _summary(runs)
    acc = _accuracies_oracle(common, runs)
    mean = sum(acc) / len(acc)
    std = math.sqrt(sum((a - mean) ** 2 for a in acc) / (len(acc) - 1))
    flaky = sorted(c for c in common if len({run[c] for run in runs}) > 1)
    assert s.n_runs == len(runs)
    assert s.n_cases == len(common)
    assert s.accuracies == pytest.approx(tuple(acc), abs=TOL)  # same ratios
    assert s.mean == pytest.approx(mean, abs=TOL)  # summation order only
    assert s.std == pytest.approx(std, abs=TOL)  # summation order only
    assert s.flaky_case_ids == tuple(flaky)
    assert s.flip_rate == pytest.approx(len(flaky) / len(common), abs=TOL)  # same ratio


# --- C2: invariants ------------------------------------------------------------------


@pytest.mark.criterion("C2")
def test_point_case_ordering_and_flip_rate():
    # oracle: hand calculation — floor 0.4 <= min 0.6 <= mean 2/3 <= max 0.8 <= ceiling 0.8
    s = _summary(RUNS)
    assert (s.floor, s.min, s.mean, s.max, s.ceiling) == pytest.approx(
        (0.4, 0.6, 2 / 3, 0.8, 0.8), abs=TOL)  # ratios of small ints
    assert s.flip_rate == pytest.approx(s.ceiling - s.floor, abs=TOL)  # 0.8 - 0.4


@pytest.mark.criterion("C2")
def test_point_case_identical_runs():
    # oracle: hand calculation — two copies of run 1: accuracy 0.6 twice, nothing flips
    s = _summary([RUN_1, dict(RUN_1)])
    assert s.n_runs == 2
    assert s.mean == pytest.approx(0.6, abs=TOL)  # 3/5
    assert s.std == pytest.approx(0.0, abs=TOL)  # zero deviation
    assert s.flaky_case_ids == ()


@pytest.mark.criterion("C2")
@given(repeated_runs())
def test_ordering_and_flip_rate_invariants(data):
    # oracle: definitions — floor <= every run <= ceiling; flaky = ceiling minus floor
    common, runs = data
    s = _summary(runs)
    assert s.n_cases == len(common)
    assert s.floor - TOL <= s.min <= s.mean + TOL
    assert s.mean <= s.max + TOL
    assert s.max <= s.ceiling + TOL
    assert s.flip_rate == pytest.approx(s.ceiling - s.floor, abs=TOL)  # summation order


@pytest.mark.criterion("C2")
@given(repeated_runs(), st.integers(min_value=2, max_value=5))
def test_identical_runs_have_no_spread(data, n):
    # oracle: definition — identical outcomes give zero deviation and no flaky case
    common, runs = data
    # Copy only the common cases: a copied extra id would become common to every run.
    same = [{c: runs[0][c] for c in common} for _ in range(n)]
    s = _summary(same)
    acc = _accuracies_oracle(common, same[:1])[0]
    assert s.n_runs == n
    assert s.mean == pytest.approx(acc, abs=TOL)  # same ratio
    assert s.std == pytest.approx(0.0, abs=TOL)  # zero deviation
    assert s.flaky_case_ids == ()
    assert s.floor == pytest.approx(acc, abs=TOL)  # every case is all-or-nothing
    assert s.ceiling == pytest.approx(acc, abs=TOL)  # every case is all-or-nothing


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(repeated_runs(), st.randoms(use_true_random=False))
def test_reordering_runs_changes_only_accuracy_order(data, rnd):
    # oracle: metamorphic — the summary is a function of the multiset of runs
    common, runs = data
    shuffled = list(runs)
    rnd.shuffle(shuffled)
    a, b = _summary(runs), _summary(shuffled)
    assert a.n_runs == len(runs)
    assert sorted(a.accuracies) == pytest.approx(sorted(b.accuracies), abs=TOL)  # same ratios
    assert b.accuracies == pytest.approx(tuple(_accuracies_oracle(common, shuffled)), abs=TOL)  # same ratios
    for field in ("mean", "std", "min", "max", "floor", "ceiling", "flip_rate"):
        assert getattr(a, field) == pytest.approx(getattr(b, field), abs=TOL)  # summation order
    assert a.flaky_case_ids == b.flaky_case_ids


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(repeated_runs())
def test_non_common_cases_change_nothing(data):
    # oracle: metamorphic — only cases present in every run are counted
    common, runs = data
    trimmed = [{c: run[c] for c in common} for run in runs]
    a, b = _summary(runs), _summary(trimmed)
    assert a.n_cases == len(common)
    _assert_same_summary(a, b)


# --- C3: by kind ---------------------------------------------------------------------


@pytest.mark.criterion("C3")
def test_point_case_by_kind():
    # oracle: hand calculation in docs/specs/repeat-spread.md C3 (e is unmapped)
    s = _summary(RUNS, KINDS)
    assert set(s.by_kind) == {"lookup", "dimensional"}
    lk, dm = s.by_kind["lookup"], s.by_kind["dimensional"]
    assert lk.accuracies == pytest.approx((1.0, 0.5, 1.0), abs=TOL)  # ratios of small ints
    assert lk.flaky_case_ids == ("c",)
    assert dm.accuracies == pytest.approx((0.5, 1.0, 1.0), abs=TOL)  # ratios of small ints
    assert dm.flaky_case_ids == ("d",)
    assert lk.by_kind == {} and dm.by_kind == {}
    assert s.n_cases == 5  # e still counts in the totals


@pytest.mark.criterion("C3")
def test_no_kinds_gives_empty_by_kind():
    # oracle: Requirement 1 — kinds=None gives {}
    s = _summary(RUNS)
    assert s.n_runs == 3
    assert s.by_kind == {}


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(repeated_runs(), st.data())
def test_by_kind_equals_restricted_summary(data, draw):
    # oracle: metamorphic — a kind's summary is the summary of the runs restricted to it
    common, runs = data
    kinds = {c: draw.draw(st.sampled_from(["k1", "k2"])) for c in common
             if draw.draw(st.booleans())}
    s = _summary(runs, kinds)
    assert s.n_cases == len(common)
    assert set(s.by_kind) == set(kinds.values())
    for kind, sub in s.by_kind.items():
        restricted = [{c: v for c, v in run.items() if kinds.get(c) == kind} for run in runs]
        _assert_same_summary(sub, _summary(restricted))


# --- C4: invalid input ---------------------------------------------------------------


@pytest.mark.criterion("C4")
def test_fewer_than_two_runs_raises():
    # oracle: Requirement 1 — at least 2 runs
    with pytest.raises(ValueError):
        _summary([RUN_1])
    with pytest.raises(ValueError):
        _summary([])


@pytest.mark.criterion("C4")
def test_no_common_case_raises():
    # oracle: Requirement 1 — no common case
    with pytest.raises(ValueError):
        _summary([{"a": True}, {"b": True}])


@pytest.mark.criterion("C4")
@given(repeated_runs(min_runs=2))
def test_disjoint_runs_raise(data):
    # oracle: Requirement 1 — renaming each run's ids apart leaves no common case
    _, runs = data
    disjoint = [{f"r{i}:{c}": v for c, v in run.items()} for i, run in enumerate(runs)]
    with pytest.raises(ValueError):
        _summary(disjoint)
