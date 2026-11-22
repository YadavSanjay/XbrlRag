"""C1: an interrogative sentence is never the pick sentence
(docs/specs/pick-skips-questions.md, requirements 2 and 3).

Oracle: the pick each test expects is read by hand from the answer's own declarative words.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import picked_filer

TICKERS = ("DEVA", "DEVB", "DEVC")


@pytest.mark.criterion("C1")
def test_the_three_real_shapes_pick_the_answers_filer(kit):
    # oracle: each answer says "DEVA ... the highest operating (profit) margin" in a declarative
    # sentence; the [sq2] question before it lists DEVB nearest before its superlative
    for text in kit.SHAPES:
        assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVA", text[-300:]


@pytest.mark.criterion("C1")
def test_only_questions_hold_a_superlative_gives_no_pick(kit):
    # oracle: requirement 3 — no non-interrogative sentence has a superlative, so no pick
    alone = "[sq2] Among DEVA, DEVC, DEVB, which had the highest operating margin in fiscal 2024?"
    assert picked_filer(alone, kit.CANDIDATES, kit.RATIO) is None
    with_body = (alone + "\nDEVA's operating margin was 31.5%. DEVC's was 28.9%. "
                 "DEVB's was 26.1%.")
    assert picked_filer(with_body, kit.CANDIDATES, kit.RATIO) is None
    without_ratio = "Which filer led the group?\nDEVA reported revenue of 391 billion USD."
    assert picked_filer(without_ratio, kit.CANDIDATES, kit.RATIO) is None


@pytest.mark.criterion("C1")
def test_fallback_takes_the_last_declarative_superlative_sentence(kit):
    # oracle: requirement 3 — no sentence names the ratio; the last NON-interrogative sentence
    # with a superlative is "DEVA led on that measure.", so DEVA
    text = "DEVA led on that measure. Which filer led the group?"
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVA"
    text = "[sq2] Among DEVB, DEVC, which led?\nDEVA led on that measure. Which filer led?"
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVA"


@pytest.mark.criterion("C1")
def test_a_question_mark_that_is_not_a_split_is_declarative(kit):
    # oracle: requirement 2 — "?)" is not a split, so the second sentence ends at "first." and
    # is declarative; it names DEVA before "highest"
    text = ("[sq2] Which of DEVB, DEVC had the highest operating margin?\n"
            "DEVA (the highest operating margin?) ranked first.")
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVA"


@pytest.mark.criterion("C1")
def test_skipping_a_question_never_favours_the_key(kit):
    # oracle: requirement 3 — the question names DEVA (the winner in the other tests) nearest
    # before "highest"; the answer names DEVC as the highest, so the pick is DEVC
    text = ("[sq2] Among DEVB, DEVC, DEVA, which company has the highest operating profit margin "
            "for fiscal 2024?\nDEVC had the highest operating profit margin, at 0.289. "
            "DEVA's operating profit margin was 0.315.")
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVC"


def _question(k: int, order: tuple[str, ...]) -> str:
    return (f"[sq{k}] Among {', '.join(order)}, which company has the highest operating "
            f"profit margin for fiscal 2024?")


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    winner=st.sampled_from(TICKERS),
    fillers=st.lists(st.tuples(st.sampled_from(TICKERS), st.integers(1, 99)), max_size=4),
    pick_at=st.integers(0, 4),
    ranking_first=st.booleans(),
    extra=st.lists(st.tuples(st.integers(0, 6), st.permutations(TICKERS)), max_size=3),
    first_order=st.permutations(TICKERS),
)
def test_question_lines_never_change_a_declarative_pick(kit, winner, fillers, pick_at,
                                                        ranking_first, extra, first_order):
    # oracle: by construction the body's only sentence with a superlative and the ratio is
    # "<winner> had the highest operating margin ...", so its pick is <winner>; question lines
    # (each ending "?\n") are never the pick sentence, wherever they are inserted
    body = [f"{t}'s operating margin was {n}.5%." for t, n in fillers]
    body.insert(min(pick_at, len(body)), f"{winner} had the highest operating margin, at 31.5%.")
    if ranking_first:
        body.insert(0, "DEVA, DEVB and DEVC had the largest total debt.")
    # the prefixed question lists the winner first, so a loser is nearest before "highest"
    order = (winner, *(t for t in first_order if t != winner))
    pieces = list(body)
    for pos, perm in extra:
        pieces.insert(min(pos, len(pieces)), "\n" + _question(pos + 2, tuple(perm)) + "\n")
    text = _question(1, order) + "\n" + " ".join(pieces)
    assert picked_filer(" ".join(body), kit.CANDIDATES, kit.RATIO) == winner
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == winner, text
