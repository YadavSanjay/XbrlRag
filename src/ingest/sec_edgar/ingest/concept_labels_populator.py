"""Populate concept_labels and dimension_catalog.member_label via the Arelle CLI.

Classes
-------
ConceptLabelsPopulator
    Exports concept-to-label arcs → inserts into concept_labels table.

DimensionMembersPopulator
    Exports dimension axis+member pairs with labels → patches member_label in
    dimension_catalog where NULL (after the SQL INSERT that leaves it empty
    because the Arelle plugin drops label arcs from the public-schema DB).


Flow per accession
------------------
1. Query accession table for entry_url and filing_accession_number.
2. Resolve the filer CIK from filing_registry (via accession_no).
3. Invoke arelleCmdLine.exe with the export_concept_labels plugin, passing
   --concept-labels-output <tmp_json> --concept-labels-cik <cik>.
4. Read the JSON output.
5. Upsert rows into concept_labels (ON CONFLICT DO UPDATE).

Why two steps instead of one?
    The frozen Arelle executable (arelleCmdLine.exe) runs Python 3.14 — a
    different runtime than this project's Python environment.  The plugin
    (arelle_plugins/export_concept_labels.py) runs inside Arelle, using only
    the standard library (json) and Arelle itself.  This file runs in our
    normal Python environment and handles the database connection with psycopg2.

Design notes:
    - concept format:  full namespaceURI:localName  (matches xbrlDB qname table)
    - cik_override:    NULL for standard taxonomies; 10-digit CIK for extensions
    - ON CONFLICT target:  the functional unique index
        concept_labels_uq (concept, COALESCE(cik_override,''), xml_lang, label_role)

Connection parameters read from .env:
    XBRL_DB_HOST, XBRL_DB_PORT, XBRL_DB_NAME, XBRL_DB_USER, XBRL_DB_PASSWORD
    ARELLE_CMD   (default: the frozen build on Windows, `arelleCmdLine` on PATH
                  elsewhere — see src/ingest/util/arelle_cmd.py)
    SEC_EDGAR_ROOT
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.ingest.util.arelle_cmd import (
    arelle_cmd_from_env,
    default_arelle_cmd,
    resolve_arelle_cmd,
)
from src.ingest.util.arelle_run import connectivity_args, run_arelle


def _arelle_run_settings() -> dict:
    """Per-invocation Arelle knobs from env, shared by both populators.

    These must honour the same env vars as the fact ingestor. They previously did
    not: `arelle_timeout` was a hardcoded 600 and there was no offline switch, so
    a label export could stall on an unreachable schema host even when the fact
    ingest had been told to resolve from cache only.
    """
    return {
        "arelle_timeout": int(os.environ.get("ARELLE_TIMEOUT", "600")),
        "heartbeat_secs": float(os.environ.get("ARELLE_HEARTBEAT_SECS", "60")),
        "internet_connectivity": os.environ.get("ARELLE_INTERNET_CONNECTIVITY") or None,
    }


logger = logging.getLogger(__name__)

# Path to the Arelle plugin file (sibling directory)
_PLUGIN_PATH = Path(__file__).parent / "arelle_plugins" / "export_concept_labels.py"


class ConceptLabelsPopulator:
    """Populate concept_labels from XBRL filings via the Arelle CLI plugin.

    Parameters
    ----------
    arelle_cmd:
        Absolute path to arelleCmdLine.exe (or arelleCmdLine on Linux/Mac).
    db_conn_params:
        psycopg2 connection kwargs.
    arelle_timeout:
        Seconds to wait for each Arelle invocation before killing it.
    """

    DEFAULT_ARELLE_CMD = default_arelle_cmd()
    DEFAULT_ARELLE_TIMEOUT = 600  # 10 min; large filings + full DTS load can be slow

    def __init__(
        self,
        arelle_cmd: str = DEFAULT_ARELLE_CMD,
        db_conn_params: dict | None = None,
        arelle_timeout: int = DEFAULT_ARELLE_TIMEOUT,
        heartbeat_secs: float = 60.0,
        internet_connectivity: str | None = None,
    ) -> None:
        self.arelle_cmd = arelle_cmd
        self._db_conn_params = db_conn_params or {}
        self.arelle_timeout = arelle_timeout
        self.heartbeat_secs = max(0.0, float(heartbeat_secs))
        self.internet_connectivity = internet_connectivity or None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        dotenv_path: str | Path | None = None,
    ) -> ConceptLabelsPopulator:
        """Construct from environment variables (loaded from .env)."""
        load_dotenv(dotenv_path)
        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        arelle_cmd = arelle_cmd_from_env()
        return cls(
            arelle_cmd=arelle_cmd,
            db_conn_params=conn_params,
            **_arelle_run_settings(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Export concept labels for one or more accessions.

        Parameters
        ----------
        accession_nos:
            List of EDGAR accession numbers to process
            (e.g. ["0001045810-26-000021"]).
            If None, processes ALL accessions in the accession table that
            have a valid filing_accession_number (EDGAR format).
        dry_run:
            If True, run Arelle and parse JSON but do not write to the DB.

        Returns
        -------
        dict with keys:
            accessions_found, arelle_ok, arelle_failed,
            rows_exported, rows_written, dry_run, errors, warnings
        """
        stats: dict = {
            "accessions_found": 0,
            "arelle_ok": 0,
            "arelle_failed": 0,
            "rows_exported": 0,
            "rows_written": 0,
            "dry_run": dry_run,
            "errors": [],
            "warnings": [],
        }

        self._validate_arelle()
        self._validate_plugin()

        with psycopg2.connect(**self._db_conn_params) as conn:
            accession_rows = self._fetch_accession_rows(conn, accession_nos)
            stats["accessions_found"] = len(accession_rows)

            if not accession_rows:
                logger.warning(
                    "No accession rows found%s.",
                    f" for {accession_nos}" if accession_nos else "",
                )
                return stats

            for row in accession_rows:
                self._process_one(conn, row, dry_run, stats)

            if not dry_run:
                conn.commit()

        logger.info(
            "concept_labels populate done: accessions=%d  arelle_ok=%d  "
            "arelle_failed=%d  rows_exported=%d  rows_written=%d",
            stats["accessions_found"],
            stats["arelle_ok"],
            stats["arelle_failed"],
            stats["rows_exported"],
            stats["rows_written"],
        )
        return stats

    # ------------------------------------------------------------------
    # Internal: per-accession processing
    # ------------------------------------------------------------------

    def _process_one(
        self,
        conn: psycopg2.extensions.connection,
        row: dict,
        dry_run: bool,
        stats: dict,
    ) -> None:
        accession_no = row["accession_no"]
        entry_url = row["entry_url"]
        cik = row["cik"]
        ticker = row.get("ticker", "?")

        logger.info(
            "Processing %s (accno=%s, cik=%s) from %s",
            ticker,
            accession_no,
            cik,
            Path(entry_url).name,
        )

        # ── Run Arelle ────────────────────────────────────────────────
        json_path, arelle_stderr = self._run_arelle(entry_url, cik, accession_no)

        if json_path is None:
            msg = (
                f"{ticker} {accession_no}: Arelle invocation failed. "
                f"stderr: {arelle_stderr[:500]}"
            )
            logger.error(msg)
            stats["arelle_failed"] += 1
            stats["errors"].append(msg)
            return

        # ── Read JSON output ──────────────────────────────────────────
        try:
            payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
        except Exception as exc:
            msg = f"{ticker} {accession_no}: Failed to read Arelle output: {exc}"
            logger.error(msg)
            stats["arelle_failed"] += 1
            stats["errors"].append(msg)
            return
        finally:
            # Clean up temp file
            try:
                Path(json_path).unlink(missing_ok=True)
            except Exception:
                pass

        rows = payload.get("rows", [])
        stats["arelle_ok"] += 1
        stats["rows_exported"] += len(rows)

        logger.info(
            "  Arelle exported %d label rows for %s (source=%s)",
            len(rows),
            ticker,
            Path(payload.get("source_file", "?")).name,
        )

        # Warn if there is a .error sidecar file from the plugin
        error_sidecar = json_path + ".error"
        if Path(error_sidecar).exists():
            err_text = Path(error_sidecar).read_text(encoding="utf-8")
            msg = f"{ticker} {accession_no}: plugin error sidecar: {err_text[:300]}"
            logger.warning(msg)
            stats["warnings"].append(msg)
            try:
                Path(error_sidecar).unlink(missing_ok=True)
            except Exception:
                pass

        if not rows:
            msg = (
                f"{ticker} {accession_no}: Arelle returned 0 label rows. "
                "Check that the _lab.xml linkbase is present and the file loaded correctly."
            )
            logger.warning(msg)
            stats["warnings"].append(msg)
            return

        if dry_run:
            logger.info(
                "  dry_run=True — skipping DB write for %s (%d rows).",
                ticker,
                len(rows),
            )
            return

        # ── Upsert into concept_labels ────────────────────────────────
        written = self._upsert_concept_labels(conn, rows)
        stats["rows_written"] += written
        logger.info(
            "  Upserted %d rows into concept_labels for %s.",
            written,
            ticker,
        )

    # ------------------------------------------------------------------
    # Arelle invocation
    # ------------------------------------------------------------------

    def _run_arelle(
        self,
        entry_url: str,
        cik: str,
        accession_no: str,
    ) -> tuple[str | None, str]:
        """Run arelleCmdLine with the export plugin.

        Returns (json_output_path, stderr_text).
        json_output_path is None if Arelle failed or produced no output.
        """
        # Use a named temp file so Arelle can write to it
        safe_accno = accession_no.replace("/", "_").replace("-", "_")
        tmp_dir = Path(tempfile.gettempdir())
        json_path = str(tmp_dir / f"concept_labels_{safe_accno}.json")

        # Remove any leftover from a previous failed run
        try:
            Path(json_path).unlink(missing_ok=True)
        except Exception:
            pass

        cmd = [
            self.arelle_cmd,
            "-f",
            entry_url,
            "--plugin",
            str(_PLUGIN_PATH),
            "--concept-labels-output",
            json_path,
            "--concept-labels-cik",
            cik,
            "--logFile",
            "logToStdErr",  # keep stdout clean for our output JSON
        ]

        logger.debug("Arelle cmd: %s", " ".join(cmd))

        # Shared streaming runner: this exporter used to buffer output and discard
        # it on timeout, so a stall here was as opaque as the fact-ingest one was.
        cmd += connectivity_args(self.internet_connectivity)
        rc, _stdout, stderr = run_arelle(
            cmd,
            timeout=self.arelle_timeout,
            logger=logger,
            label=Path(entry_url).name,
            heartbeat_secs=self.heartbeat_secs,
        )
        if rc != 0:
            return None, stderr

        if not Path(json_path).exists():
            # Arelle returned 0 but plugin didn't write output — something silently skipped
            return None, (
                f"Arelle exited 0 but output file not written: {json_path}. "
                f"stderr: {stderr[:400]}"
            )

        return json_path, stderr

    # ------------------------------------------------------------------
    # Database operations
    # ------------------------------------------------------------------

    def _fetch_accession_rows(
        self,
        conn: psycopg2.extensions.connection,
        accession_nos: list[str] | None,
    ) -> list[dict]:
        """Return accession rows with entry_url and resolved CIK.

        Joins accession → filing_registry (via filing_accession_number = accession_no)
        to get the CIK.  Only rows with a valid EDGAR-format accession number are
        returned — rows with timestamp defaults are skipped with a warning.
        """
        where_clause = ""
        params: list = []

        if accession_nos:
            placeholders = ",".join(["%s"] * len(accession_nos))
            where_clause = f"AND a.filing_accession_number IN ({placeholders})"
            params.extend(accession_nos)

        sql = f"""
            SELECT
                a.accession_id,
                a.filing_accession_number  AS accession_no,
                a.entry_url,
                fr.cik,
                fc.ticker
            FROM accession a
            JOIN filing_registry fr
              ON fr.accession_no = a.filing_accession_number
            LEFT JOIN fiscal_calendar fc
              ON fc.cik = fr.cik
             AND fc.fiscal_year  = fr.fiscal_year
             AND fc.fiscal_period = fr.fiscal_period
            WHERE a.entry_url IS NOT NULL
              AND a.filing_accession_number ~ '^[0-9]{{10}}-[0-9]{{2}}-[0-9]{{6}}$'
              {where_clause}
            ORDER BY a.accession_id
        """
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

        # If caller specified accession_nos, warn about any that weren't found
        if accession_nos:
            found = {r["accession_no"] for r in rows}
            missing = [a for a in accession_nos if a not in found]
            for m in missing:
                logger.warning(
                    "Accession %r not found in DB (may need patch_accession_numbers first).",
                    m,
                )

        return rows

    def _upsert_concept_labels(
        self,
        conn: psycopg2.extensions.connection,
        rows: list[dict],
    ) -> int:
        """Upsert concept_labels rows.

        ON CONFLICT targets the functional unique index:
            concept_labels_uq (concept, COALESCE(cik_override,''), xml_lang, label_role)
        On conflict, label text is refreshed (later/newer label wins).
        """
        sql = """
            INSERT INTO concept_labels
                (concept, cik_override, xml_lang, label_role, label)
            VALUES
                (%(concept)s, %(cik_override)s, %(xml_lang)s, %(label_role)s, %(label)s)
            ON CONFLICT (concept, (COALESCE(cik_override, '')), xml_lang, label_role)
            DO UPDATE SET label = EXCLUDED.label
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_arelle(self) -> None:
        # Normalise to a resolved path (PATH lookup for a bare name), matching how
        # arelle_xbrl_ingestor runs the command — a bare ARELLE_CMD must not fail
        # here while working there. Raises FileNotFoundError if truly absent.
        self.arelle_cmd = resolve_arelle_cmd(self.arelle_cmd)

    def _validate_plugin(self) -> None:
        if not _PLUGIN_PATH.exists():
            raise FileNotFoundError(
                f"Arelle plugin not found: {_PLUGIN_PATH}. "
                "Expected at src/ingest/sec_edgar/ingest/arelle_plugins/export_concept_labels.py"
            )


# ---------------------------------------------------------------------------
# DimensionMembersPopulator
# ---------------------------------------------------------------------------


class DimensionMembersPopulator:
    """Patch dimension_catalog.member_label using the Arelle CLI plugin.

    Background
    ----------
    After the SQL INSERT that populates dimension_catalog from context_dimension,
    member_label is always NULL because the Arelle public-schema plugin drops
    every concept-to-label arc (relationship.to_resource_id = NULL after load).

    This class invokes arelleCmdLine with --dimension-members-output, which
    walks all XBRL contexts for explicit axis+member pairs and resolves their
    English labels from the in-memory conceptLabel arc set.  The JSON output
    is then used to UPDATE dimension_catalog.member_label where NULL.

    The --concept-labels-output and --dimension-members-output options can be
    combined in a single Arelle invocation (they share the label-arc traversal
    via an internal cache), so you can run ConceptLabelsPopulator and
    DimensionMembersPopulator together by setting both output paths in one call.
    """

    DEFAULT_ARELLE_CMD = ConceptLabelsPopulator.DEFAULT_ARELLE_CMD
    DEFAULT_ARELLE_TIMEOUT = ConceptLabelsPopulator.DEFAULT_ARELLE_TIMEOUT

    def __init__(
        self,
        arelle_cmd: str = DEFAULT_ARELLE_CMD,
        db_conn_params: dict | None = None,
        arelle_timeout: int = DEFAULT_ARELLE_TIMEOUT,
        heartbeat_secs: float = 60.0,
        internet_connectivity: str | None = None,
    ) -> None:
        self.arelle_cmd = arelle_cmd
        self._db_conn_params = db_conn_params or {}
        self.arelle_timeout = arelle_timeout
        self.heartbeat_secs = max(0.0, float(heartbeat_secs))
        self.internet_connectivity = internet_connectivity or None

    @classmethod
    def from_env(
        cls, dotenv_path: str | Path | None = None
    ) -> DimensionMembersPopulator:
        load_dotenv(dotenv_path)
        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        arelle_cmd = arelle_cmd_from_env()
        return cls(
            arelle_cmd=arelle_cmd,
            db_conn_params=conn_params,
            **_arelle_run_settings(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Export dimension member labels via Arelle and patch dimension_catalog.

        Parameters
        ----------
        accession_nos:
            EDGAR accession numbers to process.  None = all loaded accessions.
        dry_run:
            Parse and log without writing to DB.

        Returns
        -------
        dict with keys:
            accessions_found, arelle_ok, arelle_failed,
            pairs_exported, rows_updated, dry_run, errors, warnings
        """
        stats: dict = {
            "accessions_found": 0,
            "arelle_ok": 0,
            "arelle_failed": 0,
            "pairs_exported": 0,
            "rows_updated": 0,
            "dry_run": dry_run,
            "errors": [],
            "warnings": [],
        }

        # PATH-aware resolution (see src/ingest/util/arelle_cmd.py): a bare ARELLE_CMD such as
        # the container's `arelleCmdLine` must resolve here as it does in the ingestor.
        self.arelle_cmd = resolve_arelle_cmd(self.arelle_cmd)
        if not _PLUGIN_PATH.exists():
            raise FileNotFoundError(f"Arelle plugin not found: {_PLUGIN_PATH}")

        with psycopg2.connect(**self._db_conn_params) as conn:
            # Re-use the same accession-row fetch as ConceptLabelsPopulator
            accession_rows = ConceptLabelsPopulator(
                arelle_cmd=self.arelle_cmd,
                db_conn_params=self._db_conn_params,
            )._fetch_accession_rows(conn, accession_nos)

            stats["accessions_found"] = len(accession_rows)

            if not accession_rows:
                logger.warning("No accession rows found.")
                return stats

            for row in accession_rows:
                self._process_one(conn, row, dry_run, stats)

            if not dry_run:
                conn.commit()

        logger.info(
            "dimension_catalog member-label patch done: accessions=%d  "
            "arelle_ok=%d  arelle_failed=%d  pairs_exported=%d  rows_updated=%d",
            stats["accessions_found"],
            stats["arelle_ok"],
            stats["arelle_failed"],
            stats["pairs_exported"],
            stats["rows_updated"],
        )
        return stats

    # ------------------------------------------------------------------
    # Internal: per-accession processing
    # ------------------------------------------------------------------

    def _process_one(
        self,
        conn: psycopg2.extensions.connection,
        row: dict,
        dry_run: bool,
        stats: dict,
    ) -> None:
        accession_no = row["accession_no"]
        entry_url = row["entry_url"]
        ticker = row.get("ticker", "?")

        logger.info(
            "Processing %s (accno=%s) for dimension member labels from %s",
            ticker,
            accession_no,
            Path(entry_url).name,
        )

        json_path, stderr = self._run_arelle(entry_url, accession_no)

        if json_path is None:
            msg = (
                f"{ticker} {accession_no}: Arelle failed for dimension members. "
                f"stderr: {stderr[:500]}"
            )
            logger.error(msg)
            stats["arelle_failed"] += 1
            stats["errors"].append(msg)
            return

        try:
            payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
        except Exception as exc:
            msg = f"{ticker} {accession_no}: Failed to read Arelle output: {exc}"
            logger.error(msg)
            stats["arelle_failed"] += 1
            stats["errors"].append(msg)
            return
        finally:
            try:
                Path(json_path).unlink(missing_ok=True)
            except Exception:
                pass

        pairs = payload.get("rows", [])
        stats["arelle_ok"] += 1
        stats["pairs_exported"] += len(pairs)

        # Count how many have a non-null member_label (useful for diagnostic)
        with_label = sum(1 for p in pairs if p.get("member_label"))
        without_label = len(pairs) - with_label
        logger.info(
            "  Arelle exported %d axis+member pairs for %s "
            "(%d with label, %d without label)",
            len(pairs),
            ticker,
            with_label,
            without_label,
        )

        if without_label:
            logger.warning(
                "  %s: %d dimension members have no English label in the DTS "
                "(likely typed members or abstract members).",
                ticker,
                without_label,
            )
            stats["warnings"].append(
                f"{ticker} {accession_no}: {without_label} members have no label"
            )

        # Warn on .error sidecar
        error_sidecar = json_path + ".error"
        if Path(error_sidecar).exists():
            err_text = Path(error_sidecar).read_text(encoding="utf-8")
            msg = f"{ticker} {accession_no}: plugin error sidecar: {err_text[:300]}"
            logger.warning(msg)
            stats["warnings"].append(msg)
            try:
                Path(error_sidecar).unlink(missing_ok=True)
            except Exception:
                pass

        if not pairs or dry_run:
            if dry_run:
                logger.info("  dry_run=True — skipping DB update for %s.", ticker)
            return

        updated = self._update_member_labels(conn, pairs)
        stats["rows_updated"] += updated
        logger.info(
            "  Updated %d dimension_catalog.member_label rows for %s.",
            updated,
            ticker,
        )

    # ------------------------------------------------------------------
    # Arelle invocation
    # ------------------------------------------------------------------

    def _run_arelle(
        self, entry_url: str, accession_no: str
    ) -> tuple[str | None, str]:
        """Run arelleCmdLine with --dimension-members-output."""
        safe_accno = accession_no.replace("/", "_").replace("-", "_")
        json_path = str(Path(tempfile.gettempdir()) / f"dim_members_{safe_accno}.json")

        try:
            Path(json_path).unlink(missing_ok=True)
        except Exception:
            pass

        cmd = [
            self.arelle_cmd,
            "-f",
            entry_url,
            "--plugin",
            str(_PLUGIN_PATH),
            "--dimension-members-output",
            json_path,
            "--logFile",
            "logToStdErr",
        ]

        logger.debug("Arelle cmd: %s", " ".join(cmd))

        # Shared streaming runner: this exporter used to buffer output and discard
        # it on timeout, so a stall here was as opaque as the fact-ingest one was.
        cmd += connectivity_args(self.internet_connectivity)
        rc, _stdout, stderr = run_arelle(
            cmd,
            timeout=self.arelle_timeout,
            logger=logger,
            label=Path(entry_url).name,
            heartbeat_secs=self.heartbeat_secs,
        )
        if rc != 0:
            return None, stderr

        if not Path(json_path).exists():
            return None, (
                f"Arelle exited 0 but output file not written. "
                f"stderr: {stderr[:400]}"
            )

        return json_path, stderr

    # ------------------------------------------------------------------
    # Database update
    # ------------------------------------------------------------------

    def _update_member_labels(
        self,
        conn: psycopg2.extensions.connection,
        pairs: list[dict],
    ) -> int:
        """UPDATE dimension_catalog.member_label where currently NULL.

        Matching is on member_qname alone (namespaceURI:localName format).
        Standard members (e.g. srt:UnitedStatesMember) appear across many
        filers under the same member_qname, so this UPDATE correctly patches
        all filers' rows in one pass.  Extension members encode the filer's
        namespace URI in the QName, so they naturally only match the issuing
        filer's rows.

        Only rows with member_label IS NULL are updated — manually-set or
        previously-patched labels are preserved.
        """
        # Only push pairs that actually have a label — no point updating with NULL
        labelled = [p for p in pairs if p.get("member_label")]
        if not labelled:
            return 0

        sql = """
            UPDATE dimension_catalog
               SET member_label = %(member_label)s
             WHERE member_qname  = %(member_qname)s
               AND member_label IS NULL
        """
        params = [
            {"member_qname": p["member_qname"], "member_label": p["member_label"]}
            for p in labelled
        ]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, params, page_size=500)
            # rowcount is unreliable for execute_batch; count via a follow-up query
            cur.execute(
                "SELECT COUNT(*) FROM dimension_catalog WHERE member_label IS NOT NULL"
            )
        return len(params)  # upper bound; actual updated = rows that were NULL before
