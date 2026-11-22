"""C4: a malformed reply or a failed call is a judge_error; RunHalt propagates."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.base import LLMRefusalError
from src.llm.run_budget import RunBudget, RunHalt
from src.observability.trace_context import trace_query

QUESTION = "What did AAA say about competition?"
REFERENCE = "Competition is intense and pricing pressure rising."
KEY_POINTS = ["intense competition", "pricing pressure"]

_OK_CLAIMS = [
    {"id": "c1", "correct": True, "supported": True},
    {"id": "c2", "correct": False, "supported": True},
]
_OK_POINTS = [{"index": 0, "covered": True}, {"index": 1, "covered": False}]


def _obj(claims=_OK_CLAIMS, points=_OK_POINTS, drop=None) -> str:
    body = {"claims": claims, "key_points": points}
    if drop is not None:
        body.pop(drop)
    return json.dumps(body)


MALFORMED = {
    "not_json": "The answer looks fine to me.",
    "json_array": json.dumps([_OK_CLAIMS, _OK_POINTS]),
    "claims_missing": _obj(drop="claims"),
    "key_points_missing": _obj(drop="key_points"),
    "claims_not_list": _obj(claims={"c1": True}),
    "claim_missing": _obj(claims=_OK_CLAIMS[:1]),
    "claim_extra": _obj(claims=_OK_CLAIMS + [{"id": "c9", "correct": True, "supported": True}]),
    "claim_duplicate": _obj(claims=_OK_CLAIMS + [_OK_CLAIMS[0]]),
    "correct_not_bool": _obj(
        claims=[{"id": "c1", "correct": "yes", "supported": True}, _OK_CLAIMS[1]]
    ),
    "supported_missing": _obj(claims=[{"id": "c1", "correct": True}, _OK_CLAIMS[1]]),
    "supported_int": _obj(claims=[{"id": "c1", "correct": True, "supported": 1}, _OK_CLAIMS[1]]),
    "point_missing": _obj(points=_OK_POINTS[:1]),
    "point_out_of_range": _obj(points=[_OK_POINTS[0], {"index": 5, "covered": True}]),
    "point_duplicate": _obj(points=[_OK_POINTS[0], _OK_POINTS[0]]),
    "covered_not_bool": _obj(points=[_OK_POINTS[0], {"index": 1, "covered": None}]),
}


def _assert_judge_error(result) -> None:
    assert isinstance(result.error, str) and result.error.startswith("judge_error: ")
    assert result.correctness is None
    assert result.completeness is None
    assert result.citation_support is None
    assert result.unsupported_claims is None
    assert list(result.verdicts) == []
    assert result.judge_version == "v1:claude-opus-5-5"


@pytest.mark.criterion("C4")
def test_the_well_formed_reply_scores(make_judge, claim):
    # oracle: requirement 4, hand counts — the base reply every malformed one departs from:
    # correct 1 of 2, supported 2 of 2, covered 1 of 2
    judge, _ = make_judge(_obj())
    result = judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2")])
    assert result.error is None
    # tolerance: halves and ones are exact in binary; abs 1e-12 is a formality
    assert result.correctness == pytest.approx(0.5, abs=1e-12)
    assert result.citation_support == pytest.approx(1.0, abs=1e-12)
    assert result.completeness == pytest.approx(0.5, abs=1e-12)
    assert result.unsupported_claims == 0


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("kind", sorted(MALFORMED))
def test_malformed_reply_is_judge_error(make_judge, claim, kind):
    # oracle: requirement 5's list of malformed replies
    judge, adapter = make_judge(MALFORMED[kind])
    result = judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2")])
    assert adapter.calls == 1
    _assert_judge_error(result)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    "error",
    [ValueError("provider rejected the request"), LLMRefusalError("declined")],
    ids=["provider_error", "refusal"],
)
def test_failed_call_is_judge_error(make_judge, claim, error):
    # oracle: requirement 5 — a provider error or refusal is a judge_error, not a raise
    judge, _ = make_judge(_obj(), error=error)
    result = judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2")])
    _assert_judge_error(result)


@pytest.mark.criterion("C4")
def test_run_halt_propagates(make_judge, claim):
    # oracle: requirement 5 — RunBudget(max_llm_calls=0) trips before the first call
    # inside an open trace, and RunHalt is never turned into a judge_error
    judge, adapter = make_judge(_obj(), run_budget=RunBudget(max_llm_calls=0))
    with trace_query("eval-judge spec"):
        with pytest.raises(RunHalt):
            judge.judge(QUESTION, REFERENCE, KEY_POINTS, [claim("c1"), claim("c2")])
    assert adapter.calls == 0


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    verdicts=st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=6),
    data=st.data(),
)
def test_dropping_a_claim_entry_turns_a_score_into_an_error(make_judge, reply, claim,
                                                            verdicts, data):
    # oracle: requirement 5 — the reply's ids must be exactly the input ids
    ids = [f"c{i}" for i in range(len(verdicts))]
    rows = [(cid, c, s) for cid, (c, s) in zip(ids, verdicts, strict=True)]
    claims = [claim(cid) for cid in ids]

    whole, _ = make_judge(reply(rows, [True, True]))
    scored = whole.judge(QUESTION, REFERENCE, KEY_POINTS, claims)
    assert scored.error is None
    assert scored.unsupported_claims == sum(1 for _, _, s in rows if not s)

    gone = data.draw(st.integers(min_value=0, max_value=len(rows) - 1))
    partial, _ = make_judge(reply(rows[:gone] + rows[gone + 1:], [True, True]))
    _assert_judge_error(partial.judge(QUESTION, REFERENCE, KEY_POINTS, claims))
