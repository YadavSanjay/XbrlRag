"""Builders for the grader-synthesis-error spec tests (docs/specs/grader-synthesis-error.md).

Every case is a fictional record in the shape the v2 builder emits (tickers ``DEVA`` ...
``DEVZ``, round amounts, the "retailers" group: five ranked members and two not ranked);
every answer is a hand-built SystemAnswer carrying a citation, so only the rule under test
decides the grade.

- ``E`` / ``R``: the synthesizer's two fixed fallback summaries (requirement 1), copied from
  ``XbrlRagSynthesizer._degraded_response``.
- ``universe_sentence`` / ``not_ranked_sentence``: the disclosure sentences
  ``screen_disclosure_text`` writes for the retailers group, typed by hand.
- ``META_TEXT``: the stored dev xbrlrag_full answer to int-ead777fd7df3-00cd2cd1, shortened
  (the Not ranked list cut to three entries, the period ends to three).
- ``BUILDERS``: one ``(case, answer)`` factory per case kind and sub-kind; the answer is one the
  grader marks right when no fallback text is present.

Reached through the session-scoped ``kit`` fixture, so hypothesis tests can use it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

M = 1e6

E = ("This answer could not be completed: the synthesis step returned a response that was not "
     "valid JSON. The retrieved facts are preserved in execution_result; please retry the "
     "query.")
R = ("This answer could not be completed: the model declined to answer the synthesis step. "
     "The retrieved facts are preserved in execution_result.")

META_TEXT = (
    E + " Universe: the covered companies (33 companies; 14 ranked for FY2022). Not ranked: "
    "ABT (metric not reported); TSM (reports in TWD, not USD); WMT (metric not reported). "
    "Fiscal years end on different dates: AMZN 2022-12-31, MSFT 2022-06-30, "
    "ORCL 2022-05-31."
)

# Ranked members of the retailers group, in key order; two members not ranked.
MEMBERS = [("DEVA", 500 * M), ("DEVB", 400 * M), ("DEVS", 300 * M), ("DEVC", 200 * M),
           ("DEVD", 100 * M)]
EXCLUDED = [{"ticker": "DEVX", "reason": "metric_not_tagged"},
            {"ticker": "DEVY", "reason": "no_fsds"}]
NAMED = "Among the retailers: DEVA, DEVB, DEVS, DEVC, DEVD; DEVX and DEVY were not ranked."


def universe_sentence(members: int = 7, ranked: int = 5, fy: int = 2025) -> str:
    """The U sentence for the retailers group."""
    return f"Universe: the retailers group ({members} companies; {ranked} ranked for FY{fy})."


def not_ranked_sentence() -> str:
    """The N sentence for ``EXCLUDED``."""
    return "Not ranked: DEVX (metric not reported); DEVY (no fsds)."


def disclosure_text() -> str:
    """U then N, one space apart, as the pipeline appends them."""
    return f"{universe_sentence()} {not_ranked_sentence()}"


def _tags(shape: str) -> list[str]:
    return ["split:dev", "intent:int-gse", "family:formal", "key_source:sec_companyfacts",
            "key_status:verified", f"shape:{shape}"]


def _key(values: list[float]) -> dict[str, Any]:
    return {"definition": None, "disagreements": [],
            "inputs": [{"accession": "0000000000-25-000001", "fiscal_year": 2025,
                        "form": "10-K", "period_end": "2025-12-31", "unit": "USD",
                        "value": v} for v in values],
            "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
            "source": "sec_companyfacts", "status": "verified"}


def _disclosures() -> dict[str, Any]:
    ranked = [t for t, _ in MEMBERS]
    return {"universe": "retailers", "members": len(ranked) + len(EXCLUDED),
            "ranked": ranked, "excluded": [dict(e) for e in EXCLUDED],
            "period_ends": {t: "2025-12-31" for t in ranked}, "disclose_period_ends": False}


def _row(ticker: str, value: float) -> dict[str, Any]:
    return {"ticker": ticker, "value": value, "fiscal_year": 2025, "period_end": "2025-12-31"}


def _record(shape: str, kind: str, **fields: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": f"v2-gse-{shape}", "suite": "xbrl_v2_dev", "kind": kind,
        "origin": f"v2:{shape}", "fy": 2025, "unit": "USD", "rel_tol": 1e-4,
        "question": f"A {shape} question about the retailers?", "tags": _tags(shape)}
    base.update(fields)
    return EvalCase.from_dict(base)


def answer(text: str, values: list[float] | None = None,
           rows: list[tuple[str, float]] | None = None, *, refused: bool = False,
           hedged: bool = False) -> SystemAnswer:
    """A cited answer; ``values`` default to the rows' values."""
    row_dicts = [{"ticker": t, "value": float(v)} for t, v in rows or []]
    nums = [float(v) for v in values] if values is not None else [r["value"] for r in row_dicts]
    return SystemAnswer(text=text, numeric_values=nums, rows=row_dicts, fact_ids=[1],
                        refused=refused, hedged=hedged)


