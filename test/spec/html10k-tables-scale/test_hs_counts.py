"""C6-C10: which cells count shares. Spec: docs/specs/html10k-tables-scale.md."""

from __future__ import annotations

from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table, parse_tables

SHARE_NOTE = "(in millions, except share and per share amounts)"
DOLLAR_NOTE = "(Dollars in millions, except otherwise stated)"


# ---- C6: what an except list reaches ---------------------------------------------------------


@pytest.mark.criterion("C6")
def test_except_shares_reaches_rows_under_a_share_group(load, row, scales):
    # oracle: hand-read from fixtures/tmus_eps.html. "Basic" prints twice: under "Earnings per
    # share" in dollars, and under "Weighted-average shares outstanding", a full count.
    table = parse_table(load("tmus_eps.html"))
    assert table.scale == 6
    assert scales(row(table, "Net income")) == [6, 6, 6]
    assert scales(row(table, "Basic", 0)) == [0, 0, 0]
    assert scales(row(table, "Basic", 1)) == [0, 0, 0]
    assert scales(row(table, "Diluted", 1)) == [0, 0, 0]


@pytest.mark.criterion("C6")
def test_a_dilution_row_counts_shares(mini, row, scales):
    # oracle: wording of the AAPL and TMUS EPS tables; dilutive securities are counted in shares,
    # by their own label or by a group row, and a dollar row beside them stays in millions
    rows = [
        ("Net income", ["$ 11,339"]),
        ("Effect of dilutive share-based awards", ["68,316"]),
        ("Weighted-average diluted shares", ["15,812,547"]),
    ]
    caption = "(net income in millions and shares in thousands)"
    table = parse_table(mini(["2023"], rows), caption=caption)
    assert scales(row(table, "Net income")) == [6]
    assert scales(row(table, "Effect of dilutive share-based awards")) == [3]
    assert scales(row(table, "Weighted-average diluted shares")) == [3]
    grouped = [
        ("Net income", ["$ 11,339"]),
        ("Effect of dilutive securities:", [""]),
        ("Outstanding stock options and unvested stock awards", ["25,652"]),
    ]
    note = "(in millions, except shares and per share amounts)"
    other = parse_table(mini(["2024"], grouped), caption=note)
    assert scales(row(other, "Net income")) == [6]
    assert scales(row(other, "Outstanding stock options and unvested stock awards")) == [0]


@pytest.mark.criterion("C6")
def test_except_list_with_a_singular_term(mini, row, scales):
    # oracle: wording of the V FY2024 10-K; the header says "Conversion Rate", in the singular
    html = mini(
        ["Shares Outstanding", "Conversion Rate Into Class A Common Stock"],
        [("Series A preferred stock", ["1", "100.0000"])],
    )
    table = parse_table(html, caption="(in millions, except conversion rate)")
    assert scales(row(table, "Series A preferred stock")) == [6, 0]


@pytest.mark.criterion("C6")
def test_an_except_list_in_a_column_level_note(row, scales):
    # oracle: layout of the V FY2024 10-K; the note spans the columns in a row of its own, so it
    # reaches the cells as a column header, and its except list reaches them too
    html = (
        "<table>"
        "<tr><td></td><td>Shares Outstanding</td><td>Conversion Rate</td></tr>"
        '<tr><td></td><td colspan="2">(in millions, except conversion rate)</td></tr>'
        "<tr><td>Series A preferred stock</td><td>1</td><td>100.0000</td></tr>"
        "</table>"
    )
    table = parse_table(html)
    assert scales(row(table, "Series A preferred stock")) == [6, 0]


@pytest.mark.criterion("C6")
def test_a_share_word_that_is_no_count(mini, row, scales):
    # oracle: wording of the ABT and GOOGL statements. "Share-based compensation" is dollars, and
    # "par value per share" in an except list names a per-share figure, not this row.
    rows = [
        ("Share-based compensation", ["673"]),
        ("Weighted-average shares", ["1,750,000"]),
    ]
    html = mini(["2024"], rows)
    table = parse_table(html, caption="(in millions except shares and per share data)")
    assert scales(row(table, "Share-based compensation")) == [6]
    assert scales(row(table, "Weighted-average shares")) == [0]
    separate = parse_table(html, caption="(dollars in millions, shares in thousands)")
    assert scales(row(separate, "Share-based compensation")) == [6]
    assert scales(row(separate, "Weighted-average shares")) == [3]
    stock = "Convertible preferred stock, $0.001 par value per share, none issued"
    other = parse_table(
        mini(["2024"], [(stock, ["1,234"])]),
        caption="(in millions, except par value per share amounts)",
    )
    assert scales(row(other, stock)) == [6]


