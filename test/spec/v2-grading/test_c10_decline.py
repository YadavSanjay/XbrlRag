"""C10: a decline case passes on a clean decline with no number and the right reason.

Oracle: requirement 17 of docs/specs/v2-grading.md; the reason phrases are copied from its
REASON_PHRASES table.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

REASON_PHRASES = {
    "out_of_corpus": ["not in the corpus", "outside the corpus", "not covered", "no filings",
                      "not included", "not part of", "do not have", "dont have",
                      "not available", "no data"],
    "out_of_period": ["not yet filed", "not been filed", "has not filed", "hasnt filed",
                      "not yet available", "outside the period", "no data for", "not yet"],
    "not_tagged": ["not tagged", "not reported", "does not report", "doesnt report",
                   "does not disclose", "doesnt disclose", "not disclosed", "no xbrl",
                   "not separately"],
    "out_of_scope": ["outside the scope", "out of scope", "outside my scope", "not covered",
                     "proxy", "non gaap", "earnings release", "compensation", "adjusted"],
}


@pytest.mark.criterion("C10")
def test_decline_ladder(kit):
    # oracle: requirement 17 — the four outcomes for a not_tagged case.
    case = kit.case(kit.hx_decline("not_tagged"))
    ok = grade_v2(case, kit.answer(text="DEVA does not disclose that metric in XBRL.",
                                   refused=True))
    assert ok.grade is Grade.CORRECT and ok.passed is True
    assert kit.checks(ok) == {"declined": True, "no_number": True, "reason": True}

    number = grade_v2(case, kit.answer(text="It is not tagged, but about 5 million.",
                                       values=[5e6], hedged=True))
    assert number.grade is Grade.INCORRECT and number.passed is False
    assert kit.checks(number) == {"declined": True, "no_number": False, "reason": True}

    wrong_reason = grade_v2(case, kit.answer(text="That is out of scope for me.",
                                             refused=True))
    assert wrong_reason.grade is Grade.PARTIAL and wrong_reason.passed is False
    assert kit.checks(wrong_reason) == {"declined": True, "no_number": True, "reason": False}

    not_declined = grade_v2(case, kit.answer(text="DEVA does not report it."))
    assert not_declined.grade is Grade.INCORRECT
    assert kit.checks(not_declined) == {"declined": False, "no_number": True, "reason": True}

    rows = grade_v2(case, kit.answer(text="Not tagged.", refused=True,
                                     rows=[{"ticker": "DEVA", "value": 1.0}]))
    assert rows.grade is Grade.INCORRECT
    assert kit.checks(rows)["no_number"] is False


@pytest.mark.criterion("C10")
@given(data=st.data())
def test_every_reason_phrase_is_accepted(kit, data):
    # oracle: requirement 17 — for each category, a refused answer stating any of its
    # phrases (padded with neutral words) is CORRECT.
    reason = data.draw(st.sampled_from(sorted(REASON_PHRASES)))
    phrase = data.draw(st.sampled_from(REASON_PHRASES[reason]))
    pad = data.draw(st.sampled_from(["Sorry,", "For DEVA the figure is", "Unfortunately"]))
    case = kit.case(kit.hx_decline(reason))
    g = grade_v2(case, kit.answer(text=f"{pad} {phrase}.", refused=True))
    assert g.grade is Grade.CORRECT
    assert kit.checks(g) == {"declined": True, "no_number": True, "reason": True}
