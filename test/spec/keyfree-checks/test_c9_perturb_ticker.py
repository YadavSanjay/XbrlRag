"""C9: ``perturb_ticker`` — deterministic ticker / company-name swap in a question.

Oracle: requirement 12 of docs/specs/keyfree-checks.md. Expected questions are written by
hand from the rules: ticker forms case-sensitive, names case-insensitive, alnum boundaries,
longest form wins, a name is replaced by the target's first name.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.perturbation import perturb_ticker

NAMES = {"AAPL": ["Apple Inc.", "Apple"], "MSFT": ["Microsoft Corporation", "Microsoft"]}


@pytest.mark.criterion("C9")
def test_company_name_swapped_for_the_targets_first_name():
    # oracle: requirement 12 — "Apple" matches (the "'s" is not alnum) and becomes
    # names["MSFT"][0]
    got = perturb_ticker("What was Apple's revenue in fiscal 2024?", "AAPL", "MSFT", NAMES)
    assert got == {"question": "What was Microsoft Corporation's revenue in fiscal 2024?",
                   "perturbation": {"kind": "ticker", "from": "AAPL", "to": "MSFT"}}


@pytest.mark.criterion("C9")
def test_ticker_and_longest_name_form():
    # oracle: requirement 12 — "AAPL" (ticker) -> "MSFT"; "Apple Inc." is one match (longest
    # form wins over "Apple") -> "Microsoft Corporation"
    got = perturb_ticker("AAPL vs Apple Inc. margins", "AAPL", "MSFT", NAMES)
    assert got["question"] == "MSFT vs Microsoft Corporation margins"


@pytest.mark.criterion("C9")
def test_names_match_case_insensitively_and_lower_case_tickers_get_upper():
    # oracle: requirement 12 — names are case-insensitive; the tickers passed in lower case
    # are upper-cased in the perturbation
    got = perturb_ticker("how did apple do in 2023", "aapl", "msft", NAMES)
    assert got == {"question": "how did Microsoft Corporation do in 2023",
                   "perturbation": {"kind": "ticker", "from": "AAPL", "to": "MSFT"}}


@pytest.mark.criterion("C9")
def test_non_matches_return_none():
    # oracle: requirement 12 — "Pineapple" has a letter before "apple"; "aapl" is a ticker
    # form, matched case-sensitively; "AAPLX" has a letter after: no from form matches.
    # Control: the same sentence with "Apple" does match
    for q in ("Pineapple revenue", "aapl revenue", "AAPLX revenue"):
        assert perturb_ticker(q, "AAPL", "MSFT", NAMES) is None, q
    assert perturb_ticker("Apple revenue", "AAPL", "MSFT", NAMES)["question"] == (
        "Microsoft Corporation revenue")


@pytest.mark.criterion("C9")
def test_question_already_naming_the_target_returns_none():
    # oracle: requirement 12 — a to form already in the question makes the swap ambiguous.
    # Control: without the Microsoft mention it swaps
    assert perturb_ticker("Compare Apple and Microsoft", "AAPL", "MSFT", NAMES) is None
    assert perturb_ticker("Compare Apple and MSFT", "AAPL", "MSFT", NAMES) is None
    assert perturb_ticker("Compare Apple and Google", "AAPL", "MSFT", NAMES)["question"] == (
        "Compare Microsoft Corporation and Google")


@pytest.mark.criterion("C9")
def test_target_without_names_uses_its_ticker():
    # oracle: requirement 12 — no names for NVDA: a name match is replaced by "NVDA"
    got = perturb_ticker("Apple's R&D in 2024 (AAPL)", "AAPL", "NVDA", NAMES)
    assert got["question"] == "NVDA's R&D in 2024 (NVDA)"
    assert got["perturbation"] == {"kind": "ticker", "from": "AAPL", "to": "NVDA"}


@pytest.mark.criterion("C9")
def test_same_ticker_raises():
    # oracle: requirement 12 — from == to (upper-cased) -> ValueError
    with pytest.raises(ValueError):
        perturb_ticker("Apple revenue", "AAPL", "aapl", NAMES)


_words = st.lists(st.sampled_from(["revenue", "of", "and", "vs", "in", "2024", "what",
                                   "is", "AAPL", "AAPL's", "(AAPL)", "growth?"]),
                  min_size=1, max_size=12)


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(words=_words)
def test_round_trip_with_ticker_only_forms(words):
    # oracle: requirement 12 — with empty names the only forms are the tickers, so A -> B
    # then B -> A restores the question, and the output holds as many B tokens as the input
    # held A tokens
    words = words + ["AAPL"]
    q = " ".join(words)
    fwd = perturb_ticker(q, "AAPL", "MSFT", {})
    n_from = sum(w.count("AAPL") for w in words)
    assert fwd["question"].count("MSFT") == n_from
    assert "AAPL" not in fwd["question"]
    back = perturb_ticker(fwd["question"], "MSFT", "AAPL", {})
    assert back["question"] == q
