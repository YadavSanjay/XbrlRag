"""The statistics pre-registered for external test v1 (docs/specs/blog-stats.md).

Wilson intervals for every proportion, the 2-of-3 rule for a system run three times, and
the exact McNemar test of XbrlRag against each baseline (pre-registration v1 §5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

# The 0.975 quantile of the standard normal: a two-sided 95% interval.
_Z95 = 1.959963984540054


def wilson_interval(k: int, n: int) -> tuple[float, float]:
    """The Wilson score 95% interval, without continuity correction.

    Args:
        k: Successes.
        n: Trials.

    Returns:
        ``(lower, upper)``, within ``[0, 1]``.

    Raises:
        ValueError: ``n < 1``, ``k < 0`` or ``k > n``.
    """
    if n < 1 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n and n >= 1, got k={k}, n={n}")
    z2 = _Z95 * _Z95
    p = k / n
    d = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / d
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / d
    # At the edges the closed form reaches 0 or 1 only up to rounding; pin them.
    lower = 0.0 if k == 0 else max(0.0, centre - half)
    upper = 1.0 if k == n else min(1.0, centre + half)
    return lower, upper


def majority_outcomes(runs: Sequence[Mapping[str, bool]]) -> dict[str, bool]:
    """Per case, whether it passed in more than half of the runs (2 of 3; 1 of 1).

    Args:
        runs: One mapping per run, from case id to whether the case passed.

    Returns:
        Case id to the majority outcome.

    Raises:
        ValueError: An even number of runs, no run, or runs with different case ids.
    """
    if len(runs) % 2 == 0:
        raise ValueError(f"need an odd number of runs, got {len(runs)}")
    ids = set(runs[0])
    if any(set(run) != ids for run in runs[1:]):
        raise ValueError("runs cover different case ids; check the run ids")
    return {c: 2 * sum(1 for run in runs if run[c]) > len(runs) for c in sorted(ids)}


def mcnemar_exact(a: Mapping[str, bool], b: Mapping[str, bool]) -> dict[str, Any]:
    """Two-sided exact McNemar test of two systems over the cases both were given.

    A decline is a failed case like any other; only case ids missing from one side are
    left out.

    Args:
        a: Case id to whether system A passed it.
        b: Case id to whether system B passed it.

    Returns:
        ``n``, ``both``, ``a_only``, ``b_only``, ``neither`` and ``p_value``.

    Raises:
        ValueError: The two mappings share no case id.
    """
    shared = set(a) & set(b)
    if not shared:
        raise ValueError("the two runs share no case")
    both = sum(1 for c in shared if a[c] and b[c])
    a_only = sum(1 for c in shared if a[c] and not b[c])
    b_only = sum(1 for c in shared if b[c] and not a[c])
    m = a_only + b_only
    if m == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(m, i) for i in range(min(a_only, b_only) + 1))
        p_value = min(1.0, 2 * tail / 2**m)
    return {
        "n": len(shared),
        "both": both,
        "a_only": a_only,
        "b_only": b_only,
        "neither": len(shared) - both - a_only - b_only,
        "p_value": p_value,
    }
