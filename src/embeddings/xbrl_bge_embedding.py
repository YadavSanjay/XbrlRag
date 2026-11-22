"""BGE-M3 embedding generator + Qdrant payload shape for the XBRL/
HTML-canonical ingestion path (the embed + Qdrant upsert step;
``docs/design/XbrlRag_HTML_Canonical_Source.md`` §2).

The embedding *model* (BAAI/bge-m3, dense + sparse) is unchanged from the PDF
pipeline's :class:`~src.embeddings.bge_embedding.BGEEmbeddingGenerator` — only
the *text fed to the model* and the *payload written alongside each vector*
differ, because XBRL chunks are keyed by ``(cik, fiscal_year, section_path,
xbrl_fact_ids, xbrl_concepts)`` (from ``chunk_metadata`` /
``chunk_cross_links`` — see ``src/chunking/xbrl_html_base_chunking.py``)
rather than the PDF pipeline's ``(company, year, section, subsection,
table_json)`` (``src/chunking/chunk.py::ChunkMetadata``).

Per ``docs/design/XbrlRag_HTML_Canonical_Source.md`` §4 (the payload carries
``section_path``, ``html_char_start/end`` and the linked ``xbrl_fact_ids`` /
``xbrl_concepts``), :class:`XBRLChunkPayload` is the
single source of truth for what goes into the Qdrant payload — see
:class:`~src.vectorstores.xbrl_qdrant_vectordb_client.XBRLQdrantVectorDBClient`
for how it's written and filtered on.

This module does **no DB/Qdrant I/O** — it stays a pure data/embedding-input
builder, same as ``bge_embedding.py``. A Phase-10 pipeline (not part of this
change) is responsible for joining ``chunk_metadata`` ⋈ ``chunk_cross_links``
⋈ ``entity_master`` ⋈ ``filing_registry`` and constructing
:class:`XBRLChunkPayload` instances via :meth:`XBRLChunkPayload.from_prose_chunk`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from src.embeddings.bge_embedding import BGEEmbeddingGenerator
from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.chunking.xbrl_html_base_chunking import ProseChunk

logger = get_logger(__name__)


@dataclass
class XBRLChunkPayload:
    """One chunk's worth of metadata for the XBRL/HTML-canonical Qdrant
    payload.

    Field provenance (all joins performed by the Phase-10 caller, not here):

    ===================== =================================================
    Field                 Source
    ===================== =================================================
    chunk_id              ``chunk_metadata.chunk_id`` / ``ProseChunk.chunk_id``
    cik                   ``chunk_metadata.cik``
    accession_no          ``chunk_metadata.accession_no``
    fiscal_year           ``chunk_metadata.fiscal_year``
    fiscal_period         ``filing_registry.fiscal_period`` (default ``'FY'``)
    form_type             ``filing_registry.form_type``
    section_path          ``chunk_metadata.section_path`` / ``ProseChunk.section_path``
    text                  ``ProseChunk.text`` (Phase 7 — incl. table cell text)
    html_char_start/end   ``chunk_metadata.char_start/end`` / ``ProseChunk.html_char_start/end``
    page_num              ``chunk_metadata.page_num`` / ``ProseChunk.page_num``
    prev/next_chunk_id    ``chunk_metadata.prev/next_chunk_id`` / ``ProseChunk.prev/next_chunk_id``
    contains_table        ``ProseChunk.contains_table``
    source_tag            ``ProseChunk.source_tag``
    has_xbrl_facts        ``chunk_metadata.has_xbrl_facts`` (or ``bool(xbrl_fact_ids)``)
    is_financial_statement ``SectionTree`` node for ``section_path`` (``SectionNode.is_financial_statement``)
    ticker, company_name  ``entity_master`` (by ``cik``)
    period_end            ``filing_registry.period_end`` (ISO ``YYYY-MM-DD``)
    xbrl_fact_ids         ``chunk_cross_links.fact_id`` for this ``chunk_id``
    xbrl_concepts         ``fact`` ⋈ ``element`` ⋈ ``qname`` for those fact_ids, e.g. ``'us-gaap:Revenues'``
    xbrl_concept_labels   ``concept_labels.label`` for those concepts (optional, human-readable)
    ===================== =================================================
    """

    chunk_id: str
    cik: str
    accession_no: str
    fiscal_year: int
    form_type: str
    section_path: str
    text: str
    html_char_start: int
    html_char_end: int
    contains_table: bool
    source_tag: str
    has_xbrl_facts: bool
    fiscal_period: str = "FY"
    is_financial_statement: bool = False
    ticker: str | None = None
    company_name: str | None = None
    period_end: str | None = None
    page_num: int | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None
    xbrl_fact_ids: list[int] = field(default_factory=list)
    xbrl_concepts: list[str] = field(default_factory=list)
    xbrl_concept_labels: list[str] = field(default_factory=list)

    @property
    def section_path_ancestors(self) -> list[str]:
        """Every prefix of ``section_path`` (split on ``' > '``), including
        ``section_path`` itself.

        Stored as a Qdrant array payload field so a single
        ``MatchAny(any=["Part II > Item 7. MD&A"])`` filter on
        ``section_path_ancestors`` returns every chunk *at or below* that
        section — i.e. subtree filtering — without needing a prefix/LIKE
        operator (Qdrant keyword indexes only support exact match / "any
        of").
        """
        parts = self.section_path.split(" > ")
        return [" > ".join(parts[:i]) for i in range(1, len(parts) + 1)]

    @classmethod
    def from_prose_chunk(
        cls,
        chunk: ProseChunk,
        cik: str,
        accession_no: str,
        fiscal_year: int,
        form_type: str,
        *,
        fiscal_period: str = "FY",
        is_financial_statement: bool = False,
        ticker: str | None = None,
        company_name: str | None = None,
        period_end: str | None = None,
        has_xbrl_facts: bool | None = None,
        xbrl_fact_ids: Sequence[int] | None = None,
        xbrl_concepts: Sequence[str] | None = None,
        xbrl_concept_labels: Sequence[str] | None = None,
    ) -> XBRLChunkPayload:
        """Build from a Phase-7 :class:`ProseChunk`
        (``src.chunking.xbrl_html_base_chunking``) plus the per-filing /
        per-fact context the caller has already joined from
        ``chunk_metadata`` / ``chunk_cross_links`` / ``entity_master`` /
        ``filing_registry`` / ``fact`` ⋈ ``element`` ⋈ ``qname``.

        ``has_xbrl_facts`` defaults to ``bool(xbrl_fact_ids)`` when omitted —
        matching ``chunk_metadata.has_xbrl_facts``, which Phase 9 sets
        ``TRUE`` iff the chunk has at least one ``chunk_cross_links`` row.
        """
        fact_ids = list(xbrl_fact_ids or [])
        return cls(
            chunk_id=chunk.chunk_id,
            cik=cik,
            accession_no=accession_no,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            form_type=form_type,
            section_path=chunk.section_path,
            text=chunk.text,
            html_char_start=chunk.html_char_start,
            html_char_end=chunk.html_char_end,
            contains_table=chunk.contains_table,
            source_tag=chunk.source_tag,
            has_xbrl_facts=bool(fact_ids) if has_xbrl_facts is None else has_xbrl_facts,
            is_financial_statement=is_financial_statement,
            ticker=ticker,
            company_name=company_name,
            period_end=period_end,
            page_num=chunk.page_num,
            prev_chunk_id=chunk.prev_chunk_id,
            next_chunk_id=chunk.next_chunk_id,
            xbrl_fact_ids=fact_ids,
            xbrl_concepts=list(xbrl_concepts or []),
            xbrl_concept_labels=list(xbrl_concept_labels or []),
        )


class XBRLBGEEmbeddingGenerator(BGEEmbeddingGenerator):
    """XBRL-flavoured embedding generator.

    Dense/sparse encoding (:meth:`dense_embed_texts`,
    :meth:`sparse_embed_texts`, :meth:`embed_texts`) is inherited verbatim
    from :class:`~src.embeddings.bge_embedding.BGEEmbeddingGenerator` — same
    ``BAAI/bge-m3`` model. Only the *embedding-input text construction*
    differs from the PDF pipeline's
    ``load_to_vectordb_pipeline.py::_prepare_chunks_for_vectordb``.
    """

    @staticmethod
    def build_text_embedding_input(payload: XBRLChunkPayload) -> str:
        """Embedding input for the ``text_dense``/``text_sparse`` vectors
        (``TEXT_*`` collection).

        Mirrors the PDF flow's ``company + " " + year + " " + chunk.text``
        (``load_to_vectordb_pipeline.py:82``), but prefixes the HTML
        ``section_path`` too — XBRL chunks are section-keyed (no
        ``subsection``/page heuristics), so the section hierarchy is part of
        what should be semantically matched against narrative queries (e.g.
        "MD&A discussion of revenue").
        """
        company = payload.ticker or payload.company_name or payload.cik
        return f"{company} FY{payload.fiscal_year} {payload.section_path} :: {payload.text}"

    @staticmethod
    def build_metadata_embedding_input(payload: XBRLChunkPayload) -> str:
        """Embedding input for the dense-only ``METADATA_*`` collection.

        Mirrors the PDF flow's ``company + " " + year + " " + section + " "
        + subsection`` (``load_to_vectordb_pipeline.py:85``). XBRL chunks
        have no ``subsection``; in its place, append the cross-linked iXBRL
        concept names/labels (``xbrl_concept_labels`` if populated, else the
        bare namespace-stripped ``xbrl_concepts`` qnames) — this lets a
        metadata-only query like "revenue" or "stock-based compensation"
        match a table chunk even when those words don't appear in its
        section heading.
        """
        company = payload.ticker or payload.company_name or payload.cik
        parts = [company, f"FY{payload.fiscal_year}", payload.section_path]
        if payload.xbrl_concept_labels:
            parts.append(" ".join(sorted(set(payload.xbrl_concept_labels))))
        elif payload.xbrl_concepts:
            bare = sorted({c.split(":")[-1] for c in payload.xbrl_concepts})
            parts.append(" ".join(bare))
        return " ".join(p for p in parts if p)

    @staticmethod
    def prepare_embedding_inputs(
        payloads: Sequence[XBRLChunkPayload],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Build ``(chunk_id, text)`` lists ready for :meth:`embed_texts`
        (``TEXT_*`` — dense+sparse) and :meth:`dense_embed_texts`
        (``METADATA_*`` — dense only), in one pass over *payloads*.

        Static (pure string prep, no model) so the ingest pipeline can build
        inputs without instantiating the local BGE-M3 embedder — the actual
        encoding is delegated to whichever embedder ``RAGINFER_URL`` selects.
        """
        text_inputs: list[tuple[str, str]] = []
        metadata_inputs: list[tuple[str, str]] = []
        for p in payloads:
            text_inputs.append(
                (p.chunk_id, XBRLBGEEmbeddingGenerator.build_text_embedding_input(p))
            )
            metadata_inputs.append(
                (
                    p.chunk_id,
                    XBRLBGEEmbeddingGenerator.build_metadata_embedding_input(p),
                )
            )
        return text_inputs, metadata_inputs
