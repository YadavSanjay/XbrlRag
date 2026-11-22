"""Title/table-aware prose chunker for canonical iXBRL HTML filings, and
the populator for ``chunk_metadata`` (Phase 7 + Phase 8).

Implements the chunking and ``chunk_metadata`` steps of the HTML-canonical design
(``docs/design/XbrlRag_HTML_Canonical_Source.md`` §2, §5): chunks are byte ranges of
``canonical.htm`` carrying a ``section_path``, and content outside the ToC still gets a
real section node.

This is the HTML-canonical analogue of :mod:`src.chunking.base_chunking`
(:class:`~src.chunking.base_chunking.BaseChunking`).  The PDF chunker walks
``unstructured`` ``Element`` objects keyed by physical page; this one walks
the **DOM of ``canonical.htm``** directly, keyed by **byte offset**, using the
:class:`~src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser.SectionTree` (P4) to
assign every chunk a ``section_path`` deterministically — no page-number
heuristics, no "outside ToC" sentinel (see §6 of the source-of-truth doc).

Design — how byte ranges are derived
-------------------------------------------------------------------------
``canonical.htm`` is produced by ``lxml``'s own serializer (the
``HTMLCanonicalizer``), so a simple left-to-right regex scan for opening
tags (``<tagname``) yields byte offsets that line up **1:1, in document
order, with ``root.iter()``** — verified across Workiva, simple-anchor and
TSM-style filings alike (see :func:`_build_element_start_index`).  That gives
an O(N) ``element -> char_start`` index for *every* element, not just those
carrying ``id=``/``name=`` (the narrower index ``_build_id_offset_index``
builds for P4's anchor resolution).

Chunk ranges are then derived the same way :class:`SectionTree` derives
section ranges: each chunk's ``html_char_end`` is simply the **next**
chunk's ``html_char_start`` (or end-of-file for the last chunk).  This
guarantees the chunk stream is *contiguous and non-overlapping* — every byte
of the document belongs to exactly one chunk — which is exactly the
invariant Phase 9's interval-overlap cross-linker needs against
``fact_dom_position`` byte ranges.  (The very first chunk's ``char_start``
is additionally pinned to byte 0, folding the ``<head>``/``<ix:header>``
prefix — which contributes no chunk of its own, see below — into it; this
mirrors how ``SectionTree``'s ROOT node always starts at 0.)

A chunk's *content* is never split mid-element / mid-sentence the way the
PDF chunker splits accumulated ``unstructured`` text: each prose element is
atomic, and a chunk simply accumulates whole elements until
``CHUNK_SIZE_IN_CHARS`` is reached, then flushes — so what a chunk *contains*
always starts and ends on element boundaries, even though its *recorded
byte range* is widened to stay contiguous with its neighbours.

Title / table awareness
-------------------------------------------------------------------------
* **Title-aware**: a chunk is flushed whenever the containing
  ``section_path`` changes (binary-searched via ``SectionTree.find_section``
  on each element's ``char_start``).  Because :class:`SectionTree` already
  encodes the full heading hierarchy (ToC entries *and* heading-anchored
  nodes — see §6 of the source-of-truth doc), this is a strictly more
  reliable "title changed" signal than re-detecting ``<h1-h6>`` tags, which
  Workiva-authored filings do not consistently use.
* **Table-aware**: every ``<table>`` element gets its own atomic chunk
  (``contains_table=True``, ``source_tag='table'``); its descendant
  ``<tr>``/``<td>`` elements are *not* separately walked as prose (would
  double-count).  Per §4.2 of the source-of-truth doc, rich
  summarisation/VLM table processing remains a downstream concern
  (``sec_table_processor.py`` / ``table_vlm_summary_pipeline.py``); this
  chunker only needs a faithful, atomic textual chunk + byte range.
* **XBRL-machinery aware**: ``<ix:header>`` (the element wrapping *all* of a
  filing's hidden DEI facts plus ``<ix:references>``/``<ix:resources>``
  taxonomy/context/unit definitions — tens of KB of concept QNames and ISO
  dates with zero narrative content) is skipped *entirely*, like a table but
  yielding no chunk for it at all.  Left unhandled, the wrapping ``<div>``
  would be misclassified as one giant "leaf prose" block — observed
  first-hand on NVDA: a 20K-character pseudo-chunk of pure XBRL metadata.

Phase 9 — Cross-link (Section 3.11 of the design doc)
-------------------------------------------------------------------------
``populate_chunk_cross_links`` implements the **interval-overlap sweep**:
for every ``fact_dom_position`` row (every iXBRL-tagged fact's DOM byte
range — both financial *and* narrative, from Phase 5), bisect its
``char_start`` into the filing's chunk stream (sorted, contiguous,
non-overlapping — the Phase 7/8 invariant) and walk forward while chunk
``char_start`` values remain inside the fact's range, collecting every
chunk whose ``[char_start, char_end)`` overlaps it.  For each overlap,
upsert ``(fact_id, chunk_id, link_source='dom_range_overlap', confidence)``
where ``confidence`` is the fraction of the *fact's* byte range covered by
that chunk (``1.0`` for the overwhelming common case — a fact wholly inside
one chunk; split proportionally for the rare fact that spans a chunk
boundary — see "Filer used iXBRL inline tags that span across an HTML
element boundary" in §7 of the source-of-truth doc).  Every chunk that
receives at least one link gets ``chunk_metadata.has_xbrl_facts`` flipped to
``TRUE`` (idempotently — already-``TRUE`` rows are left untouched).

This phase needs **no DOM re-parse** — both inputs (``fact_dom_position``,
``chunk_metadata``) are already-populated DB tables in the same byte-offset
coordinate space, so the sweep is a pure, fast, re-runnable join.  Pure-prose
chunks correctly end up with zero links — "by design", per the spec; their
section-level *soft* links come later from ``mv_fact_prose_links`` (Layer 3,
Phase 11).

Tables populated (DDL: ``scripts/ddl/layer_0/``)
-------------------------------------------------------------------------
* ``chunk_metadata`` — one row per chunk: ``(chunk_id, cik, accession_no,
  fiscal_year, section_path, char_start, char_end, page_num, prev_chunk_id,
  next_chunk_id, has_xbrl_facts)``.  ``has_xbrl_facts`` is written ``FALSE``
  at population time and is **not** overwritten on re-run/upsert — Phase 9
  (``chunk_cross_links``) owns flipping it to ``TRUE`` once a chunk receives
  at least one fact link (see Section 3.11 of the design doc).
* ``chunk_cross_links`` — ``(fact_id, chunk_id, link_source, confidence)``,
  PK ``(fact_id, chunk_id)``.  Populated by Phase 9 only.

Usage (CLI)
-------------------------------------------------------------------------
::

    # Run both phases (8 then 9 — 9 depends on 8's writes) for every filing
    python -m src.chunking.xbrl_html_base_chunking

    # chunk_metadata only (Phase 7 + 8)
    python -m src.chunking.xbrl_html_base_chunking --chunk-metadata

    # chunk_cross_links only (Phase 9) — requires chunk_metadata AND
    # fact_dom_position already populated for the target filing(s)
    python -m src.chunking.xbrl_html_base_chunking --cross-links

    # Limit to specific accessions; dry run (parse + report only)
    python -m src.chunking.xbrl_html_base_chunking \\
        --accessions 0001045810-26-000021 --dry-run
"""

