"""Arelle plugin: export each fact's ``xml_id`` and values, as xbrlDB would store them.

Used by ``transform_backfill`` to repair facts stored without the SEC's inline transforms
(docs/specs/ixt-sec-transforms.md). It runs inside Arelle's own Python, writes one JSON file
and never connects to the database. Load it after ``EDGAR/transform``:

    arelleCmdLine --plugins "EDGAR/transform|path/to/export_transformed_facts.py" \
        -f path/to/filing.htm --transformed-facts-output /tmp/facts.json

Output JSON schema:
    {"count": <int>, "rows": [{"xml_id": "...", "value": "...", "effective_value": "..."}]}

``effective_value`` is null for a fact that is nil or not numeric.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def walk_facts(facts: Iterable[Any]) -> Iterator[Any]:
    """Yields each fact and, as xbrlDB's insertFactSet does, the facts inside a tuple.

    Args:
        facts: Arelle's top-level facts.

    Yields:
        Each fact.
    """
    for fact in facts:
        yield fact
        if fact.isTuple:
            yield from walk_facts(fact.modelTupleFacts)


def fact_row(fact: Any, xml_id: str, round_value: Any) -> dict[str, Any]:
    """The row for one fact, with xbrlDB's own ``fact_value`` and ``effective_value``.

    Args:
        fact: An Arelle fact.
        xml_id: The fact's ``elementFragmentIdentifier``.
        round_value: Arelle's ``roundValue``.

    Returns:
        ``xml_id``, ``value`` and ``effective_value``, the last as a string or None.
    """
    effective = None
    if fact.isNumeric and not fact.isNil:
        effective = str(round_value(fact.value, fact.precision, fact.decimals))
    # xbrlDB's SqlDb.dbStr doubles each % on Postgres, so the store holds the doubled text.
    value = None if fact.value is None else str(fact.value).replace("%", "%%")
    return {"xml_id": xml_id, "value": value, "effective_value": effective}


def cmdLineOptionExtender(parser: Any, *args: Any, **kwargs: Any) -> None:
    """Hook: adds the output option."""
    parser.add_option(
        "--transformed-facts-output",
        action="store",
        dest="transformedFactsOutput",
        help="Path of the JSON file of each fact's xml_id and values.",
    )


def cmdLineXbrlLoaded(cntlr: Any, options: Any, modelXbrl: Any, *args: Any, **kwargs: Any) -> None:
    """Hook: writes the JSON once the instance is loaded."""
    output_path = getattr(options, "transformedFactsOutput", None)
    if not output_path:
        return
    # Inside Arelle's runtime only; this module also imports without Arelle, for tests.
    # Arelle ships no type information, hence the ignores.
    from arelle.ValidateXbrlCalcs import roundValue  # type: ignore[import-untyped]
    from arelle.XmlUtil import elementFragmentIdentifier  # type: ignore[import-untyped]

    rows = [
        fact_row(fact, elementFragmentIdentifier(fact), roundValue)
        for fact in walk_facts(modelXbrl.facts)
    ]
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump({"count": len(rows), "rows": rows}, handle)


__pluginInfo__ = {
    "name": "XbrlRag Export Transformed Facts",
    "version": "1.0",
    "description": "Exports each fact's xml_id and values to JSON for transform_backfill.",
    "author": "XbrlRag",
    "license": "Apache-2",
    "CntlrCmdLine.Options": cmdLineOptionExtender,
    "CntlrCmdLine.Xbrl.Loaded": cmdLineXbrlLoaded,
}
