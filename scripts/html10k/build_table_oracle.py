r"""Build the table parser's cell oracle from the iXBRL tags of the development 10-Ks.

Test tooling (docs/specs/html10k-tables.md, Notes), not system input: this script reads tags so
that the parser's output can be scored, and nothing under ``src/`` may import it. Each tagged
numeric cell of a table becomes one JSON line in the record format of the spec; the output lands
under ``data/`` and is not committed.

    conda run -n py312 python scripts/html10k/build_table_oracle.py `
        --edgar-root <repo>\data\storage\sec_edgar --fiscal-year 2024

The report next to the oracle counts every tag dropped, by reason, because a rule that drops too
much would flatter the score. Filers of the held-out draw are refused, whatever is on disk.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from lxml import etree

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.ingest.html10k.tables import parse_oracle_record  # noqa: E402  (needs REPO_ROOT on path)

DEFAULT_EDGAR_ROOT = REPO_ROOT / "data" / "storage" / "sec_edgar"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "eval" / "html10k_oracle"
HELDOUT_DRAW = REPO_ROOT / "docs" / "eval" / "heldout_v2" / "heldout_v2_draw.json"

IX_NAMESPACE_MARK = "inlineXBRL"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
XBRLI = "{http://www.xbrl.org/2003/instance}"
ZERO_FORMATS = frozenset({"fixed-zero", "fixedzero", "zerodash"})
DOT_FORMATS = frozenset({"num-dot-decimal", "numdotdecimal", "num-dot-decimal-apos", "numcommadot"})
COMMA_FORMATS = frozenset({"num-comma-decimal", "numcommadecimal", "numdotcomma"})
WORD_FORMATS = frozenset({"numwordsen", "num-words-en"})

DASH_ONLY = re.compile(r"[\s$€£¥()%]*[-‐‑‒–—―−][-‐‑‒–—―−\s$€£¥()%]*")
LETTER = re.compile(r"[^\W\d_]")
DIGIT = re.compile(r"\d")
OPENS_NEGATIVE = re.compile(r"[(\-−–]\s*(?:US\$|NT\$|[$€£¥])?\s*$")
SPLIT_OPEN = re.compile(r"[\s$€£¥]*\([\s$€£¥(]*")
MAGNITUDE = re.compile(r"\d+(?:\.\d*)?|\.\d+")
RAISED_STYLE = re.compile(r"top:\s*-|vertical-align:\s*super", re.IGNORECASE)
WHITESPACE = re.compile(r"\s+")


class Filing(NamedTuple):
    """One 10-K on disk."""

    ticker: str
    fiscal_year: int
    accession: str
    path: Path


class Tag(NamedTuple):
    """A tagged number with the printed text around it in its cell."""

    element: Any
    before: str
    after: str


def local_name(element: Any) -> str:
    """Returns the tag name without its namespace, or "" for a comment."""
    return element.tag.rpartition("}")[2] if isinstance(element.tag, str) else ""


def is_fact_tag(element: Any) -> bool:
    """Tells whether ``element`` is an inline numeric fact."""
    tag = element.tag
    return isinstance(tag, str) and tag.endswith("}nonFraction") and IX_NAMESPACE_MARK in tag


def is_ix_header(element: Any) -> bool:
    """Tells whether ``element`` is the hidden header of an inline document."""
    tag = element.tag
    return isinstance(tag, str) and tag.endswith("}header") and IX_NAMESPACE_MARK in tag


def is_raised(element: Any) -> bool:
    """Tells whether ``element`` prints as a superscript."""
    if local_name(element) == "sup":
        return True
    return local_name(element) == "span" and bool(RAISED_STYLE.search(element.get("style", "")))


def held_out_tickers(draw_path: Path = HELDOUT_DRAW) -> frozenset[str]:
    """Returns every ticker in the held-out draw record, alternates included.

    Args:
        draw_path: The committed draw record.

    Returns:
        The tickers.

    Raises:
        FileNotFoundError: When the record is absent; the check never passes by default.
        ValueError: When the record names no filer.
    """
    found: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("ticker", "symbol") and isinstance(value, str):
                    found.add(value.upper())
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(json.loads(draw_path.read_text(encoding="utf-8")))
    if not found:
        raise ValueError(f"no tickers found in {draw_path}")
    return frozenset(found)


def iter_filings(
    edgar_root: Path, filers: Iterable[str] = (), fiscal_years: Iterable[int] = ()
) -> Iterator[Filing]:
    """Yields the 10-Ks on disk, never one of a held-out filer.

    Args:
        edgar_root: The root holding ``<TICKER>/<FY>/10-K/``.
        filers: Only these tickers; empty means every development filer.
        fiscal_years: Only these years; empty means all.

    Yields:
        Each filing, by ticker and year.

    Raises:
        ValueError: When ``filers`` names a held-out filer.
    """
    held_out = held_out_tickers()
    wanted = {f.upper() for f in filers}
    if wanted & held_out:
        raise ValueError("a requested filer is held out")
    years = set(fiscal_years)
    for ticker_dir in sorted(p for p in edgar_root.iterdir() if p.is_dir()):
        ticker = ticker_dir.name
        if ticker.startswith("_") or ticker.upper() in held_out:
            continue
        if wanted and ticker.upper() not in wanted:
            continue
        for manifest_path in sorted(ticker_dir.glob("*/10-K/manifest.yaml")):
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("is_amendment"):
                continue
            year = int(manifest["fiscal_year"])
            if years and year not in years:
                continue
            path = manifest_path.parent / manifest["primary_document"]
            yield Filing(ticker, year, manifest["accession_no"], path)


def own_descendants(root: Any, wanted: tuple[str, ...]) -> Iterator[Any]:
    """Yields the ``wanted`` elements under ``root``, not looking inside one or a nested table."""
    for child in root:
        name = local_name(child)
        if name in wanted:
            yield child
        elif name and name != "table":
            yield from own_descendants(child, wanted)


def printed_text(element: Any) -> str:
    """Returns the text ``element`` prints, whitespace collapsed."""
    text = etree.tostring(element, method="text", encoding="unicode", with_tail=False)
    return WHITESPACE.sub(" ", text).strip()


def text_around(cell: Any, tag: Any) -> tuple[str, str]:
    """Returns the text before and after ``tag`` in ``cell``, without raised markers."""
    before: list[str] = []
    after: list[str] = []
    current = [before]

    def walk(element: Any) -> None:
        if element is tag:
            current[0] = after
            return
        if not isinstance(element.tag, str) or is_raised(element):
            return
        if element.text:
            current[0].append(element.text)
        for child in element:
            walk(child)
            if child.tail:
                current[0].append(child.tail)

    walk(cell)
    return "".join(before), "".join(after)


def cell_tags(cell: Any, drops: Counter[str]) -> list[Any]:
    """Returns the outermost fact tags of a cell, counting the nested ones as dropped."""
    outer = []
    stack = [cell]
    while stack:
        element = stack.pop()
        for child in reversed(element):
            if local_name(child) == "table":
                continue
            if is_fact_tag(child):
                outer.append(child)
                drops["nested_tag"] += sum(1 for _ in child.iterdescendants("{*}nonFraction"))
            else:
                stack.append(child)
    return outer


def tag_magnitude(tag: Any) -> str | None:
    """Applies the tag's ``format`` to its text; ``None`` when it gives no plain magnitude."""
    text = re.sub(r"[\s$€£¥()\-−–]", "", printed_text(tag))
    transform = tag.get("format", "").rpartition(":")[2].lower()
    if transform in COMMA_FORMATS:
        text = text.replace(".", "").replace(",", ".")
    elif transform in DOT_FORMATS or not transform:
        text = text.replace(",", "").replace("'", "")
    else:
        return None
    return text if MAGNITUDE.fullmatch(text) else None


