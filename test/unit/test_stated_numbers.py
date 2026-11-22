"""Unit tests for the shared number reader's precision helpers (src/numerics/stated_numbers.py).

The reader itself is pinned by the citation-integrity spec tests (C3); these cover the
helpers the eval grader will also use.
"""

import pytest

from src.numerics.stated_numbers import (
    is_per_share_money,
    ratio_step,
    read_stated_numbers,
    reported_step,
    within_precision,
)


@pytest.mark.parametrize("unit, expected", [
    ("USD/shares", True), ("shares*USD", True), ("EUR / shares", True),
    ("USD", False), ("shares", False), ("pure", False), (None, False), ("", False),
])
def test_per_share_money_units(unit, expected):
    assert is_per_share_money(unit) is expected


@pytest.mark.parametrize("unit, decimals, expected", [
    ("USD", -6, 1e6), ("USD", 2, 0.01), ("USD/shares", -6, 0.01),  # per share wins
    ("USD", "INF", 0.0), ("USD", None, 0.0), ("USD", True, 0.0),  # no stated precision
])
def test_reported_step(unit, decimals, expected):
    assert reported_step(unit, decimals) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("value, expected", [
    (0.2397125, 0.001), (391.0, 1.0), (-12.5, 0.1), (0.0, 0.0), (float("nan"), 0.0),
])
def test_ratio_step_is_three_significant_figures(value, expected):
    assert ratio_step(value) == pytest.approx(expected, rel=1e-12)


def test_within_precision_uses_the_coarser_step_and_ignores_sign():
    (n,) = read_stated_numbers("a loss of $1.2 million")
    assert within_precision(n, -1_249_999.0)          # 49,999 <= 1e5 / 2
    assert not within_precision(n, 1_260_000.0)       # 60,000 > 5e4
    assert within_precision(n, 1_260_000.0, rule_step=1e6)


def test_text_carries_currency_and_scale_but_not_sign():
    (n,) = read_stated_numbers("fell -US$3.5bn.")
    assert n.text == "US$3.5bn"
    assert n.start == 6