from __future__ import annotations

import argparse
import bisect
import collections
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lxml.etree as etree
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src import CHUNK_SIZE_IN_CHARS
from src.ingest.sec_edgar.storage import default_local_root, local_root
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    IX_NS,
    IXBRLPosition,
    SectionTree,
    build_section_tree,
    extract_ixbrl_positions,
)
from src.logging_config import get_logger

logger = get_logger(__name__)

# Same accession-number validity gate as dom_position_populator — only
# filings whose accession number survived Layer0Populator.patch_accession_numbers
# are eligible (anything else is a placeholder Arelle couldn't resolve).
_VALID_ACCESSION_RE = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"

# A stable namespace for chunk_id generation (uuid5) — arbitrary but fixed,
# so re-running the chunker for the same byte range always yields the same id.
_CHUNK_ID_NAMESPACE = uuid.UUID("c8f9a6f0-2f0a-4f2e-9b1a-6a6b9f3a9e10")

# ---------------------------------------------------------------------------
# DOM walk: which elements count as "leaf prose" vs. structural containers
# ---------------------------------------------------------------------------

# Block-level tags that *can* be chunked as a standalone prose unit, but only
# when they contain no nested block-level descendant (i.e. they are a leaf in
# the block hierarchy — inline children such as <span>/<a>/<ix:*> are fine and
# are folded into the unit's text via itertext()).  This avoids double-counting
# a <div> and the <p>s inside it, or a <table> and its <td>s.
_BLOCK_TAGS = frozenset({
    "div", "p", "li", "td", "th", "dd", "dt", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
})
_TABLE_TAG = "table"

# ``<ix:header>`` wraps the filing's *entire* XBRL machinery — hidden DEI
# facts, ``<ix:references>``/``<ix:resources>`` taxonomy/context/unit
# definitions — tens of KB of concept QNames, dimension members and ISO
# dates with **zero** narrative content.  None of its descendants are
# block-level (they're all inline ``ix:*``), so left unchecked a wrapping
# ``<div>`` would be misclassified as one giant "leaf prose" block (observed
# first-hand on NVDA: a 20K-character pseudo-chunk of pure XBRL metadata).
# It is therefore skipped *entirely* — like a table, but yielding nothing.
_IX_HEADER_TAG = f"{{{IX_NS}}}header"
_NONPROSE_SKIP_TAGS = frozenset({_IX_HEADER_TAG})

_BLOCK_AND_TABLE_TAGS = _BLOCK_TAGS | {_TABLE_TAG}


def _local_name(elem: etree._Element) -> str:
    return elem.tag.split("}")[-1].lower()


def _has_block_descendant(elem: etree._Element) -> bool:
    """True if *elem* has any descendant whose local tag is block/table-level
    — or is an ``<ix:header>`` (see :data:`_NONPROSE_SKIP_TAGS`).

    Used to decide whether *elem* is a chunkable "leaf" — e.g. an outer
    ``<div>`` wrapping several ``<p>``s is *not* a leaf (its ``<p>``
    children are chunked individually instead); a ``<p>`` containing only
    ``<span>``/``<a>``/``<ix:nonNumeric>`` *is* a leaf.  Flagging
    ``<ix:header>`` here forces the walker to descend into (and then skip)
    it individually, rather than swallowing it whole into a "prose" leaf.
    """
    for desc in elem.iterdescendants():
        if not isinstance(desc.tag, str):
            continue
        if desc.tag in _NONPROSE_SKIP_TAGS or _local_name(desc) in _BLOCK_AND_TABLE_TAGS:
            return True
    return False


def _block_text(elem: etree._Element) -> str:
    """Normalised prose text of a leaf block element (iXBRL fact *values*
    are inline via ``itertext()``; the ``<ix:*>`` markup itself is not)."""
    parts = [t.strip() for t in elem.itertext() if t and t.strip()]
    return " ".join(parts).strip()


def _table_text(table_elem: etree._Element) -> str:
    """Row/cell textual rendering of a ``<table>`` — ``" | "``-joined cells,
    newline-joined rows.  A faithful, atomic textual stand-in; richer
    summarisation is a downstream concern (``sec_table_processor``)."""
    rows: list[str] = []
    for tr in table_elem.iter():
        if not isinstance(tr.tag, str) or _local_name(tr) != "tr":
            continue
        cells = []
        for cell in tr:
            if not isinstance(cell.tag, str) or _local_name(cell) not in ("td", "th"):
                continue
            cell_text = _block_text(cell)
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _element_byte_starts(raw_bytes: bytes, elems: list[etree._Element]) -> list[int]:
    """Pair *elems* (``root.iter()``, in document order, string tags only)
    with the byte offset of each one's opening ``'<'``.

    A left-to-right regex scan for start-tag openings (``<tagname`` —
    explicitly excluding end tags ``</…``, comments ``<!--``, and processing
    instructions ``<?…``) produces a sequence that lines up **1:1, in the
    same order, with ``root.iter()``** for canonical HTML produced by
    ``HTMLCanonicalizer`` (lxml's serializer emits no comments and exactly
    one leading ``<?xml …?>`` PI that is *not* part of the tree).  Verified
    across Workiva (NVDA, AMD), simple-anchor (MSFT) and TSM 20-F filings —
    zero mismatches in 100K+ element traversals.

    Each pair is sanity-checked (local tag names must match); on the rare
    mismatch a ``ValueError`` is raised — better a loud failure than silently
    mis-attributed byte ranges feeding Phase 9's cross-link sweep.

    Note: deliberately returns a plain list paired *positionally* with
    *elems* (not a ``{id(elem): offset}`` dict) — lxml mints ephemeral Python
    proxy objects on each traversal, so ``id()`` is **not** a stable key
    across two separate ``root.iter()``/``iterwalk()`` passes (observed
    first-hand: produced duplicate/zero-length byte ranges).  Pairing
    positionally within a *single* traversal sidesteps that trap entirely.
    """
    import re as _re
    matches = _re.finditer(rb"<([a-zA-Z][\w:.\-]*)", raw_bytes)

    offsets: list[int] = []
    for elem, m in zip(elems, matches):
        tag_in_bytes = m.group(1).decode("ascii", "replace").split(":")[-1].lower()
        if tag_in_bytes != _local_name(elem):
            raise ValueError(
                f"element/byte-scan misalignment: expected <{_local_name(elem)}>, "
                f"found <{m.group(1).decode('ascii','replace')}> at byte {m.start()}"
            )
        offsets.append(m.start())
    if len(offsets) != len(elems):
        raise ValueError(
            f"element/byte-scan count mismatch: {len(elems)} elements vs "
            f"{len(offsets)} start-tag matches"
        )
    return offsets