def lookup_case() -> tuple[EvalCase, SystemAnswer]:
    case = _record("lookup", "lookup", ticker="DEVA", expected_value=500 * M,
                   concept="us-gaap:Revenues")
    return case, answer("DEVA's revenue in fiscal 2025 was $500 million.", [500 * M])


def ratio_case() -> tuple[EvalCase, SystemAnswer]:
    case = _record("compute.ratio", "compute", ticker="DEVA", expected_value=0.25,
                   alt_values=[25.0])
    return case, answer("DEVA's ratio of R&D to revenue in fiscal 2025 was 0.25.", [0.25])


def sum_case() -> tuple[EvalCase, SystemAnswer]:
    case = _record("screen.sum", "compute", expected_value=1500 * M,
                   disclosures=_disclosures(), key=_key([v for _, v in MEMBERS]))
    return case, answer(f"{NAMED} The total is $1,500 million.", [1500 * M])


def median_case() -> tuple[EvalCase, SystemAnswer]:
    case = _record("screen.median", "compute", expected_value=300 * M,
                   disclosures=_disclosures(), key=_key([v for _, v in MEMBERS]))
    return case, answer(f"{NAMED} The median is $300 million.", [300 * M])


def top_case() -> tuple[EvalCase, SystemAnswer]:
    case = _record("screen.top", "screen", top_n=3,
                   expected_rows=[_row(t, v) for t, v in MEMBERS[:3]],
                   disclosures=_disclosures(), key=_key([v for _, v in MEMBERS]))
    return case, answer(f"{NAMED} The top 3 are DEVA, DEVB and DEVS.", rows=MEMBERS[:3])


def peer_case(subject: str = "DEVS", rank: int = 3) -> EvalCase:
    return _record("peer_relative", "screen",
                   question=f"Where does {subject} rank among the retailers?",
                   expected_rows=[_row(t, v) for t, v in MEMBERS],
                   disclosures=_disclosures(),
                   subject={"ticker": subject, "rank": rank, "universe_median": 300 * M,
                            "position": "at"},
                   key=_key([v for _, v in MEMBERS]))


def peer_pair() -> tuple[EvalCase, SystemAnswer]:
    return peer_case(), answer(f"{NAMED} DEVS ranked 3rd.", values=[])


def trend_case() -> tuple[EvalCase, SystemAnswer]:
    rows = [{"entity": f"FY{y}", "fiscal_year": y, "period_end": f"{y}-12-31", "value": v}
            for y, v in ((2023, 100 * M), (2024, 200 * M), (2025, 300 * M))]
    case = _record("trend", "breakdown", ticker="DEVA", expected_rows=rows,
                   order_sensitive=False, concept="us-gaap:Revenues")
    text = ("DEVA's revenue was $100 million in fiscal 2023, $200 million in fiscal 2024 and "
            "$300 million in fiscal 2025.")
    return case, answer(text, [100 * M, 200 * M, 300 * M])


BUILDERS = {
    "lookup": lookup_case,
    "ratio": ratio_case,
    "sum": sum_case,
    "median": median_case,
    "screen.top": top_case,
    "peer_relative": peer_pair,
    "trend": trend_case,
}


class Kit:
    M = M
    E = E
    R = R
    META_TEXT = META_TEXT
    MEMBERS = MEMBERS
    NAMED = NAMED
    BUILDERS = BUILDERS
    answer = staticmethod(answer)
    peer_case = staticmethod(peer_case)
    lookup_case = staticmethod(lookup_case)
    universe_sentence = staticmethod(universe_sentence)
    not_ranked_sentence = staticmethod(not_ranked_sentence)
    disclosure_text = staticmethod(disclosure_text)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
