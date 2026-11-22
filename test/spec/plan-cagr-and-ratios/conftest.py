"""Fixtures for plan-cagr-and-ratios (docs/specs/plan-cagr-and-ratios.md).

One I/O boundary is faked: the fact store under `PlanExecutor` (`XbrlFactstoreLookup.lookup`),
which answers each lookup step with a given `FactLookupResult`. The executor, the CAGR and
the calculator are the product's own. Every figure is synthetic (Notes: Example Co).
"""

from __future__ import annotations

import types

import pytest

from src.retriever.xbrl.plan_executor import PlanExecutor
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactLookupResult,
    LookupStatus,
)

CIK = "0000000001"  # Example Co, synthetic

# Example Co revenue (Notes): each year 10% above the last.
REVENUE = {2022: 80_000_000_000.0, 2023: 88_000_000_000.0, 2024: 96_800_000_000.0,
           2025: 106_480_000_000.0}


class ResultStore:
    """The fact store at its boundary: a given result per step id."""

    def __init__(self, answers: dict):
        self.answers = answers  # step id -> (LookupStatus, value, unit, fact_id)

    def lookup(self, step: dict) -> FactLookupResult:
        status, value, unit, fid = self.answers[step["id"]]
        return FactLookupResult(
            step_id=step["id"], status=status, value=value, unit=unit,
            citation=Citation(fact_id=fid) if fid is not None else None, confidence=0.9)


def lookup_step(sid, fy, family="Revenue", cik=CIK, **extra):
    step = {"id": sid, "op": "lookup", "cik": cik, "fy": fy, "concept_family": family,
            "dimensions": "NONE", "section_hint": "Income Statement"}
    step.update(extra)
    return step


def execute(answers, steps):
    return PlanExecutor(factstore_lookup=ResultStore(answers)).execute({"steps": steps})


def steps_by_id(result):
    return {s["step_id"]: s for s in result.to_dict()["steps"]}


@pytest.fixture(scope="session")
def fx():
    """Everything the tests share, as one namespace (safe with hypothesis)."""
    return types.SimpleNamespace(
        CIK=CIK, REVENUE=REVENUE, OK=LookupStatus.OK, AMB=LookupStatus.AMBIGUOUS,
        NF=LookupStatus.NOT_FOUND, lookup_step=lookup_step, execute=execute,
        steps_by_id=steps_by_id,
    )
