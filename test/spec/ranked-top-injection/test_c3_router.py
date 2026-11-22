"""C3: the router removes a rank label before resolving a company mention
(docs/specs/ranked-top-injection.md, Requirements 2-3)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

QUESTION = ("What was the revenue of the company ranked first among AAA (#1), BBB (#2) in fiscal "
            "year 2024?")


def _reply(mentions):
    return json.dumps({"intent": "NUMERIC_LOOKUP", "company_mentions": mentions,
                       "period_mentions": ["fiscal year 2024"], "concept_phrases": ["revenue"],
                       "dimension_hint_present": False})


@pytest.mark.criterion("C3")
def test_a_labelled_mention_reaches_the_resolver_as_its_ticker(h):
    # oracle: Requirements 2-3 by hand — "AAA (#1)" without its label is "AAA", which resolves
    router, _, resolver = h.router_with(_reply(["AAA (#1)"]))
    result = router.route(QUESTION)
    assert resolver.mentions == [["AAA"]]
    assert [c.ticker for c in result.companies] == ["AAA"]


@pytest.mark.criterion("C3")
def test_labelled_and_plain_mentions_keep_their_order(h):
    # oracle: Requirements 2-3 by hand — each mention stripped of its label, in order
    router, _, resolver = h.router_with(_reply(["BBB (#2)", "CCC"]))
    result = router.route(QUESTION)
    assert resolver.mentions == [["BBB", "CCC"]]
    assert [c.ticker for c in result.companies] == ["BBB", "CCC"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=25, deadline=None)
@given(picks=st.lists(st.tuples(st.sampled_from(["AAA", "BBB", "CCC", "DDD", "EEE"]),
                                st.one_of(st.none(), st.integers(1, 20))),
                      min_size=1, max_size=4, unique_by=lambda p: p[0]))
def test_any_mix_of_labelled_mentions_resolves(h, picks):
    # oracle: Requirements 2-3 by hand — a label "(#k)" is removed, a plain ticker passes as is;
    # the labelled and plain forms of the same list reach the resolver identically
    lifted = [t if k is None else f"{t} (#{k})" for t, k in picks]
    expected = [t for t, _ in picks]
    router, _, resolver = h.router_with(_reply(lifted))
    result = router.route(QUESTION)
    assert resolver.mentions == [expected]
    assert [c.ticker for c in result.companies] == expected
