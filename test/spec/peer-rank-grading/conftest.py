"""Builders for the peer-rank-grading spec tests (docs/specs/peer-rank-grading.md).

- ``WMT_CASE``: the real dev case ``int-f4d823788172-00694060`` (Walmart accounts payable,
  FY2023, among the retailers), copied from the frozen dev suite with its key inputs
  trimmed. Values are SEC companyfacts amounts (public domain). By value: AMZN 84.981bn,
  WMT 53.742bn, COST 17.483bn, HD 10.037bn, so WMT's key rank is 2.
- ``WMT_TEXT``: the summary of the stored xbrlrag_full answer to it (dev run
  ``eval-eefe3508621a``), with the period ends appended so that every disclosure check
  passes and only the rank decides.
- ``dev_case``: a fictional peer case (DEVA ... DEVD, names given by ``subject["names"]``)
  for metamorphic renaming.

Everything is reached through the session-scoped ``kit`` fixture, so hypothesis tests can
use it.

Answers are hand-built SystemAnswers. Grading is pure, so nothing is faked.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

BN = 1e9

WMT_CASE: dict[str, Any] = {
    "cik": 104169,
    "disclosures": {
        "disclose_period_ends": True, "excluded": [], "members": 4,
        "period_ends": {"AMZN": "2023-12-31", "COST": "2023-09-03", "HD": "2024-01-28",
                        "WMT": "2023-01-31"},
        "ranked": ["AMZN", "WMT", "COST", "HD"], "universe": "retailers"},
    "expected_rows": [
        {"fiscal_year": 2023, "period_end": "2023-12-31", "ticker": "AMZN",
         "value": 84981000000},
        {"fiscal_year": 2023, "period_end": "2023-01-31", "ticker": "WMT",
         "value": 53742000000},
        {"fiscal_year": 2023, "period_end": "2023-09-03", "ticker": "COST",
         "value": 17483000000},
        {"fiscal_year": 2023, "period_end": "2024-01-28", "ticker": "HD",
         "value": 10037000000}],
    "fy": 2023, "id": "int-f4d823788172-00694060",
    "key": {"definition": None, "disagreements": [], "inputs": [],
            "policies": ["S1", "S2", "S3", "S5", "S6"], "same_filing": None,
            "source": "sec_companyfacts", "status": "verified"},
    "kind": "screen", "origin": "v2:peer_relative", "paraphrase_group": "int-f4d823788172",
    "question": "Please compare Walmart's AP for fiscal 2023 with the median of the "
                "retailers and state its rank.",
    "rel_tol": 0.0001,
    "subject": {"position": "above", "rank": 2, "ticker": "WMT",
                "universe_median": 35612500000.0},
    "suite": "xbrl_v2_dev",
    "tags": ["split:dev", "intent:int-f4d823788172", "family:formal",
             "template:peer-formal-1", "key_source:sec_companyfacts", "key_status:verified",
             "shape:peer_relative"],
    "ticker": "WMT", "unit": "USD",
}

PERIOD_ENDS = ("Period ends: AMZN 2023-12-31, WMT 2023-01-31, COST 2023-09-03, "
               "HD 2024-01-28.")
WMT_TEXT = (
    "Walmart's accounts payable for fiscal 2023 was 53742000000.0 USD, which ranks 2nd of "
    "the 4 retailers in the screen. The screen ranks Amazon first, Walmart second, Costco "
    "third and Home Depot fourth. The median of the retailers' accounts payable was not "
    "computed by any step, so the comparison with the median cannot be stated. "
    + PERIOD_ENDS
)

# The four rows by ticker, with their real values.
WMT_ROWS = {
    "AMZN": {"ticker": "AMZN", "value": 84981000000.0},
    "WMT": {"ticker": "WMT", "value": 53742000000.0},
    "COST": {"ticker": "COST", "value": 17483000000.0},
    "HD": {"ticker": "HD", "value": 10037000000.0},
}
STATEMENT_ORDER = ["WMT", "AMZN", "COST", "HD"]  # how answer_rows lists them: subject first
ALPHABETICAL = ["AMZN", "COST", "HD", "WMT"]


def rows_in(order: list[str]) -> list[dict]:
    return [dict(WMT_ROWS[t]) for t in order]


def wmt_case(**overrides: Any) -> EvalCase:
    d = copy.deepcopy(WMT_CASE)
    d.update(overrides)
    return EvalCase.from_dict(d)


def answer(text: str = "", rows: list[dict] | None = None, values: list[float] | None = None,
           fact_ids: list[int] | None = None, refused: bool = False,
           hedged: bool = False) -> SystemAnswer:
    return SystemAnswer(text=text, numeric_values=list(values or []),
                        rows=list(rows or []), fact_ids=list(fact_ids or []),
                        refused=refused, hedged=hedged)


# A fictional peer case: DEVA 300bn, DEVB 200bn (the subject, rank 2), DEVC 150bn,
# DEVD 50bn; universe "peers" of 4, none excluded, period ends not required. Each ticker's
# one name is "<Ticker> Holdings" (DEVB -> "Devb Holdings").
DEV_TICKERS = ["DEVA", "DEVB", "DEVC", "DEVD"]
DEV_VALUES = {"DEVA": 300 * BN, "DEVB": 200 * BN, "DEVC": 150 * BN, "DEVD": 50 * BN}


def dev_name(ticker: str) -> str:
    return f"{ticker.capitalize()} Holdings"


def dev_case(rename: dict[str, str] | None = None) -> EvalCase:
    """The fictional case, every ticker renamed through ``rename`` (old -> new)."""
    rename = rename or {t: t for t in DEV_TICKERS}
    return EvalCase.from_dict({
        "id": "v2-peer-dev", "question": "Where does the subject rank among its peers?",
        "suite": "xbrl_v2_dev", "kind": "screen", "origin": "v2:peer_relative",
        "fy": 2024, "unit": "USD", "rel_tol": 1e-4,
        "tags": ["split:dev", "shape:peer_relative"],
        "expected_rows": [{"ticker": rename[t], "value": DEV_VALUES[t], "fiscal_year": 2024,
                           "period_end": "2024-12-31"} for t in DEV_TICKERS],
        "disclosures": {"universe": "peers", "members": 4,
                        "ranked": [rename[t] for t in DEV_TICKERS], "excluded": [],
                        "period_ends": {rename[t]: "2024-12-31" for t in DEV_TICKERS},
                        "disclose_period_ends": False},
        "subject": {"ticker": rename["DEVB"], "rank": 2, "universe_median": 175 * BN,
                    "position": "above",
                    "names": {rename[t]: [dev_name(rename[t])] for t in DEV_TICKERS}},
    })


class Kit:
    BN = BN
    WMT_TEXT = WMT_TEXT
    PERIOD_ENDS = PERIOD_ENDS
    WMT_ROWS = WMT_ROWS
    STATEMENT_ORDER = STATEMENT_ORDER
    ALPHABETICAL = ALPHABETICAL
    DEV_TICKERS = DEV_TICKERS
    DEV_VALUES = DEV_VALUES
    wmt_case = staticmethod(wmt_case)
    answer = staticmethod(answer)
    rows_in = staticmethod(rows_in)
    dev_case = staticmethod(dev_case)
    dev_name = staticmethod(dev_name)


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
