"""Every ifrs-full element the committed concept mapping names exists in the IFRS taxonomy.

A mapping row naming an element the taxonomy does not declare matches no fact, silently:
the IFRS ``Debt`` row named ``ifrs-full:NoncurrentBorrowings`` (declared in no IFRS taxonomy
2022-2025) and so gave TSMC, which tags only borrowing parts, no Debt row (2026-11-11,
docs/specs/debt-ifrs-elements.md requirement 5). The oracle is the IFRS Foundation's core
schemas, written to ``fixtures/ifrs_full_elements.tsv`` by
``scripts/xbrl/build_ifrs_element_list.py`` (source URLs and SHA-256 in its header). Pure:
no DB, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.ingest.util.generate_concept_mapping import (
    DEFAULT_COVERAGE_PATH,
    committed_mapping_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ifrs_full_elements.tsv"
REPO_ROOT = Path(__file__).resolve().parents[2]
# The statement-line rules name ifrs-full elements too (the Debt rule's `elements`).
STATEMENT_LINES = REPO_ROOT / "configs" / "eval" / "v2" / "statement_lines.yaml"
_IFRS_QNAME = re.compile(r"ifrs-full:([A-Za-z][A-Za-z0-9]*)")


def _declared() -> tuple[dict[str, set[str]], list[str]]:
    """Element -> taxonomy dates declaring it, and the dates the fixture was built from."""
    elements: dict[str, set[str]] = {}
    dates: list[str] = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.startswith("# source "):
            m = re.search(r"/taxonomy/([0-9-]+)/", line)
            assert m is not None, line
            dates.append(m.group(1))
        elif line and not line.startswith("#"):
            name, years = line.split("\t")
            elements[name] = set(years.split(","))
    return elements, dates


def _strings(node: object) -> list[str]:
    """Every key and string value of a parsed YAML tree (comments are already gone)."""
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [node] if isinstance(node, str) else []


def _mapping_ifrs_names() -> dict[str, str]:
    """Every ifrs-full local name the committed mapping names -> where it is named."""
    out: dict[str, str] = {}
    for row in committed_mapping_rows():
        m = _IFRS_QNAME.fullmatch(row["qname"])
        if m:
            out.setdefault(m.group(1), f"concept_mapping {row['concept_family']}")
    for path in (DEFAULT_COVERAGE_PATH, DEFAULT_COVERAGE_PATH.with_name(
            "concept_family_overrides.yaml"), STATEMENT_LINES):
        tree = yaml.safe_load(path.read_text(encoding="utf-8"))
        for s in _strings(tree):
            for m in _IFRS_QNAME.finditer(s):
                out.setdefault(m.group(1), path.name)
    return out


def test_fixture_covers_the_corpus_taxonomies():
    # oracle: the IFRS taxonomy dates of TSMC's 20-F filings (ifrs-family-meaning Notes) and
    # well-known core elements every IFRS balance sheet uses.
    elements, dates = _declared()
    assert dates == ["2022-03-24", "2023-03-23", "2024-03-27", "2025-03-27"]
    for name in ("Assets", "Borrowings", "Revenue", "ProfitLoss"):
        assert elements[name] == set(dates)
    assert "NoncurrentBorrowings" not in elements


def test_every_ifrs_element_of_the_mapping_exists_in_every_pinned_taxonomy():
    # oracle: the IFRS Foundation core schemas (fixture header gives URLs and SHA-256).
    elements, dates = _declared()
    named = _mapping_ifrs_names()
    assert "Borrowings" in named  # the scan reads the mapping at all
    missing = {n: where for n, where in named.items() if n not in elements}
    partial = {n: sorted(set(dates) - elements[n]) for n in named
               if n in elements and elements[n] != set(dates)}
    assert not missing, f"not declared in any pinned IFRS taxonomy: {missing}"
    assert not partial, f"not declared in every pinned IFRS taxonomy: {partial}"


@pytest.mark.parametrize("qname", ["ifrs-full:NoncurrentBorrowings",
                                   "ifrs-full:AcquisitionOfPropertyPlantAndEquipment"])
def test_the_removed_rows_do_not_exist(qname):
    # oracle: the IFRS core schemas 2022-2025 declare neither element (fixture).
    elements, _ = _declared()
    assert qname.split(":")[1] not in elements
