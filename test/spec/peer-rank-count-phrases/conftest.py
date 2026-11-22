"""Builders for the peer-rank-count-phrases spec tests (docs/specs/peer-rank-count-phrases.md).

- ``MCD_TEXT``: the opening sentence of a stored dev frontier answer (case
  ``int-ddc1ca21791a-042123a6``, run ``eval-e9fa0e0df9b7``), followed by the subject's stated
  rank, shortened to that clause.
- ``mcd_case``: a fictional peer case built for these tests: subject MCD at rank 17 of 33
  covered companies, the 32 peers ``PEERA`` ... named by letters. Its values are synthetic.

Everything is reached through the session-scoped ``kit`` fixture, so hypothesis tests can use
it. Grading is pure, so nothing is faked.
"""

from __future__ import annotations

import string
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

MCD_TEXT = ("I ranked 32 of the 33 covered companies on fiscal 2025 comprehensive income "
            "attributable to the parent. McDonald's is 17th.")

# 32 fictional peers (PEERA ... PEERZ, PEERAA ... PEERAF) and MCD in 17th place by value.
PEERS = [f"PEER{c}" for c in string.ascii_uppercase] + [f"PEERA{c}" for c in "ABCDEF"]
RANKED = PEERS[:16] + ["MCD"] + PEERS[16:]


def mcd_case() -> EvalCase:
    n = len(RANKED)
    rows: list[dict[str, Any]] = [
        {"ticker": t, "value": float((n - i) * 1_000_000_000), "fiscal_year": 2025,
         "period_end": "2025-12-31"} for i, t in enumerate(RANKED)]
    return EvalCase.from_dict({
        "id": "v2-peer-count-phrase", "suite": "xbrl_v2_dev", "kind": "screen",
        "origin": "v2:peer_relative", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": "Where does McDonald's rank among the covered companies?",
        "tags": ["split:dev", "shape:peer_relative"],
        "expected_rows": rows,
        "disclosures": {"universe": "covered companies", "members": n, "ranked": list(RANKED),
                        "excluded": [], "period_ends": {t: "2025-12-31" for t in RANKED},
                        "disclose_period_ends": False},
        "subject": {"ticker": "MCD", "rank": 17, "universe_median": 17e9, "position": "at"},
    })


def answer(text: str, fact_ids: list[int] | None = None) -> SystemAnswer:
    return SystemAnswer(text=text, numeric_values=[], rows=[], fact_ids=list(fact_ids or []),
                        refused=False, hedged=False)


class Kit:
    MCD_TEXT = MCD_TEXT
    RANKED = RANKED
    mcd_case = staticmethod(mcd_case)
    answer = staticmethod(answer)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
