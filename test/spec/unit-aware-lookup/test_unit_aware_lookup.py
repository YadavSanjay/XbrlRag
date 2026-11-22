"""Spec tests for unit-aware-lookup: resolve a lookup within one unit, defaulting to the
reporting currency (docs/specs/unit-aware-lookup.md).

Pure functions — no DB, no LLM.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.intent_router import IntentRouter, extract_currency_unit
from src.retriever.xbrl.xbrl_factstore_lookup import choose_lookup_unit

# TSMC FY2024 revenue as tagged in its FY2024 20-F (verified in mv_financial_facts):
# TWD for the year and both comparatives; a USD convenience translation for the latest
# period only.
TSMC_REVENUE = [
    ("TWD", "2025-01-01"),
    ("USD", "2025-01-01"),
    ("TWD", "2024-01-01"),
    ("TWD", "2023-01-01"),
]

LATEST, EARLIER = "2025-01-01", ["2024-01-01", "2023-01-01", "2022-01-01"]
CURRENCIES = ["TWD", "USD", "EUR", "JPY", "KRW", "GBP"]

# (trailing currency mention, ISO 4217 code)
CURRENCY_FORMS = [
    ("in New Taiwan dollars", "TWD"),
    ("in NT dollars", "TWD"),
    ("in NT$", "TWD"),
    ("in TWD", "TWD"),
    ("in US dollars", "USD"),
    ("in U.S. dollars", "USD"),
    ("in USD", "USD"),
]
CONCEPTS = ["revenue", "net income", "operating income", "capital expenditures", "gross profit"]

match = IntentRouter._match_concept_family


def _bare(text: str) -> str:
    """The concept text without surrounding separators, case-folded, for comparison."""
    return text.strip(" ,.;:-()").casefold()


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_requested_unit_wins_when_present_at_the_latest_period():
    # oracle: requirement 1 — a named currency restricts the lookup to that unit; one the
    # latest period does not carry means NOT_FOUND (None here)
    assert choose_lookup_unit(TSMC_REVENUE, "USD") == "USD"
    assert choose_lookup_unit(TSMC_REVENUE, "TWD") == "TWD"
    assert choose_lookup_unit(TSMC_REVENUE, "EUR") is None


@pytest.mark.criterion("C1")
@given(
    st.sets(st.sampled_from(CURRENCIES), min_size=1, max_size=4),
    st.sets(st.sampled_from(CURRENCIES), max_size=4),
    st.sampled_from(CURRENCIES),
)
def test_requested_unit_is_returned_iff_the_latest_period_has_it(latest, earlier, requested):
    # oracle: property of requirement 1, for any mix of units across periods
    obs = [(u, LATEST) for u in sorted(latest)] + [(u, EARLIER[0]) for u in sorted(earlier)]
    expected = requested if requested in latest else None
    assert choose_lookup_unit(obs, requested) == expected
    assert choose_lookup_unit(obs, sorted(latest)[0]) == sorted(latest)[0]


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_reporting_currency_beats_a_convenience_translation():
    # oracle: TSMC's 20-F convenience-translation pattern — TWD in every period, USD only
    # for the latest; a single-unit filer; and a genuinely ambiguous two-unit history
    assert choose_lookup_unit(TSMC_REVENUE) == "TWD"
    assert choose_lookup_unit([("USD", LATEST), ("USD", EARLIER[0])]) == "USD"
    assert choose_lookup_unit([("USD", LATEST)]) == "USD"
    both_everywhere = [("USD", LATEST), ("EUR", LATEST), ("USD", EARLIER[0]), ("EUR", EARLIER[0])]
    assert choose_lookup_unit(both_everywhere) is None
    assert choose_lookup_unit([("USD", LATEST), ("EUR", LATEST)]) is None


@pytest.mark.criterion("C2")
@given(
    st.lists(st.sampled_from(CURRENCIES), min_size=2, max_size=2, unique=True),
    st.integers(min_value=1, max_value=3),
)
def test_the_unit_with_comparatives_is_the_reporting_currency(pair, n_earlier):
    # oracle: property — whichever currency pair it is, the unit that also appears in
    # earlier periods is chosen over one tagged only for the latest period
    reporting, convenience = pair
    obs = [(convenience, LATEST), (reporting, LATEST)]
    obs += [(reporting, p) for p in EARLIER[:n_earlier]]
    assert choose_lookup_unit(obs) == reporting


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_order_and_extra_comparatives_do_not_change_the_choice():
    # oracle: metamorphic — reversing the facts or adding another TWD comparative leaves
    # the choice at TWD
    assert choose_lookup_unit(list(reversed(TSMC_REVENUE))) == "TWD"
    assert choose_lookup_unit([*TSMC_REVENUE, ("TWD", "2022-01-01")]) == "TWD"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(
    st.lists(st.sampled_from(CURRENCIES), min_size=2, max_size=2, unique=True),
    st.permutations(range(4)),
    st.integers(min_value=0, max_value=2),
)
def test_permuting_and_adding_comparatives_is_invariant(pair, order, extra):
    # oracle: metamorphic — a set of facts has no order, and more comparatives in the
    # reporting currency only confirm it
    reporting, convenience = pair
    base = [(convenience, LATEST), (reporting, LATEST), (reporting, EARLIER[0]),
            (reporting, EARLIER[1])]
    shuffled = [base[i] for i in order]
    padded = shuffled + [(reporting, EARLIER[2])] * extra
    assert choose_lookup_unit(shuffled) == reporting
    assert choose_lookup_unit(padded) == choose_lookup_unit(shuffled)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_currency_mentions_are_extracted_point_cases():
    # oracle: ISO 4217 — New Taiwan dollar TWD, US dollar USD
    stripped, code = extract_currency_unit("revenue in New Taiwan dollars")
    assert (stripped, code) == ("revenue", "TWD")
    stripped, code = extract_currency_unit("Net Income, in USD.")
    assert _bare(stripped) == "net income" and code == "USD"
    stripped, code = extract_currency_unit("capital expenditures (in NT$)")
    assert _bare(stripped) == "capital expenditures" and code == "TWD"
    assert extract_currency_unit("operating income") == ("operating income", None)


@pytest.mark.criterion("C4")
@given(
    st.sampled_from(CONCEPTS),
    st.sampled_from(CURRENCY_FORMS),
    st.sampled_from([str.lower, str.upper, str.title, lambda s: s]),
    st.sampled_from(["", ".", ",", ")"]),
)
def test_any_currency_form_in_any_case_is_extracted(concept, form, case, trail):
    # oracle: property — each listed currency mention maps to its ISO code regardless of
    # case and trailing punctuation, leaving the concept text
    mention, iso = form
    opener = "(" if trail == ")" else ""
    stripped, code = extract_currency_unit(f"{concept} {opener}{case(mention)}{trail}")
    assert code == iso
    assert _bare(stripped) == concept


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_extraction_is_idempotent_and_feeds_the_fast_path():
    # oracle: metamorphic — removing the currency mention leaves the concept, which then
    # matches the same family as the bare concept ("revenue" -> Revenue)
    stripped, code = extract_currency_unit("revenue in New Taiwan dollars")
    assert code == "TWD"
    assert extract_currency_unit(stripped) == (stripped, None)
    assert match(stripped) == match("revenue") == "Revenue"


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.sampled_from(CONCEPTS), st.sampled_from(CURRENCY_FORMS))
def test_stripped_phrase_matches_like_the_bare_concept(concept, form):
    # oracle: metamorphic — for every concept and currency form, the stripped phrase is a
    # fixed point of extraction and maps to the bare concept's family
    stripped, code = extract_currency_unit(f"{concept} {form[0]}")
    assert code == form[1]
    assert extract_currency_unit(stripped)[1] is None
    assert match(stripped) == match(concept)
    assert match(concept) is not None
