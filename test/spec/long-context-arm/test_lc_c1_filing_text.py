"""C1: filing_text reduces canonical XHTML to plain text, one block per line.

Every expected value is written by hand from requirement 2. Judgment calls, stated so a
reviewer can check them against the spec:

- the output is the kept lines joined by ``"\\n"`` with no trailing newline ("exactly the
  expected lines"), so a single ``<p>`` of k letters gives exactly k characters (C3 uses
  this);
- a line is stripped at both ends (whitespace collapses, and a whitespace-only line is an
  empty line);
- inline elements such as ``<span>`` do not start a line; only the listed blocks do.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context import filing_text

_WORD = st.text(
    alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")),
    min_size=1,
    max_size=10,
)
_PARAGRAPH = st.lists(_WORD, min_size=1, max_size=6).map(" ".join)
_PARAGRAPHS = st.lists(_PARAGRAPH, min_size=1, max_size=8)


@pytest.mark.criterion("C1")
def test_hidden_header_script_and_style_are_dropped(xhtml, ix_header):
    # oracle: requirement 2 — ix:header, script and style text never appears; the one
    # visible paragraph is the whole output
    html = xhtml(
        ix_header("HIDDENDOCTYPE")
        + "<script>var SCRIPTMARK = 1;</script>"
        + "<p>Visible text</p>"
        + "<style>.STYLEBODY { color: red; }</style>",
        head="<style>.STYLEHEAD { color: blue; }</style>",
    )
    out = filing_text(html)
    for hidden in ("HIDDENDOCTYPE", "SCRIPTMARK", "STYLEBODY", "STYLEHEAD", "color"):
        assert hidden not in out
    assert out == "Visible text"


@pytest.mark.criterion("C1")
def test_two_paragraphs_give_two_lines(xhtml):
    # oracle: C1's literal example — <p>Net sales</p><p>increased</p> gives two lines
    assert filing_text(xhtml("<p>Net sales</p><p>increased</p>")) == "Net sales\nincreased"


@pytest.mark.criterion("C1")
def test_table_row_cells_joined_by_pipe(xhtml):
    # oracle: C1's literal example, plus a second row on its own line (requirement 2)
    html = xhtml(
        "<table><tr><td>Revenue</td><td>391,035</td></tr>"
        "<tr><td>Cost of sales</td><td>210,352</td></tr></table>"
    )
    assert filing_text(html) == "Revenue | 391,035\nCost of sales | 210,352"


@pytest.mark.criterion("C1")
def test_whitespace_collapses_and_no_empty_lines(xhtml):
    # oracle: requirement 2 — runs of spaces, tabs and newlines become one space; the
    # source's whitespace between blocks and an empty block give no line
    html = xhtml(
        "\n  <p>  Net \t\n   sales\n\n  rose  </p>\n\t\n"
        "  <div>   </div>\n"
        "  <p>by\t\t8%</p>\n"
    )
    assert filing_text(html) == "Net sales rose\nby 8%"


@pytest.mark.criterion("C1")
def test_each_listed_block_starts_a_line(xhtml):
    # oracle: requirement 2's list — heading, division, list item, paragraph, line break
    # and table row each start a new line; a span inside a paragraph does not
    html = xhtml(
        "<h2>Item 7</h2>"
        "<div>Overview</div>"
        "<ul><li>Products</li><li>Services</li></ul>"
        "<p>Net <span>sales</span> rose</p>"
        "<p>First<br/>Second</p>"
        "<div><p>Nested</p></div>"
        "<table><tr><td>A</td><td>1</td></tr></table>"
    )
    assert filing_text(html).split("\n") == [
        "Item 7",
        "Overview",
        "Products",
        "Services",
        "Net sales rose",
        "First",
        "Second",
        "Nested",
        "A | 1",
    ]


@pytest.mark.criterion("C1")
@given(paragraphs=_PARAGRAPHS)
def test_random_paragraphs_one_line_each_in_order(xhtml, paragraphs):
    # oracle: requirement 2 (C1 "Order") — each paragraph is one line, in order
    html = xhtml("".join(f"<p>{p}</p>" for p in paragraphs))
    assert filing_text(html) == "\n".join(paragraphs)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    paragraphs=_PARAGRAPHS,
    hidden=_PARAGRAPH,
    position=st.integers(min_value=0, max_value=8),
)
def test_adding_an_ix_header_never_changes_the_output(xhtml, ix_header, paragraphs, hidden,
                                                      position):
    # oracle: requirement 2 (C1 "Hidden header") — inserting an ix:header anywhere between
    # blocks leaves the output equal to the header-free one, which is the paragraphs
    blocks = [f"<p>{p}</p>" for p in paragraphs]
    with_header = list(blocks)
    with_header.insert(min(position, len(blocks)), ix_header(hidden))
    plain = filing_text(xhtml("".join(blocks)))
    assert plain == "\n".join(paragraphs)
    assert filing_text(xhtml("".join(with_header))) == plain


@pytest.mark.criterion("C1")
@given(rows=st.lists(st.lists(_WORD, min_size=1, max_size=5), min_size=1, max_size=5))
def test_random_table_rows_join_cells_with_pipe(xhtml, rows):
    # oracle: requirement 2 — each row is one line, its cells joined by " | "
    body = "<table>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    ) + "</table>"
    assert filing_text(xhtml(body)) == "\n".join(" | ".join(row) for row in rows)
