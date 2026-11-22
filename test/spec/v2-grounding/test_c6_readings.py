"""C6: a disclosed alternate reading is a right fact and evidences the slot it serves."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import grade_v2_grounding

QCOM = 804328
ORCL = 1341439


def _shares_case(gb):
    key_input = gb.inp(QCOM, "us-gaap:CommonStockSharesOutstanding", "shares", None,
                       "2025-09-28", 1074000000, decimals=None,
                       accession="0000804328-26-000086")
    reading = {"accession": "0000804328-25-000085",
               "concept": "dei:EntityCommonStockSharesOutstanding",
               "period_end": "2025-11-03", "reading": "cover_page", "value": 1071000000}
    return gb.case("c-shares", [key_input], cik=QCOM, alt_readings=[reading])


def _shares_facts(gb):
    return {
        1: gb.rec(1, QCOM, "CommonStockSharesOutstanding", "shares", None, "2025-09-28",
                  1074000000, "0000804328-26-000086"),
        2: gb.rec(2, QCOM, "EntityCommonStockSharesOutstanding", "shares", None,
                  "2025-11-03", 1071000000, "0000804328-25-000085", ns=gb.DEI),
    }


@pytest.mark.criterion("C6")
def test_cover_page_reading_is_right(gb):
    # oracle: requirements 3 and 6 — the dei fact matches the reading, which serves slot 0
    g = grade_v2_grounding(_shares_case(gb), [2], _shares_facts(gb))
    assert (g["precision"], g["recall"], g["right"], g["evidenced"]) == (1, 1, (2,), (0,))


@pytest.mark.criterion("C6")
def test_trend_row_reading_evidences_its_year(gb):
    # oracle: requirement 3 — the current line read on the FY2024 row serves slot 1 of 3
    years = [("2023-05-31", 9000000000), ("2024-05-31", 10546000000),
             ("2025-05-31", 11000000000)]
    inputs = [gb.inp(ORCL, "s4:deferred_revenue_total", "USD", None, end, v, decimals=None)
              for end, v in years]
    rows = [{"entity": f"FY{end[:4]}", "period_end": end, "value": v} for end, v in years]
    rows[1]["alt_readings"] = [{"reading": "current_presented",
                                "concept": "us-gaap:ContractWithCustomerLiabilityCurrent",
                                "period_end": "2024-05-31", "value": 9313000000,
                                "accession": "0000950170-24-075605"}]
    c = gb.case("c-trend", inputs, shape="trend", kind="breakdown", cik=ORCL, rows=rows)
    f = gb.rec(9, ORCL, "ContractWithCustomerLiabilityCurrent", "USD", None, "2024-05-31",
               9313000000, "0000950170-25-087926")
    g = grade_v2_grounding(c, [9], {9: f})
    assert (g["precision"], g["evidenced"]) == (1, (1,))
    assert g["recall"] == pytest.approx(1 / 3, rel=1e-12)  # 1/3 is not exact in binary


@pytest.mark.criterion("C6")
@given(st.sampled_from([[1], [2], [1, 2], [2, 1]]))
def test_key_or_reading_or_both(gb, cited):
    # oracle: requirement 9 — each of the two facts is right and both serve the one slot
    g = grade_v2_grounding(_shares_case(gb), cited, _shares_facts(gb))
    assert (g["precision"], g["recall"], g["right"]) == (1, 1, tuple(cited))
