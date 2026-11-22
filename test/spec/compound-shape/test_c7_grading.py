"""C7: a generated compound case is graded by the compound grader: CORRECT only when both
the ranking and the dependent part pass, with per-part checks.

Oracle: requirement 6 of docs/specs/compound-shape.md and v2-grading requirement 14;
hand-written answers against the hand-keyed fixture cases (top 3 by FY2024 revenue DEVA
500m, DEVB 400m, DEVC 300m of 5 members, DEVE excluded; operating-margin winner DEVB at
0.3; revenue leader DEVA's net income growth 0.2).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

M = 1e6
ROWS = [{"ticker": "DEVA", "value": 500 * M, "fact_id": 1},
        {"ticker": "DEVB", "value": 400 * M, "fact_id": 2},
        {"ticker": "DEVC", "value": 300 * M, "fact_id": 3}]
# A wrong set (DEVA left out, the fourth-ranked DEVD in): wrong under any grader. A reversed
# list of the right three is right since prose-and-ranking-names requirement 2 (names only).
WRONG_SET = [{"ticker": "DEVB", "value": 400 * M, "fact_id": 2},
             {"ticker": "DEVC", "value": 300 * M, "fact_id": 3},
             {"ticker": "DEVD", "value": 200 * M, "fact_id": 6}]
RANKING = ("Among the 5 covered companies, DEVA, DEVB and DEVC had the most revenue in "
           "fiscal 2024; DEVE could not be ranked. ")
NO_UNIVERSE = "DEVA, DEVB and DEVC had the most revenue in fiscal 2024; DEVE was left out. "
WINNER = "Of these, DEVB had the highest operating margin, at 30.0%."
GROWTH = "DEVA's net income grew 20.0% from fiscal 2023."


def _answer(text, values, rows=ROWS):
    return SystemAnswer(text=text, numeric_values=list(values), rows=list(rows),
                        fact_ids=[1, 2, 3, 4, 5], refused=False, hedged=False, error=None)


def _case(kit, kw):
    return EvalCase.from_dict(kit.case_of(kit.build(), kw))


def _checks(grade):
    return getattr(grade, "checks", None) or {}


@pytest.mark.criterion("C7")
def test_correct_answers_pass_in_either_form(kit):
    # oracle: requirement 6 — right ranking with universe and exclusion stated, and the
    # dependent right in percent or decimal form: CORRECT, both parts' checks reported
    rbr = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
    lg = _case(kit, kit.lg(2024, "Revenue", "NetIncome"))
    decimal_winner = "Of these, DEVB had the highest operating margin, at 0.300."
    decimal_growth = "DEVA's net income growth from fiscal 2023 was 0.200."
    for case, forms in ((rbr, ((WINNER, 30.0), (decimal_winner, 0.3))),
                        (lg, ((GROWTH, 20.0), (decimal_growth, 0.2)))):
        for sentence, value in forms:
            text = RANKING + sentence
            g = grade_case(case, _answer(text, [500 * M, 400 * M, 300 * M, value]))
            assert g.grade is Grade.CORRECT, (case.origin, value, g.reason)
            checks = _checks(g)
            assert checks["ranking"] is True and checks["dependent"] is True
            assert checks["ranking.universe"] is True
            assert checks["ranking.exclusions"] is True


@pytest.mark.criterion("C7")
def test_wrong_dependent_fails_the_case(kit):
    # oracle: requirement 6 — the right ranking with a wrong ratio (25.0% is DEVC's), or
    # with DEVC named as the highest while DEVB's 30.0% appears elsewhere, or a wrong growth
    rbr = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
    lg = _case(kit, kit.lg(2024, "Revenue", "NetIncome"))
    wrong_value = RANKING + "Of these, DEVB had the highest operating margin, at 25.0%."
    wrong_winner = (RANKING + "Of these, DEVC had the highest operating margin, at 25.0%. "
                    "DEVB's operating margin was 30.0%.")
    for case, text, values in (
            (rbr, wrong_value, [25.0]),
            (rbr, wrong_winner, [25.0, 30.0]),
            (lg, RANKING + "DEVA's net income grew 25.0% from fiscal 2023.", [25.0])):
        g = grade_case(case, _answer(text, [500 * M, 400 * M, 300 * M, *values]))
        assert g.grade is not Grade.CORRECT and g.passed is False, text
        checks = _checks(g)
        assert checks["dependent"] is False, text
        assert checks["ranking"] is True, text


@pytest.mark.criterion("C7")
def test_ranking_restated_with_a_superlative_is_not_the_pick(kit):
    # oracle: requirement 6 as amended 2026-11-10 — the pick sentence is the first one with a
    # superlative AND the ratio's name. Here the ranking sentence also says "largest" (the
    # question's own wording) and names DEVA first; the pick sentence names DEVB, the winner,
    # so the answer is CORRECT. With the pick sentence naming DEVC instead it is not.
    rbr = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
    ranking = ("Among the 5 covered companies, the three largest by revenue in fiscal 2024 were "
               "DEVA, DEVB and DEVC; DEVE could not be ranked. ")
    right = ranking + "Of these, DEVB had the highest operating margin, at 30.0%."
    g = grade_case(rbr, _answer(right, [500 * M, 400 * M, 300 * M, 30.0]))
    assert g.grade is Grade.CORRECT, g.reason
    assert _checks(g)["dependent.pick"] is True
    wrong = (ranking + "Of these, DEVC had the highest operating margin, at 25.0%. "
             "DEVB's operating margin was 30.0%.")
    g = grade_case(rbr, _answer(wrong, [500 * M, 400 * M, 300 * M, 25.0, 30.0]))
    assert g.grade is not Grade.CORRECT
    assert _checks(g)["dependent.pick"] is False


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_pick_falls_back_to_the_last_superlative_sentence(kit):
    # oracle: requirement 6 as amended — when no sentence names the ratio, the pick sentence
    # is the LAST one with a superlative; prefixing a superlative ranking sentence to the
    # answer never changes the pick.
    rbr = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
    tail = "On that measure DEVB led, at 30.0%."
    for prefix in ("", "DEVA was the largest of the three by revenue. ",
                   "The top three by revenue were DEVA, DEVB and DEVC. "):
        text = RANKING + prefix + tail
        g = grade_case(rbr, _answer(text, [500 * M, 400 * M, 300 * M, 30.0]))
        assert _checks(g)["dependent.pick"] is True, prefix


@pytest.mark.criterion("C7")
def test_missing_universe_statement_fails_the_ranking(kit):
    # oracle: requirement 6 — no member count, no universe name and DEVD never named: the
    # universe check fails, so the case is not CORRECT though the dependent is right
    rbr = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
    g = grade_case(rbr, _answer(NO_UNIVERSE + WINNER, [500 * M, 400 * M, 300 * M, 30.0]))
    assert g.grade is not Grade.CORRECT
    checks = _checks(g)
    assert checks["ranking.universe"] is False
    assert checks["dependent"] is True


@pytest.mark.criterion("C7")
@settings(max_examples=8)
@given(ranking_ok=st.booleans(), dependent_ok=st.booleans(), lead=st.booleans())
def test_correct_exactly_when_both_parts_are(kit, ranking_ok, dependent_ok, lead):
    # oracle: v2-grading requirement 14 — CORRECT iff every part is; a wrong-set ranking and
    # a wrong dependent value each fail their part
    if lead:
        case = _case(kit, kit.lg(2024, "Revenue", "NetIncome"))
        text, good, bad = RANKING + GROWTH, 20.0, 25.0
    else:
        case = _case(kit, kit.rbr(2024, "Revenue", "operating_margin"))
        text, good, bad = RANKING + WINNER, 30.0, 25.0
    values = [500 * M, 400 * M, 300 * M, good if dependent_ok else bad]
    if not dependent_ok:
        text = text.replace(f"{good:.1f}%", f"{bad:.1f}%")
    g = grade_case(case, _answer(text, values, ROWS if ranking_ok else WRONG_SET))
    both = ranking_ok and dependent_ok
    assert g.passed is both
    assert (g.grade is Grade.CORRECT) is both
    assert _checks(g)["ranking"] is ranking_ok
    assert _checks(g)["dependent"] is dependent_ok
