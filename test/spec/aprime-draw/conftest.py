"""Fakes and builders for the aprime-draw spec tests.

Spec: docs/specs/aprime-draw.md. The only fake sits at the I/O seam the spec names:
``FakeFilings``, a ``FilingIndexSource`` returning synthetic filing-index records per CIK
and recording every call. The builders make filing records, candidates and pulses by hand,
and ``rk`` recomputes the requirement-5 ranking key with ``hashlib`` in the test. Everything
is exposed through one session-scoped ``kit`` fixture so hypothesis tests can use it without
the function-scoped-fixture health check. All values are synthetic and chosen by hand,
except the five P CIKs (pre-registration v0 section 3); no network, no data directory, no
current date.
"""

from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.aprime.base import FilingIndexSource

PREFIX = "xbrlrag-aprime-v1|"

# The five P filers, pre-registration v0 section 3 (quoted in requirement 4).
P_CIKS = {
    "AAPL": "0000320193",
    "QCOM": "0000804328",
    "EMR": "0000032604",
    "DIS": "0001744489",
    "SBUX": "0000829224",
}

GICS = [
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy",
    "Financials", "Health Care", "Industrials", "Information Technology", "Materials",
    "Real Estate", "Utilities",
]

# A synthetic 128-hex outputValue, lower case (the pulse format allows either case).
OV = ("0123456789abcdef" * 8)


def rk(output_value: str, cik: str) -> str:
    """Requirement 5, recomputed in the test: sha256 of prefix | OV upper | cik10."""
    cik10 = str(cik).zfill(10)
    return hashlib.sha256((PREFIX + output_value.upper() + "|" + cik10).encode("ascii")).hexdigest()


def tenk(report_date: str | None, *, inline: bool = True, form: str = "10-K",
         acc: str = "0000000000-00-000000") -> dict[str, Any]:
    """One filing-index record; ``report_date=None`` leaves the key out."""
    rec: dict[str, Any] = {
        "form": form,
        "filing_date": "2026-03-01",
        "accession_no": acc,
        "is_inline_xbrl": inline,
    }
    if report_date is not None:
        rec["report_date"] = report_date
    return rec


def eligible_filings() -> list[dict[str, Any]]:
    """Inline 10-Ks for fiscal 2023, 2024 and 2025 (year-end June 30)."""
    return [tenk("2023-06-30", acc="a-23"), tenk("2024-06-30", acc="a-24"),
            tenk("2025-06-30", acc="a-25")]


def missing_2023_filings() -> list[dict[str, Any]]:
    """Inline 10-Ks for 2024 and 2025 only."""
    return [tenk("2024-06-30", acc="b-24"), tenk("2025-06-30", acc="b-25")]


def not_inline_2024_filings() -> list[dict[str, Any]]:
    """Three 10-Ks, the 2024 one not inline."""
    return [tenk("2023-06-30", acc="c-23"), tenk("2024-06-30", inline=False, acc="c-24"),
            tenk("2025-06-30", acc="c-25")]


def candidate(cik: str, stratum: str, symbol: str | None = None) -> dict[str, Any]:
    """One pool candidate (requirement 4's shape)."""
    sector = "Information Technology" if stratum == "S" else "Industrials"
    sym = symbol or f"T{cik[-4:]}"
    return {"symbol": sym, "name": f"Synthetic {sym} Corp", "sector": sector, "cik": cik,
            "stratum": stratum}


def pulse(output_value: str = OV, stamp: str = "2026-11-02T15:00:00.000Z") -> dict[str, Any]:
    """A NIST Beacon 2.0 ``pulse`` object with the members the spec reads."""
    return {"uri": "https://beacon.example.invalid/pulse/1", "version": "Version 2.0",
            "timeStamp": stamp, "outputValue": output_value}


class FakeFilings(FilingIndexSource):
    """Returns a deep copy of the configured records per CIK ([] if unknown); records calls."""

    def __init__(self, by_cik: dict[str, list[dict]] | None = None) -> None:
        self.by_cik = {k: list(v) for k, v in (by_cik or {}).items()}
        self.calls: list[str] = []

    def filings(self, cik: str) -> list[dict]:
        self.calls.append(cik)
        return copy.deepcopy(self.by_cik.get(cik, []))


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The builders and the fake, as one namespace."""
    return SimpleNamespace(
        PREFIX=PREFIX,
        P_CIKS=P_CIKS,
        GICS=GICS,
        OV=OV,
        rk=rk,
        tenk=tenk,
        eligible_filings=eligible_filings,
        missing_2023_filings=missing_2023_filings,
        not_inline_2024_filings=not_inline_2024_filings,
        candidate=candidate,
        pulse=pulse,
        FakeFilings=FakeFilings,
    )
