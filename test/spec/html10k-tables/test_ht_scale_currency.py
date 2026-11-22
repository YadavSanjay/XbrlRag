"""C3 (scale) and C4 (currency). Spec: docs/specs/html10k-tables.md."""

from __future__ import annotations

from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table

COST_NOTE = "(amounts in millions, except per share data)"


def scales(row):
    return [c.scale for c in row.cells]


@pytest.mark.criterion("C3")
def test_caption_note_with_per_share_and_share_group_rows(load, row):
    # oracle: hand-read from fixtures/cost_income_statement.html. Dollars follow the caption;
    # Basic under the per-share group row is in dollars; Basic under "(000's)" is in thousands.
    table = parse_table(load("cost_income_statement.html"), caption=COST_NOTE)
    assert table.scale == 6
    assert scales(row(table, "Net sales")) == [6, 6, 6]
    assert scales(row(table, "Basic", 0)) == [0, 0, 0]
    assert scales(row(table, "Diluted", 0)) == [0, 0, 0]
    assert scales(row(table, "Basic", 1)) == [3, 3, 3]
    assert scales(row(table, "Diluted", 1)) == [3, 3, 3]


@pytest.mark.criterion("C3")
def test_scale_in_the_stub_cell_and_a_per_share_label(load, row):
    # oracle: hand-read from fixtures/unp_income.html. "Share and Per Share (Note 8):" does not
    # make the share count per-share: 607.6 stays in millions.
    table = parse_table(load("unp_income.html"))
    assert table.scale == 6
    assert scales(row(table, "Freight revenues")) == [6, 6, 6]
    assert scales(row(table, "Earnings per share - basic")) == [0, 0, 0]
    assert scales(row(table, "Weighted average number of shares - basic")) == [6, 6, 6]


@pytest.mark.criterion("C3")
def test_scale_in_a_second_header_row(load):
    # oracle: hand-read from fixtures/wmt_swaps.html; "(Amounts in millions)" is in row 1
    table = parse_table(load("wmt_swaps.html"))
    assert table.scale == 6
    assert scales(table.rows[0]) == [6, 6, 6, 6]


@pytest.mark.criterion("C3")
def test_bare_word_in_the_stub(load):
    # oracle: hand-read from fixtures/unp_equity.html; the stub says only "Millions"
    assert parse_table(load("unp_equity.html")).scale == 6


@pytest.mark.criterion("C3")
def test_note_with_per_share_row_in_the_table(load, row):
    # oracle: hand-read from fixtures/msft_summary.html
    table = parse_table(load("msft_summary.html"))
    assert table.scale == 6
    assert row(table, "Revenue").cells[0].scale == 6
    assert row(table, "Diluted earnings per share").cells[0].scale == 0


@pytest.mark.criterion("C3")
def test_precedence_of_table_note_caption_and_standing_note(mini):
    # oracle: requirement 4; the first that states a scale wins
    html = mini(["2024"], [("Sales", ["10"])], stub="(in thousands)")
    plain = mini(["2024"], [("Sales", ["10"])])
    assert parse_table(html, caption="(in millions)", scale_note="(in billions)").scale == 3
    assert parse_table(plain, caption="(in millions)", scale_note="(in billions)").scale == 6
    assert parse_table(plain, scale_note="(in billions)").scale == 9
    assert parse_table(plain).scale == 0


@pytest.mark.criterion("C3")
def test_sentence_caption_and_prose_with_a_number(mini):
    # oracle: requirement 4; a sentence ending in a note states it, a number in prose does not
    html = mini(["2024"], [("Gold Star", ["300"])])
    sentence = "Our membership was made up of the following (in thousands):"
    assert parse_table(html, caption=sentence).scale == 3
    assert parse_table(html, caption="Capital expenditures were $4,710 million in 2024.").scale == 0


@pytest.mark.criterion("C3")
def test_column_headers_with_their_own_scale(mini, row):
    # oracle: requirement 4 rule 4; each header names its column's scale
    html = mini(["Sales (in millions)", "Units (in thousands)"], [("Widgets", ["12", "340"])])
    table = parse_table(html)
    assert scales(row(table, "Widgets")) == [6, 3]


@pytest.mark.criterion("C3")
def test_row_label_with_its_own_scale(mini, row):
    # oracle: requirement 4 rule 3; the label outranks the table note
    rows = [("Sales", ["12"]), ("Units sold (in thousands)", ["340"])]
    html = mini(["2024"], rows, stub="(in millions)")
    table = parse_table(html)
    assert scales(row(table, "Sales")) == [6]
    assert scales(row(table, "Units sold (in thousands)")) == [3]


@pytest.mark.criterion("C3")
def test_separate_scale_for_shares(mini, row):
    # oracle: requirement 4 rule 5; the note names two scales
    html = mini(["2024"], [("Net income", ["7,367"]), ("Weighted-average shares", ["443,914"])])
    table = parse_table(html, caption="(dollars in millions, shares in thousands)")
    assert scales(row(table, "Net income")) == [6]
    assert scales(row(table, "Weighted-average shares")) == [3]


