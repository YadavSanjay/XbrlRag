"""C2: rank numbers and a rank's universe are never stated values
(docs/specs/grader-v9-number-reading.md, requirements 2 and 3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_stated_numbers, rank_spans


def _read(text: str) -> list[float]:
    return [n.value for n in extract_stated_numbers(text)]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("text", [
    "DEVA ranked 3rd.",
    "DEVB is #2.",
    "DEVC is No. 4 among the retailers.",
    "Rank 1: DEVA.",
    "DEVS ranked 2 of 32.",
    "DEVS ranked 3rd out of 15 companies.",
    "DEVA is #1 of 33 companies.",
    "DEVD ranks at 5 and was 21st last year.",
])
def test_rank_forms_read_nothing(text):
    # oracle: requirement 2 — every number is a rank (a)/(b) or a rank's universe (c)
    assert _read(text) == []


@pytest.mark.criterion("C2")
def test_amounts_beside_ranks_still_read():
    # oracle: requirement 3 — the amount reads, the rank does not
    # tolerance: literal values, exact
    assert _read("DEVA ranked 3rd with revenue of $3 billion.") == pytest.approx(
        [3.0e9], rel=1e-12)
    assert _read("DEVA ranked #2 with a margin of 2.5%.") == pytest.approx([2.5], rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("text,expected", [
    ("I ranked 32 of 33 companies; DEVA is 3rd.", [32.0, 33.0]),
    ("14 of 33 companies reported; DEVA ranked 1st.", [14.0, 33.0]),
    ("DEVA ranked 2.5 points ahead; DEVB is #2.", [2.5]),
])
def test_counts_and_decimals_are_not_ranks(text, expected):
    # oracle: requirement 2 — a first-person count phrase and a bare "N of M" are counts; a
    # decimal after a rank word is not a numbered rank; the ordinals and "#2" are ranks
    # tolerance: literal values, exact
    assert _read(text) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C2")
def test_rank_spans_are_the_digit_spans():
    # oracle: requirement 2 — positions by hand with str.index
    text = "DEVS ranked 2 of 32; DEVA is #1 and DEVB 3rd."
    want = [(text.index("2 of"), text.index("2 of") + 1),
            (text.index("32"), text.index("32") + 2),
            (text.index("#1") + 1, text.index("#1") + 2),
            (text.index("3rd"), text.index("3rd") + 1)]
    assert rank_spans(text) == sorted(want)
    assert rank_spans("We ranked 32 of 33 companies.") == []
    assert rank_spans("Revenue was $5 million.") == []


FORMS = st.sampled_from([
    "{t} ranked {n}{suf}.", "{t} is #{n}.", "{t} is No. {n}.", "Rank {n}: {t}.",
    "{t} ranked {n} of {m}.", "{t} ranked {n}{suf} out of {m} companies.",
    "{t} is #{n} of {m}.",
])


def _suffix(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@pytest.mark.criterion("C2")
@given(form=FORMS, n=st.integers(min_value=1, max_value=999),
       m=st.integers(min_value=1, max_value=999), x=st.integers(min_value=1, max_value=99_999),
       t=st.sampled_from(["DEVA", "DEVB", "Home Depot"]))
def test_any_rank_form_beside_an_amount_reads_only_the_amount(form, n, m, x, t):
    # oracle: requirement 2 (a)-(c) and requirement 3
    sentence = form.format(t=t, n=n, m=m, suf=_suffix(n))
    # tolerance: an integer times 1e6, exact in floating point at this size
    assert _read(f"{t} reported ${x} million. {sentence}") == pytest.approx(
        [x * 1e6], rel=1e-12)


@pytest.mark.criterion("C2")
@given(n=st.integers(min_value=0, max_value=10**6), prose=st.sampled_from(
    ["", "Revenue was $4 billion. ", "It was the "]))
def test_a_numeric_ordinal_never_reads(n, prose):
    # oracle: requirement 2 (a) — an ordinal is never an amount
    text = f"{prose}{n}{_suffix(n)} year in a row"
    want = [4e9] if prose.startswith("Revenue") else []
    # tolerance: literal value, exact
    assert _read(text) == pytest.approx(want, rel=1e-12)
