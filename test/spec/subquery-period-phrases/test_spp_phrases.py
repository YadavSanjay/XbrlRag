"""C1-C2: a year-relative phrase followed by a year states that year
(docs/specs/subquery-period-phrases.md, R1 and R3.1).

Every expected value is Requirement 1 applied by hand to synthetic text: a period phrase of
dependent-subquery-period R1.2 counts, unless it is one of the year-relative phrases
("most recent", "latest", "this year", "current year", "current fiscal year") followed by one
space, an optional "fiscal year "/"fiscal "/"year " and a year form. All results are exact.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.subquery_period import stated_fiscal_year, states_period_phrase

# dependent-subquery-period Requirement 1.2, copied from that spec.
PERIOD_PHRASES = [
    "most recent", "latest", "this year", "current year", "current fiscal year",
    "current quarter", "current fiscal quarter", "current period",
    "prior year", "last year", "previous year", "prior fiscal year", "last fiscal year",
    "previous fiscal year", "preceding fiscal year", "year ago", "year-ago", "preceding year",
    "first quarter", "1st quarter", "q1", "second quarter", "2nd quarter", "q2",
    "third quarter", "3rd quarter", "q3", "fourth quarter", "4th quarter", "q4",
    "year to date", "year-to-date", "ytd", "first half", "1st half", "six months", "six-month",
    "h1", "first nine months", "nine months", "nine-month", "9 months", "cumulative",
]
# Requirement 1, copied from this spec.
YEAR_RELATIVE = ["most recent", "latest", "this year", "current year", "current fiscal year"]
OTHER_PHRASES = [p for p in PERIOD_PHRASES if p not in YEAR_RELATIVE]
CONNECTORS = ["", "fiscal year ", "fiscal ", "year "]
# The year forms of Requirement 0, written with mixed case.
YEAR_FORMS = [
    lambda y: f"{y}", lambda y: f"FY{y}", lambda y: f"FY {y}", lambda y: f"FY{y % 100:02d}",
    lambda y: f"FY'{y % 100:02d}", lambda y: f"fy’{y % 100:02d}",
]
YEARS = st.integers(min_value=2018, max_value=2035)

DEFECT_B = "by Assets, Current fiscal year 2024"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    DEFECT_B, "the latest fiscal year 2023", "the most recent fiscal 2024", "this year FY25",
    "current year 2024", "current fiscal year FY'24", "Current Ratio", "",
])
def test_c1_excused_or_no_phrase_is_false(text):
    # oracle: Requirement 1 by hand — each year-relative phrase is followed by one space, an
    # optional connector and a year form, so it is excused; "Current Ratio" and "" hold no
    # phrase. The paired defect-B call keeps the test failing against a stub returning True.
    assert states_period_phrase(text) is False
    assert states_period_phrase("the current fiscal year") is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "the current fiscal year", "the latest fiscal year", "the latest fiscal year, 2024",
    "the current fiscal year ended June 30, 2024", "current quarter 2024", "prior year 2023",
    "Q3 2024", "current fiscal year 2024 versus the prior year",
])
def test_c1_unexcused_phrase_is_true(text):
    # oracle: Requirement 1 by hand — a year-relative phrase not directly followed by a year
    # form, a phrase that is not year-relative, or a second phrase beside an excused one
    assert states_period_phrase(text) is True
    assert states_period_phrase(DEFECT_B) is False


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(phrase=st.sampled_from(PERIOD_PHRASES), relative=st.sampled_from(YEAR_RELATIVE),
       other=st.sampled_from(OTHER_PHRASES), connector=st.sampled_from(CONNECTORS),
       form=st.sampled_from(YEAR_FORMS), year=YEARS)
def test_c1_property_a_year_after_a_year_relative_phrase_excuses_it(phrase, relative, other,
                                                                    connector, form, year):
    # oracle: metamorphic, Requirement 1 — any phrase alone in a carrier sentence counts; a
    # year-relative phrase followed by " " + connector + year form is excused; any other
    # phrase followed by the same year still counts
    assert states_period_phrase(f"Report the revenue for the {phrase} please.") is True
    yearpart = f"{connector}{form(year)}"
    assert states_period_phrase(f"Report the revenue for the {relative} {yearpart} please.") \
        is False
    assert states_period_phrase(f"Report the revenue for the {other} {yearpart} please.") is True


# --------------------------------------------------------------------------- C2
# Synthetic compound templates, {p} the one period.
TEMPLATES = [
    "Of the 3 largest retailers by Assets in {p}, which had the highest Current Ratio?",
    "Which is the largest of the top 3 retailers by Assets, Current {p}: what was its total "
    "current assets growth?",
    "Rank the covered companies by Total Debt for {p}, and give the leader's revenue growth.",
]


def _fill(template, period):
    return template.replace("{p}", period)


@pytest.mark.criterion("C2")
def test_c2_a_year_after_a_relative_phrase_is_the_stated_year():
    # oracle: Requirement 3.1 by hand — the defect-B question writes 2024 once, after
    # "Current fiscal year", which is excused; the second writes 2023 after "latest fiscal year"
    q_b = ("Which is the largest of the top 3 retailers by Assets, Current fiscal year 2024: "
           "what was its total current assets growth?")
    assert stated_fiscal_year(q_b) == 2024
    assert stated_fiscal_year(_fill(TEMPLATES[0], "the latest fiscal year 2023")) == 2023


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("period", [
    "the latest fiscal year", "the current fiscal year 2024 versus the prior year",
    "the current quarter 2024", "Current fiscal year 2024 compared from FY23",
])
def test_c2_unexcused_or_two_years_still_none(period):
    # oracle: Requirement 3.1 by hand — no year, a second (prior) phrase, a quarter phrase,
    # or a second year: not one annual fiscal year. The paired defect-B question keeps the
    # test failing against today's behaviour.
    assert stated_fiscal_year(_fill(TEMPLATES[0], period)) is None
    assert stated_fiscal_year(_fill(TEMPLATES[1], "fiscal year 2024")) == 2024


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(template=st.sampled_from(TEMPLATES), relative=st.sampled_from(YEAR_RELATIVE),
       connector=st.sampled_from(CONNECTORS), form=st.sampled_from(YEAR_FORMS), year=YEARS,
       other=st.integers(min_value=2000, max_value=2099),
       extra=st.sampled_from(["the prior year", "Q3", "year to date", "the latest quarter"]))
def test_c2_property_relative_phrase_with_year(template, relative, connector, form, year,
                                               other, extra):
    # oracle: metamorphic, Requirement 3.1 — a year-relative phrase directly followed by Y
    # states Y; adding an unexcused phrase or a second distinct year makes it not one year
    question = _fill(template, f"the {relative} {connector}{form(year)}")
    assert stated_fiscal_year(question) == year
    assert stated_fiscal_year(f"{question} Compare with {extra}.") is None
    if other != year:
        assert stated_fiscal_year(f"{question} Compare with FY{other}.") is None
