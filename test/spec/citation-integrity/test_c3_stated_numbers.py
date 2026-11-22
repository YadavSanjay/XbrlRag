"""C3: ``stated_numbers`` reads value, step and percent-ness, and skips exempt numbers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import stated_numbers

# Floats here come from decimal text times a power of ten; 1e-12 relative is far below any
# stated precision and above float rounding of the scale multiplication.
REL = 1e-12


def _check(statement: str, expected: list[tuple[float, float, bool]]) -> None:
    got = stated_numbers(statement)
    assert len(got) == len(expected), (statement, got)
    for n, (value, step, percent) in zip(got, expected, strict=True):
        assert n["value"] == pytest.approx(value, rel=REL), statement
        assert n["step"] == pytest.approx(step, rel=REL), statement
        assert n["percent"] is percent, statement
        assert n["text"] in statement


@pytest.mark.criterion("C3")
def test_values_steps_and_percent_flags_read_by_hand():
    # oracle: the stated-number rules of docs/specs/citation-integrity.md, read by hand
    _check("Apple's fiscal 2024 revenue was $391.0 billion.", [(391.0e9, 1e8, False)])
    _check("Revenue was $391,035 million in FY2024, per the 10-K filed November 1, 2024.",
           [(391_035e6, 1e6, False)])
    _check("Gross margin was 46.2% and Services grew 13 percent.",
           [(46.2, 0.1, True), (13.0, 1.0, True)])
    _check("Diluted earnings per share were $6.08.", [(6.08, 0.01, False)])
    _check("Revenue was $391.0B, or $0.4 trillion, and the multiple was 1.9x.",
           [(391.0e9, 1e8, False), (0.4e12, 1e11, False), (1.9, 0.1, False)])
    _check("A net loss of -$1.25 billion and 2,500 employees.",
           [(1.25e9, 1e7, False), (2500.0, 1.0, False)])
    _check("Revenue of US$5k and NT$3.2 mn.", [(5e3, 1e3, False), (3.2e6, 1e5, False)])


@pytest.mark.criterion("C3")
def test_exempt_numbers_are_skipped():
    # oracle: the spec's exemption table — years, fiscal labels, dates, form names, ranks and
    # counts are not stated numbers; the one amount in each statement is
    _check("Walmart ranked #1 and Amazon 2nd among the top 3 of 5 companies over the 12 months "
           "ended September 28, 2024, with net income of $15.5 billion.",
           [(15.5e9, 1e8, False)])
    _check("The 5-year plan and Q3 FY24 results, per the 20-F: 2,500 employees.",
           [(2500.0, 1.0, False)])
    _check("Revenue reached 1,234,567 on 2024-09-28 (ranked No. 4, number 2 overall, rank 7).",
           [(1_234_567.0, 1.0, False)])
    _check("In fiscal 2023 and 1999, 4 filers over 3 years and 8 quarters, Sept. 30 and "
           "Jan 2, 3rd of 21st, H1, s1: $7 million.", [(7e6, 1e6, False)])


@pytest.mark.criterion("C3")
@given(m=st.integers(1, 10_000_000))
def test_one_amount_in_two_scales_reads_the_same(m):
    # oracle: m million = m/1000 billion; both state the last digit at one million
    millions = stated_numbers(f"Revenue was ${m:,} million.")
    billions = stated_numbers(f"Revenue was ${m / 1000:,.3f} billion.")
    assert len(millions) == len(billions) == 1
    assert millions[0]["value"] == m * 1e6
    assert billions[0]["value"] == pytest.approx(m * 1e6, rel=REL)
    assert millions[0]["step"] == pytest.approx(1e6, rel=REL)
    assert billions[0]["step"] == pytest.approx(1e6, rel=REL)


EXEMPT = ["in fiscal 2023", "for FY2024", "in Q3", "per the 10-K", "on September 28, 2024",
          "ranked #2", "ranked 3rd", "among 5 companies", "over 12 months", "on 2024-09-28",
          "over the 5-year period", "at No. 4", "in the top 10"]


@pytest.mark.criterion("C3")
@given(m=st.integers(1, 999_999), d=st.integers(0, 2),
       before=st.lists(st.sampled_from(EXEMPT), max_size=3),
       after=st.lists(st.sampled_from(EXEMPT), max_size=3))
def test_inserting_exempt_numbers_changes_nothing(m, d, before, after):
    # oracle: exempt numbers are never stated numbers, so wrapping one amount in any of them
    # leaves exactly that amount: m/10^d million, its last digit at 10^-d million
    amount = f"${m / 10 ** d:,.{d}f} million"
    statement = " ".join([*before, "revenue was", amount, *after]) + "."
    got = stated_numbers(statement)
    assert len(got) == 1, statement
    assert got[0]["value"] == pytest.approx(m / 10 ** d * 1e6, rel=REL)
    assert got[0]["step"] == pytest.approx(10.0 ** -d * 1e6, rel=REL)
    assert got[0]["percent"] is False
