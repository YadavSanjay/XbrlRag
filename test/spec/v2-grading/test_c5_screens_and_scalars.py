"""C5: screens, peer-relative and scalars with context (count, sum, median, rank, CAGR, ratio).

Oracle: requirements 7-11 of docs/specs/v2-grading.md, S3 and S4. Expected values are the
conftest cases' (H-X screen DEVA 300m > DEVB 200m; count 2; sum 500m; rank 2; v2 peer
subject DEVA at rank 2 of DEVB, DEVA), read by hand.
"""

from __future__ import annotations

import pytest

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import grade_v2

M = 1e6
BN = 1e9
SCREEN_ROWS = [{"ticker": "DEVA", "value": 300 * M, "fact_id": 1},
               {"ticker": "DEVB", "value": 200 * M, "fact_id": 2}]
HX_DISCLOSED = "Among the 4 companies DEVA leads, then DEVB; DEVD could not be ranked."
HX_SILENT_EXCL = "Among the 4 companies DEVA leads, then DEVB."


@pytest.mark.criterion("C5")
def test_hx_screen_disclosure_gates_the_pass(kit):
    # oracle: requirements 10 and 7, S3 — a correct ranking passes only with its universe
    # and exclusion; silent on DEVD it is PARTIAL (value still correct); a reversed
    # ranking is INCORRECT.
    case = kit.case(kit.hx_screen())
    ok = grade_case(case, kit.answer(text=HX_DISCLOSED, rows=SCREEN_ROWS, fact_ids=[1, 2]))
    assert ok.grade is Grade.CORRECT and ok.passed is True
    assert kit.checks(ok) == {"ranking": True, "universe": True, "exclusions": True}

    silent = grade_case(case, kit.answer(text=HX_SILENT_EXCL, rows=SCREEN_ROWS,
                                         fact_ids=[1, 2]))
    assert silent.grade is Grade.PARTIAL and silent.passed is False
    assert silent.value_correct is True
    assert silent.confident_wrong is False
    assert kit.checks(silent) == {"ranking": True, "universe": True, "exclusions": False}
    assert "exclusions" in silent.reason

    rev = [{"ticker": "DEVB", "value": 200 * M, "fact_id": 2},
           {"ticker": "DEVA", "value": 300 * M, "fact_id": 1}]
    wrong = grade_case(case, kit.answer(text=HX_DISCLOSED, rows=rev, fact_ids=[1, 2]))
    assert wrong.grade is Grade.INCORRECT
    assert kit.checks(wrong) == {"ranking": False, "universe": True, "exclusions": True}


