"""Accuracy spread over repeated runs of one suite (docs/specs/repeat-spread.md).

One run of the SQL-derived suite moves a point or two from run to run, mostly from planner
non-determinism. This summarises N runs of the same cases: mean ± sample sd, the floor (cases
passed every time) and ceiling (passed at least once), and which cases flip.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def summarize_repeats(
    runs: Sequence[Mapping[str, bool]], kinds: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Summarise repeated runs over the cases common to every run.

    Args:
        runs: One mapping per run, from case id to whether the case passed.
        kinds: Optional case id to case kind, for the per-kind summaries.

    Returns:
        The summary; see docs/specs/repeat-spread.md for each key.

    Raises:
        ValueError: Fewer than 2 runs, or no case common to every run.
    """
    if len(runs) < 2:
        raise ValueError(f"need at least 2 runs to measure spread, got {len(runs)}")
    common = set(runs[0]).intersection(*runs[1:])
    if not common:
        raise ValueError("no case is common to every run")
    ids = sorted(common)
    n = len(ids)

    accuracies = tuple(sum(1 for c in ids if run[c]) / n for run in runs)
    mean = sum(accuracies) / len(accuracies)
    std = math.sqrt(sum((a - mean) ** 2 for a in accuracies) / (len(accuracies) - 1))
    n_always = sum(1 for c in ids if all(run[c] for run in runs))
    flaky = tuple(c for c in ids if len({run[c] for run in runs}) > 1)

    by_kind: dict[str, dict[str, Any]] = {}
    if kinds is not None:
        for kind in sorted({kinds[c] for c in ids if c in kinds}):
            of_kind = [c for c in ids if kinds.get(c) == kind]
            by_kind[kind] = summarize_repeats([{c: run[c] for c in of_kind} for run in runs])

    return {
        "n_runs": len(runs),
        "n_cases": n,
        "accuracies": accuracies,
        "mean": mean,
        "std": std,
        "min": min(accuracies),
        "max": max(accuracies),
        "floor": n_always / n,
        "ceiling": (n_always + len(flaky)) / n,
        "flaky_case_ids": flaky,
        "flip_rate": len(flaky) / n,
        "by_kind": by_kind,
    }
