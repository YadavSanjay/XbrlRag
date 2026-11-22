"""Ingest SEC EDGAR iXBRL filings into xbrl_db via arelleCmdLine.

Each filing lives in a directory with a manifest.yaml that identifies the
primary .htm document (iXBRL).  The Arelle xbrlDB plugin is invoked once per
filing, on the primary document or, for a filing whose inline XBRL spans several
documents, on the extracted instance (``arelle_entry_name``); duplicate detection
queries ``accession.entry_url`` so the CLI is never called twice for the same
document.

Directory layout expected::

    {sec_root}/{TICKER}/{YEAR}/{FORM_TYPE}/manifest.yaml
    {sec_root}/{TICKER}/{YEAR}/{FORM_TYPE}/{primary_document}.htm

Typical usage::

    ingestor = ArelleXBRLIngestor.from_env()

    # Ingest everything not yet in xbrl_db
    stats = ingestor.ingest_all()

    # Only one ticker
    stats = ingestor.ingest_ticker("NVDA")

    # One specific filing directory
    stats = ingestor.ingest_filing(Path("data/storage/sec_edgar/NVDA/2026/10-K"))

    # Dry-run: discover + duplicate-check but do NOT invoke arelleCmdLine
    stats = ingestor.ingest_all(dry_run=True)

Environment variables (all optional, defaults shown)::

    ARELLE_CMD         = the frozen build on Windows, else `arelleCmdLine` on PATH
                         (see src/ingest/util/arelle_cmd.py)
    SEC_EDGAR_ROOT     = <repo>\\data\\storage\\sec_edgar
    XBRL_DB_HOST       = localhost
    XBRL_DB_PORT       = 5432
    XBRL_DB_NAME       = xbrl_db
    XBRL_DB_USER       = postgres
    XBRL_DB_PASSWORD   = postgres
    XBRL_DB_TIMEOUT    = 20
    ARELLE_TIMEOUT     = 600     (per-filing wall clock, seconds)
    ARELLE_MAX_ATTEMPTS= 2       (1 disables the timeout retry)
    ARELLE_RETRY_BACKOFF = 10
    ARELLE_HEARTBEAT_SECS = 60   (0 silences the "still running" heartbeat)
    ARELLE_INTERNET_CONNECTIVITY = (unset) | offline | online

Arelle needs a **warm web cache**.  Resolving a filing's DTS pulls the whole
us-gaap/dei/srt/country taxonomy plus the XBRL 2.1 core schemas from the
internet; on a cold cache that dominates the run and, if any host is
unreachable, stalls until the timeout.  The cache lives in Arelle's user app
dir (Linux ``~/.config/arelle/cache``, Windows ``%LOCALAPPDATA%\\Arelle\\cache``)
and MUST be on a persistent volume when Arelle runs in a throwaway container —
see ``docs/guides/xbrl-flow/04-ingest-and-populate.md`` §4.13.2.  Set
``ARELLE_INTERNET_CONNECTIVITY=offline`` to force cache-only resolution, which
turns a silent stall into an immediate, legible failure.
"""

from __future__ import annotations

import collections.abc
import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import psycopg2
import yaml

from src.ingest.sec_edgar.document_set import (
    DocumentSetMismatchError,
    arelle_entry_name,
    assert_document_set_complete,
    inline_document_names,
    instance_name,
)
from src.ingest.sec_edgar.storage import FilingStore, LocalFilingStore, local_root
from src.ingest.util.arelle_cmd import arelle_cmd_from_env
from src.ingest.util.arelle_run import (
    REDACTED,
    connectivity_args,
    mask_secret,
    run_arelle,
)
from src.ingest.util.retry import backoff_seconds
from src.observability.ingestion_audit import (
    classify_arelle_failure,
    record_ingestion_run,
)

logger = logging.getLogger(__name__)

# EDGAR accession number format: NNNNNNNNNN-YY-NNNNNN
_EDGAR_ACCNO_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")

# Re-exported so existing callers/tests keep importing these from here; the
# implementations live in src/ingest/util/arelle_run.py, shared with the label
# and dimension-member exporters.
_REDACTED = REDACTED
_mask_secret = mask_secret


def _arelle_conn_params() -> dict:
    """XBRL_DB_* connection params via the shared helper (adds TLS policy)."""
    from src.db.conn import conn_params

    return conn_params()


