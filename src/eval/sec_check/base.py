"""I/O seams of the SEC companyfacts check: the SEC data and our fact store.

Spec: docs/specs/sec-companyfacts-check.md (requirements 1 and 2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypedDict


class FactRecord(TypedDict):
    """One of our facts, as the check needs it.

    Attributes:
        fact_id: The fact store's id.
        ticker: The filer's ticker.
        cik: The filer's CIK, 10 digits.
        accession_no: The filing's accession number.
        concept: Namespace URI, ``:``, local name (as ``mv_financial_facts.concept``).
        unit: The unit string, e.g. ``USD`` or ``pure``.
        period_start: ISO start date; None for an instant.
        period_end: ISO end (or instant) date.
        value: The fact's value.
        dimensional: Whether the fact carries any dimension.
    """

    fact_id: int
    ticker: str
    cik: str
    accession_no: str
    concept: str
    unit: str
    period_start: str | None
    period_end: str
    value: float
    dimensional: bool


class CompanyFactsSource(ABC):
    """Where the SEC companyfacts JSON comes from."""

    @abstractmethod
    def company_facts(self, cik: str) -> dict:
        """Returns the companyfacts JSON for one filer.

        Args:
            cik: The filer's CIK.

        Returns:
            The parsed companyfacts document.
        """
        raise NotImplementedError


class FactStore(ABC):
    """Our fact store, as the check reads it."""

    @abstractmethod
    def facts(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        """Returns the records of the given facts, keyed by fact id.

        Args:
            fact_ids: The facts to read.

        Returns:
            One record per fact id found.
        """
        raise NotImplementedError

    @abstractmethod
    def metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None:
        """Returns a derived metric's FY ``component_fact_ids`` (role -> id or ids).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.
            metric: The metric name, as in ``mv_derived_metrics.metric_name``.

        Returns:
            The components, or None when the metric has no FY row.
        """
        raise NotImplementedError
