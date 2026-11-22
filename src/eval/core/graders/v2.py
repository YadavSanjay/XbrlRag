# Grading the v2 and H-X cases by their real semantics: trends, point-in-time pairs,
# aggregates, peer-relative ranks, compounds, honesty sub-types and screen disclosures.
#
# Each v2 case is mapped onto an EvalCase kind by its emitter (hx-authoring,
# v2-suite-build); its real semantics travel in tags and extra keys. v2_subkind reads them,
# grade_v2 grades by them and records which sub-checks passed. Deterministic: no LLM.
# Spec: docs/specs/v2-grading.md.

from __future__ import annotations

import dataclasses
import functools
import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, TypeGuard

from src import PROJECT_ROOT
from src.eval.core.answer_parsing import (
    aggregate_sentences,
    answer_prose,
    blank_disclosures,
    extract_stated_numbers,
)
from src.eval.core.case import (
    KIND_BREAKDOWN,
    KIND_COMPUTE,
    KIND_NARRATIVE,
    KIND_SCREEN,
    EvalCase,
)
from src.eval.core.graders.base import CaseGrade, Grade
from src.eval.core.graders.breakdown import compare_breakdown
from src.eval.core.graders.citation import grade_grounding
from src.eval.core.graders.dispatch import (
    _base,
    grade_legacy,
    grade_narrative,
    grade_scalar,
    grade_screen,
    graded_answer,
    row_matcher,
)
from src.eval.core.graders.numeric import close_enough, match_reading
from src.eval.core.graders.precision import shape_of
from src.eval.core.graders.screen import row_entity, row_value
from src.eval.core.system import SystemAnswer
from src.eval.suites.intents import load_lexicon

# --- requirement 2: sub-kind tables ----------------------------------------------------
_SHAPE_SUBKINDS = {
    "screen.top": "screen",
    "screen.bottom": "screen",
    "screen.sum": "sum",
    "screen.median": "median",
    "peer_relative": "peer_relative",
    "trend": "trend",
    "compute.cagr": "cagr",
    "compute.ratio": "ratio",
    # Generated compounds (docs/specs/compound-shape.md requirement 6).
    "compound.rank_by_ratio": "compound",
    "compound.leader_growth": "compound",
}
# (H-X kind tag, sub-type tag or None) -> sub-kind; a None sub-type needs the kind only.
_HX_SUBKINDS = {
    ("cross_filer", "screen_top"): "screen",
    ("cross_filer", "screen_bottom"): "screen",
    ("cross_filer", "count"): "count",
    ("cross_filer", "sum"): "sum",
    ("cross_filer", "median"): "median",
    ("cross_filer", "peer_relative"): "rank",
    ("cross_period", "trend"): "trend",
    ("cross_period", "cagr"): "cagr",
    ("cross_period", "point_in_time"): "point_in_time",
    ("compound", None): "compound",
    ("honesty", "ambiguous"): "ambiguous",
    ("honesty", "false_premise"): "false_premise",
    ("honesty", "decline"): "decline",
    ("text", None): "text",
}
_SCALAR_SUBKINDS = frozenset({"count", "sum", "median", "rank", "cagr", "ratio"})

# --- requirement 8: the S4 closing-balance fallback ------------------------------------
FALLBACK_PHRASE = "year end|closing balance|ending balance|end of year"

# --- requirement 13: point-in-time labels ----------------------------------------------
ORIGINAL_LABEL_PHRASE = (
    "originally reported|as reported|original|initially reported|first reported"
)
LATEST_LABEL_PHRASE = "latest|restated|recast|revised|most recent|updated"

# --- requirement 16: false-premise lexicons --------------------------------------------
UP_WORDS = frozenset({
    "rose", "rise", "rises", "risen", "rising", "increase", "increased", "increases",
    "increasing", "grew", "grow", "grows", "grown", "growing", "higher", "gained",
    "climbed",
})
DOWN_WORDS = frozenset({
    "fell", "fall", "falls", "fallen", "falling", "decrease", "decreased", "decreases",
    "decreasing", "decline", "declined", "declines", "declining", "dropped", "drop",
    "drops", "dropping", "lower", "shrank", "shrunk", "contracted",
})
NEGATION_WORDS = frozenset({
    "not", "no", "never", "didnt", "wasnt", "isnt", "hasnt", "havent", "doesnt", "nor",
    "neither",
})
# How many tokens before a premise word a negation may stand.
NEGATION_WINDOW = 3

# --- requirement 17: decline reasons ---------------------------------------------------
REASON_PHRASES = {
    "out_of_corpus": (
        "not in the corpus|outside the corpus|not covered|no filings|not included|"
        "not part of|do not have|dont have|not available|no data"
    ),
    "out_of_period": (
        "not yet filed|not been filed|has not filed|hasnt filed|not yet available|"
        "outside the period|no data for|not yet"
    ),
    "not_tagged": (
        "not tagged|not reported|does not report|doesnt report|does not disclose|"
        "doesnt disclose|not disclosed|no xbrl|not separately"
    ),
    "out_of_scope": (
        "outside the scope|out of scope|outside my scope|not covered|proxy|non gaap|"
        "earnings release|compensation|adjusted"
    ),
}

# --- requirement 7: disclosure wording -------------------------------------------------
_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty",
)
_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)
# A standalone integer: not glued to a word, a decimal or a thousands group.
_INTEGER = re.compile(r"(?<![\w.,])(\d+)(?!\w|[.,]\d)")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")

# --- compound-shape requirement 6: which filer an answer names as highest ---------------
SUPERLATIVE_WORDS = ("highest", "largest", "greatest", "best", "top", "leads", "led")
_SUPERLATIVE = re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(SUPERLATIVE_WORDS)})(?![A-Za-z0-9])",
                          re.IGNORECASE)
# A sentence ends at . ! or ? followed by whitespace or the end ("30.0%" never ends one).
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")

# --- grader-pick-echo-grow requirement 1: a merged answer's `[sq<N>]` echo line --------
_SUBQUERY_ECHO_LINE = re.compile(r"[ \t]*\[sq[0-9]+\]")

# --- pick-skips-questions requirement 4 / grader-pick-echo-grow requirement 3: a growth
# key asked for an amount ("change", or "how much" + a movement verb) -------------------
_CHANGE_WORD = re.compile(r"(?<![A-Za-z0-9])(?:change|changed|changes)(?![A-Za-z0-9])",
                          re.IGNORECASE)
HOW_MUCH_PHRASE = "how much"
MOVEMENT_VERBS = ("grow", "grows", "grew", "grown", "growing",
                  "increase", "increases", "increased", "increasing",
                  "decrease", "decreases", "decreased", "decreasing",
                  "rise", "rises", "rose", "risen", "rising",
                  "fall", "falls", "fell", "fallen", "falling",
                  "decline", "declines", "declined", "declining")
_HOW_MUCH = re.compile(
    r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, HOW_MUCH_PHRASE.split()))
    + r"(?![A-Za-z0-9])", re.IGNORECASE)
_MOVEMENT_VERB = re.compile(
    rf"(?<![A-Za-z0-9])(?:{'|'.join(MOVEMENT_VERBS)})(?![A-Za-z0-9])", re.IGNORECASE)
# The noun "growth" stays a cue; the verb forms of "grow" are movement verbs, not cues.
PERCENT_CUE_WORDS = ("percent", "percentage", "percentages", "per cent", "pct", "rate",
                     "rates", "growth", "cagr")
_PERCENT_CUE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in PERCENT_CUE_WORDS)
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE)
# How closely a leg pair must reproduce the key's growth to be its legs.
_GROWTH_REPRODUCE_TOL = 1e-9
_GROWTH_SHAPE = "compute.yoy_growth"
_DIFFERENCE_TAG = "shape:compute.difference"

_DISCLOSURE_KEYS = frozenset({"universe", "exclusions", "period_ends"})
_FALLBACK_KEY = "fallback_disclosed"

# --- grading-dont-care-and-ties --------------------------------------------------------
# The one exclusion reason a human reviewed as "the filer's statement does not answer the
# question" (requirement 1), and how many such members a case may carry (2^6 readings).
DONT_CARE_REASON = "resolved_drop"
MAX_DONT_CARE = 6
# How closely the ranked members' input values must reproduce a median key (requirement 3).
_MEDIAN_REPRODUCE_TOL = 1e-9


def v2_subkind(case: EvalCase) -> str | None:
    """The case's v2 sub-kind from its ``shape:`` tag or its H-X kind and sub-type tags.

    Args:
        case: Any eval case.

    Returns:
        One of ``screen``, ``count``, ``sum``, ``median``, ``rank``, ``peer_relative``,
        ``trend``, ``cagr``, ``ratio``, ``point_in_time``, ``compound``, ``ambiguous``,
        ``false_premise``, ``decline``, ``text``; ``None`` for a case graded as before.
    """
    tags = [str(t) for t in case.tags]
    for tag in tags:
        if tag.startswith("shape:"):
            return _SHAPE_SUBKINDS.get(tag[len("shape:"):])
    present = set(tags)
    for (kind, subtype), subkind in _HX_SUBKINDS.items():
        if kind in present and (subtype is None or subtype in present):
            return subkind
    return None


