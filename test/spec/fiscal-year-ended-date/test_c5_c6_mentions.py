"""C5, C6: a period mention with a full date resolved through the calendar
(docs/specs/fiscal-year-ended-date.md, req. 4).

Oracle: Home Depot's public labels (conftest.py); the phrases, read by hand; the metamorphic
relations of requirement 4.6 (unchanged reading for end-year labels, a constant label offset,
form invariance).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.xbrl.fiscal_date import resolve_dated_mention

C9_LOOKUP = "the fiscal year ended February 2, 2025"
C9_RATIO = "the fiscal year ended February 1, 2026"
C9_CAGR = ("between the fiscal year ended January 28, 2024 and the fiscal year ended "
           "February 1, 2026")
C9_PIT = ("the annual report for the fiscal year ended February 1, 2026 and the fiscal year "
          "ended January 28, 2024")


def _r(mention, cal, **kw):
    got = resolve_dated_mention(mention, cal, **kw)
    assert isinstance(got, dict) and set(got) == {"status", "periods", "reason"}, got
    return got


def _resolved(periods):
    return {"status": "resolved", "periods": periods, "reason": None}


def _ambiguous(result, needle=None):
    assert result["status"] == "ambiguous" and result["periods"] == [], result
    assert isinstance(result["reason"], str) and result["reason"], result
    if needle is not None:
        assert needle in result["reason"], result


# ------------------------------------------------------------------ C5: resolved


@pytest.mark.criterion("C5")
def test_the_c9_dev_mentions(kit):
    # oracle: Home Depot's labels — 2025-02-02 is fiscal 2024, 2026-02-01 fiscal 2025,
    # 2024-01-28 fiscal 2023; "between … and …" is a range (fiscal-year-range), "… and …" a list
    assert _r(C9_LOOKUP, kit.HD) == _resolved([(2024, "FY")])
    assert _r(C9_RATIO, kit.HD) == _resolved([(2025, "FY")])
    assert _r(C9_CAGR, kit.HD) == _resolved([(2023, "FY"), (2024, "FY"), (2025, "FY")])
    assert _r(C9_PIT, kit.HD) == _resolved([(2023, "FY"), (2025, "FY")])
    to = "the fiscal year ended January 28, 2024 to the fiscal year ended February 1, 2026"
    assert _r(to, kit.HD) == _resolved([(2023, "FY"), (2024, "FY"), (2025, "FY")])


@pytest.mark.criterion("C5")
def test_mixed_quarter_and_other_forms(kit):
    # oracle: by hand on Home Depot's rows — a date with "fiscal 2022" is the list 2022, 2025;
    # F2/F3 dates read as F1; "Jan. 31, 2025" (nominal month end) is fiscal 2024; quarter dates
    # name one quarter; an "as of" date on a quarter end is that quarter
    assert _r("fiscal 2022 and the fiscal year ended February 1, 2026", kit.HD) == \
        _resolved([(2022, "FY"), (2025, "FY")])
    assert _r("the fiscal year ended 2 February 2025", kit.HD) == _resolved([(2024, "FY")])
    assert _r("from 2024-01-28 to 2026-02-01", kit.HD) == \
        _resolved([(2023, "FY"), (2024, "FY"), (2025, "FY")])
    assert _r("the fiscal year ended Jan. 31, 2025", kit.HD) == _resolved([(2024, "FY")])
    assert _r("the quarter ended May 4, 2025", kit.HD) == _resolved([(2025, "Q1")])
    assert _r("the fourth quarter of the fiscal year ended February 1, 2026", kit.HD) == \
        _resolved([(2025, "Q4")])
    assert _r("as of May 4, 2025", kit.HD) == _resolved([(2025, "Q1")])
    assert _r("as of February 2, 2025", kit.HD) == _resolved([(2024, "FY")])


@pytest.mark.criterion("C5")
def test_no_full_date_is_none(kit):
    # oracle: requirement 4 — no written date → None (today's reading); the dated twin of each
    # mention is resolved, so None for everything is not enough
    for plain in ("fiscal 2025", "FY25", "fiscal year 2023 through fiscal year 2025",
                  "January 2025", "02/02/2025", "most recent fiscal year"):
        assert resolve_dated_mention(plain, kit.HD) is None, plain
        assert _r(f"{plain} and {C9_LOOKUP}", kit.HD)["status"] == "resolved", plain


def _ends_mention(data, ends, form):
    """A mention naming 1 or 2 of ``ends`` (a range or a list), dates written by ``form``."""
    a = data.draw(st.sampled_from(ends))
    lead = data.draw(st.sampled_from(["", "the fiscal year ended ", "fiscal year ended "]))
    shape = data.draw(st.sampled_from(["one", "between", "to", "through", "from", "and"]))
    if shape == "one":
        return f"{lead}{form(a)}", [a]
    b = data.draw(st.sampled_from([e for e in ends if e != a]))
    lead2 = lead.replace("the ", "") if lead else ""
    text = {"between": f"between {lead}{form(a)} and {lead}{form(b)}",
            "to": f"{lead}{form(a)} to {lead2}{form(b)}",
            "through": f"{lead}{form(a)} through {lead2}{form(b)}",
            "from": f"from {lead}{form(a)} to {lead}{form(b)}",
            "and": f"{lead}{form(a)} and {lead}{form(b)}"}[shape]
    return text, [a, b]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(data=st.data())
def test_end_year_labels_read_as_today(kit, data):
    # oracle: metamorphic, requirement 4.6 — for a filer whose label is its end year, the
    # result is what extract_years_in_range gives the mention today (dates in F1)
    rows, annual, _q = data.draw(kit.calendars)
    assume(all(label == end.year for end, label in annual.items()))
    assume(all(2018 <= label <= 2035 for label in annual.values()))
    mention, _named = _ends_mention(data, sorted(annual), kit.f1)
    expected = [(y, "FY") for y in extract_years_in_range(mention, year_range=[2018, 2035])]
    assert _r(mention, rows) == _resolved(expected)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(data=st.data(), form=st.sampled_from(["f2", "f3"]))
def test_offset_labels_shift_the_years_and_forms_do_not_matter(kit, data, form):
    # oracle: metamorphic — with labels = end year + k, a date-only range or list gives the
    # digit years + k (range expansion between the labels); F2/F3 give the F1 result
    rows, annual, _q = data.draw(kit.calendars)
    offsets = {label - end.year for end, label in annual.items()}
    assume(len(offsets) == 1 and all(2018 <= y <= 2035 for y in annual.values()))
    (k,) = offsets
    mention, named = _ends_mention(data, sorted(annual), kit.f1)
    digits = extract_years_in_range(mention, year_range=[1900, 2100])
    assert _r(mention, rows) == _resolved([(y + k, "FY") for y in digits])
    other = mention
    for d in named:
        other = other.replace(kit.f1(d), getattr(kit, form)(d))
    assert _r(other, rows) == _r(mention, rows)


# ------------------------------------------------------------------ C6: defined outcomes


@pytest.mark.criterion("C6")
def test_no_calendar_and_unmatched(kit):
    # oracle: requirement 4.1-4.3 — no rows → no_calendar; a date 8+ days from every row, or a
    # year-end date asked about a quarter end, is ambiguous with that date in the reason; a
    # mention with one matched and one unmatched date is ambiguous on the unmatched one
    got = _r(C9_LOOKUP, [])
    assert got["status"] == "no_calendar" and got["periods"] == [], got
    assert isinstance(got["reason"], str) and got["reason"], got
    _ambiguous(_r("the fiscal year ended March 15, 2025", kit.HD), "2025-03-15")
    _ambiguous(_r("the fiscal year ended May 4, 2025", kit.HD), "2025-05-04")
    _ambiguous(_r(f"{C9_LOOKUP} and the fiscal year ended June 30, 2023", kit.HD), "2023-06-30")


@pytest.mark.criterion("C6")
def test_quarter_lists_overlong_ranges_and_out_of_range_labels(kit):
    # oracle: requirement 4.4-4.6, by hand — a quarter mention with two dates; a range whose
    # labels span 2018..2030 (13 years); a label 2017 outside (2018, 2035)
    _ambiguous(_r("the quarter ended May 4, 2025 and the quarter ended August 3, 2025",
                  kit.HD + [kit.row(2025, "Q2", date(2025, 8, 3))]))
    long_cal = [kit.row(2018, "FY", date(2019, 2, 2)), kit.row(2030, "FY", date(2031, 2, 1))]
    _ambiguous(_r("from the fiscal year ended February 2, 2019 to the fiscal year ended "
                  "February 1, 2031", long_cal))
    early = [kit.row(2017, "FY", date(2018, 2, 3))]
    _ambiguous(_r("the fiscal year ended February 3, 2018", early))
    # the same row inside the range is resolved, so the cause is the bound, not the row
    assert _r("the fiscal year ended February 3, 2018", early, year_range=(2010, 2035)) == \
        _resolved([(2017, "FY")])


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(data=st.data())
def test_an_unmatched_date_makes_any_mention_ambiguous(kit, data):
    # oracle: requirement 4.3 — no fallback to the digits: adding a date 8-40 days from every
    # annual end turns a resolved mention ambiguous, naming that date
    rows, annual, quarters = data.draw(kit.calendars)
    assume(all(2018 <= y <= 2035 for y in annual.values()))
    mention, _named = _ends_mention(data, sorted(annual), kit.f1)
    assert _r(mention, rows)["status"] == "resolved"
    end = data.draw(st.sampled_from(sorted(annual)))
    bad = end + timedelta(days=data.draw(st.integers(8, 40)) * data.draw(st.sampled_from([-1, 1])))
    assume(all(abs((q - bad).days) > 7 for q, _l, _n in quarters))
    _ambiguous(_r(f"{mention} and the fiscal year ended {kit.f1(bad)}", rows), bad.isoformat())
