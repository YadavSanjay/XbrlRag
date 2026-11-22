"""C4: naive-RAG citations are the reply's cited chunks, restricted to what was retrieved."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.naive_rag import NaiveRagSystem

RETRIEVED = ["chunk-a", "chunk-b", "chunk-c", "chunk-d"]


def _answer(cited, make_gateway, make_embedder, make_qdrant, point, make_case, reply,
            filtered=False):
    gateway, adapter = make_gateway(reply(42.0, cited_chunks=cited))
    qdrant = make_qdrant([point(cid, f"Passage {cid} text.") for cid in RETRIEVED])
    system = NaiveRagSystem(
        filtered=filtered, gateway=gateway, embedder=make_embedder(), vector_db_client=qdrant
    )
    ans = system.answer(make_case())
    assert adapter.calls == 1
    return ans


def _expected(cited):
    """First-appearance order, deduplicated, only retrieved ids (requirement 6)."""
    out = []
    for c in cited:
        if c in RETRIEVED and c not in out:
            out.append(c)
    return out


@pytest.mark.criterion("C4")
def test_hand_built_citation_list(make_gateway, make_embedder, make_qdrant, point, make_case,
                                  reply):
    # oracle: requirement 6 by hand — c is cited first, b twice, ghost was never retrieved
    cited = ["chunk-c", "chunk-ghost", "chunk-b", "chunk-c", "chunk-b"]
    ans = _answer(cited, make_gateway, make_embedder, make_qdrant, point, make_case, reply)
    assert ans.chunk_ids == ["chunk-c", "chunk-b"]
    assert ans.fact_ids == []
    assert ans.numeric_values == pytest.approx([42.0], rel=1e-12)  # tolerance: exact JSON float


@pytest.mark.criterion("C4")
def test_only_unretrieved_citations_give_none(make_gateway, make_embedder, make_qdrant, point,
                                              make_case, reply):
    # oracle: requirement 6 — every cited id is foreign, so all are dropped
    ans = _answer(["x-1", "x-2"], make_gateway, make_embedder, make_qdrant, point, make_case,
                  reply)
    assert ans.chunk_ids == []
    assert ans.fact_ids == []
    assert ans.numeric_values == pytest.approx([42.0], rel=1e-12)  # tolerance: exact JSON float


_CITED = st.lists(
    st.sampled_from(RETRIEVED + ["chunk-ghost", "chunk-zz", "chunk-a2"]), max_size=12
)


@pytest.mark.criterion("C4")
@given(cited=_CITED, filtered=st.booleans())
def test_random_citation_lists(make_gateway, make_embedder, make_qdrant, point, make_case, reply,
                               cited, filtered):
    # oracle: closed form of requirement 6 computed in the test (_expected)
    ans = _answer(cited, make_gateway, make_embedder, make_qdrant, point, make_case, reply,
                  filtered=filtered)
    assert ans.chunk_ids == _expected(cited)
    assert ans.fact_ids == []
    assert ans.numeric_values == pytest.approx([42.0], rel=1e-12)  # tolerance: exact JSON float