def _run_status(succeeded: int, failed: int) -> str:
    """Roll a run's per-filing outcomes up to OK / PARTIAL / ERROR (skips ignored)."""
    if failed == 0:
        return "OK"
    if succeeded > 0:
        return "PARTIAL"
    return "ERROR"


# Supported iXBRL primary document extensions
_IXBRL_EXTENSIONS = frozenset({".htm", ".html"})

# Filing form types we will process
_SUPPORTED_FORM_TYPES = frozenset(
    {"10-K", "10-Q", "20-F", "10-K/A", "20-F/A", "10-Q/A"}
)


def _normalise_entry_path(value: str) -> str:
    """Drop a ``file:`` scheme and slashes before a drive; ``/`` separators; lowercase."""
    s = re.sub(r"^file:/*", "", value.strip(), flags=re.IGNORECASE)
    return s.replace("\\", "/").lower()


def _entry_identity(value: str) -> str:
    """Normalise an entry path, reading an extracted instance as its primary document."""
    s = _normalise_entry_path(value)
    return s.removesuffix("_htm.xml") + ".htm" if s.endswith("_htm.xml") else s


def is_accession_number(value: str | None) -> bool:
    """Whether *value* is an EDGAR accession number (``##########-##-######``).

    Args:
        value: A ``filing_accession_number`` as stored; Arelle writes a placeholder
            until the patch step runs.

    Returns:
        True for a well-formed accession number, after stripping whitespace.
    """
    if not isinstance(value, str):
        return False
    return _EDGAR_ACCNO_RE.match(value.strip()) is not None


def already_ingested(
    accession_no: str | None,
    rows: collections.abc.Sequence[tuple[str | None, str | None]],
) -> bool:
    """Whether the filing is already in Arelle's ``accession`` table.

    A stem match identifies the filing only through its accession number: an
    amendment can reuse its original's file name
    (docs/specs/ingest-accession-identity.md).

    Args:
        accession_no: The manifest's accession number, if any.
        rows: ``(entry_url, filing_accession_number)`` of the rows whose
            ``entry_url`` matched the filing's file-name stem.

    Returns:
        True when a row carries this accession number or is not yet patched.
    """
    wanted = accession_no.strip() if accession_no else None
    for _, stored in rows:
        if wanted is None:
            return True  # no accession to compare: any stem match counts, as before
        if not is_accession_number(stored):
            return True  # not yet patched: may be this filing
        if stored.strip() == wanted:
            return True
    return False


def same_entry_path(entry_url: str | None, path: str | None) -> bool:
    """Whether an ``accession.entry_url`` names the same file as *path*.

    Args:
        entry_url: A raw path or ``file:`` URI as Arelle stored it.
        path: A local file path.

    Returns:
        True when both name the same file (separators, scheme and case normalised). A
        filing's ``<stem>_htm.xml`` and ``<stem>.htm`` in one directory count as the same
        file: a multi-document filing's entry is its instance, and the manifest names its
        primary document.
    """
    if not entry_url or not path or not entry_url.strip() or not path.strip():
        return False
    return _entry_identity(entry_url) == _entry_identity(path)


def pick_accession_row(
    accession_no: str,
    path: str | None,
    rows: collections.abc.Sequence[tuple[int, str | None, str | None]],
) -> int | None:
    """Choose the ``accession`` row a manifest's accession number belongs to.

    Args:
        accession_no: The manifest's accession number.
        path: The manifest's local primary-document path, or None (S3 mode).
        rows: ``(accession_id, entry_url, filing_accession_number)`` of the rows
            whose ``entry_url`` matched the filing's file-name stem.

    Returns:
        The ``accession_id`` to patch (or already patched), else None.
    """
    wanted = accession_no.strip()
    own = sorted(r[0] for r in rows if (r[2] or "").strip() == wanted)
    if own:
        return own[0]
    candidates = [r for r in rows if not is_accession_number(r[2])]
    if len(candidates) == 1:
        return candidates[0][0]
    if len(candidates) > 1:
        by_path = [r for r in candidates if same_entry_path(r[1], path)]
        if len(by_path) == 1:
            return by_path[0][0]
    return None


