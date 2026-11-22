"""C6: the lines of the success bar (§3 as amended) and their exact boundaries."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import build_r1

# Rational counts; 1e-12 only absorbs float division.
TOL = 1e-12


def _passing(row, arm, *, x_cost=0.125, f_cost=1.25, x_lookup_wrong=0, n_cw=0, gp=1.0,
             unstable=0):
    """A report on which every line holds, with knobs that move one line each.

    Line 1: 10 screen cases, 8 with X right and F wrong (p = 2/256), 2 both right; O
    excludes them. Grounding: only s0 carries a value, ``gp``.
    Line 3: 20 lookups l0..l19; O right on all, X wrong on the first ``x_lookup_wrong``.
    Confident-wrong: ``n_cw`` of X's 100 rows (on the 70 dimensional rows).
    Stability: intent i<j> holds l(2j) and l(2j+1); ``unstable`` intents from i9 down get
    their first lookup flipped.
    """
    x, f, o = [], [], []
    for i in range(10):
        x.append(row(f"s{i}", "screen.top", vc=True, gp=gp if i == 0 else None, cost=x_cost))
        f.append(row(f"s{i}", "screen.top", vc=i >= 8, cost=f_cost))
        o.append(row(f"s{i}", "screen.top", vc=None, error="excluded: cross-filer"))
    for i in range(20):
        x.append(row(f"l{i}", "lookup", vc=i >= x_lookup_wrong, cost=x_cost, intent=f"i{i // 2}"))
        f.append(row(f"l{i}", "lookup", vc=True, cost=f_cost))
        o.append(row(f"l{i}", "lookup", vc=True, cost=0.5))
    for i in range(70):
        cw = i < n_cw
        x.append(row(f"m{i}", "dimensional", vc=not cw, cw=cw, cost=x_cost))
        f.append(row(f"m{i}", "dimensional", vc=True, cost=f_cost))
        o.append(row(f"m{i}", "dimensional", vc=True, cost=0.5))
    for j in range(unstable):
        idx = 10 + 20 - 2 * (j + 1)  # x[10 + k] is l<k>
        x[idx]["value_correct"] = not x[idx]["value_correct"]
    return [arm("X", x), arm("F", f), arm("O", o)]


def _bar(arms):
    return build_r1(arms, "X", "F", oracle="O")["bar"]


@pytest.mark.criterion("C6")
def test_all_lines_hold_and_met(row, arm):
    # oracle: requirement 8 on the hand-built passing report (8 vs 0 discordant:
    # p = 2/256; cost 10 * 0.125 = 1.25 <= 1.25; 0 confident-wrong; grounding 1.0;
    # 10 stable intents of 10; X equals O on lookups)
    bar = _bar(_passing(row, arm))
    assert bar["line1"]["beats"] is True
    # tolerance: exact binary fraction
    assert bar["line1"]["p_value"] == pytest.approx(2 / 256, abs=TOL)
    assert bar["line1"]["cost_ok"] is True
    assert bar["line1"]["holds"] is True
    assert bar["line2"]["status"] == "withdrawn"
    assert bar["line2"]["holds"] is True
    assert bar["line3"]["holds"] is True
    assert bar["confident_wrong"]["holds"] is True
    assert bar["grounding"]["holds"] is True
    assert bar["stability"]["holds"] is True
    assert bar["met"] is True


@pytest.mark.criterion("C6")
def test_cost_ratio_boundary(row, arm):
    # oracle: requirement 8, cost_ok iff 10 * reference mean <= other mean; 0.126 > 0.125
    bar = _bar(_passing(row, arm, x_cost=0.126))
    assert bar["line1"]["cost_ok"] is False
    assert bar["line1"]["holds"] is False
    assert bar["met"] is False


@pytest.mark.criterion("C6")
def test_line3_boundary(row, arm):
    # oracle: requirement 8, line 3 on 20 lookups: one more X miss than O is -5.0 points
    # (holds); two is -10.0 (misses)
    bar = _bar(_passing(row, arm, x_lookup_wrong=1))
    assert bar["line3"]["n"] == 20
    # tolerance: k / n of small integers
    assert bar["line3"]["difference_points"] == pytest.approx(-5.0, abs=1e-9)
    assert bar["line3"]["reference_accuracy"] == pytest.approx(19 / 20, abs=TOL)
    assert bar["line3"]["oracle_accuracy"] == pytest.approx(1.0, abs=TOL)
    assert bar["line3"]["holds"] is True
    bar = _bar(_passing(row, arm, x_lookup_wrong=2))
    assert bar["line3"]["difference_points"] == pytest.approx(-10.0, abs=1e-9)
    assert bar["line3"]["holds"] is False
    assert bar["met"] is False


@pytest.mark.criterion("C6")
def test_line3_without_oracle(row, arm):
    # oracle: requirement 8, no oracle: "not computed", and the bar is not met
    arms = _passing(row, arm)[:2]
    bar = build_r1(arms, "X", "F")["bar"]
    assert bar["line3"]["status"] == "not computed"
    assert bar["line3"]["holds"] is False
    assert bar["met"] is False


@pytest.mark.criterion("C6")
def test_confident_wrong_boundary(row, arm):
    # oracle: requirement 8, <= 1% of X's 100 rows: 1 holds, 2 misses
    bar = _bar(_passing(row, arm, n_cw=1))
    assert bar["confident_wrong"]["n"] == 100
    assert bar["confident_wrong"]["n_confident_wrong"] == 1
    # tolerance: 1 / 100
    assert bar["confident_wrong"]["rate"] == pytest.approx(0.01, abs=TOL)
    assert bar["confident_wrong"]["holds"] is True
    bar = _bar(_passing(row, arm, n_cw=2))
    assert bar["confident_wrong"]["holds"] is False
    assert bar["met"] is False


@pytest.mark.criterion("C6")
def test_grounding_boundary(row, arm):
    # oracle: requirement 8, grounding >= 0.95 (only s0 carries a value)
    bar = _bar(_passing(row, arm, gp=0.95))
    # tolerance: the mean of one value
    assert bar["grounding"]["value"] == pytest.approx(0.95, abs=TOL)
    assert bar["grounding"]["holds"] is True
    bar = _bar(_passing(row, arm, gp=0.9499))
    assert bar["grounding"]["holds"] is False
    assert bar["met"] is False
    bar = _bar(_passing(row, arm, gp=None))
    assert bar["grounding"]["value"] is None
    assert bar["grounding"]["holds"] is False


@pytest.mark.criterion("C6")
def test_stability_boundary(row, arm):
    # oracle: requirement 8, >= 90% of intents stable: 9 of 10 holds, 8 of 10 misses
    bar = _bar(_passing(row, arm, unstable=1))
    assert (bar["stability"]["n_intents"], bar["stability"]["n_stable"]) == (10, 9)
    # tolerance: 9 / 10
    assert bar["stability"]["value"] == pytest.approx(0.9, abs=TOL)
    assert bar["stability"]["holds"] is True
    bar = _bar(_passing(row, arm, unstable=2))
    assert (bar["stability"]["n_intents"], bar["stability"]["n_stable"]) == (10, 8)
    assert bar["stability"]["holds"] is False
    assert bar["met"] is False


@pytest.mark.criterion("C6")
def test_stability_without_intents(row, arm):
    # oracle: requirement 8, no intent with two rows -> value None, misses
    x = [row("a1", "screen.top"), row("a2", "lookup", intent="solo")]
    bar = build_r1([arm("X", x), arm("F", [dict(r) for r in x])], "X", "F")["bar"]
    assert bar["stability"]["n_intents"] == 0
    assert bar["stability"]["value"] is None
    assert bar["stability"]["holds"] is False


@pytest.mark.criterion("C6")
@given(n=st.integers(min_value=1, max_value=60), data=st.data())
def test_line3_and_confident_wrong_follow_their_inequalities(row, arm, n, data):
    # oracle: requirement 8's count inequalities, computed here:
    # line 3 holds iff 20 * (k_ref - k_or) >= -n; confident-wrong iff 100 * k_cw <= n
    k_ref = data.draw(st.integers(min_value=0, max_value=n))
    k_or = data.draw(st.integers(min_value=0, max_value=n))
    k_cw = data.draw(st.integers(min_value=0, max_value=n - k_ref))
    x, f, o = [], [], []
    for i in range(n):
        right = i < k_ref
        cw = (not right) and i < k_ref + k_cw
        x.append(row(f"l{i}", "lookup", vc=right, cw=cw))
        f.append(row(f"l{i}", "lookup", vc=True))
        o.append(row(f"l{i}", "lookup", vc=i < k_or))
    bar = build_r1([arm("X", x), arm("F", f), arm("O", o)], "X", "F", oracle="O")["bar"]
    assert bar["line3"]["holds"] is (20 * (k_ref - k_or) >= -n)
    assert bar["line3"]["difference_points"] == pytest.approx(100 * (k_ref - k_or) / n,
                                                             abs=1e-9)
    assert bar["confident_wrong"]["holds"] is (100 * k_cw <= n)
    assert bar["met"] is False  # no line-1 case: line 1 cannot hold
