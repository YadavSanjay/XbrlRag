"""C3: reading a filled label sheet back, strictly."""

from __future__ import annotations

import csv
import io

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import build_label_sheet, parse_label_sheet

HEADER = ["answer_id", "dimension", "item_id", "text", "label"]


def _answers(answer):
    a1 = answer(
        "a1",
        claims=[("s0", "Revenue grew.", ["Revenue grew 5%."]), ("s1", "Prices fell.", [])],
        key_points=["revenue grew"],
    )
    return [a1]


def _sheet(rows: list[list[str]], *, newline: str = "\n", bom: bool = False) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator=newline).writerows([HEADER, *rows])
    return ("﻿" if bom else "") + buf.getvalue()


# The four label keys of _answers, hand-listed (requirement 3): s1 is uncited.
FILLED = [
    ["a1", "correct", "s0", "Revenue grew.", "y"],
    ["a1", "supported", "s0", "Revenue grew.", "Yes "],
    ["a1", "correct", "s1", "Prices fell.", " N"],
    ["a1", "covered", "kp0", "revenue grew", "no"],
]
EXPECTED = {
    ("a1", "correct", "s0"): True,
    ("a1", "supported", "s0"): True,
    ("a1", "correct", "s1"): False,
    ("a1", "covered", "kp0"): False,
}


@pytest.mark.criterion("C3")
def test_filled_sheet_reads_back(answer):
    # oracle: requirement 6 - y/yes True, n/no False, case and surrounding space ignored
    answers = _answers(answer)
    assert parse_label_sheet(_sheet(FILLED), answers) == EXPECTED
    assert parse_label_sheet(_sheet(FILLED, newline="\r\n", bom=True), answers) == EXPECTED


@pytest.mark.criterion("C3")
def test_bad_sheets_raise(answer):
    # oracle: requirement 6 - nothing is defaulted; every malformed sheet is refused
    answers = _answers(answer)

    def with_label(i: int, value: str) -> list[list[str]]:
        rows = [list(r) for r in FILLED]
        rows[i][4] = value
        return rows

    bad = [
        _sheet(with_label(0, "")),
        _sheet(with_label(1, "maybe")),
        _sheet(FILLED[:-1]),
        _sheet([*FILLED, FILLED[0]]),
        _sheet([*FILLED, ["a1", "supported", "s1", "Prices fell.", "n"]]),
        _sheet([*FILLED[:-1], ["a1", "covered", "kp0", "revenue grew"]]),
        _sheet(FILLED).replace("answer_id,", "answer,", 1),
    ]
    for text in bad:
        with pytest.raises(ValueError):
            parse_label_sheet(text, answers)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.permutations(list(range(len(FILLED)))), st.text(max_size=15))
def test_row_order_and_text_column_do_not_matter(answer, order, junk):
    # oracle: requirement 6 - row order is free and the text column is ignored
    answers = _answers(answer)
    rows = [[*FILLED[i][:3], junk, FILLED[i][4]] for i in order]
    assert parse_label_sheet(_sheet(rows), answers) == EXPECTED


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.data())
def test_export_fill_import_round_trip(answer, keys_of, data):
    # oracle: requirements 4 and 6 - filling the exported sheet and reading it back is the
    # identity on the labels written
    answers = [
        answer("a1", claims=[("s0", "x", ["p"]), ("s1", "y", [])], key_points=["k0", "k1"]),
        answer("a2", claims=[("s0", "z", ["q", "r"])], key_points=["k"]),
    ]
    keys = [k for a in answers for k in keys_of(a)]
    truth = {k: data.draw(st.booleans()) for k in keys}
    rows = list(csv.reader(io.StringIO(build_label_sheet(answers))))
    filled = [rows[0]] + [[*r[:4], "y" if truth[tuple(r[:3])] else "n"] for r in rows[1:]]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(filled)
    assert parse_label_sheet(buf.getvalue(), answers) == truth
