"""C5: signs and direction words (docs/specs/grading-precision.md, requirement 4).

Text replies are read by ``parse_structured_answer``'s free-text fallback.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case


def _grade(case, answer, text):
    return grade_case(case, answer(text)).grade


@pytest.mark.criterion("C5")
def test_growth_and_cagr_directions(dev, answer):
    # oracle: META interest income (2,123 - 2,517) / 2,517 = -394 / 2,517 = -0.15654
    # -> -15.7%. TSLA pretax CAGR FY2022->FY2025 = (5,278 / 13,719)**(1/3) - 1 =
    # 0.38472**(1/3) - 1 = 0.72730 - 1 = -0.27270 -> -27.3%.
    meta = dev("meta_intinc_yoy")
    assert _grade(meta, answer, "Interest income declined 15.7%.") is Grade.CORRECT
    assert _grade(meta, answer, "It was a 15.7% decline.") is Grade.CORRECT
    assert _grade(meta, answer, "Interest income changed by -15.7%.") is Grade.CORRECT
    assert _grade(meta, answer, "Interest income grew 15.7%.") is Grade.INCORRECT
    assert _grade(meta, answer, "The change was 15.7%.") is Grade.INCORRECT
    tsla = dev("tsla_pretax_cagr")
    assert _grade(tsla, answer, "Pretax income saw a decline of 27.3% a year.") is Grade.CORRECT
    assert _grade(tsla, answer, "Pretax income compounded at 27.3% a year.") is Grade.INCORRECT


@pytest.mark.criterion("C5")
def test_signed_differences_and_per_share(dev, answer):
    # oracle: P&G other non-operating income 154 - 668 = -514 million (at -6); MU FY2023
    # diluted EPS as presented in its FY2025 annual report: -5.34
    pg = dev("pg_nonop_diff")
    assert _grade(pg, answer, "It decreased by $514 million.") is Grade.CORRECT
    assert _grade(pg, answer, "The change was $(514) million.") is Grade.CORRECT
    assert _grade(pg, answer, "The change was $514 million.") is Grade.INCORRECT
    mu = dev("mu_eps_pit")
    assert _grade(mu, answer, "Diluted EPS was $(5.34).") is Grade.CORRECT
    assert _grade(mu, answer, "Diluted EPS was $5.34.") is Grade.INCORRECT


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(tenths=st.integers(min_value=1, max_value=999), negative=st.booleans(),
       down=st.sampled_from(["declined", "fell", "decreased", "dropped", "was down"]),
       up=st.sampled_from(["grew", "rose", "increased", "climbed", "was up"]))
def test_opposite_direction_fails(dev_record, answer, tenths, negative, down, up):
    # oracle: requirement 4 — a growth key of +/- tenths/10 percent; the right magnitude
    # with the matching direction word is CORRECT, with the opposite word INCORRECT
    record = dev_record("msft_tax_yoy")
    pct = tenths / 10
    record["expected_value"] = (-pct if negative else pct) / 100
    record["alt_values"] = [-pct if negative else pct]
    case = EvalCase.from_dict(record)
    right, wrong = (down, up) if negative else (up, down)
    assert _grade(case, answer, f"The provision {right} {pct:.1f}%.") is Grade.CORRECT
    assert _grade(case, answer, f"The provision {wrong} {pct:.1f}%.") is Grade.INCORRECT
