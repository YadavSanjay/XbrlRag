"""Named groups in a question: resolved by rule, routed to a screen.

Spec: docs/specs/screen-aggregates.md (Requirements 1-4 and 6). A question over a named
group ("the combined capex of the retailers", "the median R&D of the semiconductor
companies") ranks a *universe*: ``covered`` (every corpus filer) or a group id of
``configs/eval/v2/groups.yaml``. The group is found in the question's words by a fixed rule
over the group ids and a committed alias table, never by the LLM, as companies are resolved
by SQL. A question over one group and no company goes to the screen path, and the plan must
screen that group and, when the question says so with an unambiguous word, aggregate it.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

COVERED = "covered"
AGGREGATE_FNS = ("sum", "median", "mean", "count")

# The committed alias table (reviewed data, docs/specs/screen-aggregates.md Notes): common
# English for each group's definition. Written from the group definitions, never copied
# from the eval lexicon or phrasebooks, so paraphrase cases still measure generalization.
# `covered` has phrases only from here: the bare word "covered" is ordinary English.
_UNIVERSE_ALIASES: dict[str, list[str]] = {
    COVERED: ["covered companies", "companies in coverage", "all companies covered"],
    "semiconductors": [
        "semiconductor companies", "chip companies", "chipmakers", "chip makers",
    ],
    "retailers": ["retail companies", "retail sector"],
    "big_tech": ["big tech companies", "largest technology companies"],
}

_NON_WORD = re.compile(r"[^a-z0-9&]+")

_MEDIAN_WORDS = frozenset({"median", "medians"})
_SUM_WORDS = frozenset({"combined", "aggregate", "aggregated", "collectively", "altogether",
                        "summed"})
_SUM_PAIRS = frozenset({("sum", "of"), ("in", "total"), ("taken", "together")})

# docs/specs/group-sum-cue.md, Requirement 1 step 3 (written from English usage).
_ADD_UP_PAIRS = frozenset({("add", "up"), ("adds", "up"), ("added", "up"), ("adding", "up")})
_TOTAL_WORDS = frozenset({"total", "totals"})
_TOTAL_BLOCKING_WORDS = frozenset({
    "top", "bottom", "highest", "lowest", "largest", "smallest", "biggest", "most", "least",
    "rank", "ranks", "ranked", "ranking", "which", "who", "whose", "each", "every", "per",
    "versus", "vs", "compare", "compared", "comparison", "than", "list", "breakdown", "split",
    "median", "average", "mean",
})
_PER_MEMBER_PAIRS = frozenset({("by", "company"), ("by", "companies"), ("by", "filer"),
                               ("by", "member"), ("by", "ticker")})
_NUMBER_WORDS = frozenset({
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty",
})
_YEAR_MIN, _YEAR_MAX = 2000, 2099

_NUMERIC_INTENTS = frozenset({"NUMERIC_LOOKUP", "NUMERIC_COMPUTE", "NUMERIC_SCREEN"})
_WIDEN_WITH_CUE = frozenset({"NARRATIVE", "HYBRID"})
SCREEN_INTENT = "NUMERIC_SCREEN"


def _words(text: str) -> list[str]:
    """Requirement 1's normalization: lower case, non ``[a-z0-9&]`` to space, split."""
    return _NON_WORD.sub(" ", str(text).lower()).split()


def universe_aliases() -> dict[str, list[str]]:
    """The committed alias table: universe id to the phrases that name it.

    Returns:
        ``{"covered": [...], <group id>: [...], ...}``; keys are ``covered`` and group ids
        of ``configs/eval/v2/groups.yaml`` only. Requirement 1. A fresh copy each call.
    """
    return copy.deepcopy(_UNIVERSE_ALIASES)


def _phrase_table(
    groups: Mapping[str, Sequence[str]], aliases: Mapping[str, Sequence[str]]
) -> dict[tuple[str, ...], str]:
    """Every phrase's words to its universe, validated.

    Raises:
        ValueError: An alias key that is neither ``covered`` nor a group id, a phrase with
            no words, or a phrase of two universes.
    """
    table: dict[tuple[str, ...], str] = {}
    shown: dict[tuple[str, ...], str] = {}

    def add(phrase: str, universe: str) -> None:
        key = tuple(_words(phrase))
        if not key:
            raise ValueError(f"universe {universe!r}: phrase {phrase!r} has no words")
        owner = table.get(key)
        if owner is not None and owner != universe:
            raise ValueError(
                f"phrase {phrase!r} ({' '.join(key)!r}) names two universes: "
                f"{owner!r} (as {shown[key]!r}) and {universe!r}"
            )
        table[key] = universe
        shown.setdefault(key, phrase)

    for gid in groups:
        add(str(gid).replace("_", " "), str(gid))
    for universe, phrases in aliases.items():
        if universe != COVERED and universe not in groups:
            valid = ", ".join([COVERED, *sorted(groups)])
            raise ValueError(f"alias key {universe!r} is not a universe; valid: {valid}")
        for phrase in phrases:
            add(str(phrase), str(universe))
    return table


