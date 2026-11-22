"""Fixtures for entity-resolution-margin (docs/specs/entity-resolution-margin.md).

The committed data lives in ``erm_data.py`` beside this file (made importable here, as the
gate runs with ``--import-mode=importlib``); the only fixture is the committed alias table,
read through the product's own loader (a config file, not a fake).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.retriever.xbrl.entity_aliases import load_company_aliases

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def committed_aliases() -> dict[str, str]:
    """The committed alias table (configs/entities/company_aliases.yaml)."""
    return load_company_aliases()
