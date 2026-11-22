"""Unit tests for xbrl_html_base_chunking.py (Phase 7/8/9).

Validated against the canonical HTML artifacts produced by
``HTMLCanonicalizer`` — same fixture convention as
``test_xbrl_html_dom_parser.py``:

    <repo>/data/storage/xbrl/normalized/<TICKER>/<YEAR>/<FORM>/canonical.htm

Tests focus on the structural invariants the downstream phases depend on:

* **Phase 7 (chunker)**: the chunk stream must be *contiguous and
  non-overlapping* over ``[0, len(canonical.htm))`` — the same coverage
  guarantee :class:`SectionTree` upholds via its ROOT node.
* **Phase 8 (``chunk_metadata`` upsert)**: every ``chunk_id`` unique and
  deterministic (idempotent re-runs), every ``section_path`` resolvable in
  the filing's ``SectionTree``, ``prev_chunk_id``/``next_chunk_id``
  consistently linked in document order.
* **Phase 9 (``chunk_cross_links`` interval-overlap sweep)**: the bisect
  sweep (:func:`_sweep_fact_chunk_overlaps`) is exercised both as a pure
  function over synthetic ranges (boundary cases — fully-contained,
  spanning, exact-boundary, zero-length, empty inputs) *and* end-to-end
  against one real filing's actual chunker output (Phase 7) joined with its
  actual ``extract_ixbrl_positions`` output (Phase 4) — no DB required,
  since both populator inputs (``fact_dom_position``, ``chunk_metadata``)
  are simply DB reflections of these same in-memory structures.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import pytest

from src import CHUNK_SIZE_IN_CHARS, PROJECT_ROOT
from src.chunking.xbrl_html_base_chunking import (
    ProseChunk,
    XBRLHTMLChunker,
    _block_text,
    _has_block_descendant,
    _local_name,
    _make_chunk_id,
    _sweep_fact_chunk_overlaps,
    _table_text,
)
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    build_section_tree,
    extract_ixbrl_positions,
)

NORM_ROOT = Path(PROJECT_ROOT) / "data" / "storage" / "xbrl" / "normalized"


def canon(ticker: str, year: str, form: str, quarter: str = "") -> Path:
    """Return canonical.htm path, skipping the test if the file does not exist."""
    parts = [ticker, year, form]
    if quarter:
        parts.append(quarter)
    return NORM_ROOT.joinpath(*parts) / "canonical.htm"


def require(path: Path) -> Path:
    """Skip test if canonical.htm is missing."""
    if not path.exists():
        pytest.skip(f"canonical.htm not found: {path}")
    return path


# An accession number is only needed to seed deterministic chunk_ids in
# these tests — it does not need to resolve to a real DB row.
_FAKE_ACCESSION = "0000000000-00-000000"


# ---------------------------------------------------------------------------
# Helpers (unit-level, no file I/O)
# ---------------------------------------------------------------------------

class TestMakeChunkId:

    def test_deterministic(self):
        a = _make_chunk_id("0001045810-26-000021", 100, 200)
        b = _make_chunk_id("0001045810-26-000021", 100, 200)
        assert a == b

    def test_varies_with_range(self):
        a = _make_chunk_id("0001045810-26-000021", 100, 200)
        b = _make_chunk_id("0001045810-26-000021", 100, 201)
        c = _make_chunk_id("0001045810-26-000021", 101, 200)
        assert len({a, b, c}) == 3

    def test_varies_with_accession(self):
        a = _make_chunk_id("0001045810-26-000021", 100, 200)
        b = _make_chunk_id("0000320193-24-000123", 100, 200)
        assert a != b

    def test_is_uuid_like(self):
        cid = _make_chunk_id("0001045810-26-000021", 100, 200)
        # str(uuid.UUID(...)) — 36 chars, 4 hyphens
        assert len(cid) == 36
        assert cid.count("-") == 4


class TestLocalName:

    def test_strips_namespace(self):
        import lxml.etree as etree
        elem = etree.fromstring("<root xmlns='http://www.w3.org/1999/xhtml'><div/></root>")
        assert _local_name(elem) == "root"
        assert _local_name(elem[0]) == "div"


class TestHasBlockDescendant:

    def test_leaf_paragraph_with_inline_children(self):
        import lxml.etree as etree
        elem = etree.fromstring("<p>Hello <span>world</span> <a href='#'>link</a></p>")
        assert not _has_block_descendant(elem)

    def test_div_wrapping_paragraphs_is_not_leaf(self):
        import lxml.etree as etree
        elem = etree.fromstring("<div><p>one</p><p>two</p></div>")
        assert _has_block_descendant(elem)

    def test_div_wrapping_table_is_not_leaf(self):
        import lxml.etree as etree
        elem = etree.fromstring("<div><table><tr><td>x</td></tr></table></div>")
        assert _has_block_descendant(elem)

    def test_ix_header_blocks_leaf_classification(self):
        import lxml.etree as etree
        IX = "http://www.xbrl.org/2013/inlineXBRL"
        elem = etree.fromstring(
            f"<div xmlns:ix='{IX}'><ix:header><ix:hidden>"
            f"<ix:nonNumeric contextRef='c-1' name='dei:DocumentType' id='f-1'>10-K</ix:nonNumeric>"
            f"</ix:hidden></ix:header></div>"
        )
        assert _has_block_descendant(elem), (
            "a <div> wrapping <ix:header> must NOT be classified as a leaf "
            "prose block — its hidden XBRL machinery is not narrative content"
        )


class TestBlockText:

    def test_concatenates_inline_text(self):
        import lxml.etree as etree
        elem = etree.fromstring("<p>Hello <span>world</span>, how <b>are</b> you?</p>")
        assert _block_text(elem) == "Hello world , how are you?"

    def test_strips_each_text_node_and_joins_with_space(self):
        import lxml.etree as etree
        # Two separate text nodes (split by the inline <span>) — each is
        # individually stripped, then joined with a single space.
        elem = etree.fromstring("<p>  foo  <span>mid</span>  bar  </p>")
        assert _block_text(elem) == "foo mid bar"

    def test_preserves_internal_whitespace_within_a_single_text_node(self):
        import lxml.etree as etree
        # _block_text strips leading/trailing whitespace per itertext() node —
        # it does not collapse whitespace *inside* a single node.
        elem = etree.fromstring("<p>  foo  \n   bar  </p>")
        assert _block_text(elem) == "foo  \n   bar"

    def test_empty_element(self):
        import lxml.etree as etree
        elem = etree.fromstring("<p>   </p>")
        assert _block_text(elem) == ""


class TestTableText:

    def test_pipe_joins_cells_newline_joins_rows(self):
        import lxml.etree as etree
        elem = etree.fromstring(
            "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
        )
        assert _table_text(elem) == "A | B\nC | D"

    def test_skips_empty_cells(self):
        import lxml.etree as etree
        elem = etree.fromstring("<table><tr><td>  </td><td>X</td></tr></table>")
        assert _table_text(elem) == "X"

    def test_skips_rows_with_no_text(self):
        import lxml.etree as etree
        elem = etree.fromstring(
            "<table><tr><td> </td></tr><tr><td>only-row</td></tr></table>"
        )
        assert _table_text(elem) == "only-row"


# ---------------------------------------------------------------------------
# XBRLHTMLChunker — structural invariants, parametrised across filer types
# ---------------------------------------------------------------------------

# (ticker, year, form, quarter) — one representative per anchor/body pattern
# discovered in the Phase-4 survey (see xbrl_html_dom_parser module docstring):
#   Workiva flat-div siblings, simple-anchor <p>, TSM <td>/<p> ToC-driven,
#   and an amendment filing with a minimal (ROOT-only-ish) SectionTree.
CHUNK_FILING_PARAMS = [
    ("NVDA",  "2026", "10-K",   ""),
    ("MSFT",  "2023", "10-K",   ""),
    ("ORCL",  "2024", "10-K",   ""),
    ("TSM",   "2024", "20-F",   ""),
    ("TSM",   "2022", "20-F",   ""),
    ("AMD",   "2025", "10-K_A", ""),
    ("NVDA",  "2026", "10-Q",   "Q2"),
]


@pytest.mark.parametrize(
    "ticker,year,form,quarter",
    CHUNK_FILING_PARAMS,
    ids=[f"{t}-{y}-{f}{'-'+q if q else ''}" for t, y, f, q in CHUNK_FILING_PARAMS],
)
class TestChunkFiling:

    def setup_method(self):
        self._cache: dict[str, list[ProseChunk]] = {}
        self._chunker = XBRLHTMLChunker()

    def _chunks(self, ticker, year, form, quarter) -> list[ProseChunk]:
        key = f"{ticker}-{year}-{form}-{quarter}"
        if key not in self._cache:
            path = require(canon(ticker, year, form, quarter))
            self._cache[key] = self._chunker.chunk_filing(path, _FAKE_ACCESSION)
        return self._cache[key]

    # -- basic shape -----------------------------------------------------

    def test_returns_nonempty_list(self, ticker, year, form, quarter):
        chunks = self._chunks(ticker, year, form, quarter)
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    def test_all_chunks_have_text(self, ticker, year, form, quarter):
        for c in self._chunks(ticker, year, form, quarter):
            assert isinstance(c.text, str)
            assert c.text.strip(), f"chunk {c.chunk_id} has empty text"

    # -- byte-range invariants (Phase 9 depends on these) ----------------

    def test_byte_ranges_valid(self, ticker, year, form, quarter):
        chunks = self._chunks(ticker, year, form, quarter)
        raw = require(canon(ticker, year, form, quarter)).read_bytes()
        for c in chunks:
            assert 0 <= c.html_char_start < c.html_char_end <= len(raw), (
                f"{c.chunk_id}: invalid range [{c.html_char_start},{c.html_char_end}]"
            )

    def test_chunks_are_contiguous_and_cover_whole_document(self, ticker, year, form, quarter):
        """Every byte in [0, len(canonical.htm)) belongs to exactly one
        chunk — required for Phase 9's interval-overlap cross-link sweep."""
        chunks = self._chunks(ticker, year, form, quarter)
        raw = require(canon(ticker, year, form, quarter)).read_bytes()

        assert chunks[0].html_char_start == 0, (
            "first chunk must start at byte 0 (folds <head>/<ix:header> "
            "prefix in, mirroring SectionTree's ROOT sentinel)"
        )
        for i in range(len(chunks) - 1):
            assert chunks[i].html_char_end == chunks[i + 1].html_char_start, (
                f"gap/overlap between chunk {i} (end={chunks[i].html_char_end}) "
                f"and chunk {i+1} (start={chunks[i+1].html_char_start})"
            )
        assert chunks[-1].html_char_end == len(raw), (
            "last chunk must end at end-of-file"
        )

    def test_chunk_ids_unique_and_deterministic(self, ticker, year, form, quarter):
        chunks = self._chunks(ticker, year, form, quarter)
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), "duplicate chunk_id values found"

        # Re-running over the same byte ranges must yield the same ids
        # (idempotent upsert — Section 3.16 of the design doc).
        for c in chunks:
            assert c.chunk_id == _make_chunk_id(_FAKE_ACCESSION, c.html_char_start, c.html_char_end)

    # -- prev/next linking ------------------------------------------------

    def test_prev_next_linked_in_document_order(self, ticker, year, form, quarter):
        chunks = self._chunks(ticker, year, form, quarter)
        assert chunks[0].prev_chunk_id is None
        assert chunks[-1].next_chunk_id is None
        for i in range(len(chunks) - 1):
            assert chunks[i].next_chunk_id == chunks[i + 1].chunk_id
            assert chunks[i + 1].prev_chunk_id == chunks[i].chunk_id

    # -- section_path resolves in the filing's SectionTree ----------------

    def test_section_paths_resolve_in_section_tree(self, ticker, year, form, quarter):
        path = require(canon(ticker, year, form, quarter))
        chunks = self._chunks(ticker, year, form, quarter)
        tree = build_section_tree(path, _FAKE_ACCESSION)
        for c in chunks:
            assert c.section_path in tree.nodes, (
                f"{c.chunk_id}: section_path {c.section_path!r} not in SectionTree"
            )

    # -- table-awareness ---------------------------------------------------

    def test_table_chunks_flagged_and_atomic(self, ticker, year, form, quarter):
        chunks = self._chunks(ticker, year, form, quarter)
        table_chunks = [c for c in chunks if c.contains_table]
        for c in table_chunks:
            assert c.source_tag == "table"
            assert c.contains_table is True
        # Non-table chunks must not be tagged 'table'.
        for c in chunks:
            if not c.contains_table:
                assert c.source_tag != "table"

    def test_at_least_one_table_chunk_for_10k_20f(self, ticker, year, form, quarter):
        if form not in ("10-K", "20-F"):
            pytest.skip("table presence only asserted for full annual filings")
        chunks = self._chunks(ticker, year, form, quarter)
        assert any(c.contains_table for c in chunks), (
            "expected at least one <table> chunk in a 10-K/20-F filing"
        )

    # -- size discipline ----------------------------------------------------

    def test_prose_chunks_roughly_bounded_by_chunk_size(self, ticker, year, form, quarter):
        """Prose chunks accumulate whole elements up to CHUNK_SIZE_IN_CHARS —
        a single oversized leaf element can push a chunk over the limit, but
        the median should sit close to the budget, not wildly over it."""
        chunks = self._chunks(ticker, year, form, quarter)
        prose_lens = [len(c.text) for c in chunks if not c.contains_table]
        assert prose_lens, "expected at least one prose chunk"
        median_len = statistics.median(prose_lens)
        assert median_len <= CHUNK_SIZE_IN_CHARS * 1.5, (
            f"median prose chunk length {median_len} far exceeds "
            f"CHUNK_SIZE_IN_CHARS={CHUNK_SIZE_IN_CHARS}"
        )

    def test_no_pure_xbrl_machinery_chunks(self, ticker, year, form, quarter):
        """<ix:header> (hidden DEI facts + taxonomy/context/unit definitions)
        must never surface as its own pseudo-prose chunk — it has no
        narrative content (regression guard for the ~20K-char NVDA artefact
        observed before the <ix:header>-skip was added)."""
        chunks = self._chunks(ticker, year, form, quarter)
        for c in chunks:
            assert "inlineXBRL" not in c.text and "ix:header" not in c.text
            # Hidden DEI cover-page concepts read back as raw QName soup —
            # a tell-tale sign <ix:header> leaked into a "prose" chunk.
            assert "dei:EntityCentralIndexKey" not in c.text
            assert "dei:DocumentFiscalYearFocus" not in c.text


