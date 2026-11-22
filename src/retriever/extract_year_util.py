import re
from datetime import datetime
from typing import List, Optional

# --- 1) Token extraction: FY, fiscal year, quarters, apostrophes, mixed 2021/22, FY22–23, etc. ---
YEAR_TOKEN_REGEX = re.compile(
    r"""
    (?<!\w)
    (?P<q>Q[1-4])?                          # optional quarter like Q1
    \s*
    (?P<fy>FY|F\.Y\.|fiscal\s+year(?:s)?)?  # optional FY marker
    \s*
    (?:
        (?P<year4a>(?:19|20)\d{2})\s*(?:/|[-–])\s*(?P<year2c>\d{2})   # 2021/22
        | (?P<year4>(?:19|20)\d{2})                                  # 2021
        | [’'](?P<year2_ap>\d{2})                                    # FY’21 / FY'21
        | (?P<year2a>\d{2})\s*(?:/|[-–])\s*(?P<year2b>\d{2})         # FY22–23 / 22/23
        | (?P<year2>\d{2})                                           # FY23 (2-digit only if FY/Q present)
    )
    (?!\w)
    """,
    re.IGNORECASE | re.VERBOSE,
)

def _infer_four_digit_from_two_digit(
    yy: int, lo: int, hi: int, anchor_year4: int | None = None
) -> int | None:
    """Convert 2-digit year -> 4-digit year using anchor if present, else choose a century that lands in [lo, hi]."""
    if anchor_year4 is not None:
        candidate = (anchor_year4 // 100) * 100 + yy
        return candidate if lo <= candidate <= hi else None

    base_century = (lo // 100) * 100
    for century in (base_century - 100, base_century, base_century + 100):
        candidate = century + yy
        if lo <= candidate <= hi:
            return candidate
    return None

# --- 2) Range expansion (docs/specs/fiscal-year-range.md) ---
# A range is two endpoints joined by "through" / "thru" / "to" / a hyphen or en dash (with or
# without spaces) / "->" / "→", optionally led by "from"; or "between P1 and P2" where "between"
# immediately precedes P1. Each endpoint is FY2023, FY 2023, FY23, FY'23, FY’23, fiscal 2023,
# fiscal year(s) 2023, a bare 2023, or a year-end date ("the fiscal year ended June 30, 2023",
# "dec 31 2023"). It expands to every year from the lower endpoint through the higher one,
# unless it names more than MAX_RANGE_YEARS years: then only its endpoints are kept.

# The longest range, in fiscal years counted inclusively, that is expanded.
MAX_RANGE_YEARS = 10

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)


def _endpoint(n: int) -> str:
    """The regex for one range endpoint, its year in group ``d<n>`` / ``y<n>`` / ``s<n>``."""
    return (
        rf"(?:(?:the\s+)?(?:fiscal\s+year\s+ended\s+)?{_MONTH}\.?\s+\d{{1,2}},?\s+"
        rf"(?P<d{n}>(?:19|20)\d{{2}})"                        # a year-end date
        rf"|(?:FY\s*|fiscal\s+(?:years?\s+)?)?(?P<y{n}>(?:19|20)\d{{2}})"  # FY2023 / 2023
        rf"|FY\s*['’]?(?P<s{n}>\d{{2}}))"                     # FY23 / FY'23
    )


_SPAN_SEPARATOR = r"(?:\s+(?:through|thru|to)\s+|\s*(?:->|→|[-–])\s*)"
SPAN_RANGE_REGEX = re.compile(
    rf"(?<!\w){_endpoint(1)}{_SPAN_SEPARATOR}{_endpoint(2)}(?!\w)", re.IGNORECASE
)
BETWEEN_RANGE_REGEX = re.compile(
    rf"\bbetween\s+{_endpoint(1)}\s+and\s+{_endpoint(2)}(?!\w)", re.IGNORECASE
)
# The split-year forms "FY21-23" / "2021-22", read as a range as before this change; never
# the year and month of an ISO date ("2023-12-31").
SPLIT_YEAR_REGEX = re.compile(
    r"(?<![\w/–-])(?:FY\s*)?(?P<y1>(?:19|20)\d{2}|\d{2})\s*[-–]\s*(?P<y2>\d{2})(?!\w|[-–/]\d)",
    re.IGNORECASE,
)


def _endpoint_year(match: re.Match[str], n: int) -> int:
    """The year endpoint *n* of a range match writes (``20yy`` for a two-digit one)."""
    four = match.group(f"d{n}") or match.group(f"y{n}")
    return int(four) if four else 2000 + int(match.group(f"s{n}"))


def _year_ranges(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Every range in *text* as ``(lower, higher)``, both endpoints within ``[lo, hi]``."""
    spans: list[tuple[int, int]] = []
    for regex in (SPAN_RANGE_REGEX, BETWEEN_RANGE_REGEX):
        for m in regex.finditer(text):
            spans.append((_endpoint_year(m, 1), _endpoint_year(m, 2)))
    for m in SPLIT_YEAR_REGEX.finditer(text):
        y1s, y2s = m.group("y1"), m.group("y2")
        anchor = int(y1s) if len(y1s) == 4 else None
        y1 = _normalize_year_token(y1s, lo, hi, anchor_year4=anchor)
        y2 = _normalize_year_token(y2s, lo, hi, anchor_year4=anchor)
        if y1 is not None and y2 is not None:
            spans.append((y1, y2))
    ordered = [(min(a, b), max(a, b)) for a, b in spans]
    return [(a, b) for a, b in ordered if lo <= a and b <= hi]


def overlong_year_ranges(text: str, year_range: list[int] | None = None) -> list[tuple[int, int]]:
    """The ranges in *text* longer than ``MAX_RANGE_YEARS``, which are not expanded.

    Args:
        text: A question or a period mention.
        year_range: The ``[lo, hi]`` bounds an endpoint must fall in; default ``[2000, 2100]``.

    Returns:
        Each such range as ``(lower, higher)``.
    """
    start, end = year_range or [2000, 2100]
    lo, hi = min(start, end), max(start, end)
    return [(a, b) for a, b in _year_ranges(text, lo, hi) if b - a + 1 > MAX_RANGE_YEARS]


def _normalize_year_token(token: str, lo: int, hi: int, anchor_year4: int | None = None) -> int | None:
    """Turn '2021' or '21' into a 4-digit year consistent with [lo, hi]."""
    if len(token) == 4:
        y = int(token)
        return y if lo <= y <= hi else None
    if len(token) == 2:
        return _infer_four_digit_from_two_digit(int(token), lo, hi, anchor_year4=anchor_year4)
    return None


def extract_years_in_range(text: str, year_range: list[int] = [2000, 2100]) -> list[int]:
    """The fiscal years *text* writes inside the bounds, sorted.

    Ranges are expanded (one over ``MAX_RANGE_YEARS`` keeps only its endpoints); two-digit
    years are read with their context. A text that writes no year inside the bounds gives
    ``[]``: there is no default year (docs/specs/no-default-year.md).

    Args:
        text: A question or a period mention.
        year_range: The ``[lo, hi]`` bounds a year must fall in; default ``[2000, 2100]``.

    Returns:
        The years written, ascending; empty when none is written.
    """
    start, end = year_range
    lo, hi = (start, end) if start <= end else (end, start)

    years: set[int] = set()

    # A) Expand explicit ranges; one over MAX_RANGE_YEARS keeps only its endpoints.
    for a, b in _year_ranges(text, lo, hi):
        if b - a + 1 > MAX_RANGE_YEARS:
            years.update((a, b))
        else:
            years.update(range(a, b + 1))

    # B) Extract non-range tokens (FY’21, 2021/22, FY22–23, Q1 FY23, etc.)
    for m in YEAR_TOKEN_REGEX.finditer(text):
        gd = m.groupdict()
        has_context = bool(gd["fy"] or gd["q"])

        if gd["year4a"] and gd["year2c"]:
            y1 = int(gd["year4a"])
            y2 = _infer_four_digit_from_two_digit(int(gd["year2c"]), lo, hi, anchor_year4=y1)
            if lo <= y1 <= hi:
                years.add(y1)
            if y2 is not None:
                years.add(y2)

        elif gd["year4"]:
            y = int(gd["year4"])
            if lo <= y <= hi:
                years.add(y)

        elif gd["year2_ap"]:
            y = _infer_four_digit_from_two_digit(int(gd["year2_ap"]), lo, hi)
            if y is not None:
                years.add(y)

        elif gd["year2a"] and gd["year2b"]:
            y1 = _infer_four_digit_from_two_digit(int(gd["year2a"]), lo, hi)
            y2 = _infer_four_digit_from_two_digit(int(gd["year2b"]), lo, hi)
            if y1 is not None:
                years.add(y1)
            if y2 is not None:
                years.add(y2)

        elif gd["year2"] and has_context:
            y = _infer_four_digit_from_two_digit(int(gd["year2"]), lo, hi)
            if y is not None:
                years.add(y)

    # No year written inside the bounds reads as no year, never a default
    # (docs/specs/no-default-year.md); a caller that wants a default names its own.
    return sorted(years)


if __name__ == "__main__":    # Example usage: this text writes no year, so it prints [].
    sample_text = """
    Amazon’s consolidated operating income rose sharply. Decompose the change in consolidated operating income into (i) North America, (ii) International, and (iii) AWS segment operating profit changes. Which segment contributed the majority of the improvement, and what percent of the total improvement did it represent?
    """
    extracted_years = extract_years_in_range(sample_text)
    print("Extracted years:", extracted_years)
