"""Resolve "the fiscal year ended <date>" through the filer's fiscal calendar.

A full date written in a period mention maps to the fiscal period whose ``fiscal_calendar``
``period_end`` is that date (within 7 days), not to the date's calendar year: Home Depot's
"fiscal year ended February 2, 2025" is its fiscal 2024 (S2 of XbrlRag_Eval_Semantics.md).
Everything here is pure: the caller (``EntityExtractor``) reads the filer's
``fiscal_calendar`` rows and passes them in. Spec: docs/specs/fiscal-year-ended-date.md.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.retriever.extract_year_util import (
    BETWEEN_RANGE_REGEX,
    MAX_RANGE_YEARS,
    SPAN_RANGE_REGEX,
    extract_years_in_range,
    overlong_year_ranges,
)

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"]
# Month number by the lower-cased written month (full name or abbreviation, no dot).
_MONTH_NUMBER: dict[str, int] = {name.lower(): i + 1 for i, name in enumerate(_MONTH_NAMES)}
_MONTH_NUMBER.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
                      "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12})

# A full month name, or an abbreviation optionally followed by "." ("May" has none).
_MONTH = (r"(?:january|february|march|april|may|june|july|august|september|october|november"
          r"|december|(?:jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?)")
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_YEAR = r"(?:19|20)\d{2}"
# Neither preceded nor followed by a letter or digit.
_NOT_BEFORE = r"(?<![^\W_])"
_NOT_AFTER = r"(?![^\W_])"
_DATE_REGEX = re.compile(
    rf"{_NOT_BEFORE}(?:"
    rf"(?P<m1>{_MONTH})\s+(?P<d1>{_DAY}),?\s+(?P<y1>{_YEAR})"        # F1: February 2, 2025
    rf"|(?P<d2>{_DAY})\s+(?P<m2>{_MONTH}),?\s+(?P<y2>{_YEAR})"       # F2: 2 February 2025
    rf"|(?P<y3>{_YEAR})-(?P<n3>\d{{2}})-(?P<d3>\d{{2}})"             # F3: 2025-02-02
    rf"){_NOT_AFTER}",
    re.IGNORECASE,
)

_PERIODS = ("FY", "Q1", "Q2", "Q3", "Q4")
_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


def _day_number(day: str) -> int:
    """The day number of a written day ("2nd" → 2)."""
    return int(re.sub(r"\D", "", day))


def _date_of(match: re.Match[str]) -> datetime.date | None:
    """The date a regex match writes, or None when it is no real calendar date."""
    try:
        if match.group("y3"):
            return datetime.date(int(match.group("y3")), int(match.group("n3")),
                                 int(match.group("d3")))
        n = "1" if match.group("m1") else "2"
        month = _MONTH_NUMBER[match.group(f"m{n}").rstrip(".").lower()]
        return datetime.date(int(match.group(f"y{n}")), month, _day_number(match.group(f"d{n}")))
    except ValueError:
        return None


def find_written_dates(text: str) -> list[tuple[datetime.date, int, int]]:
    """Every full date written in a text, in order of appearance.

    Args:
        text: A period mention or a question.

    Returns:
        ``(value, start, end)`` per date, ``text[start:end]`` being the written date, for the
        forms F1 (``February 2, 2025``), F2 (``2 February 2025``) and F3 (``2025-02-02``) of the
        spec's requirement 1.
    """
    found: list[tuple[datetime.date, int, int]] = []
    pos = 0
    while True:
        match = _DATE_REGEX.search(text, pos)
        if match is None:
            return found
        value = _date_of(match)
        if value is None:
            # Not a real date ("February 30, 2025"): look again just past its start.
            pos = match.start() + 1
            continue
        found.append((value, match.start(), match.end()))
        pos = match.end()


def _usable_rows(calendar: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, datetime.date]]:
    """The distinct ``(fiscal_year, fiscal_period, period_end)`` of the usable rows."""
    rows: set[tuple[int, str, datetime.date]] = set()
    for r in calendar:
        try:
            period = r["fiscal_period"]
            end = r["period_end"]
            year = int(r["fiscal_year"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(end, datetime.datetime):
            end = end.date()
        if period in _PERIODS and isinstance(end, datetime.date):
            rows.add((year, period, end))
    return sorted(rows)


def _pick(candidates: list[tuple[int, int, str, datetime.date]]
          ) -> tuple[int, str, datetime.date] | None:
    """The row the candidates pick: the only one, or the only exact one; else None (a tie)."""
    if len(candidates) == 1:
        _dist, year, period, end = candidates[0]
        return year, period, end
    exact = [c for c in candidates if c[0] == 0]
    if len(exact) == 1:
        _dist, year, period, end = exact[0]
        return year, period, end
    return None


def _result(status: str, end: datetime.date, why: str) -> dict[str, Any]:
    return {"status": status, "fiscal_year": None, "fiscal_period": None,
            "matched_end": None, "reason": f"{end.isoformat()}: {why}"}


def _matched(year: int, period: str, matched_end: datetime.date) -> dict[str, Any]:
    return {"status": "matched", "fiscal_year": year, "fiscal_period": period,
            "matched_end": matched_end, "reason": None}


def match_period_end(
    end: datetime.date,
    calendar: Sequence[Mapping[str, Any]],
    period: str,
    quarter: str | None = None,
    tolerance_days: int = 7,
) -> dict[str, Any]:
    """Match one date to the filer's fiscal-calendar row that ends on it.

    Args:
        end: The written date.
        calendar: One filer's rows with ``fiscal_year``, ``fiscal_period`` and
            ``period_end`` (a ``datetime.date``).
        period: ``"FY"``, ``"quarter"`` or ``"any"``.
        quarter: A named quarter ``"Q1"``..``"Q4"``, used with ``"quarter"``.
        tolerance_days: The largest distance, in days, of a candidate row.

    Returns:
        ``{"status", "fiscal_year", "fiscal_period", "matched_end", "reason"}``, status
        ``matched``, ``ambiguous``, ``not_found`` or ``no_calendar`` (spec requirement 2).
    """
    rows = _usable_rows(calendar)
    if not rows:
        return _result("no_calendar", end, "the filer has no fiscal calendar rows")
    candidates = [(abs((row_end - end).days), year, fp, row_end) for year, fp, row_end in rows]
    candidates = [c for c in candidates if c[0] <= tolerance_days]
    annual = [c for c in candidates if c[2] == "FY"]
    quarters = [c for c in candidates if c[2] in _QUARTERS]
    window = f"within {tolerance_days} days"

    if period == "any":
        if annual:
            period = "FY"
        elif not quarters:
            return _result("not_found", end, f"no fiscal period ends {window}")
        else:
            period, quarter = "quarter", None

    if period == "FY":
        if annual:
            picked = _pick(annual)
            if picked is None:
                return _result("ambiguous", end, f"several fiscal years end {window}")
            return _matched(picked[0], "FY", picked[2])
        if quarters:
            return _result("ambiguous", end, "the date ends a quarter, not a fiscal year")
        return _result("not_found", end, f"no fiscal year ends {window}")

    if period == "quarter":
        if quarters:
            picked = _pick(quarters)
            if picked is None:
                return _result("ambiguous", end, f"several quarters end {window}")
            if quarter is not None and quarter != picked[1]:
                return _result("ambiguous", end,
                               f"the date ends {picked[1]} of fiscal {picked[0]}, not {quarter}")
            return _matched(picked[0], picked[1], picked[2])
        if annual:
            picked = _pick(annual)
            if picked is None:
                return _result("ambiguous", end, f"several fiscal years end {window}")
            return _matched(picked[0], quarter or "Q4", picked[2])
        return _result("not_found", end, f"no quarter or fiscal year ends {window}")

    return _result("ambiguous", end, f"unknown period kind {period!r}")


def _cue_regex(cues: Sequence[str]) -> re.Pattern[str]:
    """Whole-word alternatives; a space in a cue matches any run of whitespace."""
    alts = "|".join(re.escape(c).replace(r"\ ", r"\s+") for c in sorted(cues, key=len,
                                                                         reverse=True))
    return re.compile(rf"(?<![^\W_])(?:{alts})(?![^\W_])")


# Quarter cue → the quarter it names (None: a quarter cue naming none).
_QUARTER_CUES: dict[str, str | None] = {
    "quarter": None, "quarterly": None, "three months": None, "three-month": None,
    "3 months": None, "3-month": None, "13 weeks": None, "13-week": None,
    "thirteen weeks": None, "year to date": None, "year-to-date": None, "ytd": None,
    "first quarter": "Q1", "1st quarter": "Q1", "q1": "Q1",
    "second quarter": "Q2", "2nd quarter": "Q2", "q2": "Q2", "first half": "Q2",
    "six months": "Q2", "six-month": "Q2", "6 months": "Q2",
    "third quarter": "Q3", "3rd quarter": "Q3", "q3": "Q3", "nine months": "Q3",
    "nine-month": "Q3", "9 months": "Q3",
    "fourth quarter": "Q4", "4th quarter": "Q4", "q4": "Q4",
}
_ANNUAL_CUES = ("fiscal", "year ended", "years ended", "year ending", "year end", "year-end",
                "twelve months", "12 months", "52 weeks", "53 weeks", "52-week", "53-week",
                "annual", "10-k")
_QUARTER_CUE_REGEX = _cue_regex(list(_QUARTER_CUES))
_ANNUAL_CUE_REGEX = _cue_regex(_ANNUAL_CUES)
# "fy" is a whole word, or "fy" before digits ("fy2025").
_FY_CUE_REGEX = re.compile(r"(?<![^\W_])fy(?![^\W\d_])")


def mention_period_kind(mention: str) -> tuple[str, str | None]:
    """The kind of period a mention asks for, from its cue words.

    Args:
        mention: A period mention.

    Returns:
        ``(period, quarter)``: period ``"quarter"``, ``"FY"`` or ``"any"``; quarter the named
        ``"Q1"``..``"Q4"`` or ``None`` (spec requirement 3).
    """
    text = mention.lower()
    quarter_cues = [m.group() for m in _QUARTER_CUE_REGEX.finditer(text)]
    if quarter_cues:
        named = {_QUARTER_CUES[re.sub(r"\s+", " ", cue)] for cue in quarter_cues} - {None}
        return "quarter", named.pop() if len(named) == 1 else None
    if _ANNUAL_CUE_REGEX.search(text) or _FY_CUE_REGEX.search(text):
        return "FY", None
    return "any", None


def _ambiguous(reason: str) -> dict[str, Any]:
    return {"status": "ambiguous", "periods": [], "reason": reason}


def resolve_dated_mention(
    mention: str,
    calendar: Sequence[Mapping[str, Any]],
    tolerance_days: int = 7,
    year_range: tuple[int, int] = (2018, 2035),
) -> dict[str, Any] | None:
    """Resolve a period mention that writes a full date, through the filer's calendar.

    Args:
        mention: A period mention.
        calendar: One filer's fiscal_calendar rows, as for ``match_period_end``.
        tolerance_days: As for ``match_period_end``.
        year_range: The ``(lo, hi)`` bounds of a resolved fiscal year.

    Returns:
        ``None`` when the mention writes no full date; else ``{"status", "periods",
        "reason"}``, status ``resolved``, ``ambiguous`` or ``no_calendar`` and periods the
        ascending ``(fiscal_year, fiscal_period)`` tuples (spec requirement 4).
    """
    dates = find_written_dates(mention)
    if not dates:
        return None
    if not _usable_rows(calendar):
        return {"status": "no_calendar", "periods": [],
                "reason": "the filer has no fiscal calendar rows"}

    period, quarter = mention_period_kind(mention)
    matches = [match_period_end(d, calendar, period, quarter, tolerance_days)
               for d, _start, _end in dates]
    # No fallback to a date's digits when the filer has a calendar.
    for m in matches:
        if m["status"] != "matched":
            return _ambiguous(m["reason"])

    if period == "quarter":
        if len(matches) > 1:
            return _ambiguous("a quarter mention with several dates is not resolved; "
                              "name one quarter")
        return {"status": "resolved", "reason": None,
                "periods": [(matches[0]["fiscal_year"], matches[0]["fiscal_period"])]}

    quarter_matches = [m for m in matches if m["fiscal_period"] != "FY"]
    if quarter_matches:
        if len(matches) > 1:
            return _ambiguous("a mention with several dates names a quarter end "
                              f"({quarter_matches[0]['matched_end'].isoformat()}); "
                              "name one period")
        return {"status": "resolved", "reason": None,
                "periods": [(matches[0]["fiscal_year"], matches[0]["fiscal_period"])]}

    # Every date matched a fiscal year: read the years as today, with each date's year
    # replaced by its label.
    parts: list[str] = []
    pos = 0
    for (d, start, end), m in zip(dates, matches, strict=True):
        parts.append(mention[pos:start])
        parts.append(f"{_MONTH_NAMES[d.month - 1]} {d.day}, {m['fiscal_year']}")
        pos = end
    parts.append(mention[pos:])
    relabelled = "".join(parts)

    bounds = list(year_range)
    overlong = overlong_year_ranges(relabelled, year_range=bounds)
    if overlong:
        spans = ", ".join(f"{a}-{b}" for a, b in overlong)
        return _ambiguous(f"fiscal-year range {spans} is too long to expand; "
                          "name the years wanted")
    lo, hi = min(bounds), max(bounds)
    outside = sorted({m["fiscal_year"] for m in matches if not lo <= m["fiscal_year"] <= hi})
    if outside:
        return _ambiguous(f"fiscal year {outside[0]} is outside {lo}-{hi}")
    years = extract_years_in_range(relabelled, year_range=bounds)
    return {"status": "resolved", "reason": None, "periods": [(y, "FY") for y in years]}


def stated_fiscal_year_ranges(
    question: str,
    calendar: Sequence[Mapping[str, Any]] = (),
    year_range: tuple[int, int] = (2018, 2035),
) -> list[tuple[int, int]]:
    """The fiscal-year ranges a question states, as fiscal-year labels.

    Args:
        question: The question text (the router's query).
        calendar: The primary filer's fiscal_calendar rows, as for ``match_period_end``; empty
            when none were read.
        year_range: The ``(lo, hi)`` bounds both labels of a range must fall in.

    Returns:
        The distinct ``(lower, higher)`` ranges, ascending (docs/specs/fiscal-year-range-split.md
        requirements 1 to 5).
    """
    return sorted({(lo, hi) for lo, hi, _text in _stated_ranges(question, calendar, year_range)})


def missing_range_years(
    question: str,
    resolved_years: Sequence[int],
    calendar: Sequence[Mapping[str, Any]] = (),
    year_range: tuple[int, int] = (2018, 2035),
) -> list[int]:
    """The middle years of a stated range whose two endpoints were resolved but not its middle.

    Args:
        question: The question text (the router's query).
        resolved_years: The fiscal years of the question's non-ambiguous FY periods.
        calendar: As for ``stated_fiscal_year_ranges``.
        year_range: As for ``stated_fiscal_year_ranges``.

    Returns:
        The years to add, ascending, none of them in ``resolved_years``
        (docs/specs/fiscal-year-range-split.md requirement 6).
    """
    return [year for year, _text in range_fill(question, resolved_years, calendar, year_range)]


def range_fill(
    question: str,
    resolved_years: Sequence[int],
    calendar: Sequence[Mapping[str, Any]] = (),
    year_range: tuple[int, int] = (2018, 2035),
) -> list[tuple[int, str]]:
    """The years ``missing_range_years`` returns, each with the range text that states it.

    Args:
        question: As for ``missing_range_years``.
        resolved_years: As for ``missing_range_years``.
        calendar: As for ``missing_range_years``.
        year_range: As for ``missing_range_years``.

    Returns:
        ``(year, range_text)`` ascending by year, ``range_text`` the first range in the question
        (as written) whose middle holds the year.
    """
    resolved = set(resolved_years)
    added: dict[int, str] = {}
    for lo, hi, text in _stated_ranges(question, calendar, year_range):
        if lo not in resolved or hi not in resolved or hi - lo + 1 > MAX_RANGE_YEARS:
            continue
        for year in range(lo + 1, hi):
            if year not in resolved:
                added.setdefault(year, text)
    return sorted(added.items())


# Words before a range's first endpoint read for a quarter cue (spec requirement 3).
_QUARTER_WINDOW_WORDS = 3
_BETWEEN_LEAD = re.compile(r"between\s+", re.IGNORECASE)


def _endpoint_label(
    match: re.Match[str],
    n: int,
    dates_by_end: Mapping[int, datetime.date],
    calendar: Sequence[Mapping[str, Any]],
    has_calendar: bool,
) -> int | None:
    """The fiscal-year label of endpoint *n* of a range match; None drops the range."""
    if match.group(f"d{n}"):
        written = dates_by_end.get(match.end(f"d{n}"))
        if written is None:
            return None  # no real calendar date ("February 30, 2025")
        if not has_calendar:
            return written.year
        matched = match_period_end(written, calendar, "FY")
        return int(matched["fiscal_year"]) if matched["status"] == "matched" else None
    four = match.group(f"y{n}")
    return int(four) if four else 2000 + int(match.group(f"s{n}"))


def _stated_ranges(
    question: str,
    calendar: Sequence[Mapping[str, Any]],
    year_range: tuple[int, int],
) -> list[tuple[int, int, str]]:
    """Each fiscal-year range the question states, ``(lower, higher, text)``, in text order."""
    lo_bound, hi_bound = min(year_range), max(year_range)
    has_calendar = bool(_usable_rows(calendar))
    dates_by_end = {end: value for value, _start, end in find_written_dates(question)}
    found: list[tuple[int, int, int, str]] = []
    for regex in (SPAN_RANGE_REGEX, BETWEEN_RANGE_REGEX):
        for match in regex.finditer(question):
            first = match.start()
            if regex is BETWEEN_RANGE_REGEX:
                lead = _BETWEEN_LEAD.match(question, first)
                first = lead.end() if lead else first
            before = question[:first].split()[-_QUARTER_WINDOW_WORDS:]
            if mention_period_kind(" ".join([*before, match.group()]))[0] == "quarter":
                continue
            labels = [_endpoint_label(match, n, dates_by_end, calendar, has_calendar)
                      for n in (1, 2)]
            if labels[0] is None or labels[1] is None:
                continue
            lo, hi = min(labels[0], labels[1]), max(labels[0], labels[1])
            if lo_bound <= lo and hi <= hi_bound:
                found.append((match.start(), lo, hi, match.group()))
    return [(lo, hi, text) for _pos, lo, hi, text in sorted(found)]
