"""C4: asks_for_amount — amount wording without a percent cue
(docs/specs/grader-pick-echo-grow.md, requirement 3).

Oracle: the word lists of requirement 3, applied to each question by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import asks_for_amount

MOVEMENT_VERBS = (
    "grow", "grows", "grew", "grown", "growing",
    "increase", "increases", "increased", "increasing",
    "decrease", "decreases", "decreased", "decreasing",
    "rise", "rises", "rose", "risen", "rising",
    "fall", "falls", "fell", "fallen", "falling",
    "decline", "declines", "declined", "declining",
)
CUES = ("percent", "percentage", "percentages", "per cent", "pct", "rate", "rates", "growth",
        "cagr")


@pytest.mark.criterion("C4")
def test_how_much_movements_and_change_words_ask_for_an_amount():
    # oracle: requirement 3.1 — "how much" then a movement verb, or a change word; no cue
    for q in ("How much did Deva Corp's capital expenditures grow from fiscal 2023?",
              "By how much did Deva Corp's capital expenditures increase from FY23?",
              "By how much did the leader's capital expenditures decrease from FY23?",
              "By how much did Deva Corp's revenue rise in fiscal 2024?",
              "By how much did Deva Corp's revenue fall in fiscal 2024?",
              "By how much did Deva Corp's revenue decline in fiscal 2024?",
              "How much has Deva Corp's revenue grown since fiscal 2022?",
              "How much have Deva Corp's costs risen since fiscal 2022?",
              "How much has Deva Corp's cash fallen since fiscal 2022?",
              "State how much Deva Corp's capital expenditures grew from FY23 to FY24.",
              "HOW MUCH DID DEVA CORP'S CAPITAL EXPENDITURES GROW FROM FY23?",
              "How\nmuch  did Deva Corp's capital expenditures grow from FY23?",
              "By how much did the leader's capital expenditures change from FY23?",
              "What was the year-over-year change in Deva Corp's capital expenditures?",
              "By how much have Deva Corp's corporate capital expenditures changed?",
              "How much did the overgrowth reserve of Deva Corp rise in fiscal 2024?"):
        assert asks_for_amount(q) is True, q


@pytest.mark.criterion("C4")
def test_percent_cues_and_missing_amount_wording_block():
    # oracle: requirement 3.2/3.3 — a cue, or no amount wording; the first question is the
    # applying control
    base = "How much did Deva Corp's capital expenditures grow from FY23?"
    assert asks_for_amount(base) is True
    for cue in CUES:
        assert asks_for_amount(base.replace("grow from", f"grow ({cue}) from")) is False, cue
    assert asks_for_amount(base.replace("FY23?", "FY23, in %?")) is False
    for q in ("By what percent did Deva Corp's capital expenditures grow from FY23?",
              "By what percentage did Deva Corp's revenue increase in fiscal 2024?",
              "What was the growth rate of Deva Corp's revenue in fiscal 2024?",
              "What was the rate of change in Deva Corp's revenue?",
              "What was the growth in Deva Corp's capital expenditures from FY23?",
              "By how much did Deva Corp's revenue growth change in fiscal 2024?",
              "How much did Deva Corp's CAGR increase?",
              "How fast did Deva Corp's revenue grow in fiscal 2024?",
              "Did Deva Corp's revenue grow in fiscal 2024?",
              "What was the increase in Deva Corp's revenue in fiscal 2024?",
              "How much revenue did Deva Corp report in fiscal 2024?",
              "How much enterprise value did Deva Corp report in fiscal 2024?",
              "Deva Corp's capital expenditures grew; by how much?",
              "What was Deva Corp's foreign exchange effect on capital expenditures?"):
        assert asks_for_amount(q) is False, q


_SUBJECT = st.lists(st.sampled_from(("Deva", "Corp's", "capital", "expenditures", "the",
                                     "leader's", "operating", "cash", "total", "debt", "net",
                                     "income", "FY23", "fiscal", "2024", "corporate")),
                    min_size=1, max_size=6).map(" ".join)


@pytest.mark.criterion("C4")
@given(lead=st.sampled_from(("How much", "By how much", "how much", "State how much")),
       aux=st.sampled_from(("did", "has", "have", "does")), subject=_SUBJECT,
       verb=st.sampled_from(MOVEMENT_VERBS), cue=st.sampled_from(CUES + ("%",)),
       at=st.integers(0, 200), upper=st.booleans())
def test_random_how_much_questions(lead, aux, subject, verb, cue, at, upper):
    # oracle: requirement 3 — the question is "how much" + a cue-free subject + a movement
    # verb, so it asks for an amount; a cue anywhere blocks it; letter case never matters
    q = f"{lead} {aux} {subject} {verb} from the prior year?"
    assert asks_for_amount(q) is True, q
    assert asks_for_amount(q.upper() if upper else q.lower()) is True, q
    cut = min(at, len(q))
    while 0 < cut < len(q) and q[cut - 1].isalnum():
        cut -= 1  # insert at a word boundary
    blocked = f"{q[:cut]} {cue} {q[cut:]}"
    assert asks_for_amount(blocked) is False, blocked
    assert asks_for_amount(blocked.upper() if upper else blocked.lower()) is False, blocked