# ---------------------------------------------------------------------------
# DOM walk: yield (kind, element, char_start) for chunkable units, in order
# ---------------------------------------------------------------------------

def _iter_chunkable_blocks(
    raw_bytes: bytes, root: etree._Element
) -> Iterator[tuple[str, etree._Element, int]]:
    """Yield ``(kind, elem, char_start)`` — ``kind`` is ``"table"`` for every
    ``<table>`` and ``"prose"`` for every leaf prose block — in document
    order, without descending into a table's ``<tr>``/``<td>`` subtree
    (captured atomically) or a leaf prose element's inline descendants
    (captured via ``itertext()``).

    Single combined pass: ``root.iter()`` is pre-order/depth-first, so for
    any element at index *i*, its entire subtree occupies the contiguous
    range ``[i, i + subtree_size)`` in that same sequence — "skip a subtree"
    is therefore just "advance the cursor by its size", with byte offsets
    paired positionally by :func:`_element_byte_starts` from this *one*
    traversal (no second pass, no ``id()``-keyed lookups).
    """
    elems = [e for e in root.iter() if isinstance(e.tag, str)]
    starts = _element_byte_starts(raw_bytes, elems)

    n = len(elems)
    i = 0
    while i < n:
        elem = elems[i]

        if elem.tag in _NONPROSE_SKIP_TAGS:
            # <ix:header> — pure XBRL machinery, zero narrative content.
            # Skip the whole subtree, yielding nothing for it at all.
            i += sum(1 for _ in elem.iter() if isinstance(_.tag, str))
            continue

        local = _local_name(elem)

        if local == _TABLE_TAG:
            yield ("table", elem, starts[i])
            i += sum(1 for _ in elem.iter() if isinstance(_.tag, str))
            continue

        if local in _BLOCK_TAGS and not _has_block_descendant(elem):
            yield ("prose", elem, starts[i])
            i += sum(1 for _ in elem.iter() if isinstance(_.tag, str))
            continue

        # Structural container — descend into its children individually.
        i += 1


# ---------------------------------------------------------------------------
# Output data model
# ---------------------------------------------------------------------------

@dataclass
class ProseChunk:
    """One title/table-aware chunk over canonical HTML — the HTML-canonical
    analogue of :class:`~src.chunking.chunk.Chunk`.

    ``html_char_start``/``html_char_end`` are byte offsets into
    ``canonical.htm`` in the *same coordinate space* as
    ``fact_dom_position.char_start/char_end`` and
    ``SectionNode.char_start/char_end`` — required for Phase 9's
    interval-overlap cross-link sweep.
    """

    chunk_id: str
    text: str
    section_path: str
    html_char_start: int
    html_char_end: int
    contains_table: bool
    source_tag: str
    page_num: int | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None


def _make_chunk_id(accession_no: str, char_start: int, char_end: int) -> str:
    """Deterministic chunk id from ``(accession_no, byte range)``.

    Keying on the byte range (rather than text content, as
    :class:`~src.chunking.chunk.Chunk` does) makes re-runs idempotent even if
    upstream text-normalisation tweaks slightly change extracted text —
    matching the "every write is upsert-keyed … safely re-runnable" model
    (Section 3.16 of the design doc).
    """
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, f"{accession_no}:{char_start}:{char_end}"))


# ---------------------------------------------------------------------------
# Phase 9 — interval-overlap sweep (fact_dom_position ⋈ chunk_metadata)
# ---------------------------------------------------------------------------

# (fact_id, char_start, char_end) / (chunk_id, char_start, char_end)
FactRange = tuple[int, int, int]
ChunkRange = tuple[str, int, int]


