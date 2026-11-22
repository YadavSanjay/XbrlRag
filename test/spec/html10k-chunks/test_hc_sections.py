"""Sections for chunking (docs/specs/html10k-chunks.md, requirements 1-5; C6, C7, C8).

Starts are read from the marker each test puts on an element, so each is known by construction.
The two stub-Item-8 shapes are NVDA/ORCL (statements inside Item 15) and QCOM/NFLX (statements
after Item 16 and the Signatures).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.sections import detect_chunk_sections, detect_sections

NOTES = '<div id="notes">Notes to Consolidated Financial Statements</div><p>NOTESBODY</p>'


def _front(kit):
    return (
        '<div id="i1">Item 1. Business</div>' + kit.para("BUSINESS")
        + '<div id="i7">Item 7. Management’s Discussion and Analysis</div>' + kit.para("MDA")
        + '<div id="i8">Item 8. Financial Statements and Supplementary Data</div>'
    )


def _i15(kit):
    return (
        '<div id="i15">Item 15. Exhibits and Financial Statement Schedules</div>'
        + kit.para("EXHIBITS")
    )


def _i16(kit):
    return '<div id="i16">Item 16. Form 10-K Summary</div>' + kit.para("SUMMARY")


def _statements(kit):
    return "<p>BALANCE SHEETS</p>" + kit.grid(["2024"], [("Cash", ["$1"])])


def _inside_15(kit):
    """NVDA and ORCL: a stub Item 8, and the statements and notes inside Item 15."""
    return (
        _front(kit) + kit.para("STUB") + _i15(kit) + _statements(kit) + NOTES + _i16(kit)
        + '<div id="sig">Signatures</div>'
    )


def _after_signatures(kit):
    """QCOM: Item 15 and 16 are short, and the statements and notes follow the Signatures."""
    return (
        _front(kit) + kit.para("STUB") + _i15(kit) + _i16(kit) + '<div id="sig">Signatures</div>'
        + kit.para("SIGNED") + _statements(kit) + NOTES
    )


def _notes_in_8(kit):
    """Most filers: statements and notes are in Item 8, and Item 15 is the exhibit list."""
    return (
        _front(kit) + _statements(kit) + NOTES
        + '<div id="i9">Item 9. Changes in and Disagreements with Accountants</div>'
        + kit.para("NINE") + _i15(kit) + _i16(kit) + '<div id="sig">Signatures</div>'
    )


def _section(doc, key):
    return next(s for s in detect_chunk_sections(doc) if s.key == key)


@pytest.mark.criterion("C6")
def test_statements_inside_item_15(kit):
    # oracle: requirements 2-3, by hand. It runs from its heading to the end of the notes, which is
    # Item 16.
    doc = kit.stripped(_inside_15(kit))
    section = _section(doc, "item_15")
    assert section.char_start == kit.at(doc, 'id="i15"')
    assert section.char_end == kit.at(doc, 'id="i16"')
    assert section.source == "heading"
    assert section.label == "Item 15. Exhibits and Financial Statement Schedules"


@pytest.mark.criterion("C6")
def test_statements_after_item_16_and_the_signatures(kit):
    # oracle: requirement 3, by hand: QCOM's shape. The notes run to the end of the document, and
    # the statements before them are inside.
    doc = kit.stripped(_after_signatures(kit))
    section = _section(doc, "item_15")
    assert section.char_start == kit.at(doc, 'id="i15"')
    assert section.char_end == len(doc)
    assert section.char_start < doc.index(b"BALANCE SHEETS") < section.char_end


@pytest.mark.criterion("C6")
def test_notes_in_item_8_leave_item_15_as_the_exhibit_list(kit):
    # oracle: requirement 3, by hand: `notes` starts before Item 15, so the next boundary ends it.
    doc = kit.stripped(_notes_in_8(kit))
    section = _section(doc, "item_15")
    assert section.char_start == kit.at(doc, 'id="i15"')
    assert section.char_end == kit.at(doc, 'id="i16"')


@pytest.mark.criterion("C6")
def test_an_unnumbered_exhibits_entry_of_a_topic_ordered_filing(kit):
    # oracle: requirement 2, by hand: INTC's and MCD's shape, a ToC with titles and no item numbers.
    toc = (
        '<table><tr><td><a href="#rf">Risk Factors</a></td><td>20</td></tr>'
        '<tr><td><a href="#fs">Consolidated Financial Statements</a></td><td>30</td></tr>'
        '<tr><td><a href="#ex">Exhibits and Financial Statement Schedules</a></td><td>90</td></tr>'
        "</table>"
    )
    body = (
        toc + '<div id="rf">Risk Factors</div>' + kit.para("RISK")
        + '<div id="fs">Consolidated Financial Statements</div>' + kit.para("STATEMENTS")
        + '<div id="ex">Exhibits</div>' + kit.para("EXHIBITS")
    )
    doc = kit.stripped(body)
    section = _section(doc, "item_15")
    assert section.char_start == kit.at(doc, 'id="ex"')
    assert section.char_end == len(doc)
    assert section.source == "toc"
    assert section.label == "Exhibits and Financial Statement Schedules"


@pytest.mark.criterion("C6")
def test_a_numbered_toc_entry_is_preferred(kit):
    # oracle: requirement 2, by hand. The ToC entry names item 15, whatever the heading says.
    toc = (
        '<table><tr><td><a href="#x15">Item 15.</a></td><td>Exhibits</td><td>90</td></tr></table>'
    )
    doc = kit.stripped(toc + '<div id="x15">Item 15. Exhibits</div>' + kit.para("EXHIBITS"))
    section = _section(doc, "item_15")
    assert section.char_start == kit.at(doc, 'id="x15"')
    assert section.source == "toc"
    assert section.label == "Item 15. Exhibits"


@pytest.mark.criterion("C6")
def test_no_item_15_adds_nothing(kit):
    # oracle: requirement 1, by hand.
    doc = kit.stripped(_front(kit) + kit.para("STUB"))
    assert detect_chunk_sections(doc) == detect_sections(doc)
    assert "item_8" in [s.key for s in detect_chunk_sections(doc)]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(shape=st.sampled_from([_inside_15, _after_signatures, _notes_in_8]))
def test_every_other_section_is_what_detect_sections_gives(kit, shape):
    # oracle: requirement 1: `detect_sections` is the reference for everything but the new key.
    doc = kit.stripped(shape(kit))
    found = detect_chunk_sections(doc)
    assert [s for s in found if s.key != "item_15"] == detect_sections(doc)
    assert [s.key for s in found].count("item_15") == 1
    assert found == sorted(found, key=lambda s: s.char_start)


def _with(chunks, token):
    return next(c for c in chunks if token in c.text)


@pytest.mark.criterion("C7")
def test_tags_with_statements_inside_item_15(kit):
    # oracle: requirement 4, by hand.
    chunks = kit.chunks(_inside_15(kit))
    table = _with(chunks, "| Cash | $1 |")
    assert table.section_key == "item_15"
    assert table.section_keys == ("item_15",)
    assert table.section_label == "Item 15. Exhibits and Financial Statement Schedules"
    assert table.text.split("\n")[0] == (
        "Section: Item 15. Exhibits and Financial Statement Schedules"
    )
    notes = _with(chunks, "NOTESBODY")
    assert notes.section_key == "notes"
    assert notes.section_keys == ("item_15", "notes")
    assert notes.section_label == "Notes to Consolidated Financial Statements"


@pytest.mark.criterion("C7")
def test_tags_with_statements_after_the_signatures(kit):
    # oracle: requirement 4, by hand: QCOM's statements keep a section.
    chunks = kit.chunks(_after_signatures(kit))
    assert _with(chunks, "| Cash | $1 |").section_keys == ("item_15",)
    assert _with(chunks, "NOTESBODY").section_keys == ("item_15", "notes")


@pytest.mark.criterion("C7")
def test_tags_with_notes_in_item_8(kit):
    # oracle: requirement 4, by hand.
    chunks = kit.chunks(_notes_in_8(kit))
    table = _with(chunks, "| Cash | $1 |")
    assert (table.section_key, table.section_keys) == ("item_8", ("item_8",))
    notes = _with(chunks, "NOTESBODY")
    assert (notes.section_key, notes.section_keys) == ("notes", ("item_8", "notes"))


@pytest.mark.criterion("C7")
def test_a_heading_printed_as_a_table_row_starts_its_section(kit):
    # oracle: requirement 4, by hand: AVGO's shape. The section starts at the <tr>, in the table.
    title = "MANAGEMENT’S DISCUSSION AND ANALYSIS"
    heading = f"<table><tr><td>ITEM 7.</td><td>{title}</td></tr></table>"
    body = kit.para("OPENING") + heading + kit.para("MDATEXT")
    chunks = kit.chunks(body)
    assert [c.section_key for c in chunks] == ["no_section", "item_7"]
    assert chunks[1].text.startswith("ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS MDATEXT")


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(shape=st.sampled_from([_inside_15, _after_signatures]), rows=st.integers(1, 5))
def test_statements_and_notes_of_a_stub_filer_always_have_a_section(kit, shape, rows):
    # oracle: requirement 4: the point of `item_15`, whatever the statements' size.
    grid = kit.grid(["2024"], [("Cash", ["$1"])] + [(f"R{i}", [str(i)]) for i in range(rows)])
    chunks = kit.chunks(shape(kit).replace(kit.grid(["2024"], [("Cash", ["$1"])]), grid))
    assert _with(chunks, "| Cash | $1 |").section_key != "no_section"
    assert _with(chunks, "NOTESBODY").section_key != "no_section"


@pytest.mark.criterion("C8")
def test_a_filing_with_no_sections_is_indexed_with_the_marker(kit):
    # oracle: requirement 5, by hand. Nothing is detected, and nothing is dropped.
    table = kit.grid(["2024"], [("Cash", ["$1"])])
    chunks = kit.chunks(f"<p>TEXT</p>{table}")
    assert [c.kind for c in chunks] == ["prose", "table"]
    for chunk in chunks:
        assert chunk.section_key == "no_section"
        assert chunk.section_keys == ("no_section",)
        assert chunk.section_label == ""
    assert chunks[1].text.split("\n")[0] == "Section: none"


@pytest.mark.criterion("C8")
def test_a_topic_ordered_filing_keeps_what_falls_outside_its_sections(kit):
    # oracle: requirement 5, by hand: INTC's shape. Overview text and a table come before any key.
    table = kit.grid(["2024"], [("Cash", ["$1"])])
    body = (
        kit.para("OVERVIEW") + table
        + "<div>Item 1A. Risk Factors</div>" + kit.para("RISK")
        + "<div>Item 2. Properties</div>" + kit.para("PLANTS")
    )
    chunks = kit.chunks(body)
    assert _with(chunks, "OVERVIEW").section_key == "no_section"
    assert _with(chunks, "| Cash | $1 |").section_key == "no_section"
    assert _with(chunks, "RISK").section_key == "item_1a"
    assert _with(chunks, "PLANTS").section_key == "no_section"


@pytest.mark.criterion("C8")
@given(subset=st.lists(st.sampled_from(["1", "1A", "7", "8"]), unique=True))
def test_any_subset_of_sections_loses_no_token(kit, subset):
    # oracle: requirement 5: whichever sections exist, each token is indexed once.
    titles = {
        "1": "Business",
        "1A": "Risk Factors",
        "7": "Management’s Discussion and Analysis",
        "8": "Financial Statements and Supplementary Data",
    }
    tokens = ["TOKENONE", "TOKENTWO", "TOKENTHREE", "TOKENFOUR", "TOKENFIVE"]
    parts = [kit.para(tokens[0])]
    for token, item in zip(tokens[1:], ["1", "1A", "7", "8"], strict=True):
        if item in subset:
            parts.append(kit.heading(item, titles[item]))
        parts.append(kit.para(token))
    chunks = kit.chunks("".join(parts))
    for token in tokens:
        assert sum(token in c.text for c in chunks) == 1
