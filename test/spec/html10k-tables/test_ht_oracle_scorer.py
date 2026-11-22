"""C11 (the oracle record) and C12 (the scorer). Spec: docs/specs/html10k-tables.md."""

from __future__ import annotations

import json
from decimal import Decimal as Dec

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.tables import parse_oracle_record, parse_table, score_tables

ACC = "0000000000-24-000001"
RECORD = {
    "accession": ACC,
    "table_index": 12,
    "source_row": 11,
    "source_cell": 1,
    "value": "169",
    "negative": True,
    "scale": 6,
    "nil": False,
    "printed": "(169)",
    "concept": "us-gaap:InterestExpense",
    "unit": "USD",
    "tagged_negative": False,
}


def printed_table(*printed, table_index=0):
    """A one-row table in millions; its k-th printed value sits at <tr> 1, <td> k + 1."""
    tds = "".join(f"<td>{text}</td>" for text in printed)
    ths = "".join("<td>2024</td>" for _ in printed)
    html = f"<table><tr><td>(in millions)</td>{ths}</tr><tr><td>Item</td>{tds}</tr></table>"
    return parse_table(html, table_index=table_index)


def oracle(position, value, negative=False, scale=6, accession=ACC, table_index=0):
    """The oracle cell for the printed value at ``position``, or a nil when value is None."""
    return parse_oracle_record(
        {
            **RECORD,
            "accession": accession,
            "table_index": table_index,
            "source_row": 1,
            "source_cell": position + 1,
            "value": value,
            "negative": negative,
            "scale": scale,
            "nil": value is None,
        }
    )


@pytest.mark.criterion("C11")
def test_a_valid_record_from_the_spec():
    # oracle: the example record in requirement 11, through a JSON line as the oracle file holds it
    line = json.dumps(RECORD)
    parsed = parse_oracle_record(json.loads(line))
    assert (parsed.accession, parsed.table_index) == (ACC, 12)
    assert (parsed.source_row, parsed.source_cell) == (11, 1)
    assert (parsed.value, parsed.negative, parsed.scale, parsed.nil) == (Dec("169"), True, 6, False)
    assert (parsed.printed, parsed.concept) == ("(169)", "us-gaap:InterestExpense")
    assert (parsed.unit, parsed.tagged_negative) == ("USD", False)


@pytest.mark.criterion("C11")
def test_a_nil_record():
    # oracle: requirement 11; a nil has a null value
    record = {**RECORD, "value": None, "nil": True, "negative": False, "printed": "—"}
    parsed = parse_oracle_record(record)
    assert parsed.value is None
    assert parsed.nil is True


@pytest.mark.criterion("C11")
def test_a_percent_record_keeps_its_decimal_exactly():
    # oracle: requirement 11; the string 10.92 stays that decimal, scale -2 as tagged
    parsed = parse_oracle_record({**RECORD, "value": "10.92", "negative": False, "scale": -2})
    assert parsed.value == Dec("10.92")
    assert parsed.scale == -2


@pytest.mark.criterion("C11")
@pytest.mark.parametrize(
    "change",
    [
        {"value": 169.0},
        {"value": 169},
        {"value": "-169"},
        {"value": "abc"},
        {"value": None},
        {"nil": True},
        {"source_row": "11"},
        {"source_cell": 1.0},
        {"table_index": True},
        {"scale": 6.0},
        {"negative": "yes"},
        {"nil": 0},
        {"extra": 1},
    ],
)
def test_a_malformed_record_raises(change):
    # oracle: requirement 11 lists each refusal
    with pytest.raises(ValueError):
        parse_oracle_record({**RECORD, **change})


@pytest.mark.criterion("C11")
@pytest.mark.parametrize("key", sorted(RECORD))
def test_a_missing_key_raises(key):
    # oracle: requirement 11, exactly these keys
    record = {k: v for k, v in RECORD.items() if k != key}
    with pytest.raises(ValueError):
        parse_oracle_record(record)


@pytest.mark.criterion("C11")
@given(
    value=st.decimals(min_value=0, max_value=10**12, places=4),
    negative=st.booleans(),
    scale=st.sampled_from([-2, 0, 3, 6, 9]),
    ordinals=st.tuples(*[st.integers(min_value=0, max_value=5000)] * 3),
)
def test_a_record_survives_its_json_line(value, negative, scale, ordinals):
    # oracle: closed form; each field comes back as generated
    index, row, col = ordinals
    record = {
        **RECORD,
        "value": str(value),
        "negative": negative,
        "scale": scale,
        "table_index": index,
        "source_row": row,
        "source_cell": col,
    }
    parsed = parse_oracle_record(json.loads(json.dumps(record)))
    assert (parsed.value, parsed.negative, parsed.scale) == (value, negative, scale)
    assert (parsed.table_index, parsed.source_row, parsed.source_cell) == (index, row, col)


