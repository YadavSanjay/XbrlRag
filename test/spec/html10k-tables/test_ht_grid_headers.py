"""C1 (the grid) and C2 (headers and periods). Spec: docs/specs/html10k-tables.md."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table

ALL_TABLES = [
    "cost_income_statement.html",
    "cost_gross_margin.html",
    "ko_tax_rate.html",
    "unp_income.html",
    "unp_equity.html",
    "msft_summary.html",
    "wmt_swaps.html",
    "split_parens.html",
    "superscripts.html",
    "rowspan.html",
    "pension_groups.html",
]


def values(row):
    return [c.value for c in row.cells]


def blank_row_grid(table):
    return [(r.label, values(r)) for r in table.rows]


@pytest.mark.criterion("C1")
def test_three_value_columns_and_one_cell_per_column(load):
    # oracle: hand-read from fixtures/cost_income_statement.html (17 non-blank body rows)
    table = parse_table(load("cost_income_statement.html"))
    assert len(table.columns) == 3
    assert [r.label for r in table.rows] == [
        "REVENUE",
        "Net sales",
        "Membership fees",
        "Total revenue",
        "OPERATING EXPENSES",
        "Merchandise costs",
        "Operating income",
        "OTHER INCOME (EXPENSE)",
        "Interest expense",
        "Net income attributable to noncontrolling interests",
        "NET INCOME ATTRIBUTABLE TO COSTCO",
        "NET INCOME PER COMMON SHARE ATTRIBUTABLE TO COSTCO:",
        "Basic",
        "Diluted",
        "Shares used in calculation (000’s)",
        "Basic",
        "Diluted",
    ]
    assert all(len(r.cells) == 3 for r in table.rows)


@pytest.mark.criterion("C1")
def test_split_and_unsplit_rows_land_in_the_same_columns(load, row):
    # oracle: hand-read; Net sales splits "$" and the number, Merchandise costs does not
    table = parse_table(load("cost_income_statement.html"))
    assert values(row(table, "Net sales")) == [Dec("249625"), Dec("237710"), Dec("222730")]
    assert values(row(table, "Merchandise costs")) == [Dec("222358"), Dec("212586"), Dec("199382")]


@pytest.mark.criterion("C1")
def test_group_row_stays_with_empty_cells(load, row):
    # oracle: hand-read; REVENUE has a label and nothing in its value columns
    table = parse_table(load("cost_income_statement.html"))
    group = row(table, "REVENUE")
    assert values(group) == [None, None, None]
    assert [c.is_nil for c in group.cells] == [False, False, False]


@pytest.mark.criterion("C1")
def test_source_locations_are_plain_ordinals(load, row):
    # oracle: counted by hand in the fixture. Row 3: label, $, 249,625, blank, gap, $, 237,710,
    # blank, gap, $, 222,730, blank. Row 11: label, (169), blank, gap, (160), blank, gap, (158).
    table = parse_table(load("cost_income_statement.html"))
    net_sales = row(table, "Net sales")
    assert net_sales.source_row == 3
    assert [(c.source_row, c.source_cell) for c in net_sales.cells] == [(3, 2), (3, 6), (3, 10)]
    interest = row(table, "Interest expense")
    assert [(c.source_row, c.source_cell) for c in interest.cells] == [(11, 1), (11, 4), (11, 7)]


@pytest.mark.criterion("C1")
def test_a_blank_row_counts_in_source_row_but_not_in_rows(load, row):
    # oracle: hand-read; row 8 of the fixture is a blank spacer, so Operating income is <tr> 9
    table = parse_table(load("cost_income_statement.html"))
    assert row(table, "Operating income").source_row == 9
    assert "" not in [r.label for r in table.rows]


@pytest.mark.criterion("C1")
def test_rowspan_label_repeats_and_its_values_stay_aligned(load):
    # oracle: hand-read from fixtures/rowspan.html; <tr> 3 has only its two number cells
    table = parse_table(load("rowspan.html"))
    assert len(table.columns) == 2
    assert [r.label for r in table.rows] == ["Americas", "Americas", "Europe"]
    assert [values(r) for r in table.rows] == [
        [Dec(100), Dec(90)],
        [Dec(110), Dec(95)],
        [Dec(80), Dec(70)],
    ]
    assert [c.source_cell for c in table.rows[1].cells] == [0, 1]


@pytest.mark.criterion("C1")
def test_spanning_header_with_cells_in_the_gap_columns(load):
    # oracle: hand-read from fixtures/wmt_swaps.html; four value columns, the (1) markers sit in
    # gap positions and add none
    table = parse_table(load("wmt_swaps.html"))
    assert len(table.columns) == 4
    assert values(table.rows[2]) == [Dec("12150"), Dec("-1956"), Dec("13921"), Dec("-2112")]


@pytest.mark.criterion("C1")
def test_eight_columns_with_empty_positions(load, row):
    # oracle: hand-read from fixtures/unp_equity.html
    table = parse_table(load("unp_equity.html"))
    assert len(table.columns) == 8
    assert values(row(table, "Balance at January 1, 2022")) == [
        Dec("1112.4"),
        Dec("-473.6"),
        Dec("2781"),
        Dec("4979"),
        Dec("55049"),
        Dec("-47734"),
        Dec("-914"),
        Dec("14161"),
    ]
    empty = row(table, "Net income").cells[0]
    assert (empty.value, empty.is_nil) == (None, False)
    assert (empty.source_row, empty.source_cell) == (None, None)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@pytest.mark.parametrize("name", ALL_TABLES)
def test_doubling_every_colspan_changes_nothing(load, double_colspans, name):
    # oracle: the same table in a finer layout grid is the same table
    html = load(name)
    table = parse_table(html)
    assert len(table.rows) >= 2
    assert parse_table(double_colspans(html)) == table


@pytest.mark.criterion("C1")
@given(blanks=st.lists(st.integers(min_value=0, max_value=4), min_size=1, max_size=4))
def test_blank_rows_change_no_label_or_value(load, blanks):
    # oracle: a spacer row carries no content, so only source_row may move
    html = load("unp_income.html")
    table = parse_table(html)
    assert len(table.rows) >= 3
    pieces = html.split("</tr>\n")
    for position in sorted(blanks, reverse=True):
        pieces.insert(position + 2, '<tr><td colspan="12"></td>')
    spaced = "</tr>\n".join(pieces)
    assert blank_row_grid(parse_table(spaced)) == blank_row_grid(table)


@pytest.mark.criterion("C2")
def test_two_level_headers_with_a_three_period_date_row(load):
    # oracle: hand-read from fixtures/cost_income_statement.html; <br/> splits each date
    table = parse_table(load("cost_income_statement.html"))
    assert table.header_row_count == 2
    assert [c.headers for c in table.columns] == [
        ("52 Weeks Ended", "September 1, 2024"),
        ("53 Weeks Ended", "September 3, 2023"),
        ("52 Weeks Ended", "August 28, 2022"),
    ]
    assert [c.period for c in table.columns] == ["2024-09-01", "2023-09-03", "2022-08-28"]


@pytest.mark.criterion("C2")
def test_two_periods_with_the_date_in_the_stub_cell(load):
    # oracle: hand-read from fixtures/ko_tax_rate.html; "Year Ended December 31," is top-left
    table = parse_table(load("ko_tax_rate.html"))
    assert table.header_row_count == 1
    assert [c.headers for c in table.columns] == [("2023",), ("2022",)]
    assert [c.period for c in table.columns] == ["2023-12-31", "2022-12-31"]


@pytest.mark.criterion("C2")
def test_stub_cell_with_scale_and_date_together(load):
    # oracle: hand-read from fixtures/unp_income.html
    table = parse_table(load("unp_income.html"))
    assert [c.period for c in table.columns] == ["2024-12-31", "2023-12-31", "2022-12-31"]


@pytest.mark.criterion("C2")
def test_bare_years_and_a_period_free_column(load):
    # oracle: hand-read from fixtures/msft_summary.html
    table = parse_table(load("msft_summary.html"))
    assert [c.period for c in table.columns] == ["2024", "2023", None]
    assert table.columns[2].headers == ("Percentage Change",)


@pytest.mark.criterion("C2")
def test_spanning_date_over_sub_columns(load):
    # oracle: hand-read from fixtures/wmt_swaps.html; each date spans two measures
    table = parse_table(load("wmt_swaps.html"))
    assert table.header_row_count == 2
    assert [c.headers for c in table.columns] == [
        ("January 31, 2024", "Notional Amount"),
        ("January 31, 2024", "Fair Value"),
        ("January 31, 2023", "Notional Amount"),
        ("January 31, 2023", "Fair Value"),
    ]
    assert [c.period for c in table.columns] == [
        "2024-01-31",
        "2024-01-31",
        "2023-01-31",
        "2023-01-31",
    ]


@pytest.mark.criterion("C2")
def test_spanning_groups_over_repeated_years(load):
    # oracle: hand-read from fixtures/pension_groups.html
    table = parse_table(load("pension_groups.html"))
    assert table.header_row_count == 2
    assert [c.headers for c in table.columns] == [
        ("Pension Benefits", "2024"),
        ("Pension Benefits", "2023"),
        ("Other Benefits", "2024"),
        ("Other Benefits", "2023"),
    ]
    assert [c.period for c in table.columns] == [
        "2024-12-31",
        "2023-12-31",
        "2024-12-31",
        "2023-12-31",
    ]


@pytest.mark.criterion("C2")
def test_rowspan_stub_in_the_header(load):
    # oracle: hand-read from fixtures/rowspan.html; "Segment" fills the stub of both header rows
    table = parse_table(load("rowspan.html"))
    assert table.header_row_count == 2
    assert [c.headers for c in table.columns] == [("Fiscal", "2024"), ("Fiscal", "2023")]
    assert [c.period for c in table.columns] == ["2024", "2023"]


@pytest.mark.criterion("C2")
def test_a_table_with_no_header():
    # oracle: synthetic; the first row is already a row of numbers with a text label
    html = (
        "<table><tr><td>Land</td><td>9,447</td></tr>"
        "<tr><td>Buildings</td><td>8,590</td></tr></table>"
    )
    table = parse_table(html)
    assert table.header_row_count == 0
    assert [c.headers for c in table.columns] == [()]
    assert table.columns[0].period is None
    assert [r.label for r in table.rows] == ["Land", "Buildings"]
    assert values(table.rows[0]) == [Dec("9447")]


@pytest.mark.criterion("C2")
def test_fiscal_year_words_and_a_comparison_header(mini):
    # oracle: requirement 3; "Fiscal 2024" is a year, "2024 vs. 2023" is no single period
    html = mini(["Fiscal 2024", "FY2023", "2024 vs. 2023"], [("Sales", ["10", "9", "1"])])
    table = parse_table(html)
    assert [c.period for c in table.columns] == ["2024", "2023", None]


@pytest.mark.criterion("C2")
@given(day=st.dates(min_value=date(2005, 1, 1), max_value=date(2030, 12, 31)))
def test_a_printed_date_is_its_iso_form(mini, day):
    # oracle: closed form; the ISO string of the generated date
    header = f"{day:%B} {day.day}, {day.year}"
    table = parse_table(mini([header], [("Assets", ["1,234"])]))
    assert [c.period for c in table.columns] == [day.isoformat()]


@pytest.mark.criterion("C2")
@given(
    day=st.dates(min_value=date(2005, 1, 1), max_value=date(2030, 12, 31)),
    lead=st.sampled_from(["Year Ended", "Fiscal Years Ended", "As of", "Years ended"]),
)
def test_a_date_built_from_the_stub_and_a_year(mini, day, lead):
    # oracle: closed form; month and day in the stub, the year above the column
    stub = f"{lead} {day:%B} {day.day},"
    table = parse_table(mini([str(day.year)], [("Assets", ["1,234"])], stub=stub))
    assert [c.period for c in table.columns] == [day.isoformat()]


@pytest.mark.criterion("C2")
@given(years=st.lists(st.integers(2005, 2030), min_size=2, max_size=4, unique=True))
def test_bare_years_keep_their_order(mini, years):
    # oracle: closed form; the generated years, as strings, in the printed order
    numbers = [f"{i + 1},000" for i in range(len(years))]
    table = parse_table(mini([str(y) for y in years], [("Sales", numbers)]))
    assert [c.period for c in table.columns] == [str(y) for y in years]
