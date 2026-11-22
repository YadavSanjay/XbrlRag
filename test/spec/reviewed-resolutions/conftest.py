"""Fixtures for the reviewed-resolutions spec tests.

Spec: docs/specs/reviewed-resolutions.md. Everything here is constructed (Notes,
"Fixtures"): the resolution list ``fixtures/reviewed.yaml``, the witnesses and the FSDS
``pre`` rows below. The original list is key-statement-witness's locked file, read as
committed. No corpus filing, no network, no database.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]
LIVE = REPO / "configs" / "eval" / "v2"
ORIGINAL = REPO / "test" / "spec" / "key-statement-witness" / "fixtures" / "key_resolutions.yaml"
REVIEWED = FIXTURES / "reviewed.yaml"
# The locked reviewer allow-list (requirement 1) and its live copy.
REVIEWERS = FIXTURES / "reviewers.yaml"
LIVE_REVIEWERS = LIVE / "reviewers.yaml"
# An FSDS quarter directory whose four tables hold a header only (C4 needs a readable one).
EMPTY_QUARTER = FIXTURES / "fsds_empty"

ORCL_ADSH = "0000950170-25-087926"
AAA = "0000900201-26-000001"
BBB = "0000900202-26-000001"
CCC = "0000900203-26-000001"
EEE = "0000900205-26-000001"
BUCKETS = ("rule_gap", "combined_line", "definition", "note_unlisted", "by_design")


def _pre(adsh: str, report: int, line: int, stmt: str, tag: str, plabel: str) -> dict[str, str]:
    return {"adsh": adsh, "report": str(report), "line": str(line), "stmt": stmt, "inpth": "0",
            "rfile": "H", "tag": tag, "version": "us-gaap/2025", "plabel": plabel,
            "negating": "0"}


# The FSDS pre rows of the fictitious filings (spec Notes, the evidence column).
PRE = [
    _pre(AAA, 2, 13, "BS", "AccruedLiabilitiesCurrent", "Accrued compensation"),
    _pre(AAA, 2, 14, "BS", "AccountsPayableAndAccruedLiabilitiesCurrent",
         "Accounts payable and accrued expenses"),
    _pre(AAA, 2, 15, "BS", "LiabilitiesCurrent", "Total current liabilities"),
    _pre(AAA, 1, 3, "CP", "EntityRegistrantName", "Entity Registrant Name"),
    _pre(BBB, 6, 21, "CF", "ProceedsFromIssuanceOfLongTermDebt", "Proceeds from senior notes"),
    _pre(BBB, 6, 22, "CF", "RepaymentsOfLongTermDebt",
         "Repayments of senior notes and term loan credit agreements"),
    _pre(EEE, 4, 29, "BS", "LongTermDebtCurrent", "Current portion of long-term debt"),
    _pre(EEE, 4, 30, "BS", "LongTermDebtNoncurrent", "Long-term debt"),
]


def tables() -> dict[str, list[dict[str, str]]]:
    """The constructed FSDS tables (``pre`` rows only; no value is read)."""
    return {"sub": [], "pre": copy.deepcopy(PRE), "num": [], "tag": []}


def witness(family: str, cik: str, fy: int, concept: str, value: float, adsh: str,
            status: str) -> dict[str, Any]:
    """A witness record of key-statement-witness requirement 1 (resolution not yet set)."""
    return {"status": status, "family": family, "cik": cik, "fiscal_year": fy, "adsh": adsh,
            "lines": [], "value": None, "key_concept": concept, "key_value": float(value),
            "key_accession": adsh, "substitution": None, "reason": None, "resolution": None}


# One blocking witness per entry of reviewed.yaml (spec Notes, the Witness column).
BLOCKED = [
    witness("DeferredRevenue", "0001341439", 2024, "us-gaap:ContractWithCustomerLiability",
            10_546_000_000, ORCL_ADSH, "disagree"),
    witness("AccountsPayable", "0000900201", 2025,
            "us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent", 1500, AAA, "rejected_line"),
    witness("DebtRepaid", "0000900202", 2024, "us-gaap:RepaymentsOfLongTermDebt", 750, BBB,
            "rejected_line"),
    witness("InterestIncome", "0000900203", 2025, "us-gaap:InvestmentIncomeInterest", 120, CCC,
            "note_unlisted"),
    witness("Debt", "0000900205", 2025, "us-gaap:LongTermDebt", 2000, EEE, "disagree"),
]
# Accepted witnesses of the same filers; no resolution ever covers them.
ACCEPTED = [
    witness("Revenue", "0000900201", 2025, "us-gaap:Revenues", 9000, AAA, "agree"),
    witness("Revenue", "0000900205", 2025, "us-gaap:Revenues", 7000, EEE, "agree"),
    witness("NetIncome", "0000900202", 2024, "us-gaap:NetIncomeLoss", 300, BBB, "agree"),
]


def raw_entries() -> list[dict[str, Any]]:
    """The entries of reviewed.yaml as YAML gives them (AAA's date a datetime.date)."""
    return yaml.safe_load(REVIEWED.read_text(encoding="utf-8"))["resolutions"]


def loaded_entries() -> list[dict[str, Any]]:
    """The entries as load_resolutions must return them, normalized by hand (requirement 1):
    every review date is the ISO string "2026-11-10"."""
    out = raw_entries()
    for e in out:
        if "review" in e:
            e["review"]["date"] = "2026-11-10"
    return out


def statement_review() -> dict[str, Any]:
    """A complete statement review record, as a YAML document would give it."""
    return {"by": "Sanjay", "date": dt.date(2026, 11, 10), "bucket": "combined_line",
            "evidence": {"kind": "statement", "adsh": AAA, "report": 2, "line": 14,
                         "plabel": "Accounts payable and accrued expenses"}}


def note_review() -> dict[str, Any]:
    """A complete note review record."""
    return {"by": "Sanjay", "date": "2026-11-10", "bucket": "note_unlisted",
            "evidence": {"kind": "note", "adsh": CCC, "label": "Interest income"}}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """Paths and builders shared by the tests (each builder returns fresh copies)."""
    return SimpleNamespace(
        live=LIVE, original=ORIGINAL, reviewed=REVIEWED, buckets=BUCKETS,
        empty_quarter=EMPTY_QUARTER, reviewers=REVIEWERS, live_reviewers=LIVE_REVIEWERS,
        aaa=AAA, bbb=BBB, ccc=CCC, eee=EEE, orcl=ORCL_ADSH,
        tables=tables, witness=witness, raw_entries=raw_entries,
        loaded_entries=loaded_entries, statement_review=statement_review,
        note_review=note_review,
        blocked=lambda: copy.deepcopy(BLOCKED), accepted=lambda: copy.deepcopy(ACCEPTED),
    )
