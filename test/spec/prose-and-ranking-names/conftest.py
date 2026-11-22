"""Builders for the prose-and-ranking-names spec tests (docs/specs/prose-and-ranking-names.md).

Everything is synthetic (fictional DEVA..DEVE filers):

- The two generated shapes, read from ``generated_cases.json`` (copied from compound-value-correct;
  inputs built by the compound-shape spec's synthetic builder): top 3 by FY2024 revenue DEVA
  500m > DEVB 400m > DEVC 300m of five members (DEVD ranked fourth, DEVE excluded); DEVB's
  operating margin 30.0% is the highest of the three (``compound.rank_by_ratio``); DEVA's net
  income growth from FY2023 is 20.0% (``compound.leader_growth``). Right answers as in
  compound-shape C7.
- compound-value-correct's H-X ranking + trend compound: FY2025 revenue DEVA 300m > DEVB 200m
  over DEVA..DEVD (DEVD excluded), DEVA's FY2024 250m and FY2025 300m.

An "envelope" is a baseline arm's JSON reply: the prose in ``"text"`` beside other fields, kept
whole as ``SystemAnswer.text`` (``parse_structured_answer``). No grader, case or answer class is
faked; only answers are prepared.
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
SHAPES = ("rank_by_ratio", "leader_growth")

# --- generated compounds -----------------------------------------------------------------

REVENUE = {"DEVA": 500 * M, "DEVB": 400 * M, "DEVC": 300 * M, "DEVD": 200 * M, "DEVE": 100 * M}
FACT_ID = {"DEVA": 1, "DEVB": 2, "DEVC": 3, "DEVD": 6, "DEVE": 7}
TOP3 = ("DEVA", "DEVB", "DEVC")
GEN_FACT_IDS = (1, 2, 3, 4, 5)
RANKING = ("Among the 5 covered companies, DEVA, DEVB and DEVC had the most revenue in "
           "fiscal 2024; DEVE could not be ranked. ")
NO_UNIVERSE = "DEVA, DEVB and DEVC had the most revenue in fiscal 2024. "
# shape -> (right sentence, its value, wrong sentence, its value)
DEPENDENT = {
    "rank_by_ratio": ("Of these, DEVB had the highest operating margin, at 30.0%.", 30.0,
                      "Of these, DEVC had the highest operating margin, at 25.0%.", 25.0),
    "leader_growth": ("DEVA's net income grew 20.0% from fiscal 2023.", 20.0,
                      "DEVA's net income grew 35.0% from fiscal 2023.", 35.0),
}
# The second step's figures per company: operating margin (rank_by_ratio) and net income
# growth (leader_growth), in percent; DEVB's 30.0 and DEVA's 20.0 are the keyed ones.
STEP2 = {
    "rank_by_ratio": {"DEVA": 22.0, "DEVB": 30.0, "DEVC": 26.0, "DEVD": 12.0, "DEVE": 9.0},
    "leader_growth": {"DEVA": 20.0, "DEVB": 31.0, "DEVC": 4.0, "DEVD": 2.0, "DEVE": 1.0},
}


def generated(shape: str) -> EvalCase:
    data = json.loads((HERE / "generated_cases.json").read_text(encoding="utf-8"))
    return EvalCase.from_dict(data[shape])


def revenue_rows(tickers: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Rows holding each company's FY2024 revenue (the ranking's own figures)."""
    return [{"entity": t, "value": REVENUE[t]} for t in tickers]


