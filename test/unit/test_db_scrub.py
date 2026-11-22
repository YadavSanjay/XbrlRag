"""``src.db.scrub.scrub_nul``: the NUL scrub every Postgres store applies before a write."""

from __future__ import annotations

import datetime as dt

from src.db.scrub import scrub_nul


def test_strips_nul_from_nested_strings_and_keys():
    got = scrub_nul({"a\x00": ["x\x00y", {"b": ("c\x00",)}], "n": 1})
    assert got == {"a": ["xy", {"b": ("c",)}], "n": 1}


def test_keeps_tuples_as_tuples():
    # psycopg2 adapts a tuple and a list differently (row vs ARRAY), so the type must survive.
    row = ("qid", 0, "llm_call\x00", None)
    assert scrub_nul(row) == ("qid", 0, "llm_call", None)
    assert isinstance(scrub_nul(row), tuple)


def test_leaves_non_strings_and_literal_escape_text_alone():
    when = dt.datetime(2026, 11, 7)
    literal = r"a\u0000b"  # six characters of text, not a NUL
    assert scrub_nul([when, 1.5, None, True, literal]) == [when, 1.5, None, True, literal]
