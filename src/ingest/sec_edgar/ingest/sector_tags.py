# Fill ``entity_master.gics_sector`` and ``industry`` from the committed sector tags.
#
# An idempotent step run after an ingest, separately from the global ``layer0-populate``
# (which rewrites identity). It updates only rows whose stored pair differs from the tags,
# clears the pair of a row the tags no longer name, and never inserts a row.
#
# Run (after the ``layer0`` schema step has added the two columns):
#     conda run -n py312 python -m src.ingest.sec_edgar.ingest.sector_tags [--dry-run]
#
# Spec: [docs/specs/sector-tags.md](../../../../docs/specs/sector-tags.md).

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATEMENT_TIMEOUT_MS = 60_000


def plan_sector_tag_updates(
    tags: dict[str, Any], rows: list[dict[str, Any]]
) -> list[tuple[str, str | None, str | None]]:
    """Plan the ``entity_master`` updates that bring the stored tags in line with the file.

    Args:
        tags: As returned by ``src.retriever.xbrl.entity_sectors.load_sector_tags``.
        rows: The stored ``{"cik", "gics_sector", "industry"}`` rows.

    Returns:
        ``(cik, gics_sector, industry)`` per row to change, sorted by CIK.
    """
    filers = tags["filers"]
    plan: list[tuple[str, str | None, str | None]] = []
    for row in rows:
        cik = row["cik"]
        tag = filers.get(cik)
        want = (tag["gics_sector"], tag["industry"]) if tag else (None, None)
        if (row.get("gics_sector"), row.get("industry")) != want:
            plan.append((cik, *want))
    plan.sort(key=lambda update: update[0])
    return plan


def apply_sector_tags(conn: Any, tags: dict[str, Any], dry_run: bool = False
                      ) -> list[tuple[str, str | None, str | None]]:
    """Read ``entity_master``, plan the tag updates and apply them in one transaction.

    Args:
        conn: An open psycopg2 connection (not autocommit).
        tags: As returned by ``load_sector_tags``.
        dry_run: Plan only; write nothing.

    Returns:
        The planned updates, as :func:`plan_sector_tag_updates` returns them.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}")
            cur.execute(
                "SELECT cik, gics_sector, industry FROM entity_master ORDER BY cik FOR UPDATE")
            rows = [{"cik": cik, "gics_sector": sector, "industry": industry}
                    for cik, sector, industry in cur.fetchall()]
            plan = plan_sector_tag_updates(tags, rows)
            if not dry_run:
                for cik, sector, industry in plan:
                    cur.execute(
                        "UPDATE entity_master SET gics_sector = %s, industry = %s "
                        "WHERE cik = %s",
                        (sector, industry, cik),
                    )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return plan


def main(argv: list[str] | None = None) -> int:
    """Tag ``entity_master`` from ``configs/entities/sectors.yaml`` and log each change.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(
        description="Fill entity_master.gics_sector/industry from the committed sector tags.")
    parser.add_argument("--sectors", type=Path, default=None,
                        help="sectors YAML (default: configs/entities/sectors.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes, write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from src.db.conn import connect
    from src.retriever.xbrl.entity_sectors import load_sector_tags

    tags = load_sector_tags(args.sectors)
    conn = connect()
    try:
        plan = apply_sector_tags(conn, tags, dry_run=args.dry_run)
    finally:
        conn.close()
    verb = "would set" if args.dry_run else "set"
    for cik, sector, industry in plan:
        logger.info("%s %s: gics_sector=%r industry=%r", verb, cik, sector, industry)
    logger.info("sector tags (as of %s): %d row(s) %s", tags["as_of"], len(plan), verb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
