"""Builders for the grader-pick-echo-grow spec tests (docs/specs/grader-pick-echo-grow.md).

- ``CANDIDATES`` / ``RATIO``: a ``pick`` block (ticker -> names; the ratio's surface forms) over
  synthetic filers DEVA, DEVB, DEVC, for a working capital ratio.
- ``real_shape``: a hand-written answer with the structure of the real failure (dev run
  eval-18b83b48fb4d): a ``[sq1]`` imperative ranking echo and its answer, a ``[sq2]`` imperative
  echo listing a loser (DEVC) nearest before "highest", then an answer naming DEVB as the highest.
- ``pick_case`` / ``answer``: a ``compound.rank_by_ratio`` case whose dependent part carries the
  pick block (winner DEVB at 1.35), and a cited SystemAnswer.
- Rule B: ``LEGS`` (DEVA's capex FY2023 9,447,000,000 and FY2024 10,959,000,000; decimals -6),
  ``RANKING_INPUTS`` (another concept, one year), ``growth_key`` and the case builders ``lg_case``
  (a ``compound.leader_growth`` case with only its dependent part, the legs after the ranking's
  inputs in the compound's own key) and ``yoy_case`` (a ``compute.yoy_growth`` case).

Reached through the session-scoped ``kit`` fixture, so hypothesis tests can use it. Grading is
pure, so nothing is faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

CANDIDATES = {
    "DEVA": ["Deva Corp", "Deva Corporation"],
    "DEVB": ["Devb Corp", "Devb Corporation"],
    "DEVC": ["Devc Corp", "Devc Corporation"],
}
RATIO = ["Working Capital Ratio", "working capital ratio", "current ratio"]

RANKING_ECHO = ("[sq1] Among the covered companies, identify the 3 companies with the largest "
                "total debt for FY24.\n")
RANKING_ANSWER = ("The three covered companies with the largest total debt for FY24 are Deva "
                  "Corp (DEVA) at 106,629,000,000 USD, Devb Corp (DEVB) at 86,869,000,000 USD, "
                  "and Devc Corp (DEVC) at 67,566,000,000 USD.\n\n")
# The dependent's answer, as the real one reads: the winner named first, then the others.
PICK_ANSWER = ("Devb Corp (DEVB) had the highest working capital ratio among the three "
               "companies in fiscal 2024, at 1.35. Deva Corp's was 0.95 and Devc Corp's was "
               "0.83.\n"
               "DEVB's working capital ratio for fiscal 2024 was 1.35.")
PICK_VALUES = [1.35, 0.95, 0.83]


def pick_echo(ending: str = ".", order: tuple[str, ...] = ("DEVB", "DEVA", "DEVC"),
              n: int = 2) -> str:
    """An imperative ``[sqN]`` echo of the dependent sub-question, ending in ``ending``."""
    return (f"[sq{n}] Among {', '.join(order)}, identify the company with the highest working "
            f"capital ratio for fiscal 2024{ending}\n")


def real_shape(ending: str = ".") -> str:
    """The real failure's structure: two imperative echoes, each before its own answer."""
    return RANKING_ECHO + RANKING_ANSWER + pick_echo(ending) + PICK_ANSWER


def pick_case(winner: str = "DEVB", value: float = 1.35) -> EvalCase:
    """A ``compound.rank_by_ratio`` case with only its pick-carrying dependent part."""
    dependent = {
        "part": "dependent",
        "kind": "compute",
        "expected_value": value,
        "ticker": winner,
        "tags": ["shape:compute.ratio"],
        "pick": {"candidates": {t: list(n) for t, n in CANDIDATES.items()}, "ratio": list(RATIO)},
    }
    return EvalCase.from_dict({
        "id": "gpe-rbr-1",
        "question": ("Of the 3 covered companies with the largest total debt for fiscal 2024, "
                     "identify the one with the highest working capital ratio."),
        "kind": "screen",
        "origin": "v2:compound.rank_by_ratio",
        "paraphrase_group": "int-gpe",
        "suite": "gpe",
        "fy": 2024,
        "rel_tol": 0.0001,
        "unit": "USD",
        "tags": ["split:dev", "intent:int-gpe", "family:formal",
                 "shape:compound.rank_by_ratio", "compound"],
        "parts": [dependent],
    })


def answer(text: str, values, hedged: bool = False) -> SystemAnswer:
    """A cited answer with the given produced values."""
    return SystemAnswer(text=text, numeric_values=list(values), rows=[], fact_ids=[1, 2, 3],
                        refused=False, hedged=hedged, error=None)


# --- Rule B -----------------------------------------------------------------------------
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
CX23, CX24 = 9_447_000_000, 10_959_000_000


def leg(value: float, fy: int, concept: str = CAPEX, cik: str | None = "0000000001",
        unit: str = "USD", decimals: int | None = -6) -> dict:
    """One key input, in the shape the v2 keyer writes."""
    item = {"concept": concept, "decimals": decimals, "fiscal_year": fy,
            "period_end": f"{fy}-09-30", "unit": unit, "value": value}
    if cik is not None:
        item["cik"] = cik
    return item


