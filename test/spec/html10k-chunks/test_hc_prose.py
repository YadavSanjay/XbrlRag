"""Prose chunks and layout tables (docs/specs/html10k-chunks.md, requirements 6-8; C1, C5)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

ITEM_1 = "<div>Item 1. Business</div>"
ITEM_1A = "<div>Item 1A. Risk Factors</div>"


@pytest.mark.criterion("C1")
def test_prose_closes_when_the_size_is_reached(kit):
    # oracle: requirement 7. 600 + 500 = 1,100 >= 1,000 closes the first chunk; 100 is left over.
    a, b, c = "A" * 600, "B" * 500, "C" * 100
    chunks = kit.chunks(f"<p>{a}</p><p>{b}</p><p>{c}</p>")
    assert [x.text for x in chunks] == [f"{a} {b}", c]
    assert [x.kind for x in chunks] == ["prose", "prose"]
    assert [x.table_index for x in chunks] == [None, None]
    assert [(x.part, x.parts) for x in chunks] == [(0, 1), (0, 1)]


@pytest.mark.criterion("C1")
def test_a_size_exactly_reached_closes_and_the_join_space_does_not_count(kit):
    # oracle: requirement 7. Block lengths 4 + 6 = 10 >= 10; the joining space is not counted.
    chunks = kit.chunks("<p>aaaa</p><p>bbbbbb</p><p>c</p>", chunk_size_chars=10)
    assert [x.text for x in chunks] == ["aaaa bbbbbb", "c"]


@pytest.mark.criterion("C1")
def test_a_long_block_is_never_split(kit):
    # oracle: requirement 7. One 3,000-character block is one chunk.
    long_block = "word " * 600
    chunks = kit.chunks(f"<p>{long_block}</p>")
    assert [x.text for x in chunks] == [long_block.strip()]


@pytest.mark.criterion("C1")
def test_a_section_change_closes_the_chunk(kit):
    # oracle: requirement 7, by hand: each heading opens its section's chunk.
    chunks = kit.chunks(f"{ITEM_1}<p>bus</p>{ITEM_1A}<p>risk</p>")
    assert [x.text for x in chunks] == ["Item 1. Business bus", "Item 1A. Risk Factors risk"]


@pytest.mark.criterion("C1")
def test_a_data_table_closes_the_chunk_and_prose_resumes_after_it(kit):
    # oracle: requirement 7, by hand. Prose on each side of a table never joins across it.
    table = kit.grid(["2024"], [("Revenue", ["$1"])])
    chunks = kit.chunks(f"<p>before</p>{table}<p>after</p>")
    assert [x.kind for x in chunks] == ["prose", "table", "prose"]
    assert chunks[0].text == "before"
    assert chunks[2].text == "after"


@pytest.mark.criterion("C1")
def test_a_block_is_a_leaf_and_inline_runs_join_by_a_space(kit):
    # oracle: requirement 6, by hand. The outer div holds blocks, so it is not one; the span is
    # inline, so it folds into its paragraph.
    chunks = kit.chunks("<div><p>one <span>two</span> three</p><p>four</p></div>")
    assert [x.text for x in chunks] == ["one two three four"]


@pytest.mark.criterion("C1")
@given(
    lengths=st.lists(st.integers(1, 40), min_size=1, max_size=12),
    size=st.integers(5, 80),
)
def test_prose_equals_the_rule_for_any_block_lengths(kit, lengths, size):
    # oracle: requirement 7, recomputed here: add a block, close at sum >= size.
    blocks = ["x" * n for n in lengths]
    expected, current = [], []
    for block in blocks:
        current.append(block)
        if sum(map(len, current)) >= size:
            expected.append(" ".join(current))
            current = []
    if current:
        expected.append(" ".join(current))
    body = "".join(f"<p>{b}</p>" for b in blocks)
    assert [x.text for x in kit.chunks(body, chunk_size_chars=size)] == expected


@pytest.mark.criterion("C5")
def test_a_bullet_table_joins_the_prose_stream(kit):
    # oracle: requirement 8, by hand. A bullet in a table has no header and no number.
    table = "<table><tr><td>•</td><td>We make things.</td></tr></table>"
    chunks = kit.chunks(f"<p>Intro.</p>{table}<p>After.</p>")
    assert [x.text for x in chunks] == ["Intro. • We make things. After."]
    assert [x.kind for x in chunks] == ["prose"]
    assert chunks[0].table_index is None


@pytest.mark.criterion("C5")
def test_a_one_row_text_table_is_prose_and_shows_no_markdown(kit):
    # oracle: requirement 8, by hand: words in two cells, with no header and no number.
    chunks = kit.chunks("<table><tr><td>Delaware</td><td>Corporation</td></tr></table>")
    assert [x.kind for x in chunks] == ["prose"]
    assert chunks[0].text == "Delaware Corporation"


@pytest.mark.criterion("C5")
def test_a_heading_table_keeps_its_text(kit):
    # oracle: requirement 8, by hand: MCD prints headings as one-cell tables; a cell-less column
    # count must not lose the text.
    chunks = kit.chunks("<table><tr><td>FORWARD-LOOKING STATEMENTS</td></tr></table><p>We may.</p>")
    assert [x.kind for x in chunks] == ["prose"]
    assert chunks[0].text == "FORWARD-LOOKING STATEMENTS We may."


@pytest.mark.criterion("C5")
def test_an_empty_table_adds_nothing(kit):
    # oracle: requirement 8, by hand. Two paragraphs and an empty table: they stay one chunk.
    chunks = kit.chunks("<p>a</p><table><tr><td> </td></tr></table><p>b</p>")
    assert [x.text for x in chunks] == ["a b"]


@pytest.mark.criterion("C5")
def test_a_headed_text_table_and_an_unheaded_number_table_are_data(kit):
    # oracle: requirement 8, by hand. The first has a column header, the second a number.
    headed = "<table><tr><td>Name</td><td>Title</td></tr><tr><td>Ann</td><td>CEO</td></tr></table>"
    numbers = "<table><tr><td>Revenue</td><td>1,234</td></tr></table>"
    chunks = kit.chunks(headed + numbers)
    assert [x.kind for x in chunks] == ["table", "table"]
    assert [x.table_index for x in chunks] == [0, 1]
    assert chunks[0].text.split("\n") == [
        "Section: none",
        "| Name | Title |",
        "| --- | --- |",
        "| Ann | CEO |",
    ]
    assert chunks[1].text.split("\n") == [
        "Section: none",
        "|  |  |",
        "| --- | --- |",
        "| Revenue | 1,234 |",
    ]


@pytest.mark.criterion("C5")
def test_a_table_is_numbered_as_parse_tables_numbers_it(kit):
    # oracle: requirement 6, by hand. Table 0 is layout and adds prose; the data table is table 1.
    bullet = "<table><tr><td>•</td><td>Text.</td></tr></table>"
    data = kit.grid(["2024"], [("Revenue", ["$1"])])
    chunks = kit.chunks(bullet + data)
    assert [(x.kind, x.table_index) for x in chunks] == [("prose", None), ("table", 1)]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(tokens=st.lists(st.from_regex(r"LAYOUT[A-Z]{6}", fullmatch=True), min_size=1, max_size=6,
                       unique=True))
def test_every_layout_table_token_is_in_exactly_one_prose_chunk(kit, tokens):
    # oracle: requirement 8: a layout table's text is in the prose stream, once.
    body = "".join(f"<table><tr><td>•</td><td>{t}</td></tr></table>" for t in tokens)
    chunks = kit.chunks(body, chunk_size_chars=20)
    for token in tokens:
        assert sum(token in x.text for x in chunks) == 1
    assert all(x.kind == "prose" for x in chunks)
