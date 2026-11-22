"""C1: a stored `produced` payload rebuilds the answer the graders saw."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.regrade import answer_from_payload
from src.eval.core.system import SystemAnswer


def _roundtrip(ans: SystemAnswer) -> dict:
    return json.loads(json.dumps(ans.to_dict()))


@pytest.mark.criterion("C1")
def test_every_graded_field_is_restored():
    # oracle: requirement 2 — the original answer's own fields (identity)
    original = SystemAnswer(
        text="Revenue was 391,035 million.",
        numeric_values=[391035000000.0, 12.5],
        rows=[{"ticker": "AAA", "value": 3.0}],
        fact_ids=[15488, 15811],
        chunk_ids=["c-1"],
        refused=False,
        hedged=True,
        error=None,
        selection={"accession": "0000320193-24-000123", "why": "resolved"},
        agent={"steps": 3},
    )
    rebuilt = answer_from_payload(_roundtrip(original))
    assert isinstance(rebuilt, SystemAnswer)
    assert rebuilt.text == original.text
    assert rebuilt.numeric_values == original.numeric_values
    assert rebuilt.rows == original.rows
    assert rebuilt.fact_ids == original.fact_ids
    assert rebuilt.chunk_ids == original.chunk_ids
    assert rebuilt.hedged is True and rebuilt.refused is False
    assert rebuilt.selection == original.selection
    assert rebuilt.agent == original.agent
    assert rebuilt.to_dict() == original.to_dict()


@pytest.mark.criterion("C1")
def test_missing_keys_take_the_defaults():
    # oracle: requirement 2 — missing keys read as "", [], False, None, {}
    rebuilt = answer_from_payload({"numeric_values": [5.0], "error": "Boom('x')"})
    assert isinstance(rebuilt, SystemAnswer)
    assert rebuilt.text == ""
    assert rebuilt.numeric_values == [5.0]
    assert rebuilt.rows == [] and rebuilt.fact_ids == [] and rebuilt.chunk_ids == []
    assert rebuilt.refused is False and rebuilt.hedged is False
    assert rebuilt.error == "Boom('x')"
    assert rebuilt.selection == {} and rebuilt.agent == {}
    assert rebuilt.query_id is None and rebuilt.raw is None
    assert rebuilt.served_models == {}
    # tolerance: defaults are exactly zero
    assert rebuilt.cost_usd == pytest.approx(0.0, abs=1e-12)


@pytest.mark.criterion("C1")
def test_error_answer_regrades_as_error(lookup_case):
    # oracle: dispatch grade ladder — a stored error grades ERROR
    rebuilt = answer_from_payload({"error": "TimeoutError('read')", "numeric_values": []})
    assert grade_case(lookup_case("e1", 10.0), rebuilt).grade.value == "ERROR"


_VALUES = st.lists(
    st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False),
    max_size=4,
)


@pytest.mark.criterion("C1")
@given(
    values=_VALUES,
    key=st.floats(min_value=1.0, max_value=1e9, allow_nan=False),
    cited=st.booleans(),
    refused=st.booleans(),
    hedged=st.booleans(),
    hit=st.booleans(),
)
def test_regrading_a_rebuilt_answer_gives_the_original_grade(
    lookup_case, values, key, cited, refused, hedged, hit
):
    # oracle: the grade of the original answer (identity, requirement 2)
    case = lookup_case("p1", key)
    stated = [*values, key] if hit else values
    original = SystemAnswer(
        text="t",
        numeric_values=stated,
        fact_ids=list(range(1, len(stated) + 1)) if cited else [],
        refused=refused,
        hedged=hedged,
    )
    rebuilt = answer_from_payload(_roundtrip(original))
    assert grade_case(case, rebuilt).to_dict() == grade_case(case, original).to_dict()
