"""C2: answer_prose returns its input unchanged when there is no string "text" to take."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import answer_prose


@pytest.mark.criterion("C2")
def test_prose_is_unchanged():
    # oracle: requirement 1 — no JSON object: the input itself
    prose = "Revenue grew 5% in 2024, driven by services."
    assert answer_prose(prose) == prose


@pytest.mark.criterion("C2")
def test_object_without_text_is_unchanged():
    # oracle: requirement 1 — an object without "text": the input itself
    raw = json.dumps({"value": 3.0, "unit": "USD", "rows": []})
    assert answer_prose(raw) == raw


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("value", [42, 1.5, None, ["a", "b"], {"x": "y"}, True])
def test_non_string_text_is_unchanged(value):
    # oracle: requirement 1 — "text" that is not a str: the input itself
    raw = json.dumps({"value": None, "text": value})
    assert answer_prose(raw) == raw


@pytest.mark.criterion("C2")
@given(t=st.text(alphabet=st.characters(blacklist_characters="{"), min_size=1, max_size=120))
def test_text_without_an_object_is_identity(t):
    # oracle: requirement 1 — without a "{" there is no object, so the input comes back
    assert answer_prose(t) == t


@pytest.mark.criterion("C2")
@given(value=st.one_of(st.integers(), st.floats(allow_nan=False), st.none(), st.booleans(),
                       st.lists(st.integers(), max_size=3)),
       unit=st.sampled_from([None, "USD", "%"]))
def test_any_non_string_text_is_identity(value, unit):
    # oracle: requirement 1 — the input itself whenever "text" is not a str
    raw = json.dumps({"value": 1, "unit": unit, "text": value})
    assert answer_prose(raw) == raw