def step2_rows(shape: str, tickers: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Rows holding each company's second-step figure instead of its revenue."""
    return [{"entity": t, "value": STEP2[shape][t]} for t in tickers]


def gen_answer(shape: str, rows: list[dict[str, Any]] | None = None, right: bool = True,
               cited: bool = True, universe: bool = True,
               text: str | None = None) -> SystemAnswer:
    """An answer to a generated compound; ``rows`` default to the right revenue rows."""
    rows = copy.deepcopy(revenue_rows(TOP3) if rows is None else rows)
    good, good_v, bad, bad_v = DEPENDENT[shape]
    sentence, value = (good, good_v) if right else (bad, bad_v)
    prose = (RANKING if universe else NO_UNIVERSE) + sentence
    fids: list[int] = []
    if cited:
        fids = sorted({FACT_ID[str(r["entity"])] for r in rows} | {4, 5})
    return SystemAnswer(text=prose if text is None else text,
                        numeric_values=[value] + [float(r["value"]) for r in rows],
                        rows=rows, fact_ids=fids)


def gen_prose(shape: str, right: bool = True, universe: bool = True) -> str:
    good, _gv, bad, _bv = DEPENDENT[shape]
    return (RANKING if universe else NO_UNIVERSE) + (good if right else bad)


# --- envelopes ---------------------------------------------------------------------------


def envelope(prose: str, rows: list[dict[str, Any]], value: float,
             statements: list[str] | tuple[str, ...] = (), text_last: bool = True) -> str:
    """A baseline arm's JSON reply holding ``prose`` as ``"text"``."""
    fields: list[tuple[str, Any]] = [
        ("value", value), ("unit", "percent"),
        ("rows", [{"entity": r.get("entity", r.get("ticker")), "value": r["value"]}
                  for r in rows]),
        ("refused", False), ("hedged", False), ("cited_chunks", ["S1"]),
        ("statements", [{"statement": s, "sources": ["S1"]} for s in statements]),
    ]
    text = ("text", prose)
    ordered = fields + [text] if text_last else [text] + fields
    return json.dumps(dict(ordered))


# --- the H-X compound --------------------------------------------------------------------

HX_RANK = [{"ticker": "DEVA", "value": 300 * M, "fact_id": 1},
           {"ticker": "DEVB", "value": 200 * M, "fact_id": 2}]
HX_TREND = [{"entity": "FY2024", "value": 250 * M, "fact_id": 34},
            {"entity": "FY2025", "value": 300 * M, "fact_id": 1}]
HX_TEXT = "Among the 4 companies DEVA leads, then DEVB; DEVD could not be ranked."
HX_FACT_IDS = (1, 2, 34)


def hx_compound() -> EvalCase:
    rank = {"kind": "screen", "fy": 2025, "unit": "USD",
            "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
            "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
            "expected_rows": [{"entity": "DEVA", "value": 300 * M},
                              {"entity": "DEVB", "value": 200 * M}],
            "order_sensitive": True, "top_n": 2, "expected_fact_ids": [1, 2],
            "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-4}
    trend = {"kind": "breakdown", "ticker": "DEVA", "fy": 2025,
             "expected_rows": [{"entity": "FY2024", "value": 250 * M},
                               {"entity": "FY2025", "value": 300 * M}],
             "order_sensitive": False, "rel_tol": 1e-4,
             "expected_fact_ids": [34, 1], "fact_id_classes": [[34], [1, 11]]}
    d = {"id": "hx-parn01", "question": "Synthetic compound question parn01?",
         "suite": "heldout_x_v1", "origin": "handwritten:h_x",
         "tags": ["split:external", "compound"], **rank,
         "parts": [{"part": "ranking", **copy.deepcopy(rank)},
                   {"part": "dependent", **trend}]}
    return EvalCase.from_dict(d)


def hx_answer(reversed_rank: bool = False,
              fact_ids: tuple[int, ...] | list[int] = HX_FACT_IDS) -> SystemAnswer:
    rank = list(reversed(HX_RANK)) if reversed_rank else list(HX_RANK)
    rows = copy.deepcopy(rank + HX_TREND)
    return SystemAnswer(text=HX_TEXT, numeric_values=[r["value"] for r in rows], rows=rows,
                        fact_ids=list(fact_ids))


def outcome(g: Any) -> tuple[Any, ...]:
    """The grade fields requirement 1 holds equal."""
    return (g.grade, g.passed, g.value_correct, g.checks, g.confident_wrong, g.reason)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    return SimpleNamespace(
        M=M, SHAPES=SHAPES, TOP3=TOP3, REVENUE=REVENUE, STEP2=STEP2, DEPENDENT=DEPENDENT,
        GEN_FACT_IDS=GEN_FACT_IDS, HX_FACT_IDS=HX_FACT_IDS,
        generated=generated, revenue_rows=revenue_rows, step2_rows=step2_rows,
        gen_answer=gen_answer, gen_prose=gen_prose, envelope=envelope,
        hx_compound=hx_compound, hx_answer=hx_answer, outcome=outcome,
    )
