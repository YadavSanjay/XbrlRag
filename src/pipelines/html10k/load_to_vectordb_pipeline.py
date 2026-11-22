# The load of html10k_text: primary document, ix-strip, chunks, embeddings, Qdrant.
#
# A filing is replaced whole, and nothing is deleted until its embeddings exist, so a load that
# stops can restart at a filing boundary without paying again for what's done. The load reads
# filing metadata only: no fact, concept, context or chunk-link table.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
)

from src.chunking.html10k_chunking import chunk_filing
from src.embeddings.base import BaseEmbeddingGenerator, DenseEmbedding, SparseEmbedding
from src.eval.systems.long_context import (
    ANNUAL_FORMS,
    _connect_with_timeouts,
    canonical_key_from_entry_url,
)
from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.sec_edgar.storage import FilingStore, local_root
from src.logging_config import get_logger
from src.retriever.html10k.config import Html10kRagConfig, load_html10k_config
from src.vectorstores.html10k_qdrant_vectordb_client import (
    COLLECTION_NAME,
    Html10kQdrantVectorDBClient,
    build_payload,
    embedding_input,
)

logger = get_logger(__name__)

# The conditions of PostgresFilingResolver.
_LIST_SQL = """
    SELECT DISTINCT fr.cik, em.trading_symbol, em.company_name, fr.accession_no, fr.fiscal_year,
                    fr.fiscal_period, fr.form_type, fr.period_end, a.entry_url
      FROM filing_registry fr
      JOIN entity_master em ON em.cik = fr.cik
      JOIN accession a ON a.filing_accession_number = fr.accession_no
     WHERE fr.fiscal_period = 'FY'
       AND fr.superseded_by IS NULL
       AND fr.form_type = ANY(%s)
"""


def _source_key(entry_url: str | None, sec_edgar_root: str) -> str | None:
    """The primary document's key, ``entry_url`` relative to the root, or ``None`` outside it."""
    canonical = canonical_key_from_entry_url(entry_url, sec_edgar_root)
    if canonical is None or entry_url is None:
        return None
    file_name = entry_url.replace("\\", "/").rpartition("/")[2]
    return f"{canonical.rpartition('/')[0]}/{file_name}"


