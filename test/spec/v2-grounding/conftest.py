"""Builders for the v2-grounding spec tests.

Everything is synthetic: hand-built v2 cases (key inputs as the v2 suite writes them), fact
records in the fact store's own form (namespace-URI concepts, ``shares*USD`` per-share units,
SEC period ends, as ``PostgresFactStore`` returns them) and graded results. The only fake is the
in-memory ``FactStore`` (the database boundary). Fixtures are session-scoped factories so
hypothesis tests can use them.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import CaseGrade, Grade
from src.eval.core.report import CaseResult
from src.eval.sec_check.base import FactRecord, FactStore

US_GAAP = "http://fasb.org/us-gaap/2025"
DEI = "http://xbrl.sec.gov/dei/2025"


class FakeFactStore(FactStore):
    """An in-memory fact store that counts its reads."""

    def __init__(self, records: dict[int, FactRecord]) -> None:
        self.records = dict(records)
        self.calls: list[list[int]] = []

    def facts(self, fact_ids: list[int]) -> dict[int, FactRecord]:
        self.calls.append(list(fact_ids))
        return {int(i): dict(self.records[int(i)]) for i in fact_ids if int(i) in self.records}

    def metric_components(self, ticker: str, fiscal_year: int, metric: str) -> dict | None:
        return None


def rec(fact_id: int, cik: int, local: str, unit: str, start: str | None, end: str,
        value: float, accession: str = "0000000001-26-000001", *, ns: str = US_GAAP,
        dimensional: bool = False) -> dict[str, Any]:
    """A fact record in store form: the concept is ``<namespace URI>:<local>``."""
    return {
        "fact_id": fact_id, "ticker": "TKR", "cik": str(cik).zfill(10),
        "accession_no": accession, "concept": f"{ns}:{local}", "unit": unit,
        "period_start": start, "period_end": end, "value": float(value),
        "dimensional": dimensional,
    }


def inp(cik: int, concept: str, unit: str, start: str | None, end: str, value: float, *,
        decimals: Any = -6, accession: str = "0000000001-26-000001",
        selection: str = "latest_filed") -> dict[str, Any]:
    """A key input as the v2 suite writes it (10-digit CIK string, companyfacts concept)."""
    return {
        "accession": accession, "calendar_year": None, "cik": str(cik).zfill(10),
        "concept": concept, "decimals": decimals, "filed": "2026-01-23", "fiscal_year": 2025,
        "form": "10-K", "key_source": "sec_companyfacts", "period_end": end,
        "period_start": start, "policies": ["S1", "S2", "S6"], "selection": selection,
        "status": "verified", "unit": unit, "value": value, "verified_by": ["fact_store"],
    }


def case(case_id: str, inputs: list[dict], *, shape: str = "lookup", kind: str = "lookup",
         cik: int | None = None, alternates: list[dict] | None = None,
         alt_readings: list[dict] | None = None, rows: list[dict] | None = None,
         expected_fact_ids: list[int] | None = None, key: Any = "default",
         expected_value: float | None = 1.0) -> EvalCase:
    """A v2 case with the given key inputs."""
    d: dict[str, Any] = {
        "id": case_id, "question": f"question {case_id}", "kind": kind,
        "tags": ["split:dev", f"shape:{shape}"], "rel_tol": 1e-4,
    }
    if expected_value is not None and rows is None:
        d["expected_value"] = expected_value
    if cik is not None:
        d["cik"] = cik
    if key == "default":
        k: dict[str, Any] = {"definition": None, "disagreements": [], "inputs": inputs,
                             "policies": ["S1"], "same_filing": None,
                             "source": "sec_companyfacts", "status": "verified"}
        if alternates is not None:
            k["alternates"] = alternates
        d["key"] = k
    elif key is not None:
        d["key"] = key
    if alt_readings is not None:
        d["alt_readings"] = alt_readings
    if rows is not None:
        d["expected_rows"] = rows
    if expected_fact_ids:
        d["expected_fact_ids"] = expected_fact_ids
    return EvalCase.from_dict(d)


def result(c: EvalCase, fact_ids: list[int], *, grade: Grade = Grade.CORRECT,
           precision: float | None = None, recall: float | None = None) -> CaseResult:
    """A graded result whose ``produced`` carries the answer's fact ids."""
    g = CaseGrade(
        case_id=c.id, grade=grade, passed=grade is Grade.CORRECT, reason="hand-built",
        expected={}, produced={"text": "t", "numeric_values": [1.0], "rows": [],
                               "fact_ids": list(fact_ids), "chunk_ids": [],
                               "refused": False, "hedged": False, "error": None},
        has_citation=bool(fact_ids),
    )
    g.grounding_precision = precision
    g.grounding_recall = recall
    return CaseResult(case=c, grade=g)


@pytest.fixture(scope="session")
def gb() -> types.SimpleNamespace:
    """The builders, for tests (including hypothesis tests)."""
    return types.SimpleNamespace(rec=rec, inp=inp, case=case, result=result,
                                 FakeFactStore=FakeFactStore, US_GAAP=US_GAAP, DEI=DEI)
