"""C1: ``is_count_phrase`` (docs/specs/peer-rank-count-phrases.md, requirement 1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import is_count_phrase

# The listed one-word subjects (requirement 1), lower-cased, written out by hand.
_LISTED = {"i", "we"}


def _at_rank_word(text: str) -> int:
    return text.lower().index("rank")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "I ranked 32 of the 33 covered companies.",
    "We rank 30 filers by revenue.",
    "I've ranked 24 companies.",
    "Here we’ve ranked 12 peers.",
    "So I have ranked 40 companies.",
    "WE HAVE RANKED 14 FILERS.",
    "i ranked 9 of 10.",
    "In the table, we ranking 5 of them.",
])
def test_first_person_subjects_are_count_phrases(text):
    # oracle: requirement 1 — each text has a listed subject, one space, then a rank word
    assert is_count_phrase(text, _at_rank_word(text)) is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "Intel ranked 14 of 14.",
    "Walmart ranks 2 of 4.",
    "It ranks 20 of 24.",
    "That figure, which ranks 3 of 9.",
    "They ranked 3 of 4.",
    "The screen ranked 32 companies.",
    "AI ranked 5 of 6.",
    "Wwe ranked 5 of 6.",
    "I  ranked 32 of 33.",
    "I\nranked 32 of 33.",
    "I also ranked 32 of 33.",
    "Ranked 3 of 4.",
])
def test_other_subjects_are_not_count_phrases(text):
    # oracle: requirement 1 — no listed subject directly before the rank word through one
    # space (another subject, part of a word, two spaces, a newline, an extra word, none)
    assert is_count_phrase(text, _at_rank_word(text)) is False


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("text", "start"), [
    ("I rated 32 of 33.", 2),          # no rank word at start
    ("I rankled 32 of 33.", 2),        # "rank" followed by a letter is no rank word
    ("I ranked 32 of 33.", 3),         # start inside the rank word
    ("I ranked 32 of 33.", 1),         # start at the space
    ("I ranked 32 of 33.", 0),         # start at the subject
    ("I ranked 32 of 33.", -6),        # negative
    ("I ranked 32 of 33.", 18),        # at the end
    ("I ranked 32 of 33.", 400),       # past the end
])
def test_start_must_be_a_rank_word(text, start):
    # oracle: requirement 1 — false unless a rank word begins exactly at start
    assert is_count_phrase(text, start) is False


@pytest.mark.criterion("C1")
@given(subject=st.sampled_from(["I", "we", "I've", "we've", "I’ve", "we’ve",
                                "I have", "we have"]),
       word=st.sampled_from(["rank", "ranks", "ranked", "ranking"]),
       case=st.sampled_from(["lower", "upper", "as is"]),
       prefix=st.sampled_from(["", "Here ", "Overall, ", "Revenue was $5 billion. ", "(",
                               "\n"]),
       n=st.integers(min_value=1, max_value=999))
def test_every_listed_subject_and_rank_word(subject, word, case, prefix, n):
    # oracle: requirement 1 — every subject x rank word x case, after any non-letter boundary
    phrase = f"{subject} {word}"
    phrase = {"lower": phrase.lower(), "upper": phrase.upper(), "as is": phrase}[case]
    text = f"{prefix}{phrase} {n} of the covered companies."
    assert is_count_phrase(text, len(prefix) + len(subject) + 1) is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(subject=st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyzIW"),
                       min_size=1, max_size=8),
       word=st.sampled_from(["rank", "ranks", "ranked", "ranking"]))
def test_unlisted_subjects_are_never_count_phrases(subject, word):
    # oracle: requirement 1 — the subject set is closed; a letters-only word outside it
    # never qualifies (a listed word gets a letter appended, which makes it another word)
    if subject.lower() in _LISTED:
        subject = subject + "x"
    text = f"{subject} {word} 7 of 9."
    assert is_count_phrase(text, len(subject) + 1) is False
