"""Fixture loaders and the fake SEC source for the sec-keys spec tests.

Spec: docs/specs/sec-keys.md. The only fake is ``FakeSecSource``, at the ``SecSource``
seam; it reads the committed synthetic payloads in ``fixtures/`` (SEC API shapes, values
chosen by hand, made-up CIKs). Everything is exposed through one session-scoped ``kit``
fixture so hypothesis tests can use it. No network, no data directory, no current date.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.eval.keys.base import SecSource

FIXTURES = Path(__file__).parent / "fixtures"

SEP_CIK = "0000900001"  # September 52/53-week filer: three 10-Ks, a 10-K/A, a 10-Q
JUN_CIK = "0000900002"  # June year-end filer
TWD_CIK = "0000900003"  # December 20-F filer reporting in TWD
CAL_CIK = "0000900004"  # December filer present only in the frames

# Accessions of the September filer.
A22 = "0000900001-22-000010"  # FY2022 10-K, filed 2022-10-28
A23 = "0000900001-23-000020"  # FY2023 10-K, filed 2023-11-03
A24 = "0000900001-24-000030"  # FY2024 10-K, filed 2024-11-01
A24A = "0000900001-24-000031"  # FY2024 10-K/A, filed 2024-11-01
Q25 = "0000900001-25-000005"  # Q1 FY2025 10-Q, filed 2025-01-31
# Accessions of the June filer.
J24 = "0000900002-24-000040"  # FY2024 10-K, filed 2024-07-30
J25 = "0000900002-25-000050"  # FY2025 10-K, filed 2025-07-30
# The TWD filer's 20-F.
T25 = "0000900003-25-000002"  # FY2024 20-F, filed 2025-04-15

# September filer periods, (start, end).
SEP_FY21 = ("2020-09-27", "2021-09-25")
SEP_FY22 = ("2021-09-26", "2022-09-24")
SEP_FY23 = ("2022-09-25", "2023-09-30")
SEP_FY24 = ("2023-10-01", "2024-09-28")

KEY_FIELDS = {
    "value", "unit", "decimals", "concept", "cik", "period_start", "period_end",
    "fiscal_year", "calendar_year", "accession", "filed", "form", "key_source",
    "selection", "policies", "status", "verified_by",
}


def load(name: str) -> dict:
    """A fresh parsed copy of one committed fixture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def companyfacts(cik: str) -> dict:
    """The companyfacts fixture of one filer."""
    return load(f"companyfacts_CIK{cik}.json")


def revenue_frame() -> dict:
    """The CY2024 us-gaap Revenues USD frame."""
    return load("frames_us-gaap_Revenues_USD_CY2024.json")


def assets_frame() -> dict:
    """The CY2024Q4I us-gaap Assets USD frame."""
    return load("frames_us-gaap_Assets_USD_CY2024Q4I.json")


def units_of(doc: dict, taxonomy: str, tag: str) -> dict:
    """The ``units`` mapping of one concept in a companyfacts document."""
    return doc["facts"][taxonomy][tag]["units"]


class FakeSecSource(SecSource):
    """Serves the fixture payloads; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.frames_docs = {
            ("us-gaap", "Revenues", "USD", "CY2024"): revenue_frame(),
            ("us-gaap", "Assets", "USD", "CY2024Q4I"): assets_frame(),
        }

    def company_facts(self, cik: str) -> dict:
        self.calls.append(("company_facts", cik))
        return companyfacts(str(cik).zfill(10))

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        self.calls.append(("frames", taxonomy, tag, unit, period))
        doc = self.frames_docs.get((taxonomy, tag, unit, period))
        if doc is None:  # any other period: an empty frame with the requested labels
            doc = {"taxonomy": taxonomy, "tag": tag, "ccp": period, "uom": unit,
                   "label": tag, "description": "", "pts": 0, "data": []}
        return copy.deepcopy(doc)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The loaders, constants and fake, as one namespace."""
    return SimpleNamespace(
        load=load, companyfacts=companyfacts, revenue_frame=revenue_frame,
        assets_frame=assets_frame, units_of=units_of, FakeSecSource=FakeSecSource,
        SEP_CIK=SEP_CIK, JUN_CIK=JUN_CIK, TWD_CIK=TWD_CIK, CAL_CIK=CAL_CIK,
        A22=A22, A23=A23, A24=A24, A24A=A24A, Q25=Q25, J24=J24, J25=J25, T25=T25,
        SEP_FY21=SEP_FY21, SEP_FY22=SEP_FY22, SEP_FY23=SEP_FY23, SEP_FY24=SEP_FY24,
        KEY_FIELDS=KEY_FIELDS,
    )
