"""C7: precision = right / cited, recall = evidenced key slots / key slots, over every shape."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import grade_v2_grounding

AAA = 1000
FY = ("2025-01-01", "2025-12-31")


def _pr(g):
    return (g["precision"], g["recall"])


def _ratio(gb):
    num = gb.inp(AAA, "us-gaap:NetIncomeLoss", "USD", *FY, 8000000000)
    den = gb.inp(AAA, "us-gaap:Revenues", "USD", *FY, 100000000000)
    c = gb.case("c-ratio", [num, den], shape="compute.ratio", kind="compute")
    facts = {
        1: gb.rec(1, AAA, "NetIncomeLoss", "USD", *FY, 8000000000),
        2: gb.rec(2, AAA, "Revenues", "USD", *FY, 100000000000),
        3: gb.rec(3, AAA, "OperatingIncomeLoss", "USD", *FY, 9000000000),  # unrelated
    }
    return c, facts


def _screen(gb, n=7):
    inputs = [gb.inp(2001 + k, "us-gaap:Revenues", "USD", *FY, (n - k) * 10**9)
              for k in range(n)]
    c = gb.case("c-screen", inputs, shape="screen.top", kind="screen", expected_value=None)
    facts = {10 + k: gb.rec(10 + k, 2001 + k, "Revenues", "USD", *FY, (n - k) * 10**9)
             for k in range(n)}
    return c, facts


@pytest.mark.criterion("C7")
def test_ratio_counts(gb):
    # oracle: requirement 9, hand counts
    c, facts = _ratio(gb)
    g = grade_v2_grounding(c, [1, 2], facts)
    assert (*_pr(g), g["evidenced"]) == (1, 1, (0, 1))
    g = grade_v2_grounding(c, [1, 3], facts)
    assert (*_pr(g), g["right"], g["wrong"]) == (0.5, 0.5, (1,), (3,))
    g = grade_v2_grounding(c, [2, 1, 2, 1], facts)  # repeats count once, first first
    assert (*_pr(g), g["right"]) == (1, 1, (2, 1))


@pytest.mark.criterion("C7")
def test_screen_counts_and_an_unresolved_id(gb):
    # oracle: requirement 9 — three of seven ranked filers cited: 1 and 3/7; an id the store
    # does not know is cited and wrong: 3/4 = 0.75
    c, facts = _screen(gb)
    g = grade_v2_grounding(c, [10, 11, 12], facts)
    assert g["precision"] == 1
    assert g["recall"] == pytest.approx(3 / 7, rel=1e-12)  # 3/7 is not exact in binary
    g = grade_v2_grounding(c, [10, 11, 99, 12], facts)
    assert g["precision"] == pytest.approx(0.75, rel=1e-12)  # 3/4, a ratio of counts
    assert g["recall"] == pytest.approx(3 / 7, rel=1e-12)  # as above
    assert (g["right"], g["wrong"], g["unresolved"]) == ((10, 11, 12), (99,), (99,))


@pytest.mark.criterion("C7")
def test_compound_uses_the_top_level_key(gb):
    # oracle: requirement 9 — 7 ranking inputs plus the leader's two dependent inputs = 9 slots;
    # the three top rows and both dependent facts cited: 5 right of 5, recall 5/9
    rank = [gb.inp(3001 + k, "us-gaap:StockholdersEquity", "USD", None, "2023-12-31",
                   (9 - k) * 10**10) for k in range(7)]
    dep = [gb.inp(3001, "us-gaap:ComprehensiveIncomeNetOfTax", "USD", "2022-01-01",
                  "2022-12-31", 60000000000),
           gb.inp(3001, "us-gaap:ComprehensiveIncomeNetOfTax", "USD", "2023-01-01",
                  "2023-12-31", 85000000000)]
    c = gb.case("c-compound", rank + dep, shape="compound.leader_growth", kind="screen",
                expected_value=None)
    facts = {20 + k: gb.rec(20 + k, 3001 + k, "StockholdersEquity", "USD", None,
                            "2023-12-31", (9 - k) * 10**10) for k in range(3)}
    facts[30] = gb.rec(30, 3001, "ComprehensiveIncomeNetOfTax", "USD", "2022-01-01",
                       "2022-12-31", 60000000000)
    facts[31] = gb.rec(31, 3001, "ComprehensiveIncomeNetOfTax", "USD", "2023-01-01",
                       "2023-12-31", 85000000000)
    g = grade_v2_grounding(c, [20, 21, 22, 30, 31], facts)
    assert (g["precision"], g["evidenced"]) == (1, (0, 1, 2, 7, 8))
    assert g["recall"] == pytest.approx(5 / 9, rel=1e-12)  # 5/9 is not exact in binary


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(data=st.data())
def test_permuting_citations_changes_nothing(gb, data):
    # oracle: requirement 9 — precision and recall are counts over a set of cited ids
    c, facts = _screen(gb)
    pool = [10, 11, 12, 13, 14, 15, 16, 98, 99]
    facts[98] = gb.rec(98, 2001, "Revenues", "USD", *FY, 1)  # the right element, wrong value
    cited = data.draw(st.lists(st.sampled_from(pool), min_size=1, max_size=9, unique=True))
    shuffled = data.draw(st.permutations(cited))
    a = grade_v2_grounding(c, cited, facts)
    b = grade_v2_grounding(c, shuffled, facts)
    assert (*_pr(a), a["evidenced"]) == (*_pr(b), b["evidenced"])
    assert sorted(a["right"]) == sorted(b["right"])
    assert sorted(a["wrong"]) == sorted(b["wrong"])
    right = len([i for i in cited if 10 <= i <= 16])
    assert a["precision"] == pytest.approx(right / len(cited), rel=1e-12)  # a ratio of counts
    assert a["recall"] == pytest.approx(right / 7, rel=1e-12)  # a ratio of counts


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(st.lists(st.integers(10, 16), max_size=7, unique=True), st.integers(10, 16))
def test_unrelated_lowers_and_right_never_lowers(gb, cited, extra):
    # oracle: requirement 9 — r/(n+1) < r/n when r > 0; (r+1)/(n+1) >= r/n
    c, facts = _screen(gb)
    facts[50] = gb.rec(50, 2001, "CostOfRevenue", "USD", *FY, 7000000000)  # unrelated fact
    base = grade_v2_grounding(c, cited, facts)
    plus_wrong = grade_v2_grounding(c, [*cited, 50], facts)
    assert plus_wrong["recall"] == base["recall"]
    assert plus_wrong["wrong"][-1:] == (50,)
    if cited:
        assert plus_wrong["precision"] < base["precision"]
    else:
        assert plus_wrong["precision"] == 0
    if extra not in cited:
        plus_right = grade_v2_grounding(c, [*cited, extra], facts)
        assert plus_right["precision"] == 1
        assert plus_right["recall"] > base["recall"]