@pytest.mark.criterion("C12")
def test_one_cell_of_each_kind():
    # oracle: hand count over five cells: 1 exact, 1 value, 1 scale, 1 sign, 1 missing. The table
    # prints (169), 170, 171, 172 in millions; the fifth oracle cell has no parsed cell.
    parsed = {ACC: [printed_table("(169)", "170", "171", "172")]}
    cells = [
        oracle(0, "169", negative=True),
        oracle(1, "999"),
        oracle(2, "171", scale=3),
        oracle(3, "172", negative=True),
        oracle(4, "173"),
    ]
    score = score_tables(parsed, cells)
    assert (score.total, score.exact) == (5, 1)
    assert (score.value_errors, score.scale_errors) == (1, 1)
    assert (score.sign_errors, score.missing) == (1, 1)
    assert score.accuracy == pytest.approx(0.2, abs=1e-12)  # exact rational 1/5
    assert [m[0] for m in score.mismatches] == cells[1:]
    assert score.mismatches[3][1] is None


@pytest.mark.criterion("C12")
def test_two_errors_on_one_cell_count_in_both():
    # oracle: hand count; wrong scale and wrong sign on one cell, not exact, one mismatch
    score = score_tables({ACC: [printed_table("5")]}, [oracle(0, "5", negative=True, scale=3)])
    assert (score.exact, score.scale_errors, score.sign_errors, score.value_errors) == (0, 1, 1, 0)
    assert len(score.mismatches) == 1


@pytest.mark.criterion("C12")
def test_equal_decimals_are_exact_and_a_zero_has_no_sign():
    # oracle: requirement 12; a printed 1,234.50 equals the oracle's 1234.5, and "(0)" equals 0
    parsed = {ACC: [printed_table("1,234.50", "0")]}
    score = score_tables(parsed, [oracle(0, "1234.5"), oracle(1, "0", negative=True)])
    assert (score.total, score.exact) == (2, 2)
    assert score.accuracy == pytest.approx(1.0, abs=1e-12)  # every cell is exact


@pytest.mark.criterion("C12")
def test_nil_rules():
    # oracle: requirement 12. Nil against nil is exact, whatever the oracle's scale. A printed 0
    # against a nil, a printed dash against a number and a text cell against a number are value
    # errors, with no scale or sign error.
    parsed = {ACC: [printed_table("—", "0", "—", "N/A")]}
    cells = [oracle(0, None, scale=0), oracle(1, None), oracle(2, "5"), oracle(3, "5")]
    score = score_tables(parsed, cells)
    assert (score.total, score.exact, score.value_errors) == (4, 1, 3)
    assert (score.scale_errors, score.sign_errors, score.missing) == (0, 0, 0)


@pytest.mark.criterion("C12")
def test_lookup_uses_table_index_not_position():
    # oracle: requirement 12; table 7 is first in the list, and each cell finds its own table
    parsed = {ACC: [printed_table("1", table_index=7), printed_table("2", table_index=3)]}
    score = score_tables(parsed, [oracle(0, "2", table_index=3), oracle(0, "1", table_index=7)])
    assert (score.total, score.exact) == (2, 2)


@pytest.mark.criterion("C12")
def test_an_absent_filing_counts_as_missing():
    # oracle: requirement 12; a filing that failed to parse lowers the score
    other = "0000000000-24-000002"
    parsed = {ACC: [printed_table("1")]}
    score = score_tables(parsed, [oracle(0, "1"), oracle(0, "1", accession=other)])
    assert (score.total, score.exact, score.missing) == (2, 1, 1)
    assert score.accuracy == pytest.approx(0.5, abs=1e-12)  # exact rational 1/2


@pytest.mark.criterion("C12")
def test_an_empty_oracle_is_refused():
    # oracle: requirement 12; no accuracy can come from nothing
    with pytest.raises(ValueError):
        score_tables({ACC: [printed_table("1")]}, [])


@pytest.mark.criterion("C12")
def test_a_repeated_location_is_refused():
    # oracle: requirement 12; a location appears once
    with pytest.raises(ValueError):
        score_tables({ACC: [printed_table("1")]}, [oracle(0, "1"), oracle(0, "2")])


@pytest.mark.criterion("C12")
@given(
    n=st.integers(min_value=1, max_value=30),
    data=st.randoms(use_true_random=False),
    wrong=st.integers(min_value=0, max_value=30),
)
def test_corrupting_k_cells_costs_exactly_k(n, data, wrong):
    # oracle: closed form; k of n oracle cells differ, so exact is n - k in any order
    k = min(wrong, n)
    parsed = {ACC: [printed_table(*[str(i + 1) for i in range(n)])]}
    cells = [oracle(i, str(i + 1 + (1 if i < k else 0))) for i in range(n)]
    data.shuffle(cells)
    score = score_tables(parsed, cells)
    assert (score.total, score.exact, score.value_errors) == (n, n - k, k)
    assert score.accuracy == pytest.approx((n - k) / n, abs=1e-12)  # exact rational


@pytest.mark.criterion("C12")
@given(
    amount=st.decimals(min_value=1, max_value=10**9, places=2),
    negative=st.booleans(),
)
def test_a_cell_always_matches_its_own_oracle(amount, negative):
    # oracle: closed form; the printed cell and the oracle cell state the same number
    printed = f"({amount:,.2f})" if negative else f"{amount:,.2f}"
    score = score_tables({ACC: [printed_table(printed)]}, [oracle(0, str(amount), negative)])
    assert (score.total, score.exact, len(score.mismatches)) == (1, 1, 0)
