"""C2: the verifier drops a statement citing any unresolved id, whatever the LLM says (rules 1, 4).

The verifier is the real ``XbrlFaithfulnessVerifier``; its LLM (a scripted adapter behind the
real gateway) answers SUPPORTED for every statement it is sent, so any drop is deterministic.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.observability.trace_context import trace_query

PREFIX = "failed faithfulness check:"


def _events(tc, check):
    return [e for e in tc.events if e.get("kind") == "decision"
            and e.get("point") == "citation_integrity" and e.get("check") == check]


def _recorded(ci, details):
    steps, plan_steps, chunks = ci.apple_evidence()
    return ci.response(details, steps, plan_steps=plan_steps, chunks=chunks)


@pytest.mark.criterion("C2")
def test_invented_ids_drop_their_statements_and_are_recorded(ci):
    # oracle: rule 1 and requirement 4, applied by hand to five recorded statements
    details = [
        ci.detail("Apple's fiscal 2024 revenue was $391.0 billion.", ["13253"]),          # keep
        ci.detail("Apple's net income was $93.7 billion.", ["13289", "13289999"]),         # drop
        ci.detail("Services grew on advertising, the App Store and cloud services.",
                  [], [ci.CHUNK_AAPL_MDNA]),                                                # keep
        ci.detail("Services gross margin was 73.9% on revenue of $391.0 billion.",
                  ["13253"], [ci.CHUNK_AAPL_MDNA, "chunk-made-up"]),                        # drop
        ci.detail("Apple's net margin was 24.0%.", ["m_aapl"]),                             # keep
    ]
    verifier, adapter = ci.make_verifier()
    with trace_query("recorded question") as tc:
        out = verifier.verify(_recorded(ci, details))

    assert out.answer_details == [details[0], details[2], details[4]]
    dropped = {p["question_fragment"]: p["reason"] for p in out.unanswered_parts}
    assert set(dropped) == {details[1]["statement"], details[3]["statement"]}
    assert dropped[details[1]["statement"]].startswith(PREFIX)
    assert "13289999" in dropped[details[1]["statement"]]
    assert "chunk-made-up" in dropped[details[3]["statement"]]

    verdicts = {v["index"]: v for v in out.metadata["faithfulness"]["verdicts"]}
    for i, ids in ((1, ["13289999"]), (3, ["chunk-made-up"])):
        assert verdicts[i]["verdict"] == "UNSUPPORTED"
        assert verdicts[i]["check"] == "unresolved_ids"
        assert verdicts[i]["unresolved_ids"] == ids
        assert verdicts[i]["statement"] == details[i]["statement"]
    assert out.metadata["fully_answered"] is False

    events = _events(tc, "unresolved_ids")
    assert sorted((e["index"], tuple(e["ids"])) for e in events) == [
        (1, ("13289999",)), (3, ("chunk-made-up",))]

    # decided without the LLM: the kept statements were sent, the dropped ones were not
    assert adapter.invoked == 1
    assert ci.was_sent(adapter, details[0]["statement"])
    assert not ci.was_sent(adapter, details[1]["statement"])
    assert not ci.was_sent(adapter, details[3]["statement"])


@pytest.mark.criterion("C2")
def test_no_llm_call_when_every_statement_cites_an_invented_id(ci):
    # oracle: requirement 4 — a deterministic drop is never sent to the verifier LLM
    details = [ci.detail("Apple's revenue was $391.0 billion.", ["13253", "f1"]),
               ci.detail("Walmart led the screen.", ["s9"])]
    verifier, adapter = ci.make_verifier()
    out = verifier.verify(_recorded(ci, details))
    assert out.answer_details == []
    assert adapter.invoked == 0
    assert [v["unresolved_ids"] for v in out.metadata["faithfulness"]["verdicts"]] == [
        ["f1"], ["s9"]]


_REAL = ["13253", "m_aapl", "s1", "6854", "b1", "13550", "gp_der", "13262"]
_WORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=3, max_size=30)


@pytest.mark.criterion("C2")
@settings(max_examples=40)
@given(statements=st.lists(
    st.tuples(_WORDS,
              st.lists(st.sampled_from(_REAL), min_size=1, max_size=3),
              st.lists(st.sampled_from(["9000000001", "zz_1", "f1", "n1"]), max_size=2),
              st.booleans()),
    min_size=1, max_size=8))
def test_survivors_are_exactly_the_statements_without_invented_ids(ci, statements):
    # oracle: rule 1 — a statement survives (the LLM says SUPPORTED to everything) exactly when
    # all its ids are in the hand-listed resolving set; statements carry no digits, so rule 3
    # never applies
    details = []
    for k, (words, real, invented, with_chunk) in enumerate(statements):
        text = f"Statement {'abcdefghijklmnopqrstuvwxyz'[k % 26]} {words}."
        details.append(ci.detail(text, real + invented,
                                 [ci.CHUNK_AAPL_MDNA] if with_chunk else []))
    verifier, _ = ci.make_verifier()
    out = verifier.verify(_recorded(ci, details))
    expected = [d for d, (_, _, invented, _) in zip(details, statements, strict=True)
                if not invented]
    assert out.answer_details == expected
    dropped = [p["question_fragment"] for p in out.unanswered_parts]
    assert dropped == [d["statement"] for d, s in zip(details, statements, strict=True) if s[2]]
