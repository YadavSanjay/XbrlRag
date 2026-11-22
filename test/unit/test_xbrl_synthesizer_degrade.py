"""The XBRL synthesizer degrades gracefully on unparseable LLM output.

Phase 2 (design §3.4): a malformed / prose-wrapped JSON response must not abort
the whole query — the synthesizer returns a structured, not-fully-answered
response instead of raising.
"""
from unittest.mock import patch

import pytest

from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer


class _FakeExecutionResult:
    steps: list = []

    def to_dict(self) -> dict:
        return {"steps": []}


@pytest.fixture
def synth():
    return XbrlRagSynthesizer(api_key="test-key")


def test_unparseable_json_degrades_instead_of_raising(synth):
    with patch.object(synth, "_do_llm_call", return_value="I could not do that."):
        resp = synth.generate_answer("What was revenue?", {"steps": []}, _FakeExecutionResult())

    assert resp.metadata.get("fully_answered") is False
    assert resp.metadata.get("synthesis_error", {}).get("type") == "parse_error"
    assert resp.answer_details == []
    assert resp.unanswered_parts and resp.unanswered_parts[0]["reason"] == "synthesizer_parse_error"
    assert "could not be completed" in resp.answer_summary


def test_prose_wrapped_json_is_recovered(synth):
    wrapped = 'Here is the answer:\n```json\n{"answer_summary": "Revenue was $10B", "answer_details": [{"x": 1}]}\n```'
    with patch.object(synth, "_do_llm_call", return_value=wrapped):
        resp = synth.generate_answer("What was revenue?", {"steps": []}, _FakeExecutionResult())

    assert resp.answer_summary == "Revenue was $10B"
    assert resp.answer_details == [{"x": 1}]
    assert "synthesis_error" not in resp.metadata


def test_non_object_json_degrades(synth):
    with patch.object(synth, "_do_llm_call", return_value="[1, 2, 3]"):
        resp = synth.generate_answer("q", {"steps": []}, _FakeExecutionResult())
    assert resp.metadata.get("fully_answered") is False
    assert resp.metadata.get("synthesis_error", {}).get("type") == "parse_error"
