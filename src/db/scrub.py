"""NUL scrubbing at the Postgres write boundary, shared by the audit and eval stores.

PostgreSQL rejects U+0000 in both ``text`` and ``jsonb`` ("unsupported Unicode escape
sequence ... \\u0000 cannot be converted to text"). An LLM answer, a filing excerpt or an
operator note occasionally contains one, and because both stores write a whole query or
run in one transaction *and* fail soft, a single stray byte silently discarded an entire
407-case eval run. Every store therefore passes its complete parameter set through
:func:`scrub_nul` right before ``execute``, so a newly added column is covered without
anyone remembering to scrub it.
"""

from __future__ import annotations

from typing import Any

_NUL = "\x00"


def scrub_nul(value: Any) -> Any:
    """Strips NUL characters from every string in *value*, recursively.

    Scrubbing happens on Python objects, before any JSON serialisation, so the
    ``\\u0000`` escape that ``json.dumps`` would emit for a NUL never comes into being.
    A literal backslash-u text sequence is ordinary data and is left alone.

    Args:
        value: A string, a dict (keys and values), a list or tuple (container type is
            kept, so a psycopg2 parameter tuple stays a tuple), or any other value.

    Returns:
        A copy of *value* with NUL removed from every string; non-string leaves (numbers,
        dates, ``None``, adapter objects) are returned unchanged.
    """
    if isinstance(value, str):
        return value.replace(_NUL, "") if _NUL in value else value
    if isinstance(value, dict):
        return {scrub_nul(k): scrub_nul(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(scrub_nul(v) for v in value)
    if isinstance(value, list):
        return [scrub_nul(v) for v in value]
    return value
