# Reader for SEC's Financial Statement Data Sets (sub, num, pre, tag). It joins and
# filters SEC's tab-separated tables; it does not parse XBRL. Used when reference
# fixtures are built, and by the reload's extension-classification step (ingest only,
# docs/specs/extension-classification.md R7); never imported at query time.
# Spec: docs/specs/fsds-reader.md.

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

_TABLES = ("sub", "num", "pre", "tag")


class FsdsTables(TypedDict):
    """The four tables, each a list of rows keyed by SEC's column names (requirement 1).

    Attributes:
        sub: Submission rows.
        num: Value rows.
        pre: Statement-line rows.
        tag: Concept rows.
    """

    sub: list[dict[str, str]]
    num: list[dict[str, str]]
    pre: list[dict[str, str]]
    tag: list[dict[str, str]]


class StatementLine(TypedDict):
    """One ``pre`` row of a filing, with typed fields (requirement 4)."""

    adsh: str
    report: int
    line: int
    stmt: str
    inpth: bool
    tag: str
    version: str
    custom: bool
    plabel: str
    negating: bool


class NumValue(TypedDict):
    """One ``num`` row of a filing, with typed fields (requirement 5)."""

    adsh: str
    tag: str
    version: str
    custom: bool
    ddate: str
    qtrs: int
    uom: str
    segments: str | None
    coreg: str | None
    value: float | None


class StatementValue(TypedDict):
    """A statement line joined to its value for one period (requirement 10)."""

    line: StatementLine
    value: float | None
    presented: float | None


def _read_tsv(path: Path) -> list[dict[str, str]]:
    """Reads one SEC tsv file: UTF-8, tab-delimited, ``\\n``-terminated, no quoting."""
    text = path.read_bytes().decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return []
    header = lines[0].split("\t")
    rows: list[dict[str, str]] = []
    for ln in lines[1:]:
        fields = ln.split("\t")
        if len(fields) != len(header):
            raise ValueError(f"{path}: {len(fields)} fields, header has {len(header)}")
        rows.append(dict(zip(header, fields, strict=True)))
    return rows


def load_tables(dirs: Sequence[str | os.PathLike[str]]) -> FsdsTables:
    """Reads sub/num/pre/tag.tsv from each directory, concatenated in order.

    Args:
        dirs: Directories, each holding ``sub.tsv``, ``num.tsv``, ``pre.tsv``, ``tag.tsv``.

    Returns:
        The rows of each table as raw strings keyed by column name.

    Raises:
        FileNotFoundError: A directory lacks one of the four files.
    """
    rows: dict[str, list[dict[str, str]]] = {t: [] for t in _TABLES}
    for d in dirs:
        base = Path(d)
        missing = [t for t in _TABLES if not (base / f"{t}.tsv").is_file()]
        if missing:
            raise FileNotFoundError(f"{base}: missing {', '.join(t + '.tsv' for t in missing)}")
        for t in _TABLES:
            rows[t].extend(_read_tsv(base / f"{t}.tsv"))
    return {"sub": rows["sub"], "num": rows["num"], "pre": rows["pre"], "tag": rows["tag"]}


def submission(tables: FsdsTables, adsh: str) -> dict[str, str] | None:
    """Returns the first ``sub`` row of ``adsh``, or None.

    Args:
        tables: The loaded tables.
        adsh: Accession number with dashes.

    Returns:
        The raw row, or None when absent.
    """
    return next((r for r in tables["sub"] if r["adsh"] == adsh), None)


def is_custom(version: str, adsh: str) -> bool:
    """Whether a tag version marks a filer extension (``version == adsh``).

    Args:
        version: The ``version`` field of a pre/num/tag row.
        adsh: The filing's accession number.

    Returns:
        True for a custom tag.
    """
    return version == adsh


def _flag(field: str) -> bool:
    if field == "1":
        return True
    if field == "0":
        return False
    raise ValueError(f"expected a 0/1 flag, got {field!r}")


def statement_lines(tables: FsdsTables, adsh: str, stmt: str | None = None) -> list[StatementLine]:
    """Returns a filing's statement lines sorted by (report, line).

    Args:
        tables: The loaded tables.
        adsh: Accession number.
        stmt: Optional statement code (``IS``, ``BS``, ``CF``, ...).

    Returns:
        One typed line per ``pre`` row.
    """
    lines: list[StatementLine] = [
        {
            "adsh": r["adsh"],
            "report": int(r["report"]),
            "line": int(r["line"]),
            "stmt": r["stmt"],
            "inpth": _flag(r["inpth"]),
            "tag": r["tag"],
            "version": r["version"],
            "custom": is_custom(r["version"], adsh),
            "plabel": r["plabel"],
            "negating": _flag(r["negating"]),
        }
        for r in tables["pre"]
        if r["adsh"] == adsh and (stmt is None or r["stmt"] == stmt)
    ]
    lines.sort(key=lambda x: (x["report"], x["line"]))
    return lines


def _iso(yyyymmdd: str) -> str:
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").date().isoformat()


