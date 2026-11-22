"""Fakes and builders for the heldout-draw-v2 spec tests.

Spec: docs/specs/heldout-draw-v2.md. The only fake sits at the I/O seam the spec names:
``FakeFilings``, a ``FilingIndexSource`` returning synthetic filing-index records per CIK
and recording every call. ``rk2`` recomputes the requirement-4 ranking key with ``hashlib``
in the test, and ``rk1`` the A' key (aprime-draw requirement 5) for the independence check.
Everything is exposed through one session-scoped ``kit`` fixture so hypothesis tests can use
it. All values are synthetic, except the wave and left-out symbols (v2 plan section 4.2 and
4.4) and the five pre-registration v0 P CIKs; no network, no data directory, no current date.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.aprime.base import FilingIndexSource

PREFIX_V2 = "xbrlrag-heldout-v2|"
PREFIX_V1 = "xbrlrag-aprime-v1|"

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED_LISTS = REPO_ROOT / "configs" / "eval" / "v2" / "heldout_exclusions.yaml"

# v2 plan section 4.2 (waves) and 4.4 (left-outs no excluded sector covers), as quoted in
# requirement 2 of the spec.
WAVE1 = ["CSCO", "ADBE", "CRM", "QCOM", "TXN", "NFLX", "KO", "PG", "WMT", "COST", "HD",
         "MCD", "LLY", "MRK", "ABT", "UNP", "LMT", "UPS"]
WAVE2 = ["XOM", "V", "TMUS", "JNJ", "PEP", "BA", "CAT", "DIS"]
LEFT_OUT = ["UNH", "CVS", "GE", "GEV", "MMM", "SNDK"]

# The five P filers of pre-registration v0 section 3 (aprime-draw requirement 4).
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
OV = "fedcba9876543210" * 8


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def rk2(output_value: str, cik: str) -> str:
    """Requirement 4, recomputed in the test: sha256 of v2 prefix | OV upper | cik10."""
    return _sha(PREFIX_V2 + output_value.upper() + "|" + str(cik).zfill(10))


def rk1(output_value: str, cik: str) -> str:
    """The A' key (aprime-draw requirement 5), for the independence check."""
    return _sha(PREFIX_V1 + output_value.upper() + "|" + str(cik).zfill(10))


def tenk(report_date: str, *, inline: bool = True, acc: str = "0000000000-00-000000"
         ) -> dict[str, Any]:
    """One 10-K filing-index record."""
    return {"form": "10-K", "filing_date": "2026-03-01", "report_date": report_date,
            "accession_no": acc, "is_inline_xbrl": inline}


def eligible_filings() -> list[dict[str, Any]]:
    """Inline 10-Ks for fiscal 2023, 2024 and 2025 (year-end September 30)."""
    return [tenk("2023-09-30", acc="a-23"), tenk("2024-09-30", acc="a-24"),
            tenk("2025-09-30", acc="a-25")]


def missing_2023_filings() -> list[dict[str, Any]]:
    """Inline 10-Ks for 2024 and 2025 only."""
    return [tenk("2024-09-30", acc="b-24"), tenk("2025-09-30", acc="b-25")]


def not_inline_2024_filings() -> list[dict[str, Any]]:
    """Three 10-Ks, the 2024 one not inline."""
    return [tenk("2023-09-30", acc="c-23"), tenk("2024-09-30", inline=False, acc="c-24"),
            tenk("2025-09-30", acc="c-25")]


def candidate(cik: str, stratum: str) -> dict[str, Any]:
    """One pool candidate (requirement 3's shape)."""
    sector = "Communication Services" if stratum == "S" else "Consumer Staples"
    sym = f"H{cik[-5:]}"
    return {"symbol": sym, "name": f"Synthetic {sym} Inc", "sector": sector, "cik": cik,
            "stratum": stratum}


def pulse(output_value: str = OV, stamp: str = "2027-01-04T16:00:00.000Z") -> dict[str, Any]:
    """A NIST Beacon 2.0 ``pulse`` object with the members the spec reads."""
    return {"uri": "https://beacon.example.invalid/pulse/2", "version": "Version 2.0",
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
    """The builders, constants and the fake, as one namespace."""
    return SimpleNamespace(
        PREFIX_V2=PREFIX_V2,
        PREFIX_V1=PREFIX_V1,
        COMMITTED_LISTS=COMMITTED_LISTS,
        WAVE1=WAVE1,
        WAVE2=WAVE2,
        LEFT_OUT=LEFT_OUT,
        P_CIKS=P_CIKS,
        GICS=GICS,
        OV=OV,
        rk2=rk2,
        rk1=rk1,
        tenk=tenk,
        eligible_filings=eligible_filings,
        missing_2023_filings=missing_2023_filings,
        not_inline_2024_filings=not_inline_2024_filings,
        candidate=candidate,
        pulse=pulse,
        FakeFilings=FakeFilings,
    )
