"""C9: a false premise must be corrected, never affirmed.

Oracle: requirement 16 of docs/specs/v2-grading.md. The conftest correction "The synthetic
metric rose, it did not fall." has "rose" as its first direction token, so the corrected set
is UP and the premise set DOWN; every verdict below applies the token rule by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2


@pytest.mark.criterion("C9")
def test_directional_correction(kit):
    # oracle: requirement 16 — "did not fall" negates "fall" (NEG within 3 tokens before);
    # "fell because" affirms it; no UP token fails direction_stated.
    case = kit.case(kit.hx_false_premise())
    ok = grade_v2(case, kit.answer(text="Revenue did not fall; it rose 2% in FY2024."))
    assert ok.grade is Grade.CORRECT and ok.passed is True
    assert kit.checks(ok) == {"direction_stated": True, "premise_not_affirmed": True}

    contracted = grade_v2(case, kit.answer(text="It didn't fall at all. Revenue increased."))
    assert contracted.grade is Grade.CORRECT

    affirmed = grade_v2(case, kit.answer(text="Revenue fell because demand weakened."))
    assert affirmed.grade is Grade.INCORRECT and affirmed.passed is False
    assert kit.checks(affirmed) == {"direction_stated": False, "premise_not_affirmed": False}

    hedge = grade_v2(case, kit.answer(text="Revenue rose in one segment but fell overall."))
    assert hedge.grade is Grade.INCORRECT
    assert kit.checks(hedge) == {"direction_stated": True, "premise_not_affirmed": False}

    refused = grade_v2(case, kit.answer(text="I cannot say why.", refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C9")
def test_down_correction_and_non_directional(kit):
    # oracle: requirement 16 — a correction whose first direction token is "declined" makes
    # DOWN the corrected set; a correction with no direction token uses correction_stated
    # (the "|" alternatives through states_phrase).
    down = kit.case(kit.hx_false_premise("Margins declined; they did not increase."))
    g = grade_v2(down, kit.answer(text="Margins were lower, not higher."))
    assert g.grade is Grade.CORRECT
    assert kit.checks(g) == {"direction_stated": True, "premise_not_affirmed": True}

    flat = kit.case(kit.hx_false_premise("reported a profit|net income was positive"))
    ok = grade_v2(flat, kit.answer(text="In fact DEVB reported a profit that year."))
    assert ok.grade is Grade.CORRECT
    assert kit.checks(ok) == {"correction_stated": True}
    bad = grade_v2(flat, kit.answer(text="DEVB lost money that year."))
    assert bad.grade is Grade.INCORRECT
    assert kit.checks(bad) == {"correction_stated": False}


_filler = st.lists(st.sampled_from(["the", "metric", "for", "devb", "in", "fiscal", "2024",
                                    "was", "reported", "by", "segment"]),
                   min_size=4, max_size=4)


@pytest.mark.criterion("C9")
@given(filler=_filler, up=st.sampled_from(["rose", "increased", "grew", "higher"]),
       down=st.sampled_from(["fell", "decreased", "declined", "dropped"]))
def test_corrected_word_passes_unnegated_premise_fails(kit, filler, up, down):
    # oracle: requirement 16 — the fillers contain no direction or NEG token, so the verdict
    # depends only on the inserted words and the 3-token negation window.
    case = kit.case(kit.hx_false_premise())
    words = " ".join(filler)
    assert grade_v2(case, kit.answer(text=f"{words} {up}")).passed is True
    assert grade_v2(case, kit.answer(text=f"{words} {down} and {up}")).passed is False
    assert grade_v2(case, kit.answer(text=f"{words} {up}; it did not {down}")).passed is True
    # four filler tokens push the NEG token out of the 3-token window
    far = f"{up}, not {words} {down}"
    assert grade_v2(case, kit.answer(text=far)).passed is False
