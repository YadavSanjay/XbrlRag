"""C4: the free-text number extractor."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_numbers

# oracle: the example table in Notes of docs/specs/arm-neutral-grading.md (hand arithmetic)
_TABLE = [
    ("Revenue was $391B.", [391e9]),
    ("Revenue was $391.035 billion in FY2024.", [391.035e9]),
    ("391,035 million", [391035e6]),
    ("Net loss of (1,234) thousand", [-1234e3]),
    ("Operating margin was 12.5%", [12.5]),
    ("margin of 31.2 percent", [31.2]),
    ("NT$2,894.31 billion", [2894.31e9]),
    ("US$ 1.2bn and -3.5 million", [1.2e9, -3.5e6]),
    ("−7.5%", [-7.5]),
    ("In fiscal 2025, per the 10-K filed 2025-10-31, Q3 sales were 94.9 billion", [94.9e9]),
    ("FY2023 revenue: 383,285", [383285.0]),
]

_SCALES = [
    ("thousand", 1e3),
    ("thousands", 1e3),
    ("million", 1e6),
    ("millions", 1e6),
    ("mn", 1e6),
    ("mm", 1e6),
    ("billion", 1e9),
    ("billions", 1e9),
    ("bn", 1e9),
    ("trillion", 1e12),
    ("tn", 1e12),
]
_FILLER = st.lists(
    st.sampled_from(["the", "revenue", "was", "reported", "approximately", "for", "total"]),
    max_size=4,
).map(" ".join)


def _approx(values):
    # tolerance: parsing decimal text into binary floats; 1e-12 relative is far tighter
    # than any grading tolerance
    return pytest.approx(values, rel=1e-12)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("text", "expected"), _TABLE)
def test_example_table(text, expected):
    # oracle: the Notes table, one row per test
    assert extract_numbers(text) == _approx(expected)


@pytest.mark.criterion("C4")
def test_text_without_amounts_yields_nothing():
    # oracle: C4 — no digits that are amounts means no amounts; the second text has one
    assert extract_numbers("No figure is available.") == []
    assert extract_numbers("For FY2025, see the 20-F.") == []
    assert extract_numbers("The 20-F reports 5 billion.") == _approx([5e9])


@pytest.mark.criterion("C4")
@given(
    amount=st.integers(min_value=1, max_value=10**9),
    scale=st.sampled_from(_SCALES),
    currency=st.sampled_from(["", "$", "US$", "USD ", "NT$", "TWD "]),
)
def test_formatted_integer_round_trips(amount, scale, currency):
    # oracle: closed form — the text states amount × scale by construction
    word, factor = scale
    text = f"{currency}{amount:,} {word}"
    assert extract_numbers(text) == _approx([amount * factor])


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(amount=st.integers(min_value=1, max_value=10**9))
def test_parentheses_negate(amount):
    # oracle: C4 rule — accounting parentheses mean a negative amount
    assert extract_numbers(f"({amount:,})") == _approx([-float(amount)])
    assert extract_numbers(f"{amount:,} and ({amount:,})") == _approx(
        [float(amount), -float(amount)]
    )


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    amount=st.integers(min_value=1, max_value=10**6),
    scale=st.sampled_from(_SCALES),
    before=_FILLER,
    after=_FILLER,
)
def test_surrounding_words_do_not_change_the_result(amount, scale, before, after):
    # oracle: closed form for the core; filler words carry no digits, scale or percent
    word, factor = scale
    core = f"${amount:,} {word}"
    assert extract_numbers(core) == _approx([amount * factor])
    assert extract_numbers(f"{before} {core} {after}") == extract_numbers(core)
