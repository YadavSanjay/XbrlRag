"""The fact-store seam for H-X's answer-key check: set B's, plus one filing's facts.

Spec: docs/specs/hx-authoring.md (requirement 9).
"""

from __future__ import annotations

from abc import abstractmethod

from src.eval.set_b.base import KeyFactStore


class HxKeyFactStore(KeyFactStore):
    """Set B's fact-store seam, plus the facts one filing reports for a fiscal year."""

    @abstractmethod
    def facts_in_filing(self, ticker: str, accession: str, fiscal_year: int) -> list[dict]:
        """Returns the candidates filing ``accession`` reports for ``fiscal_year``.

        Args:
            ticker: The filer's ticker.
            accession: The filing's accession number.
            fiscal_year: The filer's fiscal year, current or comparative column.

        Returns:
            Candidate dicts with set B's keys (docs/specs/hx-authoring.md, req. 9).
        """
