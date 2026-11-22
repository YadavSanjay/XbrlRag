"""C9: an answer that fails only by being less precise than its rule is IMPRECISE
(docs/specs/grading-precision.md, requirement 8; decided 2026-11-07 by the maintainer).

IMPRECISE is neither correct nor wrong: it is excluded from confident-wrong and
silent-wrong, and does not count towards value accuracy. Grades are compared by their
string value, ``"IMPRECISE"``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.precision import precision_outcome, precision_rule
from src.eval.core.report import CaseResult, Metrics

IMPRECISE = "IMPRECISE"
RATE = 1e-12  # the rates are exact rationals over n = 3; float division only


def _outcome(case, answer, text):
    return grade_case(case, answer(text)).grade.value


@pytest.mark.criterion("C9")
def test_precision_outcome_on_real_rules(dev):
    # oracle: requirement 8, hand-rounded.
    # TSM cash 1,342,814,100,000 at -5: stated to the million 1,342,814 million is the key
    # rounded there (imprecise); to 0.1 million it is the key (match); 1,342,815 million is
    # wrong at the million; with no stated quantum there is nothing to be imprecise about.
    tsm = precision_rule(dev("tsm_cash"))
    key = 1_342_814_100_000.0
    assert precision_outcome(1_342_814e6, 1e6, key, tsm) == "imprecise"
    assert precision_outcome(1_342_814.1e6, 1e5, key, tsm) == "match"
    assert precision_outcome(1_342_815e6, 1e6, key, tsm) == "wrong"
    assert precision_outcome(1_342_814e6, None, key, tsm) == "wrong"
    # TSM R&D ratio 204,181.8 / 2,894,307.7 = 0.070546 = 7.0546%: 7.1 (percent, 0.1) and
    # 0.1 (fraction, 0.1) are the key rounded at 0.1; 7.2 is not.
    ratio = precision_rule(dev("tsm_rd_ratio"))
    r = 204_181.8 / 2_894_307.7
    assert precision_outcome(7.1, 0.1, r, ratio) == "imprecise"
    assert precision_outcome(0.1, 0.1, r, ratio) == "imprecise"
    assert precision_outcome(7.2, 0.1, r, ratio) == "wrong"
    # MU EPS -5.34: -5.3 agrees at 0.1, +5.3 has the wrong sign at any precision.
    mu = precision_rule(dev("mu_eps_pit"))
    assert precision_outcome(-5.3, 0.1, -5.34, mu) == "imprecise"
    assert precision_outcome(5.3, 0.1, -5.34, mu) == "wrong"
    # INTC 93,000,000 rounded to the billion is 0: a zero statement of a non-zero key.
    intc = precision_rule(dev("intc_opinc"))
    assert precision_outcome(0.0, 1e9, 93_000_000.0, intc) == "wrong"
    assert precision_outcome(1e8, 1e8, 93_000_000.0, intc) == "imprecise"
    # Goodwill MSFT row 119,651,000,000, no decimals (rel_tol 1e-4): 119.7e9 is 4.1e-4
    # off (fails rel_tol) but is the key rounded at 0.1 billion.
    goodwill = dev("bigtech_goodwill_top3")
    msft_row = next(r for r in goodwill.expected_rows if r["ticker"] == "MSFT")
    msft = precision_rule(goodwill, row=msft_row)
    assert precision_outcome(119.7e9, 1e8, 119_651_000_000.0, msft) == "imprecise"
    assert precision_outcome(119.65e9, 1e7, 119_651_000_000.0, msft) == "match"


@pytest.mark.criterion("C9")
def test_legacy_rule_is_never_imprecise(dev_record):
    # oracle: requirement 8 — a case without a shape: tag keeps today's grading; 390e9 is
    # 2.6e-3 off 391,035,000,000 (outside 1e-4) and is wrong, not imprecise
    record = dev_record("intc_opinc")
    record["tags"] = [t for t in record["tags"] if not t.startswith("shape:")]
    case = EvalCase.from_dict(record)
    rule = precision_rule(case)
    assert precision_outcome(390e9, 1e9, 391_035_000_000.0, rule) == "wrong"
    assert precision_outcome(391_035e6, 1e6, 391_035_000_000.0, rule) == "match"


@pytest.mark.criterion("C9")
def test_grades_on_real_cases(dev, dev_record, answer):
    # oracle: requirement 8. INTC 93 million at -6: "$0.1 billion" is 93 million rounded
    # at 0.1 billion (IMPRECISE); "$0 billion" is zero for a non-zero key and "$94 million"
    # is wrong at the million (INCORRECT). GOOGL 8,600 - 6,000 = 2,600 million at -8: "$3
    # billion" is it rounded to the billion. MU EPS -5.34: "$(5.3)" is -5.3 (IMPRECISE),
    # "$5.3" has the wrong sign (INCORRECT).
    intc = dev("intc_opinc")
    assert _outcome(intc, answer, "Operating income was $0.1 billion.") == IMPRECISE
    assert _outcome(intc, answer, "Operating income was $0 billion.") == "INCORRECT"
    assert _outcome(intc, answer, "Operating income was $94 million.") == "INCORRECT"
    assert _outcome(dev("googl_ccl_diff"), answer, "It moved $3 billion.") == IMPRECISE
    mu = dev("mu_eps_pit")
    assert _outcome(mu, answer, "Diluted EPS was $(5.3).") == IMPRECISE
    assert _outcome(mu, answer, "Diluted EPS was $5.3.") == "INCORRECT"
    # a hedged imprecise answer stays HEDGED_MISS; an uncited one is still IMPRECISE
    hedged = answer("I cannot confirm it; the nearest figure is $0.1 billion.")
    hedged.hedged = True
    assert grade_case(intc, hedged).grade.value == "HEDGED_MISS"
    uncited = answer("Operating income was $0.1 billion.")
    uncited.fact_ids = []
    assert grade_case(intc, uncited).grade.value == IMPRECISE
    # untagged: legacy rel_tol, "$390 billion" for 391,035,000,000 is INCORRECT
    record = dev_record("intc_opinc")
    record["tags"] = [t for t in record["tags"] if not t.startswith("shape:")]
    record["expected_value"] = 391_035_000_000
    record["key"]["inputs"][0]["value"] = 391_035_000_000
    legacy = EvalCase.from_dict(record)
    assert _outcome(legacy, answer, "Revenue was $390 billion.") == "INCORRECT"


@pytest.mark.criterion("C9")
def test_imprecise_is_neither_correct_nor_wrong(dev, answer):
    # oracle: requirement 8 — IMPRECISE is not value-correct, not confident-wrong, not
    # silent-wrong; hand count over three INTC answers: $93 million CORRECT, $0.1 billion
    # IMPRECISE, $95 million INCORRECT (cited, so also silent- and confident-wrong)
    case = dev("intc_opinc")
    texts = ["Operating income was $93 million.", "Operating income was $0.1 billion.",
             "Operating income was $95 million."]
    results = []
    for text in texts:
        ans = answer(text)
        results.append(CaseResult.build(case, ans, grade_case(case, ans)))
    imprecise = results[1].grade
    assert imprecise.grade.value == IMPRECISE
    assert imprecise.value_correct is False
    assert imprecise.confident_wrong is False
    assert imprecise.silent_wrong is False
    m = Metrics.compute(results)
    assert getattr(m, "n_imprecise", None) == 1
    assert getattr(m, "imprecise_rate", None) == pytest.approx(1 / 3, abs=RATE)
    assert m.confident_wrong_rate == pytest.approx(1 / 3, abs=RATE)
    assert m.value_accuracy == pytest.approx(1 / 3, abs=RATE)
    assert m.accuracy == pytest.approx(1 / 3, abs=RATE)
    d = m.to_dict()
    assert d.get("n_imprecise") == 1
    assert d.get("imprecise_rate") == pytest.approx(1 / 3, abs=1e-4)  # to_dict rounds


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(millions=st.integers(min_value=1_000, max_value=999_999).filter(lambda m: m % 100),
       template=st.sampled_from([
           "Operating income was ${x} billion.", "It reported ${x} billion of operating income.",
           "${x} billion",
       ]))
def test_tenths_of_a_billion_are_imprecise(dev_record, answer, millions, template):
    # oracle: requirement 8 — a key of M million at -6 (M not a multiple of 100), stated
    # in tenths of a billion and correctly rounded half up by hand (Decimal), agrees only
    # at its own 0.1 billion: IMPRECISE, never value-correct or confident-/silent-wrong;
    # 0.1 billion further is wrong at its own precision: INCORRECT
    record = dev_record("intc_opinc")
    key = millions * 10**6
    record["expected_value"] = key
    record["key"]["inputs"][0].update(value=key, decimals=-6)
    case = EvalCase.from_dict(record)
    tenths = (Decimal(millions) / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    g = grade_case(case, answer(template.format(x=f"{tenths:.1f}")))
    assert g.grade.value == IMPRECISE
    assert (g.value_correct, g.confident_wrong, g.silent_wrong) == (False, False, False)
    further = tenths + Decimal("0.1")
    assert _outcome(case, answer, template.format(x=f"{further:.1f}")) == "INCORRECT"


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(units=st.integers(min_value=-10**7, max_value=10**7),
       exponent=st.sampled_from([0, 3, 5, 6]))
def test_no_coarser_than_the_rule_is_never_imprecise(dev, units, exponent):
    # oracle: requirement 8 — INTC's rule compares at 1e6; a value stated at a place
    # 10**exponent <= 1e6 either matches or is wrong, never imprecise (a value equal to
    # the key rounded at a place no coarser than the rule always matches)
    rule = precision_rule(dev("intc_opinc"))
    s = float(10**exponent)
    value = units * s
    assert precision_outcome(value, s, 93_000_000.0, rule) in {"match", "wrong"}
    assert precision_outcome(93_000_000.0, s, 93_000_000.0, rule) == "match"
