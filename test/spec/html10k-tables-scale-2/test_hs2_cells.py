"""C4-C6: what a note does not reach. Spec: docs/specs/html10k-tables-scale-2.md."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table


def _grouped(number: int) -> str:
    return f"{number:,}"


# ---- C4: a number too large for its note ---------------------------------------------------------


@pytest.mark.criterion("C4")
def test_full_counts_under_a_note_of_millions(kit):
    # oracle: by hand, a statement of equity's shape. 2,442,676,580 of millions is more than two
    # hundred trillion a filer never reports, so it is whole; the dollars beside it keep the note.
    html = kit.mini(
        ["Common", "Treasury", "Total"],
        [
            ("Balance at December 31", ["2,442,676,580", "(512,870,523)", "$ 1,234"]),
            ("Purchases", ["—", "(14,912,961)", "(14,931)"]),
        ],
        stub="Millions of dollars, except per-share amounts",
    )
    table = parse_table(html)
    assert kit.scales(kit.row(table, "Balance at December 31")) == [0, 0, 6]
    assert kit.row(table, "Purchases").cells[2].scale == 6


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    ("stub", "label", "printed", "expected"),
    [
        ("(In millions)", "Awards granted", "100,000,000", 0),  # the bound itself
        ("(In billions)", "Awards granted", "250,000,000", 0),
        ("(In millions)", "Common shares issued", "3,800,000,000", 0),
        ("(In millions)", "Debt (in thousands)", "250,000,000", 3),  # a stated label wins
        ("(In millions)", "Awards granted", "250,000,000.25", 6),  # a whole number only
    ],
)
def test_the_bound_and_what_outranks_it(kit, stub, label, printed, expected):
    # oracle: requirement 4, by hand. The control row is the rule firing in the same table.
    rows = [("Balance", ["999,999,999"]), (label, [printed])]
    table = parse_table(kit.mini(["2024"], rows, stub=stub))
    assert table.rows[0].cells[0].scale == 0
    assert table.rows[1].cells[0].scale == expected


@pytest.mark.metamorphic
@pytest.mark.criterion("C4")
@given(
    number=st.integers(min_value=100_000_000, max_value=9_000_000_000),
    below=st.integers(min_value=1, max_value=99_999_999),
    stub=st.sampled_from(["(In millions)", "Millions of dollars"]),
)
def test_a_cell_is_whole_only_from_the_bound_up(kit, number, below, stub):
    # oracle: closed form: under 10^8 the note stands, at or above it the cell is in units. A note
    # of thousands, for which the same number is a plausible amount, is not touched.
    rows = [("Line", [_grouped(number), _grouped(below)])]
    assert kit.scales(parse_table(kit.mini(["A", "B"], rows, stub=stub)).rows[0]) == [0, 6]
    thousands = parse_table(kit.mini(["A", "B"], rows, stub="(In thousands)"))
    assert kit.scales(thousands.rows[0]) == [3, 3]


# ---- C5: cents under a note of thousands or millions ---------------------------------------------


@pytest.mark.criterion("C5")
def test_a_price_with_no_per_share_wording(kit):
    # oracle: by hand, the wordings of an option table. Nothing in the label says "per share";
    # the two decimals in a row with "$" do. The row of dollars below it keeps the note.
    html = kit.mini(
        ["2024", "2023"],
        [
            ("Closing price of our common stock on grant date", ["$300.30", "$235.97"]),
            ("Service-based", ["$448.69", "$377.72"]),
            ("Unrecognized compensation", ["$412", "$388"]),
        ],
        stub="(In millions)",
    )
    table = parse_table(html)
    assert kit.scales(kit.row(table, "Closing price of our common stock on grant date")) == [0, 0]
    assert kit.scales(kit.row(table, "Service-based")) == [0, 0]
    assert kit.scales(kit.row(table, "Unrecognized compensation")) == [6, 6]


@pytest.mark.criterion("C5")
def test_a_column_with_a_currency_symbol_reaches_a_row_with_none(kit):
    # oracle: by hand. The second column prints "$" once, in its first row; 62.46 is in that
    # column and its row prints no symbol. The first column has one decimal and no "$".
    html = kit.mini(
        ["Shares", "Weighted average option price"],
        [("Options outstanding", ["71.0", "$94.40"]), ("Exercised", ["(7.0)", "62.46"])],
        stub="(In millions)",
    )
    table = parse_table(html)
    assert kit.scales(kit.row(table, "Options outstanding")) == [6, 0]
    assert kit.scales(kit.row(table, "Exercised")) == [6, 0]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("stub", "label", "printed", "expected"),
    [
        ("(In billions)", "Intrinsic value", "$1.34", 9),  # billions print cents
        ("(In millions)", "Intrinsic value (billions)", "$1.34", 9),  # a stated label wins
        ("(In millions)", "Revenue", "$1,234.5", 6),  # one decimal
        ("(In millions)", "Revenue", "$12.345", 6),  # three decimals
        ("(In millions)", "Revenue", "$1,234", 6),  # none
        ("(In millions)", "Past-due amounts", "1.20", 6),  # no currency symbol on its line
    ],
)
def test_what_keeps_its_scale_beside_a_price(kit, stub, label, printed, expected):
    # oracle: requirement 5, by hand. The control is the rule firing under millions, so a parser
    # that never reads a price cannot pass. The cell under test has no price in its own column.
    price = kit.mini(["2024"], [("Closing price", ["$300.30"])], stub="(In millions)")
    assert parse_table(price).rows[0].cells[0].scale == 0
    table = parse_table(kit.mini(["2024"], [(label, [printed])], stub=stub))
    assert table.rows[0].cells[0].scale == expected


@pytest.mark.metamorphic
@pytest.mark.criterion("C5")
@given(
    dollars=st.integers(min_value=0, max_value=9_999),
    cents=st.integers(min_value=0, max_value=99),
    stub=st.sampled_from([("(In millions)", 6), ("(In thousands)", 3), ("Millions of dollars", 6)]),
)
def test_the_cents_decide_and_a_percent_is_still_a_percent(kit, dollars, cents, stub):
    # oracle: closed form: two printed decimals give scale 0 under thousands or millions, the same
    # digits with no decimals keep the note, and "%" stays a percent, which keeps scale -2.
    note, exponent = stub
    price = f"${dollars:,}.{cents:02d}"
    rows = [("Line", [price, f"${dollars:,}", f"{cents}.50%"])]
    table = parse_table(kit.mini(["A", "B", "C"], rows, stub=note))
    assert kit.scales(table.rows[0]) == [0, exponent, -2]


# ---- C6: a count of things -----------------------------------------------------------------------


@pytest.mark.criterion("C6")
def test_a_row_that_counts_things(kit):
    # oracle: by hand, the wordings of a well-cost table. The dollars of the table keep the note.
    html = kit.mini(
        ["2024", "2023"],
        [
            ("Exploratory well costs", ["$1,648", "$1,627"]),
            ("Number of projects with costs capitalized for more than one year", ["14", "13"]),
            ("Number of receivable contracts", ["209", "276"]),
        ],
        stub="Millions of dollars",
    )
    table = parse_table(html)
    assert kit.scales(kit.row(table, "Exploratory well costs")) == [6, 6]
    label = "Number of projects with costs capitalized for more than one year"
    assert kit.scales(kit.row(table, label)) == [0, 0]
    assert kit.scales(kit.row(table, "Number of receivable contracts")) == [0, 0]


@pytest.mark.criterion("C6")
def test_a_column_that_counts_things(kit):
    # oracle: by hand: the lowest header of the second column names the count, and the first column
    # of the same rows is money.
    html = kit.mini(
        ["Amount", "Number of wells"],
        [("2000-2009", ["$263", "14"]), ("2010-2014", ["418", "49"])],
        stub="Millions of dollars",
    )
    table = parse_table(html)
    assert kit.scales(kit.row(table, "2000-2009")) == [6, 0]
    assert kit.scales(kit.row(table, "2010-2014")) == [6, 0]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(
    ("label", "printed", "expected"),
    [
        ("Number of shares repurchased", "120", 6),  # shares keep their parent rule
        ("Number of common shares outstanding", "1,810", 6),
        ("Number of options granted", "12", 6),
        ("Number of securities to be issued", "12", 6),
        ("Number of RSUs vested", "4", 6),
        ("Number of stores (in thousands)", "120", 3),  # a stated scale wins
        ("Number of projects", "$88", 6),  # a currency symbol is money
    ],
)
def test_what_keeps_its_scale_beside_a_count(kit, label, printed, expected):
    # oracle: requirement 6, by hand. The control row is the rule firing in the same table.
    rows = [("Number of plants", ["15"]), (label, [printed])]
    table = parse_table(kit.mini(["2024"], rows, stub="(In millions)"))
    assert kit.row(table, "Number of plants").cells[0].scale == 0
    assert kit.row(table, label).cells[0].scale == expected


@pytest.mark.metamorphic
@pytest.mark.criterion("C6")
@given(
    noun=st.sampled_from(["projects", "wells", "plants", "employees", "claims", "contracts"]),
    count=st.integers(min_value=1, max_value=99_999),
    stub=st.sampled_from(["(In millions)", "(In thousands)", "Millions of dollars"]),
)
def test_the_noun_counted_changes_nothing(kit, noun, count, stub):
    # oracle: closed form: any noun that is not a share or an award gives whole numbers, and a
    # share row with the same digits keeps the note's power of ten.
    rows = [(f"Number of {noun}", [_grouped(count)]), ("Number of shares", [_grouped(count)])]
    table = parse_table(kit.mini(["2024"], rows, stub=stub))
    assert table.rows[0].cells[0].scale == 0
    assert table.rows[1].cells[0].scale == (3 if "thousands" in stub else 6)
