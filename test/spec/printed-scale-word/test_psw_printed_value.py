"""C1-C2: a scale word printed with a number applies whatever ``scale`` holds."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.html10k.answerer import printed_value

_BAD_SCALES = [None, True, False, "1000", 7, 0, -1, 2.5]
_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "printed, scale, expected",
    [
        ("$1.2 billion", None, 1.2e9),
        ("$1.2 billion", True, 1.2e9),
        ("$1.2 billion", "1000", 1.2e9),
        ("$1.2 billion", 7, 1.2e9),
        ("$1.2 billion", 0, 1.2e9),
        ("3 million", None, 3e6),
        ("40 thousand", None, 4e4),
        ("2 trillion", None, 2e12),
        ("2 Billions", None, 2e9),
        ("(5 million)", None, -5e6),
        ("-5 million", None, -5e6),
    ],
)
def test_a_printed_word_applies_with_a_bad_scale(printed, scale, expected):
    # oracle: requirement 1, each a hand product; the control is a valid scale, which the word beats
    # tolerance: float product of exact decimals
    assert printed_value(printed, scale) == pytest.approx(expected, rel=1e-12)
    assert printed_value(printed, 1000) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    amount=st.integers(min_value=1, max_value=999_999),
    negative=st.booleans(),
    word=st.sampled_from(sorted(_WORDS)),
    scale=st.sampled_from(_BAD_SCALES),
)
def test_any_word_with_any_bad_scale(amount, negative, word, scale):
    # oracle: requirement 1, the closed form amount times the word's multiplier
    printed = f"{amount:,} {word}"
    expected = amount * _WORDS[word]
    if negative:
        printed, expected = f"({printed})", -expected
    # tolerance: float product of exact integers
    assert printed_value(printed, scale) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("scale", _BAD_SCALES)
def test_a_bare_number_still_needs_a_good_scale(scale):
    # oracle: requirement 2; the first line is what the word changes, the second what it must not
    # tolerance: float product of exact decimals
    assert printed_value("$1.2 billion", scale) == pytest.approx(1.2e9, rel=1e-12)
    assert printed_value("5", scale) is None


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("printed", ["—", "–", "-", "", "n/a", "see note 4", "billion", "million"])
@pytest.mark.parametrize("scale", [None, 7, 1000])
def test_no_number_is_none_never_zero(printed, scale):
    # oracle: requirement 3 and the RAG rule that nil never becomes 0; the control is requirement 1
    assert printed_value(printed, scale) is None
    # tolerance: exact product
    assert printed_value("3 million", None) == pytest.approx(3e6, rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    amount=st.integers(min_value=1, max_value=999_999),
    bad=st.sampled_from(_BAD_SCALES),
    good=st.sampled_from([1, 1000, 1000000, 1000000000]),
)
def test_any_bare_integer(amount, bad, good):
    # oracle: requirement 2, unchanged by this spec; the word case keeps it failing on the base code
    assert printed_value(f"{amount:,}", bad) is None
    # tolerance: float product of exact integers
    assert printed_value(f"{amount:,}", good) == pytest.approx(amount * good, rel=1e-12)
    assert printed_value(f"{amount:,} million", bad) == pytest.approx(amount * 1e6, rel=1e-12)
