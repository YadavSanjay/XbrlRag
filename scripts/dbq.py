#!/usr/bin/env python
"""Run an ad-hoc SQL query against ``xbrl_db`` using the project's own connection.

Why this exists: the ingest image ships ``psycopg2`` but no ``psql``, so every
validation check on the AWS runner otherwise needs either a package install on the
host or a hand-written Python one-liner with the password inlined. This routes
through :func:`src.db.conn.conn_params`, so it uses **exactly** the connection the
pipeline uses — same ``XBRL_DB_*`` env, same ``sslmode`` policy (``require`` under
``deploy_mode=aws``). A query that works here proves the pipeline's own path works;
a separate psql invocation does not.

Usage (on the AWS runner, via the `run` helper from guide 4 §4.13.2)::

    run python scripts/dbq.py "SELECT count(*) FROM fact"
    run python scripts/dbq.py -f /data/checks.sql
    run python scripts/dbq.py --csv "SELECT cik, fiscal_year FROM filing_registry" > out.csv

Locally::

    conda run -n py312 python scripts/dbq.py "SELECT count(*) FROM accession"

Reads SQL from an argument, ``-f FILE``, or stdin. Multiple statements separated by
``;`` are run in order and each result set is printed. Read-only by default: the
transaction is rolled back unless ``--commit`` is passed.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.conn import conn_params  # noqa: E402

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]?[A-Za-z0-9_]*\$")


def _split_statements(sql: str) -> list[str]:
    """Split a script into statements on each ``;`` that ends one.

    A ``;`` inside a ``--`` or ``/* */`` comment, a string literal, a quoted
    identifier or a dollar-quoted body does not split. DDL files often carry a
    ``;`` in a trailing comment (``TEXT, -- NULL = all filers; ...``); splitting
    there cut a ``CREATE TABLE`` in half (2026-11-07 reload).

    Chunks holding nothing but comments are dropped: psycopg2 rejects a
    comment-only string with "can't execute an empty query", an error that points
    nowhere near the actual cause.

    Args:
        sql: The script text.

    Returns:
        The statements, stripped, without their terminating ``;``.
    """
    statements: list[str] = []
    start = i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch in ("'", '"'):
            i += 1
            while i < n:
                if sql[i] == ch:
                    if i + 1 < n and sql[i + 1] == ch:  # doubled quote escapes itself
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif ch == "$" and (tag := _DOLLAR_TAG.match(sql, i)):
            end = sql.find(tag.group(), tag.end())
            i = n if end < 0 else end + len(tag.group())
        elif ch == ";":
            statements.append(sql[start:i])
            i += 1
            start = i
        else:
            i += 1
    statements.append(sql[start:])
    return [s.strip() for s in statements if _has_executable_sql(s)]


def _has_executable_sql(chunk: str) -> bool:
    """True if *chunk* holds anything the server would parse as a statement."""
    return any(
        line.strip() and not line.strip().startswith("--")
        for line in chunk.splitlines()
    )


def _print_table(columns: list[str], rows: list[tuple]) -> None:
    widths = [len(c) for c in columns]
    text = [["" if v is None else str(v) for v in row] for row in rows]
    for row in text:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    print(line)
    print("-+-".join("-" * w for w in widths))
    for row in text:
        print(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Run SQL against xbrl_db using the project's connection settings.",
    )
    p.add_argument("sql", nargs="?", help="SQL text. Omit to read -f FILE or stdin.")
    p.add_argument("-f", "--file", metavar="FILE", help="Read SQL from FILE.")
    p.add_argument("--csv", action="store_true", help="Emit CSV instead of a table.")
    p.add_argument(
        "--commit",
        action="store_true",
        help="Commit instead of rolling back (required for INSERT/UPDATE/DDL).",
    )
    args = p.parse_args(argv)

    if args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    elif not sys.stdin.isatty():
        sql = sys.stdin.read()
    else:
        p.error("provide SQL as an argument, via -f FILE, or on stdin")

    statements = _split_statements(sql)
    if not statements:
        p.error("no SQL statements found")

    import psycopg2

    params = conn_params()
    # Never echo the password; the host/user/sslmode are the useful part when a
    # connection fails or points somewhere unexpected.
    print(
        f"-- {params['user']}@{params['host']}:{params['port']}/{params['dbname']} "
        f"sslmode={params['sslmode']}",
        file=sys.stderr,
    )

    conn = psycopg2.connect(**params)
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
                if cur.description is None:
                    print(f"-- {cur.statusmessage}", file=sys.stderr)
                    continue
                columns = [d.name for d in cur.description]
                rows = cur.fetchall()
                if args.csv:
                    writer = csv.writer(sys.stdout, lineterminator="\n")
                    writer.writerow(columns)
                    writer.writerows(rows)
                else:
                    _print_table(columns, rows)
                    print()
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
