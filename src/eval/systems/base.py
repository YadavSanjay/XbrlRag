"""I/O-boundary interfaces for the eval systems under test.

``FilingResolver`` maps a case's filer and fiscal year to its canonical annual filing,
for the long-context arm (``src/eval/systems/long_context.py``). The production
implementation queries the fact store's Layer-0 registries; tests substitute a fake.

Spec: [docs/specs/long-context-arm.md](../../../docs/specs/long-context-arm.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class FilingResolver(ABC):
    """Finds the canonical annual filing for a filer and fiscal year (an I/O boundary)."""

    @abstractmethod
    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        """Returns the store key of the filing's ``canonical.htm``.

        Args:
            ticker: The filer's trading symbol.
            fiscal_year: The fiscal year of the annual report.

        Returns:
            A ``FilingStore`` key such as ``"AAPL/2025/10-K/canonical.htm"``, or None
            when no canonical annual filing exists.
        """


class CitedFilingResolver(ABC):
    """Finds the filings that hold given facts (an I/O boundary).

    Spec: docs/specs/set-b-restatement.md (requirement 3).
    """

    @abstractmethod
    def fact_filings(self, fact_ids: list[int]) -> dict[int, dict]:
        """Returns, per fact id found, the filing that holds it.

        Args:
            fact_ids: The fact ids.

        Returns:
            ``{fact_id: {"accession_no", "form_type", "canonical", "key"}}``; an unknown id
            is left out.
        """
