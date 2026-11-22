"""Tests for XbrlFaithfulnessVerifier, focused on R6: a verifier that fails to
run must be distinguishable from a clean pass (never silently "all supported").

All LLM calls are mocked via ``_do_llm_call``; these tests exercise the
verdict/status/metadata logic, not the LLM itself.
"""
import json
import logging
from unittest.mock import patch

import pytest

from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import (
    VerifierStatus,
    XbrlFaithfulnessVerifier,
)
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse


@pytest.fixture
def events():
    """Collect ``xbrlrag.events`` records (logger has propagate=False in prod config)."""
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


@pytest.fixture
def verifier():
    return XbrlFaithfulnessVerifier(api_key="sk-test")


def _response(answer_details=None, metadata=None):
    """A minimal response whose single statement cites fact_id=1 (has evidence)."""
    if answer_details is None:
        answer_details = [{"statement": "Revenue was $100.", "fact_ids": ["1"], "chunk_ids": []}]
    return XbrlRagResponse(
        query="What was revenue?",
        answer_summary="Revenue was $100.",
        answer_details=answer_details,
        unanswered_parts=[],
        metadata=metadata if metadata is not None else {"fully_answered": True},
        plan={"steps": []},
        execution_result={"steps": [{
            "step_id": "rev", "op": "lookup", "status": "OK", "value": 100.0,
            "unit": "USD", "citation": {"fact_id": 1},
        }]},
        narrative_chunks=[],
    )


SUPPORTED_VERDICT = json.dumps({"verdicts": [{"index": 0, "verdict": "SUPPORTED", "reason": "matches the cited fact"}]})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_clean_pass_reports_ok_status(verifier, events):
    with patch.object(verifier, "_do_llm_call", return_value=SUPPORTED_VERDICT):
        checked = verifier.verify(_response())

    faith = checked.metadata["faithfulness"]
    assert faith["status"] == "OK"
    assert faith["all_supported"] is True
    assert checked.metadata.get("fully_answered") is True
    assert len(checked.answer_details) == 1
    assert "could not be completed" not in checked.answer_summary
    # no verifier_error event on a clean pass
    assert not [e for e in events if e["kind"] == "verifier_error"]


def test_empty_answer_details_is_noop(verifier):
    resp = _response(answer_details=[])
    checked = verifier.verify(resp)
    assert checked is resp


# ---------------------------------------------------------------------------
# R6: parse failure is NOT a clean pass
# ---------------------------------------------------------------------------

def test_parse_error_is_distinguishable_from_pass(verifier, events):
    with patch.object(verifier, "_do_llm_call", return_value="this is not json"):
        checked = verifier.verify(_response())

    faith = checked.metadata["faithfulness"]
    assert faith["status"] == VerifierStatus.PARSE_ERROR.value
    assert faith["all_supported"] is False
    assert checked.metadata["fully_answered"] is False
    # the statement is kept but explicitly marked unverified (not SUPPORTED)
    verdict = faith["verdicts"][0]
    assert verdict["verdict"] == "PARTIALLY_SUPPORTED"
    assert "unverified" in verdict["reason"].lower()
    # the user-facing summary carries a caveat
    assert "could not be completed" in checked.answer_summary
    # and a structured event was emitted
    err_events = [e for e in events if e["kind"] == "verifier_error"]
    assert len(err_events) == 1
    assert err_events[0]["status"] == VerifierStatus.PARSE_ERROR.value


def test_llm_exception_is_handled_not_raised(verifier, events):
    with patch.object(verifier, "_do_llm_call", side_effect=RuntimeError("boom")):
        # must NOT propagate — verifier failure degrades gracefully
        checked = verifier.verify(_response())

    faith = checked.metadata["faithfulness"]
    assert faith["status"] == VerifierStatus.LLM_ERROR.value
    assert faith["error"] is not None
    assert checked.metadata["fully_answered"] is False
    assert "could not be completed" in checked.answer_summary
    err_events = [e for e in events if e["kind"] == "verifier_error"]
    assert len(err_events) == 1
    assert err_events[0]["status"] == VerifierStatus.LLM_ERROR.value


# ---------------------------------------------------------------------------
# Existing behaviour still holds
# ---------------------------------------------------------------------------

