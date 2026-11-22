"""C1: answer_prose returns the "text" string of a baseline's JSON reply, exactly."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import answer_prose

SENTENCE = "Apple disclosed that on March 25, 2024 the Commission opened two investigations."


def reply(text: object, **extra: object) -> str:
    """A reply in the shape of the baseline prompt's answer object (synthetic)."""
    obj = {"value": None, "unit": None, "rows": [], "refused": False, "hedged": False,
           "cited_chunks": ["c-1"], "text": text}
    obj.update(extra)
    return json.dumps(obj)


# Strings without a backtick or brace, so the reply holds exactly one JSON object and no
# fence of its own: the object parse_structured_answer reads is the one the test built.
PLAIN = st.text(alphabet=st.characters(blacklist_characters="`{}"), max_size=80)


@pytest.mark.criterion("C1")
def test_bare_reply():
    # oracle: requirement 1 — the string the test put under "text"
    assert answer_prose(reply(SENTENCE)) == SENTENCE


@pytest.mark.criterion("C1")
def test_fenced_reply():
    # oracle: requirement 1 — a ```json fence holds the same object
    assert answer_prose("```json\n" + reply(SENTENCE) + "\n```") == SENTENCE


@pytest.mark.criterion("C1")
def test_reply_after_prose():
    # oracle: requirement 1 — an object within prose is the object parse_structured_answer reads
    assert answer_prose("Here is the answer: " + reply(SENTENCE)) == SENTENCE


@pytest.mark.criterion("C1")
def test_text_is_returned_unchanged():
    # oracle: requirement 1 — "returned unchanged (not stripped)"
    padded = "  Revenue grew.\n"
    assert answer_prose(reply(padded)) == padded


@pytest.mark.criterion("C1")
@given(s=st.text(alphabet=st.characters(blacklist_characters="`"), max_size=120),
       hedged=st.booleans(), refused=st.booleans())
def test_any_text_round_trips(s, hedged, refused):
    # oracle: requirement 1 — whatever string is under "text" comes back as it is
    assert answer_prose(reply(s, hedged=hedged, refused=refused)) == s


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(s=PLAIN, lead_in=PLAIN, fenced=st.booleans())
def test_fence_or_lead_in_changes_nothing(s, lead_in, fenced):
    # oracle: requirement 1 — bare, fenced and within-prose replies hold the same object
    body = reply(s)
    variant = ("```json\n" + body + "\n```") if fenced else (lead_in + " " + body)
    assert answer_prose(variant) == answer_prose(body) == s