# ---------------------------------------------------------------------------
# Smoke test — every canonicalized filing should chunk without crashing
# ---------------------------------------------------------------------------

_ALL_CANON_PATHS = sorted(NORM_ROOT.rglob("canonical.htm")) if NORM_ROOT.exists() else []


@pytest.mark.parametrize(
    "canon_path",
    _ALL_CANON_PATHS,
    ids=[str(p.relative_to(NORM_ROOT)) for p in _ALL_CANON_PATHS],
)
class TestSmokeAllFilings:
    """No filing should raise an exception, and the coverage invariant must
    hold universally — not just for the hand-picked representative set."""

    def test_chunk_filing_no_crash_and_covers_document(self, canon_path):
        raw = canon_path.read_bytes()
        chunks = XBRLHTMLChunker().chunk_filing(canon_path, _FAKE_ACCESSION)

        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert chunks[0].html_char_start == 0
        assert chunks[-1].html_char_end == len(raw)
        for i in range(len(chunks) - 1):
            assert chunks[i].html_char_end == chunks[i + 1].html_char_start

        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Phase 9 — _sweep_fact_chunk_overlaps: pure-function boundary cases
# ---------------------------------------------------------------------------

# A synthetic, contiguous, non-overlapping chunk stream covering [0, 1000) —
# exactly the Phase 7/8 invariant the sweep relies on for its bisect walk.
_SYN_CHUNKS = [
    ("c1", 0, 100),
    ("c2", 100, 300),
    ("c3", 300, 700),
    ("c4", 700, 1000),
]