def tag_identity(tag: Any) -> tuple[str, str]:
    """Returns what two tags in one cell must share not to conflict: value and scale."""
    return printed_text(tag), tag.get("scale", "0")


def printed_negative(before: str, after: str, previous_cell: str) -> bool:
    """Reads the printed sign from the characters around the tag.

    Args:
        before: The cell's text before the tag.
        after: The cell's text after the tag.
        previous_cell: The text of the nearest printing cell to the left.

    Returns:
        True for parentheses or a minus around the number.
    """
    if OPENS_NEGATIVE.search(before):
        return True
    return not before.strip() and SPLIT_OPEN.fullmatch(previous_cell) is not None


def read_units(root: Any) -> dict[str, str]:
    """Maps each unit id to a short name: ``USD``, ``shares`` or ``USD/shares``."""
    units: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        measures = [m.text.rpartition(":")[2] for m in unit.iter(f"{XBRLI}measure") if m.text]
        units[unit.get("id", "")] = "/".join(measures)
    return units


class CellReader:
    """Turns the tagged cells of one filing into oracle records."""

    def __init__(self, accession: str, units: dict[str, str], drops: Counter[str]) -> None:
        self.accession = accession
        self.units = units
        self.drops = drops

    def read(
        self, cell: Any, previous_cell: str, location: tuple[int, int, int]
    ) -> dict[str, object] | None:
        """Returns the record of a cell, or ``None`` when its tags are dropped.

        Args:
            cell: The ``<td>``/``<th>`` element.
            previous_cell: The text of the nearest printing cell to the left.
            location: The table, row and cell ordinals.

        Returns:
            The record, or ``None``.
        """
        tags = cell_tags(cell, self.drops)
        if not tags:
            return None
        if len(tags) > 1:
            distinct = {tag_identity(t) for t in tags}
            self.drops["conflicting_tags" if len(distinct) > 1 else "multiple_tags"] += len(tags)
            return None
        tag = tags[0]
        before, after = text_around(cell, tag)
        outside = before + after
        if LETTER.search(outside) or DIGIT.search(outside):
            self.drops["other_text_in_cell"] += 1
            return None
        printed = printed_text(cell)
        is_nil_tag = tag.get(XSI_NIL, "false").lower() in ("true", "1")
        transform = tag.get("format", "").rpartition(":")[2].lower()
        fields = self._common_fields(tag, printed, location)
        if is_nil_tag or transform in ZERO_FORMATS:
            return self._nil_record(fields, printed, is_nil_tag)
        if transform in WORD_FORMATS:
            self.drops["word_format"] += 1
            return None
        value = tag_magnitude(tag)
        if value is None:
            self.drops["unreadable_value"] += 1
            return None
        negative = printed_negative(before, after, previous_cell)
        return {**fields, "value": value, "negative": negative, "nil": False}

    def _common_fields(
        self, tag: Any, printed: str, location: tuple[int, int, int]
    ) -> dict[str, object]:
        table_index, source_row, source_cell = location
        unit_ref = tag.get("unitRef", "")
        return {
            "accession": self.accession,
            "table_index": table_index,
            "source_row": source_row,
            "source_cell": source_cell,
            "scale": int(tag.get("scale", "0")),
            "printed": printed,
            "concept": tag.get("name", ""),
            "unit": self.units.get(unit_ref, unit_ref),
            "tagged_negative": tag.get("sign") == "-",
        }

    def _nil_record(
        self, fields: dict[str, object], printed: str, is_nil_tag: bool
    ) -> dict[str, object] | None:
        if not printed:
            self.drops["nil_prints_nothing"] += 1
            return None
        if not DASH_ONLY.fullmatch(printed):
            self.drops["nil_tag_prints_text" if is_nil_tag else "zero_format_prints_text"] += 1
            return None
        return {**fields, "value": None, "negative": False, "nil": True}


