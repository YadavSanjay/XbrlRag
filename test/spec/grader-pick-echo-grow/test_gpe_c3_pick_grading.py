"""C3: Rule A in grading — a compound.rank_by_ratio answer that echoes its sub-questions in the
imperative is graded on its own words (docs/specs/grader-pick-echo-grow.md, requirement 2).

Oracle: hand-written answers; the winner DEVB at 1.35 is the hand-keyed fixture's.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case


def _checks(grade):
    return getattr(grade, "checks", None) or {}


@pytest.mark.criterion("C3")
def test_the_real_shape_is_correct(kit):
    # oracle: the answer names DEVB as the highest and states DEVB's 1.35: pick and value both
    # hold, so the only part passes and the case is CORRECT, whatever the echo ends with
    case = kit.pick_case()
    for ending in (".", "", "…"):
        g = grade_case(case, kit.answer(kit.real_shape(ending), kit.PICK_VALUES))
        checks = _checks(g)
        assert checks.get("dependent.pick") is True, (ending, g.reason)
        assert checks.get("dependent.value") is True, g.reason
        assert g.grade is Grade.CORRECT and g.passed is True, g.reason


@pytest.mark.criterion("C3")
def test_an_answer_naming_another_filer_fails_the_pick(kit):
    # oracle: the echo names DEVB nearest before "highest", the answer names DEVC as the highest
    # while DEVB's 1.35 is still stated: the pick fails; the real shape is the passing control
    case = kit.pick_case()
    text = (kit.RANKING_ECHO + kit.RANKING_ANSWER + kit.pick_echo(".", ("DEVA", "DEVC", "DEVB"))
            + "Devc Corp (DEVC) had the highest working capital ratio, at 0.83. DEVB's working "
              "capital ratio was 1.35.")
    g = grade_case(case, kit.answer(text, [0.83, 1.35]))
    assert _checks(g).get("dependent.pick") is False, g.reason
    assert g.grade is not Grade.CORRECT and g.passed is False
    control = grade_case(case, kit.answer(kit.real_shape(), kit.PICK_VALUES))
    assert _checks(control).get("dependent.pick") is True, control.reason


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(n=st.integers(1, 3), order=st.permutations(("DEVA", "DEVC")),
       ending=st.sampled_from((".", "", "…", "?")))
def test_prefixed_echo_lines_never_change_the_grade(kit, n, order, ending):
    # oracle: requirement 2 — echo lines are never read for the pick, so prefixing them leaves
    # the grade and every check as the bare answer's (CORRECT, by hand, above)
    case = kit.pick_case()
    heads = "".join(kit.pick_echo(ending, ("DEVB", *order), k) for k in range(1, n + 1))
    bare = grade_case(case, kit.answer(kit.PICK_ANSWER, kit.PICK_VALUES))
    prefixed = grade_case(case, kit.answer(heads + kit.PICK_ANSWER, kit.PICK_VALUES))
    assert bare.grade is Grade.CORRECT, bare.reason
    assert prefixed.grade is bare.grade, prefixed.reason
    assert _checks(prefixed) == _checks(bare)
