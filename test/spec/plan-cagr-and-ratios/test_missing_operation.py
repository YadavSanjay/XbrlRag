"""Spec tests for plan-cagr-and-ratios C9: `mark_missing_operation`.

docs/specs/plan-cagr-and-ratios.md, Requirement 9. Pure. The responses are hand-built in the
`XbrlRagResponse.model_dump()` shape, about the synthetic Example Co (Notes: ratio fixture).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.operation_cues import mark_missing_operation

RATIO_Q = "Example Co Assets, Current / Liabilities, Current, FY2024"
CAGR_Q = "What was Example Co's revenue CAGR from FY2022 to FY2025?"


def _lookup_dict(sid, value, fid):
    return {"step_id": sid, "op": "lookup", "status": "OK", "value": value, "unit": "USD",
            "citation": {"fact_id": fid}, "confidence": 0.9, "linked_chunk_ids": [],
            "error": None}


def _response(*, compute=None, compute_status="OK", cite=("301", "302"), cagr=None,
              extra_steps=(), metadata=True, unanswered=True):
    plan_steps = [{"id": "ca", "op": "lookup"}, {"id": "cl", "op": "lookup"}]
    steps = [_lookup_dict("ca", 50_000_000_000.0, 301),
             _lookup_dict("cl", 40_000_000_000.0, 302)]
    if compute is not None:
        plan_steps.append({"id": "c1", "op": "compute", "expr": compute})
        steps.append({"step_id": "c1", "op": "compute", "status": compute_status,
                      "value": 1.25 if compute_status == "OK" else None, "unit": "pure",
                      "citation": None, "confidence": 0.9, "linked_chunk_ids": [],
                      "error": None if compute_status == "OK" else "division by zero"})
    if cagr is not None:
        plan_steps.append({"id": "g1", "op": "cagr", "start": "ca", "end": "cl"})
        steps.append(cagr)
    steps.extend(extra_steps)
    out = {
        "query": RATIO_Q,
        "answer_summary": "Example Co reported current assets of $50.0 billion and current "
                          "liabilities of $40.0 billion in FY2024.",
        "answer_details": [{"statement": "Current assets were $50.0 billion.",
                            "fact_ids": [cite[0]], "chunk_ids": []},
                           {"statement": "Current liabilities were $40.0 billion.",
                            "fact_ids": [cite[1]], "chunk_ids": []}],
        "plan": {"steps": plan_steps},
        "execution_result": {"status": "OK", "steps": steps, "bindings": {}, "error": None},
        "narrative_chunks": [],
    }
    if unanswered:
        out["unanswered_parts"] = []
    if metadata:
        out["metadata"] = {"fully_answered": True, "notes": ""}
    return out


def _added(before, after):
    old = before.get("unanswered_parts") or []
    new = after["unanswered_parts"]
    assert new[:len(old)] == old
    return new[len(old):]


@pytest.mark.criterion("C9")
def test_components_answer_is_marked_and_a_cited_ratio_is_not():
    # oracle: Requirement 9 — two cited lookups and no ratio is missing: one unanswered part
    # naming the query, fully_answered False; a cited OK ratio compute leaves it unchanged
    resp = _response()
    before = copy.deepcopy(resp)
    out = mark_missing_operation(resp, RATIO_Q, "ratio")
    assert resp == before  # the input is not changed
    added = _added(resp, out)
    assert len(added) == 1
    assert added[0]["question_fragment"] == RATIO_Q
    assert "ratio" in added[0]["reason"]
    assert out["metadata"]["fully_answered"] is False
    assert out["answer_details"] == resp["answer_details"]
    assert out["answer_summary"] == resp["answer_summary"]

    cited = _response(compute="ca / cl", cite=("c1", "302"))
    assert mark_missing_operation(cited, RATIO_Q, "ratio") == cited

    uncited = _response(compute="ca / cl")
    out = mark_missing_operation(uncited, RATIO_Q, "ratio")
    assert len(_added(uncited, out)) == 1 and out["metadata"]["fully_answered"] is False

    not_a_ratio = _response(compute="ca - cl", cite=("c1", "302"))
    out = mark_missing_operation(not_a_ratio, RATIO_Q, "ratio")
    assert len(_added(not_a_ratio, out)) == 1


@pytest.mark.criterion("C9")
def test_cagr_errors_exemptions_and_created_fields():
    # oracle: Requirement 9 — a non-OK cagr step's error is in the reason; a failed ratio
    # compute's too; a screen step exempts; None is the identity; missing fields created
    nm = {"step_id": "g1", "op": "cagr", "status": "NOT_MEANINGFUL", "value": None,
          "unit": None, "citation": None, "confidence": 0.0, "linked_chunk_ids": [],
          "error": "start <= 0: a CAGR from a non-positive value is not meaningful"}
    resp = _response(cagr=nm, cite=("g1", "302"))
    out = mark_missing_operation(resp, CAGR_Q, "cagr")
    added = _added(resp, out)
    assert len(added) == 1
    assert "CAGR" in added[0]["reason"] and nm["error"] in added[0]["reason"]
    assert out["metadata"]["fully_answered"] is False

    ok = {**nm, "status": "OK", "value": 0.1, "unit": "pure", "error": None}
    cited_ok = _response(cagr=ok, cite=("g1", "302"))
    assert mark_missing_operation(cited_ok, CAGR_Q, "cagr") == cited_ok
    out = mark_missing_operation(_response(cagr=ok), CAGR_Q, "cagr")
    assert out["metadata"]["fully_answered"] is False

    failed = _response(compute="ca / cl", compute_status="ERROR", cite=("c1", "302"))
    out = mark_missing_operation(failed, RATIO_Q, "ratio")
    assert "division by zero" in _added(failed, out)[0]["reason"]

    screen = {"step_id": "s1", "op": "screen", "status": "OK", "value": None, "unit": None,
              "citation": None, "error": None, "rows": []}
    exempt = _response(extra_steps=[screen])
    assert mark_missing_operation(exempt, RATIO_Q, "ratio") == exempt
    plain = _response()
    assert mark_missing_operation(plain, RATIO_Q, None) == plain
    assert mark_missing_operation(plain, RATIO_Q, "difference") == plain

    bare = _response(metadata=False, unanswered=False)
    out = mark_missing_operation(bare, RATIO_Q, "ratio")
    assert out["metadata"]["fully_answered"] is False
    assert len(out["unanswered_parts"]) == 1


@pytest.mark.criterion("C9")
@given(st.sampled_from([None, "ca / cl", "ca - cl", "ca / cl * 100"]),
       st.sampled_from([("301", "302"), ("c1", "302"), ("c1", "c1")]),
       st.sampled_from(["ratio", "cagr"]), st.booleans())
def test_marking_is_idempotent_and_touches_only_the_two_fields(compute, cite, op, meta):
    # oracle: Requirement 9 — once marked, a second pass adds nothing; None is the identity;
    # only unanswered_parts and metadata.fully_answered may change
    resp = _response(compute=compute, cite=cite, metadata=meta)
    once = mark_missing_operation(resp, RATIO_Q, op)
    assert mark_missing_operation(once, RATIO_Q, op) == once
    assert mark_missing_operation(resp, RATIO_Q, None) == resp
    for key in resp:
        if key not in ("unanswered_parts", "metadata"):
            assert once[key] == resp[key], key
    present = (op == "ratio" and compute in ("ca / cl", "ca / cl * 100") and "c1" in cite)
    if present:
        assert once == resp
    else:
        assert once["metadata"]["fully_answered"] is False
        assert len(_added(resp, once)) == 1
