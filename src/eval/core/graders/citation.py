"""WS-9 / E1 — fact-level grounding metrics.

WS-2 could only ask *"is there a citation?"* (``numeric.has_fact_citation``). That
cannot distinguish **the right fact** from **a fact** — an answer citing an
unrelated `fact_id` scores identically to a correctly-sourced one.

Because the SQL-derived suite knows the exact `fact_id`(s) its ground-truth value
came from, we can grade grounding directly. No external benchmark supports this:
their evidence is a page string, not a fact identity (design doc §7.2).

Default policy is **containment**, not exact match — a correct answer may
legitimately cite additional supporting facts (e.g. a compute step citing both
components plus the derived-metric row). ``EvalCase.require_exact_fact`` opts into
strict set equality.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import List, Optional, Set


@dataclass
class GroundingResult:
    precision: float | None  # produced ∩ expected / produced
    recall: float | None  # produced ∩ expected / expected
    matched: list[int]
    missing: list[int]  # expected but not produced
    extra: list[int]  # produced but not expected
    satisfied: bool  # meets the case's grounding policy

    def to_dict(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "matched": self.matched,
            "missing": self.missing,
            "extra": self.extra,
            "satisfied": self.satisfied,
        }


def _as_set(ids: Iterable[int]) -> set[int]:
    return {int(i) for i in ids}


def grade_grounding(
    expected_fact_ids: Sequence[int],
    produced_fact_ids: Sequence[int],
    *,
    require_exact: bool = False,
    fact_id_classes: Sequence[Sequence[int]] | None = None,
) -> GroundingResult:
    """Compare the facts an answer cited against the facts its ground truth came from.

    Grading is per **equivalence class**, not per raw id. A filing routinely tags the
    same number on several rows, so citing a different member of the same class is a
    correct citation — insisting on one surrogate ``fact_id`` measured row-selection
    convention rather than whether the answer was sourced correctly, and turned 48
    correct answers in the first baseline into ``PARTIAL``.

    ``fact_id_classes`` defaults to one singleton class per expected id, reproducing
    the original strict id-equality behaviour.

    With no expected ids (an unlabelled case), precision/recall are ``None`` and the
    result is ``satisfied`` — grounding simply isn't being asserted, and a missing
    label must not be scored as a failure.
    """
    produced = _as_set(produced_fact_ids)
    classes: list[set[int]] = (
        [_as_set(c) for c in fact_id_classes if c]
        if fact_id_classes
        else [{int(f)} for f in expected_fact_ids]
    )

    if not classes:
        return GroundingResult(None, None, [], [], sorted(produced), satisfied=True)

    accepted: set[int] = set().union(*classes)  # any id that counts as cited
    missed = [c for c in classes if not (c & produced)]
    matched = sorted(accepted & produced)

    # Recall is over CLASSES: each required fact must be evidenced once, so citing two
    # duplicates of the same fact cannot satisfy two distinct requirements.
    recall = (len(classes) - len(missed)) / len(classes)
    precision = (len(matched) / len(produced)) if produced else 0.0

    if require_exact:
        satisfied = bool(produced) and produced <= accepted and not missed
    else:
        satisfied = not missed  # every required fact evidenced

    return GroundingResult(
        precision=precision,
        recall=recall,
        matched=matched,
        # Report the canonical (lowest) id of each unmet class; the full class would
        # be noise in a failure message.
        missing=sorted(min(c) for c in missed),
        extra=sorted(produced - accepted),
        satisfied=satisfied,
    )
