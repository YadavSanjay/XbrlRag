"""C3: a declining answer's aggregate is read when it is stated as the aggregate
(docs/specs/grading-stated-aggregate.md, requirement 2).

The real case is int-bd7ee7bd1bce-0231b50a (fixture; dev split, public SEC XBRL values): key
21,244,000,000 is MU's value, the middle of the seven listed rows.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import aggregate_sentences
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

ROWS = [("DEVA", 100.0), ("DEVB", 200.0), ("DEVC", 300.0)]
LISTING = "DEVA had 100, DEVB had 200, DEVC had 300."
MU = "21,244,000,000"
QCOM = "22,464,000,000"
# The fixture's median sentences that state MU's value (read by hand); the row listing
# ("... Micron (MU) 21,244,000,000 USD, ...") is not among them.
STATED_MEDIAN_SENTENCES = [
    "The median total current assets among the 7 ranked semiconductor companies for fiscal "
    "2023 was 21,244,000,000 USD.",
    "The median is the middle value of the ranked group, which is Micron's (MU) total current "
    "assets of 21,244,000,000 USD.",
    "The median total current assets across the 7 ranked semiconductor companies for fiscal "
    "2023 was 21,244,000,000 USD.",
    "The median value corresponds to Micron's (MU) total current assets of 21,244,000,000 USD "
    "at fiscal 2023 year end (period ended 2023-08-31).",
]


def _restated(text: str, old: str, new: str) -> str:
    """``text`` with ``old`` replaced by ``new`` inside the stated-median sentences only."""
    for sentence in STATED_MEDIAN_SENTENCES:
        assert sentence in text
        text = text.replace(sentence, sentence.replace(old, new))
    return text


@pytest.mark.criterion("C3")
def test_the_real_hedged_answer_is_correct(kit):
    # oracle: requirement 2 by hand — the first median sentence names the median, does not
    # decline and states one row-matching number, 21,244,000,000, which is the key
    g = grade_v2(kit.real_case(), kit.real_answer())
    assert g.grade is Grade.CORRECT
    # rel 1e-12: the matched value is the parsed amount itself, no arithmetic
    assert g.matched_value == pytest.approx(21_244_000_000.0, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_the_real_answer_stating_qcoms_value_is_kept_and_wrong(kit):
    # oracle: requirement 2 by hand — with 22,464,000,000 (QCOM's row) in every median
    # sentence, that value is kept and wrong: HEDGED_MISS; MU's value now appears only in
    # the row listing, so it is removed; the stored text stays CORRECT
    case = kit.real_case()
    text = kit.real_record()["text"]
    wrong = grade_v2(case, kit.real_answer(_restated(text, MU, QCOM)))
    right = grade_v2(case, kit.real_answer(text))
    assert wrong.grade is Grade.HEDGED_MISS
    assert wrong.value_correct is False
    assert right.grade is Grade.CORRECT


@pytest.mark.criterion("C3")
def test_a_hedged_median_stated_with_the_rows_is_correct(kit):
    # oracle: requirement 2 by hand — "The median is 200." states one row-matching number,
    # the key 200
    case = kit.agg_case("screen.median", ["DEVA", "DEVB", "DEVC"], 200.0)
    g = grade_v2(case, kit.answer("The median is 200. " + LISTING, ROWS, hedged=True))
    assert g.grade is Grade.CORRECT


@pytest.mark.criterion("C3")
def test_two_row_numbers_state_no_single_value(kit):
    # oracle: requirement 2 by hand — the sentence is an aggregate sentence but states two
    # row-matching numbers, so nothing is kept: graded as today, every value removed, REFUSED
    first = "The median is between 100 and 300."
    text = first + " " + LISTING
    case = kit.agg_case("screen.median", ["DEVA", "DEVB", "DEVC"], 200.0)
    g = grade_v2(case, kit.answer(text, ROWS, hedged=True))
    assert aggregate_sentences(text, "median") == [(0, len(first))]
    assert g.grade is Grade.REFUSED


@pytest.mark.criterion("C3")
def test_sum_cases(kit):
    # oracle: requirement 2 by hand — 600 matches no row of 100/200/300, so it is graded as
    # today (CORRECT); with rows 600 and 0, "The combined total was 600." states one
    # row-matching number, the key, so it is kept: CORRECT
    plain = "Combined, the three had 600."
    case = kit.agg_case("screen.sum", ["DEVA", "DEVB", "DEVC"], 600.0)
    g = grade_v2(case, kit.answer(plain + " " + LISTING, ROWS, hedged=True))
    assert g.grade is Grade.CORRECT
    assert aggregate_sentences(plain + " " + LISTING, "sum") == [(0, len(plain))]
    two = kit.agg_case("screen.sum", ["DEVA", "DEVB"], 600.0)
    g2 = grade_v2(two, kit.answer("The combined total was 600. DEVA had 600, DEVB had 0.",
                                  [("DEVA", 600.0), ("DEVB", 0.0)], hedged=True))
    assert g2.grade is Grade.CORRECT


def _members():
    return st.lists(st.integers(1, 9999), min_size=3, max_size=9, unique=True).map(
        lambda vs: sorted(vs, reverse=True)[: len(vs) - (1 - len(vs) % 2)])


def _setup(kit, values, pick):
    vals = [v * 1e6 for v in values]
    tickers = [f"DEV{chr(65 + i)}" for i in range(len(vals))]
    rows = list(zip(tickers, vals, strict=True))
    stated = vals[pick % len(vals)]
    case = kit.agg_case("screen.median", tickers, statistics.median(vals))
    sentence = f"The median across the retailers was {int(stated):,} USD."
    listing = ", ".join(f"{t} had {int(v):,} USD" for t, v in rows) + "."
    return case, rows, stated, sentence, listing


@pytest.mark.criterion("C3")
@given(values=_members(), pick=st.integers(0, 8))
def test_appending_the_row_listing_never_changes_the_grade(kit, values, pick):
    # oracle: requirement 2 — the median sentence states one member's value; it is kept
    # whether or not the rows are listed: CORRECT when it is the median (the key), else
    # HEDGED_MISS
    case, rows, stated, sentence, listing = _setup(kit, values, pick)
    member_values = [v for _, v in rows]
    assert len(member_values) % 2 == 1
    right = stated == statistics.median(member_values)
    expected = Grade.CORRECT if right else Grade.HEDGED_MISS
    alone = grade_v2(case, kit.answer(sentence, rows, hedged=True))
    listed = grade_v2(case, kit.answer(sentence + " " + listing, rows, hedged=True))
    assert alone.grade is expected
    assert listed.grade is expected


@pytest.mark.criterion("C3")
@given(values=_members(), listed=st.booleans())
def test_hedging_never_changes_the_grade_of_a_right_aggregate(kit, values, listed):
    # oracle: requirement 2 — a right median stated as the aggregate is CORRECT, hedged or not
    vals = [v * 1e6 for v in values]
    pick = vals.index(statistics.median(vals))
    case, rows, _, sentence, listing = _setup(kit, values, pick)
    text = sentence + (" " + listing if listed else "")
    plain = grade_v2(case, kit.answer(text, rows))
    hedged = grade_v2(case, kit.answer(text, rows, hedged=True))
    assert plain.grade is Grade.CORRECT
    assert hedged.grade is Grade.CORRECT