def list_annual_filings(
    conn: Any,
    sec_edgar_root: str,
    *,
    tickers: Sequence[str] | None = None,
    fiscal_years: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """List the annual filings to load.

    Args:
        conn: A database connection.
        sec_edgar_root: The raw download root that ``entry_url`` sits under.
        tickers: Keep only these filers; all when omitted.
        fiscal_years: Keep only these fiscal years; all when omitted.

    Returns:
        ``{"filing": <the mapping build_payload takes>, "source_key": <store key or None>}``, sorted
        by ticker, fiscal year and accession number.
    """
    with conn.cursor() as cur:
        cur.execute(_LIST_SQL, (list(ANNUAL_FORMS),))
        rows = cur.fetchall()

    entries: list[dict[str, Any]] = []
    for cik, ticker, name, accession_no, fiscal_year, period, form, period_end, entry_url in rows:
        if tickers is not None and ticker not in tickers:
            continue
        if fiscal_years is not None and fiscal_year not in fiscal_years:
            continue
        entries.append(
            {
                "filing": {
                    "cik": cik,
                    "ticker": ticker,
                    "company_name": name,
                    "accession_no": accession_no,
                    "fiscal_year": int(fiscal_year),  # the registry's label, never derived
                    "fiscal_period": period,
                    "form_type": form,
                    "period_end": str(period_end),
                },
                "source_key": _source_key(entry_url, sec_edgar_root),
            }
        )
    entries.sort(
        key=lambda e: (
            str(e["filing"]["ticker"]), e["filing"]["fiscal_year"], e["filing"]["accession_no"]
        )
    )
    return entries


class Html10kLoadPipeline:
    """Loads filings into ``html10k_text``, one whole filing at a time, resumably."""

    def __init__(
        self,
        *,
        store: FilingStore,
        embedder: BaseEmbeddingGenerator,
        client: QdrantClient,
        config: Html10kRagConfig,
    ) -> None:
        """Keep the dependencies; nothing is contacted.

        Args:
            store: Reads the raw primary documents.
            embedder: Embeds the chunks.
            client: The Qdrant client.
            config: The settings.
        """
        self._store = store
        self._embedder = embedder
        self._client = client
        self._config = config
        self._collection = Html10kQdrantVectorDBClient(client)

    def _embed(
        self, payloads: Sequence[Mapping[str, object]]
    ) -> dict[str, tuple[DenseEmbedding, SparseEmbedding]]:
        """Embed the payloads in bounded batches."""
        vectors: dict[str, tuple[DenseEmbedding, SparseEmbedding]] = {}
        size = self._config.embed_batch_size
        for start in range(0, len(payloads), size):
            batch = [
                (str(payload["chunk_id"]), embedding_input(payload))
                for payload in payloads[start : start + size]
            ]
            vectors.update(self._embedder.embed_texts(batch))
        return vectors

    def load_filing(self, filing: Mapping[str, Any], source_key: str) -> str:
        """Load one filing, unless the collection already holds exactly its chunks.

        Args:
            filing: The mapping ``build_payload`` takes.
            source_key: The primary document's key in the store.

        Returns:
            ``"loaded"`` or ``"skipped"``.
        """
        accession_no = str(filing["accession_no"])
        stripped = ix_strip_html(self._store.read_bytes(source_key), name=source_key)
        chunks = chunk_filing(
            stripped, accession_no, table_max_chars=self._config.table_max_chars
        )
        payloads = [build_payload(chunk, filing) for chunk in chunks]

        this_filing = Filter(
            must=[FieldCondition(key="accession_no", match=MatchValue(value=accession_no))]
        )
        held = self._client.count(
            collection_name=COLLECTION_NAME, count_filter=this_filing, exact=True
        ).count
        if payloads and held == len(payloads):
            return "skipped"

        vectors = self._embed(payloads)
        if held:
            self._client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=FilterSelector(filter=this_filing),
                wait=True,
            )
        self._collection.upload_chunks(payloads, vectors)
        return "loaded"

    def run(self, filings: Iterable[Mapping[str, Any]]) -> dict[str, list]:
        """Ensure the collection, then load every filing; one failure does not stop the rest.

        Args:
            filings: The entries of ``list_annual_filings``.

        Returns:
            ``{"loaded": [...], "skipped": [...], "failed": [...]}``: accession numbers, and for
            ``failed`` a ``{"accession_no": ..., "error": ...}`` each.
        """
        self._collection.ensure_collection()
        outcome: dict[str, list] = {"loaded": [], "skipped": [], "failed": []}
        for entry in filings:
            filing, source_key = entry["filing"], entry["source_key"]
            accession_no = filing["accession_no"]
            if source_key is None:
                error = "the primary document is not under the download root"
                logger.warning("html10k load: %s: %s", accession_no, error)
                outcome["failed"].append({"accession_no": accession_no, "error": error})
                continue
            try:
                state = self.load_filing(filing, source_key)
            except Exception as exc:  # isolation point: one bad filing leaves the rest
                logger.warning("html10k load: %s failed: %r", accession_no, exc)
                outcome["failed"].append({"accession_no": accession_no, "error": repr(exc)})
                continue
            logger.info("html10k load: %s %s", accession_no, state)
            outcome[state].append(accession_no)
        return outcome


def _build_pipeline(config: Html10kRagConfig) -> Html10kLoadPipeline:
    """The pipeline on its real backends: the raw store, RagInference and ``QDRANT_*``."""
    from src.embeddings.remote_embedder import RemoteEmbedder
    from src.vectorstores.qdrant_vectordb_client import QdrantVectorDBClient

    return Html10kLoadPipeline(
        store=FilingStore.from_env(role="sec_edgar"),
        embedder=RemoteEmbedder(),
        client=QdrantVectorDBClient().client,
        config=config,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Load annual filings into ``html10k_text``. Needs Postgres, RagInference and Qdrant.

    Args:
        argv: The command line, ``sys.argv`` when omitted.

    Returns:
        The exit code: 1 when a filing failed.
    """
    parser = argparse.ArgumentParser(description="Load the HTML 10-K flow's chunks into Qdrant.")
    parser.add_argument("--ticker", action="append", help="A filer; may be repeated.")
    parser.add_argument(
        "--fiscal-year", action="append", type=int, help="A fiscal year; may be repeated."
    )
    parser.add_argument("--all", action="store_true", help="Every annual filing in the registry.")
    args = parser.parse_args(argv)
    if args.all == bool(args.ticker or args.fiscal_year):
        parser.error("give --all, or --ticker and/or --fiscal-year")

    conn = _connect_with_timeouts()
    try:
        entries = list_annual_filings(
            conn, local_root("sec_edgar"), tickers=args.ticker, fiscal_years=args.fiscal_year
        )
    finally:
        conn.close()

    outcome = _build_pipeline(load_html10k_config()).run(entries)
    print(json.dumps({key: len(value) for key, value in outcome.items()}))
    for failure in outcome["failed"]:
        print(json.dumps(failure))
    return 1 if outcome["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
