"""C1-C5: where a scale note sits and what it reaches. Spec: docs/specs/html10k-tables-scale.md."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_table, parse_tables

WORDS = [("thousands", 3), ("millions", 6), ("billions", 9)]
FURNITURE = "Index for Notes to the Consolidated Financial Statements"


# ---- C1: a note in a row above the first row of values --------------------------------------


@pytest.mark.criterion("C1")
def test_note_row_under_title_rows(load, row, scales):
    # oracle: hand-read from fixtures/cat_statement.html. "(Dollars in millions except per share
    # data)" sits in the third row, under two title rows and above the years.
    table = parse_table(load("cat_statement.html"))
    assert table.scale == 6
    assert scales(row(table, "Sales of Machinery, Energy & Transportation")) == [6, 6, 6]
    assert scales(row(table, "Total sales and revenues")) == [6, 6, 6]
    assert scales(row(table, "Profit per common share")) == [0, 0, 0]


@pytest.mark.criterion("C1")
def test_note_in_the_year_row_under_several_title_rows(load, row, scales):
    # oracle: hand-read from fixtures/cat_geography.html. "(Millions of dollars)" is in the stub
    # of the year row, the fourth row of the table.
    table = parse_table(load("cat_geography.html"))
    assert table.scale == 6
    assert scales(row(table, "Inside United States")) == [6, 6]
    assert scales(row(table, "Total")) == [6, 6]


@pytest.mark.criterion("C1")
def test_a_note_row_scopes_no_group(load, row):
    # oracle: fixtures/cat_statement.html. The note says "per share", but it scopes no rows: the
    # dollar row with cents under a per-share note would otherwise be read as a per-share figure.
    table = parse_table(load("cat_statement.html").replace("3,446", "3,446.5"))
    assert row(table, "Revenues of Financial Products").cells[0].scale == 6
    assert row(table, "Revenues of Financial Products").cells[1].scale == 6


@pytest.mark.criterion("C1")
@given(
    titles=st.integers(min_value=1, max_value=4),
    word=st.sampled_from(WORDS),
    form=st.sampled_from(["(Dollars in {} except per share data)", "({} of dollars)", "(in {})"]),
)
def test_title_rows_above_the_note_change_nothing(titles, word, form):
    # oracle: closed form; the stated word's power of ten, however many title rows lie above
    name, exponent = word
    note = form.format(name.capitalize() if form.startswith("({}") else name)
    rows = "".join(f"<tr><td>Statement title {i}</td><td></td></tr>" for i in range(titles))
    html = (
        f"<table>{rows}<tr><td>{note}</td><td></td></tr>"
        "<tr><td></td><td>2024</td></tr><tr><td>Assets</td><td>1,234</td></tr></table>"
    )
    table = parse_table(html)
    assert table.scale == exponent
    assert table.rows[-1].cells[0].scale == exponent


# ---- C2: page furniture and the table that runs over a page ----------------------------------


@pytest.mark.criterion("C2")
def test_a_table_over_a_page_keeps_the_note_of_the_table_before_it(load):
    # oracle: hand-read from fixtures/tmus_pages.html. q0 states "(in millions)" in its stub; q1
    # is its next page, with a page number and a running line between; q2 follows prose.
    tables = parse_tables(load("tmus_pages.html"))
    assert [t.scale for t in tables] == [6, 6, 0]


@pytest.mark.criterion("C2")
def test_the_note_of_the_table_before_outranks_a_standing_note(load):
    # oracle: hand-read from fixtures/jnj_pages.html. p1 follows p0 across a running line, and
    # p0's title says millions; p4 follows p2 across a table that holds only a page number.
    tables = parse_tables(load("jnj_pages.html"))
    assert tables[0].scale == 6
    assert tables[1].scale == 6
    assert tables[2].scale == 6
    assert tables[4].scale == 6


@pytest.mark.criterion("C2")
def test_furniture_is_not_a_caption(load):
    # oracle: hand-read from fixtures/tmus_pages.html; the caption of q0 is the sentence before
    # it, and of q1, whose only neighbours are furniture, nothing.
    tables = parse_tables(load("tmus_pages.html"))
    assert tables[0].caption == "Debt was as follows:"
    assert tables[1].caption == ""


@pytest.mark.criterion("C2")
def test_a_repeated_note_is_not_furniture(doc, mini):
    # oracle: requirement 2; a note printed on every page is the note, however often it prints.
    # The first table runs on across a page, so the third has its note only from the second.
    note = "(in thousands)"
    plain = mini(["2024"], [("Goodwill", ["700"])])
    html = doc(note, plain, note, plain, note, plain, "Prose ends the run.", plain)
    tables = parse_tables(html)
    assert [t.scale for t in tables] == [3, 3, 3, 3]
    assert tables[0].caption == note
    furnished = doc(FURNITURE, FURNITURE, note, plain, FURNITURE, plain)
    assert parse_tables(furnished)[1].caption == ""


@pytest.mark.criterion("C2")
@given(
    copies=st.integers(min_value=3, max_value=6),
    word=st.sampled_from(WORDS),
    number=st.sampled_from(["94", "F-12", "118"]),
    is_prose=st.booleans(),
)
def test_only_prose_ends_a_run_of_tables(doc, mini, copies, word, number, is_prose):
    # oracle: closed form; with furniture between, the second table has the first's scale, with a
    # sentence between, it has none of its own and no standing note
    name, exponent = word
    first = mini(["2024"], [("Cash", ["10"])], stub=f"(in {name})")
    second = mini(["2024"], [("Notes", ["20"])])
    run = [FURNITURE] * (copies - 1)
    between = ["The balance is as follows:"] if is_prose else [number, FURNITURE]
    tables = parse_tables(doc(*run, first, *between, second))
    assert tables[0].scale == exponent
    assert tables[1].scale == (0 if is_prose else exponent)


# ---- C3: what is not a standing note ---------------------------------------------------------


@pytest.mark.criterion("C3")
def test_a_note_with_a_second_sentence_stands_for_nothing(load):
    # oracle: hand-read from fixtures/jnj_pages.html; the billions note describes a chart, and p5
    # counts plaintiffs in units, with nothing above it stating a scale
    tables = parse_tables(load("jnj_pages.html"))
    assert tables[5].scale == 0
    assert tables[5].rows[0].cells[0].scale == 0


@pytest.mark.criterion("C3")
def test_a_numbered_footnote_is_not_a_standing_note(load):
    # oracle: hand-read from fixtures/cat_stock_awards.html; "Amounts are in millions of dollars"
    # closes footnote 1 of the first table and says nothing about the second
    tables = parse_tables(load("cat_stock_awards.html"))
    assert tables[1].scale == 0
    assert [c.scale for c in tables[1].rows[0].cells] == [0, 0]


@pytest.mark.criterion("C3")
@given(
    word=st.sampled_from(WORDS),
    second=st.sampled_from(
        ["Percentages in chart are as a percent to total sales", "Bars show the prior year"]
    ),
)
def test_a_lone_note_stands_and_the_same_note_with_a_sentence_does_not(doc, mini, word, second):
    # oracle: requirement 3; one statement stands, a statement that goes on describes its neighbour
    name, exponent = word
    plain = mini(["2024"], [("Leases", ["600"])])
    lone = parse_tables(doc(f"(Dollars in {name})", "Prose.", plain))
    assert lone[0].scale == exponent
    chart = parse_tables(doc(f"(Dollars in {name}. {second})", "Prose.", plain))
    assert chart[0].scale == 0


# ---- C4: a scale stated by a footnote below the table ----------------------------------------


@pytest.mark.criterion("C4")
def test_a_marked_column_takes_the_scale_of_its_footnote(load, row):
    # oracle: hand-read from fixtures/pep_options.html. "Options(a)" and "Aggregate Intrinsic
    # Value(a)" are in thousands by footnote (a); the price column is per unit; the rest of the
    # filing is in millions by the standing note.
    tables = parse_tables(load("pep_options.html"))
    assert tables[0].scale == 6
    options = tables[1]
    assert row(options, "Granted").cells[0].scale == 3
    assert row(options, "Exercised").cells[0].scale == 3
    closing = row(options, "Outstanding at December 28, 2024")
    assert [c.scale for c in closing.cells] == [3, 0, closing.cells[2].scale, 3]


@pytest.mark.criterion("C4")
def test_a_marked_row_takes_the_scale_of_its_footnote(load, row, scales):
    # oracle: hand-read from fixtures/pep_options.html; only the rows marked (a) are in thousands
    other = parse_tables(load("pep_options.html"))[2]
    assert scales(row(other, "Total number of options granted")) == [3, 3]
    assert scales(row(other, "Total intrinsic value of options exercised")) == [3, 3]
    label = "Weighted-average grant-date fair value per unit of options granted"
    assert scales(row(other, label)) == [0, 0]


@pytest.mark.criterion("C4")
def test_a_footnote_above_the_table_of_values_in_billions(load, row, scales):
    # oracle: hand-read from fixtures/pep_options.html; the notional table is in billions by (a)
    # and the plain table after it is back in the standing millions
    tables = parse_tables(load("pep_options.html"))
    assert scales(row(tables[3], "Commodity contracts")) == [9, 9]
    assert tables[4].rows[0].cells[0].scale == 6


@pytest.mark.criterion("C4")
def test_a_footnote_that_ends_in_a_statement_of_amounts(load, row):
    # oracle: hand-read from fixtures/cat_stock_awards.html; footnote 1 ends "Amounts are in
    # millions of dollars", and only the column marked 1 takes it
    awards = parse_tables(load("cat_stock_awards.html"))[0]
    closing = row(awards, "Outstanding at December 31, 2024")
    assert closing.cells[2].value == 625
    assert closing.cells[2].scale == 6
    assert closing.cells[0].scale == 0


@pytest.mark.criterion("C4")
@given(
    mark=st.sampled_from(["a", "b", "1"]),
    word=st.sampled_from(WORDS),
    rest=st.sampled_from(["", ", disclosed at target", ". Outstanding awards are at target"]),
)
def test_a_footnote_reaches_only_its_own_marker(doc, mini, mark, word, rest):
    # oracle: closed form; the marked column takes the word's power of ten, the other the table's
    name, exponent = word
    other = "z" if mark != "z" else "y"
    table = mini([f"Awards({mark})", f"Grants({other})"], [("Vested", ["12", "34"])])
    tables = parse_tables(doc(table, f"({mark})In {name}{rest}.", f"({other})Granted at target."))
    assert [c.scale for c in tables[0].rows[0].cells] == [exponent, 0]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_a_footnote_does_not_outlive_its_table(load):
    # oracle: fixtures/pep_options.html with footnote (a) of the options table moved to prose. The
    # marked cells fall back to the standing millions and the later tables do not change.
    html = load("pep_options.html")
    tables = parse_tables(html)
    unmarked = html.replace("<div>(a)In thousands.</div>\n<div>The following", "<div>The following")
    assert unmarked != html
    moved = parse_tables(unmarked)
    assert tables[1].rows[0].cells[0].scale == 3
    assert moved[1].rows[0].cells[0].scale == 6
    assert moved[2:] == tables[2:]


# ---- C5: note wordings as general forms ------------------------------------------------------


@pytest.mark.criterion("C5")
def test_any_short_lead_before_dollars_in_a_scale(doc, mini):
    # oracle: requirement 5. The first two wordings are printed by two filers (LLY and DIS); the
    # last two have the same form and appear in neither.
    plain = mini(["2024"], [("Leases", ["600"])])
    notes = [
        ("(Tables present dollars in millions, except per-share data)", 6),
        ("(Tabular dollars in millions, except where noted and per share amounts)", 6),
        ("(Schedules show dollars in thousands)", 3),
        ("(All tables report amounts in millions)", 6),
    ]
    for note, exponent in notes:
        assert parse_tables(doc(note, "Prose.", plain))[0].scale == exponent


@pytest.mark.criterion("C5")
def test_shares_and_a_scale_in_one_clause(mini, row, scales):
    # oracle: requirement 5. The first wording is AAPL's; a few words between "shares" and "in
    # thousands" are the same statement. Dollars stay in millions.
    rows = [("Net income", ["7,367"]), ("Weighted-average shares", ["443,914"])]
    notes = [
        "(In millions, except number of shares, which are reflected in thousands, and par value)",
        "(dollars in millions, shares shown in thousands)",
        "(in millions, with shares reported in thousands)",
    ]
    for note in notes:
        table = parse_table(mini(["2024"], rows), caption=note)
        assert table.scale == 6
        assert scales(row(table, "Net income")) == [6]
        assert scales(row(table, "Weighted-average shares")) == [3]


@pytest.mark.criterion("C5")
@given(
    lead=st.sampled_from(
        ["Schedules show", "Tables below report", "Figures here all give", "Our notes show"]
    ),
    what=st.sampled_from(["dollars", "amounts"]),
    word=st.sampled_from(WORDS),
)
def test_the_lead_words_are_not_a_list(doc, mini, lead, what, word):
    # oracle: closed form; the form is up to four words, then "dollars|amounts in <scale>"
    name, exponent = word
    html = doc(f"({lead} {what} in {name})", "Prose.", mini(["2024"], [("Leases", ["600"])]))
    assert parse_tables(html)[0].scale == exponent


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_prose_stays_prose_when_the_lead_is_free(doc, mini):
    # oracle: requirement 5; a free lead must not turn a sentence with a scale word into a note,
    # and the general form is still read when the note is printed beside the prose
    plain = mini(["2024"], [("Leases", ["600"])])
    prose = [
        "Our members number in the millions and we operate 890 warehouses.",
        "Capital expenditures were $4,710 million in 2024.",
        "We paid dollars in the millions to settle the claims last year.",
    ]
    for sentence in prose:
        assert parse_tables(doc(sentence, plain))[0].scale == 0
    assert parse_tables(doc("(Schedules show dollars in millions)", prose[0], plain))[0].scale == 6


