"""C4: grades read neither period lengths nor ranks as produced values
(docs/specs/grader-v9-number-reading.md, requirements 2-4).

Stored answers carry ``numeric_values`` as the old reader produced them, so each answer
here lists the rank and day-count numbers among its values, as a stored run does.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case

M = 1e6


@pytest.mark.criterion("C4")
def test_a_declined_sum_listing_ranked_rows_is_refused(kit):
    # oracle: requirement 4 with grading-dont-care-and-ties requirement 4 — once the rank
    # numbers go, the declining answer lists only its rows: REFUSED, not HEDGED_MISS
    text = ("DEVA reported $500 million, ranked 1st. DEVB reported $400 million, ranked 2nd. "
            "DEVS reported $300 million, ranked 3rd. DEVC reported $200 million, ranked 4th. "
            "No total was computed, so a combined figure cannot be stated.")
    values = [500 * M, 1.0, 400 * M, 2.0, 300 * M, 3.0, 200 * M, 4.0]
    ans = kit.answer(text, values, rows=kit.MEMBERS[:4], hedged=True)
    ans.numeric_values = values
    grade = grade_case(kit.sum_case(), ans)
    assert grade.grade is Grade.REFUSED
    assert grade.value_correct is False
    assert grade.confident_wrong is False


@pytest.mark.criterion("C4")
def test_a_day_count_never_credits_a_wrong_amount(kit):
    # oracle: requirement 4 — the key is 371; the answer states $364 and the note's day
    # counts; 371 is stated only as a day count, so nothing matches the key
    case = kit.lookup_case(expected=371.0)
    ans = kit.answer("DEVA's figure was $364. " + kit.NOTE, [364.0, 364.0, 371.0])
    grade = grade_case(case, ans)
    assert grade.grade is Grade.INCORRECT
    assert grade.value_correct is False


@pytest.mark.criterion("C4")
def test_another_companys_rank_never_credits_the_subject(kit):
    # oracle: requirements 2 and 4 with peer-rank-grading — DEVS is not ranked in the answer;
    # "#3" belongs to DEVC. Without the rank numbers the values fallback reads 500 million,
    # not 3. A rank the answer gives DEVS itself still decides.
    case = kit.peer_case("DEVS", 3)
    text = ("DEVS could not be ranked for FY2025. DEVA ranked #1 with $500 million; "
            "DEVC ranked #3 with $200 million.")
    grade = grade_case(case, kit.answer(text, [1.0, 500 * M, 3.0, 200 * M]))
    assert grade.grade not in (Grade.CORRECT, Grade.PARTIAL)
    assert grade.value_correct is False
    stated = grade_case(case, kit.answer("Among the retailers DEVS ranked 3rd.", [3.0]))
    assert stated.grade is Grade.CORRECT


def _bases(kit):
    return {
        "correct lookup": (kit.lookup_case(), kit.answer("DEVA's revenue was $500 million.",
                                                         [500 * M])),
        "wrong lookup": (kit.lookup_case(), kit.answer("DEVA's revenue was $450 million.",
                                                       [450 * M])),
        "declined lookup": (kit.lookup_case(), kit.answer(
            "DEVA's revenue for fiscal 2025 is not available in the filings.", [],
            hedged=True)),
    }


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(base=st.sampled_from(["correct lookup", "wrong lookup", "declined lookup"]),
       where=st.sampled_from(["note", "screen"]))
def test_appending_a_period_length_note_changes_no_grade(kit, base, where):
    # oracle: requirement 4 — the note states no answer value, so appending it (with its day
    # counts among the values, as the old reader stored them) moves nothing
    case, ans = _bases(kit)[base]
    tail = kit.NOTE if where == "note" else kit.SCREEN_PART
    longer = kit.answer(f"{ans.text} {tail}", [*ans.numeric_values, 364.0, 371.0],
                        hedged=ans.hedged)
    a, b = grade_case(case, ans), grade_case(case, longer)
    assert (b.grade, b.value_correct, b.confident_wrong) == \
        (a.grade, a.value_correct, a.confident_wrong)
