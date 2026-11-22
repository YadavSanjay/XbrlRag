"""C1: ``is_report_form`` admits exactly the eight annual and quarterly report forms.

Oracle: requirement 1 of docs/specs/sec-keys-report-forms.md (the set is written out here).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import is_report_form

# oracle: requirement 1, written out literally
EIGHT = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-Q", "10-Q/A")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("form", EIGHT)
def test_each_report_form_is_admitted(form):
    # oracle: requirement 1 — each of the eight is a report form
    assert is_report_form(form) is True


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("form", ["10-KT", "10-QT", "8-K", "8-K/A", "DEF 14A", "DEFA14A",
                                  "PRE 14C", "6-K", "S-1", "10-k", " 10-K", "", None])
def test_other_forms_and_none_are_not(form):
    # oracle: requirement 1 — transition reports, current reports, proxies, information
    # statements, registrations, other spellings and a missing form are not report forms
    assert is_report_form("10-K") is True
    assert is_report_form(form) is False


@pytest.mark.criterion("C1")
@given(form=st.one_of(st.none(), st.text(max_size=12),
                      st.sampled_from(EIGHT + ("10-KT", "DEF 14A", "8-K", "10-K/A "))))
def test_membership_in_the_eight_literal_forms(form):
    # oracle: requirement 1 — exact, case-sensitive membership in the literal set above
    assert is_report_form(form) is (form in EIGHT)
