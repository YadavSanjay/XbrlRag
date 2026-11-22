# The I/O seams of the A' draw: EDGAR filing indexes and the NIST beacon.
# Spec: docs/specs/aprime-draw.md (requirement 11).

from __future__ import annotations

from abc import ABC, abstractmethod


class FilingIndexSource(ABC):
    """A filer's filing-index records (no financial data)."""

    @abstractmethod
    def filings(self, cik: str) -> list[dict]:
        """Returns every filing-index record of a filer.

        Args:
            cik: The filer's CIK.

        Returns:
            Records with the keys in requirement 8 of the spec.
        """
        raise NotImplementedError("tdd:aprime-draw")


class BeaconSource(ABC):
    """NIST Randomness Beacon 2.0 pulses."""

    @abstractmethod
    def pulse(self, timestamp_utc: str) -> dict:
        """Returns the pulse object for one minute.

        Args:
            timestamp_utc: The pulse time, ISO 8601 UTC, on a whole minute.

        Returns:
            The ``pulse`` member of the beacon's JSON.
        """
        raise NotImplementedError("tdd:aprime-draw")
