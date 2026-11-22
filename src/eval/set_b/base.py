"""The fact-store seam for set B's answer-key check.

Spec: docs/specs/set-b-tooling.md (requirement 5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class KeyFactStore(ABC):
    """Candidate facts for one filer-year, read from our fact store."""

    @abstractmethod
    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the filer-year's annual and FY-end facts, flat and dimensional.

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            Candidate dicts with the keys in requirement 5 of the spec.
        """

    def breakdown_facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the filer-year's breakdown members: one filing's split (S1).

        The default is ``facts_for``'s candidates, so a store that knows no breakdown
        rule keeps today's behaviour.

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            Candidate dicts with the keys in requirement 5 of the spec.

        Spec: docs/specs/set-b-dimensional-s1.md (requirement 3).
        """
        return self.facts_for(ticker, fiscal_year)
