#!/usr/bin/env python
"""Write the committed list of IFRS taxonomy elements the concept mapping may name.

Reads the IFRS Accounting Standards Taxonomy core schemas (``full_ifrs-cor_<date>.xsd``) and
writes every non-abstract item element (``substitutionGroup="xbrli:item"``) with the
taxonomy years that declare it, one per line, to
``test/unit/fixtures/ifrs_full_elements.tsv``. ``test/unit/test_mapping_ifrs_elements_exist.py``
checks every ``ifrs-full:`` QName of the committed concept mapping against it, so a row naming
an element the taxonomy does not declare (``ifrs-full:NoncurrentBorrowings``, 2026-11-11)
fails a unit test instead of silently matching no fact.

The schemas are the official IFRS Foundation files (byte-identical to Arelle's cache, which
fetches them while loading an IFRS filing)::

    https://xbrl.ifrs.org/taxonomy/<date>/full_ifrs/full_ifrs-cor_<date>.xsd

Usage (PowerShell; the default directory is Arelle's local cache)::

    conda run -n py312 python scripts/xbrl/build_ifrs_element_list.py `
      [--cache C:\\Users\\<you>\\AppData\\Local\\Arelle\\cache\\https\\xbrl.ifrs.org\\taxonomy]

A new taxonomy year: add its date to ``TAXONOMY_DATES`` and rerun.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "test" / "unit" / "fixtures" / "ifrs_full_elements.tsv"
# The taxonomy dates of the corpus's IFRS filings (TSMC's 20-Fs FY2021-FY2025).
TAXONOMY_DATES = ("2022-03-24", "2023-03-23", "2024-03-27", "2025-03-27")
SOURCE_URL = "https://xbrl.ifrs.org/taxonomy/{date}/full_ifrs/full_ifrs-cor_{date}.xsd"
_XSD_ELEMENT = "{http://www.w3.org/2001/XMLSchema}element"


def default_cache() -> Path:
    """Arelle's local cache of the IFRS taxonomy files."""
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Arelle" / "cache" / "https" / \
        "xbrl.ifrs.org" / "taxonomy"


def item_elements(schema: Path) -> set[str]:
    """The non-abstract item elements a core schema declares.

    Args:
        schema: A ``full_ifrs-cor_<date>.xsd`` file.

    Returns:
        Their local names.
    """
    out: set[str] = set()
    for el in ET.parse(schema).getroot().iter(_XSD_ELEMENT):
        name = el.get("name")
        if name and el.get("substitutionGroup") == "xbrli:item" and el.get("abstract") != "true":
            out.add(name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, default=None,
                        help="directory holding <date>/full_ifrs/full_ifrs-cor_<date>.xsd")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    cache = args.cache or default_cache()

    years: dict[str, list[str]] = {}
    header = [
        "# Non-abstract item elements of the IFRS Accounting Standards Taxonomy core schemas,",
        "# with the taxonomy dates that declare each. Written by",
        "# scripts/xbrl/build_ifrs_element_list.py; do not edit by hand.",
    ]
    for date in TAXONOMY_DATES:
        schema = cache / date / "full_ifrs" / f"full_ifrs-cor_{date}.xsd"
        if not schema.is_file():
            print(f"missing {schema}", file=sys.stderr)
            return 1
        digest = hashlib.sha256(schema.read_bytes()).hexdigest()
        header.append(f"# source {SOURCE_URL.format(date=date)} sha256 {digest}")
        for name in item_elements(schema):
            years.setdefault(name, []).append(date)
    lines = [f"{name}\t{','.join(dates)}" for name, dates in sorted(years.items())]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(header + lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(lines)} elements to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
