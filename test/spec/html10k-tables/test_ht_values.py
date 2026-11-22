"""C5 (values, sign), C6 (nil), C7 (footnotes), C8 (percent). Spec: docs/specs/html10k-tables.md."""

from __future__ import annotations

import re
from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table


def values(row):
    return [c.value for c in row.cells]


@pytest.mark.criterion("C5")
def test_parentheses_are_negative(load, row):
    # oracle: hand-read from fixtures/cost_income_statement.html
    table = parse_table(load("cost_income_statement.html"))
    assert values(row(table, "Interest expense")) == [Dec("-169"), Dec("-160"), Dec("-158")]


@pytest.mark.criterion("C5")
def test_decimals_and_thousands_separators(load, row):
    # oracle: hand-read from fixtures/cost_income_statement.html and unp_income.html
    assert values(row(parse_table(load("cost_income_statement.html")), "Basic")) == [
        Dec("16.59"),
        Dec("14.18"),
        Dec("13.17"),
    ]
    assert values(row(parse_table(load("unp_income.html")), "Earnings per share - basic")) == [
        Dec("11.10"),
        Dec("10.47"),
        Dec("11.24"),
    ]


@pytest.mark.criterion("C5")
def test_closing_parenthesis_in_its_own_cell(load, row):
    # oracle: hand-read from fixtures/split_parens.html; "(1,646" then ")"
    table = parse_table(load("split_parens.html"))
    assert values(row(table, "Other, net"))[:2] == [Dec("-1646"), Dec("-409")]
    assert row(table, "Other, net").cells[0].source_cell == 3
    assert values(row(table, "Interest"))[:2] == [Dec("-2935"), Dec("1120")]


@pytest.mark.criterion("C5")
def test_dollar_sign_inside_and_outside_the_parentheses(mini):
    # oracle: both forms are in filings: "$(654)" (Walmart) and "($1,006)" (Procter & Gamble)
    table = parse_table(mini(["2024", "2023"], [("Hedges", ["$(654)", "($1,006)"])]))
    assert values(table.rows[0]) == [Dec("-654"), Dec("-1006")]


@pytest.mark.criterion("C5")
def test_hyphen_and_minus_sign_are_negative(mini):
    # oracle: requirement 6; U+2212 is the typographic minus
    table = parse_table(mini(["2024", "2023"], [("Other", ["-1,234", "−5.6"])]))
    assert values(table.rows[0]) == [Dec("-1234"), Dec("-5.6")]


@pytest.mark.criterion("C5")
def test_print_decides_the_sign_not_the_label(mini):
    # oracle: requirement 6; an expense printed bare stays positive
    rows = [("Interest expense", ["1,527"]), ("Interest income", ["(907)"])]
    table = parse_table(mini(["2024"], rows))
    assert [values(r) for r in table.rows] == [[Dec("1527")], [Dec("-907")]]


@pytest.mark.criterion("C5")
@given(
    amount=st.decimals(min_value=0, max_value=10_000_000, places=2),
    form=st.sampled_from(
        [
            ("{}", "", 1),
            ("${}", "", 1),
            ("({})", "", -1),
            ("$({})", "", -1),
            ("(${})", "", -1),
            ("-{}", "", -1),
            ("−{}", "", -1),
            ("({}", ")", -1),
            ("$({}", ")", -1),
        ]
    ),
)
def test_every_printed_form_gives_the_signed_decimal(amount, form):
    # oracle: closed form; the generated decimal with the form's sign
    head, tail, sign = form
    html = (
        '<table><tr><td></td><td colspan="2">2024</td></tr>'
        f"<tr><td>Item</td><td>{head.format(f'{amount:,.2f}')}</td><td>{tail}</td></tr></table>"
    )
    cell = parse_table(html).rows[0].cells[0]
    assert cell.value == sign * amount
    assert cell.source_cell == 1


@pytest.mark.criterion("C6")
def test_em_dash_is_nil(load, row):
    # oracle: hand-read from fixtures/cost_income_statement.html; the third period prints (71)
    table = parse_table(load("cost_income_statement.html"))
    cells = row(table, "Net income attributable to noncontrolling interests").cells
    assert [c.is_nil for c in cells] == [True, True, False]
    assert [c.value for c in cells] == [None, None, Dec("-71")]
    assert [c.source_cell for c in cells] == [1, 4, 7]


@pytest.mark.criterion("C6")
def test_hyphen_is_nil_and_blank_is_empty(load, row):
    # oracle: hand-read from fixtures/unp_equity.html; "Net income" prints two blanks, then
    # hyphens, then 6,998 under Retained Earnings
    cells = row(parse_table(load("unp_equity.html")), "Net income").cells
    assert [c.is_nil for c in cells] == [False, False, True, True, False, True, True, False]
    total = Dec("6998")
    assert [c.value for c in cells] == [None, None, None, None, total, None, None, total]


