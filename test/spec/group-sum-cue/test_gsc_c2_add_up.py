"""C2: the add-up phrase asks for the group's sum
(docs/specs/group-sum-cue.md, Requirement 1 step 3, first rule).

Every phrasing is written for this spec. ``_words`` lower-cases and splits on anything but
``[a-z0-9&]``, so "Adding" reads as ``adding``, "retailers'" as ``retailers`` and "R&D" as
one word ``r&d``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.universe_routing import aggregate_cue, group_aggregate_cue

ADD_UP = ["add up", "adds up", "added up", "adding up"]

# One-group question parts written for this spec; none holds a cue word, "total", "add" or a
# ranking word, so the base question is uncued and returns None.
GROUPS = [("the retailers", "retailers"), ("the semiconductor companies", "semiconductors"),
          ("the big tech companies", "big_tech"), ("the covered companies", "covered")]
METRICS = ["revenue", "net income", "R&D expense", "long-term debt", "inventories",
           "capital expenditures", "interest expense", "operating cash flow"]
YEARS = ["fiscal 2021", "FY2022", "in 2024", "fiscal year 2023", ""]
TEMPLATES = [
    "{m} of {g}, {y}",
    "What was the {m} of {g} {y}?",
    "{g}: {m} {y}",
    "How much {m} did {g} report {y}?",
]


@pytest.mark.criterion("C2")
def test_add_up_phrasings_are_a_sum():
    # oracle: Requirement 1 step 3 by hand — each question has one of the word pairs add up /
    # adding up / adds up, one universe and no company, and no aggregate_cue word.
    cases = [
        ("If we add up the semiconductor companies, what was their R&D in fiscal 2022?",
         "semiconductors"),
        ("Adding up the retailers' inventories for 2024, what do you get?", "retailers"),
        ("The retailers' revenue adds up to what in fiscal 2021?", "retailers"),
        ("Net income of the big tech companies, added up, for FY2023", "big_tech"),
    ]
    for q, universe in cases:
        assert aggregate_cue(q) is None
        assert group_aggregate_cue(q, [universe], 0) == "sum"


@pytest.mark.criterion("C2")
def test_up_without_add_is_not_a_cue():
    # oracle: Requirement 1 step 3 by hand — "up" alone (or "add" not followed by "up") is not
    # one of the word pairs and the question has no "total", so step 4 returns None; the same
    # question with "add up" is a sum.
    went_up = "Did revenue of the retailers go up in fiscal 2024?"
    sum_q = "Did revenue of the retailers add up to more in fiscal 2024?"
    assert group_aggregate_cue(went_up, ["retailers"], 0) is None
    assert group_aggregate_cue("Add the retailers' revenue for 2024", ["retailers"], 0) is None
    assert group_aggregate_cue(sum_q, ["retailers"], 0) == "sum"


@pytest.mark.criterion("C2")
@given(
    template=st.sampled_from(TEMPLATES),
    group=st.sampled_from(GROUPS),
    metric=st.sampled_from(METRICS),
    year=st.sampled_from(YEARS),
    phrase=st.sampled_from(ADD_UP),
    where=st.sampled_from(["prefix", "suffix", "middle"]),
    upper=st.booleans(),
)
def test_inserting_an_add_up_pair_makes_an_uncued_question_a_sum(template, group, metric, year,
                                                                 phrase, where, upper):
    # oracle: Requirement 1 step 3 — the base question has no cue, no "total" and no add-up
    # pair, so it is None (step 4); inserting any add-up pair as whole words makes it a sum.
    group_text, universe = group
    base = template.format(m=metric, g=group_text, y=year)
    assert aggregate_cue(base) is None
    assert group_aggregate_cue(base, [universe], 0) is None
    inserted = phrase.upper() if upper else phrase
    if where == "prefix":
        q = f"{inserted.capitalize()} {base}"
    elif where == "suffix":
        q = f"{base}, {inserted}"
    else:
        q = base.replace(group_text, f"{group_text} {inserted}", 1)
    assert group_aggregate_cue(q, [universe], 0) == "sum"
