"""C4: absolute_change_reading (docs/specs/pick-skips-questions.md, requirement 4).

Oracle: the change is later minus earlier of the key's stated legs, by hand:
9,447,000,000 - 10,959,000,000 = -1,512,000,000.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import absolute_change_reading

CHANGE = -1_512_000_000  # hand: 9,447,000,000 - 10,959,000,000
# grader-pick-echo-grow requirement 3.2: the verb forms of "grow" are no longer cues.
CUES = ("percent", "percentage", "percentages", "per cent", "pct", "rate", "rates", "growth",
        "cagr")
# Exact: both legs are integers below 2**53, so their float difference is exact.
EXACT = 0.0


def _compound_key(kit, inputs=None, definition="growth"):
    inputs = [*kit.RANKING_INPUTS, *kit.LEGS] if inputs is None else inputs
    return kit.growth_key(inputs, kit.GROWTH, definition=definition, dependent=True)


@pytest.mark.criterion("C4")
def test_the_real_shape_reads_the_absolute_change(kit):
    # oracle: hand subtraction of the stated legs (module docstring); the ranking inputs are
    # another concept at one year, so they form no pair
    got = absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit), kit.GROWTH)
    assert got == pytest.approx(CHANGE, abs=EXACT)
    single = kit.growth_key(kit.LEGS, kit.GROWTH)
    assert absolute_change_reading(kit.YOY_CHANGE_Q, single, kit.GROWTH) == pytest.approx(
        CHANGE, abs=EXACT)
    shuffled = [*kit.LEGS[::-1], *kit.RANKING_INPUTS[::-1]]
    assert absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit, shuffled),
                                   kit.GROWTH) == pytest.approx(CHANGE, abs=EXACT)


@pytest.mark.criterion("C4")
def test_each_percent_cue_blocks_the_reading(kit):
    # oracle: requirement 4.3 — the applying question reads the change; the same question with
    # any one cue (a word, or "%") reads nothing
    key = _compound_key(kit)
    assert absolute_change_reading(kit.LG_CHANGE_Q, key, kit.GROWTH) == pytest.approx(
        CHANGE, abs=EXACT)
    for cue in CUES:
        q = kit.LG_CHANGE_Q.replace("change from", f"change ({cue}) from")
        assert absolute_change_reading(q, key, kit.GROWTH) is None, cue
        assert absolute_change_reading(q.upper(), key, kit.GROWTH) is None, cue
    assert absolute_change_reading(kit.LG_CHANGE_Q.replace("FY23?", "FY23, in %?"), key,
                                   kit.GROWTH) is None
    assert absolute_change_reading(kit.YOY_PERCENT_Q, kit.growth_key(kit.LEGS, kit.GROWTH),
                                   kit.GROWTH) is None


@pytest.mark.criterion("C4")
def test_words_are_matched_whole_and_in_any_case(kit):
    # oracle: requirement 4.2/4.3 — "corporate"/"rated" hold no cue word, "CHANGED" is a
    # change word, "exchange" is not one
    key = kit.growth_key(kit.LEGS, kit.GROWTH)
    for q in ("By how much have Deva Corp's corporate capital expenditures changed?",
              "How much did the rated filer's capital expenditures CHANGE from fiscal 2023?",
              "What were the year-over-year changes in Deva Corp's capital expenditures?",
              # grader-pick-echo-grow requirement 4: a how-much movement is amount wording
              "How much did Deva Corp's capital expenditures grow from fiscal 2023?"):
        assert absolute_change_reading(q, key, kit.GROWTH) == pytest.approx(CHANGE, abs=EXACT), q
    for q in ("By how much did Deva Corp's capital expenditures move from fiscal 2023?",
              "What was Deva Corp's foreign exchange effect on capital expenditures?"):
        assert absolute_change_reading(q, key, kit.GROWTH) is None, q


@pytest.mark.criterion("C4")
def test_no_reading_without_a_growth_key_or_a_single_pair(kit):
    # oracle: requirement 4.1/4.4, each beside the applying variant
    assert absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit), kit.GROWTH) == \
        pytest.approx(CHANGE, abs=EXACT)
    assert absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit, definition="difference"),
                                   kit.GROWTH) is None
    assert absolute_change_reading(kit.LG_CHANGE_Q, None, kit.GROWTH) is None
    assert absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit), 0.25) is None
    assert absolute_change_reading(kit.LG_CHANGE_Q, _compound_key(kit), None) is None
    # two pairs both at -10% (100 -> 90, 200 -> 180) disagree on the change: no reading
    two = [kit.leg(100, 2023, concept="x:A"), kit.leg(90, 2024, concept="x:A"),
           kit.leg(200, 2023, concept="x:B"), kit.leg(180, 2024, concept="x:B")]
    assert absolute_change_reading(kit.LG_CHANGE_Q, kit.growth_key(two, -0.1), -0.1) is None
    # one of them alone: -10
    assert absolute_change_reading(kit.LG_CHANGE_Q, kit.growth_key(two[:2], -0.1),
                                   -0.1) == pytest.approx(-10, abs=EXACT)
    # a different unit or cik breaks the pair
    mixed_unit = [kit.LEGS[0], {**kit.LEGS[1], "unit": "EUR"}]
    assert absolute_change_reading(kit.LG_CHANGE_Q, kit.growth_key(mixed_unit, kit.GROWTH),
                                   kit.GROWTH) is None
    mixed_cik = [kit.LEGS[0], {**kit.LEGS[1], "cik": "0000000009"}]
    assert absolute_change_reading(kit.LG_CHANGE_Q, kit.growth_key(mixed_cik, kit.GROWTH),
                                   kit.GROWTH) is None


_legs = st.tuples(
    st.integers(-10**12, 10**12).filter(lambda v: v != 0),
    st.integers(-10**12, 10**12),
)


@pytest.mark.criterion("C4")
@given(legs=_legs, cue=st.sampled_from(CUES + ("%",)), at=st.integers(0, 200),
       seed=st.integers(0, 2**32 - 1))
def test_random_legs(kit, legs, cue, at, seed):
    # oracle: requirement 4 — the change is b - a (a closed form computed here); a cue anywhere
    # blocks it; the inputs' order never matters
    a, b = legs
    growth = (b - a) / abs(a)
    inputs = [*kit.RANKING_INPUTS, kit.leg(a, 2022), kit.leg(b, 2023)]
    key = kit.growth_key(inputs, growth, dependent=True)
    # rel 1e-12: the closed form is a float subtraction of the same two integers
    assert absolute_change_reading(kit.LG_CHANGE_Q, key, growth) == pytest.approx(
        b - a, rel=1e-12, abs=0.5)
    shuffled = list(inputs)
    random.Random(seed).shuffle(shuffled)
    assert absolute_change_reading(kit.LG_CHANGE_Q, kit.growth_key(shuffled, growth),
                                   growth) == pytest.approx(b - a, rel=1e-12, abs=0.5)
    q = kit.LG_CHANGE_Q
    cut = min(at, len(q))
    while 0 < cut < len(q) and q[cut - 1].isalnum():
        cut -= 1  # insert at a word boundary
    blocked = f"{q[:cut]} {cue} {q[cut:]}"
    assert absolute_change_reading(blocked, key, growth) is None, blocked
