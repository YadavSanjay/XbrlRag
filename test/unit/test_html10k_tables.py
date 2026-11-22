"""Unit tests for the table parser's input forms and the oracle generator's rules.

The locked spec tests live in test/spec/html10k-tables and test/spec/html10k-tables-scale; these
cover what the oracle runs added.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest.html10k.tables import parse_table, parse_tables

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "html10k" / "build_table_oracle.py"
_spec = importlib.util.spec_from_file_location("build_table_oracle", SCRIPT)
assert _spec is not None and _spec.loader is not None
oracle_script = importlib.util.module_from_spec(_spec)
sys.modules["build_table_oracle"] = oracle_script
_spec.loader.exec_module(oracle_script)

XHTML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body><div>(in millions)</div>'
    "<table><tr><td/><td>2024</td></tr><tr><td>Sales</td><td>1,234</td></tr>"
    "<tr><td/><td/></tr><tr><td>Costs</td><td>(5)</td></tr></table></body></html>"
)
IX_DOC = (
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>'
    "<ix:header><ix:hidden><table><tr><td>hidden</td></tr></table></ix:hidden></ix:header>"
    "<table><tr><td>Label</td><td>$</td><td>(</td>"
    '<td><ix:nonFraction name="us-gaap:Assets" unitRef="u" scale="6" sign="-"'
    ' format="ixt:num-dot-decimal">1,234</ix:nonFraction></td><td>)</td></tr>'
    "<tr><td>Other</td>"
    '<td><ix:nonFraction name="us-gaap:Cash" unitRef="u" scale="6"'
    ' format="ixt:fixed-zero">—</ix:nonFraction></td></tr>'
    "<tr><td>Words</td>"
    '<td>less <ix:nonFraction name="us-gaap:Cash" unitRef="u" scale="6">5</ix:nonFraction></td>'
    "</tr><tr><td>Two</td>"
    '<td><ix:nonFraction name="us-gaap:Cash" unitRef="u">5</ix:nonFraction> '
    '<ix:nonFraction name="us-gaap:Cash" unitRef="u">6</ix:nonFraction></td></tr>'
    "</table></body></html>"
)


def test_strip_output_form_parses_like_plain_html():
    # ix-strip writes default-namespace XHTML with an XML declaration and self-closed cells.
    tables = parse_tables(XHTML)
    assert len(tables) == 1
    assert tables[0].scale == 6
    assert [r.label for r in tables[0].rows] == ["Sales", "Costs"]
    assert tables[0].rows[0].cells[0].value == Decimal("1234")
    assert (tables[0].rows[1].source_row, tables[0].rows[1].cells[0].value) == (3, Decimal("-5"))


def test_raised_marker_that_lost_its_style_leaves_a_label():
    # ix-strip removes style, so the filing agent's raised span arrives bare.
    html = (
        "<table><tr><td></td><td>2024</td></tr>"
        "<tr><td>Basic Net Income Per Share<span>1</span></td><td>$ 2.47</td></tr></table>"
    )
    row = parse_table(html, caption="(in millions)").rows[0]
    assert (row.label, row.footnotes) == ("Basic Net Income Per Share", ("1",))
    assert row.cells[0].scale == 0


def test_a_header_laid_out_away_from_its_numbers_still_labels_them():
    # Three headers, three value positions, on different layout grids.
    html = (
        '<table><tr><td colspan="4"></td><td>2024</td><td></td><td>2023</td><td></td>'
        '<td>2022</td></tr><tr><td colspan="9">Sales</td><td>1,000</td><td>2,000</td>'
        "<td>3,000</td></tr></table>"
    )
    table = parse_table(html)
    assert [c.period for c in table.columns] == ["2024", "2023", "2022"]
    assert [c.value for c in table.rows[0].cells] == [
        Decimal("1000"),
        Decimal("2000"),
        Decimal("3000"),
    ]


def test_oracle_counts_no_table_inside_the_hidden_header():
    records, _ = oracle_script.build_records(IX_DOC.encode(), "0000000000-24-000001")
    assert {r["table_index"] for r in records} == {0}


def test_oracle_reads_the_printed_sign_from_a_split_parenthesis():
    records, _ = oracle_script.build_records(IX_DOC.encode(), "0000000000-24-000001")
    first = records[0]
    assert (first["source_row"], first["source_cell"]) == (0, 3)
    assert (first["value"], first["negative"], first["scale"]) == ("1234", True, 6)
    assert first["tagged_negative"] is True


def test_oracle_marks_a_printed_dash_nil():
    records, _ = oracle_script.build_records(IX_DOC.encode(), "0000000000-24-000001")
    nil = records[1]
    assert (nil["value"], nil["nil"]) == (None, True)


def test_oracle_drops_and_counts_mixed_and_double_cells():
    records, drops = oracle_script.build_records(IX_DOC.encode(), "0000000000-24-000001")
    assert len(records) == 2
    assert drops["other_text_in_cell"] == 1
    assert drops["conflicting_tags"] == 2


def test_held_out_filers_are_refused(tmp_path):
    held_out = sorted(oracle_script.held_out_tickers())
    assert held_out
    with pytest.raises(ValueError):
        list(oracle_script.iter_filings(tmp_path, held_out[:1]))


def _share_table(header: str, note: str) -> str:
    return (
        f"<table><tr><td>{note}</td><td>{header}</td></tr>"
        "<tr><td>Balance at year end</td><td>5</td></tr></table>"
    )


def test_except_share_price_is_no_except_for_share_counts():
    # The term names a price; the count column stays in the note's millions (AMD's option tables).
    table = parse_table(_share_table("Number of Shares", "(In millions, except share price)"))
    assert table.rows[0].cells[0].scale == 6


def test_except_share_data_reaches_a_share_count_column():
    table = parse_table(_share_table("Number of Shares", "(In millions, except share data)"))
    assert table.rows[0].cells[0].scale == 0


def test_a_total_of_shares_is_a_count():
    # "Total" alone is money, but it does not outrank what it totals.
    table = parse_table(_share_table("Total Number of Shares", "(in millions, except share data)"))
    assert table.rows[0].cells[0].scale == 0
    money = parse_table(_share_table("Total", "(in millions, except share data)"))
    assert money.rows[0].cells[0].scale == 6


def test_a_footnote_in_a_cell_scales_only_that_cell():
    html = (
        "<div>(in millions)</div><table><tr><td></td><td>2024</td></tr>"
        "<tr><td>Awards</td><td>12 (a)</td></tr><tr><td>Grants</td><td>34</td></tr></table>"
        "<div>(a)In thousands.</div>"
    )
    table = parse_tables(html)[0]
    assert [r.cells[0].scale for r in table.rows] == [3, 6]


def test_a_page_number_table_does_not_hide_the_caption():
    # A one-cell table is furniture, so the sentence above it still captions the next table.
    html = (
        "<div>Debt was as follows (in millions):</div><table><tr><td>66</td></tr></table>"
        "<table><tr><td></td><td>2024</td></tr><tr><td>Notes</td><td>700</td></tr></table>"
    )
    table = parse_tables(html)[1]
    assert table.caption == "Debt was as follows (in millions):"
    assert table.scale == 6


_VALUES = "<table><tr><td></td><td>2024</td></tr><tr><td>Revenue</td><td>700</td></tr></table>"


@pytest.mark.parametrize(
    ("block", "scale"),
    [
        ("(Shares and dollars in millions)", 6),  # shares are named, and so are the dollars
        ("(Figures in tables in millions, except shares)", 6),  # the except list adds nothing
        ("(In millions of shares)", 0),  # shares alone, however it is worded
        ("(Common shares in millions)", 0),
    ],
)
def test_a_parenthesis_stands_unless_it_is_about_shares_alone(block, scale):
    html = f"<div>{block}</div><div>Debt.</div>{_VALUES}"
    assert parse_tables(html)[0].scale == scale


def test_a_note_table_between_two_tables_ends_the_run():
    # It acts as a block, so the table after it continues nothing before it.
    html = (
        f"<div>(in millions)</div>{_VALUES}"
        "<table><tr><td>(in thousands of dollars)</td></tr></table>"
        f"{_VALUES}"
    )
    assert [t.scale for t in parse_tables(html)] == [6, 6, 3]  # the note table holds no value


def test_a_note_table_with_a_second_sentence_stands_for_nothing():
    html = (
        "<table><tr><td>Heading</td></tr><tr><td>In millions. Percentages in chart.</td></tr>"
        f"</table><div>Debt.</div>{_VALUES}"
    )
    assert parse_tables(html)[1].scale == 0
