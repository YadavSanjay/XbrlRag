"""C6: no rank or value from a disclosure sentence, and grader version 8
(docs/specs/grader-synthesis-error.md, requirements 4-6).

Ranked by hand: DEVA 500, DEVB 400, DEVS 300, DEVC 200, DEVD 100 (million) among the retailers;
DEVX and DEVY are not ranked. The disclosure sentence states "7 companies; 5 ranked".
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_numbers
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.honesty import grader_version
from src.eval.core.report import CaseResult, Metrics

ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


def _with_disclosure(kit, text, values, **flags):
    """The answer as the system extracts it: the sentence appended, its numbers among values."""
    full = f"{text} {kit.disclosure_text()}".strip()
    return kit.answer(full, values=list(values) + extract_numbers(kit.disclosure_text()),
                      **flags)


@pytest.mark.criterion("C6")
def test_peer_answers_beside_the_sentence(kit):
    # oracle: requirement 5 — no stated rank, no rows, and 7 and 5 are not values: nothing to
    # read, and the answer hedged
    case = kit.peer_case("DEVS", 3)
    ans = _with_disclosure(kit, "DEVS could not be ranked.", [], hedged=True)
    assert grade_case(case, ans).grade is Grade.REFUSED
    # the stated rank decides; the sentence alone states the universe (7 companies) and
    # names DEVX and DEVY, so the disclosure checks pass on it (requirement 5, last paragraph)
    grade = grade_case(case, _with_disclosure(kit, "DEVS ranked 3rd.", []))
    assert grade.grade is Grade.CORRECT
    assert grade.checks["universe"] is True
    assert grade.checks["exclusions"] is True
    assert grade_case(case, _with_disclosure(kit, "DEVS ranked 4th.", [])).grade \
        is Grade.INCORRECT


@pytest.mark.criterion("C6")
def test_an_unhedged_one_has_no_rank(kit):
    # oracle: requirement 5 and peer-rank-grading 4 — no rank read, not "ranked 7.0"
    case = kit.peer_case("DEVS", 3)
    grade = grade_case(case, _with_disclosure(kit, "DEVS is in the group.", []))
    assert grade.grade is Grade.INCORRECT
    assert grade.reason == "no rank in the answer"


@pytest.mark.criterion("C6")
def test_the_ranked_count_never_credits_a_rank(kit):
    # oracle: requirement 5 — DEVD is 5th of 5; "5 ranked" is a count, not DEVD's rank
    case = kit.peer_case("DEVD", 5)
    hedged = grade_case(case, _with_disclosure(kit, "DEVD could not be ranked.", [],
                                               hedged=True))
    assert hedged.grade is Grade.REFUSED
    assert hedged.value_correct is False
    plain = grade_case(case, _with_disclosure(kit, "DEVD is in the group.", []))
    assert plain.grade is Grade.INCORRECT
    assert plain.value_correct is False


@pytest.mark.criterion("C6")
def test_a_hedged_lookup_with_only_the_sentence_is_refused(kit):
    # oracle: requirement 4 — 7 and 5 are not produced values, so nothing was produced
    case, _ = kit.lookup_case()
    ans = _with_disclosure(kit, "DEVA's revenue could not be determined.", [], hedged=True)
    assert grade_case(case, ans).grade is Grade.REFUSED


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(kind=st.sampled_from(["lookup", "sum", "median", "peer_relative", "screen.top"]),
       own=st.sampled_from(["none", "right", "wrong"]), hedged=st.booleans())
def test_appending_the_sentence_changes_no_grade(kit, kind, own, hedged):
    # oracle: requirements 4 and 5 — the builders' texts already name the universe and every
    # exclusion, so the sentence adds no disclosure, and its numbers are not values
    case, right = kit.BUILDERS[kind]()
    if own == "right":
        text, values = right.text, list(right.numeric_values)
    elif own == "wrong":
        text, values = f"{kit.NAMED} DEVS ranked 1st; the figure was $9 million.", [1.0, 9e6]
    else:
        text, values = f"{kit.NAMED} The figure could not be determined.", []
    rows = [(r["ticker"], r["value"]) for r in right.rows] if own == "right" else None
    without = kit.answer(text, values=values, rows=rows, hedged=hedged)
    with_sentence = _with_disclosure(kit, text, values, rows=rows, hedged=hedged)
    a, b = grade_case(case, without), grade_case(case, with_sentence)
    assert (b.grade, b.value_correct, b.confident_wrong) == \
        (a.grade, a.value_correct, a.confident_wrong)


@pytest.mark.criterion("C6")
def test_grader_version_is_8(kit):
    # oracle: requirement 6 (identity), on an empty and a graded scorecard. Relaxed to "at
    # least 8" by grader-v9-number-reading (requirement 7), which pins "9".
    assert int(grader_version()) >= 8
    assert int(Metrics.compute([]).to_dict()["grader_version"]) >= 8
    case, ans = kit.BUILDERS["lookup"]()
    m = Metrics.compute([CaseResult.build(case, ans, grade_case(case, ans))])
    assert int(m.to_dict()["grader_version"]) >= 8
