"""C1-C3: where a note stands from. Spec: docs/specs/html10k-tables-scale-2.md."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_printed_tables, parse_tables

WORDS = [("thousands", 3), ("millions", 6), ("billions", 9)]
HEADING = "Prose that ends any run of tables."
RUNNING = "(Dollars in tables in millions, except per share amounts)"


# ---- C1: a block that is one parenthesis stating a scale -----------------------------------------


@pytest.mark.criterion("C1")
def test_a_running_parenthesis_stands_for_the_tables_below_it(kit):
    # oracle: hand-read from fixtures/page_lines.html. The second table follows a sentence and a
    # heading, which end any run, and only the note printed above the first can reach it. Every
    # table of the document is in millions; the per-share row keeps its cents.
    tables = parse_tables(kit.load("page_lines.html"))
    assert [t.scale for t in tables] == [6, 6, 6, 6, 6, 6]
    assert kit.scales(kit.row(tables[1], "Land")) == [6, 6]
    assert kit.scales(kit.row(tables[0], "Earnings per share")) == [0, 0]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("note", "scale"),
    [
        ("(Dollars in tables in millions)", 6),  # HON
        ("(Dollars in tables and graphs in millions, except per share amounts)", 6),  # HON
        ("(Dollars and shares in millions, except per-share data)", 6),  # LLY
        ("(Dollars and shares in millions)", 6),  # LLY
        ("(Amounts in the tables below in thousands)", 3),  # the form; no filer here prints it
        ("(Amounts in the tables in billions of dollars)", 9),  # the form again
    ],
)
def test_every_wording_of_the_form_stands(kit, plain, note, scale):
    # oracle: the note's own word, by hand. A heading comes between it and the table, so the note
    # reaches the table only by standing.
    assert parse_tables(kit.doc(note, HEADING, plain()))[0].scale == scale


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    "block",
    [
        "(Dollars in billions. Percentages in chart are as a percent to total sales)",  # JNJ
        "(Shares in millions)",
        "(Number of shares in thousands)",
        "Results for the year (Dollars in tables in millions)",
        "(Dollars in tables in millions) and other amounts as described",
    ],
)
def test_what_stands_for_nothing_beside_a_note_that_does(kit, plain, block):
    # oracle: requirement 1, by hand: a second sentence, shares alone, or text around the
    # parenthesis. The same document with the form instead of the block is the control.
    control = parse_tables(kit.doc(RUNNING, HEADING, plain()))
    assert control[0].scale == 6
    assert parse_tables(kit.doc(block, HEADING, plain()))[0].scale == 0


@pytest.mark.criterion("C1")
def test_a_later_note_replaces_it_and_a_repeat_stays_a_note(kit, plain):
    # oracle: requirement 1, by hand. Printed six times it is still a note, not a running line,
    # and the note of thousands then stands in its place.
    pages = [part for i in range(6) for part in (RUNNING, f"{HEADING} {i}", plain())]
    tables = parse_tables(kit.doc(*pages, "(Dollars in tables in thousands)", HEADING, plain()))
    assert [t.scale for t in tables] == [6, 6, 6, 6, 6, 6, 3]


@pytest.mark.criterion("C1")
@given(
    word=st.sampled_from(WORDS),
    lead=st.sampled_from(["Dollars in tables", "Dollars and graphs in tables", "Tabular data"]),
    tail=st.sampled_from(["", ", except per share amounts", ", unless otherwise noted"]),
    prose=st.integers(min_value=1, max_value=4),
    copies=st.integers(min_value=1, max_value=4),
)
def test_prose_and_copies_change_nothing(kit, plain, word, lead, tail, prose, copies):
    # oracle: closed form; the stated word's power of ten, whatever lies between it and the tables
    name, exponent = word
    note = f"({lead} in {name}{tail})"
    parts = [
        text
        for copy in range(copies)
        for text in (note, *[f"{HEADING} {copy}.{i}" for i in range(prose)])
    ]
    tables = parse_tables(kit.doc(*parts, plain()))
    assert tables[0].scale == exponent


# ---- C2: the note directly above a table is that table's note ------------------------------------


@pytest.mark.criterion("C2")
def test_such_a_block_is_the_note_and_the_title_is_the_block_before(kit, plain):
    # oracle: requirement 2, by hand; the wording is LLY's.
    note = "(Dollars and shares in millions, except per-share data)"
    printed = parse_printed_tables(kit.doc("Earnings per share", note, plain()))[0]
    assert printed.note == note
    assert printed.title == "Earnings per share"


@pytest.mark.criterion("C2")
def test_a_note_printed_on_every_page_still_reaches_the_note_line(kit):
    # oracle: hand-read from fixtures/page_lines.html. The table on the fourth page has the note
    # as the only block above it that is no running line; the note prints six times, so it is no
    # title, and the chunk still says it.
    chunks = kit.chunks(kit.load("page_lines.html"))
    table = next(c for c in chunks if "Accrued interest" in c.text)
    assert table.text.split("\n")[:2] == ["Section: none", f"Note: {RUNNING}"]


@pytest.mark.metamorphic
@pytest.mark.criterion("C2")
def test_every_part_of_a_split_table_carries_the_note(kit):
    # oracle: requirement 2; a table split by size prints its prelude on every part, so the note
    # is on each. The size is small enough to split on any row.
    rows = [(f"Line item number {i}", [f"{100 + i}"]) for i in range(12)]
    document = kit.doc(RUNNING, "NOTE 9. ASSETS", kit.mini(["2024"], rows))
    parts = [c for c in kit.chunks(document, table_max_chars=160) if c.kind == "table"]
    assert len(parts) > 1
    assert all(f"Note: {RUNNING}" in part.text for part in parts)


# ---- C3: a table of no numbers that states a note ------------------------------------------------


@pytest.mark.criterion("C3")
def test_a_heading_table_stands_for_the_tables_after_it(kit):
    # oracle: hand-read from fixtures/heading_tables.html. Tables 0 and 3 are the headings; tables
    # 1, 2 and 4 are under a note they hear only through a heading and a sentence.
    tables = parse_tables(kit.load("heading_tables.html"))
    assert [t.scale for t in tables[1:3]] == [6, 6]
    assert tables[4].scale == 6
    assert kit.scales(kit.row(tables[1], "Interest paid")) == [6, 6]
    assert kit.scales(kit.row(tables[1], "Earnings per share")) == [0, 0]


@pytest.mark.criterion("C3")
def test_it_reaches_the_note_line_and_stays_a_table_of_its_own(kit):
    # oracle: hand-read from fixtures/heading_tables.html; five tables, the heading the first.
    source = kit.load("heading_tables.html")
    printed = parse_printed_tables(source)
    assert [p.table.table_index for p in printed] == [0, 1, 2, 3, 4]
    assert printed[2].note == "Millions of dollars, except per-share amounts"
    chunk = next(c for c in kit.chunks(source) if "Income taxes paid" in c.text)
    assert "Note: Millions of dollars, except per-share amounts" in chunk.text.split("\n")


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("heading", "scale"),
    [
        ("<tr><td>(millions of dollars)</td></tr>", 6),  # XOM, one cell
        ("<tr><td>Millions of dollars</td><td>2024</td><td>2023</td></tr>", 6),  # CVX, only years
        ("<tr><td>Results</td></tr><tr><td>(In thousands)</td></tr>", 3),
        ("<tr><td>(In millions)</td></tr><tr><td>In thousands of dollars</td></tr>", 3),
    ],
)
def test_each_shape_of_table_stands(kit, plain, heading, scale):
    # oracle: requirement 3, by hand: one cell or many, years are not numbers, the last note wins
    assert parse_tables(kit.doc(f"<table>{heading}</table>", HEADING, plain()))[1].scale == scale


@pytest.mark.criterion("C3")
def test_a_table_of_numbers_stands_for_nothing_and_a_tables_own_note_wins(kit, plain):
    # oracle: requirements 3 and 4 of the parent spec, by hand. Under the same heading, the middle
    # table states thousands for itself; after a table of numbers that states billions, the note
    # of the heading is still the one in force, because a table of numbers does not stand.
    heading = "<table><tr><td>Millions of dollars</td></tr></table>"
    own = kit.mini(["2024"], [("Land", ["1,234"])], stub="(In thousands)")
    numbers = kit.mini(["2024"], [("Land", ["12"])], stub="(In billions)")
    document = kit.doc(heading, "One.", plain(), "Two.", own, "Three.", numbers, "Four.", plain())
    assert [t.scale for t in parse_tables(document)][1:] == [6, 3, 9, 6]


@pytest.mark.criterion("C3")
@given(
    word=st.sampled_from(WORDS),
    form=st.sampled_from(["{} of dollars", "({} of dollars)", "(In {})", "Dollars in {}"]),
    title=st.sampled_from(["", "Notes to the Financial Statements"]),
    gaps=st.integers(min_value=1, max_value=3),
)
def test_gaps_change_nothing_below_a_heading_table(kit, plain, word, form, title, gaps):
    # oracle: closed form; the stated word's power of ten across any number of blocks
    name, exponent = word
    text = form.format(name.capitalize() if form[0] == "{" else name)
    first = f"<tr><td>{title}</td></tr>" if title else ""
    heading = f"<table>{first}<tr><td>{text}</td></tr></table>"
    blocks = [f"{HEADING} {i}" for i in range(gaps)]
    assert parse_tables(kit.doc(heading, *blocks, plain()))[1].scale == exponent
