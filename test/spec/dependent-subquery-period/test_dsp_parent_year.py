"""C2: the compound question's one fiscal year (docs/specs/dependent-subquery-period.md, R2).

Expected years are read by hand from the question text: exactly one annual fiscal year in
2018-2035, and no second year, range, quarter, relative period, date or calendar year.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.subquery_period import stated_fiscal_year

# Compound templates of configs/eval/v2/phrasebook_compound.yaml, metrics filled by hand,
# {p} the one period.
TEMPLATES = [
    "Identify the 3 members of the covered companies with the largest Total Debt for {p}, and "
    "give the percentage growth in Payments to Acquire Property, Plant, and Equipment of the "
    "first-ranked one.",
    "Of the 3 largest of the retailers by Assets in {p}, which had the highest Current Ratio?",
    "top 3 the big tech companies by Liabilities, Current, {p}: which has the highest operating "
    "margin?",
    "largest of the top 3 the retailers by Assets, Current {p}: total current assets growth?",
]
IN_BOUNDS = st.integers(min_value=2018, max_value=2035)
YEAR_FORMS = [
    lambda y: f"{y}", lambda y: f"FY{y % 100:02d}", lambda y: f"fiscal {y}",
    lambda y: f"FY'{y % 100:02d}",
]
PHRASES = ["the latest fiscal year", "the prior year", "Q3", "year to date", "the first half",
           "the most recent quarter", "the current fiscal year"]


def _fill(template, period):
    return template.replace("{p}", period)


@pytest.mark.criterion("C2")
def test_c2_one_year_is_stated(kit):
    # oracle: Requirement 2 by hand — 094b writes FY24 once and nothing else that is a period
    assert stated_fiscal_year(kit.Q094B) == 2024
    assert stated_fiscal_year(_fill(TEMPLATES[1], "fiscal year 2023")) == 2023
    assert stated_fiscal_year(_fill(TEMPLATES[2], "FY'25")) == 2025


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("question", [
    "Looking at the top 3 of the covered companies by total debt in FY24, by how much did the "
    "leader's capital expenditures change from FY23?",
    "Of the 3 largest of the retailers by Assets for FY2022 through FY2024, which had the "
    "highest Current Ratio?",
    "Of the 3 largest of the retailers by Assets in Q3 FY24, which had the highest Current Ratio?",
    "Of the 3 largest of the retailers by Assets in the latest fiscal year, which had the "
    "highest Current Ratio?",
    "Of the 3 largest of the retailers by Assets in calendar 2024, which had the highest "
    "Current Ratio?",
    "Of the 3 largest of the retailers by Assets for the fiscal year ended February 2, 2025, "
    "which had the highest Current Ratio?",
    "Of the 3 largest of the retailers by Assets in FY2040, which had the highest Current Ratio?",
    "Of the 3 largest of the retailers by Assets in fiscal 2015, which had the highest Current "
    "Ratio?",
    "Of the 3 largest of the retailers by Assets, which had the highest Current Ratio?",
])
def test_c2_anything_but_one_annual_year_states_none(kit, question):
    # oracle: Requirement 2 by hand — two years, a range, a quarter, a relative period, a
    # calendar year, a date, a year outside 2018-2035 or no year: not one annual fiscal year.
    # The paired 094b check keeps the test meaningful against a constant None.
    assert stated_fiscal_year(question) is None
    assert stated_fiscal_year(kit.Q094B) == 2024


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(template=st.sampled_from(TEMPLATES), year=IN_BOUNDS, form=st.sampled_from(YEAR_FORMS),
       other=st.integers(min_value=2000, max_value=2099), phrase=st.sampled_from(PHRASES))
def test_c2_one_year_in_any_form_and_what_breaks_it(template, year, form, other, phrase):
    # oracle: metamorphic, Requirement 2 — one year written in any form is that year; a second
    # distinct year, a period phrase or the word "calendar" makes it not one annual year
    question = _fill(template, form(year))
    assert stated_fiscal_year(question) == year
    if other != year:
        assert stated_fiscal_year(f"{question} Compare with FY{other}.") is None
    assert stated_fiscal_year(f"{question} Use {phrase}.") is None
    assert stated_fiscal_year(f"{question} Use calendar years.") is None
