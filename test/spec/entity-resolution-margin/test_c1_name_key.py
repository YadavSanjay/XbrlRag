"""C1: company_name_key (docs/specs/entity-resolution-margin.md, R1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_resolution import company_name_key

# Real spellings (entity_master and SEC's list) and their keys, R1 applied by hand:
# casefold, drop apostrophes, & -> and, drop a trailing /XX tag, non-alphanumerics -> space,
# drop legal-form words, drop leading/trailing "and".
HAND_KEYS = {
    "McDONALD’S CORPORATION": "mcdonalds",
    "QUALCOMM INC/DE": "qualcomm",
    "COSTCO WHOLESALE CORP /NEW": "costco wholesale",
    "ELI LILLY AND COMPANY": "eli lilly",
    "ELI LILLY & Co": "eli lilly",
    "Merck & Co., Inc.": "merck",
    "PROCTER & GAMBLE CO": "procter and gamble",
    "AMAZON.COM, INC.": "amazon com",
    "HOME DEPOT, INC.": "home depot",
    "The Home Depot": "home depot",
    "VERTEX PHARMACEUTICALS INC / MA": "vertex pharmaceuticals",
    "Taiwan Semiconductor Manufacturing Company Limited": "taiwan semiconductor manufacturing",
    "COCA-COLA EUROPACIFIC PARTNERS plc": "coca cola europacific partners",
    "JOHNSON & JOHNSON": "johnson and johnson",
}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("name,expected", sorted(HAND_KEYS.items()))
def test_real_spellings_have_the_hand_derived_key(name, expected):
    # oracle: R1's seven steps applied by hand to each spelling (table above)
    assert company_name_key(name) == expected


@pytest.mark.criterion("C1")
def test_a_name_of_only_legal_words_has_an_empty_key():
    # oracle: R1 steps 5-7 — every token is a legal-form word or a leading/trailing "and"
    assert company_name_key("The Company, Inc.") == ""
    assert company_name_key("Apple Inc.") == "apple"


@st.composite
def decorated(draw, name: str) -> str:
    """The name as a user or another filing might spell it: any case, spaced out, wrapped
    in punctuation, with a leading "The" and/or an appended legal suffix."""
    case = draw(st.sampled_from([str.lower, str.upper, str.title, lambda s: s]))
    words = case(name).split(" ")
    gaps = [" " * draw(st.integers(min_value=1, max_value=3)) for _ in words[1:]]
    body = words[0] + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    the = draw(st.sampled_from(["", "The ", "the ", "THE "]))
    # A state tag ("/DE") is recognized only at the very end of the name (R1 step 3), so a
    # name carrying one gets no suffix and no closing punctuation after it.
    tagged = "/" in name
    suffixes = [""] if tagged else ["", ", Inc.", " Corp.", " Co.", " Ltd", " plc", " INC"]
    suffix = draw(st.sampled_from(suffixes))
    left = draw(st.sampled_from(["", " ", '"', "(", "  "]))
    right = draw(st.sampled_from(["", " ", "  "] if tagged else ["", " ", '"', ")", ".", "  "]))
    return f"{left}{the}{body}{suffix}{right}"


@st.composite
def named_and_decorated(draw):
    name, key = draw(st.sampled_from(sorted(HAND_KEYS.items())))
    return draw(decorated(name)), key


@pytest.mark.criterion("C1")
@given(named_and_decorated())
def test_key_ignores_case_spacing_punctuation_the_and_legal_suffix(pair):
    # oracle: R1 — case (1), punctuation and whitespace runs (4), "the" and legal-form
    # words (5) never reach the key; the expected key is the hand-derived one above
    mention, key = pair
    assert company_name_key(mention) == key


@pytest.mark.criterion("C1")
@given(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40))
def test_key_is_idempotent_and_canonical(text):
    # oracle: R1 — the key is single-spaced lower-case ASCII alphanumerics with no
    # legal-form word and no leading/trailing "and", so applying R1 again changes nothing
    key = company_name_key(text)
    assert company_name_key(key) == key
    tokens = key.split(" ") if key else []
    assert " ".join(tokens) == key
    assert all(t.isascii() and t.isalnum() and t == t.lower() for t in tokens)
    assert not tokens or (tokens[0] != "and" and tokens[-1] != "and")
    assert company_name_key(text + " Apple").endswith("apple")