def text_part(case: EvalCase) -> EvalCase | None:
    """The compound's judged text part, built as a part case (compound-text-part, req. 1).

    Args:
        case: Any eval case.

    Returns:
        The first ``narrative`` part with a reference answer and key points, as an
        ``EvalCase``; ``None`` for a non-compound case or a compound without one.
    """
    index = _text_part_index(case)
    return None if index is None else _part_case(case, case.parts[index])


def _text_part_index(case: EvalCase) -> int | None:
    """The position in ``case.parts`` of a compound's judged text part, or ``None``."""
    if v2_subkind(case) != "compound":
        return None
    for i, p in enumerate(case.parts or []):
        if not isinstance(p, dict) or p.get("kind") != KIND_NARRATIVE:
            continue
        text = p.get("expected_text")
        if isinstance(text, str) and text.strip() and p.get("key_points"):
            return i
    return None


def _part_case(case: EvalCase, part: dict) -> EvalCase:
    """A compound part as its own case, as v2-grading requirement 14 builds it."""
    fields = {k: v for k, v in part.items() if k != "part"}
    fields.update(id=f"{case.id}#{part.get('part')}", question=case.question)
    return EvalCase.from_dict(fields)


def grade_v2(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grades a case with a v2 sub-kind and records its sub-checks in ``checks``.

    Args:
        case: A case whose ``v2_subkind`` is not ``None``.
        answer: The system's answer.

    Returns:
        The grade, with ``checks`` set (``None`` for a text case).

    Raises:
        ValueError: When the case has no v2 sub-kind.
    """
    subkind = v2_subkind(case)
    if subkind is None:
        raise ValueError(f"case {case.id!r} has no v2 sub-kind; grade it with grade_case")
    # Stored values that are only date parts are not produced values (requirement 7).
    answer = graded_answer(answer)
    # The text readers read a JSON reply's prose, as they read XbrlRag's
    # (docs/specs/prose-and-ranking-names.md requirement 1); the stored answer keeps its text.
    text = answer.text
    prose = answer_prose(text) if isinstance(text, str) else text
    read = answer if prose == text else dataclasses.replace(answer, text=prose)
    if subkind == "rank":  # H-X peer-relative (docs/specs/hx-peer-rank.md requirement 1)
        grade = _grade_hx_rank(case, read)
    elif subkind in _SCALAR_SUBKINDS:
        grade = _grade_scalar_in_context(case, read)
    else:
        grade = _GRADERS[subkind](case, read)
    if read is not answer and isinstance(grade.produced, dict) and "text" in grade.produced:
        grade.produced["text"] = text
    return grade


def states_phrase(text: str, phrase: str) -> bool:
    """Whether the text states the phrase (or one of its ``|`` alternatives), normalised."""
    haystack = f" {_normalise(text)} "
    for alternative in phrase.split("|"):
        needle = _normalise(alternative)
        if needle and f" {needle} " in haystack:
            return True
    return False


def disclosure_checks(
    case: EvalCase, text: str, aliases: Mapping[str, Sequence[str]] | None = None
) -> dict[str, bool]:
    """The S2/S3 disclosure checks of a cross-filer case against an answer's text.

    Args:
        case: The case; its disclosure data is ``disclosures`` (v2) or ``universe`` and
            ``expected_exclusions`` (H-X).
        text: The answer's text.
        aliases: Optional other names per ticker.

    Returns:
        ``{"universe", "exclusions"}`` plus ``"period_ends"`` when required; ``{}`` when
        the case has no disclosure data.
    """
    aliases = aliases or {}
    name: str | None = None
    period_ends: list[str] = []
    if isinstance(case.disclosures, dict):
        d = case.disclosures
        ranked = [str(t) for t in d.get("ranked") or []]
        all_exclusions = _tickers_of(d.get("excluded") or [])
        members = ranked + all_exclusions
        counts = {len(members), len(ranked)}
        # A don't-care member need not be named, and may be counted as ranked
        # (grading-dont-care-and-ties requirement 3).
        dont_care = dont_care_members(case)
        exclusions = [t for t in all_exclusions if t not in dont_care]
        counts |= {len(ranked) + k for k in range(1, len(dont_care) + 1)}
        if isinstance(d.get("universe"), str):
            name = d["universe"].replace("_", " ")
        if d.get("disclose_period_ends"):
            ends = d.get("period_ends") or {}
            period_ends = [str(ends[t]) for t in ranked if ends.get(t)]
    elif case.universe:
        members = [str(t) for t in case.universe]
        exclusions = _tickers_of(case.expected_exclusions)
        counts = {len(members), len(members) - len(case.expected_exclusions)}
    else:
        return {}

    def named(ticker: str) -> bool:
        return _names_ticker(text, ticker, aliases)

    universe = (
        _states_count(text, counts)
        or (bool(members) and all(named(t) for t in members))
        or (name is not None and states_phrase(text, name))
    )
    checks = {"universe": universe, "exclusions": all(named(t) for t in exclusions)}
    if period_ends:
        checks["period_ends"] = all(_states_date(text, e) for e in period_ends)
    return checks


def stated_ranks(text: str, ticker: str, names: Mapping[str, Sequence[str]]) -> list[int]:
    """Every rank the text attributes to ``ticker``, in text order.

    Spec: docs/specs/peer-rank-grading.md (requirement 1).

    Args:
        text: The answer's text.
        ticker: The subject's ticker.
        names: Every candidate ticker -> its other names; ``ticker`` is a candidate too.

    Returns:
        The ranks (1 = highest) of the rank expressions ``ticker`` owns.
    """
    mentions = _mentions(text, ticker, names)
    sentences = _sentence_spans(text, mentions)
    owned: list[int] = []
    for expr in _rank_expressions(text):
        if _owner(text, expr, mentions, sentences, ticker) == ticker:
            owned.append(expr.rank)
    return owned


def is_count_phrase(text: str, start: int) -> bool:
    """Whether the rank word at ``start`` has a first-person subject ("I ranked 32 of 33").

    Spec: docs/specs/peer-rank-count-phrases.md (requirement 1). Such a phrase counts the
    companies the author ranked; it is not a rank.

    Args:
        text: The answer's text.
        start: The index where a rank word (rank, ranks, ranked, ranking) may begin.

    Returns:
        True when a rank word begins at ``start`` and is directly preceded, through one
        space, by I, we, I've, we've, I have or we have.
    """
    if not 0 < start < len(text) or not _RANK_WORD_AT.match(text, start):
        return False
    return _FIRST_PERSON_BEFORE.search(text[:start]) is not None


def value_rank(rows: Sequence[Any], ticker: str, names: Mapping[str, Sequence[str]],
               n_ranked: int) -> int | None:
    """The subject's rank implied by the rows' values (requirement 2), order-free.

    Args:
        rows: The answer's rows.
        ticker: The subject's ticker.
        names: Candidate ticker -> its other names, to map rows named by a name.
        n_ranked: How many filers the key ranked; fewer valued entities imply no rank.

    Returns:
        The rank (value descending, ties by ticker ascending), or None.
    """
    by_name: dict[str, str] = {}
    for candidate, aliases in names.items():
        for alias in aliases:
            by_name.setdefault(str(alias).casefold(), candidate)
    candidates = {*names, ticker}
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        entity, value = row_entity(row), row_value(row)
        if entity is None or value is None:
            continue
        if entity not in candidates:
            entity = by_name.get(entity.casefold(), entity)
        values.setdefault(entity, value)
    if ticker not in values or len(values) < max(n_ranked, 2):
        return None
    own = values[ticker]
    return 1 + sum(
        1 for entity, value in values.items()
        if entity != ticker and (value > own or (value == own and entity < ticker))
    )


def dont_care_members(case: EvalCase) -> dict[str, float]:
    """The case's "don't care" members: reviewed drops whose key value the case records.

    Spec: docs/specs/grading-dont-care-and-ties.md (requirement 1).

    Args:
        case: Any eval case.

    Returns:
        Ticker -> value for every ``disclosures["excluded"]`` entry whose reason is
        ``resolved_drop`` and which carries a finite numeric ``value``; empty otherwise.

    Raises:
        ValueError: When the case has more than six such members.
    """
    d = case.disclosures
    if not isinstance(d, dict):
        return {}
    out: dict[str, float] = {}
    for entry in d.get("excluded") or []:
        if not isinstance(entry, dict) or entry.get("reason") != DONT_CARE_REASON:
            continue
        ticker, value = entry.get("ticker"), entry.get("value")
        if not isinstance(ticker, str) or not ticker or not _is_numeric(value):
            continue
        out.setdefault(ticker, float(value))
    if len(out) > MAX_DONT_CARE:
        raise ValueError(
            f"case {case.id!r} has {len(out)} don't-care members (at most {MAX_DONT_CARE})"
        )
    return out


def _is_numeric(value: Any) -> TypeGuard[int | float]:
    """An int or float, not a bool, and finite (requirement 1)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _readings(dont_care: Mapping[str, float]) -> list[tuple[str, ...]]:
    """Every subset of the don't-care tickers: by size, then by sorted tickers (req. 3)."""
    tickers = sorted(dont_care)
    return [combo for size in range(len(tickers) + 1)
            for combo in combinations(tickers, size)]


def _with_reading(grade: CaseGrade, subset: Sequence[str]) -> CaseGrade:
    """Names a non-key reading in the grade's reason, so a re-grade diff can count it."""
    grade.reason = f"{grade.reason}; reading: with {', '.join(subset)}"
    return grade


def peer_names(case: EvalCase) -> dict[str, list[str]]:
    """The candidate tickers of a peer-relative case and their names (requirement 3).

    Args:
        case: A peer-relative case.

    Returns:
        Ticker -> names: the case's ``subject["names"]`` first, then the v2 lexicon's.
    """
    subject = case.subject if isinstance(case.subject, dict) else {}
    raw_names = subject.get("names")
    case_names: dict[Any, Any] = raw_names if isinstance(raw_names, dict) else {}
    disclosures = case.disclosures if isinstance(case.disclosures, dict) else {}
    keys: list[str] = []
    sources: list[Any] = [subject.get("ticker")]
    sources += list(disclosures.get("ranked") or [])
    sources += _tickers_of(disclosures.get("excluded") or [])
    sources += [r.get("ticker") for r in case.expected_rows or [] if isinstance(r, dict)]
    sources += list(case_names)
    # H-X keys (docs/specs/hx-peer-rank.md requirement 5); a v2 case has none of them.
    sources += [case.ticker, *(case.universe or [])]
    sources += _tickers_of(case.expected_exclusions or [])
    for source in sources:
        key = str(source or "").strip().upper()
        if key and key not in keys:
            keys.append(key)
    case_by_key = {str(k).strip().upper(): v for k, v in case_names.items()}
    companies = _lexicon_companies()
    out: dict[str, list[str]] = {}
    for key in keys:
        found = [str(n) for n in case_by_key.get(key) or [] if str(n).strip()]
        for form in ("short", "legal"):
            name = companies.get(key, {}).get(form)
            if name and name not in found:
                found.append(name)
        out[key] = found
    return out


# ---------------------------------------------------------------------------------------
# Peer-rank helpers (docs/specs/peer-rank-grading.md requirement 1)
# ---------------------------------------------------------------------------------------

_LEXICON_PATH = Path(PROJECT_ROOT) / "configs" / "eval" / "v2" / "lexicon.yaml"

_ORDINAL_UNITS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh",
                  "eighth", "ninth")
