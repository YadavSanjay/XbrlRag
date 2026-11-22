"""XBRL/HTML-canonical -> Qdrant vector DB load pipeline.

Implements Phase 10 ("Embed + Qdrant upsert") of the XBRL ingest, using the payload
shape and clients of the HTML-canonical design
(``docs/design/XbrlRag_HTML_Canonical_Source.md`` §4):

* :class:`~src.embeddings.xbrl_bge_embedding.XBRLChunkPayload` /
  :class:`~src.embeddings.xbrl_bge_embedding.XBRLBGEEmbeddingGenerator`
* :class:`~src.vectorstores.xbrl_qdrant_vectordb_client.XBRLQdrantVectorDBClient`

Per filing, this pipeline:

1. Builds the :class:`SectionTree` and runs :class:`XBRLHTMLChunker`
   (Phase 7) over ``canonical.htm`` to get the ordered :class:`ProseChunk`
   stream — same chunker, same ``chunk_id``s, as Phase 8's
   ``chunk_metadata`` populator
   (:mod:`src.chunking.xbrl_html_base_chunking`).
2. Loads ``chunk_cross_links`` (Phase 9) joined to ``fact`` ⋈ ``element`` ⋈
   ``qname`` (+ ``concept_labels``) to get each chunk's ``xbrl_fact_ids`` /
   ``xbrl_concepts`` / ``xbrl_concept_labels``.
3. Resolves ``ticker``/``company_name`` from ``entity_master`` and
   ``form_type``/``fiscal_period``/``period_end`` from ``filing_registry``
   (already loaded at discovery time).
4. Builds one :class:`XBRLChunkPayload` per chunk via
   :meth:`XBRLChunkPayload.from_prose_chunk`, embeds
   (``BAAI/bge-m3`` dense+sparse for the text collection, dense-only for the
   metadata collection) and upserts both collections.

This module does **not** populate ``chunk_metadata`` / ``chunk_cross_links``
themselves (Phases 7-9 — see ``xbrl_html_base_chunking.py``); it *consumes*
them. A filing with no ``chunk_metadata`` rows is skipped at discovery.

Configuration
-------------------------------------------------------------------------
Driven by ``configs/pipelines/xbrl/load_to_vectordb.yaml`` (see
:mod:`src.pipelines.xbrl` for the loader). The config's ``scope`` selects
between processing every eligible filing (``all``), specific tickers
(``tickers`` — each with optional ``fiscal_years``/``form_types``), or
specific accession numbers (``accessions``). CLI flags
(``--all`` / ``--ticker`` / ``--accessions``) override the config's scope.

Usage (CLI)
-------------------------------------------------------------------------
::

    # Use configs/pipelines/xbrl/load_to_vectordb.yaml's `scope:` as-is
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline

    # Every eligible filing, regardless of config
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline --all

    # All filings for one ticker
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline --ticker NVDA

    # One specific filing for one ticker
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline \\
        --ticker NVDA --fiscal-year 2026 --form-type 10-K

    # Specific accession number(s), any ticker
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline \\
        --accessions 0001045810-26-000021

    # Dry run (parse + report only, no embedding/Qdrant calls)
    python -m src.pipelines.xbrl.load_to_vectordb_pipeline --ticker NVDA --dry-run
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src import (
    CHUNK_SIZE_IN_CHARS,
    XBRL_METADATA_COLLECTION_NAME,
    XBRL_TEXT_COLLECTION_NAME,
)
from src.chunking.xbrl_html_base_chunking import ProseChunk, XBRLHTMLChunker
from src.embeddings.xbrl_bge_embedding import (
    XBRLBGEEmbeddingGenerator,
    XBRLChunkPayload,
)
from src.ingest.sec_edgar.storage import default_local_root, local_root
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    build_section_tree,
    extract_ixbrl_positions,
)
from src.logging_config import get_logger
from src.observability.ingestion_audit import record_ingestion_run
from src.pipelines.xbrl import load_config
from src.vectorstores.xbrl_qdrant_vectordb_client import XBRLQdrantVectorDBClient

logger = get_logger(__name__)

# Same accession-number validity gate as the other Layer-0 populators — only
# filings whose accession number survived Layer0Populator.patch_accession_numbers
# are eligible.
_VALID_ACCESSION_RE = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"

# XBRL namespace URI -> conventional short prefix, for building
# "us-gaap:Revenues"-style xbrl_concepts strings from
# fact -> element -> qname.namespace + qname.local_name.
_NAMESPACE_PREFIX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"fasb\.org/us-gaap", re.I), "us-gaap"),
    (re.compile(r"xbrl\.sec\.gov/dei", re.I), "dei"),
    (re.compile(r"fasb\.org/srt", re.I), "srt"),
    (re.compile(r"xbrl\.sec\.gov/country", re.I), "country"),
    (re.compile(r"xbrl\.sec\.gov/exch", re.I), "exch"),
    (re.compile(r"xbrl\.sec\.gov/stpr", re.I), "stpr"),
    (re.compile(r"xbrl\.sec\.gov/currency", re.I), "currency"),
    (re.compile(r"ifrs\.org", re.I), "ifrs-full"),
]

_STANDARD_LABEL_ROLE = "http://www.xbrl.org/2003/role/label"


def _namespace_to_prefix(namespace: str, ticker: str | None) -> str:
    """Map a fully-qualified XBRL namespace URI to its conventional short
    prefix, e.g. ``http://fasb.org/us-gaap/2025`` -> ``us-gaap``.

    Standard taxonomies (us-gaap, dei, srt, ...) get their well-known prefix.
    Anything else is a filer extension taxonomy, prefixed with the filer's
    ticker (lowercased) for readability — e.g.
    ``http://www.nvidia.com/20260125`` + ticker ``NVDA`` ->
    ``nvda:RevenuesNetOfRefundsLiability``.
    """
    for pattern, prefix in _NAMESPACE_PREFIX_PATTERNS:
        if pattern.search(namespace):
            return prefix
    return (ticker or "ext").lower()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TickerSpec:
    """One ``tickers:`` entry (config) or ``--ticker``/--fiscal-year/--form-type
    combination (CLI) — a ticker plus optional fiscal-year / form-type filters."""

    ticker: str
    fiscal_years: list[int] | None = None
    form_types: list[str] | None = None


@dataclass
class FilingTarget:
    """One filing to embed + upsert — resolved ``filing_registry`` row +
    ``entity_master`` enrichment + canonical HTML path."""

    cik: str
    accession_no: str
    fiscal_year: int
    fiscal_period: str
    form_type: str
    period_end: str | None
    ticker: str | None
    company_name: str | None
    canonical_path: Path


@dataclass
class LoadStats:
    """Aggregated counters for one :meth:`XBRLVectorDBLoader.run`."""

    targets_found: int = 0
    targets_processed: int = 0
    targets_skipped_no_sections: int = 0
    chunks_total: int = 0
    chunks_with_facts: int = 0
    table_chunks: int = 0
    rows_upserted: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Run lineage (§4.8) — populated by `XBRLVectorDBLoader.run`.
    run_id: str | None = None
    status: str = "OK"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None

    def scalar_counts(self) -> dict:
        """The integer/bool tallies only (for the ingestion_run `counts` snapshot)."""
        return {
            "targets_found": self.targets_found,
            "targets_processed": self.targets_processed,
            "targets_skipped_no_sections": self.targets_skipped_no_sections,
            "chunks_total": self.chunks_total,
            "chunks_with_facts": self.chunks_with_facts,
            "table_chunks": self.table_chunks,
            "rows_upserted": self.rows_upserted,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class XBRLVectorDBLoader:
    """Phase 10 — embed :class:`ProseChunk`s and upsert
    :class:`~src.embeddings.xbrl_bge_embedding.XBRLChunkPayload` into the
    XBRL text/metadata Qdrant collections.

    Parameters mirror :class:`~src.ingest.sec_edgar.xbrl.dom_position_populator.DOMPositionPopulator`
    / :class:`~src.chunking.xbrl_html_base_chunking.XBRLHTMLChunkPopulator`
    (same ``sec_edgar_root``/``normalized_root`` conventions, same
    ``from_env``-via-``.env`` pattern) so this pipeline composes with the
    earlier phases.
    """

    DEFAULT_SEC_ROOT = default_local_root("sec_edgar")
    DEFAULT_NORMALIZED_ROOT = default_local_root("normalized")

    def __init__(
        self,
        sec_edgar_root: str | Path = DEFAULT_SEC_ROOT,
        normalized_root: str | Path = DEFAULT_NORMALIZED_ROOT,
        db_conn_params: dict | None = None,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        qdrant_timeout: int = 60,
        text_collection: str = XBRL_TEXT_COLLECTION_NAME,
        metadata_collection: str = XBRL_METADATA_COLLECTION_NAME,
        chunk_size_chars: int = CHUNK_SIZE_IN_CHARS,
        toggle_hnsw: bool = True,
        ensure_payload_indexes: bool = True,
        persist_audit: bool = True,
        embed_batch_size: int = 12,
        embed_max_length: int = 8192,
    ) -> None:
        self.sec_edgar_root = Path(sec_edgar_root).expanduser().resolve()
        self.normalized_root = Path(normalized_root).expanduser().resolve()
        self._db_conn_params = db_conn_params or {}
        self.text_collection = text_collection
        self.metadata_collection = metadata_collection
        self.toggle_hnsw = toggle_hnsw
        self.ensure_payload_indexes = ensure_payload_indexes
        # "auto" => persist run lineage to audit.ingestion_run via env config;
        # None => skip persistence (e.g. tests).
        self._audit_store = "auto" if persist_audit else None

        # BGE-M3 encode batch_size: FlagEmbedding's default (256) builds one
        # oversized forward pass that segfaults on CPU — cap it (see
        # XBRLBGEEmbeddingGenerator / BGEEmbeddingGenerator).
        self._embed_batch_size = embed_batch_size
        self._embed_max_length = embed_max_length

        self._chunker = XBRLHTMLChunker(chunk_size_chars=chunk_size_chars)
        # timeout=60 (not the client's 5s default): payload-index builds on a
        # freshly created on-disk-HNSW collection can exceed 5s and raise
        # ReadTimeout during ensure_collections().
        self._vectordb = XBRLQdrantVectorDBClient(
            host=qdrant_host, port=qdrant_port, timeout=qdrant_timeout
        )
        self._embedder: XBRLBGEEmbeddingGenerator | None = None

        logger.info(
            "XBRLVectorDBLoader  sec_edgar_root=%s  normalized_root=%s  "
            "text_collection=%s  metadata_collection=%s",
            self.sec_edgar_root,
            self.normalized_root,
            text_collection,
            metadata_collection,
        )

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_env(
        cls, dotenv_path: str | Path | None = None, **overrides
    ) -> XBRLVectorDBLoader:
        """Construct with defaults + environment variables only (no YAML
        config) — reads ``XBRL_DB_HOST/PORT/NAME/USER/PASSWORD`` and
        ``SEC_EDGAR_ROOT`` from ``.env``, same as the Layer-0 populators."""
        return cls.from_config({}, dotenv_path=dotenv_path, **overrides)

    @classmethod
    def from_config(
        cls,
        config: dict,
        dotenv_path: str | Path | None = None,
        **overrides,
    ) -> XBRLVectorDBLoader:
        """Construct from a parsed ``configs/pipelines/xbrl/load_to_vectordb.yaml``
        document (see :func:`src.pipelines.xbrl.load_config`), with env-var
        fallbacks for DB connection params and ``SEC_EDGAR_ROOT``.

        ``**overrides`` are applied last (take precedence over both the
        config and the env-derived defaults) — handy for tests.
        """
        load_dotenv(dotenv_path)
        db_conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        paths = config.get("paths", {}) or {}
        qdrant = config.get("qdrant", {}) or {}
        collections = config.get("collections", {}) or {}
        chunking = config.get("chunking", {}) or {}
        embedding = config.get("embedding", {}) or {}

        kwargs: dict = dict(
            # Env wins over the YAML, same precedence as qdrant_host below: the
            # checked-in config carries a local-dev path, and the container must
            # not need it edited. A YAML absolute path is a *relative* path on
            # POSIX, so letting it win yields /app/D:/... silently.
            sec_edgar_root=os.environ.get("SEC_EDGAR_ROOT")
            or paths.get("sec_edgar_root")
            or local_root("sec_edgar"),
            normalized_root=os.environ.get("XBRLRAG_NORMALIZED_ROOT")
            or paths.get("normalized_root")
            or local_root("normalized"),
            db_conn_params=db_conn_params,
            # Env wins over the YAML default so an AWS/in-VPC Qdrant is reached
            # without editing the checked-in config; env unset => YAML/localhost.
            qdrant_host=os.environ.get("QDRANT_HOST")
            or qdrant.get("host")
            or "localhost",
            qdrant_port=int(
                os.environ.get("QDRANT_PORT") or qdrant.get("port") or 6333
            ),
            qdrant_timeout=qdrant.get("timeout", 60),
            text_collection=collections.get(
                "text_collection", XBRL_TEXT_COLLECTION_NAME
            ),
            metadata_collection=collections.get(
                "metadata_collection", XBRL_METADATA_COLLECTION_NAME
            ),
            chunk_size_chars=chunking.get("chunk_size_chars", CHUNK_SIZE_IN_CHARS),
            toggle_hnsw=collections.get("toggle_hnsw", True),
            ensure_payload_indexes=collections.get("ensure_payload_indexes", True),
            embed_batch_size=embedding.get("batch_size", 12),
            embed_max_length=embedding.get("max_length", 8192),
        )
        kwargs.update(overrides)
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Collection setup
    # ------------------------------------------------------------------

    def ensure_collections(self) -> None:
        """Create the text/metadata collections if missing (idempotent) and,
        if ``ensure_payload_indexes``, (re)create the
        :class:`XBRLQdrantVectorDBClient` payload indexes on both."""
        self._vectordb.create_collection(self.text_collection, include_sparse=True)
        self._vectordb.create_collection(self.metadata_collection, include_sparse=False)
        if self.ensure_payload_indexes:
            self._vectordb.create_payload_indexes(self.text_collection)
            self._vectordb.create_payload_indexes(self.metadata_collection)

    @property
    def embedder(self):
        """Lazily-constructed embedder.

        When ``RAGINFER_URL`` is set, embed over HTTP via the shared RagInference
        service (``RemoteEmbedder``) — so the ingest image stays torch-free, the
        same way the query path works. Otherwise load ``BAAI/bge-m3`` in-process
        (the local dev path; pulls torch). Either way, dry runs never construct it.
        """
        if self._embedder is None:
            if os.getenv("RAGINFER_URL", "").strip():
                from src.embeddings.remote_embedder import RemoteEmbedder

                logger.info(
                    "Embedding via RagInference (RAGINFER_URL set) — no local BGE-M3."
                )
                self._embedder = RemoteEmbedder()
            else:
                from src.embeddings.xbrl_bge_embedding import XBRLBGEEmbeddingGenerator

                logger.info("Embedding with in-process BGE-M3 (RAGINFER_URL unset).")
                self._embedder = XBRLBGEEmbeddingGenerator(
                    batch_size=self._embed_batch_size,
                    max_length=self._embed_max_length,
                )
        return self._embedder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        scope: str = "all",
        ticker_specs: list[TickerSpec] | None = None,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> LoadStats:
        """Discover targets per *scope* and embed + upsert each.

        Parameters
        ----------
        scope:
            ``"all"`` — every non-superseded filing with ``chunk_metadata``
            rows. ``"tickers"`` — union of filings matching each
            :class:`TickerSpec` in *ticker_specs* (deduplicated by
            ``accession_no``). ``"accessions"`` — exactly the filings in
            *accession_nos*.
        dry_run:
            Parse + chunk + report counts without loading the embedding model
            or calling Qdrant.
        """
        stats = LoadStats(dry_run=dry_run)

        if not dry_run:
            self.ensure_collections()

        with record_ingestion_run(
            "load_to_vectordb",
            scope=self._scope_desc(scope, ticker_specs, accession_nos),
            dry_run=dry_run,
            store=self._audit_store,
        ) as run:
            with psycopg2.connect(**self._db_conn_params) as conn:
                targets = self._resolve_targets(
                    conn, scope, ticker_specs, accession_nos
                )
                stats.targets_found = len(targets)
                logger.info(
                    "xbrl_load_to_vectordb: %d target filing(s) discovered (scope=%s).",
                    len(targets),
                    scope,
                )

                do_toggle = not dry_run and self.toggle_hnsw and targets
                if do_toggle:
                    self._vectordb.prepare_for_ingestion(self.text_collection)
                    self._vectordb.prepare_for_ingestion(self.metadata_collection)

                for target in targets:
                    before_skipped = stats.targets_skipped_no_sections
                    item_status, failure_class, item_error = "success", None, None
                    try:
                        self._process_target(conn, target, stats, dry_run)
                        if stats.targets_skipped_no_sections > before_skipped:
                            item_status = "skipped_no_sections"
                    except Exception as exc:
                        msg = f"{target.accession_no}: unhandled error"
                        logger.exception(
                            "xbrl_load_to_vectordb failed for %s", target.accession_no
                        )
                        stats.errors.append(msg)
                        item_status, failure_class, item_error = (
                            "error",
                            "unhandled",
                            repr(exc),
                        )
                    run.add_item(
                        accession_no=target.accession_no,
                        ticker=target.ticker,
                        fiscal_year=str(target.fiscal_year),
                        form_type=target.form_type,
                        status=item_status,
                        failure_class=failure_class,
                        error=item_error,
                    )

                if do_toggle:
                    self._vectordb.update_post_ingestion(self.text_collection)
                    self._vectordb.update_post_ingestion(self.metadata_collection)

            stats.status = (
                "OK"
                if not stats.errors
                else ("PARTIAL" if stats.targets_processed > 0 else "ERROR")
            )
            run.set_counts(
                status=stats.status, counts=stats.scalar_counts(), errors=stats.errors
            )
            stats.run_id = run.run_id
            stats.started_at = run.started_at

        stats.finished_at = run.finished_at
        stats.duration_ms = run.duration_ms

        self._log_summary(stats)
        return stats

    @staticmethod
    def _scope_desc(
        scope: str,
        ticker_specs: list[TickerSpec] | None,
        accession_nos: list[str] | None,
    ) -> str:
        if scope == "tickers" and ticker_specs:
            return "tickers=" + ",".join(t.ticker for t in ticker_specs)
        if scope == "accessions" and accession_nos:
            return "accessions=" + ",".join(accession_nos)
        return scope

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _resolve_targets(
        self,
        conn: psycopg2.extensions.connection,
        scope: str,
        ticker_specs: list[TickerSpec] | None,
        accession_nos: list[str] | None,
    ) -> list[FilingTarget]:
        if scope == "accessions":
            return self._discover_targets(conn, accession_nos=accession_nos or [])

        if scope == "tickers":
            targets: list[FilingTarget] = []
            seen: set[str] = set()
            for spec in ticker_specs or []:
                for t in self._discover_targets(
                    conn,
                    tickers=[spec.ticker],
                    fiscal_years=spec.fiscal_years,
                    form_types=spec.form_types,
                ):
                    if t.accession_no not in seen:
                        seen.add(t.accession_no)
                        targets.append(t)
            return targets

        # scope == "all" (or unrecognised -> safest default)
        return self._discover_targets(conn)

    def _discover_targets(
        self,
        conn: psycopg2.extensions.connection,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        form_types: list[str] | None = None,
        accession_nos: list[str] | None = None,
    ) -> list[FilingTarget]:
        """Resolve filings to process: ``filing_registry`` ⋈ ``accession``
        ⋈ ``entity_master``, restricted to filings that already have
        ``chunk_metadata`` rows (Phase 8 must have run first)."""
        sql = f"""
            SELECT fr.cik, fr.accession_no, fr.fiscal_year, fr.fiscal_period,
                   fr.form_type, fr.period_end, a.entry_url,
                   em.trading_symbol, em.company_name
              FROM filing_registry fr
              JOIN accession a ON a.filing_accession_number = fr.accession_no
              LEFT JOIN entity_master em ON em.cik = fr.cik
             WHERE fr.superseded_by IS NULL
               AND fr.accession_no ~ '{_VALID_ACCESSION_RE}'
               AND EXISTS (SELECT 1 FROM chunk_metadata cm WHERE cm.accession_no = fr.accession_no)
        """
        params: list = []
        if tickers:
            sql += " AND em.trading_symbol = ANY(%s)"
            params.append([t.upper() for t in tickers])
        if fiscal_years:
            sql += " AND fr.fiscal_year = ANY(%s)"
            params.append(list(fiscal_years))
        if form_types:
            sql += " AND fr.form_type = ANY(%s)"
            params.append(list(form_types))
        if accession_nos:
            sql += " AND fr.accession_no = ANY(%s)"
            params.append(list(accession_nos))
        sql += " ORDER BY fr.cik, fr.fiscal_year"

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        targets: list[FilingTarget] = []
        for (
            cik,
            accession_no,
            fiscal_year,
            fiscal_period,
            form_type,
            period_end,
            entry_url,
            ticker,
            company_name,
        ) in rows:
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
                    cik=cik,
                    accession_no=accession_no,
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period or "FY",
                    form_type=form_type,
                    period_end=period_end.isoformat() if period_end else None,
                    ticker=ticker,
                    company_name=company_name,
                    canonical_path=canon,
                )
            )
        return targets

    def _resolve_canonical_path(self, entry_url: str | None) -> Path | None:
        """Mirror ``DOMPositionPopulator._resolve_canonical_path`` /
        ``XBRLHTMLChunkPopulator._resolve_canonical_path`` — map
        ``accession.entry_url`` to ``canonical.htm`` by mirroring its
        relative path under ``normalized_root``."""
        if not entry_url:
            return None
        try:
            rel = Path(entry_url).resolve().relative_to(self.sec_edgar_root)
        except (ValueError, OSError):
            return None
        return self.normalized_root / rel.parent / "canonical.htm"

    # ------------------------------------------------------------------
    # chunk_cross_links -> xbrl_fact_ids / xbrl_concepts / xbrl_concept_labels
    # ------------------------------------------------------------------

    def _load_cross_links(
        self, conn: psycopg2.extensions.connection, target: FilingTarget
    ) -> dict[str, dict[str, list]]:
        """Per ``chunk_id`` in *target*, collect ``chunk_cross_links.fact_id``
        plus the cross-linked facts' taxonomy concepts (and human-readable
        labels where available).

        Returns ``{chunk_id: {"fact_ids": [...], "concepts": [...],
        "labels": [...]}}`` — empty for chunks with no cross-links (pure
        prose, by design — see ``chunk_cross_links.sql``).
        """
        sql = """
            SELECT cl.chunk_id, cl.fact_id, q.namespace, q.local_name
              FROM chunk_cross_links cl
              JOIN chunk_metadata cm ON cm.chunk_id = cl.chunk_id
              JOIN fact f  ON f.fact_id = cl.fact_id
              JOIN element el ON el.element_id = f.element_id
              JOIN qname q ON q.qname_id = el.qname_id
             WHERE cm.accession_no = %s
        """
        with conn.cursor() as cur:
            cur.execute(sql, (target.accession_no,))
            rows = cur.fetchall()

        by_chunk: dict[str, dict[str, set]] = collections.defaultdict(
            lambda: {"fact_ids": set(), "concept_keys": set()}
        )
        for chunk_id, fact_id, namespace, local_name in rows:
            entry = by_chunk[chunk_id]
            entry["fact_ids"].add(fact_id)
            prefix = _namespace_to_prefix(namespace, target.ticker)
            # (short display form, full key for concept_labels lookup)
            entry["concept_keys"].add(
                (f"{prefix}:{local_name}", f"{namespace}:{local_name}")
            )

        all_label_keys = {ck[1] for v in by_chunk.values() for ck in v["concept_keys"]}
        labels = self._load_concept_labels(conn, all_label_keys, target.cik)

        result: dict[str, dict[str, list]] = {}
        for chunk_id, entry in by_chunk.items():
            result[chunk_id] = {
                "fact_ids": sorted(entry["fact_ids"]),
                "concepts": sorted({ck[0] for ck in entry["concept_keys"]}),
                "labels": sorted(
                    {labels[ck[1]] for ck in entry["concept_keys"] if ck[1] in labels}
                ),
            }
        return result

    @staticmethod
    def _load_concept_labels(
        conn: psycopg2.extensions.connection,
        concept_keys: set[str],
        cik: str,
    ) -> dict[str, str]:
        """``{namespace:local_name -> standard label}``, preferring a
        filer-specific (``cik_override = cik``) label over the shared
        taxonomy label when both exist."""
        if not concept_keys:
            return {}
        sql = """
            SELECT concept, cik_override, label
              FROM concept_labels
             WHERE label_role = %s
               AND xml_lang = 'en-US'
               AND concept = ANY(%s)
               AND (cik_override = %s OR cik_override IS NULL)
             ORDER BY concept, (cik_override IS NULL)
        """
        with conn.cursor() as cur:
            cur.execute(sql, (_STANDARD_LABEL_ROLE, list(concept_keys), cik))
            rows = cur.fetchall()

        labels: dict[str, str] = {}
        for concept, _cik_override, label in rows:
            labels.setdefault(
                concept, label
            )  # first row wins (filer-specific sorts first)
        return labels

    # ------------------------------------------------------------------
    # Per-filing processing
    # ------------------------------------------------------------------

    def _process_target(
        self,
        conn: psycopg2.extensions.connection,
        target: FilingTarget,
        stats: LoadStats,
        dry_run: bool,
    ) -> None:
        section_tree = build_section_tree(target.canonical_path, target.accession_no)
        if section_tree.section_count == 0:
            stats.targets_skipped_no_sections += 1
            logger.warning("%s: empty SectionTree — skipping.", target.accession_no)
            return

        ixbrl_positions = extract_ixbrl_positions(target.canonical_path)
        chunks: list[ProseChunk] = self._chunker.chunk_filing(
            target.canonical_path,
            target.accession_no,
            section_tree=section_tree,
            ixbrl_positions=ixbrl_positions,
        )
        if not chunks:
            stats.warnings.append(f"{target.accession_no}: chunker produced 0 chunks")
            return

        cross_links = self._load_cross_links(conn, target)

        payloads: list[XBRLChunkPayload] = []
        for chunk in chunks:
            node = section_tree.nodes.get(chunk.section_path)
            cl = cross_links.get(chunk.chunk_id, {})
            payloads.append(
                XBRLChunkPayload.from_prose_chunk(
                    chunk,
                    cik=target.cik,
                    accession_no=target.accession_no,
                    fiscal_year=target.fiscal_year,
                    form_type=target.form_type,
                    fiscal_period=target.fiscal_period,
                    is_financial_statement=bool(node and node.is_financial_statement),
                    ticker=target.ticker,
                    company_name=target.company_name,
                    period_end=target.period_end,
                    xbrl_fact_ids=cl.get("fact_ids"),
                    xbrl_concepts=cl.get("concepts"),
                    xbrl_concept_labels=cl.get("labels"),
                )
            )

        stats.targets_processed += 1
        stats.chunks_total += len(payloads)
        stats.chunks_with_facts += sum(1 for p in payloads if p.has_xbrl_facts)
        stats.table_chunks += sum(1 for p in payloads if p.contains_table)

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would embed+upsert %d chunk(s) "
                "(%d table, %d with xbrl_fact_ids) -> %s / %s.",
                target.accession_no,
                len(payloads),
                sum(1 for p in payloads if p.contains_table),
                sum(1 for p in payloads if p.has_xbrl_facts),
                self.text_collection,
                self.metadata_collection,
            )
            return

        # Input prep is pure string work (static) — no model needed, so it works
        # regardless of which embedder RAGINFER_URL selected.
        text_inputs, metadata_inputs = (
            XBRLBGEEmbeddingGenerator.prepare_embedding_inputs(payloads)
        )
        text_embeddings = self.embedder.embed_texts(text_inputs)
        metadata_embeddings = self.embedder.dense_embed_texts(metadata_inputs)

        payload_map = {p.chunk_id: p for p in payloads}
        self._vectordb.upload_collection(
            self.text_collection, text_embeddings, payload_map
        )
        self._vectordb.upload_collection(
            self.metadata_collection, metadata_embeddings, payload_map
        )

        stats.rows_upserted += len(payloads)
        logger.info(
            "%s: upserted %d chunk(s) (%d table, %d with xbrl_fact_ids) -> %s / %s.",
            target.accession_no,
            len(payloads),
            sum(1 for p in payloads if p.contains_table),
            sum(1 for p in payloads if p.has_xbrl_facts),
            self.text_collection,
            self.metadata_collection,
        )

    # ------------------------------------------------------------------
    def _log_summary(self, stats: LoadStats) -> None:
        prefix = "[DRY RUN] " if stats.dry_run else ""
        duration = (
            f"{stats.duration_ms:.0f}ms" if stats.duration_ms is not None else "?"
        )
        logger.info(
            "%sxbrl_load_to_vectordb summary [run=%s status=%s dur=%s]: "
            "targets=%d processed=%d "
            "skipped_no_sections=%d | chunks: total=%d table=%d with_facts=%d | "
            "rows_upserted=%d errors=%d",
            prefix,
            stats.run_id,
            stats.status,
            duration,
            stats.targets_found,
            stats.targets_processed,
            stats.targets_skipped_no_sections,
            stats.chunks_total,
            stats.table_chunks,
            stats.chunks_with_facts,
            stats.rows_upserted,
            len(stats.errors),
        )
        for w in stats.warnings:
            logger.warning("  %s", w)
        for e in stats.errors:
            logger.error("  %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _ticker_specs_from_config(config: dict) -> list[TickerSpec]:
    return [
        TickerSpec(
            ticker=t["ticker"],
            fiscal_years=t.get("fiscal_years"),
            form_types=t.get("form_types"),
        )
        for t in config.get("tickers", []) or []
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.pipelines.xbrl.load_to_vectordb_pipeline``."""
    p = argparse.ArgumentParser(
        prog="xbrl_load_to_vectordb",
        description=(
            "Embed XBRL/HTML-canonical prose+table chunks (BAAI/bge-m3) and "
            "upsert XBRLChunkPayload into the xbrl_sec10k_text / "
            "xbrl_sec10k_metadata Qdrant collections (Phase 10)."
        ),
    )
    p.add_argument(
        "--config",
        default="load_to_vectordb",
        metavar="NAME",
        help="Config name under configs/pipelines/xbrl/ (without "
        ".yaml). Default: load_to_vectordb",
    )
    p.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env in current directory).",
    )

    scope_group = p.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--all",
        action="store_true",
        help="Process every eligible filing (overrides config scope).",
    )
    scope_group.add_argument(
        "--accessions",
        nargs="+",
        default=None,
        metavar="ACCESSION_NO",
        help="Process specific EDGAR accession number(s) "
        "(overrides config scope), e.g. 0001045810-26-000021.",
    )

    p.add_argument(
        "--ticker",
        nargs="+",
        default=None,
        metavar="TICKER",
        help="Limit to filing(s) for these ticker(s) (overrides config "
        "`tickers`). Combine with --fiscal-year/--form-type to "
        "target one specific filing per ticker.",
    )
    p.add_argument(
        "--fiscal-year",
        nargs="+",
        type=int,
        default=None,
        metavar="YEAR",
        help="Restrict --ticker to these fiscal year(s).",
    )
    p.add_argument(
        "--form-type",
        nargs="+",
        default=None,
        metavar="FORM",
        help="Restrict --ticker to these form type(s), e.g. 10-K.",
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse, chunk and report without embedding or writing to Qdrant.",
    )
    p.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )

    args = p.parse_args(argv)

    import logging

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    loader = XBRLVectorDBLoader.from_config(config, dotenv_path=args.env_file)

    # ---- Resolve scope: CLI flags override config `scope` ----
    ticker_specs: list[TickerSpec] | None = None
    accession_nos: list[str] | None = None

    if args.accessions:
        scope = "accessions"
        accession_nos = args.accessions
    elif args.all:
        scope = "all"
    elif args.ticker:
        scope = "tickers"
        ticker_specs = [
            TickerSpec(
                ticker=t, fiscal_years=args.fiscal_year, form_types=args.form_type
            )
            for t in args.ticker
        ]
    else:
        scope = config.get("scope", "all")
        if scope == "tickers":
            ticker_specs = _ticker_specs_from_config(config)
        elif scope == "accessions":
            accession_nos = config.get("accessions", []) or []

    stats = loader.run(
        scope=scope,
        ticker_specs=ticker_specs,
        accession_nos=accession_nos,
        dry_run=args.dry_run,
    )

    if stats.errors:
        print(f"\n  Total errors ({len(stats.errors)}):")
        for e in stats.errors:
            print(f"    - {e}")

    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
