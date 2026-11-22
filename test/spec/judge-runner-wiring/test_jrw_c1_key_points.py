"""C1: a case's key points come from the suite JSON, and decide whether it is text-graded."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.judge_wiring import case_key_points, is_text_graded


@pytest.mark.criterion("C1")
def test_key_points_read_and_round_trip(make_case):
    # oracle: requirement 1 — from_dict reads "key_points", to_dict writes it back
    case = make_case("t1", kind="narrative", expected_text="Ref.", key_points=["a", "b"])
    assert case_key_points(case) == ["a", "b"]
    dumped = case.to_dict()
    assert dumped.get("key_points") == ["a", "b"]
    assert case_key_points(EvalCase.from_dict(dumped)) == ["a", "b"]

    # a new list each time: editing it leaves the case alone
    got = case_key_points(case)
    got.append("x")
    assert case_key_points(case) == ["a", "b"]


@pytest.mark.criterion("C1")
def test_no_key_points(make_case):
    # oracle: requirement 1 — absent means none, and to_dict writes no key
    case = make_case("l1", expected_value=1.0)
    assert case_key_points(case) == []
    assert isinstance(case_key_points(case), list)
    assert "key_points" not in case.to_dict()


@pytest.mark.criterion("C1")
def test_text_graded_table(make_case):
    # oracle: requirement 2 — non-blank expected_text AND a non-empty key-point list
    table = [
        ({"expected_text": "Ref.", "key_points": ["a"]}, True),
        ({"expected_text": "Ref.", "key_points": ["a", "b"]}, True),
        ({"expected_text": "Ref."}, False),
        ({"expected_text": "Ref.", "key_points": []}, False),
        ({"key_points": ["a"]}, False),
        ({"expected_text": "", "key_points": ["a"]}, False),
        ({"expected_text": "  \n\t", "key_points": ["a"]}, False),
    ]
    for i, (fields, expected) in enumerate(table):
        case = make_case(f"c{i}", kind="narrative", **fields)
        assert is_text_graded(case) is expected, fields


_TEXT = st.one_of(st.none(), st.text(max_size=12))


@pytest.mark.criterion("C1")
@given(points=st.lists(st.text(max_size=10), max_size=5), text=_TEXT)
def test_round_trip_and_closed_form(make_case, points, text):
    # oracle: requirements 1 and 2, evaluated here on the generated fields
    fields = {"kind": "narrative", "key_points": points}
    if text is not None:
        fields["expected_text"] = text
    case = make_case("p1", **fields)
    assert case_key_points(case) == points
    assert case_key_points(EvalCase.from_dict(case.to_dict())) == points
    expected = isinstance(text, str) and bool(text.strip()) and bool(points)
    assert is_text_graded(case) is expected
