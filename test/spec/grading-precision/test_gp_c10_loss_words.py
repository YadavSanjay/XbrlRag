"""C10: loss words sign an income-type metric (docs/specs/grading-precision.md,
requirement 9; decided 2026-11-07 by the maintainer).

The parser marks a number a loss word governs (``StatedNumber.loss``); the grader reads
it as -|v| only for an income-type lookup. MU's FY2023 diluted EPS (-5.34) is the dev
split's real negative-income key; INTC's FY2023 operating income (+93,000,000) is a real
positive one; NFLX G&A stands in for a metric where "loss" stays unsigned.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import extract_stated_numbers
from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.precision import is_income_metric

VREL = 1e-12  # values are exact decimals times a power of ten; float formatting only
QREL = 1e-9  # quanta are powers of ten


def _one(text: str):
    found = extract_stated_numbers(text)
    assert len(found) == 1, (text, found)
    return found[0]


def _outcome(case, answer, text):
    return grade_case(case, answer(text)).grade.value


@pytest.mark.criterion("C10")
def test_parser_marks_loss_words():
    # oracle: requirement 9, hand-read: a loss word directly before the number (fillers
    # "of", "a", "was" allowed) or directly after it ("net loss", "loss per share")
    # governs it; the value keeps its own sign
    n = _one("The company reported a net loss of $1.2 billion.")
    assert (n.value, n.loss) == (pytest.approx(1.2e9, rel=VREL), True)
    assert n.quantum == pytest.approx(1e8, rel=QREL)
    n = _one("It posted a loss per share of $0.40.")
    assert (n.value, n.loss) == (pytest.approx(0.40, rel=VREL), True)
    assert n.quantum == pytest.approx(0.01, rel=QREL)
    n = _one("Diluted loss per share was $(5.34).")
    assert (n.value, n.loss) == (pytest.approx(-5.34, rel=VREL), True)
    assert _one("Tesla booked a $1.2 billion net loss.").loss is True
    assert _one("Micron reported $5.34 loss per share.").loss is True
    # not loss words: in parentheses, after "impairment", after "or"; not adjacent
    assert _one("Net income (loss) per share of $5.34.").loss is False
    assert _one("It recorded an impairment loss of $2 billion.").loss is False
    assert _one("A gain or loss of $3 million was recognised.").loss is False
    assert _one("Revenue of $5 billion.").loss is False
    assert _one("The loss widened while revenue was $5 billion.").loss is False


@pytest.mark.criterion("C10")
def test_income_metric_kinds(dev, dev_record):
    # oracle: requirement 9 — the concept's local name contains IncomeLoss, ProfitLoss or
    # EarningsLossPerShare, or starts with EarningsPerShare or ComprehensiveIncome
    assert is_income_metric(dev("mu_eps_pit")) is True  # EarningsPerShareDiluted
    assert is_income_metric(dev("intc_opinc")) is True  # OperatingIncomeLoss
    assert is_income_metric(dev("tsla_ni_trend")) is True  # NetIncomeLoss
    assert is_income_metric(dev("nflx_ga_pit")) is False  # GeneralAndAdministrativeExpense
    assert is_income_metric(dev("tsm_cash")) is False  # CashAndCashEquivalents
    assert is_income_metric(dev("pg_nonop_diff")) is False  # OtherNonoperatingIncomeExpense
    for concept, want in [("ifrs-full:ProfitLoss", True),
                          ("ifrs-full:BasicEarningsLossPerShare", True),
                          ("us-gaap:ComprehensiveIncomeNetOfTax", True),
                          ("us-gaap:GoodwillImpairmentLoss", False)]:
        record = dev_record("nflx_ga_pit")
        record["concept"] = concept
        record["key"]["inputs"][0]["concept"] = concept
        assert is_income_metric(EvalCase.from_dict(record)) is want, concept


@pytest.mark.criterion("C10")
def test_loss_words_grade_mu_eps(dev, answer):
    # oracle: MU FY2023 diluted EPS -5.34 (a net loss year), compared at cents. Every
    # loss-worded statement of 5.34 states -5.34 (a stated minus or parentheses does not
    # flip it back); "(loss)" in a label is not a loss word; "$5.3" under a loss word is
    # -5.3, the key rounded at 0.1 (IMPRECISE, C9)
    mu = dev("mu_eps_pit")
    for text in ["Micron reported a loss per share of $5.34.",
                 "Micron reported a diluted loss per share of $5.34.",
                 "It was a net loss of $(5.34) per share.",
                 "It was a loss of -$5.34 per share.",
                 "Micron reported $5.34 loss per share."]:
        assert _outcome(mu, answer, text) == "CORRECT", text
    assert _outcome(mu, answer, "Net income (loss) per share of $5.34.") == "INCORRECT"
    assert _outcome(mu, answer, "Micron reported a loss per share of $5.3.") == "IMPRECISE"


@pytest.mark.criterion("C10")
def test_loss_words_on_a_positive_key_and_outside_scope(dev, answer):
    # oracle: INTC FY2023 operating income +93,000,000: "an operating loss of $93 million"
    # states -93 million (INCORRECT). NFLX FY2023 G&A 1,720,285,000 at -3 is not an
    # income metric, so "a loss of $1,720.285 million" stays +1,720,285,000 (CORRECT).
    intc = dev("intc_opinc")
    assert _outcome(intc, answer, "Intel had an operating loss of $93 million.") == "INCORRECT"
    assert _outcome(intc, answer, "Intel had operating income of $93 million.") == "CORRECT"
    nflx = dev("nflx_ga_pit")
    assert _outcome(nflx, answer, "G&A was a loss of $1,720.285 million.") == "CORRECT"


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(cents=st.integers(min_value=1, max_value=99_999), negative=st.booleans())
def test_loss_forms_agree_for_an_income_metric(dev_record, answer, cents, negative):
    # oracle: requirement 9 — for an income-type EPS key of +/- cents/100, every
    # loss-worded form (plain, parenthesised, minus-signed, trailing) reads -|x|, so all
    # are CORRECT exactly when the key is negative; the loss-free form exactly when positive
    record = dev_record("mu_eps_pit")
    key = (-cents if negative else cents) / 100
    record["expected_value"] = key
    record["key"]["inputs"][0]["value"] = key
    case = EvalCase.from_dict(record)
    x = f"{Decimal(cents) / 100:.2f}"
    loss_forms = [f"a loss per share of ${x}", f"a loss per share of $({x})",
                  f"a loss per share of -${x}", f"${x} loss per share"]
    want_loss = "CORRECT" if negative else "INCORRECT"
    for form in loss_forms:
        assert _outcome(case, answer, f"Micron reported {form}.") == want_loss, form
    want_plain = "INCORRECT" if negative else "CORRECT"
    assert _outcome(case, answer, f"Micron reported EPS of ${x}.") == want_plain


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(thousands=st.integers(min_value=10_000, max_value=999_999_999).filter(
    lambda t: t % 100))
def test_loss_is_unsigned_outside_income_metrics(dev_record, answer, thousands):
    # oracle: requirement 9 — NFLX G&A is not income-type: a loss-worded statement grades
    # as the bare number does, at full precision (CORRECT) and at 0.1 million, the key
    # rounded half up by hand (IMPRECISE, C9; t not a multiple of 100)
    record = dev_record("nflx_ga_pit")
    key = thousands * 10**3
    record["expected_value"] = key
    record["key"]["inputs"][0].update(value=key, decimals=-3)
    case = EvalCase.from_dict(record)
    full = f"{Decimal(thousands) / 1000:.3f}"
    tenth = (Decimal(thousands) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    for x, want in [(full, "CORRECT"), (f"{tenth:.1f}", "IMPRECISE")]:
        bare = _outcome(case, answer, f"G&A was ${x} million.")
        lossy = _outcome(case, answer, f"G&A was a loss of ${x} million.")
        assert (bare, lossy) == (want, want), x
