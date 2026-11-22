"""Unit tests for what the html10k-chunks spec tests leave to the implementation.

The locked spec tests live in test/spec/html10k-chunks; these cover nested tables, the helper in
tables.py that carries what a table prints, and the rules added after review: no printed text is
lost, text tables and indexes are prose, a running line is no title, and a long caption keeps its
scale statement. Documents are synthetic.
"""

from __future__ import annotations

from src.chunking.html10k_chunking import chunk_filing
from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.html10k.tables import note_statement, parse_printed_tables, parse_tables

ACCESSION = "0000000000-26-000001"
GRID = "<table><tr><td></td><td>2024</td></tr><tr><td>Land</td><td>1,234</td></tr></table>"


def _doc(body: str) -> bytes:
    html = f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'
    return ix_strip_html(html.encode())


def _chunks(body: str):
    return chunk_filing(_doc(body), ACCESSION)


def test_printed_tables_hold_what_parse_tables_returns():
    document = _doc(f"<p>Title</p><p>(In millions)</p>{GRID}<p>Other</p>{GRID}").decode()
    printed = parse_printed_tables(document)
    assert [p.table for p in printed] == parse_tables(document)
    assert [(p.title, p.note) for p in printed] == [
        ("Title", "(In millions)"),
        ("Other", "(In millions)"),
    ]


def test_the_title_is_never_read_across_a_table():
    document = _doc(f"<p>Title</p>{GRID}<p>(In millions)</p>{GRID}").decode()
    assert parse_printed_tables(document)[1].title == ""


def test_a_footnote_block_is_not_a_title():
    document = _doc(f"<p>Title</p><p>(a) In thousands.</p><p>(In millions)</p>{GRID}").decode()
    assert parse_printed_tables(document)[0].title == "Title"


def test_a_continuation_shows_the_note_of_the_table_before():
    stubbed = GRID.replace("<td></td>", "<td>(In thousands)</td>", 1)
    document = _doc(f"<p>Standing</p><p>(In millions)</p>{stubbed}{GRID}").decode()
    first, second = parse_printed_tables(document)
    assert (first.stub, first.note) == ("(In thousands)", "(In millions)")
    assert (second.title, second.note) == ("", "(In thousands)")


def test_a_nested_table_is_its_own_block_and_its_text_is_not_counted_twice():
    body = f"<table><tr><td>Outer words</td><td>{GRID}</td></tr></table>"
    chunks = _chunks(body)
    assert [(c.kind, c.table_index) for c in chunks] == [("prose", None), ("table", 1)]
    assert chunks[0].text == "Outer words"
    assert "| Land | 1,234 |" in chunks[1].text


# A list printed on a grid of its own: the numbers and page references fix two value positions,
# and the titles between them have none. Layout as in an exhibit list or a statement index.
UNPLACED_TEXT = (
    "<table>"
    '<tr><td colspan="2">No.</td><td></td><td colspan="2">Filed</td></tr>'
    "<tr><td></td><td>1.5</td><td>First agreement</td><td></td><td>10</td></tr>"
    "<tr><td></td><td>2.5</td><td>Second agreement</td><td></td><td>20</td></tr>"
    "<tr><td></td><td>3.5</td><td>Third agreement</td><td></td><td>35</td></tr>"
    "</table>"
)


def test_text_with_no_value_position_gets_a_printed_column():
    table = next(c for c in _chunks(UNPLACED_TEXT) if c.kind == "table")
    assert "| 2.5 | Second agreement | 20 |" in table.text.replace("|  |", "|")


def test_a_second_text_in_a_filled_column_stays_with_it():
    table = (
        "<table><tr><td></td><td>2024</td></tr>"
        "<tr><td>Land</td><td>1,234</td></tr>"
        "<tr><td>Note</td><td>see below</td></tr>"
        "</table>"
    )
    assert "| Note | see below |" in _chunks(table)[0].text


def test_a_small_negative_amount_with_its_symbol_is_a_number():
    # The parentheses of "$(23)" are a sign: a dollar sign alone has nothing to footnote.
    table = "<table><tr><td></td><td>2024</td></tr><tr><td>Net loss</td><td>$(23)</td></tr></table>"
    cell = parse_tables(_doc(table).decode())[0].rows[0].cells[0]
    assert (cell.text, str(cell.value)) == ("$(23)", "-23")
    assert "| Net loss | $(23) |" in _chunks(table)[0].text


def test_a_parsed_table_is_unchanged_by_the_printed_columns():
    document = _doc(UNPLACED_TEXT).decode()
    assert parse_printed_tables(document)[0].table == parse_tables(document)[0]