def num_values(
    tables: FsdsTables, adsh: str, tag: str | None = None, consolidated: bool = True
) -> list[NumValue]:
    """Returns a filing's values, optionally for one tag and consolidated only.

    Args:
        tables: The loaded tables.
        adsh: Accession number.
        tag: Optional tag name.
        consolidated: Keep only rows without segments and without a co-registrant.

    Returns:
        One typed value per ``num`` row, in table order.
    """
    out: list[NumValue] = []
    for r in tables["num"]:
        if r["adsh"] != adsh or (tag is not None and r["tag"] != tag):
            continue
        segments = r.get("segments", "") or None
        coreg = r.get("coreg", "") or None
        if consolidated and (segments is not None or coreg is not None):
            continue
        out.append({
            "adsh": r["adsh"],
            "tag": r["tag"],
            "version": r["version"],
            "custom": is_custom(r["version"], adsh),
            "ddate": _iso(r["ddate"]),
            "qtrs": int(r["qtrs"]),
            "uom": r["uom"],
            "segments": segments,
            "coreg": coreg,
            "value": float(r["value"]) if r["value"] != "" else None,
        })
    return out


def fsds_period(period_start: str | None, period_end: str) -> tuple[str, int]:
    """Encodes an XBRL period as FSDS ``(ddate, qtrs)``.

    Args:
        period_start: ISO start date, or None for an instant.
        period_end: ISO end date as XBRL writes it (inclusive).

    Returns:
        ``(ddate as yyyy-mm-dd, qtrs)``.

    Raises:
        ValueError: The end is equidistant from two month ends, or start is after end.
    """
    end = dt.date.fromisoformat(period_end)
    first_next = (end.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    this_end = first_next - dt.timedelta(days=1)
    prev_end = end.replace(day=1) - dt.timedelta(days=1)
    up, down = (this_end - end).days, (end - prev_end).days
    if up == down:
        raise ValueError(f"{period_end} is equidistant from {prev_end} and {this_end}")
    ddate = this_end if up < down else prev_end
    if period_start is None:
        return ddate.isoformat(), 0
    start = dt.date.fromisoformat(period_start)
    if start > end:
        raise ValueError(f"period start {period_start} is after end {period_end}")
    days = (end - start).days + 1
    return ddate.isoformat(), round(days * 4 / 365.25)


def fsds_uom(unit: str) -> str:
    """Maps an XBRL/companyfacts unit to the FSDS ``uom`` (``X/shares`` becomes ``X``).

    Args:
        unit: The unit, e.g. ``USD``, ``USD/shares``, ``shares``.

    Returns:
        The FSDS unit of measure.
    """
    head, sep, tail = unit.partition("/")
    if sep and tail == "shares":
        return head
    return unit


def period_value(
    tables: FsdsTables,
    adsh: str,
    tag: str,
    period_start: str | None,
    period_end: str,
    uom: str | None = None,
    version: str | None = None,
) -> float | None:
    """Returns the consolidated value of a tag for one period of a filing.

    Args:
        tables: The loaded tables.
        adsh: Accession number.
        tag: Tag name.
        period_start: ISO start date, or None for an instant.
        period_end: ISO end date (inclusive).
        uom: Optional unit (mapped with ``fsds_uom``).
        version: Optional tag version.

    Returns:
        The value, or None when no non-nil candidate exists.

    Raises:
        ValueError: The candidates hold more than one distinct value.
    """
    ddate, qtrs = fsds_period(period_start, period_end)
    want_uom = fsds_uom(uom) if uom is not None else None
    values: set[float] = set()
    for v in num_values(tables, adsh, tag, consolidated=True):
        if (v["ddate"], v["qtrs"]) != (ddate, qtrs):
            continue
        if want_uom is not None and v["uom"] != want_uom:
            continue
        if version is not None and v["version"] != version:
            continue
        if v["value"] is not None:
            values.add(v["value"])
    if not values:
        return None
    if len(values) > 1:
        raise ValueError(
            f"{adsh} {tag} ({ddate}, {qtrs}q): {len(values)} distinct values {sorted(values)};"
            " pass a uom or version"
        )
    return values.pop()


def presented_value(line: StatementLine, value: float | None) -> float | None:
    """Returns the value as the statement shows it (negated on a negating line).

    Args:
        line: The statement line.
        value: The fact value, or None.

    Returns:
        ``-value`` when the line is negating, else ``value``; None stays None.
    """
    if value is None:
        return None
    return -value if line["negating"] else value


def statement(
    tables: FsdsTables,
    adsh: str,
    stmt: str,
    period_start: str | None,
    period_end: str,
    uom: str | None = None,
) -> list[StatementValue]:
    """Joins a statement's lines to their values for one period.

    Args:
        tables: The loaded tables.
        adsh: Accession number.
        stmt: Statement code.
        period_start: ISO start date, or None for an instant.
        period_end: ISO end date (inclusive).
        uom: Optional unit.

    Returns:
        One row per statement line, in line order.

    Raises:
        ValueError: A line's value is ambiguous.
    """
    out: list[StatementValue] = []
    for line in statement_lines(tables, adsh, stmt):
        value = period_value(
            tables, adsh, line["tag"], period_start, period_end, uom, line["version"]
        )
        out.append({"line": line, "value": value, "presented": presented_value(line, value)})
    return out
