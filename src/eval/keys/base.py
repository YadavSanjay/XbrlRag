# The I/O seam of the answer keys: SEC companyfacts and frames payloads.
# Spec: docs/specs/sec-keys.md (requirement 1).

from __future__ import annotations

from abc import ABC, abstractmethod


class SecSource(ABC):
    """Where the SEC companyfacts and frames JSON comes from."""

    @abstractmethod
    def company_facts(self, cik: str) -> dict:
        """Returns the companyfacts JSON for one filer.

        Args:
            cik: The filer's CIK.

        Returns:
            The parsed companyfacts document.
        """
        raise NotImplementedError("tdd:sec-keys")

    @abstractmethod
    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        """Returns the frames JSON for one concept, unit and calendar period.

        Args:
            taxonomy: The taxonomy key, e.g. ``us-gaap``.
            tag: The concept's local name, e.g. ``Revenues``.
            unit: The unit, e.g. ``USD``.
            period: The frame period, ``CY####`` or ``CY####Q4I``.

        Returns:
            The parsed frames document.
        """
        raise NotImplementedError("tdd:sec-keys")


class HttpTransport(ABC):
    """One HTTP GET: the I/O seam of the live SEC source (docs/specs/sec-live-source.md)."""

    @abstractmethod
    def get(self, url: str, headers: dict) -> tuple[int, bytes]:
        """Sends one GET request, without retrying or pacing.

        Args:
            url: The URL to fetch.
            headers: The request headers.

        Returns:
            The status code and the raw response body.
        """
        raise NotImplementedError("tdd:sec-live-source")
