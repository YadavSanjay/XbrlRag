# The ranked text a ``<<TOP:<id>:<n>>>`` token injects, and the rank label's removal.
#
# A dependent sub-query refers to an earlier screen's result through the token
# (``query_decomposer.TOP_TOKEN_RE``); the pipeline replaces it with that screen's top
# companies, each carrying its rank, so a position ("ranked first", "#2") resolves without
# re-ranking. The router removes the label from a company mention before resolving it.
# Spec: docs/specs/ranked-top-injection.md. Pure: no I/O, no logging, no LLM.

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# One trailing rank label, "(#<ASCII digits>)", optionally preceded by whitespace.
_RANK_LABEL_RE = re.compile(r"^(?P<name>.*?)\s*\(#[0-9]+\)$")


def ranked_top_text(rows: Sequence[Mapping[str, Any]], n: int) -> str | None:
    """The injection text for the top *n* rows of a screen.

    Args:
        rows: The prior screen step's ranked rows, in the order the step reports them
            (rank 1 first); each a mapping with a ``ticker``.
        n: The token's ``n``.

    Returns:
        ``"AMZN (#1), MSFT (#2), GOOGL (#3)"`` for ``n >= 2``, the bare ticker for
        ``n == 1``; None when no row of the top *n* names a ticker, or ``n < 1``.
    """
    if n < 1:
        return None
    top: list[tuple[int, str]] = []
    for label, row in enumerate(rows[:n], start=1):
        ticker = row.get("ticker")
        if isinstance(ticker, str) and ticker.strip():
            top.append((label, ticker.strip()))
    if not top:
        return None
    if n == 1:
        return top[0][1]
    return ", ".join(f"{ticker} (#{label})" for label, ticker in top)


def strip_rank_label(mention: str) -> str:
    """A company mention without a trailing rank label (``"AMZN (#1)"`` -> ``"AMZN"``).

    Args:
        mention: A company mention as the router LLM lifted it.

    Returns:
        The mention before its trailing ``(#k)`` label, stripped; *mention* unchanged when
        it carries no such label or nothing precedes it.
    """
    match = _RANK_LABEL_RE.match(mention.strip())
    if match is None:
        return mention
    name = match.group("name").strip()
    return name if name else mention