@pytest.mark.criterion("C3")
def test_except_list_names_what_the_row_counts(mini, row):
    # oracle: requirement 4 rule 6; wording from the Costco FY2024 10-K
    caption = "(amounts in millions, except share, per share, and warehouse count data)"
    html = mini(
        ["2024"],
        [
            ("Net sales", ["249,625"]),
            ("Warehouses in operation", ["890"]),
            ("Shares issued", ["443,126,000"]),
        ],
    )
    table = parse_table(html, caption=caption)
    assert scales(row(table, "Net sales")) == [6]
    assert scales(row(table, "Warehouses in operation")) == [0]
    assert scales(row(table, "Shares issued")) == [0]


@pytest.mark.criterion("C3")
def test_scaled_value_is_value_times_the_scale(load, row):
    # oracle: closed form; (169) in millions is -169 x 10^6, $16.59 per share stays 16.59
    table = parse_table(load("cost_income_statement.html"), caption=COST_NOTE)
    assert row(table, "Interest expense").cells[0].scaled_value == Dec("-169000000")
    assert row(table, "Basic", 0).cells[0].scaled_value == Dec("16.59")
    assert row(table, "Basic", 1).cells[0].scaled_value == Dec("443914000")


@pytest.mark.criterion("C3")
@given(
    word=st.sampled_from([("thousands", 3), ("millions", 6), ("billions", 9)]),
    note=st.sampled_from(
        ["(in {})", "(amounts in {})", "(dollars in {})", "($ in {})", "(In {})", "{}"]
    ),
    where=st.sampled_from(["stub", "caption", "scale_note"]),
    number=st.integers(min_value=1, max_value=999_999),
)
def test_every_number_takes_the_stated_scale(mini, word, note, where, number):
    # oracle: closed form; the generated word's power of ten
    name, exponent = word
    text = note.format(name.capitalize() if note == "{}" else name)
    html = mini(["2024"], [("Assets", [f"{number:,}"])], stub=text if where == "stub" else "")
    kwargs = {where: text} if where != "stub" else {}
    table = parse_table(html, **kwargs)
    cell = table.rows[0].cells[0]
    assert table.scale == exponent
    assert cell.scale == exponent
    assert cell.scaled_value == Dec(number) * Dec(10) ** exponent


@pytest.mark.criterion("C4")
def test_symbols_in_split_cells_and_the_table_currency(load, row):
    # oracle: hand-read from fixtures/cost_income_statement.html; "$" prints only on some rows
    table = parse_table(load("cost_income_statement.html"))
    assert table.currency == "USD"
    assert [c.currency for c in row(table, "Net sales").cells] == ["USD", "USD", "USD"]
    assert [c.currency for c in row(table, "Membership fees").cells] == [None, None, None]


@pytest.mark.criterion("C4")
def test_symbol_inside_the_number_cell(load, row):
    # oracle: hand-read from fixtures/split_parens.html; "($1,006)" holds its own symbol
    table = parse_table(load("split_parens.html"))
    dividends = row(table, "Dividends")
    assert [c.currency for c in dividends.cells] == ["USD", "USD", None]
    assert dividends.cells[1].value == Dec("-1006")


@pytest.mark.criterion("C4")
def test_currency_from_the_caption(mini):
    # oracle: requirement 5; no symbol in the body, so the note decides and cells stay None
    table = parse_table(mini(["2024"], [("Sales", ["1,234"])]), caption="(euros in millions)")
    assert table.currency == "EUR"
    assert table.scale == 6
    assert table.rows[0].cells[0].currency is None


@pytest.mark.criterion("C4")
def test_no_currency(mini):
    # oracle: a table of counts with no symbol and no note states no currency
    table = parse_table(mini(["2024"], [("Warehouses", ["890"])]))
    assert table.currency is None
    assert table.rows[0].cells[0].value == Dec("890")


@pytest.mark.criterion("C4")
@given(
    symbol=st.sampled_from(
        [("$", "USD"), ("US$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("NT$", "TWD")]
    ),
    number=st.integers(min_value=1, max_value=999_999),
    split=st.booleans(),
)
def test_a_symbol_changes_no_value(symbol, number, split):
    # oracle: requirement 5's symbol list; the value is the generated number
    printed, code = symbol
    together = f"<td>{printed} {number:,}</td><td></td>"
    cells = f"<td>{printed}</td><td>{number:,}</td>" if split else together
    html = (
        '<table><tr><td></td><td colspan="2">2024</td></tr>'
        f"<tr><td>Sales</td>{cells}</tr></table>"
    )
    table = parse_table(html)
    cell = table.rows[0].cells[0]
    assert (cell.value, cell.currency, table.currency) == (Dec(number), code, code)
