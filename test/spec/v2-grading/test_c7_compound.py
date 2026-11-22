"""C7: compound cases pass only when both parts pass, with a per-part report.

Oracle: requirement 14 of docs/specs/v2-grading.md. The conftest compound's ranking is the
H-X screen (DEVA 300m > DEVB 200m over DEVA..DEVD, DEVD excluded) and its dependent a
two-year trend of DEVA (FY2024 250m, FY2025 300m).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

M = 1e6
ROWS = [{"ticker": "DEVA", "value": 300 * M, "fact_id": 1},
        {"ticker": "DEVB", "value": 200 * M, "fact_id": 2}]
REVERSED = [{"ticker": "DEVB", "value": 200 * M, "fact_id": 2},
            {"ticker": "DEVA", "value": 300 * M, "fact_id": 1}]
DISCLOSED = "Among the 4 companies DEVA leads, then DEVB; DEVD could not be ranked."


@pytest.mark.criterion("C7")
def test_both_parts_pass(kit):
    # oracle: requirement 14 — ranking (rule 10) and dependent (rule 12) both CORRECT, the
    # checks are the parts' verdicts plus their own checks, prefixed.
    case = kit.case(kit.hx_compound())
    g = grade_v2(case, kit.answer(text=DISCLOSED, rows=ROWS, values=[250 * M, 300 * M],
                                  fact_ids=[1, 2, 34]))
    assert g.grade is Grade.CORRECT and g.passed is True
    assert kit.checks(g) == {
        "ranking": True, "dependent": True,
        "ranking.ranking": True, "ranking.universe": True, "ranking.exclusions": True,
        "dependent.FY2024": True, "dependent.FY2025": True,
    }
    assert g.reason.startswith("ranking: ")
    assert "; dependent: " in g.reason


@pytest.mark.criterion("C7")
def test_failing_dependent_and_silent_ranking(kit):
    # oracle: requirement 14 — a dependent missing FY2025 is INCORRECT, so the compound is;
    # a ranking silent on DEVD is PARTIAL, so the compound is PARTIAL.
    case = kit.case(kit.hx_compound())
    dep_fail = grade_v2(case, kit.answer(text=DISCLOSED, rows=ROWS, values=[250 * M],
                                         fact_ids=[1, 2, 34]))
    assert dep_fail.grade is Grade.INCORRECT and dep_fail.passed is False
    checks = kit.checks(dep_fail)
    assert checks["ranking"] is True and checks["dependent"] is False
    assert checks["dependent.FY2024"] is True and checks["dependent.FY2025"] is False

    silent = grade_v2(case, kit.answer(text="DEVA leads, then DEVB.", rows=ROWS,
                                       values=[250 * M, 300 * M], fact_ids=[1, 2, 34]))
    assert silent.grade is Grade.PARTIAL and silent.passed is False
    checks = kit.checks(silent)
    assert checks["ranking"] is False and checks["dependent"] is True
    assert checks["ranking.ranking"] is True and checks["ranking.exclusions"] is False

    refused = grade_v2(case, kit.answer(text="I cannot answer.", refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C7")
@given(ranking_ok=st.booleans(), dependent_ok=st.booleans())
def test_correct_exactly_when_both_parts_are(kit, ranking_ok, dependent_ok):
    # oracle: requirement 14 — CORRECT iff every part CORRECT; any INCORRECT part makes the
    # compound INCORRECT (a reversed ranking and a missing year each are INCORRECT).
    case = kit.case(kit.hx_compound())
    ans = kit.answer(text=DISCLOSED, rows=ROWS if ranking_ok else REVERSED,
                     values=[250 * M, 300 * M] if dependent_ok else [250 * M],
                     fact_ids=[1, 2, 34])
    g = grade_v2(case, ans)
    both = ranking_ok and dependent_ok
    assert g.passed is both
    assert g.grade is (Grade.CORRECT if both else Grade.INCORRECT)
    assert kit.checks(g)["ranking"] is ranking_ok
    assert kit.checks(g)["dependent"] is dependent_ok
