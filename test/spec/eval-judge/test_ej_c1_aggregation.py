"""C1: the sub-scores are computed in code from the per-claim verdicts."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

QUESTION = "How did AAA describe its revenue trend in fiscal 2024?"
REFERENCE = "Revenue grew 5%, driven by services; hardware declined."
KEY_POINTS = ["revenue grew", "5%", "services drove growth", "hardware declined"]


@pytest.mark.criterion("C1")
def test_hand_counted_scores(make_judge, reply, claim):
    # oracle: requirement 4, hand counts — correct: c1, c2 (2 of 3); supported: c1, c3
    # (2 of 3, so 1 unsupported); covered: points 0, 2, 3 (3 of 4)
    text = reply(
        [("c3", False, True), ("c1", True, True), ("c2", True, False)],
        [True, False, True, True],
    )
    judge, adapter = make_judge(text)
    result = judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2"), claim("c3")])

    assert result.error is None
    # tolerance: three-term ratios of small ints; any correct division is within 1e-12
    assert result.correctness == pytest.approx(2 / 3, abs=1e-12)
    assert result.completeness == pytest.approx(3 / 4, abs=1e-12)
    assert result.citation_support == pytest.approx(2 / 3, abs=1e-12)
    assert result.unsupported_claims == 1
    got = [(v.claim_id, v.correct, v.supported) for v in result.verdicts]
    assert got == [("c1", True, True), ("c2", True, False), ("c3", False, True)]
    assert result.judge_version == "v1:claude-opus-5-5"
    assert adapter.calls == 1


_VERDICTS = st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=8)
_COVERED = st.lists(st.booleans(), min_size=1, max_size=6)


@pytest.mark.criterion("C1")
@given(verdicts=_VERDICTS, covered=_COVERED)
def test_scores_follow_the_formulas(make_judge, reply, claim, verdicts, covered):
    # oracle: requirement 4's formulas, evaluated here on the generated verdicts
    ids = [f"c{i}" for i in range(len(verdicts))]
    rows = [(cid, c, s) for cid, (c, s) in zip(ids, verdicts, strict=True)]
    judge, _ = make_judge(reply(rows, covered))
    result = judge.judge(
        QUESTION, REFERENCE, [f"point {i}" for i in range(len(covered))],
        [claim(cid) for cid in ids],
    )
    n = len(verdicts)
    n_correct = sum(c for c, _ in verdicts)
    n_supported = sum(s for _, s in verdicts)
    assert result.error is None
    # tolerance: ratios of small ints; any correct division is within 1e-12
    assert result.correctness == pytest.approx(n_correct / n, abs=1e-12)
    assert result.completeness == pytest.approx(sum(covered) / len(covered), abs=1e-12)
    assert result.citation_support == pytest.approx(n_supported / n, abs=1e-12)
    assert result.unsupported_claims == n - n_supported
    assert [(v.claim_id, v.correct, v.supported) for v in result.verdicts] == rows
    assert result.judge_version == "v1:claude-opus-5-5"


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(verdicts=_VERDICTS, covered=_COVERED, data=st.data())
def test_permuting_claims_keeps_scores(make_judge, reply, claim, verdicts, covered, data):
    # oracle: requirement 4 — the scores are counts, so input order cannot matter, and
    # verdicts follow the input order
    ids = [f"c{i}" for i in range(len(verdicts))]
    rows = [(cid, c, s) for cid, (c, s) in zip(ids, verdicts, strict=True)]
    order = data.draw(st.permutations(list(range(len(ids)))))
    points = [f"point {i}" for i in range(len(covered))]

    judge_a, _ = make_judge(reply(rows, covered))
    first = judge_a.judge(QUESTION, REFERENCE, points, [claim(cid) for cid in ids])
    judge_b, _ = make_judge(reply(rows, covered))
    second = judge_b.judge(QUESTION, REFERENCE, points, [claim(ids[i]) for i in order])

    assert first.error is None and second.error is None
    # tolerance: the same counts divided by the same n; equal to 1e-12
    assert second.correctness == pytest.approx(first.correctness, abs=1e-12)
    assert second.completeness == pytest.approx(first.completeness, abs=1e-12)
    assert second.citation_support == pytest.approx(first.citation_support, abs=1e-12)
    assert second.unsupported_claims == first.unsupported_claims
    assert [v.claim_id for v in second.verdicts] == [ids[i] for i in order]
    assert [(v.claim_id, v.correct, v.supported) for v in second.verdicts] == [
        rows[i] for i in order
    ]
