"""C1: the closed-book arm makes one call, sees only the question, and parses its reply."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.closed_book import ClosedBookSystem


def _approx(values):
    # tolerance: JSON float text round-trips exactly; 1e-12 relative only guards repr
    return pytest.approx(values, rel=1e-12)


@pytest.mark.criterion("C1")
def test_one_call_question_verbatim_and_reply_fields(make_gateway, make_case, reply):
    # oracle: requirements 1 and 2; expected values are the hand-built reply's own fields
    question = "What was AAA total revenue in fiscal 2024?"
    text = reply(
        391035000000,
        rows=[{"entity": "Americas", "value": 167045000000}],
        hedged=True,
        cited_chunks=["chunk-1", "chunk-2"],
    )
    gateway, adapter = make_gateway(text)
    ans = ClosedBookSystem(gateway=gateway).answer(make_case(question))

    assert adapter.calls == 1
    assert question in adapter.all_text()
    assert ans.error is None
    assert ans.numeric_values == _approx([391035000000.0, 167045000000.0])
    assert [r["entity"] for r in ans.rows] == ["Americas"]
    assert [r["value"] for r in ans.rows] == _approx([167045000000.0])
    assert ans.hedged is True
    assert ans.refused is False
    # cited_chunks in the reply never become citations for a closed-book answer
    assert ans.fact_ids == []
    assert ans.chunk_ids == []


@pytest.mark.criterion("C1")
def test_refusal_reply_is_a_refusal(make_gateway, make_case, reply):
    # oracle: requirement 2 (the model may say it doesn't know); the reply's own fields
    gateway, adapter = make_gateway(reply(None, refused=True))
    ans = ClosedBookSystem(gateway=gateway).answer(make_case())
    assert adapter.calls == 1
    assert ans.refused is True
    assert ans.numeric_values == []
    assert ans.rows == []
    assert ans.error is None


@pytest.mark.criterion("C1")
def test_no_retrieved_or_ground_truth_text_reaches_the_model(make_gateway, make_case, reply):
    # oracle: requirement 2 — the model sees the question and nothing retrieved; the
    # case's ground truth and ids are not part of the question
    gateway, adapter = make_gateway(reply(1.0))
    case = make_case("How much cash did AAA hold at the end of fiscal 2024?", case_id="zz-case-77")
    ClosedBookSystem(gateway=gateway).answer(case)
    sent = adapter.all_text()
    assert case.question in sent
    assert "zz-case-77" not in sent
    assert "391035000000" not in sent


_QUESTION = st.text(
    alphabet=st.sampled_from(
        list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.?-")
    ),
    min_size=5,
    max_size=80,
).filter(lambda s: s.strip() == s and s != "")

_OBJECTS = st.fixed_dictionaries(
    {
        "value": st.one_of(st.none(), st.floats(min_value=-1e13, max_value=1e13, allow_nan=False)),
        "rows": st.lists(
            st.fixed_dictionaries(
                {
                    "entity": st.sampled_from(["AAA", "BBB", "Europe", "Services"]),
                    "value": st.floats(min_value=-1e13, max_value=1e13, allow_nan=False),
                }
            ),
            max_size=4,
        ),
        "refused": st.booleans(),
        "hedged": st.booleans(),
        "cited_chunks": st.lists(st.sampled_from(["c-1", "c-2", "c-3"]), max_size=3),
    }
)


@pytest.mark.criterion("C1")
@given(obj=_OBJECTS, question=_QUESTION)
def test_any_reply_round_trips_without_citations(make_gateway, make_case, obj, question):
    # oracle: closed form — the random reply object's own fields are the expected values
    gateway, adapter = make_gateway(json.dumps(obj))
    ans = ClosedBookSystem(gateway=gateway).answer(make_case(question))

    expected_values = ([obj["value"]] if obj["value"] is not None else []) + [
        r["value"] for r in obj["rows"]
    ]
    assert adapter.calls == 1
    assert question in adapter.all_text()
    assert ans.error is None
    assert ans.numeric_values == _approx(expected_values)
    assert [r["entity"] for r in ans.rows] == [r["entity"] for r in obj["rows"]]
    assert ans.refused is obj["refused"]
    assert ans.hedged is obj["hedged"]
    assert ans.fact_ids == []
    assert ans.chunk_ids == []
