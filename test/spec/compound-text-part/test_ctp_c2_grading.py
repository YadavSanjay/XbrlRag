"""C2: a compound's text part is not graded; the other parts decide (requirement 2).

The ranking part is graded by v2-grading rule 10 (a screen with disclosure); the expected
grades below are derived by hand from requirement 2 and that rule.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

RANKING_CHECKS = {"ranking": True, "ranking.ranking": True, "ranking.universe": True,
                  "ranking.exclusions": True}


@pytest.mark.criterion("C2")
def test_correct_ranking_passes(kit):
    # oracle: requirement 2 — the ranking is CORRECT and the text part is left out, so the
    # compound is CORRECT; checks carry only the ranking's keys
    g = grade_v2(kit.case(kit.compound()), kit.answer())
    assert g.grade is Grade.CORRECT and g.passed is True
    assert g.checks == RANKING_CHECKS
    assert g.reason.startswith("ranking: ")
    assert g.reason.endswith("dependent: judged, not graded")


@pytest.mark.criterion("C2")
def test_ranking_decides_wrong_silent_refused(kit):
    # oracle: requirement 2 with rule 10 — reversed rows are a wrong ranking (INCORRECT); a
    # ranking silent on DEVD is PARTIAL; a refusal of the only graded part is REFUSED
    c = kit.case(kit.compound())
    wrong = grade_v2(c, kit.answer(rows=kit.REVERSED))
    assert wrong.grade is Grade.INCORRECT and wrong.passed is False
    assert wrong.checks["ranking"] is False

    silent = grade_v2(c, kit.answer(text=kit.SILENT))
    assert silent.grade is Grade.PARTIAL and silent.passed is False
    assert silent.checks["ranking.exclusions"] is False
    assert "dependent" not in silent.checks

    refused = grade_v2(c, kit.answer(text="I cannot answer.", rows=[], fact_ids=[],
                                     refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C2")
def test_only_a_text_part(kit):
    # oracle: requirement 2 — nothing left to grade: PARTIAL, not passed, no checks
    g = grade_v2(kit.case(kit.compound(ranking=False)), kit.answer())
    assert g.grade is Grade.PARTIAL and g.passed is False
    assert g.checks == {}
    assert g.reason == "dependent: judged, not graded"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(reference=st.text(min_size=1, max_size=40).filter(lambda s: s.strip()),
       points=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=3),
       quote=st.text(max_size=40), ranking_ok=st.booleans())
def test_text_part_content_never_changes_the_grade(kit, reference, points, quote,
                                                   ranking_ok):
    # oracle: requirement 2 — the text part is not graded, so its reference answer, key
    # points and passages cannot move the grade; the ranking alone decides
    rows = kit.ROWS if ranking_ok else kit.REVERSED
    base = grade_v2(kit.case(kit.compound()), kit.answer(rows=rows))
    other = kit.text_fields(reference=reference, key_points=points,
                            passages=[{"accession": "0000900101-26-000009", "quote": quote}])
    moved = grade_v2(kit.case(kit.compound(other)), kit.answer(rows=rows))
    assert moved.grade is base.grade
    assert moved.passed is base.passed
    assert moved.checks == base.checks
    assert base.grade is (Grade.CORRECT if ranking_ok else Grade.INCORRECT)
