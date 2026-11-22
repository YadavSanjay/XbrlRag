"""C5: mean cost per question, latency p50/p95 and the line-1 cost ratio."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.r1_report import build_r1

# Means of binary-exact values over small n; 1e-12 only absorbs float rounding.
TOL = 1e-12


def _linear_percentile(values: list[float], q: float) -> float:
    """rank = q * (m - 1) over the sorted values, linear between neighbours (requirement 5)."""
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


@pytest.mark.criterion("C5")
def test_scenario_latency_by_hand(scenario):
    # oracle: hand arithmetic. X all: 100..500 -> p50 300, p95 = 400 + 0.8 * 100 = 480.
    # F all: 1000, 3000 -> p50 2000, p95 = 1000 + 0.95 * 2000 = 2900.
    # O all: excluded rows (7 ms) left out; 10, 20, 30, 50 -> p50 25, p95 = 30 + 0.85 * 20 = 47
    r1 = build_r1(scenario(), "X", "F", oracle="O")
    g = r1["groups"]["all"]
    # tolerance: interpolation of integers; float rounding only
    assert g["X"]["latency_p50_ms"] == pytest.approx(300.0, abs=1e-9)
    assert g["X"]["latency_p95_ms"] == pytest.approx(480.0, abs=1e-9)
    assert g["F"]["latency_p50_ms"] == pytest.approx(2000.0, abs=1e-9)
    assert g["F"]["latency_p95_ms"] == pytest.approx(2900.0, abs=1e-9)
    assert g["O"]["latency_p50_ms"] == pytest.approx(25.0, abs=1e-9)
    assert g["O"]["latency_p95_ms"] == pytest.approx(47.0, abs=1e-9)
    assert r1["groups"]["compound"]["X"]["latency_p50_ms"] is None


@pytest.mark.criterion("C5")
def test_scenario_mean_cost_by_hand(scenario):
    # oracle: hand arithmetic. X 0.0625 per row; F 1.0 per row; O 0.5 per evaluable row
    # (its excluded rows leave the denominator: 2.5 / 5, not 2.5 / 12)
    r1 = build_r1(scenario(), "X", "F", oracle="O")
    g = r1["groups"]["all"]
    # tolerance: means of binary-exact values
    assert g["X"]["mean_cost_usd"] == pytest.approx(0.0625, abs=TOL)
    assert g["F"]["mean_cost_usd"] == pytest.approx(1.0, abs=TOL)
    assert g["O"]["mean_cost_usd"] == pytest.approx(0.5, abs=TOL)
    line1 = r1["comparisons"]["F"]["line1"]
    assert line1["reference_mean_cost_usd"] == pytest.approx(0.0625, abs=TOL)
    assert line1["other_mean_cost_usd"] == pytest.approx(1.0, abs=TOL)
    assert line1["cost_ratio"] == pytest.approx(0.0625, abs=TOL)


@pytest.mark.criterion("C5")
def test_cost_ratio_uses_pairs_only(row, arm):
    # oracle: requirement 7, the ratio's means are over the line-1 pairs. X's unpaired
    # case a3 (missing from F) costs 100 and must not count: 0.25 / 2.0 = 0.125
    x = [row("a1", "screen.top", cost=0.25), row("a2", "trend", cost=0.25),
         row("a3", "screen.top", cost=100.0)]
    f = [row("a1", "screen.top", cost=2.0), row("a2", "trend", cost=2.0)]
    line1 = build_r1([arm("X", x), arm("F", f)], "X", "F")["comparisons"]["F"]["line1"]
    # tolerance: binary-exact values
    assert line1["reference_mean_cost_usd"] == pytest.approx(0.25, abs=TOL)
    assert line1["other_mean_cost_usd"] == pytest.approx(2.0, abs=TOL)
    assert line1["cost_ratio"] == pytest.approx(0.125, abs=TOL)


@pytest.mark.criterion("C5")
def test_null_cost_reads_zero_and_null_latency_is_left_out(row, arm):
    # oracle: requirement 2 (None cost reads 0.0) and 5 (null latency left out)
    x = [row("a1", cost=None, latency=None), row("a2", cost=1.0, latency=40.0)]
    r1 = build_r1([arm("X", x), arm("F", [dict(r) for r in x])], "X", "F")
    cell = r1["groups"]["all"]["X"]
    # tolerance: binary-exact
    assert cell["mean_cost_usd"] == pytest.approx(0.5, abs=TOL)
    assert cell["latency_p50_ms"] == pytest.approx(40.0, abs=TOL)
    assert cell["latency_p95_ms"] == pytest.approx(40.0, abs=TOL)


@pytest.mark.criterion("C5")
@given(lat=st.lists(st.integers(min_value=0, max_value=100_000), min_size=1, max_size=40),
       costs=st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=40),
       scale=st.sampled_from([0.5, 2.0, 4.0, 0.25]))
def test_percentiles_closed_form_and_cost_scaling(row, arm, lat, costs, scale):
    # oracle: the linear-interpolation closed form computed here; scaling every cost by s
    # scales the mean by s (s a power of two keeps it exact)
    n = min(len(lat), len(costs))
    x = [row(f"c{i}", cost=costs[i] / 8, latency=float(lat[i])) for i in range(n)]
    xs = [row(f"c{i}", cost=costs[i] / 8 * scale, latency=float(lat[i])) for i in range(n)]
    r1 = build_r1([arm("X", x), arm("F", xs)], "X", "F")
    cx, cf = r1["groups"]["all"]["X"], r1["groups"]["all"]["F"]
    values = [float(v) for v in lat[:n]]
    # tolerance: interpolation of integers up to 1e5; relative float rounding only
    assert cx["latency_p50_ms"] == pytest.approx(_linear_percentile(values, 0.50), rel=1e-12,
                                                 abs=1e-9)
    assert cx["latency_p95_ms"] == pytest.approx(_linear_percentile(values, 0.95), rel=1e-12,
                                                 abs=1e-9)
    assert cx["latency_p50_ms"] <= cx["latency_p95_ms"]
    expected_mean = sum(costs[:n]) / 8 / n
    assert cx["mean_cost_usd"] == pytest.approx(expected_mean, rel=1e-12, abs=TOL)
    assert cf["mean_cost_usd"] == pytest.approx(expected_mean * scale, rel=1e-12, abs=TOL)