@dataclass
class IngestResult:
    """Outcome for a single filing ingestion attempt."""

    filing_dir: Path
    ticker: str
    year: str
    form_type: str
    primary_document: str
    accession_no: str
    skipped_duplicate: bool = False
    dry_run: bool = False
    success: bool = False
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    attempts: int = 1  # how many arelleCmdLine invocations (>1 => a retry happened)


@dataclass
class IngestStats:
    """Aggregated results from a batch ingest run."""

    total_discovered: int = 0
    skipped_duplicate: int = 0
    skipped_no_manifest: int = 0
    skipped_unsupported: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    dry_run_would_ingest: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[IngestResult] = field(default_factory=list)
    # Run lineage (§4.8) — populated by `_ingest_from_dirs`.
    run_id: str | None = None
    status: str = "OK"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None

    def scalar_counts(self) -> dict:
        """The integer tallies only (for the ingestion_run `counts` snapshot)."""
        return {
            "total_discovered": self.total_discovered,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_no_manifest": self.skipped_no_manifest,
            "skipped_unsupported": self.skipped_unsupported,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dry_run_would_ingest": self.dry_run_would_ingest,
        }


class ArelleXBRLIngestor:
    """Ingest SEC EDGAR iXBRL filings into xbrl_db via arelleCmdLine.

    Parameters
    ----------
    arelle_cmd:
        Full path to the arelleCmdLine executable.
    sec_root:
        Root of the local SEC EDGAR storage tree.
    db_conn_params:
        psycopg2 connect kwargs (host, port, dbname, user, password).
    db_timeout:
        Value passed as the last segment of ``--store-to-XBRL-DB`` connection
        string (Arelle's internal operation timeout, seconds).
    heartbeat_secs:
        How often to log a "still running" line while Arelle works. Arelle is
        silent for long stretches (DTS resolution, then the first filing's whole
        taxonomy insert), so without this a healthy run is indistinguishable
        from a hang. ``0`` disables it.
    internet_connectivity:
        Passed through to ``--internetConnectivity`` when set. ``"offline"``
        forces cache-only DTS resolution — a cache miss then fails immediately
        instead of stalling on an unreachable host.
    """

    def __init__(
        self,
        arelle_cmd: str,
        sec_root: Path,
        db_conn_params: dict,
        db_timeout: int = 2000,
        persist_audit: bool = True,
        max_attempts: int = 2,
        retry_backoff: float = 10.0,
        arelle_timeout: int = 600,
        heartbeat_secs: float = 60.0,
        internet_connectivity: str | None = None,
        store: FilingStore | None = None,
    ) -> None:
        self.arelle_cmd = arelle_cmd
        self.sec_root = Path(sec_root)
        # Filing store (CL #4c): local FS reads in place; S3 mode localizes each
        # filing dir to a temp path so the external Arelle process gets real files
        # (it resolves sibling schema/linkbase refs). Discovery goes through it too.
        self._store = store or FilingStore.from_env(root=sec_root, role="sec_edgar")
        self._db_conn_params = dict(db_conn_params)
        self.db_timeout = db_timeout
        # Bounded retry for *transient* arelle failures (subprocess timeout only;
        # validation errors are permanent and never retried). max_attempts=1
        # disables retry.
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff = retry_backoff
        self.arelle_timeout = arelle_timeout
        self.heartbeat_secs = max(0.0, float(heartbeat_secs))
        self.internet_connectivity = internet_connectivity or None
        # "auto" => persist run lineage to audit.ingestion_run via env config;
        # None => skip persistence (e.g. tests).
        self._audit_store = "auto" if persist_audit else None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        arelle_cmd: str | None = None,
        sec_edgar_root: str | None = None,
    ) -> ArelleXBRLIngestor:
        """Build from environment variables, with optional overrides."""
        return cls(
            arelle_cmd=arelle_cmd_from_env(arelle_cmd),
            sec_root=Path(sec_edgar_root or local_root("sec_edgar")),
            db_conn_params=_arelle_conn_params(),
            db_timeout=int(os.environ.get("XBRL_DB_TIMEOUT", "2000")),
            max_attempts=int(os.environ.get("ARELLE_MAX_ATTEMPTS", "2")),
            retry_backoff=float(os.environ.get("ARELLE_RETRY_BACKOFF", "10")),
            arelle_timeout=int(os.environ.get("ARELLE_TIMEOUT", "600")),
            heartbeat_secs=float(os.environ.get("ARELLE_HEARTBEAT_SECS", "60")),
            internet_connectivity=os.environ.get("ARELLE_INTERNET_CONNECTIVITY")
            or None,
        )

    # ------------------------------------------------------------------
    # Public ingest API
    # ------------------------------------------------------------------

    def ingest_all(self, dry_run: bool = False) -> IngestStats:
        """Discover and ingest every filing under ``sec_root`` / the S3 tree."""
        return self._ingest_from_keys(
            self._discover_filing_keys(), dry_run=dry_run, scope="all"
        )

    def ingest_ticker(
        self,
        ticker: str,
        year: str | None = None,
        form_type: str | None = None,
        dry_run: bool = False,
    ) -> IngestStats:
        """Ingest all filings for *ticker*, optionally filtered by year/form_type.

        Parameters
        ----------
        ticker:
            Ticker symbol, case-insensitive (e.g. ``"NVDA"``).
        year:
            Four-digit year string to restrict to (e.g. ``"2026"``).
        form_type:
            Filing form folder name to restrict to (e.g. ``"10-K"``).
        """
        prefix = ticker.upper()
        # Local mode preserves the historical "directory not found" raises; S3 mode
        # has no notion of an empty dir, so it just discovers no keys.
        if isinstance(self._store, LocalFilingStore):
            ticker_dir = self.sec_root / ticker.upper()
            if not ticker_dir.is_dir():
                raise FileNotFoundError(f"Ticker directory not found: {ticker_dir}")
            if year:
                if not (ticker_dir / year).is_dir():
                    raise FileNotFoundError(
                        f"Year directory not found: {ticker_dir / year}"
                    )
                if form_type and not (ticker_dir / year / form_type).is_dir():
                    raise FileNotFoundError(
                        f"Form directory not found: {ticker_dir / year / form_type}"
                    )
        if year:
            prefix += f"/{year}"
            if form_type:
                prefix += f"/{form_type}"

        scope = f"ticker={ticker.upper()}"
        if year:
            scope += f" year={year}"
        if form_type:
            scope += f" form={form_type}"
        return self._ingest_from_keys(
            self._discover_filing_keys(prefix), dry_run=dry_run, scope=scope
        )

    def ingest_filing(self, filing_dir: Path, dry_run: bool = False) -> IngestStats:
        """Ingest a single specific filing directory / store key."""
        if isinstance(self._store, LocalFilingStore) and not Path(filing_dir).is_dir():
            raise FileNotFoundError(f"Filing directory not found: {filing_dir}")
        rel_key = self._to_rel_key(filing_dir)
        name = rel_key.rsplit("/", 1)[-1] if rel_key else str(filing_dir)
        return self._ingest_from_keys(
            [rel_key], dry_run=dry_run, scope=f"filing={name}"
        )

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _ingest_from_keys(
        self,
        keys: Iterator[str] | list[str],
        dry_run: bool,
        scope: str | None = None,
    ) -> IngestStats:
        stats = IngestStats()
        with record_ingestion_run(
            "arelle_xbrl_ingest", scope=scope, dry_run=dry_run, store=self._audit_store
        ) as run:
            conn = self._connect_db()
            try:
                for rel_key in keys:
                    stats.total_discovered += 1
                    # Local: the real dir (no temp); S3: a temp download of the
                    # whole filing dir so Arelle gets real sibling files.
                    with self._resolve_local_dir(rel_key) as filing_dir:
                        result = self._process_one(filing_dir, rel_key, conn, dry_run)
                    stats.results.append(result)
                    run.add_item_from_arelle(result)

                    if result.error and not result.skipped_duplicate:
                        # categorise non-duplicate skips
                        if "no manifest" in (result.error or "").lower():
                            stats.skipped_no_manifest += 1
                        elif "unsupported" in (result.error or "").lower():
                            stats.skipped_unsupported += 1
                        else:
                            stats.failed += 1
                            stats.errors.append(f"{filing_dir}: {result.error}")
                    elif result.skipped_duplicate:
                        stats.skipped_duplicate += 1
                    elif dry_run:
                        stats.dry_run_would_ingest += 1
                    elif result.success:
                        stats.attempted += 1
                        stats.succeeded += 1
                    else:
                        stats.attempted += 1
                        stats.failed += 1
                        stats.errors.append(
                            f"{filing_dir}: arelle exit {result.returncode}"
                        )
            finally:
                conn.close()

            stats.status = _run_status(stats.succeeded, stats.failed)
            run.set_counts(
                status=stats.status, counts=stats.scalar_counts(), errors=stats.errors
            )
            stats.run_id = run.run_id
            stats.started_at = run.started_at

        stats.finished_at = run.finished_at
        stats.duration_ms = run.duration_ms

        self._log_summary(stats, dry_run)
        return stats

    def _process_one(
        self, filing_dir: Path, rel_key: str, conn, dry_run: bool
    ) -> IngestResult:
        """Process a single filing dir (local real dir or localized temp dir).

        ``rel_key`` is the store-relative ``{TICKER}/{YEAR}/{FORM_TYPE}`` key; it
        carries the identity (Arelle may run on a temp path in S3 mode, so we
        can't derive it from ``filing_dir`` anymore)."""
        parts = Path(rel_key).parts
        ticker = parts[0] if len(parts) > 0 else "UNKNOWN"
        year = parts[1] if len(parts) > 1 else "UNKNOWN"
        form_type = parts[2] if len(parts) > 2 else "UNKNOWN"

        # Read manifest
        manifest = self._read_manifest(filing_dir)
        if manifest is None:
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=form_type,
                primary_document="",
                accession_no="",
                error="No manifest.yaml found",
            )

        primary_doc: str = manifest.get("primary_document", "")
        accession_no: str = manifest.get("accession_no", "")
        filing_form: str = manifest.get("form", form_type)

        if not primary_doc:
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document="",
                accession_no=accession_no,
                error="manifest.yaml has no primary_document",
            )

        htm_path = filing_dir / primary_doc
        if not htm_path.exists():
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document=primary_doc,
                accession_no=accession_no,
                error=f"Primary document not found: {htm_path}",
            )

        if htm_path.suffix.lower() not in _IXBRL_EXTENSIONS:
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document=primary_doc,
                accession_no=accession_no,
                error=f"Unsupported extension: {htm_path.suffix}",
            )

        # Duplicate detection
        if self._is_already_ingested(conn, htm_path, accession_no):
            logger.info("SKIP (already ingested): %s", htm_path)
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document=primary_doc,
                accession_no=accession_no,
                skipped_duplicate=True,
            )

        # Filings whose saved documents lost facts stop here, before anything is loaded.
        refusal = self._document_set_error(filing_dir, manifest)
        if refusal:
            logger.error("REFUSED %s: %s", htm_path, refusal)
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document=primary_doc,
                accession_no=accession_no,
                error=refusal,
            )

        entry_path = filing_dir / arelle_entry_name(manifest)

        if dry_run:
            logger.info("DRY-RUN would ingest: %s", entry_path)
            return IngestResult(
                filing_dir=filing_dir,
                ticker=ticker,
                year=year,
                form_type=filing_form,
                primary_document=primary_doc,
                accession_no=accession_no,
                dry_run=True,
            )

        # Run arelleCmdLine (with bounded retry on transient timeouts)
        logger.info("Ingesting: %s", entry_path)
        rc, stdout, stderr, attempts = self._run_arelle_with_retry(entry_path)
        success = rc == 0
        if success:
            logger.info(
                "OK (rc=0%s): %s",
                f", attempts={attempts}" if attempts > 1 else "",
                primary_doc,
            )
        else:
            logger.error(
                "FAILED (rc=%d, attempts=%d): %s\n%s",
                rc,
                attempts,
                primary_doc,
                stderr[-2000:],
            )

        return IngestResult(
            filing_dir=filing_dir,
            ticker=ticker,
            year=year,
            form_type=filing_form,
            primary_document=primary_doc,
            accession_no=accession_no,
            success=success,
            returncode=rc,
            stdout=stdout,
            stderr=stderr,
            error=None if success else f"arelle exit code {rc}",
            attempts=attempts,
        )

    @staticmethod
    def _document_set_error(filing_dir: Path, manifest: dict) -> str | None:
        """Say why a filing's saved documents cannot be loaded, if they cannot.

        Args:
            filing_dir: A real local filing directory.
            manifest: The filing's manifest.

        Returns:
            The refusal, or None for a filing that may load. A single-document filing
            with no instance on disk has nothing to reconcile and loads as before.
        """
        names = inline_document_names(manifest)
        absent = [name for name in names if not (filing_dir / name).exists()]
        if absent:
            return f"Listed inline documents not on disk: {', '.join(absent)}"
        instance = instance_name(manifest)
        if instance is None or not (filing_dir / instance).exists():
            if len(names) > 1:
                return f"{len(names)} inline documents and no extracted instance on disk"
            return None
        try:
            assert_document_set_complete(
                [(filing_dir / name).read_bytes() for name in names],
                (filing_dir / instance).read_bytes(),
            )
        except DocumentSetMismatchError as exc:
            return str(exc)
        return None

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_already_ingested(
        self, conn, htm_path: Path, accession_no: str | None = None
    ) -> bool:
        """Return True if this filing is already in Arelle's ``accession`` table.

        Arelle stores the full file URI (e.g. ``file:///D:/path/to/doc.htm``)
        or the raw Windows path in ``entry_url``. Rows are found by the filename
        stem (e.g. ``nvda-20260125``), but the stem is NOT unique across filings:
        an amendment can reuse its original's file name (AMD FY2025's 10-K and
        10-K/A are both ``amd-20251227.htm``). So a stem match counts only through
        the accession number (``already_ingested``,
        docs/specs/ingest-accession-identity.md).
        """
        stem = htm_path.stem  # e.g. "nvda-20260125"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entry_url, filing_accession_number FROM accession "
                    "WHERE entry_url ILIKE %s",
                    (f"%{stem}%",),
                )
                rows = [(r[0], r[1]) for r in cur.fetchall()]
            return already_ingested(accession_no or None, rows)
        except Exception as exc:
            logger.warning("Duplicate check failed for %s: %s", stem, exc)
            conn.rollback()
            return False

    # ------------------------------------------------------------------
    # arelleCmdLine invocation
    # ------------------------------------------------------------------

    def _run_arelle_with_retry(self, htm_path: Path) -> tuple[int, str, str, int]:
        """Invoke arelleCmdLine, retrying only *transient* failures.

        ``classify_arelle_failure`` flags ``timeout`` (rc=-1) as the single
        retryable class; validation errors (rc>0), a missing executable (rc=-2),
        and other subprocess errors (rc=-3) are permanent and returned on the
        first attempt. Returns ``(rc, stdout, stderr, attempts)``.
        """
        attempt = 0
        rc, stdout, stderr = -3, "", "no attempt made"
        while attempt < self.max_attempts:
            attempt += 1
            rc, stdout, stderr = self._run_arelle(htm_path)
            if classify_arelle_failure(rc) != "timeout" or attempt >= self.max_attempts:
                break
            wait = backoff_seconds(attempt, self.retry_backoff, cap=120.0)
            logger.warning(
                "Arelle timeout on %s (attempt %d/%d); retrying in %.0fs",
                htm_path.name,
                attempt,
                self.max_attempts,
                wait,
            )
            time.sleep(wait)
        return rc, stdout, stderr, attempt

    def _arelle_argv(self, htm_path: Path) -> list[str]:
        """Build the arelleCmdLine argv for *htm_path*."""
        p = self._db_conn_params
        # The 7th field ("postgres") is the xbrlDB product/type identifier.
        # Without it xbrlDB probes the port via HTTP (Rexster check) which
        # hangs because PostgreSQL does not speak HTTP.
        db_conn_str = (
            f"{p['host']},{p['port']},{p['user']},{p['password']},"
            f"{p['dbname']},{self.db_timeout},postgres"
        )
        # Load the iXBRL compatibility shim before xbrlDB so that
        # ModelInlineFact.xAttributes is patched before insertFacts() runs.
        # EDGAR/transform holds the SEC's ixt-sec transforms; without it their facts
        # store as (ixTransformValueError) (docs/specs/ixt-sec-transforms.md).
        patch_plugin = str(
            Path(__file__).parent / "arelle_plugins" / "xbrl_db_ixbrl_patch.py"
        )
        argv = [
            self.arelle_cmd,
            "--plugins",
            f"{patch_plugin}|EDGAR/transform|xbrlDB",
            "--store-to-XBRL-DB",
            db_conn_str,
            "-f",
            str(htm_path),
        ]
        argv += connectivity_args(self.internet_connectivity)
        return argv

    def _run_arelle(self, htm_path: Path) -> tuple[int, str, str]:
        """Invoke arelleCmdLine and return (returncode, stdout, stderr).

        Delegates to the shared streaming runner so this stage and the label /
        dimension exporters behave identically — output visible as it arrives,
        and a timeout that reports what Arelle was last doing.
        """
        return run_arelle(
            self._arelle_argv(htm_path),
            timeout=self.arelle_timeout,
            logger=logger,
            label=htm_path.name,
            heartbeat_secs=self.heartbeat_secs,
            secret=self._db_conn_params.get("password"),
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_filing_keys(self, prefix: str = "") -> list[str]:
        """Return store-relative ``{TICKER}/{YEAR}/{FORM_TYPE}`` keys (a
        manifest's parent) under *prefix*, via the store (local FS or S3)."""
        keys = set()
        for mkey in self._store.iter_manifest_keys(prefix):
            parent = mkey.rsplit("/", 1)[0] if "/" in mkey else ""
            keys.add(parent)
        return sorted(keys)

    def _to_rel_key(self, filing_ref: str | Path) -> str:
        """Normalize a filing reference (absolute local path under ``sec_root``,
        or an already-relative store key) to a store-relative key."""
        p = Path(str(filing_ref))
        if p.is_absolute():
            try:
                return p.relative_to(self.sec_root).as_posix()
            except ValueError:
                parts = p.parts
                return Path(*parts[-3:]).as_posix() if len(parts) >= 3 else p.name
        return str(filing_ref).replace("\\", "/").strip("/")

    @contextmanager
    def _resolve_local_dir(self, rel_key: str):
        """Yield a real local dir for *rel_key* — the store path in local mode,
        a temp download of the whole filing dir in S3 mode (cleaned up on exit)."""
        lp = self._store.local_path(rel_key)
        if lp is not None:
            yield lp
        else:
            with self._store.localize_dir(rel_key) as d:
                yield d

    # ------------------------------------------------------------------
    # Manifest reader
    # ------------------------------------------------------------------

    @staticmethod
    def _read_manifest(filing_dir: Path) -> dict | None:
        manifest_path = filing_dir / "manifest.yaml"
        if not manifest_path.exists():
            return None
        try:
            with manifest_path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", manifest_path, exc)
            return None

    # ------------------------------------------------------------------
    # DB connection
    # ------------------------------------------------------------------

    def _connect_db(self):
        try:
            return psycopg2.connect(**self._db_conn_params)
        except Exception as exc:
            # Never echo the password — this message reaches logs and tracebacks.
            safe = {
                k: (_REDACTED if k == "password" else v)
                for k, v in self._db_conn_params.items()
            }
            raise RuntimeError(
                f"Cannot connect to xbrl_db: {exc}\nParams: {safe}"
            ) from exc

    # ------------------------------------------------------------------
    # Summary logging
    # ------------------------------------------------------------------

    @staticmethod
    def _log_summary(stats: IngestStats, dry_run: bool) -> None:
        label = " (DRY RUN)" if dry_run else ""
        logger.info(
            "=== Arelle XBRL Ingest%s ===\n"
            "  Run id:              %s\n"
            "  Status / duration:   %s / %sms\n"
            "  Discovered:          %d\n"
            "  Skipped (duplicate): %d\n"
            "  Skipped (no manifest):%d\n"
            "  Skipped (unsupported):%d\n"
            "  %s %d\n"
            "  Succeeded:           %d\n"
            "  Failed:              %d",
            label,
            stats.run_id,
            stats.status,
            f"{stats.duration_ms:.0f}" if stats.duration_ms is not None else "?",
            stats.total_discovered,
            stats.skipped_duplicate,
            stats.skipped_no_manifest,
            stats.skipped_unsupported,
            "Would ingest:" if dry_run else "Attempted:",
            stats.dry_run_would_ingest if dry_run else stats.attempted,
            stats.succeeded,
            stats.failed,
        )
        if stats.errors:
            for err in stats.errors:
                logger.error("  ERROR: %s", err)
