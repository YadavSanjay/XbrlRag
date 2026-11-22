"""C2: picked_filer ignores [sqN] echo lines, whatever their final punctuation
(docs/specs/grader-pick-echo-grow.md, requirement 2).

Oracle: the pick each test expects is read by hand from the answer's own declarative words.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import picked_filer

TICKERS = ("DEVA", "DEVB", "DEVC")
ENDINGS = (".", "…", "….", "?", "")


@pytest.mark.criterion("C2")
def test_the_real_shape_picks_the_answers_filer(kit):
    # oracle: the answer says "Devb Corp (DEVB) had the highest working capital ratio"; the
    # [sq2] echo before it lists DEVC nearest before "highest", whatever it ends with
    for ending in ENDINGS:
        text = kit.real_shape(ending)
        assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVB", ending


@pytest.mark.criterion("C2")
def test_skipping_an_echo_never_favours_the_key(kit):
    # oracle: requirement 2 — the echo names DEVB (the winner elsewhere) nearest before
    # "highest"; the answer names DEVC as the highest, so the pick is DEVC
    text = (kit.pick_echo(".", ("DEVA", "DEVC", "DEVB"))
            + "Devc Corp (DEVC) had the highest working capital ratio, at 0.83. DEVB's working "
              "capital ratio was 1.35.")
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == "DEVC"


@pytest.mark.criterion("C2")
def test_echoes_as_the_only_superlative_sentences_give_no_pick(kit):
    # oracle: requirement 2 — with the echo lines gone no sentence has a superlative, so None;
    # the control with a declarative superlative sentence picks its filer
    body = "DEVA's working capital ratio was 0.95. DEVB's was 1.35. DEVC's was 0.83."
    assert picked_filer(kit.RANKING_ECHO + kit.pick_echo(".") + body, kit.CANDIDATES,
                        kit.RATIO) is None
    control = kit.pick_echo(".") + body + " DEVB led the group."
    assert picked_filer(control, kit.CANDIDATES, kit.RATIO) == "DEVB"


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    winner=st.sampled_from(TICKERS),
    fillers=st.lists(st.tuples(st.sampled_from(TICKERS), st.integers(1, 99)), max_size=4),
    pick_at=st.integers(0, 4),
    extra=st.lists(st.tuples(st.integers(0, 6), st.permutations(TICKERS),
                             st.sampled_from(ENDINGS)), max_size=3),
    first_order=st.permutations(TICKERS),
    first_ending=st.sampled_from(ENDINGS),
)
def test_echo_lines_never_change_a_declarative_pick(kit, winner, fillers, pick_at, extra,
                                                    first_order, first_ending):
    # oracle: by construction the body's only sentence with a superlative and the ratio is
    # "<winner> had the highest working capital ratio ...", so its pick is <winner>; echo lines
    # (each a whole line) are never read, wherever they are inserted
    body = [f"{t}'s working capital ratio was 0.{n:02d}.\n" for t, n in fillers]
    body.insert(min(pick_at, len(body)),
                f"{winner} had the highest working capital ratio, at 1.35.\n")
    # the first echo lists the winner first, so a loser is nearest before "highest"
    order = (winner, *(t for t in first_order if t != winner))
    pieces = list(body)
    for pos, perm, ending in extra:
        pieces.insert(min(pos, len(pieces)), kit.pick_echo(ending, tuple(perm), pos + 3))
    text = kit.pick_echo(first_ending, order, 1) + "".join(pieces)
    assert picked_filer("".join(body), kit.CANDIDATES, kit.RATIO) == winner
    assert picked_filer(text, kit.CANDIDATES, kit.RATIO) == winner, text
