"""Layer-0 DDL must be re-runnable — every CREATE carries IF NOT EXISTS.

`bootstrap_db.py`'s module docstring promises "every step is idempotent and
re-runnable except `base`", and the operator guide tells you to run
`--phase schema` before ingesting. That promise was false: 11 of the 14
`scripts/ddl/layer_0/*.sql` files opened with a bare `CREATE TABLE`, so running
the documented command against an already-bootstrapped database aborted with
`psycopg2.errors.DuplicateTable: relation "chunk_cross_links" already exists`
on the first file alphabetically.

Nothing caught it because the failure only appears against a *populated*
database, which no test had. This asserts the property statically instead.

Scope: `IF NOT EXISTS` makes the phase re-runnable, NOT migrating — it skips a
table whose definition has since changed. A Layer-0 schema change still needs a
deliberate migration; this only guarantees a re-run does not raise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LAYER0 = Path(__file__).resolve().parents[2] / "scripts" / "ddl" / "layer_0"

# CREATE TABLE / CREATE INDEX / CREATE UNIQUE INDEX not followed by IF NOT EXISTS.
BARE_CREATE = re.compile(
    r"^CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\s+(?!IF\s+NOT\s+EXISTS)",
    re.MULTILINE | re.IGNORECASE,
)

SQL_FILES = sorted(LAYER0.glob("*.sql"))


def test_layer0_ddl_discovered() -> None:
    """Guard against the glob silently matching nothing (a vacuous pass)."""
    assert SQL_FILES, f"no .sql files found in {LAYER0}"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_creates_are_idempotent(path: Path) -> None:
    offenders = BARE_CREATE.findall(path.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{path.name} has {len(offenders)} CREATE statement(s) without IF NOT EXISTS: "
        f"{offenders}. `bootstrap_db.py --phase schema` is documented as re-runnable, "
        "so a second run against a bootstrapped database would raise DuplicateTable."
    )


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_seed_inserts_tolerate_existing_rows(path: Path) -> None:
    """A seed INSERT must not raise on a re-run either."""
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"^INSERT\s+INTO\b", text, re.MULTILINE | re.IGNORECASE):
        statement = text[match.start() : text.find(";", match.start())]
        assert re.search(r"ON\s+CONFLICT", statement, re.IGNORECASE), (
            f"{path.name} has an INSERT without ON CONFLICT: {statement.strip()[:120]!r}. "
            "Re-running the schema phase would fail on a duplicate key."
        )
