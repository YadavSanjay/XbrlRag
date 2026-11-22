"""C2: an answer's statements become judge claims with their resolved cited passages."""

from __future__ import annotations

import copy
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.judge_wiring import answer_claims
from src.eval.core.system import SystemAnswer
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse

LOOKUP = {"step_id": "s1", "op": "lookup", "status": "OK", "value": 100.0, "unit": "USD",
          "citation": {"fact_id": 501}}
LOOKUP_2 = {"step_id": "s2", "op": "lookup", "status": "OK", "value": 80.0, "unit": "USD",
            "citation": {"fact_id": 502}}
COMPUTE = {"step_id": "s3", "op": "compute", "status": "OK", "value": 0.25, "unit": None,
           "citation": None}
NOT_FOUND = {"step_id": "s4", "op": "lookup", "status": "NOT_FOUND", "value": None,
             "unit": None, "citation": {"fact_id": 503}}
ROW_A = {"ticker": "AAA", "value": 5.0, "unit": "USD", "fact_id": 601}
ROW_B = {"ticker": "BBB", "value": 3.0, "unit": "USD", "fact_id": 602}
SCREEN = {"step_id": "s5", "op": "screen", "status": "OK", "value": None, "unit": "USD",
          "citation": None, "rows": [ROW_A, ROW_B]}
STEPS = [LOOKUP, LOOKUP_2, COMPUTE, NOT_FOUND, SCREEN]
CHUNKS = [
    {"chunk_id": "c-1", "text": "Revenue rose on services."},
    {"chunk_id": "c-2", "text": "Hardware declined."},
]
DETAILS = [
    {"statement": "Revenue was 100.", "fact_ids": ["501"], "chunk_ids": ["c-1"]},
    {"statement": "Growth was 25%.", "fact_ids": ["s3", 501, " 501 "], "chunk_ids": ["c-9"]},
    {"statement": "   ", "fact_ids": ["501"], "chunk_ids": []},
    {"statement": "AAA ranks first.", "fact_ids": [601, 503], "chunk_ids": ["c-2", "c-2"]},
]


def _dump(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


# oracle: requirement 3, worked by hand on DETAILS —
#   s0: fact 501 -> the lookup step (citation.fact_id), then chunk c-1's text;
#   s1: "s3" -> the compute step (step_id); 501 and " 501 " -> the lookup step, kept once;
#       c-9 resolves to nothing and is dropped;
#   entry 2 is blank and skipped without renumbering;
#   s3: 601 -> screen row A; 503 -> only a NOT_FOUND step, dropped; c-2 kept once.
EXPECTED = [
    ("s0", "Revenue was 100.", [_dump(LOOKUP), "Revenue rose on services."]),
    ("s1", "Growth was 25%.", [_dump(COMPUTE), _dump(LOOKUP)]),
    ("s3", "AAA ranks first.", [_dump(ROW_A), "Hardware declined."]),
]


def _as_tuples(claims) -> list[tuple]:
    return [(c.claim_id, c.text, list(c.cited_passages)) for c in claims]


@pytest.mark.criterion("C2")
def test_claims_from_a_response_dict(make_response):
    # oracle: EXPECTED above (requirement 3, by hand)
    raw = make_response(details=DETAILS, steps=STEPS, chunks=CHUNKS)
    claims = answer_claims(SystemAnswer(text="summary", raw=raw))
    assert _as_tuples(claims) == EXPECTED


@pytest.mark.criterion("C2")
def test_claims_from_an_xbrlrag_response(make_response):
    # oracle: requirement 3 — an XbrlRagResponse and its dict form give the same claims
    raw = make_response(details=DETAILS, steps=STEPS, chunks=CHUNKS)
    model = XbrlRagResponse(**raw)
    claims = answer_claims(SystemAnswer(text="summary", raw=model))
    assert _as_tuples(claims) == EXPECTED


@pytest.mark.criterion("C2")
def test_unstructured_answers(make_response):
    # oracle: requirement 3 — no structured statements: one uncited claim of the text,
    # none for blank text; an empty answer_details list asserts nothing
    assert _as_tuples(answer_claims(SystemAnswer(text="Revenue grew."))) == [
        ("s0", "Revenue grew.", [])
    ]
    assert answer_claims(SystemAnswer(text="  ")) == []
    no_details = make_response(steps=STEPS, chunks=CHUNKS)
    assert _as_tuples(answer_claims(SystemAnswer(text="Prose.", raw=no_details))) == [
        ("s0", "Prose.", [])
    ]
    empty = make_response(details=[], steps=STEPS, chunks=CHUNKS)
    assert answer_claims(SystemAnswer(text="I cannot answer.", raw=empty)) == []


@pytest.mark.criterion("C2")
def test_uncited_and_unknown_ids_give_no_passages(make_response):
    # oracle: requirement 3 — ids that resolve to nothing are dropped
    details = [{"statement": "Margins widened.", "fact_ids": ["999", "s9"],
                "chunk_ids": ["nope"]},
               {"statement": "No citation at all."}]
    raw = make_response(details=details, steps=STEPS, chunks=CHUNKS)
    assert _as_tuples(answer_claims(SystemAnswer(text="t", raw=raw))) == [
        ("s0", "Margins widened.", []),
        ("s1", "No citation at all.", []),
    ]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(data=st.data())
def test_unknown_citations_change_nothing(make_response, data):
    # oracle: requirement 3 — appending an id that resolves to nothing changes no claim
    details = copy.deepcopy(DETAILS)
    for _ in range(data.draw(st.integers(min_value=1, max_value=4))):
        entry = details[data.draw(st.integers(min_value=0, max_value=len(details) - 1))]
        field = data.draw(st.sampled_from(["fact_ids", "chunk_ids"]))
        unknown = data.draw(st.sampled_from(["zz-unknown", "77777", "c-404", "s99"]))
        entry[field] = [*entry.get(field, []), unknown]
    raw = make_response(details=details, steps=STEPS, chunks=CHUNKS)
    assert _as_tuples(answer_claims(SystemAnswer(text="summary", raw=raw))) == EXPECTED