LEGS = [leg(CX23, 2023), leg(CX24, 2024)]
# The same legs the other way round: a decline of 1,512,000,000.
DOWN_LEGS = [leg(CX24, 2023), leg(CX23, 2024)]
RANKING_INPUTS = [
    leg(106_629_000_000, 2024, concept="s4:debt_total", decimals=None),
    leg(86_869_000_000, 2024, concept="s4:debt_total", cik="0000000002", decimals=None),
    leg(67_566_000_000, 2024, concept="s4:debt_total", cik="0000000003", decimals=None),
]
# Each key's growth, by its own definition: (later - earlier) / |earlier|.
GROWTH = (CX24 - CX23) / abs(CX23)
DOWN_GROWTH = (CX23 - CX24) / abs(CX24)

LG_GROW_Q = ("Looking at the top 3 of the covered companies by total debt in FY24, how much did "
             "the leader's capital expenditures grow from FY23?")
LG_NOUN_Q = ("Looking at the top 3 of the covered companies by total debt in FY24, what was the "
             "growth in the leader's capital expenditures from FY23?")
YOY_GROW_Q = ("How much did Deva Corp's capital expenditures grow from fiscal 2023 to fiscal "
              "2024?")
YOY_PERCENT_Q = ("By what percent did Deva Corp's capital expenditures grow from fiscal 2023 to "
                 "fiscal 2024?")


def growth_key(inputs, growth: float, definition: str = "growth",
               dependent: bool = False) -> dict:
    """A growth key over ``inputs``; with ``dependent``, a compound key's dependent block."""
    key = {"definition": definition, "disagreements": [], "inputs": [dict(i) for i in inputs],
           "policies": ["S1", "S2", "S6"], "same_filing": True, "source": "sec_companyfacts",
           "status": "verified"}
    if dependent:
        key["dependent"] = {"definition": definition, "same_filing": True, "ticker": "DEVA",
                            "value": growth}
    return key


def lg_case(question: str = LG_GROW_Q, legs=None, growth: float = GROWTH) -> EvalCase:
    """A ``compound.leader_growth`` case with only its dependent part (leader DEVA)."""
    legs = LEGS if legs is None else legs
    dependent = {"part": "dependent", "kind": "compute", "expected_value": growth,
                 "alt_values": [growth * 100], "ticker": "DEVA",
                 "tags": ["shape:compute.yoy_growth"]}
    return EvalCase.from_dict({
        "id": "gpe-lg-1", "question": question, "kind": "screen",
        "origin": "v2:compound.leader_growth", "paraphrase_group": "int-gpe-lg",
        "suite": "gpe", "fy": 2024, "rel_tol": 0.0001, "unit": "USD", "top_n": 3,
        "tags": ["split:dev", "intent:int-gpe-lg", "family:conversational",
                 "shape:compound.leader_growth", "compound"],
        "key": growth_key([*RANKING_INPUTS, *legs], growth, dependent=True),
        "parts": [dependent],
    })


def yoy_case(question: str = YOY_GROW_Q, legs=None, growth: float = GROWTH) -> EvalCase:
    """A non-compound ``compute.yoy_growth`` case for DEVA."""
    legs = LEGS if legs is None else legs
    return EvalCase.from_dict({
        "id": "gpe-yoy-1", "question": question, "kind": "compute",
        "origin": "v2:compute.yoy_growth", "paraphrase_group": "int-gpe-yoy",
        "suite": "gpe", "fy": 2024, "rel_tol": 0.0001, "ticker": "DEVA",
        "expected_value": growth, "alt_values": [growth * 100],
        "tags": ["split:dev", "intent:int-gpe-yoy", "family:formal",
                 "shape:compute.yoy_growth"],
        "key": growth_key(legs, growth),
    })


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The pick block, the real shape, the growth keys and the builders, as one namespace."""
    return SimpleNamespace(
        CANDIDATES=CANDIDATES, RATIO=RATIO, RANKING_ECHO=RANKING_ECHO,
        RANKING_ANSWER=RANKING_ANSWER, PICK_ANSWER=PICK_ANSWER, PICK_VALUES=PICK_VALUES,
        pick_echo=pick_echo, real_shape=real_shape, pick_case=pick_case, answer=answer,
        CAPEX=CAPEX, CX23=CX23, CX24=CX24, LEGS=LEGS, DOWN_LEGS=DOWN_LEGS,
        RANKING_INPUTS=RANKING_INPUTS, GROWTH=GROWTH, DOWN_GROWTH=DOWN_GROWTH,
        LG_GROW_Q=LG_GROW_Q, LG_NOUN_Q=LG_NOUN_Q, YOY_GROW_Q=YOY_GROW_Q,
        YOY_PERCENT_Q=YOY_PERCENT_Q, leg=leg, growth_key=growth_key, lg_case=lg_case,
        yoy_case=yoy_case,
    )
