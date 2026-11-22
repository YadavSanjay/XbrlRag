"""C7: ``/api/v1/answer`` and the MCP ``answer`` tool carry citations unchanged (R4).

The serving surfaces reach the pipeline through ``src.api.pipelines``' cached pipeline slot.
The slot is filled with the C6 ``Compound`` pipeline (real stages, scripted LLM, fake fact
store) for the duration of a call and restored afterwards, and the capturing audit sink is
marked installed so no database is touched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.api import pipelines
from src.api.v1 import router, schemas
from src.mcp_server import server

EXPECTED = [
    {"statement": "Walmart ranked first with fiscal 2024 revenue of $648.1 billion.",
     "fact_ids": ["205006"], "chunk_ids": [], "subquery_id": "sq1"},
    {"statement": "Amazon was second with revenue of $637,959 million.",
     "fact_ids": ["6854"], "chunk_ids": [], "subquery_id": "sq1"},
    {"statement": "Walmart's net margin was 2.4%.",
     "fact_ids": ["m_wmt"], "chunk_ids": [], "subquery_id": "sq2"},
    {"statement": "Amazon's net margin was 9.3%, on net income of $59.2 billion.",
     "fact_ids": ["m_amzn"], "chunk_ids": [], "subquery_id": "sq2"},
]


@contextlib.contextmanager
def serving(pipeline) -> Iterator[None]:
    """Serves *pipeline* from the API's pipeline slot for the block."""
    saved = (pipelines._xbrl_pipeline, pipelines._sink_installed)
    pipelines._xbrl_pipeline, pipelines._sink_installed = pipeline, True
    try:
        yield
    finally:
        pipelines._xbrl_pipeline, pipelines._sink_installed = saved


@pytest.mark.criterion("C7")
def test_the_v1_answer_route_returns_the_verified_citations(ci):
    # oracle: the hand-built C6 answer (EXPECTED)
    with serving(ci.Compound().pipeline):
        body = router.answer(schemas.AnswerRequest(query=ci.COMPOUND, user_id="analyst-1"))
    assert body.answer_details == EXPECTED
    assert len(body.unanswered_parts) == 5


@pytest.mark.criterion("C7")
def test_the_mcp_answer_tool_returns_the_verified_citations(ci):
    # oracle: the hand-built C6 answer (EXPECTED), after the tool's JSON-mode dump
    with serving(ci.Compound().pipeline):
        body = server.answer(query=ci.COMPOUND)
    assert body.get("error") is None
    assert body["answer_details"] == EXPECTED


@pytest.mark.criterion("C7")
@settings(max_examples=25)
@given(new_ids=st.lists(st.integers(1, 10**12).filter(lambda i: i != 9_000_000_001),
                        min_size=13, max_size=13, unique=True))
def test_renamed_fact_ids_come_back_renamed_on_both_surfaces(ci, new_ids):
    # oracle: rules 1-3 use fact ids only through equality, so renaming every real fact id by
    # one injective map renames EXPECTED's fact ids by that map and changes nothing else
    real = sorted({v[0] for v in ci.FACTS.values()} | {ci.AAPL_COST_OF_REVENUE, 14256, 13550,
                                                       13562})[:13]
    mapping = dict(zip(real, new_ids, strict=True))
    expected = [{**d, "fact_ids": [str(mapping[int(f)]) if f.isdigit() else f
                                   for f in d["fact_ids"]]} for d in EXPECTED]
    with serving(ci.Compound(ids=mapping).pipeline):
        api = router.answer(schemas.AnswerRequest(query=ci.COMPOUND))
    with serving(ci.Compound(ids=mapping).pipeline):
        mcp = server.answer(query=ci.COMPOUND)
    assert api.answer_details == expected
    assert mcp["answer_details"] == expected
