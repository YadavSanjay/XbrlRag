"""C3: ``states_phrase`` — normalised, word-bounded phrase containment with ``|`` alternatives.

Oracle: requirement 5 of docs/specs/v2-grading.md; each expectation below is the
normalisation applied by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import states_phrase


@pytest.mark.criterion("C3")
def test_point_cases():
    # oracle: requirement 5 by hand — "Fiscal-Year, 2024!" normalises to "fiscal year 2024".
    assert states_phrase("We use the Fiscal-Year, 2024!", "fiscal year") is True
    assert states_phrase("We use the Fiscal-Year, 2024!", "fiscal year 2024") is True
    assert states_phrase("calendar basis", "fiscal year") is False
    # apostrophes are deleted: "didn't" -> "didnt"
    assert states_phrase("Revenue didn't fall.", "didnt fall") is True
    assert states_phrase("Revenue didn’t fall.", "did not fall") is False
    # alternatives
    assert states_phrase("on a calendar basis", "fiscal year|calendar") is True
    assert states_phrase("on a calendar basis", "fiscal year|quarter") is False
    # word boundaries: "rose" is not in "prose"
    assert states_phrase("Some prose here.", "rose") is False
    assert states_phrase("It rose.", "rose") is True
    # blank phrase or blank alternative is never stated
    assert states_phrase("anything at all", "") is False
    assert states_phrase("anything at all", " | ") is False
    assert states_phrase("anything at all", "|anything") is True


_words = st.lists(st.text(alphabet="abcdefghij", min_size=1, max_size=6), min_size=1,
                  max_size=6)


@pytest.mark.criterion("C3")
@given(before=_words, phrase=_words, after=_words)
def test_inserted_phrase_is_stated(before, phrase, after):
    # oracle: requirement 5 — words joined by spaces are already normalised, so the phrase
    # occurs on word boundaries in the joined text.
    text = " ".join([*before, *phrase, *after])
    assert states_phrase(text, " ".join(phrase)) is True
    # a phrase containing a word the text lacks ("zzz" is outside the alphabet) is not
    assert states_phrase(text, " ".join([*phrase, "zzz"])) is False


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(words=_words, phrase=_words, upper=st.booleans(),
       sep=st.sampled_from([" ", ", ", " - ", "; ", "! "]))
def test_case_and_punctuation_do_not_matter(words, phrase, upper, sep):
    # oracle: requirement 5 — lower-casing and collapsing non-alphanumeric runs make the
    # re-cased, re-punctuated text normalise to the plain one.
    plain = " ".join(words)
    noisy = sep.join(w.upper() if upper else w.capitalize() for w in words) + "."
    target = " ".join(phrase)
    assert states_phrase(noisy, target) == states_phrase(plain, target)
    assert states_phrase(noisy, words[0]) is True
