"""Unit tests for model escalation (ladder, triggers, and stage wiring)."""
import json
import logging
from unittest.mock import patch

import pytest

from src.llm.escalation import escalation_ladder, is_escalation_trigger
from src.llm.gateway.model_ref import resolve_model_ref
from src.llm.profiles import load_committed_settings
from src.observability.trace_context import trace_query


def _tier(name: str) -> str:
    """The fallback profile's model for tier *name* (docs/specs/arm-model-from-profile.md).

    The ladder climbs the profile's tiers, so the expected models follow the profile
    rather than naming one model family.
    """
    return load_committed_settings().model_for_tier(name)


@pytest.fixture
def events():
    logger = logging.getLogger("xbrlrag.events")
    captured = []

    class _Collector(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage()))

    handler = _Collector(level=logging.INFO)
    logger.addHandler(handler)
    prev = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


# ----------------------------------------------------------------------
# Ladder + trigger policy
# ----------------------------------------------------------------------

def test_synthesizer_ladder_climbs_medium_to_frontier():
    refs = escalation_ladder("synthesizer", resolve_model_ref(_tier("medium")))
    assert [r.model for r in refs] == [_tier("medium"), _tier("frontier")]


def test_verifier_ladder_climbs_small_to_medium():
    refs = escalation_ladder("faithfulness_verifier", resolve_model_ref(_tier("small")))
    assert [r.model for r in refs] == [_tier("small"), _tier("medium")]


def test_stage_without_policy_has_no_ladder():
    refs = escalation_ladder("intent_router", resolve_model_ref("gpt-5-mini"))
    assert [r.model for r in refs] == ["gpt-5-mini"]


def test_ladder_is_provider_aware():
    refs = escalation_ladder("synthesizer", resolve_model_ref("claude-sonnet-4-6"))
    assert [r.model for r in refs] == ["claude-sonnet-4-6", "claude-opus-4-8"]


def test_trigger_policy():
    assert is_escalation_trigger("synthesizer", "parse_error")
    assert is_escalation_trigger("synthesizer", "empty_content")
    assert not is_escalation_trigger("synthesizer", "content_filter")
    assert is_escalation_trigger("faithfulness_verifier", "parse_error")
    assert not is_escalation_trigger("faithfulness_verifier", "llm_error")
    assert not is_escalation_trigger("intent_router", "parse_error")


# ----------------------------------------------------------------------
# Synthesizer escalation wiring
# ----------------------------------------------------------------------

class _FakeExecutionResult:
    steps: list = []

    def to_dict(self) -> dict:
        return {"steps": []}


@pytest.fixture
def synth():
    from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
    return XbrlRagSynthesizer(api_key="test-key")


_GOOD = json.dumps({"answer_summary": "Revenue was $10B", "answer_details": [{"x": 1}]})


def test_synthesizer_escalates_then_succeeds(synth, events):
    with patch.object(synth, "_do_llm_call", side_effect=["not json", _GOOD]) as m:
        with trace_query("q"):
            resp = synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())

    assert resp.answer_summary == "Revenue was $10B"
    assert m.call_count == 2
    esc = [e for e in events if e["kind"] == "model_escalation"]
    assert len(esc) == 1
    assert esc[0]["stage"] == "synthesizer"
    assert esc[0]["reason"] == "parse_error"
    assert esc[0]["from_model"] == _tier("medium")
    assert esc[0]["to_model"] == _tier("frontier")


def test_synthesizer_escalates_on_empty_content(synth, events):
    with patch.object(synth, "_do_llm_call",
                      side_effect=[ValueError("LLM returned empty message content"), _GOOD]):
        with trace_query("q"):
            resp = synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())
    assert resp.answer_summary == "Revenue was $10B"
    esc = [e for e in events if e["kind"] == "model_escalation"]
    assert len(esc) == 1 and esc[0]["reason"] == "empty_content"


def test_synthesizer_exhausts_then_degrades(synth, events):
    with patch.object(synth, "_do_llm_call", side_effect=["nope", "still nope"]) as m:
        with trace_query("q"):
            resp = synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())

    assert resp.metadata["fully_answered"] is False
    assert resp.metadata["synthesis_error"]["type"] == "parse_error"
    assert m.call_count == 2  # tried both rungs
    assert len([e for e in events if e["kind"] == "model_escalation"]) == 1


def test_synthesizer_no_escalation_on_first_success(synth, events):
    with patch.object(synth, "_do_llm_call", side_effect=[_GOOD]) as m:
        with trace_query("q"):
            synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())
    assert m.call_count == 1
    assert not [e for e in events if e["kind"] == "model_escalation"]


# ----------------------------------------------------------------------
# Verifier escalation wiring
# ----------------------------------------------------------------------

def test_verifier_escalates_batch_on_parse_error(events):
    from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import (
        VerifierStatus,
        XbrlFaithfulnessVerifier,
    )
    v = XbrlFaithfulnessVerifier(api_key="test-key")
    with patch.object(v, "_do_llm_call", side_effect=["not json", '{"verdicts": []}']):
        with trace_query("q"):
            call = v._call_llm_with_escalation([{"index": 0, "statement": "s"}])

    assert call.status == VerifierStatus.OK
    esc = [e for e in events if e["kind"] == "model_escalation"]
    assert len(esc) == 1
    assert esc[0]["stage"] == "faithfulness_verifier"
    assert esc[0]["from_model"] == _tier("small")
    assert esc[0]["to_model"] == _tier("medium")
