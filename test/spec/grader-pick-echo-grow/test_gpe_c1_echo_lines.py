"""C1: without_subquery_echoes deletes every [sqN] echo line, and nothing else
(docs/specs/grader-pick-echo-grow.md, requirement 1).

Oracle: each expected string is the input with its echo lines struck out by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import without_subquery_echoes


@pytest.mark.criterion("C1")
def test_echo_lines_are_deleted_with_their_newline(kit):
    # oracle: requirement 1 by hand — the two echo lines (one ending ".", one with no final
    # punctuation) go with their newlines; the answer lines stay verbatim, in order
    text = ("[sq1] Among the covered companies, identify the 3 companies with the largest "
            "total debt for FY24.\n"
            "DEVA, DEVB and DEVC carry the most debt.\n\n"
            "[sq2] Among DEVB, DEVA, DEVC, identify the highest working capital ratio\n"
            "DEVB had the highest working capital ratio, at 1.35.")
    assert without_subquery_echoes(text) == ("DEVA, DEVB and DEVC carry the most debt.\n\n"
                                             "DEVB had the highest working capital ratio, at "
                                             "1.35.")
    assert without_subquery_echoes(kit.real_shape()) == kit.RANKING_ANSWER + kit.PICK_ANSWER


@pytest.mark.criterion("C1")
def test_indented_and_multi_digit_labels_are_echoes():
    # oracle: requirement 1 — spaces and tabs may precede the label; N is one or more digits;
    # an empty rest is still an echo line
    text = "  [sq12] Which led?\n\t[sq3]\n[sq7]Identify the leader.\nDEVA led.\n"
    assert without_subquery_echoes(text) == "DEVA led.\n"


@pytest.mark.criterion("C1")
def test_lines_that_are_not_echoes_are_kept():
    # oracle: requirement 1 — the label counts only at a line's start, lowercase "sq", at least
    # one digit, closed by "]" right after the digits; so every line here stays
    text = ("See [sq2] above.\n[sq] Identify the leader.\n[SQ2] Identify the leader.\n"
            "[q2] Identify the leader.\n[sq2a] Identify the leader.\nsq2] Identify.\nDEVA led.")
    assert without_subquery_echoes(text) == text
    plain = "DEVA had the highest working capital ratio.\nDEVB followed."
    assert without_subquery_echoes(plain) == plain


@pytest.mark.criterion("C1")
def test_a_last_echo_line_leaves_the_newline_before_it():
    # oracle: requirement 1 — the last line has no newline of its own; the previous line's
    # newline belongs to that line and is kept
    assert without_subquery_echoes("DEVA led.\n[sq2] Identify the leader.") == "DEVA led.\n"
    assert without_subquery_echoes("[sq1] Identify the leader.") == ""
    assert without_subquery_echoes("DEVA led.\r\n[sq2] Which led?\r\nDEVB.") == "DEVA led.\r\nDEVB."


_WORD = st.sampled_from(("DEVA", "Devb", "Corp", "led", "the", "highest", "ratio", "1.35",
                         "[note]", "sq2]", "(DEVC)", "fiscal", "2024", "?", "."))
_LINE = st.lists(_WORD, max_size=8).map(" ".join)
_ECHO = st.tuples(
    st.sampled_from(("", " ", "  ", "\t")),
    st.integers(0, 999),
    st.lists(_WORD, max_size=8).map(" ".join),
    st.sampled_from(("", ".", "?", "…", "….")),
).map(lambda t: f"{t[0]}[sq{t[1]}] {t[2]}{t[3]}\n")


@pytest.mark.criterion("C1")
@given(lines=st.lists(_LINE, min_size=1, max_size=6),
       echoes=st.lists(st.tuples(st.integers(0, 6), _ECHO), min_size=1, max_size=4))
def test_inserted_echo_lines_are_exactly_removed(lines, echoes):
    # oracle: requirement 1 — the original lines each end in "\n" and none starts with a label,
    # so deleting the inserted echo lines (each a whole line with its newline) gives it back
    original = [f"{line}\n" for line in lines]
    pieces = list(original)
    for pos, echo in echoes:
        pieces.insert(min(pos, len(pieces)), echo)
    assert without_subquery_echoes("".join(pieces)) == "".join(original)
    once = without_subquery_echoes("".join(pieces))
    assert without_subquery_echoes(once) == once