@pytest.mark.criterion("C5")
def test_v2_peer_relative_rank(kit):
    # oracle: requirement 11 — DEVA is 2nd among the rows DEVB, DEVA; universe "peers" has 3
    # members, 2 ranked; ZZZ is the exclusion.
    case = kit.case(kit.v2_peer())
    text = "DEVA ranks second of the 3 peers; ZZZ is not in the corpus."
    rows = [{"ticker": "DEVB", "value": 220 * BN, "fact_id": 5},
            {"ticker": "DEVA", "value": 121 * BN, "fact_id": 6}]
    g = grade_v2(case, kit.answer(text=text, rows=rows, fact_ids=[5, 6]))
    assert g.grade is Grade.CORRECT and g.passed is True
    assert g.matched_value == pytest.approx(2.0, abs=0.0)  # a rank: exact
    assert kit.checks(g) == {"rank": True, "universe": True, "exclusions": True}

    by_value = grade_v2(case, kit.answer(text=text, values=[2.0], fact_ids=[6]))
    assert by_value.grade is Grade.CORRECT
    assert kit.checks(by_value)["rank"] is True

    # Updated by docs/specs/peer-rank-grading.md (requirement 4): the rank is the stated one,
    # else the rows' values, never the row order. Reversed rows with "second" stated stay
    # right; stating "first" is wrong.
    reordered = grade_v2(case, kit.answer(text=text, rows=list(reversed(rows)),
                                          fact_ids=[5, 6]))
    assert reordered.grade is Grade.CORRECT
    wrong_text = "DEVA ranks first of the 3 peers; ZZZ is not in the corpus."
    wrong = grade_v2(case, kit.answer(text=wrong_text, rows=rows, fact_ids=[5, 6]))
    assert wrong.grade is Grade.INCORRECT
    assert kit.checks(wrong)["rank"] is False

    silent = grade_v2(case, kit.answer(text="DEVA ranks second.", rows=rows, fact_ids=[5, 6]))
    assert silent.grade is Grade.PARTIAL and silent.passed is False
    assert kit.checks(silent) == {"rank": True, "universe": False, "exclusions": False}

    refused = grade_v2(case, kit.answer(text="Cannot rank.", refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C5")
def test_count_rank_sum_tolerances(kit):
    # oracle: requirement 9 — count and rank carry rel_tol 0.0 (exact); the sum carries 1e-2:
    # 503m is 0.6 % off 500m (passes), 510m is 2 % off (fails).
    text = "Of the 4 filers, DEVD was excluded."
    count = kit.case(kit.hx_count())
    g = grade_v2(count, kit.answer(text=text, values=[2.0], fact_ids=[1, 2]))
    assert g.grade is Grade.CORRECT
    assert kit.checks(g) == {"value": True, "universe": True, "exclusions": True}
    off = grade_v2(count, kit.answer(text=text, values=[2.5], fact_ids=[1, 2]))
    assert off.grade is Grade.INCORRECT
    assert kit.checks(off)["value"] is False

    rank = kit.case(kit.hx_rank())
    assert grade_v2(rank, kit.answer(text=text, values=[2.0],
                                     fact_ids=[1, 2, 3])).grade is Grade.CORRECT
    assert grade_v2(rank, kit.answer(text=text, values=[3.0],
                                     fact_ids=[1, 2, 3])).grade is Grade.INCORRECT

    total = kit.case(kit.hx_sum())
    assert grade_v2(total, kit.answer(text=text, values=[503 * M],
                                      fact_ids=[1, 2])).grade is Grade.CORRECT
    assert grade_v2(total, kit.answer(text=text, values=[510 * M],
                                      fact_ids=[1, 2])).grade is Grade.INCORRECT

    med = kit.case(kit.hx_median())
    silent = grade_v2(med, kit.answer(text="The median is 200 million.", values=[200 * M],
                                      fact_ids=[1, 2, 3]))
    assert silent.grade is Grade.PARTIAL
    assert kit.checks(silent) == {"value": True, "universe": False, "exclusions": False}


@pytest.mark.criterion("C5")
def test_cagr_not_meaningful_and_ratio_fallback(kit):
    # oracle: requirement 9 with S4 — a not-meaningful CAGR declined is CORRECT (n/m rule);
    # a ratio whose key used the closing balance needs the fallback stated (requirement 8).
    cagr = {**kit.v2_with_shape("compute.cagr"), "kind": "compute",
            "expect_not_meaningful": True}
    cagr.pop("expected_value")
    g = grade_v2(kit.case(cagr), kit.answer(text="Not meaningful: the base is negative.",
                                            refused=True))
    assert g.grade is Grade.CORRECT
    assert kit.checks(g) == {"value": True}

    fallback = kit.case(kit.v2_ratio("closing"))
    said = grade_v2(fallback, kit.answer(text="Net margin was 25%, on year-end balances.",
                                         values=[0.25], fact_ids=[7]))
    assert said.grade is Grade.CORRECT
    assert kit.checks(said) == {"value": True, "fallback_disclosed": True}
    unsaid = grade_v2(fallback, kit.answer(text="Net margin was 25%.", values=[0.25],
                                           fact_ids=[7]))
    assert unsaid.grade is Grade.PARTIAL and unsaid.passed is False
    assert kit.checks(unsaid) == {"value": True, "fallback_disclosed": False}

    plain = grade_v2(kit.case(kit.v2_ratio()), kit.answer(text="Net margin was 25%.",
                                                          values=[0.25], fact_ids=[7]))
    assert plain.grade is Grade.CORRECT
    assert kit.checks(plain) == {"value": True}


_ORDER = {Grade.CORRECT: 3, Grade.PARTIAL: 2, Grade.HEDGED_MISS: 1, Grade.INCORRECT: 1,
          Grade.REFUSED: 1, Grade.ERROR: 0}


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("which", ["hx_screen", "hx_count", "hx_sum", "v2_peer"])
def test_removing_disclosure_never_raises_a_grade(kit, which):
    # oracle: requirements 9-11 — disclosure only ever downgrades; the value/ranking/rank
    # check is unchanged when the disclosure sentence is removed.
    case = kit.case(getattr(kit, which)())
    rows = {"hx_screen": SCREEN_ROWS,
            "v2_peer": [{"ticker": "DEVB", "value": 220 * BN, "fact_id": 5},
                        {"ticker": "DEVA", "value": 121 * BN, "fact_id": 6}]}.get(which, [])
    values = {"hx_count": [2.0], "hx_sum": [500 * M]}.get(which, [])
    disclosed = ("Among the 4 filers; DEVD could not be ranked." if which != "v2_peer"
                 else "Among the 3 peers; ZZZ is not in the corpus.")
    full = grade_v2(case, kit.answer(text="Result. " + disclosed, rows=rows, values=values,
                                     fact_ids=[1, 2, 5, 6]))
    bare = grade_v2(case, kit.answer(text="Result.", rows=rows, values=values,
                                     fact_ids=[1, 2, 5, 6]))
    assert full.grade is Grade.CORRECT
    assert _ORDER[bare.grade] <= _ORDER[full.grade]
    assert bare.grade is Grade.PARTIAL
    core = {k: v for k, v in kit.checks(full).items() if k in ("value", "ranking", "rank")}
    assert core and core == {k: kit.checks(bare)[k] for k in core}
    assert {k for k, v in kit.checks(bare).items() if not v} == {"universe", "exclusions"}
