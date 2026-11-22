"""C7: date parts are never produced values (docs/specs/grading-dont-care-and-ties.md,
requirements 6 and 7).

``MRK_TEXT`` is the stored dev xbrlrag_full refusal to int-3df803cba7fe-01c77e81, whose only
produced value was 31.0 (the day of "December 31").
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import date_components, extract_numbers, graded_values
from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(("text", "want"), [
    ("the fiscal year ended December 31, 2024", [31.0, 2024.0]),
    ("as of Dec. 31, 2024.", [31.0, 2024.0]),
    ("on December 31st 2024", [31.0, 2024.0]),
    ("ended Sept 30, '24", [30.0, 24.0]),
    ("ended september 28", [28.0]),
    ("at 31 December 2024", [31.0, 2024.0]),
    ("the 31st of December, 2024", [31.0, 2024.0]),
    ("on 12/31/2024 and 3/1/24", [12.0, 31.0, 2024.0, 3.0, 1.0, 24.0]),
    ("June 30 and Jan 2", [30.0, 2.0]),
])
def test_each_written_form(text, want):
    # oracle: requirement 6, forms 1-3, read by hand
    assert date_components(text) == want


@pytest.mark.criterion("C7")
def test_non_dates_have_no_components():
    # oracle: requirement 6 — no day after/before a month word, no a/b/c form; the one date
    # in the text gives its day and year only
    text = ("In December 2024, fiscal 2024, 31 stores at $12/share, Mayday 4, March 32, "
            "on 2024-12-31, and finally June 30, 2025.")
    assert date_components(text) == [30.0, 2025.0]


@pytest.mark.criterion("C7")
def test_the_parser_skips_date_parts():
    # oracle: requirement 6 — the day is a date part; $5 million is the only amount
    assert extract_numbers("Revenue was $5 million for the year ended December 31, 2024.") \
        == [5e6]
    assert extract_numbers("31 stores at December 31, 2024, and 12/31/2024.") == [31.0]


@pytest.mark.criterion("C7")
def test_graded_values_drops_only_date_only_copies():
    # oracle: requirement 7 — n_D(31)=1; "31 stores" states 31 once (n_A=1), so of two
    # stored copies one is dropped (the earliest); "fell 31%" is -31, not 31
    assert graded_values("Stores at December 31, 2024: 31.", [31.0, 31.0]) == [31.0]
    assert graded_values("Sales fell 31% for the year ended December 31, 2024.",
                         [-31.0, 31.0]) == [-31.0]
    assert graded_values("On December 31, revenue was 31 and 7.", [31.0, 7.0, 31.0]) \
        == [7.0, 31.0]
    assert graded_values("No date here; 31 and 31.", [31.0, 31.0, 31.0]) == [31.0, 31.0, 31.0]


@pytest.mark.criterion("C7")
def test_the_mrk_refusal_is_refused(kit):
    # oracle: requirement 7 — the only produced value is the day of "December 31", so no
    # value remains and the refused answer is REFUSED, not a silent-wrong INCORRECT
    case = EvalCase.from_dict({
        "id": "int-3df803cba7fe-01c77e81", "suite": "xbrl_v2_dev", "kind": "lookup",
        "origin": "v2:lookup", "fy": 2024, "unit": "USD", "rel_tol": 1e-4, "ticker": "MRK",
        "question": "MRK general and administrative expenses the fiscal year ended "
                    "December 31, 2024",
        "tags": ["split:dev", "shape:lookup"], "expected_value": 10816000000.0})
    ans = kit.answer(kit.MRK_TEXT, values=[31.0], refused=True, hedged=True)
    g = grade_case(case, ans)
    assert g.grade is Grade.REFUSED
    assert g.silent_wrong is False
    legacy = EvalCase.from_dict({"id": "legacy-mrk", "suite": "s", "kind": "lookup",
                                 "question": "q", "expected_value": 10816000000.0})
    assert grade_case(legacy, ans).grade is Grade.REFUSED


@pytest.mark.criterion("C7")
@given(month=st.sampled_from(MONTHS), day=st.integers(1, 28), year=st.integers(2000, 2030),
       form=st.integers(0, 3), amount=st.integers(1, 10**6))
def test_inserting_a_date_changes_no_amount(month, day, year, form, amount):
    # oracle: requirement 6 — a written date adds no amount; its components are its day and
    # year in order (the a/b/c form also its month number)
    date = [f"{month} {day}, {year}", f"{day} {month} {year}", f"{month[:3]}. {day} {year}",
            f"{MONTHS.index(month) + 1}/{day}/{year}"][form]
    base = f"Revenue was ${amount:,} thousand."
    assert extract_numbers(f"{base} The year ended {date}.") == extract_numbers(base)
    want = [float(day), float(year)]
    if form == 3:
        want = [float(MONTHS.index(month) + 1), *want]
    assert date_components(f"{base} The year ended {date}.") == want


@pytest.mark.criterion("C7")
@given(day=st.integers(1, 31), copies=st.integers(0, 3), stated=st.integers(0, 2),
       noise=st.lists(st.integers(32, 99), max_size=3))
def test_graded_values_is_idempotent_and_never_invents(day, copies, stated, noise):
    # oracle: requirement 7 — the text states the day as a date part, so at most the
    # `stated` copies it also states as amounts are kept; the noise (never a date part) is
    # kept; a second pass changes nothing
    text = f"For the year ended December {day}, 2024: " + ", ".join(
        [f"{day} units"] * stated + [f"{n} units" for n in noise]) + "."
    values = [float(day)] * (copies + stated) + [float(n) for n in noise]
    once = graded_values(text, values)
    assert graded_values(text, once) == once
    assert once == [float(day)] * stated + [float(n) for n in noise]