_ORDINAL_TEENS = ("tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth",
                  "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth")


def _ordinal_words() -> dict[str, int]:
    """Ordinal word (with ``-`` between the parts) -> its number, for 1-49."""
    words = {w: i + 1 for i, w in enumerate(_ORDINAL_UNITS)}
    words.update({w: i + 10 for i, w in enumerate(_ORDINAL_TEENS)})
    words.update({"thirtieth": 30, "fortieth": 40})
    for tens, base in (("twenty", 20), ("thirty", 30), ("forty", 40)):
        for i, unit in enumerate(_ORDINAL_UNITS):
            words[f"{tens}-{unit}"] = base + i + 1
    return words


_ORDINAL_WORDS = _ordinal_words()
_WORD_ORDINAL_SOURCE = "|".join(
    w.replace("-", "[- ]") for w in sorted(_ORDINAL_WORDS, key=len, reverse=True)
)
_ORDINAL_SOURCE = rf"(?:\d+(?:st|nd|rd|th)|{_WORD_ORDINAL_SOURCE})"
_LB, _LA = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_ORDINAL = re.compile(rf"{_LB}(?P<ord>{_ORDINAL_SOURCE}){_LA}", re.IGNORECASE)
# "#", "No.", "number" or a rank word (with an optional connector) before an integer N.
_NUMBERED = re.compile(
    rf"{_LB}(?:# ?|No\. ?|number |(?:rank|ranks|ranked|ranking) (?:at |of |is |No\. |#|number )?)"
    r"(?P<n>\d{1,3})(?!\d|[.,]\d)(?![A-Za-z])",
    re.IGNORECASE,
)
_TOP = re.compile(rf"{_LB}(?:highest|largest|greatest|biggest){_LA}", re.IGNORECASE)
_BEFORE_TOP = re.compile(rf"(?:{_LB}next|{_LB}{_ORDINAL_SOURCE})[- ]$", re.IGNORECASE)
_BOTTOM_AFTER = re.compile(
    r"^(?:[- ](?:lowest|smallest|least)(?![A-Za-z0-9])|[- ]to[- ]last(?![A-Za-z0-9])|"
    r" from the bottom(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
_NOT_RANK_AFTER = re.compile(
    r"^ (?:quarter|half|fiscal|year|years|time|percentile|consecutive|reported|filed|"
    r"disclosed)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_MONTH_BEFORE = re.compile(
    rf"{_LB}(?:{'|'.join(_MONTHS)}|{'|'.join(m[:3] for m in _MONTHS)})\.? $", re.IGNORECASE
)
# A rank word at a position, and a first-person subject + one space ending the text before
# it: together a count phrase ("I ranked 32 of 33"), not a rank.
_RANK_WORD_AT = re.compile(rf"(?:rank|ranks|ranked|ranking){_LA}", re.IGNORECASE)
_FIRST_PERSON_BEFORE = re.compile(
    rf"{_LB}(?:I|we)(?:['’]ve| have)? \Z", re.IGNORECASE
)
_FORWARD_BINDING = re.compile(r"^-(?:ranked|placed)(?![A-Za-z0-9])", re.IGNORECASE)


class _RankExpression:
    """One rank expression: its span, its rank and whether it binds forward."""

    __slots__ = ("start", "end", "rank", "forward")

    def __init__(self, start: int, end: int, rank: int, forward: bool) -> None:
        self.start, self.end, self.rank, self.forward = start, end, rank, forward


def _ordinal_value(surface: str) -> int:
    lowered = surface.lower()
    if lowered[0].isdigit():
        return int(lowered[:-2])
    return _ORDINAL_WORDS[re.sub(r"[- ]", "-", lowered)]


def _rank_expressions(text: str) -> list[_RankExpression]:
    """The rank expressions of a text in text order; overlapping ones keep the first."""
    found: list[_RankExpression] = []
    for m in _ORDINAL.finditer(text):
        before, after = text[: m.start()], text[m.end():]
        if _BOTTOM_AFTER.match(after) or _NOT_RANK_AFTER.match(after):
            continue
        if _MONTH_BEFORE.search(before):
            continue
        rank = _ordinal_value(m.group("ord"))
        found.append(_RankExpression(m.start(), m.end(), rank,
                                     bool(_FORWARD_BINDING.match(after))))
    for m in _NUMBERED.finditer(text):
        n = int(m.group("n"))
        if m.group(0)[0] in "rR" and is_count_phrase(text, m.start()):
            continue
        if 1 <= n <= 999:
            found.append(_RankExpression(m.start(), m.end(), n, False))
    for m in _TOP.finditer(text):
        if _BEFORE_TOP.search(text[: m.start()]):
            continue
        found.append(_RankExpression(m.start(), m.end(), 1, False))
    found.sort(key=lambda e: (e.start, -(e.end - e.start)))
    kept: list[_RankExpression] = []
    for expr in found:
        if kept and expr.start < kept[-1].end:
            continue
        kept.append(expr)
    return kept


def _mentions(text: str, ticker: str, names: Mapping[str, Sequence[str]]
              ) -> list[tuple[int, int, str]]:
    """Non-overlapping candidate mentions ``(start, end, ticker)`` in text order."""
    found: list[tuple[int, int, str]] = []
    for candidate in dict.fromkeys([*names, ticker]):
        surfaces = [(candidate, 0)] + [
            (str(n), re.IGNORECASE) for n in names.get(candidate, ()) if str(n).strip()
        ]
        for surface, flags in surfaces:
            found += [(m.start(), m.end(), candidate)
                      for m in re.finditer(_words(surface), text, flags)]
    found.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    kept: list[tuple[int, int, str]] = []
    for mention in found:
        if kept and mention[0] < kept[-1][1]:
            continue
        kept.append(mention)
    return kept


def _sentence_spans(text: str, mentions: Sequence[tuple[int, int, str]]
                    ) -> list[tuple[int, int]]:
    """Sentence spans: split after ``.!?`` + whitespace and at newlines, never in a mention."""
    cuts = [m.end() for m in _SENTENCE_END.finditer(text)]
    cuts += [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    cuts = sorted({c for c in cuts
                   if not any(s <= c - 1 < e for s, e, _t in mentions)})
    spans, start = [], 0
    for cut in [*cuts, len(text)]:
        if cut > start:
            spans.append((start, cut))
            start = cut
    return spans or [(0, len(text))]


def _owner(text: str, expr: _RankExpression, mentions: Sequence[tuple[int, int, str]],
           sentences: Sequence[tuple[int, int]], ticker: str) -> str:
    """The candidate an expression belongs to (requirement 1's owner rules 1-5)."""
    first, last = next(((s, e) for s, e in sentences if s <= expr.start < e),
                       (0, len(text)))
    in_sentence = [m for m in mentions if m[0] >= first and m[1] <= last]
    if expr.forward:
        after = [m for m in in_sentence if m[0] >= expr.end]
        if after:
            return after[0][2]
    if text.rfind("(", 0, expr.start) > text.rfind(")", 0, expr.start):
        before = [m for m in in_sentence if m[1] <= expr.start]
        if before:
            return before[-1][2]
    if in_sentence:
        def gap(m: tuple[int, int, str]) -> tuple[int, int]:
            if m[1] <= expr.start:
                return expr.start - m[1], 0
            return max(m[0] - expr.end, 0), 1
        return min(in_sentence, key=gap)[2]
    earlier = [m for m in mentions if m[1] <= first]
    if earlier:
        return earlier[-1][2]
    return ticker


@functools.cache
def _lexicon_companies() -> dict[str, dict[str, str]]:
    """The committed v2 lexicon's companies (ticker -> short/legal names)."""
    return load_lexicon(_LEXICON_PATH).companies


# ---------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lower-cases, drops apostrophes and collapses non-alphanumeric runs (requirement 5)."""
    lowered = text.lower().replace("'", "").replace("’", "")
    return _NOT_ALNUM.sub(" ", lowered).strip()


def _tokens(text: str) -> list[str]:
    return _normalise(text).split()


def _tickers_of(exclusions: Sequence[Any]) -> list[str]:
    return [
        str(e["ticker"]) for e in exclusions if isinstance(e, dict) and e.get("ticker")
    ]


def _names_ticker(text: str, ticker: str, aliases: Mapping[str, Sequence[str]]) -> bool:
    """Whether the text names the ticker, or one of its aliases (requirement 6)."""
    pattern = rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])"
    if re.search(pattern, text):
        return True
    return any(states_phrase(text, alias) for alias in aliases.get(ticker, ()))


