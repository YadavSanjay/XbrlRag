"""C2: the blank label sheet and the labeller's context."""

from __future__ import annotations

import csv
import io

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import build_label_sheet, render_label_context

HEADER = ["answer_id", "dimension", "item_id", "text", "label"]
ODD = 'Margins, "adjusted",\nfell.'


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def _two(answer):
    a1 = answer(
        "a1",
        claims=[("s0", "Revenue grew.", ["Revenue grew 5%."]), ("s1", ODD, [])],
        key_points=["revenue grew", "margins fell"],
    )
    a2 = answer(
        "a2", claims=[("s3", "Cash rose.", ["Cash rose.", "Cash was up."])], key_points=["cash"]
    )
    return a1, a2


@pytest.mark.criterion("C2")
def test_sheet_rows_in_order(answer):
    # oracle: requirements 3-4, rows listed by hand: per claim correct then (if cited)
    # supported, then covered per key point; the uncited s1 has no supported row
    a1, a2 = _two(answer)
    text = build_label_sheet([a1, a2])
    assert _rows(text) == [
        HEADER,
        ["a1", "correct", "s0", "Revenue grew.", ""],
        ["a1", "supported", "s0", "Revenue grew.", ""],
        ["a1", "correct", "s1", ODD, ""],
        ["a1", "covered", "kp0", "revenue grew", ""],
        ["a1", "covered", "kp1", "margins fell", ""],
        ["a2", "correct", "s3", "Cash rose.", ""],
        ["a2", "supported", "s3", "Cash rose.", ""],
        ["a2", "covered", "kp0", "cash", ""],
    ]
    assert text.startswith("answer_id,dimension,item_id,text,label\n")


@pytest.mark.criterion("C2")
def test_duplicate_answer_ids_raise(answer):
    # oracle: requirement 4
    with pytest.raises(ValueError):
        build_label_sheet([answer("a1"), answer("a1")])


@pytest.mark.criterion("C2")
def test_context_holds_every_text(answer):
    # oracle: requirement 5 - every string the labeller needs appears verbatim, answers in order
    a1, a2 = _two(answer)
    md = render_label_context([a1, a2])
    for needle in (
        "## a1",
        "## a2",
        a1.question,
        a1.reference_answer,
        "kp0: revenue grew",
        "kp1: margins fell",
        "kp0: cash",
        "s0: Revenue grew.",
        f"s1: {ODD}",
        "s3: Cash rose.",
        "Revenue grew 5%.",
        "Cash was up.",
        "(no cited passages)",
    ):
        assert needle in md
    assert md.index("## a1") < md.index("## a2")
    assert md.count("(no cited passages)") == 1


_CHARS = st.characters(blacklist_categories=("Cs", "Cc"))
_TEXT = st.text(alphabet=_CHARS, min_size=1, max_size=20)
_CLAIM = st.tuples(_TEXT, st.lists(_TEXT, max_size=2))
_SPEC = st.tuples(st.lists(_CLAIM, min_size=1, max_size=3), st.integers(1, 3))


@pytest.mark.criterion("C2")
@given(st.lists(_SPEC, min_size=1, max_size=4))
def test_row_count_and_keys(answer, keys_of, spec):
    # oracle: requirement 3 - rows = claims + cited claims + key points, keys as listed
    answers = []
    for n, (claims, k) in enumerate(spec):
        answers.append(
            answer(
                f"a{n}",
                claims=[(f"s{i}", t, p) for i, (t, p) in enumerate(claims)],
                key_points=[f"point {j}" for j in range(k)],
            )
        )
    rows = _rows(build_label_sheet(answers))
    expected = [k for a in answers for k in keys_of(a)]
    assert len(rows) - 1 == sum(len(c) + sum(1 for _, p in c if p) + k for c, k in spec)
    assert [tuple(r[:3]) for r in rows[1:]] == expected
    assert all(r[4] == "" for r in rows[1:])
