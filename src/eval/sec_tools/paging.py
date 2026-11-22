# Paging for the ``frontier_sec_tools`` results: never silent truncation.
#
# A list result carries total, offset, next_offset and truncated, and holds as many whole
# rows as fit the size cap (requirement 14 of docs/specs/frontier-sec-tools.md).

from __future__ import annotations

import json
from typing import Any

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
# A stand-in for next_offset while sizing a page: at least as long as any real value.
_WIDEST_OFFSET = 10**12


class ToolError(Exception):
    """A tool failure the model reads as a result, with its code (requirement 16)."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        """Holds the failure.

        Args:
            code: The error code.
            message: What went wrong, for the model.
            **extra: Further payload fields.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def json_size(value: Any) -> int:
    """The length of *value* as ``json.dumps(..., ensure_ascii=False)``."""
    return len(json.dumps(value, ensure_ascii=False))


def fit_rows(
    head: dict[str, Any],
    rows: list[Any],
    offset: int,
    limit: int,
    cap: int,
    *,
    key: str = "rows",
    tail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One page of *rows*: at most *limit* whole rows from *offset*, within *cap* characters.

    Args:
        head: Fields placed before the rows.
        rows: Every row.
        offset: The first row of the page.
        limit: The most rows on the page.
        cap: The most characters of the payload, as JSON.
        key: The name of the rows field.
        tail: Fields placed after the envelope.

    Returns:
        ``{**head, key: page, "total", "offset", "next_offset", "truncated", **tail}``. A
        page holds at least one row when any remains, so walking ``next_offset`` ends.
    """
    total = len(rows)
    start = min(max(0, offset), total)

    def payload(page: list[Any], next_offset: int | None) -> dict[str, Any]:
        return {**head, key: page, "total": total, "offset": start,
                "next_offset": next_offset, "truncated": next_offset is not None,
                **(tail or {})}

    size = json_size(payload([], _WIDEST_OFFSET))
    count = 0
    for row in rows[start:start + max(0, limit)]:
        added = json_size(row) + (2 if count else 0)
        if size + added > cap and count:
            break
        if size + added > cap and not count:
            count = 1  # one row, always, so the walk progresses
            break
        size += added
        count += 1
    end = start + count
    next_offset = end if end < total and (count or limit == 0) else None
    if limit == 0 and start < total:
        next_offset = start
    return payload(rows[start:end], next_offset)