class TestSweepFactChunkOverlaps:

    def test_fact_wholly_inside_one_chunk(self):
        rows, flagged = _sweep_fact_chunk_overlaps([(1, 150, 200)], _SYN_CHUNKS)
        assert rows == [{"fact_id": 1, "chunk_id": "c2", "confidence": 1.0}]
        assert flagged == {"c2"}

    def test_fact_at_exact_chunk_start_boundary(self):
        rows, flagged = _sweep_fact_chunk_overlaps([(3, 300, 400)], _SYN_CHUNKS)
        assert rows == [{"fact_id": 3, "chunk_id": "c3", "confidence": 1.0}]
        assert flagged == {"c3"}

    def test_fact_spanning_two_chunks_splits_confidence_proportionally(self):
        # [250, 350): 50 bytes in c2 ([250,300)), 50 bytes in c3 ([300,350))
        rows, flagged = _sweep_fact_chunk_overlaps([(2, 250, 350)], _SYN_CHUNKS)
        by_chunk = {r["chunk_id"]: r["confidence"] for r in rows}
        assert by_chunk == {"c2": 0.5, "c3": 0.5}
        assert flagged == {"c2", "c3"}
        assert abs(sum(by_chunk.values()) - 1.0) < 1e-9

    def test_fact_spanning_many_chunks_confidences_sum_to_one(self):
        # [50, 750): overlaps all four chunks; proportional split, rounding
        # to 4 decimals can leave a residual on the order of 1e-4.
        rows, flagged = _sweep_fact_chunk_overlaps([(5, 50, 750)], _SYN_CHUNKS)
        assert flagged == {"c1", "c2", "c3", "c4"}
        assert abs(sum(r["confidence"] for r in rows) - 1.0) < 1e-3
        # Largest overlap (400/700 in c3) must get the largest confidence.
        by_chunk = {r["chunk_id"]: r["confidence"] for r in rows}
        assert by_chunk["c3"] == max(by_chunk.values())

    def test_zero_length_fact_range_is_skipped(self):
        rows, flagged = _sweep_fact_chunk_overlaps([(4, 500, 500)], _SYN_CHUNKS)
        assert rows == []
        assert flagged == set()

    def test_inverted_fact_range_is_skipped(self):
        rows, flagged = _sweep_fact_chunk_overlaps([(6, 500, 400)], _SYN_CHUNKS)
        assert rows == []
        assert flagged == set()

    def test_none_bounds_are_skipped_defensively(self):
        rows, flagged = _sweep_fact_chunk_overlaps([(7, None, 100), (8, 100, None)], _SYN_CHUNKS)
        assert rows == []
        assert flagged == set()

    def test_empty_facts_list(self):
        assert _sweep_fact_chunk_overlaps([], _SYN_CHUNKS) == ([], set())

    def test_empty_chunks_list(self):
        assert _sweep_fact_chunk_overlaps([(1, 0, 10)], []) == ([], set())

    def test_multiple_facts_in_same_chunk_each_get_their_own_row(self):
        rows, flagged = _sweep_fact_chunk_overlaps(
            [(1, 110, 120), (2, 200, 250)], _SYN_CHUNKS
        )
        assert {(r["fact_id"], r["chunk_id"]) for r in rows} == {(1, "c2"), (2, "c2")}
        assert all(r["confidence"] == 1.0 for r in rows)
        assert flagged == {"c2"}

    def test_facts_outside_chunk_coverage_produce_no_overlap(self):
        # A fact range entirely beyond the last chunk's char_end.
        rows, flagged = _sweep_fact_chunk_overlaps([(9, 1000, 1100)], _SYN_CHUNKS)
        assert rows == []
        assert flagged == set()

    def test_confidence_is_fraction_of_fact_range_not_chunk_range(self):
        # Fact occupies only 10 of its own 100 bytes inside a 200-byte chunk —
        # confidence reflects "how much of the FACT is in this chunk" (1.0,
        # since the whole fact fits), not "how much of the chunk the fact
        # covers" (which would be 0.05).
        rows, _ = _sweep_fact_chunk_overlaps([(1, 110, 120)], _SYN_CHUNKS)
        assert rows == [{"fact_id": 1, "chunk_id": "c2", "confidence": 1.0}]


