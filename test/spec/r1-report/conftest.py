"""Builders for the r1-report spec tests.

Everything is synthetic: case rows shaped like ``eval.eval_case_result`` rows and arm
mappings shaped as docs/specs/r1-report.md requirement 2 describes. The only fake is the
SELECT-only reader of requirement 11 (the database boundary). Fixtures are session-scoped
factories so hypothesis tests can use them.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

# Rational counts over small n; 1e-12 only absorbs float division.
TOL = 1e-12

EXCLUDED = "excluded: no single filing for this case"


def make_row(case_id: str, shape: str | None = "lookup", vc: bool | None = True,
             cw: bool | None = False, gp: float | None = None, cost: float | None = 0.0,
             latency: float | None = None, error: str | None = None,
             split: str | None = "dev", intent: str | None = None,
             tags: list[str] | None = None) -> dict[str, Any]:
    """One case row. ``tags`` replaces the generated tags when given."""
    if tags is None:
        tags = []
        if split is not None:
            tags.append(f"split:{split}")
        if intent is not None:
            tags.append(f"intent:{intent}")
        if shape is not None:
            tags.append(f"shape:{shape}")
    return {"case_id": case_id, "tags": list(tags), "value_correct": vc,
            "confident_wrong": cw, "grounding_precision": gp, "cost_usd": cost,
            "latency_ms": latency, "error": error}


def make_arm(label: str, rows: list[dict[str, Any]], split: str = "dev",
             suite: str = "xbrl_v2_dev", system: str | None = None,
             config: dict[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
    """One arm mapping."""
    return {"label": label, "run_id": run_id or f"eval-{label.lower()}000000",
            "system": system or label, "suite": suite, "split": split,
            "config": dict(config or {}), "cases": [dict(r) for r in rows]}


@pytest.fixture(scope="session")
def row():
    return make_row


@pytest.fixture(scope="session")
def arm():
    return make_arm


# The hand-built three-arm scenario of C3, C4, C5 and C7. X is the reference (XbrlRag),
# F the tools agent, O the oracle. Groups (requirement 1):
#   cross_filer  s1 s2 s3 s4 (screen.top), p1 (peer_relative)
#   cross_period t1 (trend), c1 (compute.cagr; also lookup_compute)
#   compound     k1 (compound.rank_by_ratio)
#   single-filer l1 l2 (lookup), g1 (compute.yoy_growth), d1 (dimensional)
SHAPES = {"s1": "screen.top", "s2": "screen.top", "s3": "screen.top", "s4": "screen.top",
          "p1": "peer_relative", "t1": "trend", "c1": "compute.cagr",
          "k1": "compound.rank_by_ratio", "l1": "lookup", "l2": "lookup",
          "g1": "compute.yoy_growth", "d1": "dimensional"}

X_CONFIG = {"split": "dev", "service_tier": "flex", "max_workers": 8,
            "kind_concurrency": {"narrative": 1}}
F_CONFIG = {"split": "dev", "service_tier": "standard", "max_workers": 4,
            "kind_concurrency": {}}
O_CONFIG: dict[str, Any] = {}


def scenario_arms() -> list[dict[str, Any]]:
    """The three arms of the hand-built scenario (see the table above)."""
    xc = 0.0625  # exact in binary
    x = [
        make_row("s1", SHAPES["s1"], vc=True, gp=1.0, cost=xc, latency=100.0),
        make_row("s2", SHAPES["s2"], vc=True, cost=xc, latency=200.0),
        make_row("s3", SHAPES["s3"], vc=True, cost=xc, latency=300.0),
        make_row("s4", SHAPES["s4"], vc=False, cw=True, cost=xc, latency=400.0),
        make_row("p1", SHAPES["p1"], vc=True, cost=xc, latency=500.0),
        make_row("t1", SHAPES["t1"], vc=True, cost=xc),
        make_row("c1", SHAPES["c1"], vc=None, cw=None, cost=xc, error="provider 500"),
        make_row("k1", SHAPES["k1"], vc=True, cost=xc),
        make_row("l1", SHAPES["l1"], vc=True, gp=1.0, cost=xc),
        make_row("l2", SHAPES["l2"], vc=True, gp=0.5, cost=xc),
        make_row("g1", SHAPES["g1"], vc=False, cw=True, gp=0.0, cost=xc),
        make_row("d1", SHAPES["d1"], vc=True, cost=xc),
    ]
    f = [  # no row for k1: missing
        make_row("s1", SHAPES["s1"], vc=False, cost=1.0, latency=1000.0),
        make_row("s2", SHAPES["s2"], vc=True, cost=1.0, latency=3000.0),
        make_row("s3", SHAPES["s3"], vc=False, cost=1.0),
        make_row("s4", SHAPES["s4"], vc=False, cost=1.0),
        make_row("p1", SHAPES["p1"], vc=False, cost=1.0),
        make_row("t1", SHAPES["t1"], vc=True, cost=1.0),
        make_row("c1", SHAPES["c1"], vc=True, cost=1.0),
        make_row("l1", SHAPES["l1"], vc=True, cost=1.0),
        make_row("l2", SHAPES["l2"], vc=False, cw=False, cost=1.0),  # hedged decline
        make_row("g1", SHAPES["g1"], vc=True, cost=1.0),
        make_row("d1", SHAPES["d1"], vc=False, cw=True, cost=1.0),
    ]
    o = [make_row(cid, SHAPES[cid], vc=None, cw=None, cost=0.0, latency=7.0, error=EXCLUDED)
         for cid in ("s1", "s2", "s3", "s4", "p1", "k1", "d1")]
    o += [
        make_row("t1", SHAPES["t1"], vc=False, cost=0.5, latency=50.0),
        make_row("c1", SHAPES["c1"], vc=True, cost=0.5),
        make_row("l1", SHAPES["l1"], vc=True, cost=0.5, latency=10.0),
        make_row("l2", SHAPES["l2"], vc=True, cost=0.5, latency=20.0),
        make_row("g1", SHAPES["g1"], vc=True, cost=0.5, latency=30.0),
    ]
    return [make_arm("X", x, system="xbrlrag_full", config=X_CONFIG),
            make_arm("F", f, system="frontier_sec_tools", config=F_CONFIG),
            make_arm("O", o, system="long_context_oracle", config=O_CONFIG)]


@pytest.fixture(scope="session")
def scenario():
    """Factory: a fresh deep copy of the scenario's arms."""

    def make() -> list[dict[str, Any]]:
        return copy.deepcopy(scenario_arms())

    return make


class FakeReader:
    """The SELECT-only reader of requirement 11, over in-memory rows."""

    def __init__(self, runs: dict[str, dict[str, Any]],
                 cases: dict[str, list[dict[str, Any]]]) -> None:
        self.runs = runs
        self.cases = cases

    def run_row(self, run_id: str) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        return copy.deepcopy(row) if row is not None else None

    def case_rows(self, run_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.cases.get(run_id, []))


@pytest.fixture(scope="session")
def fake_reader():
    return FakeReader
