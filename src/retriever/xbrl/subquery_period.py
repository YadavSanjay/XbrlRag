# A dependent sub-query with no period of its own inherits the compound question's one year.
#
# The decomposer LLM sometimes writes a dependent sub-query ("For <<TOP:sq1:1>>, what is the
# percentage growth in capex?") without the fiscal year the compound question stated once, in
# its screen part. This module decides, deterministically and after the LLM, whether such a
# sub-query inherits that year and appends it to the text
# (docs/specs/dependent-subquery-period.md).
#
# Pure: no I/O, no clock, no logging, no LLM.

from __future__ import annotations

import re

from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.xbrl.fiscal_date import find_written_dates
from src.retriever.xbrl.query_decomposer import TOP_TOKEN_RE, SubQuery

# The period phrases EntityExtractor reads in a period mention, fixed here by the spec
# (Requirement 1.2). The bare word "current" is deliberately absent: in a whole question it
# is almost always a balance-sheet class ("current assets", "Current Ratio").
PERIOD_PHRASES: tuple[str, ...] = (
    # relative
    "most recent", "latest", "this year", "current year", "current fiscal year",
    "current quarter", "current fiscal quarter", "current period",
    # prior
    "prior year", "last year", "previous year", "prior fiscal year", "last fiscal year",
    "previous fiscal year", "preceding fiscal year", "year ago", "year-ago", "preceding year",
    # quarter
    "first quarter", "1st quarter", "q1", "second quarter", "2nd quarter", "q2",
    "third quarter", "3rd quarter", "q3", "fourth quarter", "4th quarter", "q4",
    # cumulative
    "year to date", "year-to-date", "ytd", "first half", "1st half", "six months",
    "six-month", "h1", "first nine months", "nine months", "nine-month", "9 months",
    "cumulative",
)

# The years the router's period resolution accepts (entity_extractor._MENTION_YEAR_RANGE).
_FISCAL_YEAR_BOUNDS = (2018, 2035)

# A phrase occurs with no letter or digit immediately before or after it.
_PHRASE_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(p) for p in sorted(PERIOD_PHRASES, key=len, reverse=True))
    + r")(?![a-z0-9])"
)
_CALENDAR_RE = re.compile(r"(?<![a-z0-9])calendar(?![a-z0-9])")
_WHITESPACE_RE = re.compile(r"\s+")

# docs/specs/subquery-period-phrases.md. A year form standing alone in normalised text:
# 20dd, fy20dd, fy 20dd, fydd, fy'dd, fy’dd (two digits read as 20dd).
_YEAR_FORM = r"(?:fy ?20\d\d|20\d\d|fy['’]?\d\d)"
# The phrases a year written right after them excuses (Requirement 1).
_YEAR_RELATIVE_PHRASES = frozenset(
    {"most recent", "latest", "this year", "current year", "current fiscal year"}
)
_EXCUSING_YEAR_RE = re.compile(
    r" (?:fiscal year |fiscal |year )?" + _YEAR_FORM + r"(?![a-z0-9])"
)
# A from-phrase in normalised text; group 1 is the year form (Requirement 2.1).
_FROM_PHRASE_RE = re.compile(
    r"(?<![a-z0-9])from (?:fiscal year |fiscal )?(" + _YEAR_FORM + r")(?![a-z0-9])"
)
# The same phrase in the original text: case-insensitive, each space one or more whitespace
# characters; group "year" is the part the rewrite replaces (Requirement 2.3).
_FROM_PHRASE_RAW_RE = re.compile(
    r"(?<![a-z0-9])from\s+"
    r"(?P<year>(?:fiscal\s+year\s+|fiscal\s+)?(?:fy\s*20\d\d|20\d\d|fy['’]?\d\d))"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)
