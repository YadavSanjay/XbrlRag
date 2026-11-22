"""C4: v2 scalar cases graded at their rule, on real dev keys
(docs/specs/grading-precision.md, requirements 1 and 2).

Answers are text replies read by ``parse_structured_answer``'s free-text fallback, so the
graded value is exactly what the text states.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case

IMPRECISE = "IMPRECISE"  # Grade.IMPRECISE (requirement 8, C9)


def _grade(case, answer, text):
    return grade_case(case, answer(text)).grade


def _outcome(case, answer, text):
    return grade_case(case, answer(text)).grade.value


def _value(case, answer, text):
    return grade_case(case, answer(text)).value_correct


@pytest.mark.criterion("C4")
def test_reported_amounts(dev, answer):
    # oracle: INTC FY2023 operating income 93,000,000 at decimals -6: 93.4 million rounds
    # to 93 million (passes; 0.43% off, outside the old 1e-4); 94 million does not.
    # TSM FY2022 cash 1,342,814,100,000 TWD at -5 (to NT$0.1 million): 1,342,814.1 million
    # matches; 1,342,814 million drops the 0.1 million (7e-8 off, inside 1e-4, still fails;
    # at its own precision, the million, the key rounds to 1,342,814: IMPRECISE).
    intc = dev("intc_opinc")
    assert _grade(intc, answer, "Operating income was $93.4 million.") is Grade.CORRECT
    assert _grade(intc, answer, "Operating income was $94 million.") is Grade.INCORRECT
    tsm = dev("tsm_cash")
    assert _grade(tsm, answer, "Cash was NT$1,342,814.1 million.") is Grade.CORRECT
    assert _outcome(tsm, answer, "Cash was NT$1,342,814 million.") == IMPRECISE


@pytest.mark.criterion("C4")
def test_ratios_and_growth(dev, answer):
    # oracle: TSM FY2024 R&D / revenue = 204,181.8 / 2,894,307.7 = 0.070546 -> 3 s.f.
    # 0.0705 = 7.05% (7.1% fails). MSFT FY2025 tax growth = (21,795 - 19,651) / 19,651 =
    # 2,144 / 19,651 = 0.10910 -> 10.9% (11% fails). QCOM pretax CAGR FY2023->FY2025 =
    # sqrt(12,663 / 7,443) - 1 = sqrt(1.70133) - 1 = 0.30435 -> 30.4% (30% fails).
    # Each failing statement is the key rounded at its own stated place (7.0546 -> 7.1,
    # 10.910 -> 11, 30.435 -> 30, 0.079344 -> 0.079), so it is IMPRECISE, not wrong (C9).
    tsm = dev("tsm_rd_ratio")
    assert _grade(tsm, answer, "R&D was 7.05% of revenue.") is Grade.CORRECT
    assert _outcome(tsm, answer, "R&D was 7.1% of revenue.") == IMPRECISE
    msft = dev("msft_tax_yoy")
    assert _grade(msft, answer, "The provision grew 10.9%.") is Grade.CORRECT
    assert _outcome(msft, answer, "The provision grew 11%.") == IMPRECISE
    qcom = dev("qcom_pretax_cagr")
    assert _grade(qcom, answer, "The CAGR was 30.4%.") is Grade.CORRECT
    assert _outcome(qcom, answer, "The CAGR was 30%.") == IMPRECISE
    nflx = dev("nflx_rd_ratio")
    # 2,675,758 / 33,723,297 = 0.079344 -> 0.0793
    assert _grade(nflx, answer, "The ratio was 0.0793.") is Grade.CORRECT
    assert _outcome(nflx, answer, "The ratio was 0.079.") == IMPRECISE


@pytest.mark.criterion("C4")
def test_aggregates(dev, answer):
    # oracle: dividends 19,800 + 15,025 + 398 = 35,223 million, all inputs at -6:
    # 35.223 billion passes; 35.2 billion and 35.225 billion (2 million off, 5.7e-5,
    # inside 1e-4) fail. GOOGL contract liability 8,600 - 6,000 = 2,600 million, coarsest
    # decimals -8: 2.64 billion rounds to 2.6 billion (passes); 2.7 billion fails.
    # Equity median 182,637 million, coarsest -6: 182.637 billion passes; 182.64 billion
    # (3 million off) fails. 35.2 billion (35,223 -> 35.2 at 0.1 billion) and 182.64 billion
    # (182,637 -> 182.64 at 0.01 billion) agree at their own precision: IMPRECISE; 35.225
    # billion is stated to the million and wrong there (35,225 != 35,223): INCORRECT.
    # The sum and median also carry universe/exclusion disclosure checks (v2-grading),
    # which these one-line answers do not meet, so their value verdict is read instead.
    total = dev("bigtech_div_sum")
    assert _value(total, answer, "Combined: $35.223 billion.") is True
    assert _outcome(total, answer, "Combined: $35.2 billion.") == IMPRECISE
    assert _grade(total, answer, "Combined: $35.225 billion.") is Grade.INCORRECT
    googl = dev("googl_ccl_diff")
    assert _grade(googl, answer, "It moved $2.64 billion.") is Grade.CORRECT
    assert _grade(googl, answer, "It moved $2.7 billion.") is Grade.INCORRECT
    median = dev("bigtech_equity_median")
    assert _value(median, answer, "The median is $182.637 billion.") is True
    assert _outcome(median, answer, "The median is $182.64 billion.") == IMPRECISE


@pytest.mark.criterion("C4")
def test_shares_and_per_share(dev, answer):
    # oracle: AAPL FY2022 basic shares 16,215,963,000 at -3: 16.216 billion drops the
    # thousands (fails, though 2.3e-6 off); 16,215,963 thousand matches. CRM 962,000,000
    # at -6: 962 million. EPS at cents: ADBE 16.70 <- "$16.7"; NVDA 1.19: "$1.2" fails,
    # "$1.194" rounds to 1.19. MSFT DPS 3.00 (no decimals; per-share is cents anyway).
    # 16,215,963,000 -> 16.216 billion and 1.19 -> 1.2 at their stated place: IMPRECISE.
    aapl = dev("aapl_shares")
    assert _outcome(aapl, answer, "Apple had 16.216 billion shares.") == IMPRECISE
    assert _grade(aapl, answer, "Apple had 16,215,963 thousand shares.") is Grade.CORRECT
    assert _grade(dev("crm_shares"), answer, "Salesforce: 962 million shares.") is Grade.CORRECT
    assert _grade(dev("adbe_eps"), answer, "Diluted EPS was $16.7.") is Grade.CORRECT
    nvda = dev("nvda_eps")
    assert _outcome(nvda, answer, "Diluted EPS was $1.2.") == IMPRECISE
    assert _grade(nvda, answer, "Diluted EPS was $1.194.") is Grade.CORRECT
    assert _grade(dev("msft_dps"), answer, "The dividend was $3 per share.") is Grade.CORRECT


@pytest.mark.criterion("C4")
def test_untagged_case_keeps_rel_tol(dev_record, dev, answer):
    # oracle: requirement 1 — without a shape: tag the case is graded by rel_tol as today:
    # 391.0 billion vs 391,035,000,000 is 8.95e-5 off (< 1e-4) -> CORRECT; the same
    # statement under a decimals -6 v2 key fails (391,000 != 391,035 million) but agrees at
    # its own 0.1 billion (391,035 million -> 391.0 billion): IMPRECISE
    record = dev_record("intc_opinc")
    record["tags"] = [t for t in record["tags"] if not t.startswith("shape:")]
    record["expected_value"] = 391_035_000_000
    record["key"]["inputs"][0]["value"] = 391_035_000_000
    legacy = EvalCase.from_dict(record)
    assert _grade(legacy, answer, "Revenue was $391.0 billion.") is Grade.CORRECT
    v2 = dev("intc_opinc", expected_value=391_035_000_000)
    v2.key["inputs"][0]["value"] = 391_035_000_000
    assert _outcome(v2, answer, "Revenue was $391.0 billion.") == IMPRECISE


@pytest.mark.criterion("C4")
@given(m=st.integers(min_value=1, max_value=3999), k=st.integers(min_value=3, max_value=9))
def test_within_and_beyond_a_quantum(dev_record, answer, m, k):
    # oracle: requirement 2 — a reported key m x 10**k at decimals -k; 0.4 of a quantum
    # away rounds back (CORRECT; 0.4/m >= 1e-4, so the old rel_tol failed it), one
    # quantum away does not (INCORRECT)
    record = dev_record("intc_opinc")
    key = m * 10**k
    record["expected_value"] = key
    record["key"]["inputs"][0].update(value=key, decimals=-k)
    case = EvalCase.from_dict(record)
    near = key + 4 * 10 ** (k - 1)
    far = key + 10**k
    assert _grade(case, answer, f"Operating income was ${near:,}.") is Grade.CORRECT
    assert _grade(case, answer, f"Operating income was ${far:,}.") is Grade.INCORRECT


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(millions=st.integers(min_value=1, max_value=999_999), delta=st.integers(-3, 3),
       template=st.sampled_from([
           "Operating income was {x}.", "The company reported operating income of {x}",
           "{x}", "In FY2023, operating income came to {x} (per the 10-K).",
       ]))
def test_scale_word_and_rewording_invariance(dev_record, answer, millions, delta, template):
    # oracle: requirement 2 — the grade depends on the stated value only: a key of
    # `millions` million at -6 and an answer `delta` million away (CORRECT iff delta == 0)
    # gets the same grade however the amount is scaled or the sentence is worded
    record = dev_record("intc_opinc")
    key = millions * 10**6
    record["expected_value"] = key
    record["key"]["inputs"][0].update(value=key, decimals=-6)
    case = EvalCase.from_dict(record)
    stated_m = millions + delta
    if stated_m <= 0:
        stated_m = millions + 1
        delta = 1
    # every form states the million (the billions form keeps three decimals), so a wrong
    # amount is wrong at its own precision too: INCORRECT, never IMPRECISE (C9)
    forms = [f"${stated_m:,} million", f"${Decimal(stated_m) / 1000:.3f} billion",
             f"${stated_m * 10**6:,}"]
    want = Grade.CORRECT if delta == 0 else Grade.INCORRECT
    for form in forms:
        assert _grade(case, answer, template.format(x=form)) is want, form
    # one discriminating point: half a million off still rounds to the key
    half = f"${Decimal(millions) + Decimal('0.4')} million"
    assert _grade(case, answer, template.format(x=half)) is Grade.CORRECT