@pytest.mark.criterion("C6")
@given(
    term=st.sampled_from(["conversion rate", "unit price", "exchange rate", "store count"]),
    plural=st.booleans(),
)
def test_an_except_term_matches_singular_and_plural(mini, term, plural):
    # oracle: closed form; the term names the column, in either number
    stem = term.replace(" count", "")
    header = f"{stem.title()}{'s' if plural else ''}"
    html = mini(["Sales", header], [("Series A", ["12", "34"])])
    table = parse_table(html, caption=f"(in millions, except {term})")
    assert [c.scale for c in table.rows[0].cells] == [6, 0]


# ---- C7: a column header decides between a count and money -----------------------------------


@pytest.mark.criterion("C7")
def test_equity_columns_decide_over_row_labels(load, row, scales):
    # oracle: hand-read from fixtures/tmus_equity.html. The first two columns count shares, the
    # next two hold dollars in millions, whatever the row label says.
    table = parse_table(load("tmus_equity.html"))
    assert table.scale == 6
    assert scales(row(table, "Balance as of December 31, 2021"))[:3] == [0, 0, 6]
    label = "Shares withheld related to net share settlement of stock awards and stock options"
    assert row(table, label).cells[0].scale == 0
    assert row(table, label).cells[3].scale == 6
    repurchases = row(table, "Repurchases of common stock")
    assert [repurchases.cells[i].scale for i in (0, 1, 2)] == [0, 0, 6]


@pytest.mark.criterion("C7")
def test_the_lowest_header_decides(load, row, scales):
    # oracle: hand-read from fixtures/jnj_treasury.html. "Shares" and "Amount" sit under "Treasury
    # Stock"; the row "Kenvue share exchange" holds shares in one column and dollars in the other.
    table = parse_table(load("jnj_treasury.html"))
    assert table.scale == 6
    assert scales(row(table, "Balance at January 2, 2022")) == [3, 6]
    assert scales(row(table, "Kenvue share exchange (Note 21)")) == [3, 6]


@pytest.mark.criterion("C7")
def test_an_upper_header_about_money_does_not_decide(row, scales):
    # oracle: layout of the NFLX equity statement, read by hand; "Shares" sits under a header that
    # names capital, and "Amount" beside it holds thousands of dollars even on a row about shares
    html = (
        "<table>"
        '<tr><td></td><td colspan="2">Common Stock and Additional Paid-in Capital</td></tr>'
        "<tr><td></td><td>Shares</td><td>Amount</td></tr>"
        "<tr><td>Balances as of December 31, 2021</td><td>443,963,107</td><td>$ 4,024,561</td></tr>"
        "<tr><td>Shares issued under stock plans</td><td>1,383,669</td><td>37,588</td></tr>"
        "</table>"
    )
    table = parse_table(html, caption="(in thousands, except share data)")
    assert scales(row(table, "Balances as of December 31, 2021")) == [0, 3]
    assert scales(row(table, "Shares issued under stock plans")) == [0, 3]


@pytest.mark.criterion("C7")
@given(
    money=st.sampled_from(["Amount", "Treasury Shares at Cost", "Par Value", "Retained Earnings"]),
    note=st.sampled_from(
        ["(dollars in millions, shares in thousands)", "(in millions, except share data)"]
    ),
)
def test_a_money_column_is_never_a_share_column(mini, money, note):
    # oracle: closed form; the row says shares, the column says money, and the column decides
    rows = [("Shares withheld for taxes", ["(1,900)", "(243)"])]
    table = parse_table(mini(["Shares", money], rows), caption=note)
    assert table.rows[0].cells[1].scale == 6


# ---- C8: a label that prints a number of shares ----------------------------------------------


@pytest.mark.criterion("C8")
def test_a_label_that_prints_the_count_holds_dollars(mini, row, scales):
    # oracle: wording of the DIS, JNJ and XOM balance sheets. The count is in the label, so the
    # number beside it is the dollar value, under a note that excepts share data.
    labels = [
        "Common stock, $0.01 par value, Authorized – 4.6 billion shares",
        "Treasury stock, at cost, 47 million shares at September 28, 2024",
        "Less: common stock held in treasury (712,921,000 shares and 712,765,000 shares)",
        "Common stock held in treasury (3,793 million shares in 2024)",
    ]
    rows = [(label, ["(3,919)"]) for label in labels]
    table = parse_table(mini(["2024"], rows), caption="(in millions, except share data)")
    assert [scales(row(table, label)) for label in labels] == [[6], [6], [6], [6]]