_RANGE_END_RE = re.compile(r"(?:to|through|thru|until|till)(?![a-z0-9])")
_RANGE_END_MARKS = ("-", "–", "->", "→")
_GROWTH_WORD_RE = re.compile(
    r"(?<![a-z0-9])(?:grow|grows|grew|grown|growing|growth|change|changes|changed|increase"
    r"|increases|increased|decrease|decreases|decreased|rise|rises|rose|risen|fall|falls|fell"
    r"|fallen|decline|declines|declined|cagr)(?![a-z0-9])"
)


def _normalized(text: str) -> str:
    """Lowercase ``text`` and collapse each run of whitespace to one space."""
    return _WHITESPACE_RE.sub(" ", text.lower())


def _without_top_tokens(text: str) -> str:
    """``text`` with every ``<<TOP:id:n>>`` token removed."""
    return TOP_TOKEN_RE.sub("", text)


def _has_period_phrase(text: str) -> bool:
    """Whether a period phrase of Requirement 1.2 occurs in ``text``."""
    return _PHRASE_RE.search(_normalized(text)) is not None


def _year_of_form(form: str) -> int:
    """The year a normalised year form names (``fy'24`` is 2024)."""
    digits = re.sub(r"\D", "", form)
    return int(digits) if len(digits) == 4 else 2000 + int(digits)


def _states_exactly(text: str, year: int) -> bool:
    """Whether a sub-query text states exactly the one fiscal year ``year`` and nothing else."""
    body = _without_top_tokens(text)
    return (
        extract_years_in_range(body) == [year]
        and not states_period_phrase(body)
        and not find_written_dates(body)
    )


def _with_year(text: str, year: int) -> str:
    """``text`` with " for fiscal year <year>" appended before a final "?" or "."."""
    t = text.rstrip()
    if t.endswith(("?", ".")):
        return f"{t[:-1].rstrip()} for fiscal year {year}{t[-1]}"
    return f"{t} for fiscal year {year}"


def has_own_period(text: str) -> bool:
    """Return whether a sub-query text states a period of its own.

    Args:
        text: The sub-query text; its ``<<TOP:id:n>>`` tokens never count.

    Returns:
        True when it writes a year or a period phrase (spec Requirement 1); else False.
    """
    body = _without_top_tokens(text)
    return bool(extract_years_in_range(body)) or _has_period_phrase(body)


def stated_fiscal_year(query: str) -> int | None:
    """Return the one annual fiscal year a compound question states, if it states exactly one.

    Args:
        query: The whole compound question.

    Returns:
        The year (2018-2035), or None for no year, several years, a range, a quarter, a
        relative period, a full date or a calendar year (spec Requirement 2).
    """
    years = extract_years_in_range(query)
    if len(years) != 1:
        return None
    year = years[0]
    lo, hi = _FISCAL_YEAR_BOUNDS
    if not lo <= year <= hi:
        return None
    if states_period_phrase(query) or find_written_dates(query):
        return None
    if _CALENDAR_RE.search(_normalized(query)):
        return None
    return year


def inherit_dependent_periods(query: str, subqueries: list[SubQuery]) -> list[SubQuery]:
    """Append the question's one fiscal year to each dependent sub-query that states no period.

    A dependent sub-query whose only year is a lone "from <year>" with growth or change
    wording is instead rewritten to run from that base year to its end year
    (subquery-period-phrases Requirement 3.2), whatever the question states.

    Args:
        query: The whole compound question.
        subqueries: The decomposer's sub-queries, in dependency order; not modified.

    Returns:
        A new list with the same ids, order and ``depends_on``; only inheriting or rewritten
        sub-queries have a changed text (spec Requirement 3).
    """
    year = stated_fiscal_year(query)
    ids = {sq.id for sq in subqueries}
    returned: dict[str, SubQuery] = {}
    out: list[SubQuery] = []
    for sq in subqueries:
        text = sq.query
        dependent = bool(sq.depends_on) or bool(sq.top_refs)
        if dependent and from_base_year(sq.query) is not None:
            text = with_from_end_year(query, sq.query)
        elif year is not None and dependent and not has_own_period(sq.query):
            dep_ids = [d for d in [*sq.depends_on, *(ref for ref, _n in sq.top_refs)]
                       if d in ids and d != sq.id]
            # A dependency is read as already returned by this walk, so a chain inherits
            # consistently; one listed later (not yet walked) is read as given.
            deps = [returned.get(d) or next(x for x in subqueries if x.id == d)
                    for d in dep_ids]
            if all(not has_own_period(d.query) or _states_exactly(d.query, year)
                   for d in deps):
                text = _with_year(sq.query, year)
        new = SubQuery(id=sq.id, query=text, depends_on=list(sq.depends_on))
        returned.setdefault(sq.id, new)
        out.append(new)
    return out