def _states_count(text: str, counts: set[int]) -> bool:
    """Whether some count is stated as a standalone integer or a number word 1-20."""
    if any(int(m.group(1)) in counts for m in _INTEGER.finditer(text)):
        return True
    return any(
        1 <= n <= len(_NUMBER_WORDS) and states_phrase(text, _NUMBER_WORDS[n - 1])
        for n in counts
    )


def _states_date(text: str, iso: str) -> bool:
    """Whether a ``YYYY-MM-DD`` period end is stated in one of the accepted forms."""
    match = _ISO_DATE.match(iso.strip())
    if match is None:
        return states_phrase(text, iso)
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12:
        return states_phrase(text, iso)
    full = _MONTHS[month - 1]
    forms = [iso.strip()]
    for mon in (full, full[:3]):
        forms += [f"{mon} {day} {year}", f"{day} {mon} {year}"]
    return states_phrase(text, "|".join(forms))


def _row_name(row: Any, index: int) -> str:
    """A check key for an expected row: its entity as written (``FY2023``, ``latest``)."""
    if isinstance(row, dict):
        for key in ("entity", "label", "name", "ticker"):
            val = row.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return f"row[{index}]"


# ---------------------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------------------


def _downgrade(grade: CaseGrade, checks: Mapping[str, bool], keys: frozenset[str]) -> None:
    """Turns a CORRECT grade PARTIAL when one of the named checks failed."""
    failing = [k for k, ok in checks.items() if k in keys and not ok]
    if grade.grade is Grade.CORRECT and failing:
        grade.grade, grade.passed = Grade.PARTIAL, False
        grade.reason = f"{grade.reason}; failed checks: {', '.join(failing)}"


def _uncited(case: EvalCase, answer: SystemAnswer) -> bool:
    return case.require_citation and not answer.fact_ids


def _context_checks(case: EvalCase, text: str) -> dict[str, bool]:
    """The disclosure checks plus the S4 fallback check when the key requires it."""
    checks = disclosure_checks(case, text)
    if isinstance(case.key, dict) and case.key.get("balance") == "closing":
        checks[_FALLBACK_KEY] = states_phrase(text, FALLBACK_PHRASE)
    return checks


def _grade_scalar_in_context(case: EvalCase, answer: SystemAnswer,
                             compound_key: Mapping[str, Any] | None = None) -> CaseGrade:
    """Requirement 9: count, sum, median, rank, CAGR and ratio cases.

    A sum or median is graded with grading-dont-care-and-ties requirements 3 and 4: a
    declining answer's listed row values are never read as the aggregate, and each
    don't-care reading is tried after the key's. A compound's ``compute.yoy_growth`` part
    is offered the absolute-change reading over ``compound_key``
    (pick-skips-questions requirement 5(b)).
    """
    aggregate = v2_subkind(case) in ("sum", "median")
    if aggregate:
        answer = _without_row_values(case, answer)
    grade = grade_scalar(case, answer)
    if aggregate and not grade.value_correct:
        for subset, value in _aggregate_readings(case)[1:]:
            probe = dataclasses.replace(case, expected_value=value, alt_values=[],
                                        alt_readings=[])
            candidate = grade_scalar(probe, answer)
            if candidate.value_correct:
                grade = _with_reading(candidate, subset)
                break
    if compound_key is not None:
        grade = _with_absolute_change(case, answer, grade, compound_key)
    context = _context_checks(case, answer.text)
    grade.checks = {"value": grade.value_correct, **context}
    _downgrade(grade, context, _DISCLOSURE_KEYS | {_FALLBACK_KEY})
    return grade


