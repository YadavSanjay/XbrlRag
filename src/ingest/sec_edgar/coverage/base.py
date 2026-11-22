# The I/O seam of the coverage card: rows of the mv_coverage_card materialized view.
# Spec: docs/specs/coverage-card.md (requirement 7).

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import NamedTuple


class CoverageRow(NamedTuple):
    """One row of ``mv_coverage_card``: a (filer, fiscal year, metric) cell with its tiers.

    Any 8-sequence in this field order is accepted wherever a row is taken.

    Attributes:
        cik: The filer's 10-digit CIK.
        ticker: The filer's trading symbol, if known.
        fiscal_year: The DEI focus year of the canonical FY filing.
        metric: A card metric id (``card.card_metrics()``).
        status: ``standard``, ``extension``, ``component_only`` or ``absent``.
        standard_qnames: Reported standard-taxonomy concepts at a primary rank (or, for an
            axis metric, the standard axes found).
        extension_qnames: Reported filer-extension concepts at a primary rank.
        component_qnames: Reported concepts in the component band (rank 50-99).
    """

    cik: str
    ticker: str | None
    fiscal_year: int
    metric: str
    status: str
    standard_qnames: tuple[str, ...]
    extension_qnames: tuple[str, ...]
    component_qnames: tuple[str, ...]


class CoverageCardSource(ABC):
    """Where the coverage card's rows come from."""

    @abstractmethod
    def rows(self, cik: str | None, fiscal_year: int | None) -> Sequence[Sequence]:
        """Returns the card rows, optionally restricted to one filer and one fiscal year.

        Args:
            cik: The filer's 10-digit CIK, or ``None`` for every filer.
            fiscal_year: The fiscal year, or ``None`` for every year.

        Returns:
            The matching rows (``CoverageRow`` or any 8-sequence in its field order), in no
            particular order.
        """
        raise NotImplementedError("tdd:coverage-card")
