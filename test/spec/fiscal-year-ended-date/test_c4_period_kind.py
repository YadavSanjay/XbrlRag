"""C4: the kind of period a mention asks for (docs/specs/fiscal-year-ended-date.md, req. 3).

Oracle: requirement 3's cue lists, read by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.fiscal_date import mention_period_kind


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("the fiscal year ended February 2, 2025", ("FY", None)),
        ("FY2025", ("FY", None)),
        ("the year ended Feb. 2, 2025", ("FY", None)),
        ("the twelve months ended January 31, 2025", ("FY", None)),
        ("the 53-week year ending 2025-02-02", ("FY", None)),
        ("its 10-K for 2 February 2025", ("FY", None)),
        ("the annual period to February 2, 2025", ("FY", None)),
        ("as of February 2, 2025", ("any", None)),
        ("on 2025-02-02", ("any", None)),
        ("the quarter ended May 4, 2025", ("quarter", None)),
        ("the three months ended May 4, 2025", ("quarter", None)),
        ("the 13 weeks ended May 4, 2025", ("quarter", None)),
        ("year-to-date through August 3, 2025", ("quarter", None)),
        ("the first quarter of the fiscal year ended February 1, 2026", ("quarter", "Q1")),
        ("Q3 of fiscal 2025", ("quarter", "Q3")),
        ("the 2nd quarter ended August 3, 2025", ("quarter", "Q2")),
        ("the six months ended August 3, 2025", ("quarter", "Q2")),
        ("the first half of fiscal 2025", ("quarter", "Q2")),
        ("the nine months ended November 2, 2025", ("quarter", "Q3")),
        ("the fourth quarter of the fiscal year ended February 1, 2026", ("quarter", "Q4")),
        ("the first quarter and the six months ended August 3, 2025", ("quarter", None)),
    ],
)
def test_cues_by_hand(mention, expected):
    # oracle: requirement 3, read by hand (quarter cues win over annual cues; two different
    # named quarters name none)
    assert mention_period_kind(mention) == expected


@pytest.mark.criterion("C4")
def test_cues_are_whole_words():
    # oracle: requirement 3 — "fiscally", "headquarters" and "annually" hold no whole cue word;
    # "fy" before digits is a cue
    assert mention_period_kind("headquarters move on May 4, 2025") == ("any", None)
    assert mention_period_kind("fiscally prudent on 2025-02-02") == ("any", None)
    assert mention_period_kind("fy25 ended Feb 2, 2025") == ("FY", None)


_QUARTER_CUES = {
    "first quarter": "Q1", "1st quarter": "Q1", "q1": "Q1", "second quarter": "Q2",
    "q2": "Q2", "six months": "Q2", "first half": "Q2", "third quarter": "Q3", "q3": "Q3",
    "nine months": "Q3", "fourth quarter": "Q4", "q4": "Q4", "three months": None,
    "quarter": None, "13 weeks": None, "ytd": None,
}
_ANNUAL_CUES = ["fiscal year", "fiscal", "fy", "year ended", "twelve months", "52 weeks",
                "annual", "10-k", "year-end"]
_WORDS = st.lists(st.sampled_from(["the", "of", "for", "HD", "reported", "value", "on", "as",
                                   "May 4, 2025", "2025-02-02"]), max_size=5)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(cue=st.sampled_from(sorted(_QUARTER_CUES)), annual=st.sampled_from(_ANNUAL_CUES),
       before=_WORDS, after=_WORDS)
def test_a_quarter_cue_wins_and_plain_words_change_nothing(cue, annual, before, after):
    # oracle: requirement 3 — a quarter cue gives ("quarter", its named quarter) whatever annual
    # cue or plain words surround it
    expected = ("quarter", _QUARTER_CUES[cue])
    text = " ".join([*before, cue, *after])
    assert mention_period_kind(text) == expected
    assert mention_period_kind(f"{text} of the {annual}") == expected
    assert mention_period_kind(f"{annual} {text}") == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(annual=st.sampled_from(_ANNUAL_CUES), before=_WORDS, after=_WORDS)
def test_an_annual_cue_without_a_quarter_cue_is_fy(annual, before, after):
    # oracle: requirement 3 — annual cue, no quarter cue → ("FY", None); the plain words alone
    # carry no cue → ("any", None)
    assert mention_period_kind(" ".join([*before, annual, *after])) == ("FY", None)
    assert mention_period_kind(" ".join([*before, "ended", *after])) == ("any", None)