@pytest.mark.criterion("C6")
def test_printed_zero_is_a_number_and_text_is_neither(mini):
    # oracle: requirement 7
    html = mini(["2024", "2023", "2022"], [("Other", ["0", "N/A", "NM"])])
    cells = parse_table(html).rows[0].cells
    assert (cells[0].value, cells[0].is_nil) == (Dec(0), False)
    assert (cells[1].value, cells[1].is_nil, cells[1].text) == (None, False, "N/A")
    assert (cells[2].value, cells[2].is_nil, cells[2].text) == (None, False, "NM")


@pytest.mark.criterion("C6")
@given(
    dash=st.sampled_from(["—", "–", "-"]),
    form=st.sampled_from(["{}", "$ {}", "${}", "{}%", "$ {} %"]),
    note=st.sampled_from(["", "(in millions)", "(in thousands)"]),
)
def test_a_dash_is_never_a_number(mini, dash, form, note):
    # oracle: requirement 7; plan rule that a nil never becomes 0
    html = mini(["2024", "2023"], [("Other", [form.format(dash), "12"])], stub=note)
    cell = parse_table(html).rows[0].cells[0]
    assert cell.is_nil is True
    assert cell.value is None
    assert cell.scaled_value is None


@pytest.mark.criterion("C7")
def test_footnote_in_the_next_cell(load):
    # oracle: hand-read from fixtures/wmt_swaps.html; "(1)" follows (654) in its own cell
    first = parse_table(load("wmt_swaps.html")).rows[0]
    assert [c.value for c in first.cells] == [Dec("6271"), Dec("-654"), Dec("8021"), Dec("-689")]
    assert [c.footnotes for c in first.cells] == [(), ("1",), (), ("1",)]
    assert [c.source_cell for c in first.cells] == [2, 6, 10, 14]


@pytest.mark.criterion("C7")
def test_superscripts_never_join_the_number(load, row):
    # oracle: hand-read from fixtures/superscripts.html; 7,392 with a raised 2 is not 73922
    cells = row(parse_table(load("superscripts.html")), "Third party").cells
    assert [c.value for c in cells] == [Dec("7392"), Dec("6801")]
    assert [c.footnotes for c in cells] == [("2",), ("3",)]


@pytest.mark.criterion("C7")
def test_marker_in_the_same_cell(load, row):
    # oracle: hand-read from fixtures/superscripts.html; "51,235 (a)", "125 *" and "4 †"
    table = parse_table(load("superscripts.html"))
    cells = row(table, "Intersegment").cells
    assert [(c.value, c.footnotes) for c in cells] == [(Dec("51235"), ("a",)), (Dec("125"), ("*",))]
    assert row(table, "Other").cells[1].footnotes == ("†",)


@pytest.mark.criterion("C7")
def test_a_lone_one_in_parentheses_is_a_number(load, row):
    # oracle: requirement 8; "(1)" alone in a value position, and no footnote on the row
    cell = row(parse_table(load("superscripts.html")), "Other").cells[0]
    assert (cell.value, cell.footnotes) == (Dec("-1"), ())


@pytest.mark.criterion("C7")
def test_label_markers_leave_the_label(load, mini):
    # oracle: requirement 8; a raised 1, and a "(1)" that ends a label. "(expense)" and
    # "(Note 7)" are words, not markers.
    table = parse_table(load("superscripts.html"))
    assert table.rows[0].label == "Basic Net Income Per Share"
    assert table.rows[0].footnotes == ("1",)
    labels = ["Revenue (1)", "Other income (expense)", "Income tax (Note 7)"]
    html = mini(["2024"], [(label, ["5"]) for label in labels])
    rows = parse_table(html).rows
    assert [(r.label, r.footnotes) for r in rows] == [
        ("Revenue", ("1",)),
        ("Other income (expense)", ()),
        ("Income tax (Note 7)", ()),
    ]


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_removing_the_markers_changes_no_value(load):
    # oracle: a marker carries no number, so the values are the same without it
    html = load("superscripts.html")
    bare = re.sub(r"<sup>.*?</sup>|<span style=[^>]*>.*?</span>| \(a\)| \*| †", "", html)
    assert "sup" not in bare
    with_markers, without = parse_table(html), parse_table(bare)
    assert len(with_markers.rows) == 4
    assert [values(r) for r in without.rows] == [values(r) for r in with_markers.rows]
    assert all(c.footnotes == () for r in without.rows for c in r.cells)

    swaps = load("wmt_swaps.html")
    plain = swaps.replace("(1)", "")
    expected = [values(r) for r in parse_table(swaps).rows]
    assert [values(r) for r in parse_table(plain).rows] == expected


