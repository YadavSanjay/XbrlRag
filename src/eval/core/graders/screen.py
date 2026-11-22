"""WS-9 / E1 — grading a cross-filer ``screen`` result.

A screen answer is a **ranked list**, not a scalar, so WS-2's numeric grader has no
path for it at all — the single biggest coverage hole in the existing harness given
that ``NUMERIC_SCREEN`` is a first-class intent.

Four independent signals (design doc §7.3), because a screen can be wrong in ways a
single number hides — right entities in the wrong order, right order with wrong
values, or a truncated list:

* **top-1 exact** — did the right entity win
* **set overlap** — Jaccard over the top-N entity sets
* **rank correlation** — Spearman ρ over the intersection
* **value agreement** — per-row tolerance check against the ground-truth row

Spearman is computed in plain Python (rank-transform + Pearson) so the eval core
keeps importing without numpy/scipy.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.eval.core.graders.numeric import close_enough

# Row keys tried in order when identifying what a ranked row is about. Cross-filer
# screens key on the filer; the same machinery grades a *breakdown* within one filer
# (revenue by segment, by geography, by concept), where the "entity" is the segment
# or region instead — same four signals, same verdict.
_ENTITY_KEYS = (
    "ticker",
    "entity",
    "company",
    "symbol",
    "segment_name",
    "segment_label",
    "segment_member",
    "geo_code",
    "geo_name",
    "metric_name",
    "concept_family",
    "concept",
    "label",
    "name",
    "cik",
)
# Row keys tried in order when reading the ranked value.
_VALUE_KEYS = ("value", "metric_value", "amount", "val")


def row_entity(row: dict[str, Any]) -> str | None:
    for key in _ENTITY_KEYS:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip().upper()
    return None


def row_value(row: dict[str, Any]) -> float | None:
    for key in _VALUE_KEYS:
        val = row.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _entities(rows: Sequence[dict[str, Any]], top_n: int | None) -> list[str]:
    out: list[str] = []
    for row in rows:
        ent = row_entity(row)
        if ent is not None and ent not in out:
            out.append(ent)
        if top_n is not None and len(out) >= top_n:
            break
    return out


def _ranks(order: Sequence[str]) -> dict[str, int]:
    return {ent: i for i, ent in enumerate(order)}


def spearman(
    expected_order: Sequence[str], produced_order: Sequence[str]
) -> float | None:
    """Spearman ρ over the entities appearing in *both* rankings.

    ``None`` when fewer than two entities overlap (a correlation over one point is
    undefined, and reporting 0.0 there would read as "badly ordered" rather than
    "not measurable"). Ties are impossible — these are positions in a list.
    """
    exp_rank, prod_rank = _ranks(expected_order), _ranks(produced_order)
    shared = [e for e in expected_order if e in prod_rank]
    n = len(shared)
    if n < 2:
        return None
    xs = [exp_rank[e] for e in shared]
    ys = [prod_rank[e] for e in shared]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


@dataclass
class ScreenMetrics:
    top1_exact: bool
    set_overlap: float  # Jaccard over top-N entity sets
    rank_correlation: float | None  # Spearman ρ, None if < 2 shared entities
    value_agreement: float | None  # fraction of shared entities within tolerance
    expected_order: list[str]
    produced_order: list[str]
    n_expected: int
    n_produced: int

    def to_dict(self) -> dict:
        return {
            "top1_exact": self.top1_exact,
            "set_overlap": round(self.set_overlap, 4),
            "rank_correlation": (
                None
                if self.rank_correlation is None
                else round(self.rank_correlation, 4)
            ),
            # Unrounded: a count ratio k/n, read by graders that compare it exactly.
            "value_agreement": self.value_agreement,
            "expected_order": self.expected_order,
            "produced_order": self.produced_order,
            "n_expected": self.n_expected,
            "n_produced": self.n_produced,
        }


def compare_screen(
    expected_rows: Sequence[dict[str, Any]],
    produced_rows: Sequence[dict[str, Any]],
    *,
    top_n: int | None = None,
    rel_tol: float = 1e-3,
    value_match: Callable[[dict[str, Any], float], bool] | None = None,
) -> ScreenMetrics:
    """Score a produced ranking against the SQL ground-truth ranking.

    Args:
        expected_rows: The ground-truth rows, ranked.
        produced_rows: The answer's rows, ranked.
        top_n: How many entities each ranking is cut to.
        rel_tol: The value-agreement tolerance when ``value_match`` is None.
        value_match: ``match(expected_row, produced_value)``: the value agreement of a
            case graded at its key's precision (docs/specs/grading-precision.md).

    Returns:
        The four signals.
    """
    expected_order = _entities(expected_rows, top_n)
    produced_order = _entities(produced_rows, top_n)

    exp_set, prod_set = set(expected_order), set(produced_order)
    union = exp_set | prod_set
    overlap = (len(exp_set & prod_set) / len(union)) if union else 0.0

    top1 = bool(
        expected_order and produced_order and expected_order[0] == produced_order[0]
    )

    # Value agreement over entities present in both, using each side's own row value.
    exp_rows = {
        e: r for e, r in ((row_entity(r), r) for r in expected_rows) if e is not None
    }
    exp_values = {
        e: v
        for e, v in ((row_entity(r), row_value(r)) for r in expected_rows)
        if e is not None and v is not None
    }
    prod_values = {
        e: v
        for e, v in ((row_entity(r), row_value(r)) for r in produced_rows)
        if e is not None and v is not None
    }
    shared = [e for e in exp_values if e in prod_values]
    value_agreement: float | None = None
    if shared:
        agreed = sum(
            1 for e in shared
            if (value_match(exp_rows[e], prod_values[e]) if value_match is not None
                else close_enough(prod_values[e], exp_values[e], rel_tol))
        )
        value_agreement = agreed / len(shared)

    return ScreenMetrics(
        top1_exact=top1,
        set_overlap=overlap,
        rank_correlation=spearman(expected_order, produced_order),
        value_agreement=value_agreement,
        expected_order=expected_order,
        produced_order=produced_order,
        n_expected=len(expected_order),
        n_produced=len(produced_order),
    )


# Thresholds for turning the four signals into a single pass/fail verdict. Chosen so
# that a screen must get the winner right AND recover most of the set; ordering
# beyond that is reported but not gating, because ties in the underlying metric make
# strict order brittle.
PASS_SET_OVERLAP = 0.8
PASS_RANK_CORRELATION = 0.8
PASS_VALUE_AGREEMENT = 0.9


def screen_verdict(
    metrics: ScreenMetrics, *, order_sensitive: bool = True
) -> tuple[bool, str]:
    """Collapse :class:`ScreenMetrics` to (passed, reason)."""
    if metrics.n_produced == 0:
        return False, "no screen rows produced"
    if not metrics.top1_exact:
        return False, (
            f"top-1 mismatch: expected {metrics.expected_order[:1]}, "
            f"got {metrics.produced_order[:1]}"
        )
    if metrics.set_overlap < PASS_SET_OVERLAP:
        return False, (
            f"set overlap {metrics.set_overlap:.2f} < {PASS_SET_OVERLAP} "
            f"(expected {metrics.expected_order}, got {metrics.produced_order})"
        )
    if (
        metrics.value_agreement is not None
        and metrics.value_agreement < PASS_VALUE_AGREEMENT
    ):
        return (
            False,
            f"value agreement {metrics.value_agreement:.2f} < {PASS_VALUE_AGREEMENT}",
        )
    if (
        order_sensitive
        and metrics.rank_correlation is not None
        and metrics.rank_correlation < PASS_RANK_CORRELATION
    ):
        return (
            False,
            f"rank correlation {metrics.rank_correlation:.2f} < {PASS_RANK_CORRELATION}",
        )
    return True, "ok"
