"""C2: a claim that cites nothing is unsupported, whatever the judge says."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

QUESTION = "What risks did AAA name for its supply chain?"
REFERENCE = "Single-source suppliers and export controls."
KEY_POINTS = ["single-source suppliers", "export controls"]


@pytest.mark.criterion("C2")
def test_uncited_claim_is_unsupported(make_judge, reply, claim):
    # oracle: requirement 4's override — c2 has no passages, so it is unsupported even
    # though the judge says supported; correct stays the judge's (2 of 2 correct)
    text = reply([("c1", True, True), ("c2", True, True)], [True, True])
    judge, _ = make_judge(text)
    result = judge.judge(
        QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2", passages=())]
    )
    assert result.error is None
    got = [(v.claim_id, v.correct, v.supported) for v in result.verdicts]
    assert got == [("c1", True, True), ("c2", True, False)]
    assert result.unsupported_claims == 1
    # tolerance: 1/2 and 2/2 are exact in binary; abs 1e-12 is a formality
    assert result.citation_support == pytest.approx(0.5, abs=1e-12)
    assert result.correctness == pytest.approx(1.0, abs=1e-12)
    assert result.completeness == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    verdicts=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=6),
    extra=st.tuples(st.booleans(), st.booleans()),
)
def test_adding_an_uncited_claim_adds_one_unsupported(make_judge, reply, claim, verdicts, extra):
    # oracle: requirement 4 — one more claim, unsupported by the override, so the
    # unsupported count rises by one and the supported count is unchanged over n + 1
    ids = [f"c{i}" for i in range(len(verdicts))]
    rows = [(cid, c, s) for cid, (c, s) in zip(ids, verdicts, strict=True)]
    before_judge, _ = make_judge(reply(rows, [True, False]))
    before = before_judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim(cid) for cid in ids])

    rows_after = rows + [("u0", extra[0], extra[1])]
    after_judge, _ = make_judge(reply(rows_after, [True, False]))
    after = after_judge.judge(
        QUESTION, REFERENCE, KEY_POINTS,
        [claim(cid) for cid in ids] + [claim("u0", passages=())],
    )

    n = len(verdicts)
    n_supported = sum(s for _, s in verdicts)
    assert before.error is None and after.error is None
    assert after.unsupported_claims == before.unsupported_claims + 1
    # tolerance: ratios of small ints; any correct division is within 1e-12
    assert after.citation_support == pytest.approx(n_supported / (n + 1), abs=1e-12)
    assert after.verdicts[-1].supported is False
    assert after.verdicts[-1].correct is extra[0]
