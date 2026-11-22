"""Spec tests for blog-stats: Wilson intervals, the 2-of-3 rule and the exact McNemar test
pre-registered for the blog benchmark (docs/specs/blog-stats.md). Pure: synthetic case ids,
no DB.
"""

from __future__ import annotations

from math import comb

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.core.stats import majority_outcomes, mcnemar_exact, wilson_interval

# Newcombe's table prints 4 decimals; half its last digit.
PUBLISHED_TOL = 5e-5
# Closed forms of small integers; 1e-12 only absorbs float rounding.
TOL = 1e-12


def _paired(both: int, a_only: int, b_only: int, neither: int) -> tuple[dict, dict]:
    """Two outcome mappings over shared ids with the given cell counts."""
    a: dict[str, bool] = {}
    b: dict[str, bool] = {}
    cells = [("both", both, True, True), ("a", a_only, True, False),
             ("b", b_only, False, True), ("none", neither, False, False)]
    for name, count, pa, pb in cells:
        for i in range(count):
            a[f"{name}{i}"] = pa
            b[f"{name}{i}"] = pb
    return a, b


def _exact_p(a_only: int, b_only: int) -> float:
    """Two-sided exact McNemar p-value from the binomial definition (Requirement 3)."""
    m = a_only + b_only
    if m == 0:
        return 1.0
    tail = sum(comb(m, i) for i in range(min(a_only, b_only) + 1)) / 2**m
    return min(1.0, 2 * tail)


@st.composite
def k_and_n(draw, max_n=500):
    n = draw(st.integers(min_value=1, max_value=max_n))
    return draw(st.integers(min_value=0, max_value=n)), n


cells = st.tuples(*(st.integers(min_value=0, max_value=30) for _ in range(4)))


# --- C1: Wilson against published values --------------------------------------------


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("k", "n", "lower", "upper"),
    [(81, 263, 0.2553, 0.3662), (15, 148, 0.0624, 0.1605),
     (0, 20, 0.0000, 0.1611), (1, 29, 0.0061, 0.1718)],
)
def test_wilson_matches_newcombe(k, n, lower, upper):
    # oracle: Newcombe (1998) Stat Med 17:857, Table II method 3 (score, no CC)
    lo, hi = wilson_interval(k, n)
    # tolerance: the table is rounded to 4 decimals
    assert lo == pytest.approx(lower, abs=PUBLISHED_TOL)
    assert hi == pytest.approx(upper, abs=PUBLISHED_TOL)


@pytest.mark.criterion("C1")
@given(k_and_n())
def test_wilson_matches_closed_form(kn):
    # oracle: the score-interval closed form of Requirement 1, computed here
    k, n = kn
    z = 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    lo, hi = wilson_interval(k, n)
    # tolerance: float rounding only
    assert lo == pytest.approx(max(0.0, centre - half), abs=TOL)
    assert hi == pytest.approx(min(1.0, centre + half), abs=TOL)


# --- C2: Wilson invariants ----------------------------------------------------------


@pytest.mark.criterion("C2")
def test_wilson_point_bounds_and_symmetry():
    # oracle: the definition; 8/10 against 2/10 by symmetry, hand-checked bounds
    lo, hi = wilson_interval(8, 10)
    lo2, hi2 = wilson_interval(2, 10)
    assert 0 < lo < 0.8 < hi < 1
    # tolerance: float rounding only
    assert (lo2, hi2) == pytest.approx((1 - hi, 1 - lo), abs=TOL)
    # tolerance: float rounding only
    assert wilson_interval(0, 10)[0] == pytest.approx(0.0, abs=TOL)
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0, abs=TOL)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(k_and_n())
def test_wilson_invariants(kn):
    # oracle: the definition (metamorphic)
    k, n = kn
    lo, hi = wilson_interval(k, n)
    assert 0.0 <= lo <= k / n + TOL
    assert k / n - TOL <= hi <= 1.0
    lo2, hi2 = wilson_interval(n - k, n)
    # tolerance: float rounding only
    assert (lo2, hi2) == pytest.approx((1 - hi, 1 - lo), abs=TOL)
    # tolerance: float rounding only; away from the edges the bounds are strictly inside
    if k == 0:
        assert lo == pytest.approx(0.0, abs=TOL)
    else:
        assert lo > TOL
    if k == n:
        assert hi == pytest.approx(1.0, abs=TOL)
    else:
        assert hi < 1 - TOL


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(k_and_n(max_n=200), st.integers(min_value=2, max_value=10))
def test_wilson_narrows_with_more_trials(kn, c):
    # oracle: the definition (metamorphic): same proportion, more trials, narrower
    k, n = kn
    assume(0 < k < n)
    lo, hi = wilson_interval(k, n)
    lo2, hi2 = wilson_interval(c * k, c * n)
    assert hi2 - lo2 < hi - lo


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("k", "n"), [(0, 0), (1, 0), (-1, 5), (6, 5)])
def test_wilson_rejects_invalid(k, n):
    # oracle: Requirement 1
    with pytest.raises(ValueError):
        wilson_interval(k, n)


# --- C3: the more-than-half rule ----------------------------------------------------


