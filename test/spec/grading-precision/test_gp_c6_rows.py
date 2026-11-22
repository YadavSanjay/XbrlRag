"""C6: screens and trends compare each row at its own rule
(docs/specs/grading-precision.md, requirement 6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case

AGREE = 1e-12  # value agreement is a count ratio (k/n); float division only


def _agreement(grade):
    return grade.screen_metrics["value_agreement"]


@pytest.mark.criterion("C6")
def test_goodwill_screen_mixed_decimals(dev, values_answer):
    # oracle: MSFT 119,651,000,000 has no decimals (rel_tol 1e-4: 119.65e9 is 1e6 off,
    # 8.4e-6); NVDA 20,832,000,000 at -6: 20.83e9 is 2 million off (9.6e-5, inside 1e-4
    # but wrong at the million) -> 1 of 2 rows agree; 20.832e9 -> 2 of 2
    case = dev("bigtech_goodwill_top3")
    rough = [{"ticker": "MSFT", "value": 119.65e9}, {"ticker": "NVDA", "value": 20.83e9}]
    exact = [{"ticker": "MSFT", "value": 119.65e9}, {"ticker": "NVDA", "value": 20.832e9}]
    assert _agreement(grade_case(case, values_answer(rows=rough))) == pytest.approx(
        0.5, abs=AGREE)
    assert _agreement(grade_case(case, values_answer(rows=exact))) == pytest.approx(
        1.0, abs=AGREE)


@pytest.mark.criterion("C6")
def test_tsm_gross_profit_trend(dev, answer):
    # oracle: TSM gross profit at -5 (NT$0.1 million): FY2022 1,348,354.8, FY2023
    # 1,175,110.6, FY2024 1,624,353.6 million. "NT$1,348.35 billion" drops 4.8 million
    # (3.6e-6 off, inside 1e-4) and fails FY2022.
    case = dev("tsm_gp_trend")
    full = ("FY2022 NT$1,348,354.8 million; FY2023 NT$1,175,110.6 million; "
            "FY2024 NT$1,624,353.6 million.")
    g = grade_case(case, answer(full))
    assert g.checks == {"FY2022": True, "FY2023": True, "FY2024": True}
    rough = ("FY2022 NT$1,348.35 billion; FY2023 NT$1,175,110.6 million; "
             "FY2024 NT$1,624,353.6 million.")
    g = grade_case(case, answer(rough))
    assert g.checks == {"FY2022": False, "FY2023": True, "FY2024": True}


@pytest.mark.criterion("C6")
def test_meta_equity_trend_mixed_rules(dev, answer):
    # oracle: FY2023 153,168 million at -6: 153.17 billion is 153,170 million (fails);
    # FY2024 182,637,000,000 and FY2025 217,243,000,000 have no decimals (rel_tol 1e-4):
    # 182.64e9 is 1.6e-5 off, 217.24e9 is 1.4e-5 off (pass)
    case = dev("meta_equity_trend")
    g = grade_case(case, answer("Equity: $153.17 billion, $182.64 billion, $217.24 billion."))
    assert g.checks == {"FY2023": False, "FY2024": True, "FY2025": True}
    g = grade_case(case, answer("Equity: $153.168 billion, $182.64 billion, $217.24 billion."))
    assert g.checks == {"FY2023": True, "FY2024": True, "FY2025": True}


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(index=st.integers(min_value=0, max_value=2), up=st.booleans())
def test_one_quantum_on_one_row_costs_one_row(dev, values_answer, index, up):
    # oracle: semis FY2025 net income top 3, all at -6: NVDA 72,880, AVGO 23,126, MU 8,539
    # million. Stated to the million every row agrees (3/3); moving one row by one
    # million (1.4e-5 to 1.2e-4 off) makes exactly that row disagree (2/3)
    case = dev("semis_ni_top3")
    rows = [{"ticker": r["ticker"], "value": float(r["value"])} for r in case.expected_rows]
    assert _agreement(grade_case(case, values_answer(rows=rows))) == pytest.approx(
        1.0, abs=AGREE)
    rows[index]["value"] += 1e6 if up else -1e6
    assert _agreement(grade_case(case, values_answer(rows=rows))) == pytest.approx(
        2 / 3, abs=AGREE)
