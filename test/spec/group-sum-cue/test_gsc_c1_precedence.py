"""C1: precedence and preconditions of ``group_aggregate_cue``
(docs/specs/group-sum-cue.md, Requirement 1 steps 1-2, and the purity sentence).

Every phrasing is written for this spec. Universe ids are the committed ones
(``configs/eval/v2/groups.yaml`` plus ``covered``).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.universe_routing import aggregate_cue, group_aggregate_cue

UNIVERSE_IDS = ["covered", "semiconductors", "retailers", "big_tech"]

# Word banks written for this spec (English, not eval wording).
CUE_PHRASES = [
    "median", "combined", "aggregate", "collectively", "altogether", "summed", "sum of",
    "in total", "taken together", "medians", "aggregated",
]
METRICS = ["revenue", "net income", "R&D expense", "long-term debt", "inventories",
           "capital expenditures", "interest expense", "total assets", "cash"]
GROUP_PHRASES = ["the retailers", "the semiconductor companies", "the big tech companies",
                 "the covered companies", "Apple and Microsoft", "each retailer"]
FILLER = ["what was", "show me", "for", "in fiscal 2023", "FY2022", "top 3", "which", "vs.",
          "total", "add up", "per company", "every"]


@pytest.mark.criterion("C1")
def test_an_unambiguous_cue_word_wins_whatever_the_universes():
    # oracle: Requirement 1 step 1 by hand — "median" is a median word and the question has no
    # sum word, so aggregate_cue says "median"; "combined" is a sum word, so "sum". Step 1
    # returns that answer before the universe and company preconditions are looked at.
    median_q = "What was the median revenue of the retailers in fiscal 2023?"
    combined_q = "combined R&D expense, the semiconductor companies and Apple, FY2022"
    for universes, resolved in [(["retailers"], 0), ([], 0), (["retailers", "big_tech"], 0),
                                (["retailers"], 2), ([], 5)]:
        assert group_aggregate_cue(median_q, universes, resolved) == "median"
        assert group_aggregate_cue(combined_q, universes, resolved) == "sum"


@pytest.mark.criterion("C1")
def test_two_universes_or_a_resolved_company_gives_none_for_an_uncued_total():
    # oracle: Requirement 1 steps 2-3 by hand — the question has "total" and no blocking word,
    # so it is a sum over one universe and no company; with two universes or one resolved
    # company step 2 returns None before step 3.
    q = "total revenue of the retailers in 2023"
    assert group_aggregate_cue(q, ["retailers"], 0) == "sum"
    assert group_aggregate_cue(q, ["retailers", "semiconductors"], 0) is None
    assert group_aggregate_cue(q, ["retailers"], 1) is None
    assert group_aggregate_cue(q, [], 0) is None


@pytest.mark.criterion("C1")
def test_the_add_up_phrase_also_needs_one_universe_and_no_company():
    # oracle: Requirement 1 step 2 by hand — step 2 applies to both step-3 rules.
    q = "If you add up the big tech companies, what was their revenue in 2024?"
    assert group_aggregate_cue(q, ["big_tech"], 0) == "sum"
    assert group_aggregate_cue(q, ["big_tech", "covered"], 0) is None
    assert group_aggregate_cue(q, ["big_tech"], 3) is None


@pytest.mark.criterion("C1")
@given(
    cue=st.sampled_from(CUE_PHRASES),
    metric=st.sampled_from(METRICS),
    group=st.sampled_from(GROUP_PHRASES),
    extra=st.lists(st.sampled_from(FILLER), max_size=3),
    universes=st.lists(st.sampled_from(UNIVERSE_IDS), max_size=3),
    resolved=st.integers(min_value=0, max_value=6),
)
def test_a_cued_question_returns_aggregate_cue_unchanged(cue, metric, group, extra, universes,
                                                         resolved):
    # oracle: Requirement 1 step 1 — whenever aggregate_cue (the spec's named step-1 oracle,
    # unchanged by this change) gives an answer, that answer is returned for any universes and
    # company count; and the function is pure, so ``universes`` is not mutated.
    q = " ".join([*extra[:1], f"the {cue} {metric} of {group}", *extra[1:]])
    expected = aggregate_cue(q)
    # One cue phrase of one kind and no other cue word in the banks, so step 1 applies.
    assert expected in ("median", "sum")
    before = list(universes)
    assert group_aggregate_cue(q, universes, resolved) == expected
    assert universes == before