def _grade_screen_in_context(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 10: a ranked screen that must disclose its universe and exclusions.

    Its expected rows are re-formed for ties and each don't-care reading
    (grading-dont-care-and-ties requirements 3 and 5); the grade is the first
    value-correct reading's, else the key's.
    """
    grade = _grade_screen_readings(case, answer)
    disclosure = disclosure_checks(case, answer.text)
    grade.checks = {"ranking": grade.value_correct, **disclosure}
    _downgrade(grade, disclosure, _DISCLOSURE_KEYS)
    return grade


# ---------------------------------------------------------------------------------------
# Don't-care readings, declined aggregates and ties (grading-dont-care-and-ties)
# ---------------------------------------------------------------------------------------


def _matches_row_value(case: EvalCase, answer: SystemAnswer, value: float,
                       listed: float) -> bool:
    """Whether ``value`` would match a key equal to ``listed`` under the case's rule."""
    probe = dataclasses.replace(case, expected_value=listed, alt_values=[],
                                alt_readings=[], expect_not_meaningful=False)
    single = dataclasses.replace(answer, numeric_values=[value], refused=False,
                                 hedged=False)
    return grade_scalar(probe, single).matched_value is not None


def _stated_aggregates(case: EvalCase, answer: SystemAnswer,
                       listed: Sequence[float]) -> set[float]:
    """The row-matching values the answer states as the aggregate.

    Spec: docs/specs/grading-stated-aggregate.md (requirement 2). A sentence that names the
    aggregate and does not decline states a value as the aggregate when it states exactly
    one distinct number matching a row value ("200 (200 USD)" states one).
    """
    sub_kind = v2_subkind(case) or ""
    text = answer.text or ""
    found: set[float] = set()
    for start, end in aggregate_sentences(text, sub_kind):
        matching = {n.value for n in extract_stated_numbers(text[start:end])
                    if any(_matches_row_value(case, answer, n.value, r) for r in listed)}
        if len(matching) == 1:
            found |= matching
    return found


def _without_row_values(case: EvalCase, answer: SystemAnswer) -> SystemAnswer:
    """Requirement 4: a declining answer's values that match one of its own rows dropped.

    A value the answer states as the aggregate is kept (grading-stated-aggregate,
    requirement 2).
    """
    if not (answer.refused or answer.hedged) or not answer.numeric_values:
        return answer
    listed = [v for v in (row_value(r) for r in answer.rows or [] if isinstance(r, dict))
              if v is not None]
    if not listed:
        return answer
    stated = _stated_aggregates(case, answer, listed)
    kept = [v for v in answer.numeric_values
            if v in stated
            or not any(_matches_row_value(case, answer, v, r) for r in listed)]
    if len(kept) == len(answer.numeric_values):
        return answer
    return dataclasses.replace(answer, numeric_values=kept)


def _ranked_input_values(case: EvalCase) -> list[float] | None:
    """The ranked members' key values from ``key["inputs"]``, in ranked order, or None.

    None unless the inputs hold at least ``len(ranked)`` entries, each a numeric value.
    """
    d = case.disclosures
    if not isinstance(d, dict) or not isinstance(case.key, dict):
        return None
    ranked = d.get("ranked") or []
    inputs = case.key.get("inputs") or []
    if not isinstance(inputs, list) or len(inputs) < len(ranked):
        return None
    values: list[float] = []
    for item in inputs[: len(ranked)]:
        value = item.get("value") if isinstance(item, dict) else None
        if not _is_numeric(value):
            return None
        values.append(float(value))
    return values


def _median(values: Sequence[float]) -> float:
    """The middle value, or the mean of the two middle values."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _aggregate_readings(case: EvalCase) -> list[tuple[tuple[str, ...], float | None]]:
    """A sum's or median's readings ``(subset, value)``, the key's first (requirement 3)."""
    readings: list[tuple[tuple[str, ...], float | None]] = [((), case.expected_value)]
    dont_care = dont_care_members(case)
    key = case.expected_value
    if not dont_care or key is None:
        return readings
    if v2_subkind(case) == "sum":
        return readings + [
            (subset, key + sum(dont_care[t] for t in subset))
            for subset in _readings(dont_care)[1:]
        ]
    ranked_values = _ranked_input_values(case)
    if not ranked_values or not math.isclose(_median(ranked_values), key,
                                             rel_tol=_MEDIAN_REPRODUCE_TOL):
        return readings
    return readings + [
        (subset, _median([*ranked_values, *(dont_care[t] for t in subset)]))
        for subset in _readings(dont_care)[1:]
    ]


def _screen_descending(case: EvalCase, values: Sequence[float]) -> bool | None:
    """The screen's order rule: True for value descending, False for ascending.

    Read from the shape or H-X sub-type tag; for a part without one (a compound's
    ranking, keyed as a top screen), from its known values, descending when they do not
    tell. None when the known values contradict the order rule.
    """
    tags = {str(t) for t in case.tags}
    descending: bool | None = None
    if tags & {"shape:screen.bottom", "screen_bottom"}:
        descending = False
    elif tags & {"shape:screen.top", "screen_top", "shape:peer_relative"}:
        descending = True
    pairs = list(zip(values, values[1:], strict=False))
    non_increasing = all(a >= b for a, b in pairs)
    non_decreasing = all(a <= b for a, b in pairs)
    if descending is None:
        return non_increasing or not non_decreasing
    if (descending and not non_increasing) or (not descending and not non_decreasing):
        return None
    return descending


def _known_members(case: EvalCase) -> list[tuple[str, float, dict | None]] | None:
    """A screen's known members ``(ticker, value, expected row or None)`` (requirement 5).

    Every expected row, then, for a v2 case whose ``key["inputs"]`` line up with its
    ranked members, the ranked members past the cut. None when a row has no entity or
    value.
    """
    known: list[tuple[str, float, dict | None]] = []
    for row in case.expected_rows or []:
        if not isinstance(row, dict):
            return None
        ticker, value = row_entity(row), row_value(row)
        if ticker is None or value is None:
            return None
        known.append((ticker, value, row))
    d = case.disclosures
    ranked = ([str(t).strip().upper() for t in d.get("ranked") or []]
              if isinstance(d, dict) else [])
    values = _ranked_input_values(case)
    if values is None or len(ranked) <= len(known):
        return known
    aligned = all(
        ranked[i] == ticker and values[i] == value
        for i, (ticker, value, _row) in enumerate(known)
    )
    if aligned:
        known += [(ranked[j], values[j], None) for j in range(len(known), len(ranked))]
    return known


def _reformed_rows(known: Sequence[tuple[str, float, dict | None]],
                   added: Mapping[str, float], descending: bool,
                   answer_order: Sequence[str], n: int) -> list[dict]:
    """The expected rows of one reading, ties ordered as the answer orders them."""
    present = {t for t, _v, _r in known}
    members = [*known, *((t, v, None) for t, v in added.items() if t not in present)]
    position: dict[str, int] = {}
    for i, ticker in enumerate(answer_order):
        position.setdefault(ticker, i)

    def order(member: tuple[str, float, dict | None]) -> tuple[float, int, int, str]:
        ticker, value = member[0], member[1]
        return (-value if descending else value, 0 if ticker in position else 1,
                position.get(ticker, 0), ticker)

    members.sort(key=order)
    return [dict(row) if row is not None else {"ticker": t, "value": v}
            for t, v, row in members[:n]]


def _grade_screen_readings(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """A screen graded against its key and each don't-care reading (requirements 3, 5)."""
    dont_care = dont_care_members(case)
    known = _known_members(case)
    if known is None:
        return grade_screen(case, answer)
    values = [v for _t, v, _r in known]
    if not dont_care and len(set(values)) == len(values):
        return grade_screen(case, answer)  # no reading and no tie: the key as keyed
    descending = _screen_descending(case, values)
    if descending is None:
        return grade_screen(case, answer)
    n = case.top_n or len(case.expected_rows or [])
    answer_order = [e for e in (row_entity(r) for r in answer.rows or []
                                if isinstance(r, dict)) if e is not None]
    key_grade: CaseGrade | None = None
    for subset in _readings(dont_care):
        rows = _reformed_rows(known, {t: dont_care[t] for t in subset}, descending,
                              answer_order, n)
        grade = grade_screen(dataclasses.replace(case, expected_rows=rows), answer)
        if grade.value_correct:
            return _with_reading(grade, subset) if subset else grade
        if key_grade is None:
            key_grade = grade
    if key_grade is None:  # unreachable: the empty subset is always a reading
        return grade_screen(case, answer)
    return key_grade


# Generated compounds ask about "the top N" without asking for their order or figures
# (docs/specs/prose-and-ranking-names.md requirement 2).
_NAMES_ONLY_SHAPES = frozenset({"compound.rank_by_ratio", "compound.leader_growth"})


def _ranking_name_sets(case: EvalCase, answer_order: Sequence[str]) -> list[set[str]]:
    """The top-N name sets a ranking part accepts: the key's, then each reading's.

    The readings are those ``_grade_screen_readings`` forms (don't-care members and ties,
    grading-dont-care-and-ties requirements 3 and 5).

    Args:
        case: A ranking part.
        answer_order: The answer's row entities, in order (ties are ordered by it).

    Returns:
        The accepted sets, the key's first; empty when the key names no row.
    """
    n = case.top_n or len(case.expected_rows or [])
    key = {e for e in (row_entity(r) for r in (case.expected_rows or [])[:n]) if e is not None}
    sets = [key] if key else []
    known = _known_members(case)
    if known is None:
        return sets
    descending = _screen_descending(case, [v for _t, v, _r in known])
    if descending is None:
        return sets
    dont_care = dont_care_members(case)
    for subset in _readings(dont_care):
        rows = _reformed_rows(known, {t: dont_care[t] for t in subset}, descending,
                              answer_order, n)
        names = {e for e in (row_entity(r) for r in rows) if e is not None}
        if names and names not in sets:
            sets.append(names)
    return sets


def _grade_ranking_by_names(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """A generated compound's ranking part: right when its rows name the key's top N.

    docs/specs/prose-and-ranking-names.md requirements 2 and 3. The first ``N`` row
    entities, as a set, must equal an accepted top-N set (``_ranking_name_sets``); their order
    and values are not compared. A match is graded as a standalone screen whose ranking
    matched (citations, grounding and disclosure checks as usual); no match keeps the
    ordinary grade.

    Args:
        case: A ranking part of a ``_NAMES_ONLY_SHAPES`` compound.
        answer: The answer, as the compound grader reads it.

    Returns:
        The part's grade.
    """
    grade = _grade_screen_in_context(case, answer)
    if grade.value_correct:
        return grade
    n = case.top_n or len(case.expected_rows or [])
    listed = [r for r in answer.rows or [] if isinstance(r, dict) and row_entity(r) is not None]
    first = listed[:n]
    names = {row_entity(r) for r in first}
    if n == 0 or len(first) < n or len(names) < n:
        return grade
    if names not in _ranking_name_sets(case, [str(row_entity(r)) for r in listed]):
        return grade
    # Compared against its own rows, the answer's figures and order always agree, so only
    # the names decide; citations, grounding and disclosures are graded as for any screen.
    matched = grade_screen(dataclasses.replace(
        case, expected_rows=[dict(r) for r in first], order_sensitive=False), answer)
    matched.expected = grade.expected
    matched.reason = f"names matched (order and figures not compared); {matched.reason}"
    disclosure = disclosure_checks(case, answer.text)
    matched.checks = {"ranking": matched.value_correct, **disclosure}
    _downgrade(matched, disclosure, _DISCLOSURE_KEYS)
    return matched


def _accepted_ranks(case: EvalCase, ticker: str, rank: int
                    ) -> list[tuple[tuple[str, ...], set[int]]] | None:
    """A peer subject's accepted ranks per reading (requirements 3 and 5), or None.

    None when the expected rows carry no value for the subject. When the rows do not
    reproduce the key's rank, the key's rank stands for the key reading, shifted by the
    don't-care members of each other reading.
    """
    values: dict[str, float] = {}
    for row in case.expected_rows or []:
        if isinstance(row, dict):
            entity, value = row_entity(row), row_value(row)
            if entity is not None and value is not None:
                values.setdefault(entity, value)
    if ticker not in values:
        return None
    own = values[ticker]
    others = [v for t, v in values.items() if t != ticker]
    low = 1 + sum(1 for v in others if v > own)
    high = 1 + sum(1 for v in others if v >= own)
    if not low <= rank <= high:
        low = high = rank
    dont_care = dont_care_members(case)
    out: list[tuple[tuple[str, ...], set[int]]] = []
    for subset in _readings(dont_care):
        above = sum(1 for t in subset if dont_care[t] > own)
        tied = sum(1 for t in subset if dont_care[t] == own)
        out.append((subset, set(range(low + above, high + above + tied + 1))))
    return out


def _grade_peer_relative(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """The subject's rank among its peers, read from what the answer states.

    Spec: docs/specs/peer-rank-grading.md requirement 4 (replaces v2-grading
    requirement 11): the first rank the text states for the subject, else the rank the
    rows' values imply, else a structured value equal to the key's rank. Row order never
    decides.
    """
    subject = case.subject if isinstance(case.subject, dict) else {}
    ticker = str(subject.get("ticker") or "").strip().upper()
    d = case.disclosures
    if isinstance(d, dict) and isinstance(d.get("ranked"), list):
        n_ranked = len(d["ranked"])
    else:
        n_ranked = len(case.expected_rows or [])
    rank = subject.get("rank")
    readings = None
    if ticker and isinstance(rank, int) and not isinstance(rank, bool):
        readings = _accepted_ranks(case, ticker, rank)
    if readings is None:
        return _grade_rank(case, answer, ticker, rank, n_ranked)
    # Any rank within a tie, and each don't-care reading (grading-dont-care-and-ties
    # requirements 3 and 5): the first value-correct reading's grade, else the key's.
    key_grade: CaseGrade | None = None
    for subset, accepted in readings:
        grade = _grade_rank(case, answer, ticker, rank, n_ranked + len(subset), accepted)
        if grade.value_correct:
            return _with_reading(grade, subset) if subset else grade
        if key_grade is None:
            key_grade = grade
    if key_grade is None:  # unreachable: the empty subset is always a reading
        return _grade_rank(case, answer, ticker, rank, n_ranked)
    return key_grade


def _grade_hx_rank(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """An H-X peer-relative case, by the same reader (docs/specs/hx-peer-rank.md).

    The subject is ``case.ticker`` and the key's rank ``expected_value`` when it is a whole
    number of at least 1; the peers ranked are the universe minus its exclusions. The
    scalar path's grounding is kept and never changes the grade (requirement 4).
    """
    ticker = str(case.ticker or "").strip().upper()
    value = case.expected_value
    rank: int | None = None
    if isinstance(value, (int, float)) and float(value).is_integer() and value >= 1:
        rank = int(value)
    n_ranked = len(case.universe or []) - len(case.expected_exclusions or [])
    grade = _grade_rank(case, answer, ticker, rank, n_ranked)
    grounding = grade_grounding(
        case.expected_fact_ids,
        answer.fact_ids,
        require_exact=case.require_exact_fact,
        fact_id_classes=case.grounding_classes(),
    )
    grade.grounding_precision = grounding.precision
    grade.grounding_recall = grounding.recall
    return grade


def _grade_rank(case: EvalCase, answer: SystemAnswer, ticker: str, rank: Any,
                n_ranked: int, accepted: Collection[Any] | None = None) -> CaseGrade:
    """Grades the subject's rank (peer-rank-grading requirement 4) for given inputs.

    ``accepted`` are the ranks counted right (a tie or a don't-care reading,
    grading-dont-care-and-ties requirements 3 and 5); None accepts the key's rank only.
    """
    disclosure = disclosure_checks(case, answer.text)
    if not ticker or rank is None:
        grade = _base(case, answer, Grade.INCORRECT, False, "the case has no subject rank")
        grade.checks = {"rank": False, **disclosure}
        return grade

    if accepted is None:
        accepted = {rank}
    read, how = _answer_rank(case, answer, ticker, accepted, n_ranked)
    right = read is not None and read in accepted
    checks = {"rank": right, **disclosure}

    if read is None:
        if answer.refused or answer.hedged:
            grade = _base(case, answer, Grade.REFUSED, False, "system declined to rank")
        else:
            grade = _base(case, answer, Grade.INCORRECT, False, "no rank in the answer")
    elif not right:
        grade = _base(
            case, answer, Grade.INCORRECT, False,
            f"{ticker} is ranked {read} ({how}), not {rank}",
        )
    else:
        grade = _base(case, answer, Grade.CORRECT, True, "ok")
        grade.matched_value = float(read)
        _downgrade(grade, disclosure, _DISCLOSURE_KEYS)
        if grade.grade is Grade.CORRECT and _uncited(case, answer):
            grade.grade, grade.passed = Grade.PARTIAL, False
            grade.reason = "rank matched but the answer carries no fact_id citation"
    grade.checks = checks
    return grade


def _answer_rank(case: EvalCase, answer: SystemAnswer, ticker: str,
                 accepted: Collection[Any], n_ranked: int) -> tuple[Any, str]:
    """The answer's rank for the subject and how it was read (requirement 4).

    Returns:
        ``(rank read or None, "stated" | "rows" | "values" | "none")``. For the values
        reading the rank read is the first value equal to an accepted rank, else the
        first value.
    """
    names = peer_names(case)
    known = {*names}
    for aliases in names.values():
        known.update(str(a).upper() for a in aliases)
    for row in answer.rows or []:
        entity = row_entity(row) if isinstance(row, dict) else None
        # A row named by a candidate's name is that candidate, not a new one.
        if entity is not None and entity not in known:
            names[entity] = []
            known.add(entity)
    # Never a rank from an appended disclosure sentence
    # (docs/specs/grader-synthesis-error.md requirement 5).
    stated = stated_ranks(blank_disclosures(answer.text or ""), ticker, names)
    if stated:
        return stated[0], "stated"
    by_value = value_rank(answer.rows or [], ticker, names, n_ranked)
    if by_value is not None:
        return by_value, "rows"
    if answer.numeric_values:
        hit = next((v for v in answer.numeric_values if v in accepted), None)
        if hit is not None:
            return hit, "values"
        return answer.numeric_values[0], "values"
    return None, "none"


def _row_readings(row: Any) -> list[dict]:
    """A row's own alternate readings (docs/specs/shares-outstanding-reading.md)."""
    readings = row.get("alt_readings") if isinstance(row, dict) else None
    return [r for r in readings if isinstance(r, dict)] if isinstance(readings, list) else []


def _match_rows(
    rows: Sequence[Any], values: Sequence[float], rel_tol: float, *, share_equal: bool,
    marks: dict[str, str] | None = None,
    matcher: Callable[[dict, float], bool] | None = None,
) -> dict[str, bool]:
    """Matches each expected row to the first unused produced value within tolerance.

    A row with ``alt_readings`` is matched by its value, or else by one of its own
    readings, each within ``rel_tol`` on its own; for each row the key is preferred
    (docs/specs/shares-outstanding-reading.md requirement 8).

    Args:
        rows: The expected rows, in order.
        values: The produced values.
        rel_tol: The case's relative tolerance.
        share_equal: Whether one produced value may match every row when all the
            expected values agree within tolerance (a point-in-time pair).
        marks: When given, filled with row name -> ``"key"`` or the reading's name for
            each matched row.
        matcher: ``match(expected_row, produced_value)`` for a case graded at its key's
            precision (``row_matcher``); None matches the key within ``rel_tol``.

    Returns:
        Row name -> whether a produced value matched it.
    """
    expected = [row_value(r) if isinstance(r, dict) else None for r in rows]
    known = [v for v in expected if v is not None]
    reuse = share_equal and bool(known) and all(
        close_enough(v, known[0], rel_tol) for v in known
    )
    used = [False] * len(values)
    out: dict[str, bool] = {}
    for i, (row, want) in enumerate(zip(rows, expected, strict=True)):
        hit = None
        if want is not None:
            hit = next(
                (
                    j for j, got in enumerate(values)
                    if (reuse or not used[j]) and (
                        matcher(row, got) if matcher is not None
                        else close_enough(got, want, rel_tol))
                ),
                None,
            )
        mark = "key"
        if hit is None:
            readings = _row_readings(row)
            for j, got in enumerate(values):
                if reuse or not used[j]:
                    name = match_reading(got, [], readings, rel_tol) if readings else None
                    if name is not None:
                        hit, mark = j, name
                        break
        if hit is not None:
            used[hit] = True
            if marks is not None:
                marks[_row_name(row, i)] = mark
        out[_row_name(row, i)] = hit is not None
    return out


def _with_rows_evidence(case: EvalCase, answer: SystemAnswer, grade: CaseGrade) -> None:
    """Attaches breakdown-style component metrics and grounding, as a breakdown does."""
    metrics = compare_breakdown(
        case.expected_rows or [],
        answer.numeric_values,
        expected_fact_ids=case.expected_fact_ids,
        produced_fact_ids=answer.fact_ids,
        rel_tol=case.rel_tol,
    )
    grounding = grade_grounding(
        case.expected_fact_ids,
        answer.fact_ids,
        require_exact=case.require_exact_fact,
        fact_id_classes=case.grounding_classes(),
    )
    grade.screen_metrics = metrics.to_dict()
    grade.grounding_precision = grounding.precision
    grade.grounding_recall = grounding.recall


def _no_values(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    if answer.refused or answer.hedged:
        return _base(case, answer, Grade.REFUSED, False, "system declined to answer")
    return _base(case, answer, Grade.INCORRECT, False, "no audited values in the answer")


def _grade_trend(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 12: every year of the trend, each produced value used once."""
    rows = case.expected_rows or []
    marks: dict[str, str] = {}
    checks = _match_rows(rows, answer.numeric_values, case.rel_tol, share_equal=False,
                         marks=marks, matcher=row_matcher(case))
    if not answer.numeric_values:
        grade = _no_values(case, answer)
    elif rows and all(checks.values()):
        grade = _base(case, answer, Grade.CORRECT, True, f"all {len(rows)} periods matched")
        if _uncited(case, answer):
            grade.grade, grade.passed = Grade.PARTIAL, False
            grade.reason = "every period matched but the answer carries no fact_id citation"
        _with_rows_evidence(case, answer, grade)
    else:
        missing = [k for k, ok in checks.items() if not ok]
        grade = _base(
            case, answer, Grade.INCORRECT, False,
            f"matched {len(checks) - len(missing)}/{len(checks)} periods; "
            f"missing {', '.join(missing) or 'every period'}",
        )
        grade.incomplete = True
        _with_rows_evidence(case, answer, grade)
    grade.checks = checks
    if any(_row_readings(r) for r in rows):
        grade.matched_readings = marks
    return grade


def _grade_point_in_time(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 13: the as-originally-reported and latest values, each labelled."""
    rows = case.expected_rows or []
    matched = _match_rows(rows, answer.numeric_values, case.rel_tol, share_equal=True,
                          matcher=row_matcher(case))
    labels = states_phrase(answer.text, ORIGINAL_LABEL_PHRASE) and states_phrase(
        answer.text, LATEST_LABEL_PHRASE
    )
    checks = {**matched, "labels": labels}
    if not answer.numeric_values:
        grade = _no_values(case, answer)
    elif rows and all(matched.values()):
        if not labels:
            grade = _base(
                case, answer, Grade.PARTIAL, False,
                "both values matched but not labelled original vs latest",
            )
        elif _uncited(case, answer):
            grade = _base(
                case, answer, Grade.PARTIAL, False,
                "both values matched but the answer carries no fact_id citation",
            )
        else:
            grade = _base(case, answer, Grade.CORRECT, True, "both values matched and labelled")
        _with_rows_evidence(case, answer, grade)
    else:
        missing = [k for k, ok in matched.items() if not ok]
        grade = _base(
            case, answer, Grade.INCORRECT, False,
            f"missing {', '.join(missing) or 'both values'}",
        )
        grade.incomplete = True
        _with_rows_evidence(case, answer, grade)
    grade.checks = checks
    return grade


def _words(surface: str) -> str:
    """A whole-word pattern for a surface form."""
    return rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])"


def without_subquery_echoes(text: str) -> str:
    """The text with every ``[sq<N>]`` sub-query echo line deleted.

    docs/specs/grader-pick-echo-grow.md requirement 1: a line (cut after each newline) is
    an echo line when it begins, after optional spaces and tabs, with ``[sq`` + ASCII
    digits + ``]``; it is deleted with the newline that ends it, everything else kept.

    Args:
        text: An answer's text.

    Returns:
        The concatenation of the non-echo lines, each as written.
    """
    return "".join(
        line for line in _lines(text) if not _SUBQUERY_ECHO_LINE.match(line))


def _lines(text: str) -> list[str]:
    """The text cut after every ``\n``, each line keeping its ``\n``."""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def asks_for_amount(question: str) -> bool:
    """Whether a question asks for an amount (grader-pick-echo-grow requirement 3).

    True when the question holds amount wording (a change word, or ``how much`` followed
    later by a movement verb) and no percent cue.

    Args:
        question: The case's (for a compound part, the compound's) question.

    Returns:
        Whether the absolute-change reading may apply to a growth key asked this way.
    """
    if "%" in question or _PERCENT_CUE.search(question):
        return False
    if _CHANGE_WORD.search(question):
        return True
    how_much = _HOW_MUCH.search(question)
    return how_much is not None and _MOVEMENT_VERB.search(question, how_much.end()) is not None


def picked_filer(text: str, candidates: Mapping[str, Sequence[str]],
                 ratio_forms: Sequence[str] = ()) -> str | None:
    """The filer an answer names as the highest (compound-shape requirement 6).

    The pick sentence is the first non-interrogative sentence holding a superlative
    (``SUPERLATIVE_WORDS``) and one of ``ratio_forms``; when none does, the last
    non-interrogative sentence holding a superlative. A sentence is interrogative when the
    split that ends it is a ``?`` (an echoed ``[sqN]`` sub-question, say), so a question is
    never the pick sentence (docs/specs/pick-skips-questions.md requirements 2-3). Every
    ``[sq<N>]`` sub-query echo line is deleted before sentences are split
    (``without_subquery_echoes``, docs/specs/grader-pick-echo-grow.md requirement 2), so an
    echo is never the pick sentence whatever its final punctuation. In it,
    the candidate whose mention ends nearest before its first superlative; else the
    first named after it. A mention is the ticker (as written) or one of its names; the
    superlatives, names and ratio forms match whole words in any case.

    Args:
        text: The answer's text.
        candidates: ticker -> its other names (the lexicon's short and legal names).
        ratio_forms: The ratio's surface forms (the lexicon's label, synonyms, shorthand).

    Returns:
        The ticker, or None when no non-interrogative sentence has a superlative or the
        pick sentence names no candidate.
    """
    text = without_subquery_echoes(text)
    mentions: list[tuple[int, int, str]] = []
    for ticker, names in candidates.items():
        patterns = [(ticker, 0)] + [(name, re.IGNORECASE) for name in names if name]
        for surface, flags in patterns:
            mentions += [(m.start(), m.end(), ticker)
                         for m in re.finditer(_words(surface), text, flags)]
    ratio = [re.compile(_words(f), re.IGNORECASE) for f in ratio_forms if f]
    # (end, interrogative) per split; the text after the last split is never a question.
    splits = [(m.end(), m.group() == "?") for m in _SENTENCE_END.finditer(text)
              if not any(s <= m.start() < e for s, e, _t in mentions)]
    with_superlative: list[tuple[int, int, re.Match[str]]] = []
    start = 0
    for end, interrogative in [*splits, (len(text), False)]:
        sup = None if interrogative else _SUPERLATIVE.search(text, start, end)
        if sup is not None:
            with_superlative.append((start, end, sup))
        start = end
    if not with_superlative:
        return None
    named_ratio = [s for s in with_superlative
                   if any(r.search(text, s[0], s[1]) for r in ratio)]
    first, last, sup = named_ratio[0] if named_ratio else with_superlative[-1]
    inside = [m for m in mentions if m[0] >= first and m[1] <= last]
    before = [m for m in inside if m[1] <= sup.start()]
    if before:
        return max(before, key=lambda m: (m[1], m[1] - m[0]))[2]
    after = [m for m in inside if m[0] >= sup.end()]
    if after:
        return min(after, key=lambda m: (m[0], m[0] - m[1]))[2]
    return None


def absolute_change_reading(question: str, key: Mapping[str, Any] | None,
                            growth: float | None) -> float | None:
    """The absolute change a growth key also accepts (pick-skips-questions requirement 4).

    Args:
        question: The case's (for a compound part, the compound's) question.
        key: The case's (for a compound part, the compound's) answer key.
        growth: The growth value the key states.

    Returns:
        The later leg's value minus the earlier leg's, or None when the reading does not
        apply.
    """
    changes = {b["value"] - a["value"] for a, b in _change_legs(question, key, growth)}
    return changes.pop() if len(changes) == 1 else None


def _change_legs(question: str, key: Mapping[str, Any] | None,
                 growth: float | None) -> list[tuple[dict, dict]]:
    """The leg pairs of a growth key asked for an amount (requirement 4).

    Empty unless ``key`` is a growth key with a finite ``growth`` and ``question`` asks for
    an amount (``asks_for_amount``, grader-pick-echo-grow requirement 4). Each pair is
    (earlier, later): the same concept, unit and cik, an earlier fiscal year, a non-zero
    earlier value, and a growth within a relative 1e-9 of ``growth``.
    """
    if not isinstance(key, Mapping) or key.get("definition") != "growth":
        return []
    if not _is_numeric(growth) or not math.isfinite(growth):
        return []
    if not asks_for_amount(question or ""):
        return []
    raw = key.get("inputs")
    legs = [i for i in raw if _is_leg(i)] if isinstance(raw, list) else []
    pairs: list[tuple[dict, dict]] = []
    for a in legs:
        for b in legs:
            if (a["concept"] != b["concept"] or a["unit"] != b["unit"]
                    or a.get("cik") != b.get("cik") or a["value"] == 0
                    or int(a["fiscal_year"]) >= int(b["fiscal_year"])):
                continue
            if math.isclose((b["value"] - a["value"]) / abs(a["value"]), growth,
                            rel_tol=_GROWTH_REPRODUCE_TOL):
                pairs.append((a, b))
    return pairs


def _is_leg(item: Any) -> bool:
    """Whether a key input can be a growth leg: a concept, unit, fiscal year and value."""
    if not isinstance(item, dict) or not _is_numeric(item.get("value")):
        return False
    if item.get("concept") is None or item.get("unit") is None:
        return False
    try:
        int(item["fiscal_year"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(item["value"])


def _with_absolute_change(case: EvalCase, answer: SystemAnswer, grade: CaseGrade,
                          key: Mapping[str, Any] | None) -> CaseGrade:
    """The key's grade, else the absolute-change reading's when it matches (requirement 5).

    Args:
        case: A case or compound part; its ``question`` is the one asked (a part carries
            its compound's).
        answer: The answer, as ``grade_scalar`` reads it.
        grade: The key's own grade.
        key: The case's key (for a part, the compound's).

    Returns:
        ``grade`` when it is value-correct, the case is not ``compute.yoy_growth``, or the
        reading does not apply or does not match; else the probe's grade, its reason
        ending ``; reading: absolute change``.
    """
    if grade.value_correct or case.expect_not_meaningful:
        return grade
    if shape_of(case) != _GROWTH_SHAPE:
        return grade
    pairs = _change_legs(case.question, key, case.expected_value)
    if len({b["value"] - a["value"] for a, b in pairs}) != 1:
        return grade
    earlier, later = pairs[0]
    tags = [_DIFFERENCE_TAG if str(t) == f"shape:{_GROWTH_SHAPE}" else t for t in case.tags]
    probe = dataclasses.replace(
        case, expected_value=later["value"] - earlier["value"], alt_values=[],
        alt_readings=[], tags=tags, key={"inputs": [dict(earlier), dict(later)]})
    candidate = grade_scalar(probe, answer)
    if not candidate.value_correct:
        return grade
    candidate.reason = f"{candidate.reason}; reading: absolute change"
    return candidate


def grade_growth_scalar(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """``grade_scalar`` plus the absolute-change reading (pick-skips-questions req. 5(a)).

    A non-compound ``compute.yoy_growth`` case asked as a "change" without a percent cue
    also accepts the change between its key's two legs; any other case grades as
    ``grade_scalar`` does.

    Args:
        case: A scalar case.
        answer: The answer, as ``grade_scalar`` reads it.

    Returns:
        The grade.
    """
    grade = grade_scalar(case, answer)
    return _with_absolute_change(case, answer, grade, case.key)


def _grade_pick(part: EvalCase, answer: SystemAnswer,
                candidates: Mapping[str, Sequence[str]],
                ratio_forms: Sequence[str]) -> CaseGrade:
    """A ``rank_by_ratio`` dependent: the winner named as highest, its ratio stated.

    ``"pick"`` is whether ``picked_filer`` is the winner, ``"value"`` whether the
    winner's ratio is in the answer at its precision (compound-shape requirement 6).
    """
    grade = grade_scalar(part, answer)
    winner = str(part.ticker or "").strip().upper()
    pick = picked_filer(answer.text or "", candidates, ratio_forms) == winner
    grade.checks = {"pick": pick, "value": grade.value_correct}
    if not pick and grade.grade in (Grade.CORRECT, Grade.PARTIAL, Grade.IMPRECISE):
        grade.grade, grade.passed = Grade.INCORRECT, False
        grade.reason = f"the answer does not name {winner} as the highest; {grade.reason}"
    return grade


def _grade_part(part: EvalCase, answer: SystemAnswer,
                raw: Mapping[str, Any] | None = None,
                compound_key: Mapping[str, Any] | None = None,
                names_only: bool = False) -> CaseGrade:
    """Grades one compound part by its kind (requirement 14).

    ``raw`` is the part as written; a part with a ``pick`` block is a generated
    ``rank_by_ratio`` dependent (compound-shape requirement 6). ``compound_key`` is the
    compound's own key, from which a ``compute.yoy_growth`` part's absolute-change reading
    takes its legs (pick-skips-questions requirement 5(b)). ``names_only`` grades a screen
    part by its names (a generated compound's ranking, prose-and-ranking-names
    requirement 2).
    """
    pick = (raw or {}).get("pick")
    if isinstance(pick, dict):
        candidates = {str(t): [str(n) for n in names or []]
                      for t, names in (pick.get("candidates") or {}).items()}
        ratio_forms = [str(f) for f in pick.get("ratio") or []]
        return _grade_pick(part, answer, candidates, ratio_forms)
    if part.kind == KIND_SCREEN:
        if names_only:
            return _grade_ranking_by_names(part, answer)
        return _grade_screen_in_context(part, answer)
    if part.kind == KIND_COMPUTE:
        return _grade_scalar_in_context(part, answer, compound_key)
    if part.kind == KIND_BREAKDOWN:
        if part.accessions:
            return _grade_point_in_time(part, answer)
        return _grade_trend(part, answer)
    if part.kind == KIND_NARRATIVE:
        return grade_narrative(part, answer)
    return grade_legacy(part, answer)


def _grade_compound(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 14: every part graded against the same answer; all must pass.

    A judged text part (``text_part``) is not graded: the judge scores it, and the other
    parts decide (docs/specs/compound-text-part.md requirement 2).
    """
    if not case.parts:
        grade = grade_legacy(case, answer)
        grade.checks = {}
        return grade
    judged = _text_part_index(case)
    judged_note = (
        None if judged is None
        else f"{case.parts[judged].get('part')}: judged, not graded"
    )
    checks: dict[str, bool] = {}
    graded: list[tuple[str, CaseGrade]] = []
    names_only = shape_of(case) in _NAMES_ONLY_SHAPES
    for i, p in enumerate(case.parts):
        if i == judged:
            continue
        name = str(p.get("part"))
        part_grade = _grade_part(_part_case(case, p), answer, p, case.key,
                                 names_only=names_only and p.get("part") == "ranking")
        graded.append((name, part_grade))
        checks[name] = part_grade.passed
        for key, ok in (part_grade.checks or {}).items():
            checks[f"{name}.{key}"] = ok

    if not graded:  # only a judged text part: nothing to grade
        grade = _base(case, answer, Grade.PARTIAL, False, judged_note or "")
        grade.checks = {}
        return grade

    grades = [g.grade for _, g in graded]
    if all(g is Grade.CORRECT for g in grades):
        overall = Grade.CORRECT
    elif all(g is Grade.REFUSED for g in grades):
        overall = Grade.REFUSED
    elif any(g is Grade.INCORRECT for g in grades):
        overall = Grade.INCORRECT
    else:
        overall = Grade.PARTIAL
    reason = "; ".join(f"{name}: {g.reason}" for name, g in graded)
    if judged_note is not None:
        reason = f"{reason}; {judged_note}"
    grade = _base(case, answer, overall, overall is Grade.CORRECT, reason)
    wrong = [g for _, g in graded if g.grade is Grade.INCORRECT]
    # A compound is a relevance miss, not a false number, when every wrong part is one.
    grade.incomplete = bool(wrong) and all(g.incomplete for g in wrong)
    grade.checks = checks
    # Arm-neutral: an uncited right part is PARTIAL but value-correct, and so is the
    # compound when every graded part is (docs/specs/compound-value-correct.md).
    grade.parts_value_correct = all(g.value_correct for _, g in graded)
    return grade


def _grade_ambiguous(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 15: every required assumption stated."""
    checks = {
        f"assumption[{i}]": states_phrase(answer.text, a)
        for i, a in enumerate(case.assumptions)
    }
    if checks and all(checks.values()):
        grade = _base(case, answer, Grade.CORRECT, True, "every assumption stated")
    elif answer.refused:
        grade = _base(case, answer, Grade.REFUSED, False, "declined without the assumption")
    else:
        grade = _base(case, answer, Grade.INCORRECT, False, "an assumption is not stated")
    grade.checks = checks
    return grade


def _grade_false_premise(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 16: the premise corrected, never affirmed."""
    correction = case.expected_text or ""
    corrected: frozenset[str] | None = None
    premise: frozenset[str] = frozenset()
    for token in _tokens(correction):
        if token in UP_WORDS:
            corrected, premise = UP_WORDS, DOWN_WORDS
            break
        if token in DOWN_WORDS:
            corrected, premise = DOWN_WORDS, UP_WORDS
            break
    if corrected is None:
        checks = {"correction_stated": states_phrase(answer.text, correction)}
    else:
        tokens = _tokens(answer.text)
        checks = {
            "direction_stated": any(t in corrected for t in tokens),
            "premise_not_affirmed": all(
                any(w in NEGATION_WORDS for w in tokens[max(0, i - NEGATION_WINDOW):i])
                for i, t in enumerate(tokens)
                if t in premise
            ),
        }
    if all(checks.values()):
        grade = _base(case, answer, Grade.CORRECT, True, "premise corrected")
    elif answer.refused:
        grade = _base(case, answer, Grade.REFUSED, False, "declined without correcting")
    else:
        failing = ", ".join(k for k, ok in checks.items() if not ok)
        grade = _base(case, answer, Grade.INCORRECT, False, f"failed checks: {failing}")
    grade.checks = checks
    return grade


def _grade_decline(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 17: a clean decline, no number, with the right reason."""
    phrase = REASON_PHRASES.get(case.decline_reason or "", "")
    checks = {
        "declined": answer.refused or answer.hedged,
        "no_number": not answer.numeric_values and not answer.rows,
        "reason": states_phrase(answer.text, phrase),
    }
    if not checks["no_number"]:
        grade = _base(case, answer, Grade.INCORRECT, False, "gave a number it should decline")
    elif not checks["declined"]:
        grade = _base(case, answer, Grade.INCORRECT, False, "did not decline")
    elif checks["reason"]:
        grade = _base(case, answer, Grade.CORRECT, True, "declined with the reason")
    else:
        grade = _base(
            case, answer, Grade.PARTIAL, False,
            f"declined without stating the {case.decline_reason} reason",
        )
    grade.checks = checks
    return grade


def _grade_text(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Requirement 18: unchanged narrative grading; the judge path scores the text."""
    return grade_narrative(case, answer)


_GRADERS: dict[str, Callable[[EvalCase, SystemAnswer], CaseGrade]] = {
    "screen": _grade_screen_in_context,
    "peer_relative": _grade_peer_relative,
    "trend": _grade_trend,
    "point_in_time": _grade_point_in_time,
    "compound": _grade_compound,
    "ambiguous": _grade_ambiguous,
    "false_premise": _grade_false_premise,
    "decline": _grade_decline,
    "text": _grade_text,
}
