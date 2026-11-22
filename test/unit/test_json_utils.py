"""Unit tests for the tolerant JSON extractor (gateway/json_utils.py)."""
import json

import pytest

from src.llm.gateway import extract_json


def test_plain_json_object():
    assert extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_plain_json_array():
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_strips_json_code_fence():
    raw = '```json\n{"intent": "NARRATIVE"}\n```'
    assert extract_json(raw) == {"intent": "NARRATIVE"}


def test_strips_bare_code_fence():
    raw = '```\n{"ok": true}\n```'
    assert extract_json(raw) == {"ok": True}


def test_extracts_object_from_surrounding_prose():
    raw = 'Sure! Here is the result:\n{"verdicts": [{"index": 0}]}\nHope that helps.'
    assert extract_json(raw) == {"verdicts": [{"index": 0}]}


def test_braces_inside_strings_do_not_confuse_matcher():
    raw = 'prefix {"note": "a } brace { inside", "n": 2} suffix'
    assert extract_json(raw) == {"note": "a } brace { inside", "n": 2}


def test_nested_objects():
    raw = 'text {"a": {"b": {"c": [1, 2]}}} more'
    assert extract_json(raw) == {"a": {"b": {"c": [1, 2]}}}


def test_no_json_raises_jsondecodeerror():
    with pytest.raises(json.JSONDecodeError):
        extract_json("this is not json at all")


def test_none_raises_jsondecodeerror():
    with pytest.raises(json.JSONDecodeError):
        extract_json(None)


def test_unbalanced_raises_jsondecodeerror():
    with pytest.raises(json.JSONDecodeError):
        extract_json('{"a": 1, "b":')
