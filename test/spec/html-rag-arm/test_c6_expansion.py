"""C6: neighbours and sibling parts.

One candidate is kept in most tests, so what's listed after it can only arrive by expansion.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

AAA = "0000000001"
ALL = {"ciks": [AAA], "fiscal_years": [2024]}
ONE = {"rerank_candidates": 1, "context_top_k": 1}


def _chain(payload, chunk_id_of, n: int) -> list[dict]:
    """n chunks linked in order, as html10k-chunks requirement 13 links them."""
    accession = "0000000001-25-000001"
    ids = [chunk_id_of(accession, i * 10, i * 10 + 10) for i in range(n)]
    return [
        payload(
            f"p{i}", start=i * 10, end=i * 10 + 10,
            prev_chunk_id=ids[i - 1] if i else None,
            next_chunk_id=ids[i + 1] if i + 1 < n else None,
        )
        for i in range(n)
    ]


@pytest.mark.criterion("C6")
def test_a_kept_chunk_brings_its_neighbours(make_retriever, payload, chunk_id_of):
    # oracle: requirement 12, by hand — p1 is kept; p0 then p2 come after it, marked, unscored
    first, kept, last = _chain(payload, chunk_id_of, 3)
    retriever, client, _, _ = make_retriever([kept, first, last], default=0.75, **ONE)
    result = retriever.retrieve("q", [ALL])
    assert [c.text for c in result.chunks] == ["passage p1", "passage p0", "passage p2"]
    assert [c.is_expansion for c in result.chunks] == [False, True, True]
    assert [c.rerank_score is None for c in result.chunks] == [False, True, True]
    assert len(client.retrieve_calls) == 1
    call = client.retrieve_calls[0]
    assert call["collection_name"] == "html10k_text"
    assert list(call["ids"]) == [first["chunk_id"], last["chunk_id"]]
    assert call["with_payload"] is True


@pytest.mark.criterion("C6")
def test_a_table_part_brings_its_sibling_parts(make_retriever, table_chunk, chunk_id_of):
    # oracle: requirement 12 — part 1 of 4: prev part 0, next part 2, then the sibling not yet
    # chosen, part 3, whose id only html10k-chunks requirement 12's formula gives
    accession = "0000000001-25-000001"
    ids = [chunk_id_of(accession, 500, 900, part) for part in range(4)]
    parts = [
        table_chunk(start=500, end=900, part=part, parts=4,
                    prev_chunk_id=ids[part - 1] if part else None,
                    next_chunk_id=ids[part + 1] if part < 3 else None)
        for part in range(4)
    ]
    retriever, client, _, _ = make_retriever(
        [parts[1], parts[0], parts[2], parts[3]], default=0.75, **ONE
    )
    result = retriever.retrieve("q", [ALL])
    assert list(client.retrieve_calls[0]["ids"]) == [ids[0], ids[2], ids[3]]
    assert [c.part for c in result.chunks] == [1, 0, 2, 3]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(
    "score, expanded", [(0.4375, False), (0.5, True), (0.5625, True)],
)
def test_the_threshold(make_retriever, payload, chunk_id_of, score, expanded):
    # oracle: requirement 12 — at least the threshold, 0.5
    first, kept, last = _chain(payload, chunk_id_of, 3)
    retriever, client, _, _ = make_retriever([kept, first, last], default=score, **ONE)
    result = retriever.retrieve("q", [ALL])
    assert len(result.chunks) == (3 if expanded else 1)
    assert len(client.retrieve_calls) == (1 if expanded else 0)


@pytest.mark.criterion("C6")
def test_the_cap(make_retriever, payload, chunk_id_of):
    # oracle: requirement 12 — a cap of one keeps the first asked for, p0
    first, kept, last = _chain(payload, chunk_id_of, 3)
    retriever, client, _, _ = make_retriever(
        [kept, first, last], default=0.75, expansion_max_chunks=1, **ONE
    )
    assert [c.text for c in retriever.retrieve("q", [ALL]).chunks] == ["passage p1", "passage p0"]
    assert list(client.retrieve_calls[0]["ids"]) == [first["chunk_id"]]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("settings", [{"expand_neighbors": False}, {"expansion_max_chunks": 0}])
def test_expansion_off_makes_no_fetch(make_retriever, payload, chunk_id_of, settings):
    # oracle: requirement 12 — off, and a cap of nothing
    first, kept, last = _chain(payload, chunk_id_of, 3)
    retriever, client, _, _ = make_retriever(
        [kept, first, last], default=0.75, **ONE, **settings
    )
    assert len(retriever.retrieve("q", [ALL]).chunks) == 1
    assert client.retrieve_calls == []


@pytest.mark.criterion("C6")
def test_what_is_kept_is_not_fetched_and_what_is_gone_is_left_out(
    make_retriever, payload, chunk_id_of
):
    # oracle: requirement 12 — p0 and p1 are kept; p1 needs only p2, which the store lacks
    first, second, third = _chain(payload, chunk_id_of, 3)
    retriever, client, _, _ = make_retriever(
        [first, second], default=0.75, rerank_candidates=2, context_top_k=2
    )
    result = retriever.retrieve("q", [ALL])
    assert list(client.retrieve_calls[0]["ids"]) == [third["chunk_id"]]
    assert [c.text for c in result.chunks] == ["passage p0", "passage p1"]


@pytest.mark.criterion("C6")
def test_a_failed_fetch_is_loud(make_retriever, fake_qdrant, payload, chunk_id_of):
    # oracle: requirement 11 — unlike _expand_with_neighbors, this does not fall back silently
    first, kept, last = _chain(payload, chunk_id_of, 3)
    client = fake_qdrant([kept, first, last], retrieve_error=RuntimeError("fetch failed"))
    retriever, _, _, _ = make_retriever(client=client, default=0.75, **ONE)
    with pytest.raises(RuntimeError, match="fetch failed"):
        retriever.retrieve("q", [ALL])


@pytest.mark.criterion("C6")
@given(
    n=st.integers(1, 9),
    kept=st.integers(1, 9),
    cap=st.integers(0, 4),
)
def test_expansion_is_bounded_and_never_repeats(make_retriever, payload, chunk_id_of, n, kept, cap):
    # oracle: requirement 12 — in a chain whose first `kept` chunks are kept, the only id left
    # to fetch is the next of the last kept one
    chain = _chain(payload, chunk_id_of, n)
    kept = min(kept, n)
    retriever, _, _, _ = make_retriever(
        chain, default=0.75, rerank_candidates=kept, context_top_k=kept,
        expansion_max_chunks=cap,
    )
    chunks = retriever.retrieve("q", [ALL]).chunks
    direct = [c.chunk_id for c in chunks if not c.is_expansion]
    extra = [c.chunk_id for c in chunks if c.is_expansion]
    assert direct == [p["chunk_id"] for p in chain[:kept]]
    assert extra == ([chain[kept]["chunk_id"]] if kept < n and cap else [])
