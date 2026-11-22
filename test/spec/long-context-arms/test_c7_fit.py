"""C7: where the filings stop fitting is reported, and nothing is cut."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context_arms import (
    LongContextMultiSystem,
    LongContextResolvedSystem,
    fit_summary,
)

A = "AAA/2024/10-K/canonical.htm"
B = "BBB/2024/10-K/canonical.htm"
C = "CCC/2024/10-K/canonical.htm"
KEYS = {("AAA", 2024): A, ("BBB", 2024): B, ("CCC", 2024): C}


def _objects(doc, *lengths: int) -> dict[str, str]:
    """Filings whose ``filing_text`` is exactly this many characters (one paragraph each)."""
    return {key: doc("x" * n) for key, n in zip((A, B, C), lengths, strict=False)}


@pytest.mark.criterion("C7")
def test_exactly_at_the_limit_is_sent(make_multi, make_case, doc, router_reply):
    # oracle: requirement 12 — 10 + 20 + 30 = 60 = 4 * 15 characters
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB", "CCC"], ["fiscal 2024"]),
        keys=KEYS, objects=_objects(doc, 10, 20, 30), max_input_tokens=15,
    )
    answer = arm.answer(make_case())
    assert answer.error is None
    assert adapter.calls == 1
    selection = answer.selection
    assert selection["status"] == "sent"
    assert (selection["k"], selection["total_chars"], selection["limit_chars"]) == (3, 60, 60)
    assert selection["fits"] == 3


@pytest.mark.criterion("C7")
def test_one_character_over_is_excluded_and_nothing_is_cut(
    make_multi, make_case, doc, router_reply
):
    # oracle: requirement 12 — 10 + 20 + 31 = 61 > 60, and 10 + 20 = 30 <= 60, so two fit
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB", "CCC"], ["fiscal 2024"]),
        keys=KEYS, objects=_objects(doc, 10, 20, 31), max_input_tokens=15,
    )
    answer = arm.answer(make_case())
    assert answer.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert answer.numeric_values == []
    selection = answer.selection
    assert selection["status"] == "over_window"
    assert (selection["k"], selection["total_chars"], selection["limit_chars"]) == (3, 61, 60)
    assert selection["fits"] == 2
    assert [f["chars"] for f in selection["filings"]] == [10, 20, 31]


@pytest.mark.criterion("C7")
def test_too_many_targets_stop_before_anything_is_read(make_multi, make_case, router_reply):
    # oracle: requirement 13 — 2 companies x 3 years = 6 targets, over max_filings = 5
    arm, _, adapter, resolver, store = make_multi(
        router_text=router_reply(
            ["AAA", "BBB"], ["fiscal 2022", "fiscal 2023", "fiscal 2024"]
        ),
        max_filings=5,
    )
    answer = arm.answer(make_case())
    assert answer.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert resolver.calls == []
    assert store.reads == []
    assert answer.selection["status"] == "too_many"
    assert answer.selection["k"] == 6


@pytest.mark.criterion("C7")
def test_exactly_max_filings_is_allowed(make_multi, make_case, router_reply):
    # oracle: requirement 13 — "more than", so 2 x 3 = 6 with max_filings = 6 goes ahead
    arm, _, _, resolver, _ = make_multi(
        router_text=router_reply(
            ["AAA", "BBB"], ["fiscal 2022", "fiscal 2023", "fiscal 2024"]
        ),
        max_filings=6,
    )
    arm.answer(make_case())
    assert len(resolver.calls) == 6


@pytest.mark.criterion("C7")
def test_fit_summary_table():
    # oracle: requirement 14, by hand — k=2: one sent, one over; k=3: two over (one too_many
    # would need k over max_filings, so k=11 stands in); undecided and no_filing are not fit
    # results and not_in_store neither
    selections = [
        {"status": "sent", "k": 2},
        {"status": "over_window", "k": 2},
        {"status": "sent", "k": 1},
        {"status": "over_window", "k": 3},
        {"status": "over_window", "k": 3},
        {"status": "too_many", "k": 11},
        {"status": "undecided", "k": 0},
        {"status": "no_filing", "k": 0},
        {"status": "not_in_store", "k": 0},
    ]
    assert fit_summary(selections) == {
        1: {"sent": 1, "over_window": 0},
        2: {"sent": 1, "over_window": 1},
        3: {"sent": 0, "over_window": 2},
        11: {"sent": 0, "over_window": 1},
    }


@pytest.mark.criterion("C7")
def test_fit_summary_of_nothing_is_empty():
    # oracle: requirement 14 — no fit results, no rows. The sent row keeps this from being
    # satisfied by a constant empty answer.
    assert fit_summary([]) == {}
    assert fit_summary([{"status": "sent", "k": 1}]) == {1: {"sent": 1, "over_window": 0}}


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("cls", [LongContextResolvedSystem, LongContextMultiSystem])
def test_a_wider_window_is_refused_at_construction(offline_env, cls):
    # oracle: requirement 15 — prompts over 272K tokens bill at rates pricing.yaml does not have
    with pytest.raises(ValueError):
        cls(max_input_tokens=250_001)
    assert cls(max_input_tokens=250_000).max_input_tokens == 250_000


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(
    lengths=st.lists(st.integers(1, 40), min_size=1, max_size=3),
    tokens=st.integers(1, 30),
)
def test_fit_equals_the_prefix_sum(make_multi, make_case, doc, router_reply, lengths, tokens):
    # oracle: the prefix sum, computed here from requirement 12
    tickers = ["AAA", "BBB", "CCC"][: len(lengths)]
    limit = 4 * tokens
    total, fits = 0, 0
    for n in lengths:
        total += n
        if total <= limit:
            fits += 1
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(tickers, ["fiscal 2024"]),
        keys=KEYS, objects=_objects(doc, *lengths), max_input_tokens=tokens,
    )
    answer = arm.answer(make_case())
    assert adapter.calls == (1 if total <= limit else 0)
    assert answer.selection["fits"] == fits
    assert answer.selection["total_chars"] == total
    assert answer.selection["status"] == ("sent" if total <= limit else "over_window")
    if total > limit:
        assert answer.error.startswith("excluded: ")