def build_records(source: bytes, accession: str) -> tuple[list[dict[str, object]], Counter[str]]:
    """Writes one record per tagged cell of a document.

    Args:
        source: The iXBRL primary document.
        accession: The filing's accession number.

    Returns:
        The records in document order and the count of dropped tags by reason.
    """
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(source, parser=parser)
    drops: Counter[str] = Counter()
    reader = CellReader(accession, read_units(root), drops)
    records: list[dict[str, object]] = []
    # A table inside the hidden header is not printed, and ix-strip removes it with the header.
    tables = [
        e
        for e in root.iter()
        if local_name(e) == "table" and not any(is_ix_header(a) for a in e.iterancestors())
    ]
    for table_index, table in enumerate(tables):
        for row_ordinal, row in enumerate(own_descendants(table, ("tr",))):
            previous_cell = ""
            for cell_ordinal, cell in enumerate(own_descendants(row, ("td", "th"))):
                record = reader.read(cell, previous_cell, (table_index, row_ordinal, cell_ordinal))
                if record is not None:
                    records.append(record)
                previous_cell = printed_text(cell) or previous_cell
    return records, drops


def main(argv: list[str] | None = None) -> int:
    """Builds the oracle file and its report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--edgar-root", type=Path, default=DEFAULT_EDGAR_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--fiscal-year", type=int, action="append", default=[])
    parser.add_argument("--filer", action="append", default=[])
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_drops: Counter[str] = Counter()
    per_filing = []
    total = 0
    oracle_path = args.out_dir / "table_oracle.jsonl"
    with oracle_path.open("w", encoding="utf-8", newline="\n") as out:
        for filing in iter_filings(args.edgar_root, args.filer, args.fiscal_year):
            records, drops = build_records(filing.path.read_bytes(), filing.accession)
            for record in records:
                parse_oracle_record(record)
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            all_drops.update(drops)
            total += len(records)
            per_filing.append(
                {
                    "ticker": filing.ticker,
                    "fiscal_year": filing.fiscal_year,
                    "accession": filing.accession,
                    "records": len(records),
                    "dropped": sum(drops.values()),
                }
            )
    report = {
        "filings": len(per_filing),
        "records": total,
        "dropped_by_reason": dict(sorted(all_drops.items())),
        "per_filing": per_filing,
    }
    report_path = args.out_dir / "table_oracle_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{len(per_filing)} filings, {total} records, dropped {dict(all_drops)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
