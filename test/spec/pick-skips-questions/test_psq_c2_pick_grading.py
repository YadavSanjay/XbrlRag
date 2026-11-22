"""C2: Rule A in grading — a compound.rank_by_ratio answer that echoes its sub-questions is
graded on its own words (docs/specs/pick-skips-questions.md, requirement 3; compound-shape
requirement 6 as amended).

Oracle: hand-written answers; the winner DEVA at 0.315 is the hand-keyed fixture's.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case


def _checks(grade):
    return getattr(grade, "checks", None) or {}


@pytest.mark.criterion("C2")
def test_the_three_real_shapes_are_correct(kit):
    # oracle: each answer names DEVA as the highest and states DEVA's 0.315 (31.5%): pick and
    # value both hold, so the only part passes and the case is CORRECT
    case = kit.pick_case()
    for text, values in zip(kit.SHAPES, kit.SHAPE_VALUES, strict=True):
        g = grade_case(case, kit.answer(text, values))
        checks = _checks(g)
        assert checks.get("dependent.pick") is True, (g.reason, text[-200:])
        assert checks.get("dependent.value") is True, g.reason
        assert checks.get("dependent") is True
        assert g.grade is Grade.CORRECT and g.passed is True, g.reason


@pytest.mark.criterion("C2")
def test_an_answer_naming_another_filer_fails_the_pick(kit):
    # oracle: the question names DEVA nearest before "highest", the answer names DEVC as the
    # highest while DEVA's 0.315 is still stated: the pick fails, so the case is not CORRECT
    case = kit.pick_case()
    text = (kit.RANKING_PART + "[sq2] Among DEVB, DEVC, DEVA, which company has the highest "
            "operating profit margin for fiscal 2024?\nDEVC had the highest operating profit "
            "margin, at 0.289. DEVA's operating profit margin was 0.315.")
    g = grade_case(case, kit.answer(text, [0.289, 0.315]))
    assert _checks(g).get("dependent.pick") is False, g.reason
    assert g.grade is not Grade.CORRECT and g.passed is False


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(n=st.integers(1, 3), shape=st.integers(0, 2), order=st.permutations(("DEVB", "DEVC")))
def test_prefixed_question_lines_never_change_the_grade(kit, n, shape, order):
    # oracle: requirement 3 — question lines are never the pick sentence, so prefixing them
    # leaves the grade and every check as the bare answer's (CORRECT, by hand, above)
    case = kit.pick_case()
    body = kit.SHAPES[shape].split("[sq2]", 1)[1].split("\n", 1)[1]
    values = kit.SHAPE_VALUES[shape]
    heads = "".join(f"[sq{k}] Among DEVA, {', '.join(order)}, which company has the highest "
                    f"operating profit margin?\n" for k in range(1, n + 1))
    bare = grade_case(case, kit.answer(body, values))
    prefixed = grade_case(case, kit.answer(heads + body, values))
    assert bare.grade is Grade.CORRECT, bare.reason
    assert prefixed.grade is bare.grade, prefixed.reason
    assert _checks(prefixed) == _checks(bare)
