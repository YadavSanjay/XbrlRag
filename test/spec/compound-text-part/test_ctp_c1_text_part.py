"""C1: ``text_part`` finds a compound's judged text part (compound-text-part, requirement 1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import text_part


@pytest.mark.criterion("C1")
def test_text_dependent_is_the_text_part(kit):
    # oracle: requirement 1 — built as _grade_compound builds a part: id "<id>#<part>", the
    # compound's question, the part's own fields
    part = text_part(kit.case(kit.compound()))
    assert part is not None
    assert part.id == "hx-c01#dependent"
    assert part.kind == "narrative"
    assert part.question == kit.QUESTION
    assert part.expected_text == kit.REFERENCE
    assert list(part.key_points) == kit.KEY_POINTS


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("which", ["trend", "no_key_points", "blank_reference",
                                   "standalone_text", "screen"])
def test_no_text_part(kit, which):
    # oracle: requirement 1 — only a compound's narrative part with a non-blank reference
    # answer and at least one key point is a text part
    d = {
        "trend": lambda: kit.compound(kit.trend_fields()),
        "no_key_points": lambda: kit.compound(kit.text_fields(key_points=[])),
        "blank_reference": lambda: kit.compound(kit.text_fields(reference="   ")),
        "standalone_text": kit.standalone_text,
        "screen": kit.screen,
    }[which]()
    assert text_part(kit.case(d)) is None


@pytest.mark.criterion("C1")
def test_first_of_two_text_parts(kit):
    # oracle: requirement 1 — the first qualifying entry of case.parts
    d = kit.compound()
    second = {"part": "other", **kit.text_fields(reference="A second reference.")}
    d["parts"].append(second)
    part = text_part(kit.case(d))
    assert part is not None
    assert part.id == "hx-c01#dependent"
    assert part.expected_text == kit.REFERENCE


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(text_first=st.booleans())
def test_part_order_does_not_change_the_part(kit, text_first):
    # oracle: requirement 1 — the text part is found wherever it sits among the parts
    part = text_part(kit.case(kit.compound(text_first=text_first)))
    assert part is not None
    assert part.id == "hx-c01#dependent"
    assert part.expected_text == kit.REFERENCE