def test_a_first_row_of_body_text_is_no_header():
    # Three shapes the parser reads a header in: a state and tax number on a cover page, the
    # continuation of a list, and a signature block.
    cover = (
        "<table><tr><td>Ohio</td><td>12-3456789</td></tr>"
        "<tr><td>(State)</td><td>(Id)</td></tr></table>"
    )
    listed = (
        "<table><tr><td>4.6</td><td>Certificate of the Company, dated the first day.</td></tr>"
        "<tr><td>4.7</td><td>Another certificate.</td></tr></table>"
    )
    signed = (
        "<table><tr><td>By:</td><td>/s/ Ann Lee</td></tr>"
        "<tr><td></td><td>Ann Lee</td></tr></table>"
    )
    for table in (cover, listed, signed):
        assert [c.kind for c in _chunks(table)] == ["prose"]


def test_a_real_header_over_text_stays_a_table():
    table = (
        "<table><tr><td>Term</td><td>Definition</td></tr>"
        "<tr><td>AI</td><td>Intelligence</td></tr></table>"
    )
    assert [c.kind for c in _chunks(table)] == ["table"]


INDEX = (
    "<table><tr><td></td><td>Page</td></tr>"
    "<tr><td>Balance sheets</td><td>41</td></tr>"
    "<tr><td>Income statements</td><td>42</td></tr>"
    "<tr><td>Notes</td><td>47</td></tr></table>"
)


def test_an_index_is_prose():
    chunks = _chunks(INDEX)
    assert [c.kind for c in chunks] == ["prose"]
    assert chunks[0].text == "Page Balance sheets 41 Income statements 42 Notes 47"


def test_rising_numbers_under_a_period_are_data():
    assert [c.kind for c in _chunks(INDEX.replace("Page", "2024"))] == ["table"]


def test_falling_numbers_or_money_are_no_index():
    falling = INDEX.replace(">41<", ">50<")
    money = INDEX.replace(">41<", ">$41<")
    assert [c.kind for c in _chunks(falling)] == ["table"]
    assert [c.kind for c in _chunks(money)] == ["table"]


def _running(line: str, copies: int) -> str:
    return "".join(f"<p>{line}</p><p>Page text number {i}, long enough.</p>" for i in range(copies))


def test_a_running_line_in_another_case_is_no_title():
    # The running line is printed five times in one case, and once in capitals over the table.
    body = _running("Table of Contents", 5) + f"<p>Real title</p><p>TABLE OF CONTENTS</p>{GRID}"
    assert parse_printed_tables(_doc(body).decode())[0].title == "Real title"


def test_no_title_when_only_a_running_line_is_above():
    body = f"{_running('Acme Corp', 5)}{GRID}<p>ACME CORP</p>{GRID}"
    assert parse_printed_tables(_doc(body).decode())[1].title == ""


def test_a_page_footer_with_its_number_is_no_title():
    line = "Acme Corp | 2025 Form 10-K"
    footers = "".join(f"<p>{line} | {n}</p><p>Text {n} here.</p>" for n in range(5))
    body = f"{footers}<p>Real title</p><p>Acme Corp | 2025 Form 10-K | 56</p>{GRID}"
    assert parse_printed_tables(_doc(body).decode())[0].title == "Real title"


def test_numbered_headings_are_not_a_running_line():
    notes = "".join(f"<p>Note {n}</p><p>Words about note {n} go here.</p>" for n in range(1, 7))
    body = f"{notes}<p>Note 7</p>{GRID}"
    assert parse_printed_tables(_doc(body).decode())[0].title == "Note 7"


def test_a_heading_printed_a_few_times_stays_a_title():
    # Four copies in all, two in this case: below both the parser's and the title's count.
    repeated = _running("Research", 1) + _running("RESEARCH", 1) + _running("research", 1)
    body = repeated + f"<p>Research</p>{GRID}"
    assert parse_printed_tables(_doc(body).decode())[0].title == "Research"


def test_a_long_caption_keeps_a_scale_statement_it_would_cut():
    opening = "The policies above apply. Amounts are presented in millions. "
    caption = opening + "word " * 100 + "and the table follows."
    table = next(c for c in _chunks(f"<p>{caption}</p>{GRID}") if c.kind == "table")
    assert note_statement(caption) in table.text.split("\n")[2]
    assert table.text.split("\n")[2].startswith("Note: ")


def test_a_long_note_keeps_its_statement():
    caption = "(In millions, " + "word " * 100 + "and more)"
    table = next(c for c in _chunks(f"<p>{caption}</p>{GRID}") if c.kind == "table")
    note = next(x for x in table.text.split("\n") if x.startswith("Note: "))
    assert "In millions" in note
    assert "…" in note
