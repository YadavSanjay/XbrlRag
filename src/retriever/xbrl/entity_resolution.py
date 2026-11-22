# Company-name resolution with a threshold and a runner-up margin: a mention resolves to a
# filer only on an exact ticker, alias or normalized-name match, or when its fuzzy best
# match clears FUZZY_THRESHOLD and beats every other filer by FUZZY_MARGIN; otherwise it is
# ambiguous or unresolved and names its candidates.
# Spec: docs/specs/entity-resolution-margin.md.

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from src.retriever.xbrl.entity_aliases import normalize_company_mention

FUZZY_THRESHOLD = 0.75
FUZZY_MARGIN = 0.10
CANDIDATE_FLOOR = 0.30
MAX_CANDIDATES = 3

# Both comparisons allow this slack so an exact rational such as 12/16 passes ">= 0.75".
_SLACK = 1e-9

_LEGAL_FORM_WORDS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "llc", "lp", "the", "sa", "nv", "ag", "se",
})
# A trailing state-of-incorporation tag: "INC/DE", "CORP /NEW", "/CO/", "INC / MA".
_STATE_TAG = re.compile(r"/\s*[a-z]{1,3}/?\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WORD = re.compile(r"[a-z0-9]+")


class CompanyMatch(NamedTuple):
    """How one company mention resolved against a universe of filers (R3).

    Attributes:
        status: ``exact_ticker``, ``alias``, ``exact_name``, ``fuzzy_name``,
            ``ambiguous`` or ``unresolved``.
        cik: The resolved filer's 10-digit CIK; None unless resolved.
        ticker: The resolved filer's ticker; None unless resolved.
        company_name: The resolved filer's name; None unless resolved.
        score: 1.0 for an exact tier, else the best fuzzy score.
        candidates: CIKs named when not resolved, best first; ``()`` when resolved.
    """

    status: str
    cik: str | None
    ticker: str | None
    company_name: str | None
    score: float
    candidates: tuple[str, ...]


def company_name_key(name: str) -> str:
    """The normalized key of a company name or mention (R1).

    Args:
        name: A company name as filed, or a mention as the router extracted it.

    Returns:
        Lower-case ASCII tokens without legal-form words, single-spaced; possibly empty.
    """
    text = name.casefold().replace("’", "'").replace("'", "")
    text = text.replace("&", " and ")
    text = _STATE_TAG.sub("", text.rstrip())
    tokens = [t for t in _NON_ALNUM.sub(" ", text).split() if t not in _LEGAL_FORM_WORDS]
    while tokens and tokens[0] == "and":
        tokens.pop(0)
    while tokens and tokens[-1] == "and":
        tokens.pop()
    return " ".join(tokens)


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in _WORD.findall(text.lower()):
        padded = f"  {word} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def trigram_similarity(a: str, b: str) -> float:
    """``pg_trgm`` similarity of two strings, restricted to ASCII words (R2).

    Args:
        a: One string.
        b: The other string.

    Returns:
        Shared trigrams over all trigrams, in [0, 1]; 0.0 when either has none.
    """
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _first_rows(
    filers: Sequence[tuple[str, str | None, str]],
) -> dict[str, tuple[str, str | None, str]]:
    first: dict[str, tuple[str, str | None, str]] = {}
    for row in filers:
        first.setdefault(row[0], row)
    return first


def _resolved(status: str, row: tuple[str, str | None, str], score: float) -> CompanyMatch:
    return CompanyMatch(status, row[0], row[1], row[2], score, ())


def _ambiguous(ciks: set[str], score: float) -> CompanyMatch:
    return CompanyMatch("ambiguous", None, None, None, score, tuple(sorted(ciks)))


def resolve_company(
    mention: str,
    filers: Sequence[tuple[str, str | None, str]],
    aliases: Mapping[str, str],
) -> CompanyMatch:
    """Resolve one company mention against a universe of filers (R3).

    Args:
        mention: The company mention as the router extracted it.
        filers: Rows ``(cik, ticker, company_name)``; a CIK may have several rows.
        aliases: Normalized alias to CIK, as ``load_company_aliases`` returns it.

    Returns:
        The match: resolved by ticker, alias, exact name or fuzzy name with margin, or
        ambiguous / unresolved with its candidates.
    """
    first = _first_rows(filers)

    # 1) ticker
    normalized = normalize_company_mention(mention)
    if normalized:
        by_ticker = [r for r in filers if r[1] is not None and r[1].casefold() == normalized]
        ticker_ciks = {r[0] for r in by_ticker}
        if len(ticker_ciks) == 1:
            return _resolved("exact_ticker", by_ticker[0], 1.0)
        if len(ticker_ciks) > 1:
            return _ambiguous(ticker_ciks, 1.0)

    # 2) committed alias: authoritative, never handed to fuzzy matching
    alias_cik = aliases.get(normalized) if normalized else None
    if alias_cik is not None:
        if alias_cik in first:
            return _resolved("alias", first[alias_cik], 1.0)
        return CompanyMatch("unresolved", None, None, None, 0.0, ())

    # 3) exact normalized name
    key = company_name_key(mention)
    keys = {row: company_name_key(row[2]) for row in filers}
    if key:
        name_ciks = {row[0] for row, k in keys.items() if k == key}
        if len(name_ciks) == 1:
            return _resolved("exact_name", first[next(iter(name_ciks))], 1.0)
        if len(name_ciks) > 1:
            return _ambiguous(name_ciks, 1.0)

    # 4) fuzzy, per CIK: its best row
    best: dict[str, tuple[float, tuple[str, str | None, str]]] = {}
    for row, k in keys.items():
        score = trigram_similarity(key, k)
        held = best.get(row[0])
        if held is None or score > held[0]:
            best[row[0]] = (score, row)
    if not best:
        return CompanyMatch("unresolved", None, None, None, 0.0, ())
    ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
    s1, top_row = ranked[0][1]
    s2 = ranked[1][1][0] if len(ranked) > 1 else 0.0
    if s1 >= FUZZY_THRESHOLD - _SLACK and s1 - s2 >= FUZZY_MARGIN - _SLACK:
        return _resolved("fuzzy_name", top_row, s1)

    # 5) not resolved: name the closest candidates
    candidates = tuple(
        cik for cik, (score, _) in ranked if score >= CANDIDATE_FLOOR - _SLACK
    )[:MAX_CANDIDATES]
    status = "ambiguous" if candidates else "unresolved"
    return CompanyMatch(status, None, None, None, s1, candidates)