def _sweep_fact_chunk_overlaps(
    facts: list[FactRange],
    chunks: list[ChunkRange],
) -> tuple[list[dict], set[str]]:
    """Bisect-driven interval-overlap sweep — the literal Phase 9 algorithm:
    "for each fact_dom_position row, find chunks whose [char_start, char_end]
    overlaps the fact's range (bisect on sorted chunk starts)".

    Both inputs must be pre-sorted by ``char_start`` (the natural order of a
    ``... ORDER BY char_start`` query).  Because the chunk stream is
    contiguous and non-overlapping (Phase 7/8 invariant — every byte belongs
    to exactly one chunk), the chunk containing a fact's *first* byte is
    located in O(log N) via ``bisect_right``, and every subsequent
    overlapping chunk is then necessarily contiguous — so a linear forward
    walk "while chunk.char_start < fact.char_end" finds them all with no
    further searching.  Total cost: O(F log C + F + C).

    Returns
    -------
    (rows, flagged_chunk_ids)
        ``rows`` — upsert-ready dicts ``{fact_id, chunk_id, confidence}``.
        ``confidence`` is the fraction of the **fact's** byte range covered
        by that chunk: ``1.0`` for the overwhelmingly common case (a fact
        wholly contained in one chunk); split proportionally to overlap
        length for the rare fact that spans a chunk boundary (inline iXBRL
        tags crossing an HTML element boundary — see §7 of the
        source-of-truth doc).  Confidences for one ``fact_id`` therefore sum
        to (very close to) ``1.0``.
        ``flagged_chunk_ids`` — the set of distinct ``chunk_id``s that
        received at least one link (candidates for
        ``chunk_metadata.has_xbrl_facts = TRUE``).
    """
    chunk_starts = [c[1] for c in chunks]
    n_chunks = len(chunks)

    rows: list[dict] = []
    flagged: set[str] = set()

    for fact_id, f_start, f_end in facts:
        if f_start is None or f_end is None or f_end <= f_start or n_chunks == 0:
            continue

        # First chunk whose range could contain f_start: the rightmost chunk
        # with char_start <= f_start (bisect_right - 1; clamped to 0 — every
        # fact's char_start is >= 0 and chunks[0].char_start == 0).
        idx = max(0, bisect.bisect_right(chunk_starts, f_start) - 1)

        i = idx
        fact_len = f_end - f_start
        while i < n_chunks and chunks[i][1] < f_end:
            chunk_id, c_start, c_end = chunks[i]
            overlap_start = max(f_start, c_start)
            overlap_end   = min(f_end, c_end)
            overlap_len   = overlap_end - overlap_start
            if overlap_len > 0:
                rows.append({
                    "fact_id":   fact_id,
                    "chunk_id":  chunk_id,
                    "confidence": round(overlap_len / fact_len, 4),
                })
                flagged.add(chunk_id)
            i += 1

    return rows, flagged


# ---------------------------------------------------------------------------
# Phase 7 — chunker
# ---------------------------------------------------------------------------

@dataclass
class _PendingChunk:
    char_start: int
    text: str
    contains_table: bool
    source_tag: str
    section_path: str


class XBRLHTMLChunker:
    """Title/table-aware chunker over canonical iXBRL HTML (Phase 7).

    The HTML-canonical analogue of
    :class:`~src.chunking.base_chunking.BaseChunking`: same
    ``CHUNK_SIZE_IN_CHARS`` budget and table-gets-its-own-chunk philosophy,
    but driven by DOM byte offsets + :class:`SectionTree` instead of
    ``unstructured`` ``Element``/physical-page bookkeeping.
    """

    def __init__(self, chunk_size_chars: int = CHUNK_SIZE_IN_CHARS) -> None:
        self.chunk_size_chars = chunk_size_chars

    # ------------------------------------------------------------------
    def chunk_filing(
        self,
        canonical_path: Path | str,
        accession_no: str,
        section_tree: SectionTree | None = None,
        ixbrl_positions: list[IXBRLPosition] | None = None,
    ) -> list[ProseChunk]:
        """Chunk one filing's ``canonical.htm`` into an ordered list of
        :class:`ProseChunk`.

        Parameters
        ----------
        canonical_path:
            Path to ``canonical.htm`` (produced by ``HTMLCanonicalizer``).
        accession_no:
            EDGAR accession number — seeds deterministic ``chunk_id``s and is
            passed through to ``build_section_tree`` if it must be built here.
        section_tree, ixbrl_positions:
            Optionally pre-computed (shared-pass optimisation, mirroring
            ``DOMPositionPopulator.populate_all`` / ``_run_combined``) — if
            omitted, both are derived internally.  ``ixbrl_positions`` is used
            only for best-effort ``page_num`` derivation (today always
            ``None`` pending the HTML→PDF renderer — see
            ``IXBRLPosition.page_num`` — but the wiring is forward-compatible).

        Returns
        -------
        list of ProseChunk
            Sorted by ``html_char_start``; contiguous and non-overlapping
            (``chunks[i].html_char_end == chunks[i+1].html_char_start``);
            ``prev_chunk_id``/``next_chunk_id`` linked in document order.
        """
        canonical_path = Path(canonical_path).resolve()
        raw_bytes = canonical_path.read_bytes()

        tree = etree.parse(str(canonical_path))
        root = tree.getroot()

        if section_tree is None:
            section_tree = build_section_tree(canonical_path, accession_no)

        pending: list[_PendingChunk] = []
        accum_parts: list[str] = []
        accum_start: int | None = None
        accum_section: str | None = None
        accum_source_tag: str | None = None

        def flush_accum() -> None:
            nonlocal accum_parts, accum_start, accum_section, accum_source_tag
            if accum_parts and accum_start is not None:
                text = " ".join(accum_parts).strip()
                if text:
                    pending.append(_PendingChunk(
                        char_start=accum_start,
                        text=text,
                        contains_table=False,
                        source_tag=accum_source_tag or "p",
                        section_path=accum_section or "ROOT",
                    ))
            accum_parts, accum_start, accum_section, accum_source_tag = [], None, None, None

        for kind, elem, char_start in _iter_chunkable_blocks(raw_bytes, root):
            section_path = section_tree.find_section(char_start) or "ROOT"

            # Title-aware: a section-boundary crossing always flushes —
            # SectionTree nodes already encode the heading hierarchy
            # (ToC entries + heading-anchored nodes; see §6 of the
            # source-of-truth doc), so this *is* "title changed".
            if accum_section is not None and section_path != accum_section:
                flush_accum()

            if kind == "table":
                flush_accum()
                table_text = _table_text(elem)
                if table_text:
                    pending.append(_PendingChunk(
                        char_start=char_start,
                        text=table_text,
                        contains_table=True,
                        source_tag=_TABLE_TAG,
                        section_path=section_path,
                    ))
                continue

            # kind == "prose"
            text = _block_text(elem)
            if not text:
                continue

            if accum_start is None:
                accum_start = char_start
                accum_section = section_path
                accum_source_tag = _local_name(elem)
            accum_parts.append(text)

            if sum(len(p) for p in accum_parts) >= self.chunk_size_chars:
                flush_accum()

        flush_accum()

        return self._finalize(pending, accession_no, len(raw_bytes), ixbrl_positions)

    # ------------------------------------------------------------------
    def _finalize(
        self,
        pending: list[_PendingChunk],
        accession_no: str,
        doc_end: int,
        ixbrl_positions: list[IXBRLPosition] | None,
    ) -> list[ProseChunk]:
        """Assign contiguous ``char_end``s, deterministic ids, ``page_num``
        and prev/next links — turning raw ``_PendingChunk``s into the final
        ordered :class:`ProseChunk` stream.

        Two adjustments guarantee **full, gap-free document coverage**
        (every byte in ``[0, doc_end)`` belongs to exactly one chunk —
        the same invariant :class:`SectionTree` upholds via its ROOT node,
        and the one Phase 9's interval-overlap sweep depends on):

        * ``char_end[i] = char_start[i+1]`` (or ``doc_end`` for the last) —
          any gap between two emitted blocks (whitespace, structural
          containers with no extractable text, skipped ``<ix:header>``
          machinery, …) is absorbed into the *preceding* chunk's range.
        * the **first** chunk's ``char_start`` is forced to ``0`` — there is
          no "preceding chunk" to absorb the ``<head>``/``<ix:header>``
          prefix into, so it is folded into the first real content chunk
          instead (mirrors how ``SectionTree``'s ROOT always starts at 0).
        """
        pending.sort(key=lambda p: p.char_start)

        chunks: list[ProseChunk] = []
        for i, p in enumerate(pending):
            char_start = 0 if i == 0 else p.char_start
            char_end = pending[i + 1].char_start if i + 1 < len(pending) else doc_end
            chunk_id = _make_chunk_id(accession_no, char_start, char_end)
            chunks.append(ProseChunk(
                chunk_id=chunk_id,
                text=p.text,
                section_path=p.section_path,
                html_char_start=char_start,
                html_char_end=char_end,
                contains_table=p.contains_table,
                source_tag=p.source_tag,
                page_num=self._derive_page_num(char_start, char_end, ixbrl_positions),
            ))

        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.prev_chunk_id = chunks[i - 1].chunk_id
                chunks[i - 1].next_chunk_id = chunk.chunk_id

        return chunks

    @staticmethod
    def _derive_page_num(
        char_start: int,
        char_end: int,
        ixbrl_positions: list[IXBRLPosition] | None,
    ) -> int | None:
        """Best-effort ``page_num`` — the most common non-``None`` page
        number among ``IXBRLPosition``s whose byte range overlaps this
        chunk's range.

        ``IXBRLPosition.page_num`` is populated by the (not-yet-built)
        HTML→PDF renderer (see its docstring: "for citation UI only"), so
        today every position has ``page_num=None`` and this always returns
        ``None``.  The lookup is wired up regardless so chunk population
        becomes immediately page-aware once the renderer ships — no
        ``chunk_metadata`` re-derivation needed (page_num upserts via
        ``COALESCE`` — see ``_upsert_chunk_metadata``).
        """
        if not ixbrl_positions:
            return None
        counts: collections.Counter = collections.Counter(
            pos.page_num
            for pos in ixbrl_positions
            if pos.page_num is not None and pos.char_start < char_end and pos.char_end > char_start
        )
        if not counts:
            return None
        return counts.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Phase 8 — populate chunk_metadata
