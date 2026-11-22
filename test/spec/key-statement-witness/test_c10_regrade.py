"""C10: a key change re-grades every arm's stored answers on the same changed cases.

Oracle: hand grading of the recorded answers (fixtures/recorded_answers.json) at the cases'
``rel_tol`` 1e-4: an answer equal to the expected value is CORRECT, one 2.6 times or 0.38
times it is INCORRECT. The two readings of KO's FY2025 accounts payable (5,649 in note 8,
14,813 on the balance sheet) are hand-checked in KO's FY2025 10-K.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.keys.statement_witness import key_change_regrade


def _cases(kit, which):
    out = []
    for cid, c in kit.answers["cases"].items():
        out.append(EvalCase(id=cid, question=c["question"], kind="lookup",
                            expected_value=float(c[f"{which}_expected"]), rel_tol=1e-4,
                            require_citation=False, unit=c["unit"]))
    return out


def _runs(kit):
    return {arm: [dict(r) for r in rows] for arm, rows in kit.answers["runs"].items()}


@pytest.mark.criterion("C10")
def test_each_arm_moves_by_its_own_answer(kit):
    # oracle: hand grades: xbrlrag answered 5,649 (old key) -> CORRECT then INCORRECT against
    # 14,813; frontier answered 14,813 -> INCORRECT then CORRECT; TSLA's key did not change
    got = key_change_regrade(_cases(kit, "old"), _cases(kit, "new"), _runs(kit))
    assert set(got) == {"xbrlrag_full", "frontier_sec_tools"}
    assert got["xbrlrag_full"] == {
        "changed": ["ko-ap-fy2025"], "before": {"ko-ap-fy2025": "CORRECT"},
        "after": {"ko-ap-fy2025": "INCORRECT"}, "transitions": {"CORRECT -> INCORRECT": 1}}
    assert got["frontier_sec_tools"] == {
        "changed": ["ko-ap-fy2025"], "before": {"ko-ap-fy2025": "INCORRECT"},
        "after": {"ko-ap-fy2025": "CORRECT"}, "transitions": {"INCORRECT -> CORRECT": 1}}


@pytest.mark.criterion("C10")
def test_an_arm_missing_a_changed_case_is_refused(kit):
    # oracle: requirement 10, "a re-grade never moves one arm alone"
    arm = "frontier_sec_tools"
    runs = _runs(kit)
    runs[arm] = [r for r in runs[arm] if r["case_id"] != "ko-ap-fy2025"]
    with pytest.raises(ValueError):
        key_change_regrade(_cases(kit, "old"), _cases(kit, "new"), runs)
    # an arm missing only an unchanged case is fine: unchanged cases are not re-graded
    runs = _runs(kit)
    runs[arm] = [r for r in runs[arm] if r["case_id"] != "tsla-rev-fy2023"]
    got = key_change_regrade(_cases(kit, "old"), _cases(kit, "new"), runs)
    assert got["frontier_sec_tools"]["changed"] == ["ko-ap-fy2025"]
    with pytest.raises(ValueError):
        key_change_regrade(_cases(kit, "old"), _cases(kit, "new")[:1], _runs(kit))


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
def test_unchanged_keys_change_nothing(kit):
    # oracle: requirement 10: identical old and new cases have no changed case in any arm
    got = key_change_regrade(_cases(kit, "old"), _cases(kit, "old"), _runs(kit))
    assert got == {arm: {"changed": [], "before": {}, "after": {}, "transitions": {}}
                   for arm in ("xbrlrag_full", "frontier_sec_tools")}


@pytest.mark.criterion("C10")
@given(answer=st.sampled_from([5_649e6, 14_813e6, 9_000e6]))
def test_arms_with_the_same_answer_move_alike(kit, answer):
    # oracle: hand grading at rel_tol 1e-4: 5,649 is right only under the old key, 14,813 only
    # under the new one, 9,000 under neither; two arms giving one answer get one transition
    runs = {arm: [{"case_id": "ko-ap-fy2025", "produced": {"text": "", "numeric_values": [answer]}}]
            for arm in ("a", "b")}
    got = key_change_regrade(_cases(kit, "old"), _cases(kit, "new"), runs)
    before = "CORRECT" if answer == pytest.approx(5_649e6, rel=1e-4) else "INCORRECT"
    after = "CORRECT" if answer == pytest.approx(14_813e6, rel=1e-4) else "INCORRECT"
    for arm in ("a", "b"):
        assert got[arm]["before"] == {"ko-ap-fy2025": before}
        assert got[arm]["after"] == {"ko-ap-fy2025": after}
        assert got[arm]["transitions"] == ({} if before == after else {f"{before} -> {after}": 1})
    assert got["a"] == got["b"]
