"""Repairs facts stored without the SEC's inline transforms.

Facts stored while ``EDGAR/transform`` wasn't loaded hold ``(ixTransformValueError)``. This
module runs Arelle with the plugin over each such filing and updates only those rows, in
place, so that every ``fact_id`` stays (docs/specs/ixt-sec-transforms.md). Any mismatch
between the store and Arelle stops that filing. Dry by default:

    python -m src.ingest.sec_edgar.ingest.transform_backfill           # plan every filing
    python -m src.ingest.sec_edgar.ingest.transform_backfill --apply   # and write it
    ... --accession 0000021344-24-000009                                # one filing

Run the publish gate only after the repair: a filing that still holds the marker is held back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.db.conn import connect
from src.ingest.util.arelle_cmd import arelle_cmd_from_env, resolve_arelle_cmd
from src.ingest.util.arelle_run import connectivity_args, run_arelle
from src.logging_config import get_logger

logger = get_logger(__name__)

_MARKER = "(ixTransformValueError)"
_PLUGIN_PATH = Path(__file__).parent / "arelle_plugins" / "export_transformed_facts.py"
_UPDATE_SQL = (
    "UPDATE fact SET fact_value = %s, effective_value = %s "
    "WHERE fact_id = %s AND fact_value = %s"
)
_FILINGS_SQL = """
    SELECT a.filing_accession_number, a.accession_id, a.entry_url
      FROM accession a
     WHERE EXISTS (SELECT 1 FROM fact f
                    WHERE f.accession_id = a.accession_id AND f.fact_value = %s)
     ORDER BY a.filing_accession_number
"""
_FACTS_SQL = "SELECT fact_id, xml_id, fact_value FROM fact WHERE accession_id = %s"


def _effective_value(raw: str | None, xml_id: str) -> Decimal | None:
    """Parses Arelle's effective value exactly; a non-finite one is a mismatch."""
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{xml_id}: effective value {raw!r} is not a number") from exc
    if not value.is_finite():
        raise ValueError(f"{xml_id}: effective value {raw!r} is not finite")
    return value


def plan_fact_updates(
    stored: Sequence[Mapping[str, Any]], exported: Mapping[str, Mapping[str, Any]]
) -> list[tuple[int, str, Decimal | None]]:
    """Plans one filing's repairs.

    Args:
        stored: The filing's rows, each with ``fact_id``, ``xml_id`` and ``fact_value``.
        exported: Arelle's facts by ``xml_id``, each with ``value`` and ``effective_value``.

    Returns:
        One ``(fact_id, fact_value, effective_value)`` per stored row holding the marker.

    Raises:
        ValueError: If the export does not match the store, as the spec lists.
    """
    unknown = set(exported) - {row["xml_id"] for row in stored}
    if unknown:
        raise ValueError(f"{len(unknown)} exported fact(s) are not stored: {sorted(unknown)[:5]}")
    updates: list[tuple[int, str, Decimal | None]] = []
    for row in stored:
        xml_id = row["xml_id"]
        item = exported.get(xml_id)
        if item is None:
            raise ValueError(f"{xml_id}: stored but not exported")
        if row["fact_value"] != _MARKER:
            if item["value"] != row["fact_value"]:
                raise ValueError(f"{xml_id}: a fact without the marker has another value")
            continue
        if item["value"] == _MARKER:
            raise ValueError(f"{xml_id}: still the marker, was EDGAR/transform loaded?")
        updates.append(
            (row["fact_id"], item["value"], _effective_value(item["effective_value"], xml_id))
        )
    return updates


def apply_fact_updates(
    cursor: Any, updates: Sequence[tuple[int, str, Decimal | None]]
) -> None:
    """Sends the updates on *cursor*; the caller holds the transaction.

    Args:
        cursor: A database cursor.
        updates: The planned updates.

    Raises:
        RuntimeError: If a statement changes other than one row.
    """
    for fact_id, fact_value, effective_value in updates:
        cursor.execute(_UPDATE_SQL, (fact_value, effective_value, fact_id, _MARKER))
        if cursor.rowcount != 1:
            raise RuntimeError(f"fact {fact_id}: changed {cursor.rowcount} rows, not 1")


