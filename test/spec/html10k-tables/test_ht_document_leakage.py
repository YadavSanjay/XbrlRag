"""C9 (the document walk) and C10 (leakage). Spec: docs/specs/html10k-tables.md."""

from __future__ import annotations

import ast
import re
from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table, parse_tables

TABLE_FIXTURES = [
    "cost_income_statement.html",
    "ko_tax_rate.html",
    "unp_income.html",
    "wmt_swaps.html",
    "split_parens.html",
    "superscripts.html",
    "pension_groups.html",
]
FORBIDDEN_TEXT = [
    r"\bix:",
    "nonfraction",
    "nonnumeric",
    "contextref",
    "unitref",
    "xbrl_",
    "fact_id",
    r"\bmv_",
]
FORBIDDEN_IMPORTS = {"arelle", "psycopg", "psycopg2", "qdrant_client", "sqlalchemy"}


@pytest.mark.criterion("C9")
def test_one_table_per_element_with_the_plain_ordinal(load):
    # oracle: fixtures/document.html holds nine tables, t2 a layout table of a bullet and text
    tables = parse_tables(load("document.html"))
    assert [t.table_index for t in tables] == list(range(9))
    assert tables[3].rows[0].cells[0].value == Dec(300)


@pytest.mark.criterion("C9")
def test_caption_is_the_nearest_block_with_no_table_between(load):
    # oracle: hand-read from fixtures/document.html
    tables = parse_tables(load("document.html"))
    assert tables[0].caption == "(amounts in millions, except per share data)"
    assert tables[1].caption == "Capital expenditures were $4,710 million in 2024."
    assert tables[2].caption == ""
    assert tables[3].caption == "Our membership was made up of the following (in thousands):"
    assert tables[4].caption == "Inventories consist of the following:"
    assert tables[7].caption == "Goodwill by segment:"
    assert tables[8].caption == ""


@pytest.mark.criterion("C9")
def test_scale_notes_reach_the_right_tables(load):
    # oracle: requirement 10, read by hand. t0 standing note, t1 inherits it past prose, t3 its
    # sentence, t4 the standing note again, t5 its own, t6 the standing note again, t7 a later
    # standing note two blocks up, t8 still that one.
    tables = parse_tables(load("document.html"))
    assert [tables[i].scale for i in (0, 1, 3, 4, 5, 6, 7, 8)] == [6, 6, 3, 6, 9, 6, 3, 3]


@pytest.mark.criterion("C9")
def test_no_note_anywhere_means_units():
    # oracle: requirement 4; nothing states a scale
    table_html = "<table><tr><td>Cash</td><td>1,000</td></tr></table>"
    html = f"<html><body><div>Cash</div>{table_html}</body></html>"
    tables = parse_tables(html)
    assert [t.scale for t in tables] == [0]
    assert tables[0].rows[0].cells[0].value == Dec("1000")


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_a_leading_table_only_shifts_the_ordinals(load):
    # oracle: one more table in front changes each ordinal by one and nothing else
    html = load("document.html")
    tables = parse_tables(html)
    assert len(tables) == 9
    shifted = parse_tables(html.replace("<body>", "<body><table><tr><td>x</td></tr></table>"))
    assert shifted[1:] == [t._replace(table_index=t.table_index + 1) for t in tables]


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
def test_the_walk_equals_a_single_parse_with_the_same_context(load):
    # oracle: parse_tables and parse_table share one parser, so the same context gives the same grid
    html = load("document.html")
    table_html = re.search(r'<table id="t1">.*?</table>', html).group(0)
    walked = parse_tables(html)[1]
    alone = parse_table(
        table_html,
        caption="Capital expenditures were $4,710 million in 2024.",
        scale_note="(amounts in millions, except per share data)",
        table_index=1,
    )
    assert walked == alone
    assert alone.scale == 6


@pytest.mark.criterion("C10")
def test_tags_with_false_attributes_are_ignored(load):
    # oracle: fixtures/leak_free.html is the tagged snippet with its tags removed by hand. Read
    # from the print: (169) is -169, 160 is positive, the dash is nil, all in millions.
    tagged = parse_table(load("leak_tagged.html"))
    free = parse_table(load("leak_free.html"))
    assert tagged == free
    assert tagged.scale == 6
    interest, other = tagged.rows
    assert [c.value for c in interest.cells] == [Dec("-169"), Dec("160")]
    assert [c.scale for c in interest.cells] == [6, 6]
    assert other.cells[0].is_nil is True
    assert other.cells[1].value == Dec("7")


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@pytest.mark.parametrize("name", TABLE_FIXTURES)
def test_wrapping_every_number_in_a_tag_changes_nothing(load, wrap_tags, name):
    # oracle: the tag-free twin of the same snippet
    html = load(name)
    table = parse_table(html)
    assert len(table.rows) >= 2
    tagged = wrap_tags(html, "3", 'sign="-"', "eur")
    assert "ix:nonFraction" in tagged
    assert parse_table(tagged) == table


@pytest.mark.criterion("C10")
@given(
    name=st.sampled_from(TABLE_FIXTURES),
    scale=st.sampled_from(["-2", "0", "3", "6", "9"]),
    sign=st.sampled_from(['sign="-"', ""]),
    unit=st.sampled_from(["usd", "eur", "shares", "pure"]),
)
def test_no_tag_attribute_reaches_the_parse(load, wrap_tags, name, scale, sign, unit):
    # oracle: the tag-free twin; only the attributes vary
    html = load(name)
    table = parse_table(html)
    assert len(table.rows) >= 2
    assert parse_table(wrap_tags(html, scale, sign, unit)) == table


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
def test_the_document_walk_ignores_tags_too(load, wrap_tags):
    # oracle: the tag-free twin of the document
    html = load("document.html")
    tables = parse_tables(html)
    assert len(tables) == 9
    assert parse_tables(wrap_tags(html, "9", 'sign="-"', "eur")) == tables


@pytest.mark.criterion("C10")
def test_the_module_reads_no_tagging_or_fact_store(load, tables_source):
    # oracle: plan 5.2, the parser must not read tagging markup, the fact store or xbrl_* fields
    assert len(parse_table(load("leak_free.html")).rows) == 2
    lowered = tables_source.lower()
    assert [p for p in FORBIDDEN_TEXT if re.search(p, lowered)] == []
    imported = set()
    for node in ast.walk(ast.parse(tables_source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert [m for m in imported if m.split(".")[0] in FORBIDDEN_IMPORTS] == []
    project = [m for m in imported if m.split(".")[0] == "src"]
    assert [m for m in project if not m.startswith("src.ingest.html10k")] == []
