"""C3: a baseline arm's claim is the "text" of its JSON reply, not the reply itself."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.judge_wiring import answer_claims
from src.eval.core.system import SystemAnswer

SENTENCE = "Apple disclosed that on March 25, 2024 the Commission opened two investigations."


def reply(text: object) -> str:
    """A reply in the shape of the baseline prompt's answer object (synthetic)."""
    return json.dumps({"value": None, "unit": None, "rows": [], "refused": False,
                       "hedged": False, "cited_chunks": ["c-1"], "text": text})


def _as_tuples(claims) -> list[tuple]:
    return [(c.claim_id, c.text, list(c.cited_passages)) for c in claims]


@pytest.mark.criterion("C3")
def test_baseline_reply_gives_its_text():
    # oracle: requirement 2 — one uncited claim of the reply's "text"
    answer = parse_structured_answer(reply(SENTENCE))
    assert _as_tuples(answer_claims(answer)) == [("s0", SENTENCE, [])]


@pytest.mark.criterion("C3")
def test_fenced_baseline_reply_gives_its_text():
    # oracle: requirement 2 — the same object inside a ```json fence
    answer = parse_structured_answer("```json\n" + reply(SENTENCE) + "\n```")
    assert _as_tuples(answer_claims(answer)) == [("s0", SENTENCE, [])]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_text_gives_no_claims(blank):
    # oracle: requirement 2 — a blank "text" gives no claims, as blank prose does
    assert answer_claims(parse_structured_answer(reply(blank))) == []


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(s=st.text(alphabet=st.characters(blacklist_characters="`{}"), max_size=120))
def test_json_reply_and_its_text_give_the_same_claims(s):
    # oracle: requirement 2 — wrapping the text in the baseline's JSON changes no claim
    wrapped = answer_claims(parse_structured_answer(reply(s)))
    plain = answer_claims(SystemAnswer(text=s))
    assert _as_tuples(wrapped) == _as_tuples(plain)
