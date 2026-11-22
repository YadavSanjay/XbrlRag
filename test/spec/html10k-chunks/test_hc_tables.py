"""Table chunk text (docs/specs/html10k-chunks.md, requirements 9-11; C2, C3, C4).

Expected text is written out by hand from the table in each test, line by line.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

ITEM_8 = "<div>Item 8. Financial Statements and Supplementary Data</div>"
SECTION_8 = "Section: Item 8. Financial Statements and Supplementary Data"

STACKED = """
<table>
<tr><td></td><td colspan="2">Year Ended December 31,</td></tr>
<tr><td></td><td>2024</td><td>2023</td></tr>
<tr><td>Assets</td><td></td><td></td></tr>
<tr><td>Revenue</td><td>$1,234</td><td>$1,000</td></tr>
<tr><td>Interest expense</td><td>(169)</td><td>—</td></tr>
<tr><td>Margin</td><td>10.92%</td><td>N/A</td></tr>
</table>
"""


def _table_chunk(kit, body, **kwargs):
    return next(c for c in kit.chunks(body, **kwargs) if c.kind == "table")


def _row_lines(text):
    return [line for line in text.split("\n")[3:] if line.startswith("| ")]


@pytest.mark.criterion("C2")
def test_table_text_with_stacked_headers_and_a_group_row(kit):
    # oracle: requirement 9, written by hand from STACKED.
    chunk = _table_chunk(kit, f"{ITEM_8}<p>Selected Results</p>{STACKED}")
    assert chunk.text.split("\n") == [
        SECTION_8,
        "Table: Selected Results",
        "|  | Year Ended December 31, 2024 | Year Ended December 31, 2023 |",
        "| --- | --- | --- |",
        "| Assets |  |  |",
        "| Revenue | $1,234 | $1,000 |",
        "| Interest expense | (169) | — |",
        "| Margin | 10.92% | N/A |",
    ]
    assert chunk.kind == "table"
    assert chunk.table_index == 0
    assert (chunk.part, chunk.parts) == (0, 1)
    assert chunk.section_key == "item_8"


@pytest.mark.criterion("C2")
def test_the_label_header_is_the_printed_stub_text(kit):
    # oracle: requirement 9, by hand: the stub cell is printed in the label column's header.
    table = kit.grid(["2024"], [("Revenue", ["$5"])], stub="Segment")
    assert _table_chunk(kit, table).text.split("\n") == [
        "Section: none",
        "| Segment | 2024 |",
        "| --- | --- |",
        "| Revenue | $5 |",
    ]


@pytest.mark.criterion("C2")
def test_footnote_markers_follow_in_brackets(kit):
    # oracle: requirement 9, by hand. A superscript beside a number or a label goes in brackets;
    # parentheses would read as a negative.
    table = (
        "<table><tr><td></td><td>2024</td></tr>"
        "<tr><td>Third party<sup>(a)</sup></td><td>7,392<sup>2</sup></td></tr></table>"
    )
    assert _table_chunk(kit, table).text.split("\n")[-1] == "| Third party [a] | 7,392 [2] |"


@pytest.mark.criterion("C2")
def test_a_pipe_in_text_is_escaped(kit):
    # oracle: requirement 9, by hand. An unescaped | would add a column.
    table = kit.grid(["A | B"], [("Lease | owned", ["$5"])])
    lines = _table_chunk(kit, table).text.split("\n")
    assert lines[1] == "|  | A \\| B |"
    assert lines[3] == "| Lease \\| owned | $5 |"


@pytest.mark.criterion("C2")
@given(
    rows=st.lists(
        st.tuples(
            st.from_regex(r"[A-Z][a-z]{3,9}", fullmatch=True),
            st.integers(1, 999_999),
            st.integers(1, 999),
        ),
        min_size=1,
        max_size=8,
        unique_by=lambda row: row[0],
    )
)
def test_each_row_is_one_line_with_its_numbers_as_printed(kit, rows):
    # oracle: requirement 9; the printed strings are built here, and are never re-derived.
    printed = [(label, f"${a:,}", f"({b})") for label, a, b in rows]
    table = kit.grid(["2024", "2023"], [(label, [a, b]) for label, a, b in printed])
    lines = _table_chunk(kit, table).text.split("\n")
    assert lines[3:] == [f"| {label} | {a} | {b} |" for label, a, b in printed]


NO_COMPUTED = re.compile(r"1,?234,?000")


@pytest.mark.criterion("C3")
def test_a_note_in_the_stub_cell_is_printed_there_and_nowhere_else(kit):
    # oracle: requirement 10, by hand. The table's own note is shown where the filing prints it, and
    # the number stays 1,234, never 1,234,000,000.
    table = kit.grid(["2024"], [("Revenue", ["$1,234"])], stub="(In millions)")
    chunk = _table_chunk(kit, table)
    assert chunk.text.split("\n") == [
        "Section: none",
        "| (In millions) | 2024 |",
        "| --- | --- |",
        "| Revenue | $1,234 |",
    ]
    assert not NO_COMPUTED.search(chunk.text)


@pytest.mark.criterion("C3")
def test_a_caption_that_is_only_a_note_goes_on_the_note_line(kit):
    # oracle: requirement 10, by hand: NVDA's shape. The title is the block before the note.
    table = kit.grid(["2024"], [("Revenue", ["$1,234"])])
    title = "<p>CONSOLIDATED STATEMENTS OF INCOME</p>"
    body = f"{title}<p>(In millions, except per share data)</p>{table}"
    assert _table_chunk(kit, body).text.split("\n") == [
        "Section: none",
        "Table: CONSOLIDATED STATEMENTS OF INCOME",
        "Note: (In millions, except per share data)",
        "|  | 2024 |",
        "| --- | --- |",
        "| Revenue | $1,234 |",
    ]


@pytest.mark.criterion("C3")
def test_a_sentence_caption_stating_the_scale_needs_no_note_line(kit):
    # oracle: requirement 10, by hand. The standing note is stale: the caption states the scale.
    standing = "<p>(Dollars in millions)</p><p>Other text here.</p>"
    table = kit.grid(["2024"], [("Land", ["1,234"])])
    body = f"{standing}<p>Inventories were as follows (in thousands):</p>{table}"
    assert _table_chunk(kit, body).text.split("\n")[:3] == [
        "Section: none",
        "Table: Inventories were as follows (in thousands):",
        "|  | 2024 |",
    ]


@pytest.mark.criterion("C3")
def test_a_standing_note_is_printed_on_the_note_line(kit):
    # oracle: requirement 10, by hand. The note was printed at the head of the notes, once.
    table = kit.grid(["2024"], [("Land", ["1,234"])])
    body = f"<p>(Dollars in millions)</p><p>Long-lived assets</p>{table}"
    assert _table_chunk(kit, body).text.split("\n")[:3] == [
        "Section: none",
        "Table: Long-lived assets",
        "Note: (Dollars in millions)",
    ]


@pytest.mark.criterion("C3")
def test_a_stub_note_silences_a_stale_standing_note(kit):
    # oracle: requirement 10, by hand. Two scales in one chunk would mislead, and the stub wins.
    table = kit.grid(["2024"], [("Land", ["1,234"])], stub="(In thousands)")
    body = f"<p>(Dollars in millions)</p><p>Long-lived assets</p>{table}"
    assert _table_chunk(kit, body).text.split("\n") == [
        "Section: none",
        "Table: Long-lived assets",
        "| (In thousands) | 2024 |",
        "| --- | --- |",
        "| Land | 1,234 |",
    ]


@pytest.mark.criterion("C3")
def test_no_scale_anywhere_gives_no_note_line(kit):
    # oracle: requirement 10, by hand.
    table = kit.grid(["2024"], [("Stores", ["1,234"])])
    lines = _table_chunk(kit, f"<p>Our stores</p>{table}").text.split("\n")
    assert not any(line.startswith("Note:") for line in lines)


@pytest.mark.criterion("C3")
def test_a_long_caption_keeps_its_end(kit):
    # oracle: requirement 10, by hand. The caption is 100 five-character words, then the sentence
    # that introduces the table; the kept text starts at a whole word.
    tail = "The table below shows land (in thousands):"
    caption = "word " * 100 + tail
    table = kit.grid(["2024"], [("Land", ["1,234"])])
    line = _table_chunk(kit, f"<p>{caption}</p>{table}").text.split("\n")[1]
    kept = line.removeprefix("Table: ")
    assert kept.startswith("…word ")
    assert kept.endswith(tail)
    assert len(kept) <= 301  # 300 characters and the mark
    assert len(kept) >= 296  # at most the four-letter word and its space go


@pytest.mark.criterion("C3")
def test_no_module_of_the_chunker_reads_a_computed_value(kit, source):
    # oracle: requirement 10: these three names are the parser's computed numbers.
    table = kit.grid(["2024"], [("Revenue", ["$1,234"])], stub="(In millions)")
    assert "| Revenue | $1,234 |" in _table_chunk(kit, table).text
    text = source("src/chunking/html10k_chunking.py")
    assert "scaled_value" not in text
    assert ".period" not in text
    assert "Decimal" not in text


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(word=st.sampled_from(["thousands", "millions", "billions"]))
def test_a_different_scale_word_changes_no_row_line(kit, word):
    # oracle: requirement 10: the row lines are the print, whatever the parser reads the note as.
    table = kit.grid(["2024", "2023"], [("Revenue", ["$1,234", "(56)"]), ("Tax", ["—", "7.5"])])
    base = _table_chunk(kit, f"<p>Title</p><p>(In hundreds)</p>{table}")
    other = _table_chunk(kit, f"<p>Title</p><p>(In {word})</p>{table}")
    assert _row_lines(other.text)[-2:] == ["| Revenue | $1,234 | (56) |", "| Tax | — | 7.5 |"]
    assert _row_lines(base.text)[-2:] == _row_lines(other.text)[-2:]
    assert f"Note: (In {word})" in other.text


GROUPED = [
    ("Assets:", ["", ""]),
    ("Cash", ["1", "2"]),
    ("Land", ["3", "4"]),
    ("Liabilities:", ["", ""]),
    ("Debt", ["5", "6"]),
    ("Leases", ["7", "8"]),
]
PRELUDE = ["Section: none", "Table: Position", "|  | 2024 | 2023 |", "| --- | --- | --- |"]
ASSETS = ["| Assets: |  |  |", "| Cash | 1 | 2 |", "| Land | 3 | 4 |"]
LIABILITIES = ["| Liabilities: |  |  |", "| Debt | 5 | 6 |", "| Leases | 7 | 8 |"]


def _grouped_body(kit, rows=GROUPED):
    return f"<p>Position</p>{kit.grid(['2024', '2023'], rows)}"


def _size(lines):
    return len("\n".join(lines))


@pytest.mark.criterion("C4")
def test_a_table_that_fits_is_one_chunk(kit):
    # oracle: requirement 11, by hand: the limit is exactly the full text's length.
    limit = _size(PRELUDE + ASSETS + LIABILITIES)
    chunks = [c for c in kit.chunks(_grouped_body(kit), table_max_chars=limit) if c.kind == "table"]
    assert [c.text.split("\n") for c in chunks] == [PRELUDE + ASSETS + LIABILITIES]
    assert [(c.part, c.parts) for c in chunks] == [(0, 1)]


@pytest.mark.criterion("C4")
def test_one_character_less_splits_at_the_row_group(kit):
    # oracle: requirement 11, by hand. Each part repeats the prelude and both header rows.
    limit = _size(PRELUDE + ASSETS + LIABILITIES) - 1
    chunks = [c for c in kit.chunks(_grouped_body(kit), table_max_chars=limit) if c.kind == "table"]
    assert [c.text.split("\n") for c in chunks] == [PRELUDE + ASSETS, PRELUDE + LIABILITIES]
    assert [(c.part, c.parts) for c in chunks] == [(0, 2), (1, 2)]
    assert chunks[0].table_index == chunks[1].table_index == 0


@pytest.mark.criterion("C4")
def test_a_group_too_large_splits_between_rows_and_repeats_its_group_row(kit):
    # oracle: requirement 11, by hand. Room for the group row and two rows, of four.
    rows = [("Assets:", ["", ""])] + [(f"R{i}", [str(i), str(i)]) for i in range(1, 5)]
    head, lines = "| Assets: |  |  |", [f"| R{i} | {i} | {i} |" for i in range(1, 5)]
    limit = _size(PRELUDE + [head] + lines[:2])
    chunks = [
        c for c in kit.chunks(_grouped_body(kit, rows), table_max_chars=limit) if c.kind == "table"
    ]
    assert [c.text.split("\n") for c in chunks] == [
        PRELUDE + [head] + lines[:2],
        PRELUDE + [head] + lines[2:],
    ]


@pytest.mark.criterion("C4")
def test_consecutive_group_rows_stay_together(kit):
    # oracle: requirement 11, by hand. `Assets:` and `Current:` open one group, so the break comes
    # before `Assets:`. The limit would also hold Revenue and Assets, the wrong break.
    rows = [
        ("Revenue", ["1", "1"]),
        ("Assets:", ["", ""]),
        ("Current:", ["", ""]),
        ("Cash", ["2", "2"]),
    ]
    first = ["| Revenue | 1 | 1 |"]
    second = ["| Assets: |  |  |", "| Current: |  |  |", "| Cash | 2 | 2 |"]
    limit = _size(PRELUDE + second)
    assert limit >= _size(PRELUDE + first + second[:1])
    chunks = [
        c for c in kit.chunks(_grouped_body(kit, rows), table_max_chars=limit) if c.kind == "table"
    ]
    assert [c.text.split("\n") for c in chunks] == [PRELUDE + first, PRELUDE + second]


@pytest.mark.criterion("C4")
def test_a_row_too_long_is_a_part_of_its_own_and_whole(kit):
    # oracle: requirement 11, by hand. A row is never cut, whatever the limit.
    long_label = "Long " * 60 + "label"
    rows = [("Cash", ["1", "2"]), (long_label, ["3", "4"]), ("Land", ["5", "6"])]
    chunks = [
        c for c in kit.chunks(_grouped_body(kit, rows), table_max_chars=120) if c.kind == "table"
    ]
    assert [c.text.split("\n")[4:] for c in chunks] == [
        ["| Cash | 1 | 2 |"],
        [f"| {long_label} | 3 | 4 |"],
        ["| Land | 5 | 6 |"],
    ]


@pytest.mark.criterion("C4")
def test_the_default_limit_is_two_thousand_characters(kit):
    # oracle: requirement 11, the Interface default. 200 rows of 20 characters is over 2,000.
    rows = [(f"Row{i:03d}", [f"{i:,}", "0"]) for i in range(200)]
    chunks = [c for c in kit.chunks(_grouped_body(kit, rows)) if c.kind == "table"]
    assert len(chunks) > 1
    assert max(len(c.text) for c in chunks) <= 2000


@pytest.mark.criterion("C4")
@given(
    plan=st.lists(st.integers(0, 5), min_size=1, max_size=8),
    limit=st.integers(60, 400),
)
def test_parts_keep_every_row_in_order_with_the_same_prelude(kit, plan, limit):
    # oracle: requirement 11: each number in `plan` is a group row and that many rows under it.
    rows, expected = [], []
    for g, size in enumerate(plan):
        rows.append((f"Group{g}:", ["", ""]))
        for r in range(size):
            rows.append((f"G{g}R{r}", [str(r), str(g)]))
            expected.append(f"| G{g}R{r} | {r} | {g} |")
    assume(expected)
    chunks = [
        c for c in kit.chunks(_grouped_body(kit, rows), table_max_chars=limit) if c.kind == "table"
    ]
    prelude = chunks[0].text.split("\n")[:4]
    seen = []
    for chunk in chunks:
        lines = chunk.text.split("\n")
        assert lines[:4] == prelude
        body = [line for line in lines[4:] if not line.startswith("| Group")]
        seen.extend(body)
        if len(chunk.text) > limit:
            assert len(body) <= 1
    assert seen == expected
    assert [c.part for c in chunks] == list(range(len(chunks)))
    assert {c.parts for c in chunks} == {len(chunks)}