@pytest.mark.criterion("C8")
@given(
    number=st.integers(min_value=1, max_value=999_999_999),
    unit=st.sampled_from(["", " million", " billion", " thousand"]),
    form=st.sampled_from(["Treasury stock ({} shares)", "Stock held in treasury, {} shares"]),
)
def test_a_printed_count_of_any_size(mini, number, unit, form):
    # oracle: closed form; the printed count in the label makes the row a dollar row
    label = form.format(f"{number:,}{unit}")
    html = mini(["2024"], [(label, ["5,000"])])
    table = parse_table(html, caption="(in millions, except share data)")
    assert table.rows[0].cells[0].scale == 6


# ---- C9: a count too large for the note ------------------------------------------------------


@pytest.mark.criterion("C9")
def test_a_share_column_of_full_numbers_is_in_units(load, doc, row):
    # oracle: hand-read from fixtures/ba_options.html; the standing note says millions, and
    # 792,662 million option shares could not exist. The price column is in dollars.
    table = parse_tables(doc(DOLLAR_NOTE, "Prose.", load("ba_options.html")))[0]
    assert table.scale == 6
    assert row(table, "Outstanding at beginning of year").cells[0].scale == 0
    assert row(table, "Exercised").cells[0].scale == 0
    assert row(table, "Exercised").cells[1].scale == 0


@pytest.mark.criterion("C9")
def test_a_group_of_units_is_a_count(load, doc, row, scales):
    # oracle: hand-read from fixtures/ba_rsus.html; "Number of units:" scopes the rows below it
    table = parse_tables(doc(DOLLAR_NOTE, "Prose.", load("ba_rsus.html")))[0]
    assert scales(row(table, "Outstanding at beginning of year")) == [0, 0]
    assert scales(row(table, "Forfeited")) == [0, 0]


@pytest.mark.criterion("C9")
def test_a_share_row_beside_big_dollars_stays_in_millions(mini, row, scales):
    # oracle: wording of the HD, MSFT and WMT statements; dollars above 100,000 in the column are
    # not share counts, so "999" is 999 million shares
    rows = [
        ("Total assets", ["$ 352,583"]),
        ("Weighted-average common shares", ["999"]),
        ("Weighted-average diluted shares", ["1,024,500"]),
    ]
    small = rows[:2]
    table = parse_table(mini(["2024"], small), caption="(in millions, except per share data)")
    assert scales(row(table, "Weighted-average common shares")) == [6]
    big = parse_table(mini(["2024"], rows), caption="(in millions, except per share data)")
    assert scales(row(big, "Weighted-average common shares")) == [0]


@pytest.mark.criterion("C9")
@given(
    big=st.integers(min_value=100_000, max_value=9_999_999_999),
    small=st.integers(min_value=1, max_value=99_999),
)
def test_the_largest_count_in_the_column_decides(mini, big, small):
    # oracle: closed form; 100,000 million shares is more than any filer has outstanding, so a
    # share column reaching that is not in millions. Below it the note stands.
    above = [("Shares granted", [f"{big:,}"]), ("Shares forfeited", [f"{small:,}"])]
    below = [("Shares granted", [f"{small:,}"]), ("Shares forfeited", [f"{small:,}"])]
    caption = "(dollars in millions)"
    assert [r.cells[0].scale for r in parse_table(mini(["2024"], above), caption=caption).rows] == [
        0,
        0,
    ]
    assert [r.cells[0].scale for r in parse_table(mini(["2024"], below), caption=caption).rows] == [
        6,
        6,
    ]


# ---- C10: a figure per award or per option ---------------------------------------------------


@pytest.mark.criterion("C10")
def test_a_figure_per_stock_award(mini, row, scales):
    # oracle: wording of the TMUS FY2024 10-K; dollars with cents per award, in a table in millions
    rows = [
        ("Stock-based compensation expense", ["$ 649"]),
        ("Weighted-average fair value per stock award granted", ["$ 162.99"]),
    ]
    table = parse_table(mini(["2024"], rows), caption="(in millions, except per share amounts)")
    assert scales(row(table, "Stock-based compensation expense")) == [6]
    assert scales(row(table, "Weighted-average fair value per stock award granted")) == [0]


@pytest.mark.criterion("C10")
@given(
    noun=st.sampled_from(["stock award", "award", "option", "stock option", "RSU award"]),
    cents=st.integers(min_value=1, max_value=99),
)
def test_a_figure_per_any_award(mini, noun, cents):
    # oracle: closed form; the same statement with any award noun
    label = f"Fair value per {noun}"
    table = parse_table(mini(["2024"], [(label, [f"$ 12.{cents:02d}"])]), caption="(in millions)")
    cell = table.rows[0].cells[0]
    assert cell.scale == 0
    assert cell.value == Dec(f"12.{cents:02d}")
