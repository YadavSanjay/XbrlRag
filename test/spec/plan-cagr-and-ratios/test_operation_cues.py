"""Spec tests for plan-cagr-and-ratios C5 (`operation_kinds`, `operation_cue`), C6
(`operation_intent`) and C8 (`split_loses_operation`).

docs/specs/plan-cagr-and-ratios.md, Requirements 5, 6 and 8. Pure. Every question is written
for the test (Notes), about the synthetic Example Co.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.operation_cues import (
    operation_cue,
    operation_intent,
    operation_kinds,
    split_loses_operation,
)

CAGR_CUES = [
    "What was Example Co's revenue CAGR from FY2022 to FY2025?",
    "Example Co revenue cagrs over five years",
    "What was the compound annual growth rate of Example Co's revenue, FY2022-FY2025?",
    "Example Co net income compounded annually from FY2021 to FY2024",
    "What was Example Co's compound growth in operating income since FY2022?",
    "Example Co's compounded rate of revenue growth, FY2022 to FY2025",
    "What was Example Co's annualized growth in revenue from FY2022 to FY2025?",
    "Example Co annualised growth of capex, FY2022-FY2025",
]
RATIO_CUES = [
    "Example Co Assets, Current / Liabilities, Current, FY2024",
    "Example Co (current assets) / (current liabilities), FY2024",
    "What was the ratio of Example Co's current assets to current liabilities in FY2024?",
    "Example Co operating income divided by revenue, FY2024",
    "Example Co R&D as a percentage of revenue in FY2024",
    "Example Co R&D as a % of revenue in FY2024",
    "Example Co R&D as a percent of revenue in FY2024",
    "Example Co capex as a share of operating cash flow, FY2024",
    "Example Co SG&A as a proportion of revenue, FY2024",
    "Example Co dividends as a fraction of net income, FY2024",
    "What was Example Co's debt to equity ratio in FY2024?",
    "What was Example Co's debt-to-equity ratio in FY2024?",
    "Example Co net debt to EBITDA ratio, FY2024",
    "Example Co debt to total capital ratios for FY2023 and FY2024",
]
DIFFERENCE_CUES = [
    "What is the difference between Example Co's revenue and cost of revenue in FY2024?",
    "Example Co difference in operating income from FY2023 to FY2024",
]
NO_CUES = [
    "What was Example Co's revenue in FY2024?",
    "Did Example Co file a 10-K/A for FY2024?",
    "Example Co revenue and/or net income, FY2024",
    "Example Co revenue FY2024 / FY2025",
    "Example Co debt/equity in FY2024",
    "What was Example Co's current ratio in FY2024?",
    "What was Example Co's operating margin in FY2024?",
    "What percentage of revenue came from services at Example Co in FY2024?",
    "What is Example Co's market share of smartphones?",
    "How did Example Co respond to the ratio covenant in FY2024?",
    "How much did Example Co's revenue grow per year from FY2022 to FY2025?",
    "What was Example Co's average annual growth in revenue?",
    "Example Co annual growth in revenue, FY2024",
    "Example Co annualized revenue run rate in Q4 FY2024",
    "Did the supply shortage compound the problem for Example Co?",
    "Example Co total assets FY2024",
]


@pytest.mark.criterion("C5")
def test_every_listed_cue_and_non_cue():
    # oracle: Requirement 5 applied by hand to each written question
    for q in CAGR_CUES:
        assert operation_kinds(q) == frozenset({"cagr"}), q
        assert operation_cue(q) == "cagr", q
    for q in RATIO_CUES:
        assert operation_kinds(q) == frozenset({"ratio"}), q
        assert operation_cue(q) == "ratio", q
    for q in DIFFERENCE_CUES:
        assert operation_kinds(q) == frozenset({"difference"}), q
        assert operation_cue(q) is None, q
    for q in NO_CUES:
        assert operation_kinds(q) == frozenset(), q
        assert operation_cue(q) is None, q


@pytest.mark.criterion("C5")
def test_cagr_takes_precedence_over_ratio():
    # oracle: Requirement 5 — operation_cue is cagr when cagr is among the kinds
    q = "Example Co CAGR of R&D as a percentage of revenue, FY2022 to FY2025"
    assert operation_kinds(q) == frozenset({"cagr", "ratio"})
    assert operation_cue(q) == "cagr"
    q = "The difference between Example Co's revenue CAGR and its net income CAGR"
    assert operation_kinds(q) == frozenset({"cagr", "difference"})
    assert operation_cue(q) == "cagr"


_ALL = CAGR_CUES + RATIO_CUES + DIFFERENCE_CUES + NO_CUES
_FILLER = st.sampled_from(["please", "kindly", "Example", "figures", "reported", "thanks"])


@pytest.mark.criterion("C5")
@given(st.sampled_from(_ALL), st.sampled_from(["lower", "upper", "title"]), _FILLER, _FILLER)
def test_kinds_ignore_case_and_added_non_cue_words(q, case, before, after):
    # oracle: Requirement 5 — words are lower-cased; a cue needs its own words in order
    expected = operation_kinds(q)
    assert expected == operation_kinds(getattr(q, case)())
    assert expected == operation_kinds(f"{before} {q} {after}")
    cue = operation_cue(q)
    assert cue in ({None} | set(expected))
    assert cue != "difference"
    assert (cue is None) == (not ({"cagr", "ratio"} & expected))
    assert operation_kinds(CAGR_CUES[0]) == frozenset({"cagr"})


# --------------------------------------------------------------------------- C6
INTENTS = ["NUMERIC_LOOKUP", "NUMERIC_COMPUTE", "NUMERIC_SCREEN", "NARRATIVE", "HYBRID"]


@pytest.mark.criterion("C6")
def test_operation_intent_table():
    # oracle: Requirement 6 — only a cued NUMERIC_LOOKUP changes, to NUMERIC_COMPUTE
    for op in ("cagr", "ratio"):
        assert operation_intent("NUMERIC_LOOKUP", op) == "NUMERIC_COMPUTE"
        for intent in INTENTS[1:]:
            assert operation_intent(intent, op) == intent
    for intent in INTENTS:
        assert operation_intent(intent, None) == intent


@pytest.mark.criterion("C6")
@given(st.sampled_from(INTENTS), st.sampled_from([None, "cagr", "ratio"]))
def test_operation_intent_only_widens_a_lookup(intent, op):
    # oracle: Requirement 6
    out = operation_intent(intent, op)
    assert out in (intent, "NUMERIC_COMPUTE")
    if out != intent:
        assert intent == "NUMERIC_LOOKUP" and op is not None
    assert operation_intent("NUMERIC_LOOKUP", "ratio") == "NUMERIC_COMPUTE"


# --------------------------------------------------------------------------- C8
RATIO_Q = "Example Co Assets, Current / Liabilities, Current, FY2024"


@pytest.mark.criterion("C8")
def test_split_loses_operation_points():
    # oracle: Requirement 8 applied by hand
    assert split_loses_operation(RATIO_Q, ["Example Co current assets FY2024",
                                           "Example Co current liabilities FY2024"]) is True
    compound = ("Of the three largest covered companies by revenue in FY2024, which had the "
                "highest revenue CAGR from FY2022 to FY2024?")
    assert split_loses_operation(compound, [
        "Top 3 covered companies by revenue in FY2024",
        "Revenue CAGR from FY2022 to FY2024 of <<TOP:sq1:3>>"]) is False
    assert split_loses_operation(RATIO_Q, [RATIO_Q]) is False
    assert split_loses_operation("What was Example Co's revenue in FY2024 and what drove it?",
                                 ["Example Co revenue FY2024",
                                  "What drove Example Co's revenue in FY2024?"]) is False
    diff = "What is the difference between Example Co's revenue in FY2023 and FY2024?"
    assert split_loses_operation(diff, ["Example Co revenue FY2023",
                                        "Example Co revenue FY2024"]) is True
    both = "Example Co revenue CAGR FY2022 to FY2025 and R&D as a share of revenue in FY2025"
    assert split_loses_operation(both, ["Example Co revenue CAGR FY2022 to FY2025",
                                        "Example Co R&D FY2025", "Example Co revenue FY2025"]
                                 ) is True


@pytest.mark.criterion("C8")
@given(st.sampled_from(CAGR_CUES + RATIO_CUES + DIFFERENCE_CUES),
       st.lists(st.sampled_from(NO_CUES), min_size=1, max_size=4))
def test_keeping_the_query_as_a_sub_query_never_loses_it(q, parts):
    # oracle: Requirement 8 — the query's own kinds are in one sub-query when it is one
    assert split_loses_operation(q, [*parts, q]) is False
    assert split_loses_operation(q, [*parts, "Example Co revenue FY2024"]) is True