# ---------------------------------------------------------------------------
# Phase 9 — end-to-end sweep over real chunker (P7) + real iXBRL positions (P4)
# ---------------------------------------------------------------------------
#
# No DB required: fact_dom_position / chunk_metadata are simply DB
# reflections of extract_ixbrl_positions() / XBRLHTMLChunker output in the
# very same byte-offset coordinate space — so running the sweep directly
# over those in-memory structures exercises the *real* Phase 9 algorithm
# against *real* filing data.

CROSS_LINK_FILING_PARAMS = [
    ("NVDA",  "2026", "10-K",   ""),
    ("MSFT",  "2023", "10-K",   ""),
    ("TSM",   "2024", "20-F",   ""),
]


@pytest.mark.parametrize(
    "ticker,year,form,quarter",
    CROSS_LINK_FILING_PARAMS,
    ids=[f"{t}-{y}-{f}" for t, y, f, _ in CROSS_LINK_FILING_PARAMS],
)
class TestCrossLinkEndToEnd:

    def setup_method(self):
        self._cache: dict[str, tuple] = {}

    def _sweep(self, ticker, year, form, quarter):
        key = f"{ticker}-{year}-{form}-{quarter}"
        if key not in self._cache:
            path = require(canon(ticker, year, form, quarter))
            chunks = XBRLHTMLChunker().chunk_filing(path, _FAKE_ACCESSION)
            positions = extract_ixbrl_positions(path)

            chunk_ranges = sorted(
                ((c.chunk_id, c.html_char_start, c.html_char_end) for c in chunks),
                key=lambda r: r[1],
            )
            # Synthesize fact_ids the way fact_dom_position's join would —
            # the sweep only cares about (id, char_start, char_end) ranges.
            fact_ranges = sorted(
                ((i, p.char_start, p.char_end) for i, p in enumerate(positions)),
                key=lambda r: r[1],
            )
            rows, flagged = _sweep_fact_chunk_overlaps(fact_ranges, chunk_ranges)
            self._cache[key] = (fact_ranges, chunk_ranges, rows, flagged)
        return self._cache[key]

    def test_every_fact_links_to_at_least_one_chunk(self, ticker, year, form, quarter):
        fact_ranges, _, rows, _ = self._sweep(ticker, year, form, quarter)
        linked_fact_ids = {r["fact_id"] for r in rows}
        unlinked = [f for f in fact_ranges if f[0] not in linked_fact_ids]
        assert not unlinked, (
            f"{len(unlinked)}/{len(fact_ranges)} fact(s) got zero chunk links "
            f"(sample: {unlinked[:3]}) — every iXBRL fact lies somewhere in "
            f"the contiguous chunk stream, so this should never happen"
        )

    def test_confidences_per_fact_sum_to_one(self, ticker, year, form, quarter):
        _, _, rows, _ = self._sweep(ticker, year, form, quarter)
        totals: dict[int, float] = defaultdict(float)
        for r in rows:
            totals[r["fact_id"]] += r["confidence"]
        bad = [(fid, total) for fid, total in totals.items() if abs(total - 1.0) > 0.01]
        assert not bad, f"fact confidence totals deviating from 1.0: {bad[:5]}"

    def test_confidence_values_in_valid_range(self, ticker, year, form, quarter):
        _, _, rows, _ = self._sweep(ticker, year, form, quarter)
        for r in rows:
            assert 0.0 < r["confidence"] <= 1.0, r

    def test_majority_of_facts_link_to_exactly_one_chunk(self, ticker, year, form, quarter):
        """Facts spanning a chunk boundary are the rare edge case (§7 of the
        source-of-truth doc) — the overwhelming majority must resolve to a
        single chunk with confidence 1.0."""
        fact_ranges, _, rows, _ = self._sweep(ticker, year, form, quarter)
        per_fact_chunk_count: dict[int, int] = defaultdict(int)
        for r in rows:
            per_fact_chunk_count[r["fact_id"]] += 1
        single_chunk = sum(1 for n in per_fact_chunk_count.values() if n == 1)
        pct = 100 * single_chunk / len(fact_ranges)
        assert pct >= 95, f"only {pct:.1f}% of facts resolved to a single chunk"

    def test_flagged_chunks_are_subset_of_all_chunks_and_nonempty(self, ticker, year, form, quarter):
        _, chunk_ranges, _, flagged = self._sweep(ticker, year, form, quarter)
        all_ids = {c[0] for c in chunk_ranges}
        assert flagged, "expected at least one chunk to be flagged has_xbrl_facts=TRUE"
        assert flagged.issubset(all_ids)

    def test_flagged_chunks_are_exactly_those_appearing_in_rows(self, ticker, year, form, quarter):
        _, _, rows, flagged = self._sweep(ticker, year, form, quarter)
        assert flagged == {r["chunk_id"] for r in rows}

    def test_rows_reference_only_known_facts_and_chunks(self, ticker, year, form, quarter):
        fact_ranges, chunk_ranges, rows, _ = self._sweep(ticker, year, form, quarter)
        fact_ids = {f[0] for f in fact_ranges}
        chunk_ids = {c[0] for c in chunk_ranges}
        for r in rows:
            assert r["fact_id"] in fact_ids
            assert r["chunk_id"] in chunk_ids

    def test_no_duplicate_fact_chunk_pairs(self, ticker, year, form, quarter):
        """chunk_cross_links PK is (fact_id, chunk_id) — the sweep must never
        emit the same pair twice (it would violate the upsert's uniqueness
        and silently overwrite confidence with itself, masking a bug)."""
        _, _, rows, _ = self._sweep(ticker, year, form, quarter)
        pairs = [(r["fact_id"], r["chunk_id"]) for r in rows]
        assert len(pairs) == len(set(pairs))
