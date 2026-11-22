"""C5: parsing a baseline's JSON answer object into a SystemAnswer."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import parse_structured_answer


def _approx(values):
    # tolerance: JSON float text round-trips exactly; 1e-12 relative only guards repr
    return pytest.approx(values, rel=1e-12)


@pytest.mark.criterion("C5")
def test_bare_object_maps_every_field():
    # oracle: the field table in C5, hand-built object
    text = json.dumps(
        {
            "value": 391035000000,
            "unit": "USD",
            "refused": False,
            "hedged": True,
            "cited_chunks": ["c-1", "c-2"],
        }
    )
    ans = parse_structured_answer(text)
    assert ans.numeric_values == _approx([391035000000.0])
    assert ans.hedged is True
    assert ans.refused is False
    assert ans.chunk_ids == ["c-1", "c-2"]
    assert ans.fact_ids == []
    assert ans.text == text


@pytest.mark.criterion("C5")
def test_numeric_string_value_and_rows():
    # oracle: C5 — a string value goes through extract_numbers ("$391B" → 391e9); row
    # values follow it in numeric_values, in row order
    text = json.dumps(
        {
            "value": "$391B",
            "rows": [{"entity": "AAA", "value": 300}, {"entity": "BBB", "value": "200"}],
        }
    )
    ans = parse_structured_answer(text)
    assert ans.numeric_values == _approx([391e9, 300.0, 200.0])
    assert [r["entity"] for r in ans.rows] == ["AAA", "BBB"]
    assert [r["value"] for r in ans.rows] == _approx([300.0, 200.0])
    assert ans.refused is False and ans.hedged is False


@pytest.mark.criterion("C5")
def test_unparseable_reply_falls_back_to_free_text():
    # oracle: C5 fallback — numbers from extract_numbers; refused only when none found
    stated = parse_structured_answer("Revenue was $94.9 billion.")
    assert stated.numeric_values == _approx([94.9e9])
    assert stated.refused is False
    assert stated.hedged is False
    assert stated.rows == []

    declined = parse_structured_answer("I don't know.")
    assert declined.numeric_values == []
    assert declined.refused is True
    assert declined.text == "I don't know."


_OBJECTS = st.fixed_dictionaries(
    {
        "value": st.one_of(
            st.none(), st.floats(min_value=-1e13, max_value=1e13, allow_nan=False)
        ),
        "rows": st.lists(
            st.fixed_dictionaries(
                {
                    "entity": st.sampled_from(["AAA", "BBB", "CCC", "Europe", "Services"]),
                    "value": st.floats(min_value=-1e13, max_value=1e13, allow_nan=False),
                }
            ),
            max_size=5,
        ),
        "refused": st.booleans(),
        "hedged": st.booleans(),
        "cited_chunks": st.lists(st.sampled_from(["c-1", "c-2", "c-3"]), max_size=3),
    }
)


@pytest.mark.criterion("C5")
@given(obj=_OBJECTS)
def test_round_trip_of_any_object(obj):
    # oracle: closed form — the object's own fields are the expected values
    ans = parse_structured_answer(json.dumps(obj))
    expected_values = ([obj["value"]] if obj["value"] is not None else []) + [
        r["value"] for r in obj["rows"]
    ]
    assert ans.numeric_values == _approx(expected_values)
    assert [r["entity"] for r in ans.rows] == [r["entity"] for r in obj["rows"]]
    assert ans.refused is obj["refused"]
    assert ans.hedged is obj["hedged"]
    assert ans.chunk_ids == obj["cited_chunks"]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(obj=_OBJECTS, prose=st.sampled_from(["Here is my answer:", "Answer follows.", ""]))
def test_fencing_or_prose_does_not_change_the_result(obj, prose):
    # oracle: C5 — the first top-level object is used however it is wrapped
    body = json.dumps(obj)
    bare = parse_structured_answer(body)
    fenced = parse_structured_answer(f"{prose}\n```json\n{body}\n```\nThanks.")
    inline = parse_structured_answer(f"{prose} {body} That is all.")
    for other in (fenced, inline):
        assert other.numeric_values == _approx(list(bare.numeric_values))
        assert other.rows == bare.rows
        assert other.refused is bare.refused
        assert other.hedged is bare.hedged
        assert other.chunk_ids == bare.chunk_ids
    assert bare.refused is obj["refused"]