def _masked(words: list[str], ignore: Sequence[str]) -> list[str | None]:
    """The words with every word-sequence occurrence of an ``ignore`` string masked."""
    out: list[str | None] = list(words)
    for text in ignore:
        target = _words(text)
        if not target:
            continue
        n = len(target)
        for i in range(len(words) - n + 1):
            if words[i:i + n] == target:
                for j in range(i, i + n):
                    out[j] = None
    return out


def find_universe_mentions(
    text: str,
    groups: Mapping[str, Sequence[str]],
    aliases: Mapping[str, Sequence[str]],
    ignore: Sequence[str] = (),
) -> list[str]:
    """The universes a text names, by exact word match of their phrases.

    Args:
        text: The question (or one mention).
        groups: Group id to member tickers (``load_universe_groups()["groups"]``).
        aliases: Universe id to extra phrases (``universe_aliases()``).
        ignore: Strings whose occurrences in ``text`` match no phrase (resolved company
            mentions).

    Returns:
        Universe ids in order of first occurrence, without duplicates.

    Raises:
        ValueError: An alias key that is neither ``covered`` nor a group id, a phrase with
            no words, or a phrase of two universes.
    """
    table = _phrase_table(groups, aliases)
    lengths = sorted({len(k) for k in table}, reverse=True)
    words = _masked(_words(text), ignore)
    found: list[str] = []
    i = 0
    while i < len(words):
        match: tuple[int, str] | None = None
        for n in lengths:  # longest first
            window = words[i:i + n]
            if len(window) < n or any(w is None for w in window):
                continue
            universe = table.get(tuple(w for w in window if w is not None))
            if universe is not None:
                match = (n, universe)
                break
        if match is None:
            i += 1
            continue
        n, universe = match
        if universe not in found:
            found.append(universe)
        i += n
    return found


def split_company_mentions(
    mentions: Sequence[str],
    groups: Mapping[str, Sequence[str]],
    aliases: Mapping[str, Sequence[str]],
) -> tuple[list[str], list[str]]:
    """Separate group mentions from company mentions.

    Args:
        mentions: The router LLM's company mentions.
        groups: Group id to member tickers.
        aliases: Universe id to extra phrases.

    Returns:
        ``(kept, universes)``: the mentions naming no universe, in order, and the universes
        named by the others, in order of first occurrence without duplicates.
    """
    kept: list[str] = []
    universes: list[str] = []
    for mention in mentions:
        named = find_universe_mentions(str(mention), groups, aliases)
        if not named:
            kept.append(mention)
            continue
        for universe in named:
            if universe not in universes:
                universes.append(universe)
    return kept, universes


def aggregate_cue(query: str) -> str | None:
    """The aggregate a question asks for by an unambiguous word.

    ``total`` ("total assets"), ``average``/``mean`` ("average shares outstanding") and
    ``how many`` are deliberately not cues here. For a question over one named group,
    ``group_aggregate_cue`` (docs/specs/group-sum-cue.md) extends this with ``total`` as an
    amount word and the add-up phrase; the router uses that function.

    Args:
        query: The question.

    Returns:
        ``"median"``, ``"sum"``, or None (no cue, or cues of both kinds).
    """
    words = _words(query)
    median = any(w in _MEDIAN_WORDS for w in words)
    total = any(w in _SUM_WORDS for w in words) or any(
        pair in _SUM_PAIRS for pair in zip(words, words[1:], strict=False)
    )
    if median == total:
        return None
    return "median" if median else "sum"


