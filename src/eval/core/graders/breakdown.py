"""WS-9 / E2 — grading a single-filer breakdown (revenue by segment / by geography).

Distinct from :mod:`screen` because the *pipeline answers it differently*. A
cross-filer screen produces one `screen` step carrying ranked rows; a breakdown is
planned as a **separate ``lookup`` per component**, so the execution result holds N
scalar steps and no rows at all.

Grading a breakdown as a screen therefore reads zero rows and scores a fully correct
answer as a refusal — which is exactly what the first live run did, and why this
module exists.

What actually matters for a breakdown:

* **value coverage** — did the answer resolve the component values (order-irrelevant;
  a breakdown is a set, not a ranking), and
* **fact coverage** — did it cite the components' own facts.

Both are computed against ``EvalCase.expected_rows``, which the SQL-derived builder
fills from the segment/geography views.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.eval.core.graders.honesty import unexplained_breakdown_values
from src.eval.core.graders.numeric import close_enough

# A breakdown passes when it recovers most components. Not 1.0: a filer's smallest
# segments are frequently untagged or roll into "Other", and demanding every one
# would fail answers an analyst would call correct.
PASS_VALUE_COVERAGE = 0.8


@dataclass
class BreakdownMetrics:
    value_coverage: float  # expected component values found, 0..1
    fact_coverage: float | None  # expected component facts cited, 0..1
    matched_values: list[float]
    missing_values: list[float]
    n_expected: int
    n_produced: int
    # Produced amounts no expected component or subset sum accounts for (see
    # honesty.unexplained_breakdown_values) — what drives CaseGrade.needs_review.
    unexplained_values: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_coverage": round(self.value_coverage, 4),
            "fact_coverage": (
                None if self.fact_coverage is None else round(self.fact_coverage, 4)
            ),
            "matched_values": self.matched_values,
            "missing_values": self.missing_values,
            "n_expected": self.n_expected,
            "n_produced": self.n_produced,
            "unexplained_values": self.unexplained_values,
        }


def compare_breakdown(
    expected_rows: Sequence[dict[str, Any]],
    produced_values: Sequence[float],
    *,
    expected_fact_ids: Sequence[int] = (),
    produced_fact_ids: Sequence[int] = (),
    rel_tol: float = 1e-4,
) -> BreakdownMetrics:
    """Score component recovery. Each expected value is matched at most once, so a
    single produced number cannot satisfy several components."""
    expected_values = [
        float(r["value"])
        for r in expected_rows
        if isinstance(r, dict) and isinstance(r.get("value"), (int, float))
    ]
    remaining = list(produced_values)
    matched: list[float] = []
    missing: list[float] = []

    for want in expected_values:
        hit = next(
            (i for i, got in enumerate(remaining) if close_enough(got, want, rel_tol)),
            None,
        )
        if hit is None:
            missing.append(want)
        else:
            matched.append(remaining.pop(hit))

    coverage = (len(matched) / len(expected_values)) if expected_values else 0.0

    fact_coverage: float | None = None
    if expected_fact_ids:
        want_facts = {int(f) for f in expected_fact_ids}
        got_facts = {int(f) for f in produced_fact_ids}
        fact_coverage = len(want_facts & got_facts) / len(want_facts)

    return BreakdownMetrics(
        value_coverage=coverage,
        fact_coverage=fact_coverage,
        matched_values=matched,
        missing_values=missing,
        n_expected=len(expected_values),
        n_produced=len(produced_values),
        unexplained_values=unexplained_breakdown_values(
            expected_values, produced_values, rel_tol
        ),
    )


def breakdown_verdict(metrics: BreakdownMetrics) -> tuple[bool, str]:
    if metrics.n_produced == 0:
        return False, "no audited values produced for the breakdown"
    if metrics.value_coverage < PASS_VALUE_COVERAGE:
        return False, (
            f"resolved {len(metrics.matched_values)}/{metrics.n_expected} components "
            f"(coverage {metrics.value_coverage:.2f} < {PASS_VALUE_COVERAGE}); "
            f"missing {[f'{v:,.0f}' for v in metrics.missing_values[:4]]}"
        )
    return True, (
        f"resolved {len(metrics.matched_values)}/{metrics.n_expected} " f"components"
    )