@pytest.mark.criterion("C8")
def test_percent_in_its_own_cell(load, row):
    # oracle: hand-read from fixtures/ko_tax_rate.html
    cells = row(parse_table(load("ko_tax_rate.html")), "Statutory U.S. federal tax rate").cells
    rate = (Dec("21.0"), -2, True)
    assert [(c.value, c.scale, c.is_percent) for c in cells] == [rate, rate]
    assert cells[0].scaled_value == Dec("0.21")


@pytest.mark.criterion("C8")
def test_a_rate_column_that_prints_percent_only_at_its_ends(load, row):
    # oracle: hand-read from fixtures/ko_tax_rate.html; the middle rows print no %, and the
    # columns print no currency symbol, so they are rates too
    table = parse_table(load("ko_tax_rate.html"))
    middle = row(table, "Equity income or loss").cells
    assert [(c.value, c.scale, c.is_percent) for c in middle] == [
        (Dec("-2.1"), -2, True),
        (Dec("-2.7"), -2, True),
    ]


@pytest.mark.criterion("C8")
def test_a_percent_row_among_dollars_stays_alone(load, row):
    # oracle: hand-read from fixtures/cost_gross_margin.html; the column prints "$" too
    table = parse_table(load("cost_gross_margin.html"), caption="(dollars in millions)")
    sales = row(table, "Net sales").cells[0]
    costs = row(table, "Less merchandise costs").cells[0]
    margin = row(table, "Gross margin percentage").cells[0]
    assert (sales.value, sales.scale, sales.is_percent) == (Dec("249625"), 6, False)
    assert (costs.value, costs.scale, costs.is_percent) == (Dec("222358"), 6, False)
    assert (margin.value, margin.scale, margin.is_percent) == (Dec("10.92"), -2, True)


@pytest.mark.criterion("C8")
def test_percent_inside_the_cell_and_split_after_parentheses(load, row):
    # oracle: hand-read from fixtures/split_parens.html: "(1.4" + ")%", "(0.3)%", "5.20%"
    table = parse_table(load("split_parens.html"))
    cells = [row(table, name).cells[2] for name in ("Other, net", "Interest", "Dividends")]
    assert [(c.value, c.scale, c.is_percent) for c in cells] == [
        (Dec("-1.4"), -2, True),
        (Dec("-0.3"), -2, True),
        (Dec("5.20"), -2, True),
    ]


@pytest.mark.criterion("C8")
def test_percent_column_next_to_dollar_columns(load, row):
    # oracle: hand-read from fixtures/msft_summary.html; 16% sits beside $245,122
    cells = row(parse_table(load("msft_summary.html")), "Revenue").cells
    assert [(c.value, c.is_percent) for c in cells] == [
        (Dec("245122"), False),
        (Dec("211915"), False),
        (Dec("16"), True),
    ]


@pytest.mark.criterion("C8")
def test_percent_from_a_column_header_or_a_row_label(mini):
    # oracle: requirement 9; no % is printed beside these numbers
    html = mini(["2024", "% of Net Sales"], [("Food", ["$ 400", "37.5"])])
    by_column = parse_table(html).rows[0].cells
    assert by_column[0].is_percent is False
    assert (by_column[1].value, by_column[1].is_percent) == (Dec("37.5"), True)
    rows = [("Sales", ["$ 400"]), ("Operating margin (%)", ["12.5"])]
    by_row = parse_table(mini(["2024"], rows), caption="(in millions)")
    margin = by_row.rows[1].cells[0]
    assert (margin.value, margin.scale, margin.is_percent) == (Dec("12.5"), -2, True)
    assert by_row.rows[0].cells[0].scale == 6


@pytest.mark.criterion("C8")
@given(
    amount=st.decimals(min_value=0, max_value=1000, places=2),
    negative=st.booleans(),
    form=st.sampled_from(["same", "next"]),
    note=st.sampled_from(["", "(in millions)"]),
)
def test_where_the_percent_sign_sits_changes_nothing(amount, negative, form, note):
    # oracle: closed form; the printed number, scale -2, whichever cell holds the %
    number = f"({amount:.2f})" if negative else f"{amount:.2f}"
    cells = f"<td>{number}%</td><td></td>" if form == "same" else f"<td>{number}</td><td>%</td>"
    html = (
        f'<table><tr><td>{note}</td><td colspan="2">2024</td></tr>'
        f"<tr><td>Rate</td>{cells}</tr></table>"
    )
    cell = parse_table(html).rows[0].cells[0]
    assert cell.value == (-amount if negative else amount)
    assert (cell.scale, cell.is_percent) == (-2, True)
    assert cell.scaled_value == cell.value / 100
