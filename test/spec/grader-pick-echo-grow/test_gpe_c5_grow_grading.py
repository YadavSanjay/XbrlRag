"""C5: Rule B — a growth key asked "how much did X grow / increase / ..." also accepts the
absolute change (docs/specs/grader-pick-echo-grow.md, requirement 4).

Oracle: DEVA's capex legs FY2023 9,447,000,000 and FY2024 10,959,000,000 (decimals -6): the
absolute change is 10,959,000,000 - 9,447,000,000 = +1,512,000,000 and the growth
1,512 / 9,447 = 16.0% (both by hand); the reversed legs give -1,512,000,000. Answers
hand-written.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import absolute_change_reading

CHANGE = 1_512_000_000  # hand: 10,959,000,000 - 9,447,000,000
# Exact: both legs are integers below 2**53, so their float difference is exact.
EXACT = 0.0
VERBS = ("grow", "increase", "decrease", "rise", "fall", "decline")
STATED_CHANGE = ("Deva Corp's capital expenditures were 9,447,000,000 USD in FY2023 and "
                 "10,959,000,000 USD in FY2024, so they grew by 1,512,000,000 USD.")
STATED_GROWTH = "Deva Corp's capital expenditures grew 16.0% from FY2023 to FY2024."


def _checks(grade):
    return getattr(grade, "checks", None) or {}


def _dependent_ok(grade) -> bool:
    return _checks(grade).get("dependent.value") is True and _checks(grade).get("dependent") is True


def _compound_key(kit, legs=None, growth=None):
    legs = kit.LEGS if legs is None else legs
    growth = kit.GROWTH if growth is None else growth
    return kit.growth_key([*kit.RANKING_INPUTS, *legs], growth, dependent=True)


@pytest.mark.criterion("C5")
def test_how_much_did_it_grow_reads_the_signed_change(kit):
    # oracle: later - earlier of the stated legs (module docstring); the verb never flips the
    # sign: "grow" over a decline reads the negative change, "fall" over a rise the positive
    assert absolute_change_reading(kit.LG_GROW_Q, _compound_key(kit), kit.GROWTH) == \
        pytest.approx(CHANGE, abs=EXACT)
    down = _compound_key(kit, kit.DOWN_LEGS, kit.DOWN_GROWTH)
    assert absolute_change_reading(kit.LG_GROW_Q, down, kit.DOWN_GROWTH) == pytest.approx(
        -CHANGE, abs=EXACT)
    for verb in VERBS:
        q = kit.LG_GROW_Q.replace("how much did", "by how much did").replace("grow", verb)
        assert absolute_change_reading(q, _compound_key(kit), kit.GROWTH) == pytest.approx(
            CHANGE, abs=EXACT), q
        assert absolute_change_reading(q, down, kit.DOWN_GROWTH) == pytest.approx(
            -CHANGE, abs=EXACT), q


@pytest.mark.criterion("C5")
def test_a_cue_or_the_growth_noun_gives_no_reading(kit):
    # oracle: requirement 3.2/3.3 — the grow question is the applying control; "growth" (noun)
    # and each cue leave no reading
    key = _compound_key(kit)
    assert absolute_change_reading(kit.LG_GROW_Q, key, kit.GROWTH) == pytest.approx(
        CHANGE, abs=EXACT)
    assert absolute_change_reading(kit.LG_NOUN_Q, key, kit.GROWTH) is None
    for cue in ("percent", "percentage", "rate", "growth", "cagr", "%"):
        q = kit.LG_GROW_Q.replace("grow from", f"grow ({cue}) from")
        assert absolute_change_reading(q, key, kit.GROWTH) is None, cue
    single = kit.growth_key(kit.LEGS, kit.GROWTH)
    assert absolute_change_reading(kit.YOY_GROW_Q, single, kit.GROWTH) == pytest.approx(
        CHANGE, abs=EXACT)
    assert absolute_change_reading(kit.YOY_PERCENT_Q, single, kit.GROWTH) is None


@pytest.mark.criterion("C5")
def test_compound_grow_question_accepts_the_absolute_change(kit):
    # oracle: requirement 4, case (b) — the stated +1,512,000,000 is the key's absolute change,
    # so the dependent passes by that reading; under the noun "growth" only the rate counts; a
    # wrong change (1,400,000,000) never matches
    values = [9_447_000_000.0, 10_959_000_000.0, float(CHANGE)]
    g = grade_case(kit.lg_case(), kit.answer(STATED_CHANGE, values))
    assert _dependent_ok(g), g.reason
    assert g.grade is Grade.CORRECT, g.reason
    assert "absolute change" in g.reason
    noun = grade_case(kit.lg_case(kit.LG_NOUN_Q), kit.answer(STATED_CHANGE, values))
    assert _checks(noun).get("dependent.value") is False, noun.reason
    assert noun.grade is not Grade.CORRECT
    wrong_text = STATED_CHANGE.replace("1,512,000,000", "1,400,000,000")
    wrong = grade_case(kit.lg_case(), kit.answer(wrong_text, [9_447_000_000.0,
                                                              10_959_000_000.0, 1.4e9]))
    assert _checks(wrong).get("dependent.value") is False, wrong.reason


@pytest.mark.criterion("C5")
def test_the_growth_form_passes_under_either_question(kit):
    # oracle: requirement 4 — the key is tried first and wins; 16.0% is the key's percent form;
    # the absolute change under the grow question is the control that fails today
    for q in (kit.LG_GROW_Q, kit.LG_NOUN_Q):
        g = grade_case(kit.lg_case(q), kit.answer(STATED_GROWTH, [16.0]))
        assert _dependent_ok(g), (q, g.reason)
    g = grade_case(kit.lg_case(), kit.answer(STATED_CHANGE, [float(CHANGE)]))
    assert _dependent_ok(g), g.reason


@pytest.mark.criterion("C5")
def test_non_compound_grow_case(kit):
    # oracle: requirement 4, case (a) — the same reading for a compute.yoy_growth case; "By what
    # percent" carries a cue, so there the change is not accepted
    ans = kit.answer("Deva Corp's capital expenditures grew by 1,512,000,000 USD.", [float(CHANGE)])
    g = grade_case(kit.yoy_case(), ans)
    assert g.grade is Grade.CORRECT and g.passed is True, g.reason
    assert "absolute change" in g.reason
    cued = grade_case(kit.yoy_case(kit.YOY_PERCENT_Q), ans)
    assert cued.grade is not Grade.CORRECT and cued.passed is False, cued.reason


_QUESTIONS = (
    ("How much did Deva Corp's capital expenditures {verb} from fiscal 2022 to fiscal 2023?",
     True),
    ("By how much did the leader's capital expenditures {verb} from FY22?", True),
    ("By what percent did Deva Corp's capital expenditures {verb} from fiscal 2022?", False),
    ("How much did Deva Corp's capital expenditures {verb}, as a growth rate, in fiscal 2023?",
     False),
    ("Did Deva Corp's capital expenditures {verb} in fiscal 2023, and what was the growth?",
     False),
)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(a=st.integers(1, 90_000), b=st.integers(1, 90_000), which=st.sampled_from(_QUESTIONS),
       verb=st.sampled_from(VERBS), compound=st.booleans())
def test_change_accepted_exactly_under_amount_wording(kit, a, b, which, verb, compound):
    # oracle: requirement 4 — legs on the reported step (millions, decimals -6); the stated
    # absolute change (b - a, in USD) is value-correct exactly when the question has amount
    # wording and no cue; the growth in percent form is value-correct under every question
    template, offered = which
    question = template.format(verb=verb)
    lo, hi = a * 1_000_000, b * 1_000_000
    growth = (hi - lo) / lo
    legs = [kit.leg(lo, 2022), kit.leg(hi, 2023)]
    build = kit.lg_case if compound else kit.yoy_case
    case = build(question, legs=legs, growth=growth)
    change = float(hi - lo)
    by_change = grade_case(case, kit.answer(f"It changed by {change:,.0f} USD.", [change]))
    by_rate = grade_case(case, kit.answer(f"It changed by {growth * 100!r}%.", [growth * 100]))
    if compound:
        change_ok = _checks(by_change).get("dependent.value") is True
        rate_ok = _checks(by_rate).get("dependent.value") is True
    else:
        change_ok, rate_ok = by_change.value_correct, by_rate.value_correct
    if hi != lo:  # a zero change is also a zero rate
        assert change_ok is offered, (question, change, by_change.reason)
    assert rate_ok is True, (question, growth, by_rate.reason)
