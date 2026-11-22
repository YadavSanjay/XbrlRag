"""C1: EvalCase tags map to the six slice fields (requirement 1)."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.run_columns import slice_fields_from_tags

SPLITS = ["dev", "validation", "heldout_filers", "external", "temporal", "legacy"]
STATUSES = ["verified", "single-source"]
# field -> tag prefix, from the requirement-1 table
PREFIX = {"split": "split", "intent_id": "intent", "phrasing_family": "family",
          "template_id": "template", "key_source": "key_source", "key_status": "key_status"}
NONE6 = {k: None for k in PREFIX}

VALUE = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_.:", min_size=1, max_size=12)
# unrelated tags never start with one of the six prefixes followed by ':'
UNRELATED = st.sampled_from(["kind:lookup", "splitter:x", "intents:y", "families:z",
                             "keysource:q", "lookup", "sql_derived", "Split:dev"])


@pytest.mark.criterion("C1")
def test_all_six_fields_are_read():
    # oracle: requirement 1's table; the value is everything after the first colon
    tags = ["split:dev", "family:terse", "template:t7:v2", "intent:i-42",
            "key_source:sec_companyfacts", "key_status:verified", "kind:lookup"]
    assert slice_fields_from_tags(tags) == {
        "split": "dev", "phrasing_family": "terse", "template_id": "t7:v2",
        "intent_id": "i-42", "key_source": "sec_companyfacts", "key_status": "verified",
    }


@pytest.mark.criterion("C1")
def test_untagged_fields_are_none_and_other_tags_ignored():
    # oracle: requirement 1 — a field with no tag is None; `splitter:x` and a
    # differently-cased `Split:dev` are not split tags (prefixes are case-sensitive)
    assert slice_fields_from_tags([]) == NONE6
    assert slice_fields_from_tags(["splitter:x", "Split:dev", "sql_derived"]) == NONE6
    assert slice_fields_from_tags(["key_status:single-source"]) == {
        **NONE6, "key_status": "single-source"}


@pytest.mark.criterion("C1")
def test_a_repeated_identical_tag_is_fine():
    # oracle: requirement 1 — the same value twice is not a conflict
    assert slice_fields_from_tags(["split:validation", "split:validation"]) == {
        **NONE6, "split": "validation"}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("tags", [
    ["split:"],                              # empty value
    ["intent:"],                             # empty value
    ["split:dev", "split:validation"],       # two different values
    ["family:terse", "family:formal"],       # two different values
    ["split:test"],                          # unknown split
    ["split:Dev"],                           # unknown split (values are case-sensitive)
    ["key_status:unverified"],               # unknown key status
])
def test_malformed_tags_raise_value_error(tags):
    # oracle: requirement 1's four ValueError conditions
    with pytest.raises(ValueError):
        slice_fields_from_tags(tags)


@pytest.mark.criterion("C1")
@given(
    split=st.one_of(st.none(), st.sampled_from(SPLITS)),
    status=st.one_of(st.none(), st.sampled_from(STATUSES)),
    intent=st.one_of(st.none(), VALUE),
    family=st.one_of(st.none(), VALUE),
    template=st.one_of(st.none(), VALUE),
    source=st.one_of(st.none(), VALUE),
    extra=st.lists(UNRELATED, max_size=4),
    seed=st.integers(0, 2**32 - 1),
)
def test_rendered_tags_parse_back(split, status, intent, family, template, source, extra,
                                  seed):
    # oracle: requirement 1 — rendering each field as `<prefix>:<value>` and parsing,
    # in any order and among unrelated tags, gives back exactly those values
    expected = {"split": split, "intent_id": intent, "phrasing_family": family,
                "template_id": template, "key_source": source, "key_status": status}
    tags = [f"{PREFIX[k]}:{v}" for k, v in expected.items() if v is not None] + extra
    random.Random(seed).shuffle(tags)
    assert slice_fields_from_tags(tags) == expected
    assert slice_fields_from_tags(tuple(tags)) == expected
