"""C4: paired McNemar of the reference against each other arm, per kind and pooled."""

from __future__ import annotations

from math import comb

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import build_r1

# Closed forms of small integers; 1e-12 only absorbs float rounding.
TOL = 1e-12


def _exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p from requirement 7, with exact integers."""
    m = b + c
    if m == 0:
        return 1.0
    tail = sum(comb(m, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2**m)


def _cells(row, arm, shape, both, ref_only, other_only, neither, prefix="c"):
    x, f = [], []
    spec = [(both, True, True), (ref_only, True, False), (other_only, False, True),
            (neither, False, False)]
    i = 0
    for count, vx, vf in spec:
        for _ in range(count):
            x.append(row(f"{prefix}{i}", shape, vc=vx))
            f.append(row(f"{prefix}{i}", shape, vc=vf))
            i += 1
    return x, f


@pytest.mark.criterion("C4")
def test_eight_against_one_beats(row, arm):
    # oracle: closed form by hand, m = 9: p = 2 * (C(9,0) + C(9,1)) / 2^9 = 20/512
    x, f = _cells(row, arm, "screen.top", 5, 8, 1, 6)
    r1 = build_r1([arm("X", x), arm("F", f)], "X", "F")
    cmp = r1["comparisons"]["F"]["cross_filer"]
    assert (cmp["n"], cmp["both"], cmp["reference_only"], cmp["other_only"],
            cmp["neither"]) == (20, 5, 8, 1, 6)
    # tolerance: 20/512 is exact in binary; abs guards the float sum
    assert cmp["p_value"] == pytest.approx(0.0390625, abs=TOL)
    assert cmp["beats"] is True


@pytest.mark.criterion("C4")
def test_one_against_eight_does_not_beat(row, arm):
    # oracle: same closed form; "beats" also needs reference_only > other_only (§3)
    x, f = _cells(row, arm, "screen.top", 5, 1, 8, 6)
    cmp = build_r1([arm("X", x), arm("F", f)], "X", "F")["comparisons"]["F"]["cross_filer"]
    # tolerance: exact binary fraction
    assert cmp["p_value"] == pytest.approx(0.0390625, abs=TOL)
    assert cmp["beats"] is False


@pytest.mark.criterion("C4")
def test_no_discordant_pair_gives_p_one(row, arm):
    # oracle: requirement 7, p = 1.0 when m = 0
    x, f = _cells(row, arm, "trend", 4, 0, 0, 3)
    cmp = build_r1([arm("X", x), arm("F", f)], "X", "F")["comparisons"]["F"]["cross_period"]
    # tolerance: exact
    assert cmp["p_value"] == pytest.approx(1.0, abs=TOL)
    assert cmp["beats"] is False
    assert cmp["n"] == 7


@pytest.mark.criterion("C4")
def test_scenario_pairs_leave_out_excluded_and_missing(scenario):
    # oracle: hand pairing of the conftest scenario
    r1 = build_r1(scenario(), "X", "F", oracle="O")
    xf = r1["comparisons"]["F"]
    # cross_filer: s1 s3 p1 reference only, s2 both, s4 neither; m = 3 -> p = 2/8
    assert (xf["cross_filer"]["n"], xf["cross_filer"]["both"], xf["cross_filer"]["reference_only"],
            xf["cross_filer"]["other_only"], xf["cross_filer"]["neither"]) == (5, 1, 3, 0, 1)
    # tolerance: exact binary fraction
    assert xf["cross_filer"]["p_value"] == pytest.approx(0.25, abs=TOL)
    # compound: k1 missing from F
    assert (xf["compound"]["n"], xf["compound"]["n_missing"], xf["compound"]["n_excluded"]) == \
        (0, 1, 0)
    # line1: 7 pairs, both t1 s2, reference only s1 s3 p1, other only c1, neither s4;
    # m = 4 -> p = 2 * (1 + 4) / 16
    line1 = xf["line1"]
    assert (line1["n"], line1["both"], line1["reference_only"], line1["other_only"],
            line1["neither"], line1["n_missing"], line1["n_excluded"]) == (7, 2, 3, 1, 1, 1, 0)
    # tolerance: exact binary fraction
    assert line1["p_value"] == pytest.approx(0.625, abs=TOL)
    assert line1["beats"] is False
    xo = r1["comparisons"]["O"]
    assert (xo["cross_filer"]["n"], xo["cross_filer"]["n_excluded"]) == (0, 5)
    # cross_period vs O: t1 reference only, c1 other only; m = 2 -> min(1, 2*3/4) = 1
    assert (xo["cross_period"]["reference_only"], xo["cross_period"]["other_only"]) == (1, 1)
    # tolerance: exact
    assert xo["cross_period"]["p_value"] == pytest.approx(1.0, abs=TOL)
    assert (xo["line1"]["n"], xo["line1"]["n_excluded"]) == (2, 6)
    assert set(r1["comparisons"]) == {"F", "O"}


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_line1_pools_the_three_kinds(row, arm):
    # oracle: requirement 7, line1 is one pooled test; its cells are the kinds' sums and its
    # p is the closed form on the pooled discordant counts (here 6 against 1)
    x1, f1 = _cells(row, arm, "screen.top", 2, 2, 1, 1, prefix="a")
    x2, f2 = _cells(row, arm, "trend", 1, 2, 0, 0, prefix="b")
    x3, f3 = _cells(row, arm, "compound.rank_by_ratio", 0, 2, 0, 2, prefix="k")
    r1 = build_r1([arm("X", x1 + x2 + x3), arm("F", f1 + f2 + f3)], "X", "F")
    cmp = r1["comparisons"]["F"]
    for key in ("n", "both", "reference_only", "other_only", "neither"):
        assert cmp["line1"][key] == sum(cmp[k][key] for k in ("cross_filer", "cross_period",
                                                               "compound"))
    # tolerance: closed form of small integers
    assert cmp["line1"]["p_value"] == pytest.approx(_exact_p(6, 1), abs=TOL)


@pytest.mark.criterion("C4")
@given(outcomes=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=60))
def test_p_matches_closed_form_and_is_symmetric(row, arm, outcomes):
    # oracle: requirement 7 closed form, computed here with exact integers
    x = [row(f"c{i}", "screen.top", vc=a) for i, (a, _) in enumerate(outcomes)]
    f = [row(f"c{i}", "screen.top", vc=b) for i, (_, b) in enumerate(outcomes)]
    b = sum(1 for a, o in outcomes if a and not o)
    c = sum(1 for a, o in outcomes if o and not a)
    cmp = build_r1([arm("X", x), arm("F", f)], "X", "F")["comparisons"]["F"]["line1"]
    swapped = build_r1([arm("X", f), arm("F", x)], "X", "F")["comparisons"]["F"]["line1"]
    assert (cmp["reference_only"], cmp["other_only"]) == (b, c)
    # tolerance: closed form of integers up to 2^60; float rounding only
    assert cmp["p_value"] == pytest.approx(_exact_p(b, c), abs=TOL)
    assert swapped["p_value"] == pytest.approx(cmp["p_value"], abs=TOL)
    assert cmp["beats"] is (cmp["p_value"] < 0.05 and b > c)
