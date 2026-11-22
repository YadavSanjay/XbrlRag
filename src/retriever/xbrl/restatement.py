"""Which filing answers a period (S1): re-exported from :mod:`src.numerics.restatement`.

The pure functions moved to ``src.numerics.restatement`` so that set B's tooling, which must
not import ``src.retriever``, uses the same definition as the product
(docs/specs/set-b-restatement.md, requirement 1). This module keeps the old import path.
"""

from __future__ import annotations

from src.numerics.restatement import (
    COVER_SHARES_LOCAL_NAME,
    breakdown_filings,
    current_column,
    dimension_identity,
    latest_dimensional_restatement,
    latest_restatement,
    yoy_pairs,
)

__all__ = ["COVER_SHARES_LOCAL_NAME", "breakdown_filings", "current_column",
           "dimension_identity", "latest_dimensional_restatement", "latest_restatement",
           "yoy_pairs"]