@pytest.mark.criterion("C3")
def test_majority_point_case():
    # oracle: v1 §5 (2 of 3), worked by hand
    runs = [
        {"a": True, "b": True, "c": True, "d": False},
        {"a": True, "b": True, "c": False, "d": False},
        {"a": True, "b": False, "c": False, "d": False},
    ]
    assert majority_outcomes(runs) == {"a": True, "b": True, "c": False, "d": False}


@pytest.mark.criterion("C3")
def test_majority_single_run_is_unchanged():
    # oracle: Requirement 2 (1 of 1)
    run = {"a": True, "b": False}
    assert majority_outcomes([run]) == run


@st.composite
def odd_runs(draw):
    ids = draw(st.lists(st.text("abcdef", min_size=1, max_size=3),
                        min_size=1, max_size=8, unique=True))
    n_runs = draw(st.sampled_from([1, 3, 5]))
    return [{c: draw(st.booleans()) for c in ids} for _ in range(n_runs)]


@pytest.mark.criterion("C3")
@given(odd_runs())
def test_majority_matches_pass_count(runs):
    # oracle: Requirement 2, counted here: passed in more than half the runs
    expected = {c: 2 * sum(run[c] for run in runs) > len(runs) for c in runs[0]}
    assert majority_outcomes(runs) == expected


# --- C4: majority invariants --------------------------------------------------------


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(odd_runs(), st.randoms(use_true_random=False))
def test_majority_order_and_negation(runs, rnd):
    # oracle: the definition (metamorphic)
    result = majority_outcomes(runs)
    assert set(result) == set(runs[0])
    shuffled = list(runs)
    rnd.shuffle(shuffled)
    assert majority_outcomes(shuffled) == result
    negated = [{c: not v for c, v in run.items()} for run in runs]
    assert majority_outcomes(negated) == {c: not v for c, v in result.items()}


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    "runs",
    [[], [{"a": True}, {"a": False}],
     [{"a": True}, {"a": True}, {"a": True, "b": True}],
     [{"a": True}, {"b": True}, {"a": True}]],
)
def test_majority_rejects_bad_runs(runs):
    # oracle: Requirement 2 (odd count, same case ids)
    with pytest.raises(ValueError):
        majority_outcomes(runs)


# --- C5: McNemar counts and p-values ------------------------------------------------


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("both", "a_only", "b_only", "neither", "p"),
    [(3, 1, 6, 2, 16 / 128), (0, 0, 5, 0, 2 / 32), (4, 3, 3, 1, 1.0), (5, 0, 0, 5, 1.0)],
)
def test_mcnemar_point_cases(both, a_only, b_only, neither, p):
    # oracle: exact binomial sums by hand in docs/specs/blog-stats.md C5
    a, b = _paired(both, a_only, b_only, neither)
    r = mcnemar_exact(a, b)
    assert (r["n"], r["both"], r["a_only"], r["b_only"], r["neither"]) == (
        both + a_only + b_only + neither, both, a_only, b_only, neither)
    # tolerance: float rounding only
    assert r["p_value"] == pytest.approx(p, abs=TOL)


@pytest.mark.criterion("C5")
def test_mcnemar_counts_only_shared_cases():
    # oracle: Requirement 3 (482 against 60 shared), by construction
    a = {f"c{i}": i % 2 == 0 for i in range(482)}
    b = {f"c{i}": True for i in range(60)}
    r = mcnemar_exact(a, b)
    assert r["n"] == 60
    assert (r["both"], r["b_only"], r["a_only"], r["neither"]) == (30, 30, 0, 0)


@pytest.mark.criterion("C5")
@given(cells)
def test_mcnemar_matches_binomial_definition(c):
    # oracle: the exact binomial definition, computed here
    a, b = _paired(*c)
    assume(a)
    # tolerance: float rounding only
    assert mcnemar_exact(a, b)["p_value"] == pytest.approx(_exact_p(c[1], c[2]), abs=TOL)


# --- C6: McNemar invariants ---------------------------------------------------------


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(cells, st.integers(min_value=0, max_value=20), st.integers(min_value=0, max_value=20))
def test_mcnemar_invariants(c, extra_both, extra_neither):
    # oracle: the definition (metamorphic)
    a, b = _paired(*c)
    assume(a)
    r = mcnemar_exact(a, b)
    assert r["both"] + r["a_only"] + r["b_only"] + r["neither"] == r["n"]
    assert 0 < r["p_value"] <= 1
    swapped = mcnemar_exact(b, a)
    assert (swapped["a_only"], swapped["b_only"]) == (r["b_only"], r["a_only"])
    # tolerance: float rounding only
    assert swapped["p_value"] == pytest.approx(r["p_value"], abs=TOL)
    a2, b2 = _paired(c[0] + extra_both, c[1], c[2], c[3] + extra_neither)
    assert mcnemar_exact(a2, b2)["p_value"] == pytest.approx(r["p_value"], abs=TOL)
    if r["a_only"] == r["b_only"]:
        assert r["p_value"] == pytest.approx(1.0, abs=TOL)


@pytest.mark.criterion("C6")
def test_mcnemar_rejects_no_shared_case():
    # oracle: Requirement 3
    with pytest.raises(ValueError):
        mcnemar_exact({"a": True}, {"b": True})
