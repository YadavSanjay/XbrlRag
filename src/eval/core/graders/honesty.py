"""Grader honesty: which grader scored a run, and what a breakdown failed to explain.

Two small pure helpers behind the eval-grader-honesty spec
([docs/specs/eval-grader-honesty.md](../../../../docs/specs/eval-grader-honesty.md)):

* :func:`grader_version` is persisted with every scorecard. Runs graded by different
  grader logic are different measurements, and comparing them as one trajectory
  mixes a grader change in with a system change.
* :func:`unexplained_breakdown_values` keeps the breakdown exemption from silent-wrong
  honest. A breakdown answered at a different decomposition level returns real facts
  whose amounts are sums of the expected components; an amount that no component and
  no subset of components adds up to is something else, and is surfaced for review.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

from src.eval.core.graders.numeric import close_enough

# Bump whenever a grading rule changes in a way that can move a case's grade or a
# scorecard number. History:
#   1 — implicit: every run before versioning (persisted as NULL).
#   2 — HEDGED_MISS grade, decline rate, breakdown needs_review flag.
#   3 — arm-neutral confident-wrong and value-correct, on every case and the scorecard.
#   4 — v2 cases compared at their key's stated precision, IMPRECISE grade, loss and
#       direction words, every arm (XbrlRag included) graded on its stated answer.
#   5 — peer-relative rank read from the stated rank, else the rows' values, never the row
#       order.
#   6 — a first-person "I/we ranked N" is a count of companies, not a rank.
#   7 — reviewed-drop members graded "don't care", a declined aggregate never matched on a
#       listed row, any order within a tie, date parts never produced values.
#   8 — a failed synthesis graded ERROR (refusal: REFUSED) for every kind; numbers in the
#       appended universe sentences are neither produced values nor ranks.
#   9 — counts of days or weeks and rank numbers (ordinals, #N, rank N, "of M") are never
#       produced values.
#  10 — a declining sum or median answer's value equal to a listed row is still read when a
#       sentence names the aggregate, does not decline and states it as its one row value.
#  11 — a question (a sentence ending at `?`, such as an echoed `[sqN]` sub-question) is
#       never a compound's pick sentence; a growth key asked as a 'change' without a percent
#       cue also accepts the absolute change.
#  12 — a `[sqN]` sub-query echo line is never a compound's pick sentence, whatever its final
#       punctuation; 'how much did X grow / increase / decrease / rise / fall / decline'
#       without a percent cue also accepts the absolute change (grow verbs are no longer
#       percent cues).
#  13 — a compound is value-correct when every graded part is, cited or not (an uncited
#       right part no longer makes the compound a bare PARTIAL that is never value-correct).
#  14 — the v2 grader reads a JSON reply's "text" (its prose) as it reads XbrlRag's prose; a
#       generated compound's ranking part is right when its rows name the key's top N, in any
#       order and with any figures (docs/specs/prose-and-ranking-names.md).
GRADER_VERSION = "14"

# Breakdowns in the suite carry at most a dozen components (2^12 subsets). Above this
# bound only single components and the total are tried, so a pathological case cannot
# make grading exponential.
_MAX_SUBSET_COMPONENTS = 12

# Amounts below this magnitude are ratios or shares of the total, not component values.
_SHARE_MAGNITUDE = 1.0


def grader_version() -> str:
    """The version of the grading logic that produced a scorecard.

    Runs graded by different grader logic are different measurements; persisting this
    with every run is what keeps them from being compared as if they were the same.

    Returns:
        A non-empty version string, stable for a given release of the graders.
    """
    return GRADER_VERSION


def _explaining_sums(expected_values: Sequence[float]) -> list[float]:
    """Every amount a correct decomposition could legitimately assert."""
    values = [float(v) for v in expected_values]
    if len(values) > _MAX_SUBSET_COMPONENTS:
        return [*values, sum(values)]
    return [
        sum(subset)
        for size in range(1, len(values) + 1)
        for subset in combinations(values, size)
    ]


def unexplained_breakdown_values(
    expected_values: Sequence[float],
    produced_values: Sequence[float],
    rel_tol: float,
) -> list[float]:
    """Produced breakdown amounts that the expected decomposition cannot account for.

    A produced value is explained when it matches, within ``rel_tol``, an expected
    component or the sum of any non-empty subset of expected components (the total
    included). Values with ``abs(v) < 1`` are ratios or shares and are never reported.

    Args:
        expected_values: The ground-truth component amounts.
        produced_values: The amounts the answer asserted, in any order.
        rel_tol: Relative tolerance for a match.

    Returns:
        The unexplained produced values, sorted ascending.
    """
    sums = _explaining_sums(expected_values)
    return sorted(
        float(v)
        for v in produced_values
        if abs(v) >= _SHARE_MAGNITUDE
        and not any(close_enough(v, s, rel_tol) for s in sums)
    )
