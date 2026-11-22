"""C4: wiring through ``QueryDecomposer.decompose`` (docs/specs/dependent-subquery-period.md,
R3-R4).

The real decomposer and gateway run; only the provider adapter is faked, answering with a
hand-built decomposer reply (the 094b one copied from its stored trace). Expected texts and
events are Requirements 3-4 applied by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C4")
def test_c4_the_094b_reply_inherits_and_a_two_year_reply_does_not(kit):
    # oracle: Requirements 3-4 by hand — 094b states FY24 once, so sq2 inherits 2024 with one
    # decision event; the two-year question states FY24 and FY23, so nothing changes and no
    # inherit_period event is logged
    out, events = kit.decompose(kit.Q094B, kit.reply([
        kit.sq("sq1", kit.SQ1_094B), kit.sq("sq2", kit.SQ2_094B, ["sq1"])]))
    assert kit.dicts(out) == [
        {"id": "sq1", "query": kit.SQ1_094B, "depends_on": []},
        {"id": "sq2", "query": kit.SQ2_094B_INHERITED, "depends_on": ["sq1"]},
    ]
    found = kit.inherit_events(events)
    assert len(found) == 1
    assert found[0]["subquery"] == "sq2"
    assert found[0]["fiscal_year"] == 2024

    two = [kit.sq("sq1", "Which are the top 3 covered companies by total debt in FY24?"),
           kit.sq("sq2", "For <<TOP:sq1:1>>, by how much did capital expenditures change?",
                  ["sq1"])]
    out2, events2 = kit.decompose(kit.Q_TWO_YEARS, kit.reply(two))
    assert kit.dicts(out2) == kit.dicts(two)
    assert kit.inherit_events(events2) == []


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=25)
@given(year=st.integers(min_value=2018, max_value=2035), n=st.integers(min_value=1, max_value=5),
       with_independent=st.booleans())
def test_c4_any_single_year_question_reaches_the_dropped_sub_query(kit, year, n,
                                                                    with_independent):
    # oracle: metamorphic, Requirements 3-4 — the year written once in the question is appended
    # to the dependent sub-query that dropped it; the screen (states the year) and an
    # independent sub-query (not dependent) are returned as the reply wrote them; one event per
    # rewritten sub-query
    question = (f"Identify the {n} members of the covered companies with the largest Total Debt "
                f"for fiscal year {year}, and give the percentage growth in revenue of the "
                f"first-ranked one.")
    subs = [kit.sq("sq1", f"Which {n} covered companies have the largest Total Debt for "
                          f"fiscal year {year}?"),
            kit.sq("sq2", "For <<TOP:sq1:1>>, what is the percentage growth in revenue?",
                   ["sq1"])]
    if with_independent:
        subs.append(kit.sq("sq3", "What are Amazon's main logistics-related risk factors?"))
    out, events = kit.decompose(question, kit.reply(subs))
    expected = kit.dicts(subs)
    expected[1]["query"] = ("For <<TOP:sq1:1>>, what is the percentage growth in revenue for "
                            f"fiscal year {year}?")
    assert kit.dicts(out) == expected
    found = kit.inherit_events(events)
    assert [(e["subquery"], e["fiscal_year"]) for e in found] == [("sq2", year)]