# ---------------------------------------------------------------------------

@dataclass
class ChunkTarget:
    """One filing to chunk — resolved registry row + canonical HTML path."""

    accession_no: str
    cik: str
    fiscal_year: int
    form_type: str
    canonical_path: Path


@dataclass
class ChunkingStats:
    """Aggregated counters for one ``populate_chunk_metadata`` run."""

    targets_found: int = 0
    targets_processed: int = 0
    targets_skipped_no_sections: int = 0
    chunks_produced: int = 0
    prose_chunks: int = 0
    table_chunks: int = 0
    rows_written: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 9 — populate chunk_cross_links
# ---------------------------------------------------------------------------

@dataclass
class CrossLinkTarget:
    """One filing to cross-link — registry row identified by accession_no.

    Unlike :class:`ChunkTarget` / ``FilingTarget`` (``dom_position_populator``),
    no ``canonical_path``/``accession_id`` is needed: Phase 9 is a pure
    DB-side join over already-populated ``fact_dom_position`` and
    ``chunk_metadata`` rows — no DOM re-parse.
    """

    accession_no: str
    cik: str
    fiscal_year: int
    form_type: str


@dataclass
class CrossLinkStats:
    """Aggregated counters for one ``populate_chunk_cross_links`` run."""

    targets_found: int = 0
    targets_processed: int = 0
    targets_skipped_no_facts: int = 0
    targets_skipped_no_chunks: int = 0
    facts_considered: int = 0
    links_written: int = 0
    chunks_flagged: int = 0
    rows_written: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class XBRLHTMLChunkPopulator:
    """Populate ``chunk_metadata`` (Phase 8) and ``chunk_cross_links``
    (Phase 9) from canonical iXBRL HTML / already-populated Layer-0 tables.

    Parameters mirror ``DOMPositionPopulator`` exactly — same root layout,
    same accession-discovery shape, same canonical-path resolution — so the
    two populators can be driven from the same CLI/orchestration conventions.
    Each table can be (re)populated independently — see
    :meth:`populate_chunk_metadata` / :meth:`populate_chunk_cross_links` —
    though Phase 9 naturally depends on Phase 8's writes (it reads
    ``chunk_metadata``) and on Phase 5's (``fact_dom_position``).
    """

    DEFAULT_SEC_ROOT = default_local_root("sec_edgar")
    DEFAULT_NORMALIZED_ROOT = default_local_root("normalized")

    def __init__(
        self,
        sec_edgar_root: str | Path = DEFAULT_SEC_ROOT,
        normalized_root: str | Path = DEFAULT_NORMALIZED_ROOT,
        db_conn_params: dict | None = None,
        chunk_size_chars: int = CHUNK_SIZE_IN_CHARS,
    ) -> None:
        self.sec_edgar_root  = Path(sec_edgar_root).expanduser().resolve()
        self.normalized_root = Path(normalized_root).expanduser().resolve()
        self._db_conn_params = db_conn_params or {}
        self._chunker = XBRLHTMLChunker(chunk_size_chars=chunk_size_chars)
        logger.info(
            "XBRLHTMLChunkPopulator  sec_edgar_root=%s  normalized_root=%s",
            self.sec_edgar_root, self.normalized_root,
        )

    # ------------------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        sec_edgar_root: str | Path | None = None,
        normalized_root: str | Path | None = None,
        dotenv_path: str | Path | None = None,
    ) -> XBRLHTMLChunkPopulator:
        """Construct from environment variables loaded from ``.env`` —
        reads ``XBRL_DB_HOST/PORT/NAME/USER/PASSWORD`` and ``SEC_EDGAR_ROOT``,
        same as ``DOMPositionPopulator.from_env``."""
        load_dotenv(dotenv_path)
        conn_params = {
            "host":     os.environ.get("XBRL_DB_HOST", "localhost"),
            "port":     int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname":   os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user":     os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        root = sec_edgar_root or local_root("sec_edgar")
        return cls(
            sec_edgar_root=root,
            normalized_root=normalized_root or local_root("normalized"),
            db_conn_params=conn_params,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate_chunk_metadata(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> ChunkingStats:
        """Populate ``chunk_metadata`` for every eligible filing (Phase 8).

        Per filing: build the :class:`SectionTree`, run the title/table-aware
        chunker (Phase 7), and upsert one row per chunk — ``(chunk_id, cik,
        accession_no, fiscal_year, section_path, char_start, char_end,
        page_num, prev_chunk_id, next_chunk_id, has_xbrl_facts=FALSE)``.

        Parameters
        ----------
        accession_nos:
            Limit to specific EDGAR accession numbers. ``None`` processes
            every non-superseded filing with a valid accession number and a
            resolvable ``canonical.htm``.
        dry_run:
            Parse and chunk without writing to the database.

        Returns
        -------
        ChunkingStats
        """
        stats = ChunkingStats(dry_run=dry_run)
        with psycopg2.connect(**self._db_conn_params) as conn:
            targets = self._discover_targets(conn, accession_nos)
            stats.targets_found = len(targets)
            logger.info("chunk_metadata: %d target filing(s) discovered.", len(targets))

            for target in targets:
                try:
                    self._run_one(conn, target, dry_run, stats)
                except Exception:
                    msg = f"{target.accession_no}: unhandled error"
                    logger.exception("chunk_metadata failed for %s", target.accession_no)
                    stats.errors.append(msg)

            if not dry_run:
                conn.commit()

        self._log_summary(stats)
        return stats

    # ------------------------------------------------------------------
    # Discovery — identical shape to DOMPositionPopulator._discover_targets
    # ------------------------------------------------------------------

    def _discover_targets(
        self,
        conn: psycopg2.extensions.connection,
        accession_nos: list[str] | None,
    ) -> list[ChunkTarget]:
        """Resolve filings to process: ``filing_registry`` ⋈ ``accession`` ⋈ canonical.htm.

        ``chunk_metadata`` needs no ``accession_id`` (unlike ``fact_dom_position``
        / ``section_assignment``, which join back to Arelle's ``fact`` table),
        so this query is a thin projection of the same eligibility rule used
        by ``DOMPositionPopulator._discover_targets``: non-superseded,
        syntactically valid accession number, resolvable canonical artifact.
        """
        sql = f"""
            SELECT a.filing_accession_number, a.entry_url,
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

        targets: list[ChunkTarget] = []
        for accession_no, entry_url, cik, fiscal_year, form_type in rows:
            canon = self._resolve_canonical_path(entry_url)
            if canon is None or not canon.exists():
                logger.warning(
                    "%s: canonical.htm not found (entry_url=%r -> %s) — skipping. "
                    "Run html_canonicalizer for this filing first.",
                    accession_no, entry_url, canon,
                )
                continue
            targets.append(ChunkTarget(
                accession_no=accession_no,
                cik=cik,
                fiscal_year=fiscal_year,
                form_type=form_type,
                canonical_path=canon,
            ))
        return targets

    def _resolve_canonical_path(self, entry_url: str | None) -> Path | None:
        """Mirror ``DOMPositionPopulator._resolve_canonical_path`` — map
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
    # Row building + upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_metadata_rows(target: ChunkTarget, chunks: list[ProseChunk]) -> list[dict]:
        return [
            {
                "chunk_id":      c.chunk_id,
                "cik":           target.cik,
                "accession_no":  target.accession_no,
                "fiscal_year":   target.fiscal_year,
                "section_path":  c.section_path,
                "char_start":    c.html_char_start,
                "char_end":      c.html_char_end,
                "page_num":      c.page_num,
                "prev_chunk_id": c.prev_chunk_id,
                "next_chunk_id": c.next_chunk_id,
            }
            for c in chunks
        ]

    def _upsert_chunk_metadata(self, conn: psycopg2.extensions.connection, rows: list[dict]) -> int:
        sql = """
            INSERT INTO chunk_metadata
                (chunk_id, cik, accession_no, fiscal_year, section_path,
                 char_start, char_end, page_num, prev_chunk_id, next_chunk_id,
                 has_xbrl_facts)
            VALUES
                (%(chunk_id)s, %(cik)s, %(accession_no)s, %(fiscal_year)s, %(section_path)s,
                 %(char_start)s, %(char_end)s, %(page_num)s, %(prev_chunk_id)s, %(next_chunk_id)s,
                 FALSE)
            ON CONFLICT (chunk_id) DO UPDATE SET
                cik           = EXCLUDED.cik,
                accession_no  = EXCLUDED.accession_no,
                fiscal_year   = EXCLUDED.fiscal_year,
                section_path  = EXCLUDED.section_path,
                char_start    = EXCLUDED.char_start,
                char_end      = EXCLUDED.char_end,
                page_num      = COALESCE(EXCLUDED.page_num, chunk_metadata.page_num),
                prev_chunk_id = EXCLUDED.prev_chunk_id,
                next_chunk_id = EXCLUDED.next_chunk_id
            -- has_xbrl_facts intentionally NOT in the SET clause: Phase 9
            -- (chunk_cross_links) owns it post-population; re-running Phase 8
            -- must not clobber a TRUE flag back to FALSE (idempotency — see
            -- Section 3.16 of the design doc).
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    def _run_one(
        self,
        conn: psycopg2.extensions.connection,
        target: ChunkTarget,
        dry_run: bool,
        stats: ChunkingStats,
    ) -> None:
        section_tree = build_section_tree(target.canonical_path, target.accession_no)
        if section_tree.section_count == 0:
            stats.targets_skipped_no_sections += 1
            logger.warning("%s: empty SectionTree — skipping.", target.accession_no)
            return

        ixbrl_positions = extract_ixbrl_positions(target.canonical_path)
        chunks = self._chunker.chunk_filing(
            target.canonical_path, target.accession_no,
            section_tree=section_tree, ixbrl_positions=ixbrl_positions,
        )
        if not chunks:
            stats.warnings.append(f"{target.accession_no}: chunker produced 0 chunks")
            return

        stats.chunks_produced += len(chunks)
        stats.prose_chunks += sum(1 for c in chunks if not c.contains_table)
        stats.table_chunks += sum(1 for c in chunks if c.contains_table)
        stats.targets_processed += 1

        rows = self._chunk_metadata_rows(target, chunks)

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would upsert %d chunk_metadata row(s) "
                "(%d prose, %d table).",
                target.accession_no, len(rows),
                sum(1 for c in chunks if not c.contains_table),
                sum(1 for c in chunks if c.contains_table),
            )
            return

        stats.rows_written += self._upsert_chunk_metadata(conn, rows)
        logger.info("%s: upserted %d chunk_metadata row(s).", target.accession_no, len(rows))

    # ------------------------------------------------------------------
    def _log_summary(self, stats: ChunkingStats) -> None:
        prefix = "[DRY RUN] " if stats.dry_run else ""
        logger.info(
            "%schunk_metadata summary: targets=%d processed=%d skipped_no_sections=%d | "
            "chunks: total=%d prose=%d table=%d | rows_written=%d errors=%d",
            prefix,
            stats.targets_found, stats.targets_processed, stats.targets_skipped_no_sections,
            stats.chunks_produced, stats.prose_chunks, stats.table_chunks,
            stats.rows_written, len(stats.errors),
        )
        for w in stats.warnings:
            logger.warning("  %s", w)
        for e in stats.errors:
            logger.error("  %s", e)

    # ==================================================================
    # Public API — Phase 9: chunk_cross_links
    # ==================================================================

    def populate_chunk_cross_links(
        self,
        accession_nos: list[str] | None = None,
        dry_run: bool = False,
    ) -> CrossLinkStats:
        """Populate ``chunk_cross_links`` via interval-overlap sweep (Phase 9).

        Per filing: load ``fact_dom_position`` (every tagged fact's DOM byte
        range) and ``chunk_metadata`` (the contiguous chunk stream) — both
        ``ORDER BY char_start`` — run the bisect sweep
        (:func:`_sweep_fact_chunk_overlaps`), upsert ``(fact_id, chunk_id,
        link_source='dom_range_overlap', confidence)``, and flip
        ``chunk_metadata.has_xbrl_facts = TRUE`` for every chunk that
        received at least one link.

        Requires :meth:`populate_fact_dom_position` (Phase 5,
        ``DOMPositionPopulator``) and :meth:`populate_chunk_metadata`
        (Phase 8, this class) to have already run for the target filing(s)
        — filings missing either are skipped with a ``WARNING``.

        Parameters
        ----------
        accession_nos:
            Limit to specific EDGAR accession numbers. ``None`` processes
            every non-superseded filing that has rows in *both*
            ``fact_dom_position`` and ``chunk_metadata``.
        dry_run:
            Compute the sweep and report without writing to the database.

        Returns
        -------
        CrossLinkStats
        """
        stats = CrossLinkStats(dry_run=dry_run)
        with psycopg2.connect(**self._db_conn_params) as conn:
            targets = self._discover_cross_link_targets(conn, accession_nos)
            stats.targets_found = len(targets)
            logger.info("chunk_cross_links: %d target filing(s) discovered.", len(targets))

            for target in targets:
                try:
                    self._run_cross_link(conn, target, dry_run, stats)
                except Exception:
                    msg = f"{target.accession_no}: unhandled error"
                    logger.exception("chunk_cross_links failed for %s", target.accession_no)
                    stats.errors.append(msg)

            if not dry_run:
                conn.commit()

        self._log_cross_link_summary(stats)
        return stats

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_cross_link_targets(
        self,
        conn: psycopg2.extensions.connection,
        accession_nos: list[str] | None,
    ) -> list[CrossLinkTarget]:
        """Resolve filings eligible for cross-linking: non-superseded, valid
        accession number, **and** at least one row in both
        ``fact_dom_position`` and ``chunk_metadata`` (Phases 5 and 8 must
        already have run — no ``canonical.htm`` access needed here at all)."""
        sql = f"""
            SELECT DISTINCT fr.accession_no, fr.cik, fr.fiscal_year, fr.form_type
              FROM filing_registry fr
             WHERE fr.superseded_by IS NULL
               AND fr.accession_no ~ '{_VALID_ACCESSION_RE}'
               AND EXISTS (SELECT 1 FROM fact_dom_position fdp
                            WHERE fdp.accession_no = fr.accession_no)
               AND EXISTS (SELECT 1 FROM chunk_metadata cm
                            WHERE cm.accession_no = fr.accession_no)
        """
        params: tuple = ()
        if accession_nos:
            sql += " AND fr.accession_no = ANY(%s)"
            params = (list(accession_nos),)
        sql += " ORDER BY fr.cik, fr.fiscal_year"

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [
            CrossLinkTarget(accession_no=acc, cik=cik, fiscal_year=fy, form_type=form)
            for acc, cik, fy, form in rows
        ]

    # ------------------------------------------------------------------
    # Sweep input loading + upsert
    # ------------------------------------------------------------------

    @staticmethod
    def _load_fact_ranges(conn: psycopg2.extensions.connection, accession_no: str) -> list[FactRange]:
        sql = """
            SELECT fact_id, char_start, char_end
              FROM fact_dom_position
             WHERE accession_no = %s
               AND char_start IS NOT NULL
               AND char_end   IS NOT NULL
             ORDER BY char_start
        """
        with conn.cursor() as cur:
            cur.execute(sql, (accession_no,))
            return [(fact_id, cs, ce) for fact_id, cs, ce in cur.fetchall()]

    @staticmethod
    def _load_chunk_ranges(conn: psycopg2.extensions.connection, accession_no: str) -> list[ChunkRange]:
        sql = """
            SELECT chunk_id, char_start, char_end
              FROM chunk_metadata
             WHERE accession_no = %s
             ORDER BY char_start
        """
        with conn.cursor() as cur:
            cur.execute(sql, (accession_no,))
            return [(chunk_id, cs, ce) for chunk_id, cs, ce in cur.fetchall()]

    def _upsert_chunk_cross_links(self, conn: psycopg2.extensions.connection, rows: list[dict]) -> int:
        sql = """
            INSERT INTO chunk_cross_links (fact_id, chunk_id, link_source, confidence)
            VALUES (%(fact_id)s, %(chunk_id)s, 'dom_range_overlap', %(confidence)s)
            ON CONFLICT (fact_id, chunk_id) DO UPDATE SET
                link_source = EXCLUDED.link_source,
                confidence  = EXCLUDED.confidence
        """
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
        return len(rows)

    @staticmethod
    def _flag_chunks_with_facts(conn: psycopg2.extensions.connection, chunk_ids: set[str]) -> None:
        """Flip ``has_xbrl_facts`` to ``TRUE`` for every linked chunk —
        idempotent (``IS DISTINCT FROM TRUE`` skips rows already flagged, so
        re-runs touch nothing and never "unflag" via a stale empty sweep)."""
        if not chunk_ids:
            return
        sql = """
            UPDATE chunk_metadata
               SET has_xbrl_facts = TRUE
             WHERE chunk_id = ANY(%s)
               AND has_xbrl_facts IS DISTINCT FROM TRUE
        """
        with conn.cursor() as cur:
            cur.execute(sql, (list(chunk_ids),))

    # ------------------------------------------------------------------
    def _run_cross_link(
        self,
        conn: psycopg2.extensions.connection,
        target: CrossLinkTarget,
        dry_run: bool,
        stats: CrossLinkStats,
    ) -> None:
        facts  = self._load_fact_ranges(conn, target.accession_no)
        chunks = self._load_chunk_ranges(conn, target.accession_no)

        if not facts:
            stats.targets_skipped_no_facts += 1
            logger.warning("%s: no fact_dom_position rows — skipping (run Phase 5 first).",
                           target.accession_no)
            return
        if not chunks:
            stats.targets_skipped_no_chunks += 1
            logger.warning("%s: no chunk_metadata rows — skipping (run Phase 8 first).",
                           target.accession_no)
            return

        stats.facts_considered += len(facts)
        rows, flagged_chunk_ids = _sweep_fact_chunk_overlaps(facts, chunks)
        stats.targets_processed += 1
        stats.links_written  += len(rows)
        stats.chunks_flagged += len(flagged_chunk_ids)

        if dry_run:
            logger.info(
                "[DRY RUN] %s: would upsert %d chunk_cross_links row(s) "
                "(%d/%d facts linked) and flag %d/%d chunk(s) has_xbrl_facts=TRUE.",
                target.accession_no, len(rows),
                len({r['fact_id'] for r in rows}), len(facts),
                len(flagged_chunk_ids), len(chunks),
            )
            return

        if rows:
            stats.rows_written += self._upsert_chunk_cross_links(conn, rows)
        if flagged_chunk_ids:
            self._flag_chunks_with_facts(conn, flagged_chunk_ids)

        logger.info(
            "%s: upserted %d chunk_cross_links row(s); flagged %d/%d chunk(s) has_xbrl_facts=TRUE.",
            target.accession_no, len(rows), len(flagged_chunk_ids), len(chunks),
        )

    # ------------------------------------------------------------------
    def _log_cross_link_summary(self, stats: CrossLinkStats) -> None:
        prefix = "[DRY RUN] " if stats.dry_run else ""
        logger.info(
            "%schunk_cross_links summary: targets=%d processed=%d "
            "skipped_no_facts=%d skipped_no_chunks=%d | "
            "facts_considered=%d | links_written=%d chunks_flagged=%d | "
            "rows_written=%d errors=%d",
            prefix, stats.targets_found, stats.targets_processed,
            stats.targets_skipped_no_facts, stats.targets_skipped_no_chunks,
            stats.facts_considered, stats.links_written, stats.chunks_flagged,
            stats.rows_written, len(stats.errors),
        )
        for w in stats.warnings:
            logger.warning("  %s", w)
        for e in stats.errors:
            logger.error("  %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.chunking.xbrl_html_base_chunking``."""
    p = argparse.ArgumentParser(
        prog="xbrl_html_base_chunking",
        description=(
            "Title/table-aware prose chunking + chunk_metadata population "
            "(Phase 7+8) and interval-overlap chunk_cross_links population "
            "(Phase 9) over canonical iXBRL HTML / Layer-0 tables."
        ),
    )
    p.add_argument("--sec-root", default=None, metavar="DIR",
                   help="Root of local SEC EDGAR storage (overrides SEC_EDGAR_ROOT env var).")
    p.add_argument("--normalized-root", default=None, metavar="DIR",
                   help=f"Root of canonical HTML storage. Default: {XBRLHTMLChunkPopulator.DEFAULT_NORMALIZED_ROOT}")
    p.add_argument("--env-file", default=".env",
                   help="Path to .env file (default: .env in current directory).")

    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--chunk-metadata", action="store_true",
                       help="Populate chunk_metadata only (Phase 7 chunking + Phase 8 upsert).")
    scope.add_argument("--cross-links", action="store_true",
                       help="Populate chunk_cross_links only (Phase 9 interval-overlap sweep) "
                            "— requires fact_dom_position (Phase 5) and chunk_metadata "
                            "(Phase 8) already populated for the target filing(s).")
    # default (neither flag): run both, in dependency order — Phase 8 then Phase 9.

    p.add_argument("--accessions", nargs="+", default=None, metavar="ACCESSION_NO",
                   help="Limit to specific EDGAR accession numbers "
                        "(e.g. 0001045810-26-000021). Processes all eligible filings if omitted.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute without writing to the database.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = p.parse_args(argv)

    import logging
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    populator = XBRLHTMLChunkPopulator.from_env(
        sec_edgar_root=args.sec_root,
        normalized_root=args.normalized_root,
        dotenv_path=args.env_file,
    )

    all_errors: list[str] = []

    if args.chunk_metadata:
        stats = populator.populate_chunk_metadata(accession_nos=args.accessions, dry_run=args.dry_run)
        all_errors.extend(stats.errors)
    elif args.cross_links:
        cl_stats = populator.populate_chunk_cross_links(accession_nos=args.accessions, dry_run=args.dry_run)
        all_errors.extend(cl_stats.errors)
    else:
        cm_stats = populator.populate_chunk_metadata(accession_nos=args.accessions, dry_run=args.dry_run)
        all_errors.extend(cm_stats.errors)
        cl_stats = populator.populate_chunk_cross_links(accession_nos=args.accessions, dry_run=args.dry_run)
        all_errors.extend(cl_stats.errors)

    if all_errors:
        print(f"\n  Total errors ({len(all_errors)}):")
        for e in all_errors:
            print(f"    - {e}")

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