def test_no_evidence_statement_is_unsupported_and_dropped(verifier):
    # fact_ids/chunk_ids empty -> deterministic UNSUPPORTED, no LLM call needed
    resp = _response(answer_details=[{"statement": "Made-up claim.", "fact_ids": [], "chunk_ids": []}])
    with patch.object(verifier, "_do_llm_call", side_effect=AssertionError("LLM should not be called")):
        checked = verifier.verify(resp)

    # status is OK because the verifier never needed to call the LLM
    assert checked.metadata["faithfulness"]["status"] == "OK"
    assert checked.answer_details == []  # dropped
    assert any("faithfulness check" in p["reason"] for p in checked.unanswered_parts)


def test_unsupported_verdict_dropped_without_failure(verifier, events):
    unsupported = json.dumps({"verdicts": [{"index": 0, "verdict": "UNSUPPORTED", "reason": "not in evidence"}]})
    with patch.object(verifier, "_do_llm_call", return_value=unsupported):
        checked = verifier.verify(_response())

    assert checked.metadata["faithfulness"]["status"] == "OK"
    assert checked.answer_details == []
    assert checked.metadata["fully_answered"] is False
    # a dropped-statement caveat, but NOT a verifier-failure caveat
    assert "removed after failing" in checked.answer_summary
    assert "could not be completed" not in checked.answer_summary
    assert not [e for e in events if e["kind"] == "verifier_error"]


# ---------------------------------------------------------------------------
# Deterministic citation checks (docs/specs/citation-integrity.md)
# ---------------------------------------------------------------------------

def test_unknown_id_is_dropped_before_the_llm(verifier, events):
    # A real fact plus an invented id: dropped deterministically, never sent to the LLM.
    resp = _response(answer_details=[
        {"statement": "Revenue was $100.", "fact_ids": ["1", "999"], "chunk_ids": []}])
    with patch.object(verifier, "_do_llm_call", side_effect=AssertionError("no LLM call")):
        checked = verifier.verify(resp)

    assert checked.answer_details == []
    assert "999" in checked.unanswered_parts[0]["reason"]
    verdict = checked.metadata["faithfulness"]["verdicts"][0]
    assert verdict["check"] == "unresolved_ids"
    assert verdict["unresolved_ids"] == ["999"]
    decisions = [e for e in events if e["kind"] == "decision"
                 and e.get("point") == "citation_integrity"]
    assert [(d["check"], d["ids"]) for d in decisions] == [("unresolved_ids", ["999"])]


def test_misstated_number_is_dropped_before_the_llm(verifier):
    # The cited fact is 100; the statement says 120.
    resp = _response(answer_details=[
        {"statement": "Revenue was $120.", "fact_ids": ["1"], "chunk_ids": []}])
    with patch.object(verifier, "_do_llm_call", side_effect=AssertionError("no LLM call")):
        checked = verifier.verify(resp)

    assert checked.answer_details == []
    verdict = checked.metadata["faithfulness"]["verdicts"][0]
    assert verdict["check"] == "number_mismatch"
    assert verdict["unmatched_numbers"] == ["$120"]
    assert checked.metadata["fully_answered"] is False


def test_component_fact_id_resolves_to_its_derived_step(verifier):
    # A derived step has no citation, only component facts; citing a component must send
    # the derived step to the LLM as evidence (not drop the statement as citing nothing).
    resp = XbrlRagResponse(
        query="q", answer_summary="s.",
        answer_details=[{"statement": "Gross profit was $40.", "fact_ids": ["7"],
                         "chunk_ids": []}],
        unanswered_parts=[], metadata={"fully_answered": True}, plan={"steps": []},
        execution_result={"steps": [{
            "step_id": "gp", "op": "lookup", "status": "OK", "value": 40.0, "unit": "USD",
            "citation": None, "component_fact_ids": {"revenue": 6, "cost_of_revenue": 7}}]},
        narrative_chunks=[])
    with patch.object(verifier, "_do_llm_call", return_value=SUPPORTED_VERDICT) as call:
        checked = verifier.verify(resp)

    assert len(checked.answer_details) == 1
    sent = json.dumps(call.call_args.args[0])
    assert '\\"step_id\\": \\"gp\\"' in sent
