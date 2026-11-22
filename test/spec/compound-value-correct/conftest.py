"""Builders for the compound-value-correct spec tests (docs/specs/compound-value-correct.md).

Everything is synthetic (fictional DEVA..DEVE filers). Two families of cases:

- H-X compounds, hand-built in hx-authoring's built shape (as in the compound-text-part
  fixtures): a ranking of FY2025 revenue DEVA 300m > DEVB 200m over DEVA..DEVD, DEVD excluded
  (``not_tagged``), with either a two-year trend dependent on DEVA (FY2024 250m, FY2025 300m) or
  a judged text dependent.
- The two generated shapes, read from ``generated_cases.json`` (inputs built by the
  compound-shape spec's synthetic builder): FY2024 revenue DEVA 500m > DEVB 400m > DEVC 300m of
  five members, DEVE excluded; DEVB's operating margin 30.0% is the highest of the three
  (``compound.rank_by_ratio``); DEVA's net income growth from FY2023 is 20.0%
  (``compound.leader_growth``). Their right answers are those of compound-shape C7.

No grader, case or answer class is faked; only answers are prepared.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

HERE = Path(__file__).resolve().parent
M = 1e6

# --- H-X compounds -----------------------------------------------------------------------

RANK_ROWS = [{"ticker": "DEVA", "value": 300 * M, "fact_id": 1},
             {"ticker": "DEVB", "value": 200 * M, "fact_id": 2}]
RANK_REVERSED = [{"ticker": "DEVB", "value": 200 * M, "fact_id": 2},
                 {"ticker": "DEVA", "value": 300 * M, "fact_id": 1}]
TREND_ROWS = [{"entity": "FY2024", "value": 250 * M, "fact_id": 34},
              {"entity": "FY2025", "value": 300 * M, "fact_id": 1}]
# FY2024 stated as 330m against the key's 250m: a wrong trend value.
TREND_WRONG = [{"entity": "FY2024", "value": 330 * M, "fact_id": 34},
               {"entity": "FY2025", "value": 300 * M, "fact_id": 1}]
DISCLOSED = "Among the 4 companies DEVA leads, then DEVB; DEVD could not be ranked."
SILENT = "DEVA leads, then DEVB."
HX_FACT_IDS = (1, 2, 34)


def _ranking_fields() -> dict[str, Any]:
    return {"kind": "screen", "fy": 2025, "unit": "USD",
            "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
            "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
            "expected_rows": [{"entity": "DEVA", "value": 300 * M},
                              {"entity": "DEVB", "value": 200 * M}],
            "order_sensitive": True, "top_n": 2, "expected_fact_ids": [1, 2],
            "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-4}


def _trend_fields() -> dict[str, Any]:
    return {"kind": "breakdown", "ticker": "DEVA", "fy": 2025,
            "expected_rows": [{"entity": "FY2024", "value": 250 * M},
                              {"entity": "FY2025", "value": 300 * M}],
            "order_sensitive": False, "rel_tol": 1e-4,
            "expected_fact_ids": [34, 1], "fact_id_classes": [[34], [1, 11]]}


def _text_fields() -> dict[str, Any]:
    return {"kind": "narrative", "ticker": "DEVA", "fy": 2025,
            "expected_text": "DEVA names a single synthetic supplier as its main supply risk.",
            "key_points": ["a single supplier", "the main supply risk"],
            "passages": [{"accession": "0000900101-26-000001",
                          "quote": "We rely on a single synthetic supplier."}]}


def _hx_compound(dependent: dict[str, Any] | None, cid: str,
                 ranking: bool = True) -> EvalCase:
    rank = _ranking_fields()
    parts = []
    if ranking:
        parts.append({"part": "ranking", **copy.deepcopy(rank)})
    if dependent is not None:
        parts.append({"part": "dependent", **dependent})
    d = {"id": f"hx-{cid}", "question": f"Synthetic compound question {cid}?",
         "suite": "heldout_x_v1", "origin": "handwritten:h_x",
         "tags": ["split:external", "compound"], **rank, "parts": parts}
    return EvalCase.from_dict(d)


def trend_compound() -> EvalCase:
    """Ranking + a two-year trend dependent: both parts graded."""
    return _hx_compound(_trend_fields(), "cvc01")


def text_compound() -> EvalCase:
    """Ranking + a judged text dependent: only the ranking is graded."""
    return _hx_compound(_text_fields(), "cvc02")


def text_only_compound() -> EvalCase:
    """Only a judged text part: nothing is graded."""
    return _hx_compound(_text_fields(), "cvc03", ranking=False)


def hx_answer(rank_ok: bool = True, disclosed: bool = True, trend: str | None = "right",
              fact_ids: tuple[int, ...] | list[int] = HX_FACT_IDS,
              refused: bool = False) -> SystemAnswer:
    """An answer to an H-X compound; ``trend`` is "right", "wrong" or None (no trend rows)."""
    rows = list(RANK_ROWS if rank_ok else RANK_REVERSED)
    if trend == "right":
        rows += TREND_ROWS
    elif trend == "wrong":
        rows += TREND_WRONG
    return SystemAnswer(text=DISCLOSED if disclosed else SILENT,
                        numeric_values=[r["value"] for r in rows],
                        rows=copy.deepcopy(rows), fact_ids=list(fact_ids), refused=refused)


def refusal() -> SystemAnswer:
    return SystemAnswer(text="I cannot answer.", rows=[], fact_ids=[], refused=True)


# --- generated compounds -----------------------------------------------------------------

GEN_ROWS = [{"ticker": "DEVA", "value": 500 * M, "fact_id": 1},
            {"ticker": "DEVB", "value": 400 * M, "fact_id": 2},
            {"ticker": "DEVC", "value": 300 * M, "fact_id": 3}]
GEN_FACT_IDS = (1, 2, 3, 4, 5)
GEN_RANKING = ("Among the 5 covered companies, DEVA, DEVB and DEVC had the most revenue in "
               "fiscal 2024; DEVE could not be ranked. ")
# shape -> (right sentence, its value, wrong sentence, its value)
GEN_DEPENDENT = {
    "rank_by_ratio": ("Of these, DEVB had the highest operating margin, at 30.0%.", 30.0,
                      "Of these, DEVC had the highest operating margin, at 25.0%.", 25.0),
    "leader_growth": ("DEVA's net income grew 20.0% from fiscal 2023.", 20.0,
                      "DEVA's net income grew 35.0% from fiscal 2023.", 35.0),
}


def generated(shape: str) -> EvalCase:
    data = json.loads((HERE / "generated_cases.json").read_text(encoding="utf-8"))
    return EvalCase.from_dict(data[shape])


def gen_answer(shape: str, right: bool = True,
               fact_ids: tuple[int, ...] | list[int] = GEN_FACT_IDS) -> SystemAnswer:
    good, good_v, bad, bad_v = GEN_DEPENDENT[shape]
    sentence, value = (good, good_v) if right else (bad, bad_v)
    return SystemAnswer(text=GEN_RANKING + sentence,
                        numeric_values=[500 * M, 400 * M, 300 * M, value],
                        rows=copy.deepcopy(GEN_ROWS), fact_ids=list(fact_ids))


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    return SimpleNamespace(
        M=M, HX_FACT_IDS=HX_FACT_IDS, GEN_FACT_IDS=GEN_FACT_IDS, SHAPES=tuple(GEN_DEPENDENT),
        trend_compound=trend_compound, text_compound=text_compound,
        text_only_compound=text_only_compound, hx_answer=hx_answer, refusal=refusal,
        generated=generated, gen_answer=gen_answer,
    )
