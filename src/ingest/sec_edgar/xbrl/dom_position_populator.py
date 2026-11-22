"""Populate ``fact_dom_position`` (Phase 5) and ``section_assignment`` (Phase 6).

Implements the fact-anchor (Phase 5) and narrative-section (Phase 6) steps of the
HTML-canonical design (``docs/design/XbrlRag_HTML_Canonical_Source.md`` §4) — the bridge between Arelle's xbrlDB fact
records (loaded by P2) and the HTML DOM byte positions / narrative sections
discovered by :mod:`xbrl_html_dom_parser` (P4).

Tables populated (DDL: ``scripts/ddl/layer_0/``)
-------------------------------------------------------------------------
* ``fact_dom_position`` — DOM byte-range citation anchor for **every**
  iXBRL-tagged fact (financial-statement *and* narrative).  This is the
  universal anchor source: ``(fact_id, accession_no, xml_id, char_start,
  char_end, page_num)``.

* ``section_assignment`` — narrative ``section_path`` for tagged facts
  *outside* the financial statements (MD&A, Risk Factors, footnotes, Notes).
  Financial-statement facts are **deliberately skipped** here — their
  section comes from the XBRL presentation linkbase via
  ``mv_fact_section_assignment`` (richer: e.g. "Assets > Current Assets >
  Cash and Cash Equivalents" instead of just "Consolidated Balance Sheets").

Pipeline position & independence
-------------------------------------------------------------------------
Runs *after* P2 (Arelle xbrlDB load — ``fact.fact_id`` / ``fact.xml_id``
stable) and the HTML canonicalization pre-phase (``canonical.htm`` written).

Phase 6 has **no dependency on Phase 5's writes** — both independently
re-derive ``xml_id -> fact_id`` from the ``fact`` table and byte positions
from ``canonical.htm``.  Either table can therefore be (re)populated on its
own, and every write is an upsert keyed on ``fact_id`` — safe to re-run
(matches the idempotency model in Section 3.16 of the design doc).

``populate_all`` is provided as an efficiency option that parses each
filing's canonical HTML once and writes both tables from the shared result;
it is functionally equivalent to calling the two ``populate_*`` methods
separately.

Usage (CLI)
-------------------------------------------------------------------------
::

    # Both tables, every ingested 10-K / 10-Q / 20-F filing
    python -m src.ingest.sec_edgar.xbrl.dom_position_populator

    # fact_dom_position only
    python -m src.ingest.sec_edgar.xbrl.dom_position_populator --fact-dom-position

    # section_assignment only, limited to specific accessions
    python -m src.ingest.sec_edgar.xbrl.dom_position_populator \\
        --section-assignment --accessions 0001045810-26-000021 0000320193-24-000123

    # Dry run — parse and report without writing
    python -m src.ingest.sec_edgar.xbrl.dom_position_populator --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.ingest.sec_edgar.storage import default_local_root, local_root
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    IXBRLPosition,
    SectionTree,
    build_section_tree,
    extract_ixbrl_positions,
)

logger = logging.getLogger(__name__)

# Syntactically valid EDGAR accession number: 10-digit CIK, 2-digit year,
# 6-digit sequence.  Anything else is a default/placeholder Arelle wrote
# when it couldn't derive the accession number from a local file path
# (fixed post-ingest by Layer0Populator.patch_accession_numbers).
_VALID_ACCESSION_RE = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class FilingTarget:
    """One filing to process — resolved accession row + canonical HTML path."""

    accession_id: int
    accession_no: str
    cik: str
    fiscal_year: int
    form_type: str
    canonical_path: Path


@dataclass
class JoinedFact:
    """One iXBRL position successfully joined to its Arelle ``fact_id``."""

    fact_id: int
    xml_id: str
    char_start: int
    char_end: int
    page_num: int | None


@dataclass
class PhaseStats:
    """Aggregated counters for one phase run."""

    targets_found: int = 0
    targets_processed: int = 0
    targets_skipped_no_facts: int = 0
    facts_extracted: int = 0
    facts_matched: int = 0
    facts_unmatched: int = 0
    rows_written: int = 0
    # section_assignment-only counters (left at 0 for fact_dom_position runs)
    facts_in_financial_statements: int = 0
    facts_outside_sections: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class DOMPositionPopulator:
    """Populate ``fact_dom_position`` (Phase 5) and ``section_assignment`` (Phase 6).

    Parameters
    ----------
    sec_edgar_root:
        Root of the local SEC EDGAR download tree (``data/storage/sec_edgar``).
        Used to compute each filing's path *relative* to the root from
        ``accession.entry_url`` (the local ``.htm`` path Arelle loaded).
    normalized_root:
        Root of the canonical HTML tree written by ``HTMLCanonicalizer``
        (``data/storage/xbrl/normalized``).  ``canonical.htm`` is located by
        mirroring the relative path computed above — exactly the inverse of
        ``HTMLCanonicalizer._output_dir``.
    db_conn_params:
        psycopg2 connection kwargs (``host``, ``port``, ``dbname``, ``user``,
        ``password``).
    """

    DEFAULT_SEC_ROOT = default_local_root("sec_edgar")
    DEFAULT_NORMALIZED_ROOT = default_local_root("normalized")

    def __init__(
        self,
        sec_edgar_root: str | Path = DEFAULT_SEC_ROOT,
        normalized_root: str | Path = DEFAULT_NORMALIZED_ROOT,
        db_conn_params: dict | None = None,
    ) -> None:
        self.sec_edgar_root = Path(sec_edgar_root).expanduser().resolve()
        self.normalized_root = Path(normalized_root).expanduser().resolve()
        self._db_conn_params = db_conn_params or {}
        logger.info(
            "DOMPositionPopulator  sec_edgar_root=%s  normalized_root=%s",
            self.sec_edgar_root,
            self.normalized_root,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        sec_edgar_root: str | Path | None = None,
        normalized_root: str | Path | None = None,
        dotenv_path: str | Path | None = None,
    ) -> DOMPositionPopulator:
        """Construct from environment variables loaded from ``.env``.

        Reads ``XBRL_DB_HOST/PORT/NAME/USER/PASSWORD`` and ``SEC_EDGAR_ROOT``.
        ``normalized_root`` has no dedicated env var — pass it explicitly or
        via ``--normalized-root`` on the CLI to override the fixed default.
        """
        load_dotenv(dotenv_path)
        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        root = sec_edgar_root or local_root("sec_edgar")
        return cls(
            sec_edgar_root=root,
            normalized_root=normalized_root or local_root("normalized"),
            db_conn_params=conn_params,
        )

    # ------------------------------------------------------------------
    # Public API — Phase 5: fact_dom_position
    # ------------------------------------------------------------------

    def populate_fact_dom_position(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> PhaseStats:
        """Populate ``fact_dom_position`` for ALL tagged facts (Phase 5).

        Per filing: extract iXBRL byte positions from ``canonical.htm``,
        resolve ``xml_id -> fact_id`` via the ``fact`` table, and upsert
        ``(fact_id, accession_no, xml_id, char_start, char_end, page_num)``.

        Parameters
        ----------
        accession_nos:
            Limit to specific EDGAR accession numbers
            (e.g. ``["0001045810-26-000021"]``).  ``None`` processes every
            non-superseded filing with a valid accession number and a
            resolvable ``canonical.htm``.
        dry_run:
            Parse and report without writing to the database.

        Returns
        -------
        PhaseStats
        """
        stats = PhaseStats(dry_run=dry_run)
        with psycopg2.connect(**self._db_conn_params) as conn:
            targets = self._discover_targets(conn, accession_nos)
            stats.targets_found = len(targets)
            logger.info(
                "fact_dom_position: %d target filing(s) discovered.", len(targets)
            )

            for target in targets:
                try:
                    self._run_fact_dom_position(conn, target, dry_run, stats)
                except Exception:
                    msg = f"{target.accession_no}: unhandled error"
                    logger.exception(
                        "fact_dom_position failed for %s", target.accession_no
                    )
                    stats.errors.append(msg)

            if not dry_run:
                conn.commit()

        self._log_phase_summary("fact_dom_position", stats)
        return stats

    # ------------------------------------------------------------------
    # Public API — Phase 6: section_assignment
    # ------------------------------------------------------------------

    def populate_section_assignment(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> PhaseStats:
        """Populate ``section_assignment`` for narrative-tagged facts (Phase 6).

        Per filing: build the :class:`SectionTree`, extract iXBRL positions,
        resolve ``xml_id -> fact_id``, binary-search each fact's
        ``char_start`` into the tree, **skip** facts whose containing section
        ``is_financial_statement`` (handled by the presentation linkbase via
        ``mv_fact_section_assignment`` instead), and upsert ``(fact_id,
        section_path)`` for the rest.

        Parameters / Returns: see :meth:`populate_fact_dom_position`.
        """
        stats = PhaseStats(dry_run=dry_run)
        with psycopg2.connect(**self._db_conn_params) as conn:
            targets = self._discover_targets(conn, accession_nos)
            stats.targets_found = len(targets)
            logger.info(
                "section_assignment: %d target filing(s) discovered.", len(targets)
            )

            for target in targets:
                try:
                    self._run_section_assignment(conn, target, dry_run, stats)
                except Exception:
                    msg = f"{target.accession_no}: unhandled error"
                    logger.exception(
                        "section_assignment failed for %s", target.accession_no
                    )
                    stats.errors.append(msg)

            if not dry_run:
                conn.commit()

        self._log_phase_summary("section_assignment", stats)
        return stats

    # ------------------------------------------------------------------
    # Public API — both phases, sharing one canonical-HTML pass per filing
    # ------------------------------------------------------------------

    def populate_all(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> tuple[PhaseStats, PhaseStats]:
        """Run Phase 5 and Phase 6 together, parsing each filing's canonical
        HTML exactly once and writing both tables from the shared result.

        Functionally equivalent to calling :meth:`populate_fact_dom_position`
        followed by :meth:`populate_section_assignment`, but ~2x cheaper on
        the (extract_ixbrl_positions + build_section_tree) cost per filing.

        Returns
        -------
        (fact_dom_position_stats, section_assignment_stats)
        """
        fdp_stats = PhaseStats(dry_run=dry_run)
        sa_stats = PhaseStats(dry_run=dry_run)

        with psycopg2.connect(**self._db_conn_params) as conn:
            targets = self._discover_targets(conn, accession_nos)
            fdp_stats.targets_found = len(targets)
            sa_stats.targets_found = len(targets)
            logger.info("populate_all: %d target filing(s) discovered.", len(targets))

            for target in targets:
                try:
                    self._run_combined(conn, target, dry_run, fdp_stats, sa_stats)
                except Exception:
                    msg = f"{target.accession_no}: unhandled error"
                    logger.exception("populate_all failed for %s", target.accession_no)
                    fdp_stats.errors.append(msg)
                    sa_stats.errors.append(msg)

            if not dry_run:
                conn.commit()

        self._log_phase_summary("fact_dom_position", fdp_stats)
        self._log_phase_summary("section_assignment", sa_stats)
        return fdp_stats, sa_stats

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_targets(
        self,
        conn: psycopg2.extensions.connection,
        accession_nos: list[str] | None,
    ) -> list[FilingTarget]:
        """Resolve filings to process: ``accession`` ⋈ ``filing_registry`` ⋈ canonical.htm.

        Only non-superseded filings with a syntactically valid EDGAR accession
        number (i.e. successfully patched via
        ``Layer0Populator.patch_accession_numbers``) *and* a resolvable
        ``canonical.htm`` are returned.  Filings missing a canonical artifact
        are logged at WARNING with a pointer to run the canonicalizer first.
        """
        sql = f"""
            SELECT a.accession_id, a.filing_accession_number, a.entry_url,
                   fr.cik, fr.fiscal_year, fr.form_type
              FROM accession a
              JOIN filing_registry fr
                ON fr.accession_no = a.filing_accession_number
             WHERE a.filing_accession_number ~ '{_VALID_ACCESSION_RE}'
               AND fr.superseded_by IS NULL
        """
        params: tuple = ()
        if accession_nos:
            sql += " AND a.filing_accession_number = ANY(%s)"
            params = (list(accession_nos),)
        sql += " ORDER BY fr.cik, fr.fiscal_year"

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        targets: list[FilingTarget] = []
        for accession_id, accession_no, entry_url, cik, fiscal_year, form_type in rows:
            canon = self._resolve_canonical_path(entry_url)
            if canon is None or not canon.exists():
                logger.warning(
                    "%s: canonical.htm not found (entry_url=%r -> %s) — skipping. "
                    "Run html_canonicalizer for this filing first.",
                    accession_no,
                    entry_url,
                    canon,
                )
                continue
            targets.append(
                FilingTarget(
                    accession_id=accession_id,
                    accession_no=accession_no,
                    cik=cik,
                    fiscal_year=fiscal_year,
                    form_type=form_type,
                    canonical_path=canon,
                )
            )
        return targets

    def _resolve_canonical_path(self, entry_url: str | None) -> Path | None:
        """Map ``accession.entry_url`` to ``canonical.htm`` by mirroring its
        relative path under ``normalized_root`` — the inverse of
        ``HTMLCanonicalizer._output_dir``.

        Example::

            entry_url     = .../sec_edgar/NVDA/2026/10-K/nvda-20260125.htm
            canonical.htm = .../xbrl/normalized/NVDA/2026/10-K/canonical.htm
        """
        if not entry_url:
            return None
        try:
            rel = Path(entry_url).resolve().relative_to(self.sec_edgar_root)
        except (ValueError, OSError):
            return None
        return self.normalized_root / rel.parent / "canonical.htm"

    # ------------------------------------------------------------------
    # Shared step: join iXBRL positions to Arelle fact_id
    # ------------------------------------------------------------------

    def _load_xml_id_to_fact_id(
        self, conn: psycopg2.extensions.connection, accession_id: int
    ) -> dict[str, int]:
        """Return ``{xml_id: fact_id}`` for all tagged facts in *accession_id*."""
        sql = """
            SELECT xml_id, fact_id
              FROM fact
             WHERE accession_id = %s
               AND xml_id IS NOT NULL
        """
        with conn.cursor() as cur:
            cur.execute(sql, (accession_id,))
            return {xml_id: fact_id for xml_id, fact_id in cur.fetchall()}

    def _join_facts(
        self,
        target: FilingTarget,
        positions: list[IXBRLPosition],
        xml_id_map: dict[str, int],
        stats: PhaseStats,
    ) -> list[JoinedFact]:
        """Resolve each position's ``xml_id`` to a ``fact_id``, updating *stats*
        with matched/unmatched counters exactly once per position.

        A position whose ``xml_id`` has no corresponding ``fact`` row is rare
        but possible (e.g. ``ix:continuation`` fragments are not separate
        facts in Arelle, or GATE-A coverage issues on early iXBRL filings such
        as TSM 2022).  Those are counted and sampled into ``stats.warnings``.
        """
        joined: list[JoinedFact] = []
        unmatched_sample: list[str] = []
        for pos in positions:
            fact_id = xml_id_map.get(pos.xml_id)
            if fact_id is None:
                stats.facts_unmatched += 1
                if len(unmatched_sample) < 5:
                    unmatched_sample.append(pos.xml_id)
                continue
            stats.facts_matched += 1
            joined.append(
                JoinedFact(
                    fact_id=fact_id,
                    xml_id=pos.xml_id,
                    char_start=pos.char_start,
                    char_end=pos.char_end,
                    page_num=pos.page_num,
                )
            )

        if unmatched_sample:
            stats.warnings.append(
                f"{target.accession_no}: {stats.facts_unmatched} xml_id(s) extracted "
                f"from canonical HTML have no matching 'fact' row "
                f"(sample: {unmatched_sample}; total positions={len(positions)})"
            )
        return joined

    # ------------------------------------------------------------------
    # Phase 5: row building + upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _fact_dom_position_rows(
        target: FilingTarget, joined: list[JoinedFact]
    ) -> list[dict]:
        return [
            {
                "fact_id": j.fact_id,
                "accession_no": target.accession_no,
                "xml_id": j.xml_id,
                "char_start": j.char_start,
                "char_end": j.char_end,
                "page_num": j.page_num,
            }
            for j in joined
        ]

    def _upsert_fact_dom_position(
        self, conn: psycopg2.extensions.connection, rows: list[dict]
    ) -> int:
        sql = """
            INSERT INTO fact_dom_position
                (fact_id, accession_no, xml_id, char_start, char_end, page_num)
            VALUES
                (%(fact_id)s, %(accession_no)s, %(xml_id)s,
                 %(char_start)s, %(char_end)s, %(page_num)s)
            ON CONFLICT (fact_id) DO UPDATE SET
                accession_no = EXCLUDED.accession_no,
                xml_id       = EXCLUDED.xml_id,
                char_start   = EXCLUDED.char_start,
                char_end     = EXCLUDED.char_end,
                page_num     = COALESCE(EXCLUDED.page_num, fact_dom_position.page_num)
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    def _run_fact_dom_position(
        self,
        conn: psycopg2.extensions.connection,
        target: FilingTarget,
        dry_run: bool,
        stats: PhaseStats,
    ) -> None:
        positions = extract_ixbrl_positions(target.canonical_path)
        stats.facts_extracted += len(positions)
        if not positions:
            stats.targets_skipped_no_facts += 1
            logger.warning(
                "%s: no iXBRL positions extracted — skipping.", target.accession_no
            )
            return

        xml_id_map = self._load_xml_id_to_fact_id(conn, target.accession_id)
        joined = self._join_facts(target, positions, xml_id_map, stats)
        rows = self._fact_dom_position_rows(target, joined)
        stats.targets_processed += 1

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would upsert %d fact_dom_position row(s) "
                "(%d/%d xml_ids matched).",
                target.accession_no,
                len(rows),
                len(joined),
                len(positions),
            )
            return

        if rows:
            stats.rows_written += self._upsert_fact_dom_position(conn, rows)
        logger.info(
            "%s: upserted %d fact_dom_position row(s).",
            target.accession_no,
            len(rows),
        )

    # ------------------------------------------------------------------
    # Phase 6: row building + upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _section_assignment_rows(
        joined: list[JoinedFact],
        section_tree: SectionTree,
        stats: PhaseStats,
    ) -> list[dict]:
        """Binary-search each fact into the section tree; keep only
        narrative (non-financial-statement) assignments.

        This is the literal implementation of the Phase 6 instruction:
        "For each anchor, binary-search its char_start into the section tree.
        If the containing section is not a financial statement, write
        (fact_id, section_path)."
        """
        rows: list[dict] = []
        for j in joined:
            section_path = section_tree.find_section(j.char_start)
            if section_path is None:
                stats.facts_outside_sections += 1
                continue

            node = section_tree.nodes[section_path]
            if node.is_financial_statement:
                # Section comes from the XBRL presentation linkbase instead
                # (mv_fact_section_assignment) — deliberately not written here;
                # see module docstring / Section 3.8 of the design doc.
                stats.facts_in_financial_statements += 1
                continue

            rows.append(
                {
                    "fact_id": j.fact_id,
                    "section_path": section_path,
                    "assignment_src": "section_tree",
                }
            )
        return rows

    def _upsert_section_assignment(
        self, conn: psycopg2.extensions.connection, rows: list[dict]
    ) -> int:
        sql = """
            INSERT INTO section_assignment (fact_id, section_path, assignment_src)
            VALUES (%(fact_id)s, %(section_path)s, %(assignment_src)s)
            ON CONFLICT (fact_id) DO UPDATE SET
                section_path   = EXCLUDED.section_path,
                assignment_src = EXCLUDED.assignment_src
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    def _run_section_assignment(
        self,
        conn: psycopg2.extensions.connection,
        target: FilingTarget,
        dry_run: bool,
        stats: PhaseStats,
    ) -> None:
        positions = extract_ixbrl_positions(target.canonical_path)
        stats.facts_extracted += len(positions)
        if not positions:
            stats.targets_skipped_no_facts += 1
            logger.warning(
                "%s: no iXBRL positions extracted — skipping.", target.accession_no
            )
            return

        section_tree = build_section_tree(target.canonical_path, target.accession_no)
        xml_id_map = self._load_xml_id_to_fact_id(conn, target.accession_id)
        joined = self._join_facts(target, positions, xml_id_map, stats)
        rows = self._section_assignment_rows(joined, section_tree, stats)
        stats.targets_processed += 1

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would upsert %d section_assignment row(s) "
                "(%d in financial statements skipped, %d outside any section).",
                target.accession_no,
                len(rows),
                stats.facts_in_financial_statements,
                stats.facts_outside_sections,
            )
            return

        if rows:
            stats.rows_written += self._upsert_section_assignment(conn, rows)
        logger.info(
            "%s: upserted %d section_assignment row(s).",
            target.accession_no,
            len(rows),
        )

    # ------------------------------------------------------------------
    # Combined per-filing run (populate_all)
    # ------------------------------------------------------------------

    def _run_combined(
        self,
        conn: psycopg2.extensions.connection,
        target: FilingTarget,
        dry_run: bool,
        fdp_stats: PhaseStats,
        sa_stats: PhaseStats,
    ) -> None:
        positions = extract_ixbrl_positions(target.canonical_path)
        fdp_stats.facts_extracted += len(positions)
        sa_stats.facts_extracted += len(positions)

        if not positions:
            fdp_stats.targets_skipped_no_facts += 1
            sa_stats.targets_skipped_no_facts += 1
            logger.warning(
                "%s: no iXBRL positions extracted — skipping.", target.accession_no
            )
            return

        section_tree = build_section_tree(target.canonical_path, target.accession_no)
        xml_id_map = self._load_xml_id_to_fact_id(conn, target.accession_id)

        # Join once against fact_dom_position's stats (the authoritative pass —
        # its summary carries the "N xml_ids unmatched" warning), then mirror
        # the resulting totals into section_assignment's stats so both phase
        # summaries agree without incrementing facts_matched/unmatched twice.
        joined = self._join_facts(target, positions, xml_id_map, fdp_stats)
        sa_stats.facts_matched += len(joined)
        sa_stats.facts_unmatched += len(positions) - len(joined)

        fdp_rows = self._fact_dom_position_rows(target, joined)
        sa_rows = self._section_assignment_rows(joined, section_tree, sa_stats)

        fdp_stats.targets_processed += 1
        sa_stats.targets_processed += 1

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would upsert %d fact_dom_position + "
                "%d section_assignment row(s).",
                target.accession_no,
                len(fdp_rows),
                len(sa_rows),
            )
            return

        if fdp_rows:
            fdp_stats.rows_written += self._upsert_fact_dom_position(conn, fdp_rows)
        if sa_rows:
            sa_stats.rows_written += self._upsert_section_assignment(conn, sa_rows)

        logger.info(
            "%s: upserted %d fact_dom_position + %d section_assignment row(s).",
            target.accession_no,
            len(fdp_rows),
            len(sa_rows),
        )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_phase_summary(self, phase_name: str, stats: PhaseStats) -> None:
        prefix = "[DRY RUN] " if stats.dry_run else ""
        logger.info(
            "%s%s summary: targets=%d processed=%d skipped_no_facts=%d | "
            "facts: extracted=%d matched=%d unmatched=%d | "
            "rows_written=%d errors=%d",
            prefix,
            phase_name,
            stats.targets_found,
            stats.targets_processed,
            stats.targets_skipped_no_facts,
            stats.facts_extracted,
            stats.facts_matched,
            stats.facts_unmatched,
            stats.rows_written,
            len(stats.errors),
        )
        if phase_name == "section_assignment":
            logger.info(
                "  section_assignment detail: in_financial_statements=%d "
                "(skipped by design — see mv_fact_section_assignment) | "
                "outside_any_section=%d",
                stats.facts_in_financial_statements,
                stats.facts_outside_sections,
            )
        for w in stats.warnings:
            logger.warning("  %s", w)
        for e in stats.errors:
            logger.error("  %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.ingest.sec_edgar.xbrl.dom_position_populator``."""
    p = argparse.ArgumentParser(
        prog="dom_position_populator",
        description=(
            "Populate fact_dom_position (Phase 5) and section_assignment "
            "(Phase 6) from canonical iXBRL HTML + Arelle xbrlDB fact records."
        ),
    )
    p.add_argument(
        "--sec-root",
        default=None,
        metavar="DIR",
        help="Root of local SEC EDGAR storage (overrides SEC_EDGAR_ROOT env var).",
    )
    p.add_argument(
        "--normalized-root",
        default=None,
        metavar="DIR",
        help=f"Root of canonical HTML storage. Default: {DOMPositionPopulator.DEFAULT_NORMALIZED_ROOT}",
    )
    p.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env in current directory).",
    )

    scope = p.add_mutually_exclusive_group()
    scope.add_argument(
        "--fact-dom-position",
        action="store_true",
        help="Populate fact_dom_position only (Phase 5).",
    )
    scope.add_argument(
        "--section-assignment",
        action="store_true",
        help="Populate section_assignment only (Phase 6).",
    )
    # default (neither flag): run populate_all (both, combined pass)

    p.add_argument(
        "--accessions",
        nargs="+",
        default=None,
        metavar="ACCESSION_NO",
        help="Limit to specific EDGAR accession numbers "
        "(e.g. 0001045810-26-000021). Processes all eligible filings if omitted.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing to the database.",
    )
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )

    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    populator = DOMPositionPopulator.from_env(
        sec_edgar_root=args.sec_root,
        normalized_root=args.normalized_root,
        dotenv_path=args.env_file,
    )

    all_errors: list[str] = []

    if args.fact_dom_position:
        stats = populator.populate_fact_dom_position(
            accession_nos=args.accessions, dry_run=args.dry_run
        )
        all_errors.extend(stats.errors)
    elif args.section_assignment:
        stats = populator.populate_section_assignment(
            accession_nos=args.accessions, dry_run=args.dry_run
        )
        all_errors.extend(stats.errors)
    else:
        fdp_stats, sa_stats = populator.populate_all(
            accession_nos=args.accessions, dry_run=args.dry_run
        )
        all_errors.extend(fdp_stats.errors)
        all_errors.extend(sa_stats.errors)

    if all_errors:
        print(f"\n  Total errors ({len(all_errors)}):")
        for e in all_errors:
            print(f"    - {e}")

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
