"""C2: a rank label is removed from a company mention (docs/specs/ranked-top-injection.md,
Requirement 2)."""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.ranked_top import strip_rank_label

_LABELLED = [
    ("AMZN (#1)", "AMZN"),
    ("  MSFT(#12) ", "MSFT"),
    ("Amazon.com, Inc. (#2)", "Amazon.com, Inc."),
    ("GOOGL\t(#3)", "GOOGL"),
    ("AAA (#2) (#1)", "AAA (#2)"),
]
_UNCHANGED = ["AMZN", " Alpha Holdings ", "Berkshire Hathaway (Class B)", "AMZN #1", "AMZN (#)",
              "AMZN (#1a)", "AMZN (#1) Inc", "(#1)", "  (#4) ", ""]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("mention, expected", _LABELLED)
def test_a_trailing_label_is_removed(mention, expected):
    # oracle: Requirement 2's examples, by hand — one trailing "(#k)" removed, the name stripped
    assert strip_rank_label(mention) == expected


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("mention", _UNCHANGED)
def test_a_mention_without_a_label_is_returned_unchanged(mention):
    # oracle: Requirement 2's examples, by hand — no trailing label (or nothing before it): the
    # mention itself, not stripped; a labelled one alongside it is still stripped
    assert strip_rank_label(mention) == mention
    assert strip_rank_label("AMZN (#1)") == "AMZN"


_NAME = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,&'-",
                min_size=1, max_size=30).filter(lambda s: s.strip() != "")
_SPACE = st.text(alphabet=" \t", max_size=3)


@pytest.mark.criterion("C2")
@settings(max_examples=200)
@given(name=_NAME, k=st.integers(0, 10_000), sep=_SPACE, lead=_SPACE, trail=_SPACE)
def test_any_labelled_name_gives_the_stripped_name(name, k, sep, lead, trail):
    # oracle: Requirement 2 by hand — the name before one trailing "(#k)", stripped
    assert strip_rank_label(f"{lead}{name}{sep}(#{k}){trail}") == name.strip()


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=200)
@given(name=_NAME, k=st.integers(1, 99))
def test_an_unlabelled_mention_is_untouched_and_its_labelled_form_is_stripped(name, k):
    # oracle: Requirement 2 — a mention with no trailing "(#digits)" is returned as given; adding a
    # label to it then gives its strip
    assert not re.search(r"\(#[0-9]+\)\s*$", name)
    assert strip_rank_label(name) == name
    assert strip_rank_label(f"{name} (#{k})") == name.strip()
