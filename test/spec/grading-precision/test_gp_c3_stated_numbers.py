"""C3: the stated value, precision, percent flag and direction of each amount in a text
(docs/specs/grading-precision.md, requirements 3 and 4)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_numbers, extract_stated_numbers

VREL = 1e-12  # values are exact decimals times a power of ten; float formatting only
QREL = 1e-9  # quanta are powers of ten


def _one(text: str):
    found = extract_stated_numbers(text)
    assert len(found) == 1, (text, found)
    return found[0]


def _is(n, value, quantum, percent=False, direction=0):
    assert n.value == pytest.approx(value, rel=VREL)
    assert n.quantum == pytest.approx(quantum, rel=QREL)
    assert n.percent is percent
    assert n.direction == direction


@pytest.mark.criterion("C3")
def test_scale_and_precision():
    # oracle: requirement 3 — quantum = place value of the last stated digit x scale:
    # "1.2" billion -> 0.1 x 1e9; "1,234" million -> 1 x 1e6; "1,342,814.1" million ->
    # 0.1 x 1e6 = 1e5; "93" m -> 1e6; "14.07" % -> 0.01; "(5.34)" -> 0.01, negative;
    # "1,234,000,000" -> 1; "0.1407" -> 1e-4
    _is(_one("Revenue was $1.2 billion."), 1.2e9, 1e8)
    _is(_one("Revenue was $1,234 million."), 1.234e9, 1e6)
    _is(_one("Cash was NT$1,342,814.1 million."), 1_342_814_100_000, 1e5)
    _is(_one("Operating income: 93m"), 93_000_000, 1e6)
    _is(_one("Margin was 14.07%."), 14.07, 0.01, percent=True)
    _is(_one("Diluted EPS was $(5.34)."), -5.34, 0.01)
    _is(_one("Revenue was $1,234,000,000."), 1_234_000_000, 1)
    _is(_one("The ratio is 0.1407."), 0.1407, 1e-4)


@pytest.mark.criterion("C3")
def test_direction_words():
    # oracle: requirement 4 — a down word immediately before (fillers allowed) or after
    # the number makes it negative; an up word marks +1; "to" is not a filler
    _is(_one("Interest income declined 5.2%."), -5.2, 0.1, percent=True, direction=-1)
    _is(_one("It was a 5.2% decline."), -5.2, 0.1, percent=True, direction=-1)
    _is(_one("Other income fell by $514 million."), -514_000_000, 1e6, direction=-1)
    _is(_one("Expense was down approximately 3.4%."), -3.4, 0.1, percent=True, direction=-1)
    _is(_one("Tax expense grew 10.9%."), 10.9, 0.1, percent=True, direction=1)
    _is(_one("Revenue fell to $95 billion."), 95e9, 1e9)
    _is(_one("a decline of 27.3% a year"), -27.3, 0.1, percent=True, direction=-1)
    # an explicit negative stays negative under a down word
    _is(_one("Income decreased by $(514) million."), -514_000_000, 1e6, direction=-1)


@pytest.mark.criterion("C3")
def test_non_amounts_are_skipped():
    # oracle: requirement 3 — the skips of extract_numbers (arm-neutral-grading C4)
    found = extract_stated_numbers(
        "In FY2024 (fiscal year 2024, ended 2024-12-31) the 10-K shows revenue of $3.5 billion.")
    assert [n.value for n in found] == pytest.approx([3.5e9], rel=VREL)
    assert found[0].quantum == pytest.approx(1e8, rel=QREL)


plain_amounts = st.integers(min_value=1, max_value=10**9)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(units=plain_amounts, template=st.sampled_from([
    "Revenue was ${x}.", "The company reported {x} in net income.", "{x}",
    "Total: ${x} (FY2024).", "It stood at {x}, per the 10-K.",
]))
def test_values_agree_with_extract_numbers_without_direction_words(units, template):
    # oracle: requirement 3 — the candidates, scales and skips are those of
    # extract_numbers; with no direction word the values are the same list
    text = template.format(x=f"{units:,}")
    stated = extract_stated_numbers(text)
    assert [n.value for n in stated] == pytest.approx(extract_numbers(text), rel=VREL)
    assert all(n.direction == 0 for n in stated)
    assert len(stated) >= 1


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(millions=st.integers(min_value=1, max_value=999_999_999))
def test_scale_word_restatements_state_the_same_value(millions):
    # oracle: one amount of `millions` x 1e6 written three ways; the billions form is the
    # exact decimal millions/1000 (hand: 35,223 million = 35.223 billion)
    billions = Decimal(millions) / 1000
    texts = {
        f"${millions:,} million": 1e6,
        f"${billions.normalize():f} billion": float(
            Decimal(1).scaleb(-max(0, -billions.normalize().as_tuple().exponent)) * 10**9),
        f"${millions * 10**6:,}": 1.0,
    }
    want = float(millions) * 1e6
    for text, quantum in texts.items():
        n = _one(text)
        assert n.value == pytest.approx(want, rel=VREL), text
        assert n.quantum == pytest.approx(quantum, rel=QREL), text
