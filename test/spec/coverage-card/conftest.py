"""Fixtures for the coverage-card spec tests.

Spec: docs/specs/coverage-card.md. The only I/O boundaries are the ``CoverageCardSource``
seam (faked in the test modules on hand-built rows) and two committed files read as text:
the view's DDL and the MV rebuild runner. A file the implementer has not written yet reads
as the empty string, so the checks fail on an assertion. Session-scoped so hypothesis tests
can use them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DDL_PATH = REPO_ROOT / "scripts" / "ddl" / "mv" / "42_mv_coverage_card.sql"
REBUILD_PATH = REPO_ROOT / "scripts" / "rebuild_mvs.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@pytest.fixture(scope="session")
def coverage_card_sql() -> str:
    """The committed DDL of mv_coverage_card (empty until the implementer adds it)."""
    return _read(DDL_PATH)


@pytest.fixture(scope="session")
def rebuild_mvs_py() -> str:
    """The committed MV rebuild runner, as text."""
    return _read(REBUILD_PATH)
