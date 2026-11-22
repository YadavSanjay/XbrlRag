"""C11-C13: percent cells among money cells. Spec: docs/specs/html10k-tables-scale.md."""

from __future__ import annotations

from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table

# ---- C11: a label that says percent in words -------------------------------------------------


@pytest.mark.criterion("C11")
def test_percent_in_words_in_a_label(mini, row, scales):
    # oracle: wording of the XOM FY2024 10-K; "(percent)" is "(%)" in words. Dollar rows in the
    # same table stay in millions.
    rows = [
        ("Discount rate (percent)", ["5.30", "5.60"]),
        ("Long-term rate of return on funded assets (percent)", ["6.80", "5.20"]),
        ("Service cost", ["499", "466"]),
    ]
    html = mini(["2024", "2023"], rows, stub="(millions of dollars, except where stated otherwise)")
    table = parse_table(html)
    assert scales(row(table, "Discount rate (percent)")) == [-2, -2]
    assert row(table, "Discount rate (percent)").cells[0].is_percent is True
    assert scales(row(table, "Long-term rate of return on funded assets (percent)")) == [-2, -2]
    assert scales(row(table, "Service cost")) == [6, 6]


@pytest.mark.criterion("C11")
@given(
    stem=st.sampled_from(["Discount rate", "Weighted-average discount rate", "Growth"]),
    word=st.sampled_from(["(percent)", "(Percent)", "(percentage)"]),
    value=st.integers(min_value=1, max_value=999),
)
def test_the_word_form_in_any_label(mini, stem, word, value):
    # oracle: closed form; the value is printed as is, with scale -2
    number = f"{value / 100:.2f}"
    table = parse_table(mini(["2024"], [(f"{stem} {word}", [number])]), caption="(in millions)")
    cell = table.rows[0].cells[0]
    assert (cell.value, cell.scale, cell.is_percent) == (Dec(number), -2, True)


# ---- C12: a row that prints % once, and the rows between two such rows ------------------------


@pytest.mark.criterion("C12")
def test_a_row_prints_percent_in_its_first_cell_only(load, row, scales):
    # oracle: hand-read from fixtures/jnj_assumptions.html; each row prints % in the first column
    # and nowhere else, and every number in it is a rate
    table = parse_table(load("jnj_assumptions.html"))
    assert scales(row(table, "Service cost discount rate")) == [-2, -2, -2]
    assert scales(row(table, "Interest cost discount rate")) == [-2, -2, -2]
    cell = row(table, "Service cost discount rate").cells[2]
    assert (cell.value, cell.is_percent) == (Dec("2.46"), True)


@pytest.mark.criterion("C12")
def test_rows_between_two_percent_rows(load, row, scales):
    # oracle: hand-read from fixtures/jnj_tax_rates.html. The dollar rows above print $ in the
    # first column, so the column is no rate column; the rates run from "U.S. statutory rate"
    # to "Effective Rate", which print %.
    table = parse_table(load("jnj_tax_rates.html"))
    assert table.scale == 6
    assert scales(row(table, "International")) == [6, 6, 6]
    assert scales(row(table, "U.S. statutory rate")) == [-2, -2, -2]
    assert scales(row(table, "International operations")) == [-2, -2, -2]
    assert scales(row(table, "U.S. state taxes")) == [-2, -2, -2]
    assert scales(row(table, "Effective Rate")) == [-2, -2, -2]
    settlements = row(table, "U.S. tax settlements")
    assert [settlements.cells[0].scale, settlements.cells[1].scale] == [-2, -2]
    assert settlements.cells[2].is_nil is True


@pytest.mark.criterion("C12")
def test_amounts_beside_a_percent_stay_money(mini, row, scales):
    # oracle: wording of the DIS and V tables; a whole number beside a percent is an amount, and a
    # number with a comma never is a rate, however the percent is printed
    rows = [
        ("Services", ["$ 81,841", "90%"]),
        ("Products", ["9,520", "10%"]),
        ("State taxes", ["244", "1.2%"]),
        ("Other taxes", ["1,244", "0.8%"]),
    ]
    table = parse_table(mini(["2024", "Mix"], rows), caption="(in millions)")
    assert scales(row(table, "Products")) == [6, -2]
    assert scales(row(table, "State taxes")) == [6, -2]
    assert scales(row(table, "Other taxes")) == [6, -2]
    rates = [
        ("Discount rate", ["4.39%", "4.85"]),
        ("Salary growth", ["3.70", "3.71"]),
        ("Return on assets", ["7.25%", "7.21"]),
    ]
    assert scales(row(parse_table(mini(["2024", "2023"], rates)), "Salary growth")) == [-2, -2]


@pytest.mark.criterion("C12")
@given(
    first=st.decimals(min_value=Dec("0.1"), max_value=Dec("30"), places=2),
    other=st.lists(
        st.decimals(min_value=Dec("0.1"), max_value=Dec("30"), places=2), min_size=2, max_size=5
    ),
)
def test_every_number_of_a_percent_row_is_a_rate(mini, first, other):
    # oracle: closed form; the first cell prints %, the rest print the same decimals and no symbol
    cells = [f"{first}%", *[str(v) for v in other]]
    head = [str(2024 - i) for i in range(len(cells))]
    table = parse_table(mini(head, [("Discount rate", cells)]), caption="(dollars in millions)")
    row_ = table.rows[0]
    assert [c.scale for c in row_.cells] == [-2] * len(cells)
    assert [c.value for c in row_.cells] == [first, *other]


# ---- C13: one percent does not make a column of dollars a rate column -------------------------


@pytest.mark.criterion("C13")
def test_a_rate_at_the_foot_of_a_column_of_dollars(load, row, scales):
    # oracle: hand-read from fixtures/xom_tax.html. The filer prints no $, so the old rule took
    # every column for a rate column; the comma-grouped numbers cannot be rates.
    table = parse_table(load("xom_tax.html"))
    assert scales(row(table, "United States")) == [6, 6, 6]
    assert scales(row(table, "Total income tax expense (credit)")) == [6, 6, 6]
    assert scales(row(table, "Other")) == [6, 6, 6]
    assert scales(row(table, "Effective income tax rate")) == [-2, -2, -2]
    assert row(table, "Other").cells[0].is_percent is False


@pytest.mark.criterion("C13")
@pytest.mark.metamorphic
def test_a_true_rate_column_is_untouched(mini, row, scales):
    # oracle: the base rule of html10k-tables requirement 9: a column of small numbers that prints
    # % on its first and last rows is a rate column. A number of 1,000 or more in it ends that.
    rates = [("Statutory", ["21.0%"]), ("State", ["1.4"]), ("Effective", ["24.2%"])]
    table = parse_table(mini(["2024"], rates))
    assert scales(row(table, "State")) == [-2]
    mixed = [("Statutory", ["21.0%"]), ("Income", ["1,400"]), ("Effective", ["24%"])]
    other = parse_table(mini(["2024"], mixed), caption="(in millions)")
    assert scales(row(other, "Income")) == [6]


@pytest.mark.criterion("C13")
@given(
    amount=st.integers(min_value=1_000, max_value=999_999),
    rate=st.integers(min_value=1, max_value=60),
)
def test_a_number_of_a_thousand_or_more_is_no_rate(mini, amount, rate):
    # oracle: closed form; the amount keeps the note's scale, the printed percent keeps -2
    rows = [("Income", [f"{amount:,}"]), ("Effective tax rate", [f"{rate}%"])]
    table = parse_table(mini(["2024"], rows), caption="(millions of dollars)")
    assert [r.cells[0].scale for r in table.rows] == [6, -2]