def group_aggregate_cue(
    query: str, universes: Sequence[str], resolved_companies: int
) -> str | None:
    """The aggregate a question over one named group asks for.

    Spec: docs/specs/group-sum-cue.md (Requirement 1). ``aggregate_cue(query)`` first; then,
    for one universe and no resolved company, ``"sum"`` on an add-up phrase or on ``total``
    used as an amount word (no ranking, comparison or per-member word, per-member pair, or
    count).

    Args:
        query: The question.
        universes: The universes found in the question (not mutated).
        resolved_companies: How many company mentions resolved to a filer.

    Returns:
        ``"median"``, ``"sum"``, or None.
    """
    return group_aggregate_rule(query, universes, resolved_companies)[0]


def _is_count(word: str) -> bool:
    """A digit token that is not a year (2000-2099), or a number word one ... twenty."""
    if word in _NUMBER_WORDS:
        return True
    return word.isdigit() and not _YEAR_MIN <= int(word) <= _YEAR_MAX


def group_aggregate_rule(
    query: str, universes: Sequence[str], resolved_companies: int
) -> tuple[str | None, str | None]:
    """``group_aggregate_cue`` with the rule that fired.

    Args:
        query: The question.
        universes: The universes found in the question (not mutated).
        resolved_companies: How many company mentions resolved to a filer.

    Returns:
        ``(aggregate, rule)``: rule ``"cue"`` (``aggregate_cue``), ``"add_up"`` or
        ``"total"``; ``(None, None)`` when nothing fired.
    """
    cued = aggregate_cue(query)
    if cued is not None:
        return cued, "cue"
    if len(universes) != 1 or resolved_companies != 0:
        return None, None
    words = _words(query)
    pairs = list(zip(words, words[1:], strict=False))
    if any(pair in _ADD_UP_PAIRS for pair in pairs):
        return "sum", "add_up"
    if not any(w in _TOTAL_WORDS for w in words):
        return None, None
    if any(w in _TOTAL_BLOCKING_WORDS or _is_count(w) for w in words):
        return None, None
    if any(pair in _PER_MEMBER_PAIRS for pair in pairs):
        return None, None
    return "sum", "total"


def universe_intent(
    intent: str, universes: Sequence[str], resolved_companies: int, aggregate: str | None
) -> str:
    """The intent a question over a named group is routed with.

    Args:
        intent: The router LLM's intent value, e.g. ``"NUMERIC_COMPUTE"``.
        universes: The universes found in the question.
        resolved_companies: How many company mentions resolved to a filer.
        aggregate: ``aggregate_cue(query)``.

    Returns:
        ``"NUMERIC_SCREEN"`` for one universe, no company, and a numeric intent (or a
        narrative/hybrid one with a cue); otherwise ``intent`` unchanged.
    """
    if len(universes) != 1 or resolved_companies > 0:
        return intent
    if intent in _NUMERIC_INTENTS:
        return SCREEN_INTENT
    if intent in _WIDEN_WITH_CUE and aggregate is not None:
        return SCREEN_INTENT
    return intent


def group_plan_errors(plan: Any, universe: str | None, aggregate: str | None) -> list[str]:
    """Plan errors when a plan ignores the resolved group or the cued aggregate.

    Run by the plan generator beside ``PlanValidator.validate``; the errors feed the same
    retry (Requirement 6).

    Args:
        plan: The planner's ``{"steps": [...]}``.
        universe: The universe the router resolved, or None.
        aggregate: The cued aggregate fn, or None.

    Returns:
        One error per screen step of another universe, then one when no aggregate step of
        the cued fn aggregates a screen step; ``[]`` when there is nothing to check.
    """
    if universe is None and aggregate is None:
        return []
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return []
    steps = [s for s in plan["steps"] if isinstance(s, dict)]
    errors: list[str] = []
    if universe is not None:
        for index, step in enumerate(plan["steps"]):
            if not isinstance(step, dict) or step.get("op") != "screen":
                continue
            planned = step.get("universe")
            planned = COVERED if planned is None else planned
            if planned != universe:
                errors.append(
                    f"step[{index}] (id={step.get('id')!r}) screens universe {planned!r}, "
                    f"but the question names the {universe!r} universe; set "
                    f"\"universe\": {universe!r} on every screen step"
                )
    if aggregate is not None:
        screen_ids = {s.get("id") for s in steps if s.get("op") == "screen"}
        has = any(
            s.get("op") == "aggregate" and s.get("fn") == aggregate and s.get("of") in screen_ids
            for s in steps
        )
        if not has:
            errors.append(
                f"the question asks for a {aggregate} over the group: add an aggregate step "
                f"{{\"op\": \"aggregate\", \"of\": <screen step id>, \"fn\": {aggregate!r}}} "
                f"after the screen step"
            )
    return errors
