"""C1: aggregate_sentences (docs/specs/grading-stated-aggregate.md, requirement 1).

Expected sentences are hand-listed by reading the committed fixture text; spans in the short
texts are counted by hand (or located by ``str.index`` / ``len`` on hand-written pieces).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import aggregate_sentences

# The fixture text's sentences that name the median and do not decline (read by hand).
REAL_MEDIAN = [
    "The median total current assets among the 7 ranked semiconductor companies for fiscal "
    "2023 was 21,244,000,000 USD.",
    "The median is the middle value of the ranked group, which is Micron's (MU) total current "
    "assets of 21,244,000,000 USD.",
    "Median of 7 companies: INTC, NVDA, QCOM, MU, AVGO, AMD, TXN (middle: MU).",
    "The median total current assets across the 7 ranked semiconductor companies for fiscal "
    "2023 was 21,244,000,000 USD.",
    "The median value corresponds to Micron's (MU) total current assets of 21,244,000,000 USD "
    "at fiscal 2023 year end (period ended 2023-08-31).",
]
# The fixture text's sentences with a sum word ("aggregate") and no decline word.
REAL_SUM = [
    "The aggregate covers 7 of the 8 semiconductor universe members; TSM was excluded because "
    "it reports in TWD rather than USD.",
    "The aggregate covers 7 of the 8 members of the semiconductor universe (INTC, NVDA, QCOM, "
    "MU, AVGO, AMD, TXN); TSM was excluded because it reports in TWD (currency).",
]

MEDIAN_200 = "The median is 200."  # 18 characters

SUM_PHRASES = [
    "The sum was 600.", "Combined, they had 600.", "The aggregate was 600.",
    "Altogether they had 600.", "Together they had 600.", "In total they had 600.",
    "They had a total of 600.", "Revenue totaled 600.", "Revenue totalled 600.",
    "Totaling 600, revenue rose.", "Totalling 600, revenue rose.",
    "The members add up to 600.", "The list adds up to 600.", "COMBINED REVENUE WAS 600.",
]
NOT_SUM_PHRASES = ["Total revenue was 600.", "Total current assets were 600.",
                   "The totals were 600."]
DECLINES = [
    "The median was not computed.", "There is no median for 200.",
    "The median cannot be stated.", "The median can't be stated.",
    "The median couldn't be stated.", "We are unable to give the median.",
    "The median is unavailable.", "Data is insufficient for a median.",
    "The median is n/a.", "The median is shown without TSM.", "The median isn't 200.",
    "The MEDIAN was NOT computed.",
]


def _texts(text: str, spans: list[tuple[int, int]]) -> list[str]:
    return [text[s:e] for s, e in spans]


@pytest.mark.criterion("C1")
def test_the_real_answers_median_sentences(kit):
    # oracle: requirement 1 by hand — the five fixture sentences containing "median" with no
    # decline word, in order ("Not ranked: ..." and "Note that ..." carry no aggregate word)
    text = kit.real_record()["text"]
    assert _texts(text, aggregate_sentences(text, "median")) == REAL_MEDIAN


@pytest.mark.criterion("C1")
def test_the_real_answers_sum_sentences(kit):
    # oracle: requirement 1 by hand — "aggregate" is a sum word; bare "total" ("total current
    # assets") is not, so only the two "The aggregate covers ..." sentences
    text = kit.real_record()["text"]
    assert _texts(text, aggregate_sentences(text, "sum")) == REAL_SUM


@pytest.mark.criterion("C1")
def test_a_declined_median_is_not_an_aggregate_sentence():
    # oracle: requirement 1 by hand — "not" declines, so the spec's sentence gives no span; the
    # appended plain median sentence is the only span
    declined = "The median could not be computed; MU reported 21,244,000,000 USD."
    assert aggregate_sentences(declined, "median") == []
    text = declined + " " + MEDIAN_200
    start = len(declined) + 1
    assert aggregate_sentences(text, "median") == [(start, start + 18)]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("declined", DECLINES)
def test_every_decline_word_declines(declined):
    # oracle: requirement 1 by hand — each sentence has a listed decline word (case-insensitive),
    # so only the appended "The median is 200." (18 characters) is a span
    text = declined + " " + MEDIAN_200
    start = len(declined) + 1
    assert aggregate_sentences(text, "median") == [(start, start + 18)]


@pytest.mark.criterion("C1")
def test_combined_is_a_sum_word_and_bare_total_is_not():
    # oracle: requirement 1 by hand — the spec's two sentences: "Combined revenue was $5.2
    # billion." (34 characters) is one sum span and no median span; "Total revenue ..." is none
    combined = "Combined revenue was $5.2 billion."
    assert aggregate_sentences(combined, "sum") == [(0, 34)]
    assert aggregate_sentences(combined, "median") == []
    assert aggregate_sentences("Total revenue was $5.2 billion.", "sum") == []


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("phrase", SUM_PHRASES)
def test_every_sum_word_names_the_sum(phrase):
    # oracle: requirement 1 by hand — each sentence carries one listed sum word or phrase
    # (case-insensitive) and no decline word: one span, the whole sentence; none for median
    assert aggregate_sentences(phrase, "sum") == [(0, len(phrase))]
    assert aggregate_sentences(phrase, "median") == []


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("phrase", NOT_SUM_PHRASES)
def test_bare_total_is_not_a_sum_word(phrase):
    # oracle: requirement 1 by hand — "total" / "totals" are not sum words; the appended "The
    # sum is 3." (13 characters) is the only span
    text = phrase + " The sum is 3."
    start = len(phrase) + 1
    assert aggregate_sentences(text, "sum") == [(start, start + 13)]


@pytest.mark.criterion("C1")
def test_a_decimal_point_does_not_split():
    # oracle: requirement 1 by hand — "The median is 1.5." is 18 characters, "The sum is 3."
    # runs 19..32; a "." inside 21,244,000,000.5 does not end the sentence
    text = "The median is 1.5. The sum is 3."
    assert aggregate_sentences(text, "median") == [(0, 18)]
    assert aggregate_sentences(text, "sum") == [(19, 32)]
    long = "The median is 21,244,000,000.5 USD. Done."
    assert aggregate_sentences(long, "median") == [(0, long.index(" Done"))]


@pytest.mark.criterion("C1")
def test_a_newline_splits_and_whitespace_is_excluded():
    # oracle: requirement 1 by hand — the newline ends "The median is 200" (17 characters);
    # "  The sum is 600!  " is trimmed to its 15 characters, and "?" ends a sentence too
    text = "The median is 200\nDEVA had 200.\n  The sum is 600!  Was the median 5?"
    assert aggregate_sentences(text, "median") == [(0, 17), (text.index("Was"), len(text))]
    assert aggregate_sentences(text, "sum") == [(text.index("The sum"),
                                                 text.index("The sum") + 15)]


@pytest.mark.criterion("C1")
def test_other_sub_kinds_have_no_aggregate_sentences():
    # oracle: requirement 1 by hand — only median and sum name an aggregate
    text = "The median is 200. The sum is 600."
    assert aggregate_sentences(text, "rank") == []
    assert aggregate_sentences(text, "count") == []
    assert aggregate_sentences(text, "median") == [(0, 18)]
    assert aggregate_sentences(text, "sum") == [(19, 34)]


# Sentences labelled by hand: (text, names the median, names the sum), none declining.
POOL = [
    ("The median was 21,244,000,000 USD.", True, False),
    ("The median is 1.5 times the mean.", True, False),
    ("Was the median 200?", True, False),
    ("The combined total was 600 USD.", False, True),
    ("In total, the three reported 600.", False, True),
    ("Altogether the members had 600!", False, True),
]
NEUTRAL = [
    "MU reported 21,244,000,000 USD.",
    "Total revenue was 5.2 billion.",
    "Intel (INTC) 43,269,000,000 USD, and AMD 16,768,000,000 USD.",
    "Fiscal years end on different dates: INTC 2023-12-30, NVDA 2023-01-29.",
    "The median could not be computed.",
    "The sum is unavailable.",
]
SEPARATORS = [" ", "\n", "  ", " \n "]


def _sentences():
    agg = st.sampled_from(POOL)
    neutral = st.sampled_from(NEUTRAL).map(lambda s: (s, False, False))
    return st.tuples(agg, st.lists(st.one_of(agg, neutral), max_size=5)).map(
        lambda t: [t[0], *t[1]])


def _join(parts, seps):
    out = parts[0][0]
    for (sentence, _, _), sep in zip(parts[1:], seps, strict=False):
        out += sep + sentence
    return out


@pytest.mark.criterion("C1")
@given(parts=_sentences(), seps=st.lists(st.sampled_from(SEPARATORS), min_size=6, max_size=6),
       sub_kind=st.sampled_from(["median", "sum"]))
def test_spans_are_the_labelled_sentences_in_order(parts, seps, sub_kind):
    # oracle: requirement 1 — the hand labels of POOL say which sentences name the aggregate;
    # spans are in order, disjoint, inside the text and trimmed of whitespace
    text = _join(parts, seps)
    spans = aggregate_sentences(text, sub_kind)
    col = 1 if sub_kind == "median" else 2
    assert _texts(text, spans) == [p[0] for p in parts if p[col]]
    previous_end = 0
    for s, e in spans:
        assert previous_end <= s < e <= len(text)
        assert text[s:e] == text[s:e].strip()
        previous_end = e


@pytest.mark.criterion("C1")
@given(parts=_sentences(), extra=st.sampled_from(NEUTRAL), at=st.integers(0, 6))
def test_a_sentence_without_an_aggregate_word_adds_no_span(parts, extra, at):
    # oracle: requirement 1 — the inserted sentence names no aggregate (or declines), so the
    # span sentences are unchanged; the median spans are the hand-labelled ones
    before = " ".join(p[0] for p in parts)
    grown = [p[0] for p in parts]
    grown.insert(min(at, len(grown)), extra)
    after = " ".join(grown)
    expected = [p[0] for p in parts if p[1]]
    assert _texts(before, aggregate_sentences(before, "median")) == expected
    assert _texts(after, aggregate_sentences(after, "median")) == expected
    assert (_texts(after, aggregate_sentences(after, "sum"))
            == _texts(before, aggregate_sentences(before, "sum")))


@pytest.mark.criterion("C1")
@given(parts=_sentences(), pick=st.integers(0, 5), sub_kind=st.sampled_from(["median", "sum"]))
def test_inserting_not_removes_that_span(parts, pick, sub_kind):
    # oracle: requirement 1 — "not" is a decline word, so prefixing it to one span's sentence
    # removes exactly that span; the others keep their sentences
    text = " ".join(p[0] for p in parts)
    col = 1 if sub_kind == "median" else 2
    spans = aggregate_sentences(text, sub_kind)
    expected = [p[0] for p in parts if p[col]]
    assert _texts(text, spans) == expected
    if not spans:
        return
    i = pick % len(spans)
    s = spans[i][0]
    negated = text[:s] + "Not so: " + text[s:]
    remaining = expected[:i] + expected[i + 1:]
    assert _texts(negated, aggregate_sentences(negated, sub_kind)) == remaining
