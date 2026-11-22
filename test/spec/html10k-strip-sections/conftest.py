"""Fixtures for html10k-strip-sections: committed, synthetic HTML for an invented filer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes() -> Callable[[str], bytes]:
    """Return a loader of one committed fixture's bytes, by file name."""
    return lambda name: (FIXTURES / name).read_bytes()
