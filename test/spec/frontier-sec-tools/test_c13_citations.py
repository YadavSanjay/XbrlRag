"""C13: answers cite what the tools returned, and honesty is the same as for every arm."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import answer_claims

FIND = [("find_company", {"query": "AAA"})]
TWO = [("find_company", {"query": "AAA"}), ("calculate", {"expression": "1+1"})]


@pytest.mark.criterion("C13")
def test_citations_resolve_to_tool_results(make_arm, make_case, reply):
    # oracle: requirement 23 — statements first, then cited_chunks, each id once
    final = reply(
        120,
        cited=["S2", "S1"],
        statements=[{"statement": "Alpha has ticker AAA.", "sources": ["S1"]}],
    )
    arm, _, _ = make_arm([TWO], final=final)
    answer = arm.answer(make_case())
    assert answer.chunk_ids == ["S1", "S2"]
    assert answer.fact_ids == []
    assert answer.agent["unresolved_citations"] == []
    assert answer.numeric_values == [120.0]


@pytest.mark.criterion("C13")
def test_an_invented_id_is_not_a_citation(make_arm, make_case, reply):
    # oracle: requirement 23 — S9 was never returned
    arm, _, _ = make_arm([FIND], final=reply(120, cited=["S1", "S9"]))
    answer = arm.answer(make_case())
    assert answer.chunk_ids == ["S1"]
    assert answer.agent["unresolved_citations"] == ["S9"]


@pytest.mark.criterion("C13")
def test_a_failed_result_cannot_be_cited(make_arm, make_case, reply):
    # oracle: requirement 23 — a bad call has no source id, so S1 names nothing
    arm, _, _ = make_arm([[("list_filings", {"cik": "abc"})]], final=reply(120, cited=["S1"]))
    answer = arm.answer(make_case())
    assert answer.chunk_ids == []
    assert answer.agent["unresolved_citations"] == ["S1"]
    assert answer.agent["tool_errors"] == 1


@pytest.mark.criterion("C13")
def test_the_judge_reads_the_cited_passages(make_arm, make_case, reply):
    # oracle: requirement 23 — judge_wiring.answer_claims, on the shape it already reads
    final = reply(
        120,
        statements=[{"statement": "Alpha has ticker AAA.", "sources": ["S1"]},
                    {"statement": "An uncited claim.", "sources": []}],
    )
    arm, adapter, _ = make_arm([FIND], final=final)
    answer = arm.answer(make_case())
    claims = answer_claims(answer)
    assert [c.text for c in claims] == ["Alpha has ticker AAA.", "An uncited claim."]
    assert len(claims[0].cited_passages) == 1
    assert json.loads(claims[0].cited_passages[0])["source_id"] == "S1"
    assert "Alpha Holdings" in claims[0].cited_passages[0]
    assert claims[1].cited_passages == ()
    assert claims[0].cited_passages[0] == adapter.texts(1, "tool")[0]


@pytest.mark.criterion("C13")
def test_a_reply_with_no_statements_still_gives_a_claim(make_arm, make_case, reply):
    # oracle: requirement 23 — its text is one statement, citing cited_chunks
    arm, _, _ = make_arm([FIND], final=reply(120, cited=["S1"], text="Revenue was 120."))
    answer = arm.answer(make_case())
    claims = answer_claims(answer)
    assert [c.text for c in claims] == ["Revenue was 120."]
    assert json.loads(claims[0].cited_passages[0])["source_id"] == "S1"


@pytest.mark.criterion("C13")
def test_a_refusal_stays_a_refusal(make_arm, make_case, reply):
    # oracle: requirement 24 — refused means what it means for every arm
    final = reply(None, refused=True, text="The filer has not tagged backlog.")
    arm, _, _ = make_arm([FIND], final=final)
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.numeric_values == []
    assert "has not tagged backlog" in answer.text
    assert answer.error is None
    assert answer.agent["stop"] == "answered"


@pytest.mark.criterion("C13")
def test_a_hedge_stays_a_hedge(make_arm, make_case, reply):
    # oracle: requirement 24
    arm, _, _ = make_arm(final=reply(120, hedged=True))
    answer = arm.answer(make_case())
    assert answer.hedged is True
    assert answer.refused is False


@pytest.mark.criterion("C13")
def test_a_ranking_reads_as_rows(make_arm, make_case):
    # oracle: requirement 24 — parse_structured_answer's rows, as for every arm
    final = json.dumps({
        "value": None, "unit": "USD", "rows": [{"entity": "BBB", "value": 300},
                                               {"entity": "AAA", "value": 120}],
        "refused": False, "hedged": False, "cited_chunks": ["S1"], "statements": [], "text": "x",
    })
    arm, _, _ = make_arm([FIND], final=final)
    answer = arm.answer(make_case())
    assert answer.rows == [{"entity": "BBB", "value": 300.0}, {"entity": "AAA", "value": 120.0}]


@pytest.mark.criterion("C13")
@given(cited=st.lists(st.sampled_from(["S1", "S2", "S3", "S4", "x", ""]), max_size=8))
def test_chunk_ids_are_real_sources_once_each(make_arm, make_case, reply, cited):
    # oracle: requirement 23 — two successful calls give S1 and S2, and nothing else resolves
    arm, _, _ = make_arm([TWO], final=reply(120, cited=cited))
    answer = arm.answer(make_case())
    expected = list(dict.fromkeys(c for c in cited if c in ("S1", "S2")))
    assert answer.chunk_ids == expected
    assert sorted(answer.agent["unresolved_citations"]) == sorted(
        dict.fromkeys(c for c in cited if c not in ("S1", "S2")))
    assert [s["id"] for s in answer.agent["sources"]] == ["S1", "S2"]
