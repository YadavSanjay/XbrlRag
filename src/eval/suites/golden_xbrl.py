"""WS-9 / E1 — the WS-2 golden set, adapted to the eval-core suite type.

The 13-case A3 set ([test/eval/golden/xbrl_golden_v1.json](../../../test/eval/golden/xbrl_golden_v1.json))
is not rewritten or migrated — it is *read* through this adapter, so the file
stays the input to both the original ``NumericGrader`` (and the drift check and
regression matrix built on it) and the new :class:`~src.eval.core.runner.EvalRunner`.
One committed file, two harnesses, no divergence.

Its cases carry no ``expected_fact_ids``, so grounding is not asserted for them —
correctly reported as ``None`` rather than scored as a failure. Fact-level grounding
arrives with the Phase-1 SQL-derived suite, where the ground-truth query knows
exactly which fact it read.
"""

from __future__ import annotations

import os
from typing import List, Optional

from src.eval.core.case import KIND_COMPUTE, KIND_LOOKUP, EvalCase, EvalSuite
from src.eval.xbrl.golden import DEFAULT_GOLDEN_PATH, GoldenCase, load_golden_set

SUITE_NAME = "golden_xbrl"
SUITE_VERSION = "v1"

# GoldenCase.kind vocabulary -> EvalCase kind. "dimensional" is shared verbatim.
_KIND_MAP = {"lookup": KIND_LOOKUP, "compute": KIND_COMPUTE}


def to_eval_case(case: GoldenCase) -> EvalCase:
    """Adapt one :class:`GoldenCase`. Field names differ (`query` vs `question`) and
    the golden schema has no rows/grounding/paraphrase concepts."""
    return EvalCase(
        id=case.id,
        question=case.query,
        suite=f"{SUITE_NAME}_{SUITE_VERSION}",
        kind=_KIND_MAP.get(case.kind, case.kind),
        expected_value=case.expected_value,
        alt_values=list(case.alt_values),
        rel_tol=case.rel_tol,
        require_citation=case.require_citation,
        origin=f"{SUITE_NAME}:{case.id}",
        ticker=case.ticker,
        fy=case.fy,
        concept=case.concept,
        unit=case.unit,
        tags=list(case.tags),
    )


def load(path: str | None = None) -> EvalSuite:
    """Load the committed golden set as an :class:`EvalSuite`."""
    resolved = path or DEFAULT_GOLDEN_PATH
    cases: list[GoldenCase] = load_golden_set(resolved)
    return EvalSuite(
        name=SUITE_NAME,
        version=SUITE_VERSION,
        cases=[to_eval_case(c) for c in cases],
        description=(
            "WS-2 / A3 static golden set (hand-built, snapshotted from the fact store) "
            f"read from {os.path.basename(resolved)}"
        ),
    )
