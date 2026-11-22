"""DOM parser for SEC EDGAR iXBRL canonical HTML files.

Implements the section-tree step of the HTML-canonical ingestion pipeline
(``docs/design/XbrlRag_HTML_Canonical_Source.md`` §3):

* ``build_section_tree(canonical_path)``  →  :class:`SectionTree`
* ``extract_ixbrl_positions(canonical_path)``  →  ``list[IXBRLPosition]``

Both functions operate on the **canonical.htm** artifacts produced by
:mod:`html_canonicalizer`.  They never read the raw SEC EDGAR download —
all byte offsets are relative to the canonical file.

Survey findings (60 filings across 12 tickers, 2022-2026):
---------------------------------------------------------------------------
Three distinct anchor / body patterns were found:

1. **Workiva** (AAPL, AMD 2023+, AMZN, AVGO, GOOGL, META, MU, NVDA, TSLA
   2023+, TSM 2025):
   - Body children: sibling ``<div>`` elements (flat).
   - Section anchors: empty ``<div id="i<32hexchars>_<N>"/>``.
   - Heading text: in the *next* sibling ``<div>`` after the anchor div.

2. **Simple-anchor** (MSFT, ORCL, TSLA 2022, TSM 2022-2024):
   - Body children: sibling ``<p>`` or ``<div>`` elements.
   - Section anchors: ``<p id="item_1a_risk_factors">ITEM 1A...</p>`` —
     the heading text IS the element's own text content.
   - TSM ``<td>`` anchors: fall back to the ToC link label.

3. **Amendment / partial** (AMD 2025 10-K/A, TSLA 2024/2025 10-K/A):
   - No ToC links → minimal tree with just the ROOT node.

4. **Page-number ToC** (INTC, added 2026-10): Workiva anchors, but every ToC
   link's text is a page number; the section name is another cell of the
   row.  A back-of-filing "Form 10-K Cross-Reference Index" (rows with a
   ``Page(s) <n>`` cell) links to page breaks and contributes no sections.
   See docs/specs/toc-page-number-links.md.

iXBRL fact coverage:
---------------------------------------------------------------------------
* All Workiva filings and most simple-anchor filings: 100% of
  ``ix:nonFraction`` / ``ix:nonNumeric`` facts carry an ``id=`` attribute
  that becomes ``fact.xml_id`` in Arelle's xbrlDB.
* TSM 2022 20-F: only ~100 of 3,666 facts have ``id=`` attributes
  (early iXBRL adoption).  ``extract_ixbrl_positions`` logs a warning and
  returns only the facts that can be byte-position-linked.
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import lxml.etree as etree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace URIs
# ---------------------------------------------------------------------------

XHTML_NS = "http://www.w3.org/1999/xhtml"
IX_NS    = "http://www.xbrl.org/2013/inlineXBRL"

_IX_FACT_LOCALS  = frozenset({"nonFraction", "nonNumeric", "fraction"})
_IX_FACT_TAGS    = frozenset(f"{{{IX_NS}}}{loc}" for loc in _IX_FACT_LOCALS)
_IX_CONT_TAG     = f"{{{IX_NS}}}continuation"

# ---------------------------------------------------------------------------
# Financial statement keyword detection
# ---------------------------------------------------------------------------

# Squashed (whitespace-removed) keyword set for financial statement detection.
# Squashing handles CSS letter-spacing artifacts found in MSFT/ORCL filings
# where headings are emitted as e.g. "INC OME STATEMENTS" or "BALANCE  SHEETS".
# Both the label and each keyword are whitespace-stripped before comparison.
_FIN_STMT_KW_SQUASHED = frozenset({
    # Standard consolidated variants
    "balancesheets", "balancesheet",
    "statementsofincome", "statementsofloss",
    "statementsofoperations",
    "statementsofearnings",
    "statementsofcomprehensiveincome", "statementsofcomprehensiveincomeorloss",
    "statementsofcomprehensiveincomeorlossandotheritems",
    "statementsofshareholdersequity", "statementsofshareholders",
    "statementsofstockholdersequity", "statementsofstockholders",
    "statementsofequity",
    "statementsofcashflows", "statementsofcashflow",
    "statementoffinancialposition",
    "statementofprofitorloss", "statementofprofit",
    "statementsofprofitorloss", "statementsofprofit",
    # Condensed / unaudited variants (10-Q)
    "condensedconsolidatedbalancesheets", "condensedconsolidatedbalancesheet",
    "condensedconsolidatedstatements",
    # Reversed-word-order headings produced by MSFT / ORCL authoring tools:
    # "INCOME STATEMENTS" (vs "STATEMENTS OF INCOME"), etc.
    "incomestatements", "incomestatement",
    "cashflowsstatements", "cashflowstatements",
    "stockholdersequitystatements", "stockholdersequitystatement",
    "shareholdersequitystatements",
    "comprehensiveincomestatements", "comprehensiveincomestatement",
    # ORCL-style "CONSOLIDATED BALANCE SHEETS" squashed
    "consolidatedbalancesheets",
})


def _is_financial_statement(label: str) -> bool:
    """Return True when *label* names one of the five tabular financial statements.

    Uses whitespace-squashed substring matching to handle CSS letter-spacing
    artefacts (e.g. ``'INC OME STATEMENTS'`` from MSFT/ORCL filings).
    """
    squashed = re.sub(r"\s+", "", label).lower()
    return any(kw in squashed for kw in _FIN_STMT_KW_SQUASHED)


# ---------------------------------------------------------------------------
# Level inference
# ---------------------------------------------------------------------------

_RE_PART  = re.compile(r"^part\s+[ivxlcdm]+\.?\s*$", re.I)
_RE_ITEM  = re.compile(r"\bitem\s+\d", re.I)
_RE_ANCHOR_PART = re.compile(r"^part_?[ivxlcdm]", re.I)
_RE_ANCHOR_ITEM = re.compile(r"^item_?\d", re.I)


def _infer_level(label: str, anchor_id: str) -> int:
    """Infer tree depth (1=Part, 2=Item, 3=sub-section) from label and anchor."""
    if _RE_PART.match(label.strip()) or _RE_ANCHOR_PART.match(anchor_id):
        return 1
    if _RE_ITEM.search(label) or _RE_ANCHOR_ITEM.match(anchor_id):
        return 2
    if _is_financial_statement(label):
        return 3
    return 2   # sensible default: narrative items without "Item N." prefix


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SectionNode:
    """One node in the :class:`SectionTree`.

    ``char_start`` and ``char_end`` are **byte offsets** into ``canonical.htm``
    (UTF-8 encoded).  The half-open range ``[char_start, char_end)`` covers
    the full HTML source belonging to this section.
    """

    section_path: str
    """Stable path string, e.g. ``'Part II > Item 7. MD&A'``."""

    label: str
    """Short heading as it appears in the document / ToC."""

    level: int
    """Tree depth: 0=ROOT, 1=Part, 2=Item, 3=sub-section."""

    html_anchor: str | None
    """Value of the ``id=`` or ``name=`` attribute that marks this section's
    start in the canonical HTML.  ``None`` for the ROOT node."""

    char_start: int
    """Byte offset of the opening ``<`` of the anchor element in canonical.htm."""

    char_end: int
    """Byte offset just after the last byte belonging to this section
    (= ``char_start`` of the *next* section, or ``len(raw_bytes)`` for the
    last node)."""

    is_in_toc: bool
    """True when this section appears as a link in the HTML Table of Contents."""

    source: str
    """``'toc'`` | ``'heading'`` | ``'root'``."""

    is_financial_statement: bool
    """True for the five tabular financial statements (Balance Sheets,
    Income Statement, Comprehensive Income, Shareholders' Equity, Cash Flows).
    Financial-statement facts are *skipped* in ``section_assignment`` because
    their section comes from the XBRL presentation linkbase."""

    toc_label: str | None
    """Full label as it appears in the ToC links (may differ from ``label``)."""

    parent_path: str | None
    """``section_path`` of the parent node, or ``None`` for ROOT."""

    children_paths: list[str] = field(default_factory=list)
    """Ordered list of child ``section_path`` values."""

    derived_page_start: int | None = None
    """Set by the HTML→PDF renderer; for citation UI only."""

    derived_page_end: int | None = None


@dataclass
class SectionTree:
    """Complete section tree for one filing.

    Use :meth:`find_section` to locate the section containing a given byte
    offset (Phase 5 / Phase 6 binary-search).
    """

    accession_no: str
    canonical_path: str
    canonical_size_bytes: int

    nodes: dict[str, SectionNode]
    """keyed by ``section_path``."""

    # Parallel sorted arrays for O(log N) bisect
    ordered_char_starts: list[int]
    ordered_section_paths: list[str]

    def find_section(self, char_pos: int) -> str | None:
        """Return the ``section_path`` of the section containing *char_pos*.

        Uses ``bisect_right`` on the sorted ``ordered_char_starts`` array.
        Returns ``None`` only when *char_pos* precedes the very first node.
        """
        idx = bisect.bisect_right(self.ordered_char_starts, char_pos) - 1
        if idx < 0:
            return None
        return self.ordered_section_paths[idx]

    @property
    def section_count(self) -> int:
        return len(self.nodes)


@dataclass
class IXBRLPosition:
    """Byte location of one ``ix:nonFraction`` / ``ix:nonNumeric`` /
    ``ix:fraction`` element within the canonical HTML file.

    ``xml_id`` maps to ``fact.xml_id`` in Arelle's xbrlDB.  Phase 5 uses this
    to upsert ``fact_dom_position``.  Phase 9 uses ``char_start`` / ``char_end``
    to cross-link facts to :class:`ProseBlock` ranges.
    """

    xml_id: str
    """Value of the ``id=`` attribute → ``fact.xml_id`` in Arelle's DB."""

    tag_name: str
    """``'nonFraction'`` | ``'nonNumeric'`` | ``'fraction'``."""

    concept: str
    """``name=`` attribute value, e.g. ``'us-gaap:Revenues'``."""

    context_ref: str
    """``contextRef=`` attribute value."""

    unit_ref: str | None
    """``unitRef=`` (present on nonFraction; absent on nonNumeric)."""

    decimals: str | None
    """``decimals=`` attribute (nonFraction only)."""

    char_start: int
    """Byte offset of the opening ``<`` of the ``ix:*`` element."""

    char_end: int
    """Byte offset just after the closing ``>`` of ``</ix:*>``."""

    text_value: str
    """The raw text content between the tags (the fact's display value)."""

    continuation_positions: list[tuple[int, int]] = field(default_factory=list)
    """Byte ranges of associated ``ix:continuation`` elements (multi-span facts).
    Each tuple is ``(char_start, char_end)`` of one continuation element."""

    page_num: int | None = None
    """Set by the HTML→PDF renderer; for citation UI only."""


@dataclass
class ProseBlock:
    """A contiguous, semantically coherent block of prose extracted from one
    :class:`SectionNode`.

    ``text`` contains human-readable content (iXBRL fact *values* are inline;
    iXBRL *markup* is not).  ``html_char_start`` / ``html_char_end`` capture the
    full HTML source (including any ``<ix:*>`` wrappers inside).

    ``ixbrl_xml_ids`` is populated during Phase 9 (cross-linking) by
    interval-overlap between :class:`IXBRLPosition` ranges and this block's
    ``[html_char_start, html_char_end)`` range.
    """

    text: str
    html_char_start: int
    html_char_end: int
    section_path: str
    contains_table: bool
    source_tag: str
    ixbrl_xml_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal: byte-offset index
# ---------------------------------------------------------------------------

def _build_id_offset_index(raw_bytes: bytes) -> dict[str, int]:
    """Single O(N) scan of *raw_bytes* → ``{id_or_name_value: element_tag_start}``.

    For each ``id="..."`` or ``name="..."`` attribute found, records the byte
    offset of the opening ``<`` of the *element* that carries that attribute.
    The result allows O(1) byte-offset lookup for any known anchor ID.
    """
    index: dict[str, int] = {}
    n = len(raw_bytes)

    # Collect positions of every  id="  and  name="  occurrence.
    entries: list[tuple[int, int]] = []   # (byte_pos_of_attr, attr_prefix_len)
    for pat, plen in ((b' id="', 5), (b' name="', 7)):
        start = 0
        while True:
            pos = raw_bytes.find(pat, start)
            if pos < 0:
                break
            entries.append((pos, plen))
            start = pos + 1

    entries.sort()   # process in document order

    for pos, attr_len in entries:
        val_start = pos + attr_len
        val_end   = raw_bytes.find(b'"', val_start)
        if val_end < 0 or val_end >= n:
            continue
        value = raw_bytes[val_start:val_end].decode("utf-8", errors="replace")
        if not value or value in index:
            continue
        # Walk back to the opening '<' of the element's start tag.
        tag_start = raw_bytes.rfind(b"<", 0, pos)
        if tag_start < 0:
            continue
        index[value] = tag_start

    return index


# ---------------------------------------------------------------------------
# Internal: namespace prefix lookup
# ---------------------------------------------------------------------------

def _get_ns_prefix(root: etree._Element, ns_uri: str, default: str = "ix") -> str:
    """Return the namespace prefix used for *ns_uri* in *root*'s ``nsmap``."""
    ns_map = root.nsmap or {}
    for prefix, uri in ns_map.items():
        if uri == ns_uri and prefix is not None:
            return prefix
    return default


# ---------------------------------------------------------------------------
# Internal: heading-text extraction
# ---------------------------------------------------------------------------

def _extract_heading_text(
    anchor_elem: etree._Element | None,
    toc_label: str,
) -> str:
    """Return the best heading text for a section anchor.

    Priority order
    --------------
    1. **ToC label** — the text from the ``<a href="#…">`` link in the HTML
       Table of Contents.  This is always the official section name and is
       free of CSS letter-spacing artefacts (``"INC OME STATEMENTS"`` etc.).
       Used first whenever it is non-trivially long (> 4 characters).
    2. **Element's own text** — fallback for cases where the ToC label is
       absent or too short (heading-only anchors not linked from the ToC).
    3. **Next sibling's text** — for Workiva empty-anchor divs where the
       label is in the immediately following ``<div>``.
    """
    # (1) Prefer the ToC label — it is always clean and canonical.
    if toc_label and len(toc_label.strip()) > 4:
        return toc_label.strip()

    if anchor_elem is None:
        return toc_label or ""

    # (2) Element's own text (MSFT/ORCL/TSLA: <p id="…">heading</p>).
    own = " ".join(anchor_elem.itertext()).strip()
    if own and len(own) < 300:
        return own

    # (3) Next sibling (Workiva: empty <div id="…"> followed by <div>heading</div>).
    nxt = anchor_elem.getnext()
    if nxt is not None and isinstance(nxt.tag, str):
        nxt_text = " ".join(nxt.itertext()).strip()
        if nxt_text and len(nxt_text) < 300:
            return nxt_text

    return toc_label or ""


# ---------------------------------------------------------------------------
# Internal: section path builder
# ---------------------------------------------------------------------------

def _make_section_path(label: str, parent_path: str | None) -> str:
    label = label.strip()
    return f"{parent_path} > {label}" if parent_path else label


# ---------------------------------------------------------------------------
# Internal: page-number ToC rows (Intel)
# ---------------------------------------------------------------------------

# A ToC link whose text is a page reference: "37", "81-87", "53 – 67".
_RE_PAGE_REF = re.compile(r"^\d+(?:\s*[-–]\s*\d+)?$")
# A cross-reference-index cell ("Page 54", "Pages 37 - 51"): its row's links
# point at page breaks inside sections, not at section starts.
_RE_PAGE_CELL = re.compile(r"^pages?\s+\d", re.I)


def _is_page_ref(text: str) -> bool:
    return bool(_RE_PAGE_REF.match(text.strip()))


def _page_row_label(link: etree._Element) -> str:
    """Return the section label for a page-number ToC link, from its table row.

    The label is the row's first non-empty cell that is not itself a page
    reference.  Returns ``""`` when the link is not in a table row, or when
    the row belongs to a cross-reference index (any ``Page(s) <n>`` cell).
    """
    row = next(
        (a for a in link.iterancestors()
         if isinstance(a.tag, str) and etree.QName(a).localname == "tr"),
        None,
    )
    if row is None:
        return ""
    cells = [
        " ".join(" ".join(cell.itertext()).split())
        for cell in row
        if isinstance(cell.tag, str)
    ]
    if any(_RE_PAGE_CELL.match(c) for c in cells):
        return ""
    return next((c for c in cells if c and not _is_page_ref(c)), "")


# ---------------------------------------------------------------------------
# Public: build_section_tree()
# ---------------------------------------------------------------------------

def build_section_tree(
    canonical_path: str | Path,
    accession_no: str = "",
) -> SectionTree:
    """Build a :class:`SectionTree` from a canonical iXBRL HTML file.

    Parameters
    ----------
    canonical_path:
        Path to ``canonical.htm`` (produced by :mod:`html_canonicalizer`).
    accession_no:
        EDGAR accession number string (optional; stored verbatim in the result).

    Returns
    -------
    SectionTree
        Nodes are sorted by ``char_start``.  ``find_section(byte_offset)``
        does an O(log N) binary search to locate the containing section.

    Notes
    -----
    * The ROOT node (``section_path='ROOT'``) always covers the entire document
      ``[0, len(raw_bytes))``.  It acts as the binary-search sentinel so that
      any byte offset in the file resolves to *something*.
    * Amendment filings (10-K/A, 20-F/A) that have no ToC links return a tree
      with only the ROOT node.
    * TSM ``<td>`` anchor elements that contain only an item number (``'ITEM 1.'``)
      fall back to the ToC label for the heading text.
    """
    canonical_path = Path(canonical_path).resolve()
    raw_bytes      = canonical_path.read_bytes()

    tree = etree.parse(str(canonical_path))
    root = tree.getroot()

    ix_ns_prefix = _get_ns_prefix(root, IX_NS, "ix")
    offset_index = _build_id_offset_index(raw_bytes)

    # ── 1. Build anchor → element map ────────────────────────────────────────
    anchor_elem_map: dict[str, etree._Element] = {}
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        for attr in ("id", "name"):
            val = elem.get(attr)
            if val and val not in anchor_elem_map:
                anchor_elem_map[val] = elem

    # ── 2. Extract ToC links → {anchor_id: [label_text, ...]} ────────────────
    _RE_FACT_ANCHOR    = re.compile(r"^f-\d+")
    _RE_XBRL_RESOURCE  = re.compile(r"^(c-\d+|usd\b|shares\b|number\b|period\b|segment\b|usdPer)")

    raw_toc: dict[str, list[str]] = {}   # anchor_id → accumulated label parts
    page_toc: dict[str, str] = {}        # anchor_id → row label of its first page-number link
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        href = elem.get("href", "")
        if not href.startswith("#"):
            continue
        target = href[1:]
        if not target:
            continue
        # Skip iXBRL resource anchors — they are not section boundaries.
        if _RE_FACT_ANCHOR.match(target) or _RE_XBRL_RESOURCE.match(target):
            continue
        text = " ".join(elem.itertext()).strip().replace("\n", " ")
        if text and _is_page_ref(text):
            if target not in page_toc:
                page_toc[target] = _page_row_label(elem)
            continue
        if text:
            raw_toc.setdefault(target, []).append(text)

    # Page-number ToC (Intel): every link's text is a page number and the
    # section name sits in another cell of the row.  Used only when the filing
    # has no text-labelled ToC (its sole text links are "Table of Contents"
    # back-links to one target), so text-ToC filings build the same tree.
    if len(raw_toc) < 2:
        for target, label in page_toc.items():
            if label and target not in raw_toc:
                raw_toc[target] = [label]

    # Consolidate multi-cell ToC rows into a single label.
    toc_label_map: dict[str, str] = {}
    for anchor_id, parts in raw_toc.items():
        seen: set[str] = set()
        unique: list[str] = []
        for p in parts:
            p = p.strip()
            if p and not p.isdigit() and p not in seen:
                seen.add(p)
                unique.append(p)
        toc_label_map[anchor_id] = " ".join(unique) or anchor_id

    # ── 3. Filter to anchors with a known byte offset ─────────────────────────
    # (excludes fact/resource anchors already filtered above, plus any href
    # targets that don't actually appear as id= in the canonical file)
    section_data: dict[str, tuple[int, str]] = {}   # anchor_id → (char_start, label)
    for anchor_id, label in toc_label_map.items():
        if anchor_id not in offset_index:
            logger.debug("ToC target %r not found in canonical bytes; skipping", anchor_id)
            continue
        section_data[anchor_id] = (offset_index[anchor_id], label)

    if not section_data:
        logger.warning(
            "%s: no ToC section anchors resolved — returning ROOT-only tree "
            "(amendment filing?)",
            canonical_path.name,
        )

    # ── 4. Sort by document order (char_start) ────────────────────────────────
    sorted_anchors: list[tuple[str, int, str]] = sorted(
        ((aid, cs, lbl) for aid, (cs, lbl) in section_data.items()),
        key=lambda x: x[1],
    )

    # ── 5. Build SectionNode list ─────────────────────────────────────────────
    n_anchors = len(sorted_anchors)
    raw_nodes: list[tuple[SectionNode, str]] = []   # (node, anchor_id)

    for i, (anchor_id, char_start, toc_label) in enumerate(sorted_anchors):
        char_end = (
            sorted_anchors[i + 1][1] if i + 1 < n_anchors else len(raw_bytes)
        )

        anchor_elem  = anchor_elem_map.get(anchor_id)
        heading_text = _extract_heading_text(anchor_elem, toc_label)
        # Use heading text as the section label (falls back to toc_label cleanly).
        label = heading_text or toc_label or anchor_id
        level = _infer_level(label, anchor_id)

        node = SectionNode(
            section_path="",         # filled in after hierarchy is resolved
            label=label,
            level=level,
            html_anchor=anchor_id,
            char_start=char_start,
            char_end=char_end,
            is_in_toc=True,
            source="toc",
            is_financial_statement=_is_financial_statement(label),
            toc_label=toc_label,
            parent_path=None,
            children_paths=[],
        )
        raw_nodes.append((node, anchor_id))

    # ── 6. Assign section_path using parent-stack walk ────────────────────────
    # Stack entries: (level, section_path)
    stack: list[tuple[int, str]] = []
    nodes: dict[str, SectionNode] = {}

    for node, anchor_id in raw_nodes:
        # Pop stack entries that are at the same or deeper level (we've moved on).
        while stack and stack[-1][0] >= node.level:
            stack.pop()

        parent_path = stack[-1][1] if stack else None
        node.parent_path = parent_path
        section_path = _make_section_path(node.label, parent_path)

        # De-duplicate section paths (rare, but two ToC entries can have identical text).
        base_path = section_path
        suffix    = 1
        while section_path in nodes:
            suffix    += 1
            section_path = f"{base_path} ({suffix})"

        node.section_path = section_path
        nodes[section_path] = node

        # Register as child of parent.
        if parent_path and parent_path in nodes:
            nodes[parent_path].children_paths.append(section_path)

        stack.append((node.level, section_path))

    # ── 7. Add ROOT sentinel node (always covers [0, file_end)) ───────────────
    root_node = SectionNode(
        section_path="ROOT",
        label="ROOT",
        level=0,
        html_anchor=None,
        char_start=0,
        char_end=len(raw_bytes),
        is_in_toc=False,
        source="root",
        is_financial_statement=False,
        toc_label=None,
        parent_path=None,
        children_paths=[sp for sp, n in nodes.items() if n.parent_path is None],
    )
    nodes["ROOT"] = root_node

    # Update children whose parent was None to point to ROOT for tree traversal.
    for sp, node in nodes.items():
        if node.parent_path is None and sp != "ROOT":
            node.parent_path = "ROOT"

    # ── 8. Build sorted arrays for bisect ─────────────────────────────────────
    sorted_nodes   = sorted(nodes.values(), key=lambda n: n.char_start)
    ordered_starts = [n.char_start for n in sorted_nodes]
    ordered_paths  = [n.section_path for n in sorted_nodes]

    logger.info(
        "%s: built SectionTree with %d nodes (%d ToC sections, %d fin-stmt)",
        canonical_path.name,
        len(nodes),
        sum(1 for n in nodes.values() if n.is_in_toc),
        sum(1 for n in nodes.values() if n.is_financial_statement),
    )

    return SectionTree(
        accession_no=accession_no,
        canonical_path=str(canonical_path),
        canonical_size_bytes=len(raw_bytes),
        nodes=nodes,
        ordered_char_starts=ordered_starts,
        ordered_section_paths=ordered_paths,
    )


# ---------------------------------------------------------------------------
# Public: extract_ixbrl_positions()
# ---------------------------------------------------------------------------

def extract_ixbrl_positions(
    canonical_path: str | Path,
) -> list[IXBRLPosition]:
    """Extract byte positions of all ``ix:nonFraction`` / ``ix:nonNumeric`` /
    ``ix:fraction`` elements from a canonical iXBRL HTML file.

    Parameters
    ----------
    canonical_path:
        Path to ``canonical.htm`` produced by :mod:`html_canonicalizer`.

    Returns
    -------
    list of IXBRLPosition
        One entry per fact element that has an ``id=`` attribute.  Facts
        without ``id=`` (rare; occurs in early TSM 20-F filings) are skipped
        with a ``WARNING`` log.

    Notes
    -----
    * ``char_start`` is the byte offset of the opening ``<`` of the
      ``ix:nonFraction`` (or sibling type) element.
    * ``char_end`` is the byte offset just after the matching closing tag.
    * ``continuation_positions`` lists byte ranges of all ``ix:continuation``
      elements chained to a multi-span fact.
    * The index scan is O(N) in file size; the per-element lookups are O(1).
    """
    canonical_path = Path(canonical_path).resolve()
    raw_bytes      = canonical_path.read_bytes()

    tree = etree.parse(str(canonical_path))
    root = tree.getroot()

    ix_prefix    = _get_ns_prefix(root, IX_NS, "ix")
    offset_index = _build_id_offset_index(raw_bytes)

    positions: list[IXBRLPosition] = []
    skipped_no_id = 0
    skipped_no_offset = 0

    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        if elem.tag not in _IX_FACT_TAGS:
            continue

        local  = elem.tag.split("}")[-1]   # "nonFraction" | "nonNumeric" | "fraction"
        xml_id = elem.get("id")
        if not xml_id:
            skipped_no_id += 1
            continue

        char_start = offset_index.get(xml_id)
        if char_start is None:
            skipped_no_offset += 1
            logger.debug("id=%r not found in byte index for %s", xml_id, canonical_path.name)
            continue

        # Find the matching closing tag.
        # Handle both explicit closing (</ix:nonFraction>) and self-closing (/>).
        close_explicit = f"</{ix_prefix}:{local}>".encode()
        pos_explicit   = raw_bytes.find(close_explicit, char_start)

        # Self-closing: look for "/> " or "/>", must be INSIDE the start tag.
        # We look for "/> " between char_start and pos_explicit (or a bound).
        search_end = pos_explicit if pos_explicit >= 0 else char_start + 10_000
        pos_selfclose = raw_bytes.find(b"/>", char_start, search_end)

        if pos_explicit < 0 and pos_selfclose < 0:
            logger.debug("Could not find end of element id=%r", xml_id)
            continue

        if pos_explicit < 0:
            char_end = pos_selfclose + 2
        elif pos_selfclose < 0:
            char_end = pos_explicit + len(close_explicit)
        else:
            # Take whichever end marker comes first.
            if pos_selfclose < pos_explicit:
                char_end = pos_selfclose + 2
            else:
                char_end = pos_explicit + len(close_explicit)

        positions.append(IXBRLPosition(
            xml_id=xml_id,
            tag_name=local,
            concept=elem.get("name", ""),
            context_ref=elem.get("contextRef", ""),
            unit_ref=elem.get("unitRef"),
            decimals=elem.get("decimals"),
            char_start=char_start,
            char_end=char_end,
            text_value=(elem.text or "").strip(),
        ))

    if skipped_no_id:
        logger.warning(
            "%s: %d ix:* fact element(s) have no id= attribute — "
            "cannot position-link (early iXBRL filing?)",
            canonical_path.name, skipped_no_id,
        )
    if skipped_no_offset:
        logger.warning(
            "%s: %d facts with id= not found in byte index",
            canonical_path.name, skipped_no_offset,
        )

    # Attach ix:continuation positions to their base facts.
    _attach_continuation_positions(root, raw_bytes, offset_index, positions, ix_prefix)

    logger.info(
        "%s: extracted %d iXBRL positions (%d skipped — no id=)",
        canonical_path.name, len(positions), skipped_no_id,
    )
    return positions


def _attach_continuation_positions(
    root: etree._Element,
    raw_bytes: bytes,
    offset_index: dict[str, int],
    positions: list[IXBRLPosition],
    ix_prefix: str,
) -> None:
    """Populate ``IXBRLPosition.continuation_positions`` from ``ix:continuation``
    elements in the tree.

    An ``ix:continuation`` with ``id="f-404-3"`` is a continuation fragment of
    the fact with base id ``"f-404"``.
    """
    pos_map: dict[str, IXBRLPosition] = {p.xml_id: p for p in positions}
    _RE_CONT_ID = re.compile(r"^(f-\d+)-\d+$")

    close_tag = f"</{ix_prefix}:continuation>".encode()

    for elem in root.iter():
        if not isinstance(elem.tag, str) or elem.tag != _IX_CONT_TAG:
            continue
        cont_id = elem.get("id", "")
        if not cont_id:
            continue
        m = _RE_CONT_ID.match(cont_id)
        if not m:
            continue
        base_id = m.group(1)
        if base_id not in pos_map:
            continue

        cs = offset_index.get(cont_id)
        if cs is None:
            continue
        ce_pos = raw_bytes.find(close_tag, cs)
        if ce_pos < 0:
            continue
        pos_map[base_id].continuation_positions.append((cs, ce_pos + len(close_tag)))