# --------------------------------------------------------------------------- period phrases
# docs/specs/subquery-period-phrases.md: a year-relative phrase directly followed by a year
# states that year; a dependent sub-query's lone "from <year>" is a base year.


def states_period_phrase(text: str) -> bool:
    """Return whether ``text`` holds a period phrase not excused by a year right after it.

    Args:
        text: A question or sub-query text.

    Returns:
        True when an occurrence of a Requirement 1.2 phrase of dependent-subquery-period is
        not a year-relative phrase immediately followed by an explicit year
        (subquery-period-phrases Requirement 1); else False.
    """
    norm = _normalized(text)
    for m in _PHRASE_RE.finditer(norm):
        if m.group(0) in _YEAR_RELATIVE_PHRASES and _EXCUSING_YEAR_RE.match(norm, m.end()):
            continue
        return True
    return False


def from_base_year(text: str) -> int | None:
    """Return the base year of a sub-query whose only stated year is the object of "from".

    Args:
        text: The sub-query text; its ``<<TOP:id:n>>`` tokens never count.

    Returns:
        The year (2018-2035) when the text's one stated year is written "from <year>" and
        no end year, period phrase or date is stated, and a growth or change word occurs
        (subquery-period-phrases Requirement 2.1); else None.
    """
    body = _without_top_tokens(text)
    years = extract_years_in_range(body)
    if len(years) != 1:
        return None
    base = years[0]
    lo, hi = _FISCAL_YEAR_BOUNDS
    if not lo <= base <= hi:
        return None
    norm = _normalized(body)
    match = next((m for m in _FROM_PHRASE_RE.finditer(norm)
                  if _year_of_form(m.group(1)) == base), None)
    if match is None:
        return None
    after = norm[match.end():].lstrip(" ")
    if _RANGE_END_RE.match(after) or after.startswith(_RANGE_END_MARKS):
        return None
    if states_period_phrase(body) or find_written_dates(body):
        return None
    if _GROWTH_WORD_RE.search(norm) is None:
        return None
    return base


def from_end_year(query: str, base_year: int) -> int:
    """Return the end year a lone "from <base_year>" runs to.

    Args:
        query: The whole compound question.
        base_year: The base year a sub-query states with "from".

    Returns:
        The question's one stated year later than ``base_year`` when exactly one is stated,
        else ``base_year + 1`` (subquery-period-phrases Requirement 2.2).
    """
    later = {y for y in extract_years_in_range(query) if y > base_year}
    return later.pop() if len(later) == 1 else base_year + 1


def with_from_end_year(query: str, text: str) -> str:
    """Return ``text`` with its lone "from <year>" rewritten to state the base and end years.

    Args:
        query: The whole compound question.
        text: The sub-query text.

    Returns:
        ``text`` with the year form after "from" replaced by "fiscal year B to fiscal year E"
        when ``from_base_year(text)`` is B, else ``text`` unchanged
        (subquery-period-phrases Requirement 2.3).
    """
    base = from_base_year(text)
    if base is None:
        return text
    match = _FROM_PHRASE_RAW_RE.search(text)
    if match is None:
        return text
    end = from_end_year(query, base)
    return (f"{text[: match.start('year')]}fiscal year {base} to fiscal year {end}"
            f"{text[match.end('year'):]}")
