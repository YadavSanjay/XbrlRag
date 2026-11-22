"""C8: an ambiguous question is correct only when it states the required assumption(s).

Oracle: requirement 15 of docs/specs/v2-grading.md (phrases via requirement 5); every
expected verdict is read off the made-up assumptions by hand.
"""

from __future__ import annotations

import pytest

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

STATED = "Assuming the fiscal year, DEVA revenue was 5 million."


@pytest.mark.criterion("C8")
def test_assumption_stated_or_not(kit):
    # oracle: requirement 15 — "fiscal year" is the first alternative of the assumption.
    case = kit.case(kit.hx_ambiguous())
    ok = grade_v2(case, kit.answer(text=STATED, values=[5e6]))
    assert ok.grade is Grade.CORRECT and ok.passed is True
    assert kit.checks(ok) == {"assumption[0]": True}

    alt = grade_v2(case, kit.answer(text="For fiscal 2024 it was 5 million.", values=[5e6]))
    assert alt.grade is Grade.CORRECT

    silent = grade_v2(case, kit.answer(text="DEVA revenue was 5 million.", values=[5e6]))
    assert silent.grade is Grade.INCORRECT and silent.passed is False
    assert kit.checks(silent) == {"assumption[0]": False}

    refused = grade_v2(case, kit.answer(text="The question is unclear.", refused=True))
    assert refused.grade is Grade.REFUSED
    assert kit.checks(refused) == {"assumption[0]": False}


@pytest.mark.criterion("C8")
def test_every_assumption_required(kit):
    # oracle: requirement 15 — two assumptions, only the first stated: INCORRECT, index 1
    # false; both stated: CORRECT.
    case = kit.case(kit.hx_ambiguous(["fiscal year", "class A shares|class a"]))
    one = grade_v2(case, kit.answer(text=STATED, values=[5e6]))
    assert one.grade is Grade.INCORRECT
    assert kit.checks(one) == {"assumption[0]": True, "assumption[1]": False}
    both = grade_v2(case, kit.answer(text=STATED + " Counting Class A shares only.",
                                     values=[5e6]))
    assert both.grade is Grade.CORRECT
    assert kit.checks(both) == {"assumption[0]": True, "assumption[1]": True}


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@pytest.mark.parametrize("filler", ["", "DEVA is a made-up filer. ", "Numbers: 1, 2, 3. "])
def test_removing_the_assumption_sentence_fails(kit, filler):
    # oracle: requirement 15 — the same answer with and without the assumption sentence.
    case = kit.case(kit.hx_ambiguous())
    with_it = grade_v2(case, kit.answer(text=filler + "We read it as the fiscal year.",
                                        values=[5e6]))
    without = grade_v2(case, kit.answer(text=filler + "We read it plainly.", values=[5e6]))
    assert with_it.passed is True
    assert without.passed is False
    assert kit.checks(with_it) != kit.checks(without)
