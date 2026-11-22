"""Builders for the pick-skips-questions spec tests (docs/specs/pick-skips-questions.md).

- ``CANDIDATES`` / ``RATIO``: a ``pick`` block in the shape compound-shape requirement 6 carries
  (ticker -> names; the ratio's surface forms), over synthetic filers DEVA, DEVB, DEVC.
- ``SHAPES``: hand-written answers with the structure of the three real failures (dev
  int-684520183450-08bb76ce, int-684520183450-10cb126b, validation int-684520183450-03e5d45d):
  a ``[sq1]`` ranking question and its answer, a ``[sq2]`` question listing the winner DEVA first
  and a loser nearest before the superlative, then an answer naming DEVA as the highest.
- ``pick_case`` / ``answer``: a ``compound.rank_by_ratio`` case whose dependent part carries the
  pick block (winner DEVA at 0.315), and a cited SystemAnswer.
- Rule B: ``LEGS`` (DEVA's capex FY2023 10,959,000,000 and FY2024 9,447,000,000, as the real
  int-21e0eddbbb5b-0f25e25f key states Apple's; decimals -6), ``RANKING_INPUTS`` (another
  concept, one year), ``growth_key``, and the case builders ``lg_case`` (a
  ``compound.leader_growth`` case with only its dependent part; the legs sit in the compound's
  own key after the ranking's inputs, as the v2 keyer writes them) and ``yoy_case`` (a
  ``compute.yoy_growth`` case).

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
RATIO = ["Operating Margin", "operating profit margin", "operating margin percentage", "op margin"]

RANKING_PART = (
    "[sq1] Which 3 companies among the covered companies have the largest total debt for "
    "fiscal 2024?\n"
    "The three covered companies with the largest total debt for fiscal 2024 are Deva Corp "
    "(DEVA) at 106,629,000,000 USD, Devb Corp (DEVB) at 86,869,000,000 USD, and Devc Corp "
    "(DEVC) at 67,566,000,000 USD. Universe: the covered companies (33 companies; 31 ranked "
    "for FY2024).\n\n"
)

# Shape 1 (08bb76ce): the answer ends with a "therefore has the highest" summary.
SHAPE_1 = RANKING_PART + (
    "[sq2] Among DEVA, DEVC, DEVB, which company has the highest operating profit margin for "
    "fiscal 2024?\n"
    "For fiscal 2024, DEVA's operating profit margin was 0.315 pure, DEVC's was 0.289 pure, "
    "and DEVB's was 0.261 pure. These ratios are computed from each company's reported "
    "operating income and revenue. DEVA therefore has the highest operating profit margin "
    "among DEVA, DEVC, and DEVB.\n"
    "DEVA's operating profit margin for fiscal 2024 was 0.315 pure.\n"
    "DEVA has the highest operating profit margin among DEVA, DEVC, and DEVB for fiscal 2024."
)

# Shape 2 (10cb126b): the answer leads with the pick and names a "lowest" filer after it.
SHAPE_2 = RANKING_PART + (
    "[sq2] Among DEVA, DEVC, DEVB, which had the highest operating margin percentage in "
    "fiscal 2024?\n"
    "Deva Corp (DEVA) had the highest operating margin among the three companies in fiscal "
    "2024, at 31.5 (percent). Devc Corp (DEVC) followed at 28.9 and Devb Corp (DEVB) was "
    "lowest at 26.1. No narrative chunks were retrieved.\n"
    "Deva's operating margin in fiscal 2024 was 31.5 (percent), the highest of the three "
    "companies.\n"
    "Devb's operating margin in fiscal 2024 was 26.1 (percent), the lowest of the three "
    "companies."
)

# Shape 3 (03e5d45d): the question asks for the "best" ratio, the answer says "highest".
SHAPE_3 = RANKING_PART + (
    "[sq2] Of DEVA, DEVC, DEVB, which company had the best operating profit margin in fiscal "
    "year 2024?\n"
    "Deva Corp (DEVA) had the highest operating profit margin among DEVA, DEVC, and DEVB in "
    "fiscal year 2024. Deva's operating margin was 0.315 (pure). Devc's operating margin was "
    "0.289, and Devb's was 0.261.\n"
    "Among the three companies, Deva Corp had the highest operating profit margin in fiscal "
    "year 2024, at 0.315, compared with 0.289 for Devc Corp and 0.261 for Devb Corp."
)

SHAPES = (SHAPE_1, SHAPE_2, SHAPE_3)
SHAPE_VALUES = ([0.315, 0.289, 0.261], [31.5, 28.9, 26.1], [0.315, 0.289, 0.261])


def pick_case(winner: str = "DEVA", value: float = 0.315) -> EvalCase:
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
        "id": "psq-rbr-1",
        "question": ("Of the 3 covered companies with the largest total debt for fiscal 2024, "
                     "identify the one with the highest operating profit margin."),
        "kind": "screen",
        "origin": "v2:compound.rank_by_ratio",
        "paraphrase_group": "int-psq",
        "suite": "psq",
        "fy": 2024,
        "rel_tol": 0.0001,
        "unit": "USD",
        "tags": ["split:dev", "intent:int-psq", "family:formal",
                 "shape:compound.rank_by_ratio", "compound"],
        "parts": [dependent],
    })


def answer(text: str, values, hedged: bool = False) -> SystemAnswer:
    """A cited answer with the given produced values."""
    return SystemAnswer(text=text, numeric_values=list(values), rows=[], fact_ids=[1, 2, 3],
                        refused=False, hedged=hedged, error=None)


# --- Rule B: growth keys (requirements 4, 5) --------------------------------------------
# DEVA's capex legs, as the real int-21e0eddbbb5b-0f25e25f key states Apple's (public SEC
# XBRL values; decimals -6), and three FY2024 ranking inputs of another concept.
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
CX23, CX24 = 10_959_000_000, 9_447_000_000


def leg(value: float, fy: int, concept: str = CAPEX, cik: str | None = "0000000001",
        unit: str = "USD", decimals: int = -6) -> dict:
    """One key input, in the shape the v2 keyer writes."""
    item = {"concept": concept, "decimals": decimals, "fiscal_year": fy,
            "period_end": f"{fy}-09-30", "unit": unit, "value": value}
    if cik is not None:
        item["cik"] = cik
    return item


LEGS = [leg(CX23, 2023), leg(CX24, 2024)]
RANKING_INPUTS = [
    leg(106_629_000_000, 2024, concept="s4:debt_total", decimals=None),
    leg(86_869_000_000, 2024, concept="s4:debt_total", cik="0000000002", decimals=None),
    leg(67_566_000_000, 2024, concept="s4:debt_total", cik="0000000003", decimals=None),
]
# The key's growth, by its own definition: (later - earlier) / |earlier|.
GROWTH = (CX24 - CX23) / abs(CX23)

LG_CHANGE_Q = ("Looking at the top 3 of the covered companies by total debt in FY24, by how much "
               "did the leader's capital expenditures change from FY23?")
LG_GROWTH_Q = ("For the company ranked first among the top 3 of the covered companies by total "
               "debt for FY24, state the growth in its capital expenditures from FY23.")
YOY_CHANGE_Q = ("By how much did Deva Corp's capital expenditures change from fiscal 2023 to "
                "fiscal 2024?")
YOY_PERCENT_Q = ("By what percentage did Deva Corp's capital expenditures change from fiscal "
                 "2023 to fiscal 2024?")


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


def lg_case(question: str = LG_CHANGE_Q, legs=None, growth: float = GROWTH) -> EvalCase:
    """A ``compound.leader_growth`` case with only its dependent part (leader DEVA)."""
    legs = LEGS if legs is None else legs
    dependent = {"part": "dependent", "kind": "compute", "expected_value": growth,
                 "alt_values": [growth * 100], "ticker": "DEVA",
                 "tags": ["shape:compute.yoy_growth"]}
    return EvalCase.from_dict({
        "id": "psq-lg-1", "question": question, "kind": "screen",
        "origin": "v2:compound.leader_growth", "paraphrase_group": "int-psq-lg",
        "suite": "psq", "fy": 2024, "rel_tol": 0.0001, "unit": "USD", "top_n": 3,
        "tags": ["split:dev", "intent:int-psq-lg", "family:conversational",
                 "shape:compound.leader_growth", "compound"],
        "key": growth_key([*RANKING_INPUTS, *legs], growth, dependent=True),
        "parts": [dependent],
    })


def yoy_case(question: str = YOY_CHANGE_Q, legs=None, growth: float = GROWTH) -> EvalCase:
    """A non-compound ``compute.yoy_growth`` case for DEVA."""
    legs = LEGS if legs is None else legs
    return EvalCase.from_dict({
        "id": "psq-yoy-1", "question": question, "kind": "compute",
        "origin": "v2:compute.yoy_growth", "paraphrase_group": "int-psq-yoy",
        "suite": "psq", "fy": 2024, "rel_tol": 0.0001, "ticker": "DEVA",
        "expected_value": growth, "alt_values": [growth * 100],
        "tags": ["split:dev", "intent:int-psq-yoy", "family:formal",
                 "shape:compute.yoy_growth"],
        "key": growth_key(legs, growth),
    })


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The pick block, the three shapes, the growth keys and the builders, as one namespace."""
    return SimpleNamespace(
        CANDIDATES=CANDIDATES, RATIO=RATIO, RANKING_PART=RANKING_PART, SHAPES=SHAPES,
        SHAPE_VALUES=SHAPE_VALUES, pick_case=pick_case, answer=answer,
        CAPEX=CAPEX, CX23=CX23, CX24=CX24, LEGS=LEGS, RANKING_INPUTS=RANKING_INPUTS,
        GROWTH=GROWTH, LG_CHANGE_Q=LG_CHANGE_Q, LG_GROWTH_Q=LG_GROWTH_Q,
        YOY_CHANGE_Q=YOY_CHANGE_Q, YOY_PERCENT_Q=YOY_PERCENT_Q, leg=leg,
        growth_key=growth_key, lg_case=lg_case, yoy_case=yoy_case,
    )