def source_path(entry_url: str, root: Path | None) -> Path:
    """The filing's document, moved under *root* when its stored path no longer exists.

    Args:
        entry_url: ``accession.entry_url``, which holds the path at ingest.
        root: The current filings root, ``SEC_EDGAR_ROOT``.

    Returns:
        A path that exists.

    Raises:
        FileNotFoundError: If neither path exists.
    """
    path = Path(entry_url)
    if path.exists():
        return path
    parts = entry_url.replace("\\", "/").split("/")
    if root is not None and "sec_edgar" in parts:
        moved = root.joinpath(*parts[len(parts) - parts[::-1].index("sec_edgar"):])
        if moved.exists():
            return moved
    raise FileNotFoundError(entry_url)


def _export_facts(document: Path, arelle_cmd: str, timeout: float) -> dict[str, Mapping[str, Any]]:
    """Runs Arelle with the transforms over *document*; returns its facts by ``xml_id``."""
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "facts.json"
        connectivity = connectivity_args(os.environ.get("ARELLE_INTERNET_CONNECTIVITY") or None)
        argv = [
            arelle_cmd,
            "--plugins",
            f"EDGAR/transform|{_PLUGIN_PATH}",
            "-f",
            str(document),
            "--transformed-facts-output",
            str(output),
            *connectivity,
        ]
        rc, _, stderr = run_arelle(argv, timeout=timeout, logger=logger, label=document.name)
        if rc != 0 or not output.exists():
            raise RuntimeError(f"Arelle failed on {document.name}: rc={rc} {stderr[-300:]}")
        rows = json.loads(output.read_text(encoding="utf-8"))["rows"]
    return {row["xml_id"]: row for row in rows}


def repair_filings(
    conn: Any,
    *,
    apply: bool,
    accessions: Sequence[str] | None,
    arelle_cmd: str,
    root: Path | None,
    timeout: float,
) -> dict[str, Any]:
    """Plans, and with *apply* writes, each filing that holds the marker.

    One transaction per filing, so a filing that fails leaves the others done.

    Args:
        conn: A database connection.
        apply: Whether to write.
        accessions: Limits the run to these accession numbers.
        arelle_cmd: The Arelle command.
        root: The current filings root.
        timeout: Seconds allowed per filing.

    Returns:
        Counts, and the filings that failed with their reasons.
    """
    with conn.cursor() as cursor:
        cursor.execute(_FILINGS_SQL, (_MARKER,))
        filings = cursor.fetchall()
    conn.rollback()
    if accessions is not None:
        filings = [row for row in filings if row[0] in set(accessions)]
    report: dict[str, Any] = {"apply": apply, "filings": len(filings), "planned": 0,
                              "applied": 0, "failed": {}}
    for accession_no, accession_id, entry_url in filings:
        try:
            exported = _export_facts(source_path(entry_url, root), arelle_cmd, timeout)
            with conn.cursor() as cursor:
                cursor.execute(_FACTS_SQL, (accession_id,))
                stored = [
                    {"fact_id": fact_id, "xml_id": xml_id, "fact_value": fact_value}
                    for fact_id, xml_id, fact_value in cursor.fetchall()
                ]
                updates = plan_fact_updates(stored, exported)
                report["planned"] += len(updates)
                if apply:
                    apply_fact_updates(cursor, updates)
            conn.commit()
            if apply:
                report["applied"] += len(updates)
        except Exception as exc:  # isolation point: one filing's failure is reported, not raised
            conn.rollback()
            logger.error("transform repair failed for %s: %s", accession_no, exc)
            report["failed"][accession_no] = str(exc)[:300]
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the repair; returns 1 if any filing failed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--accession", action="append", help="limit to these accession numbers")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per filing")
    args = parser.parse_args(argv)
    load_dotenv()
    root = os.environ.get("SEC_EDGAR_ROOT")
    conn = connect()
    try:
        report = repair_filings(
            conn,
            apply=args.apply,
            accessions=args.accession,
            arelle_cmd=resolve_arelle_cmd(arelle_cmd_from_env()),
            root=Path(root) if root else None,
            timeout=args.timeout,
        )
    finally:
        conn.close()
    print(json.dumps(report, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
