"""HTML canonicalizer for SEC EDGAR iXBRL filings.

The first step of the HTML-canonical ingestion pipeline
(``docs/design/XbrlRag_HTML_Canonical_Source.md`` §2).

Given an EDGAR submission directory (containing ``manifest.yaml`` and the
iXBRL primary ``.htm`` document), this module:

1.  Identifies the filing's inline documents via ``manifest.yaml``
    (``inline_document_names``: normally the ``primary_document`` alone; a filing
    that splits its inline XBRL across documents lists them all, and they are
    merged into one canonical file, docs/specs/ingest-multidoc-ixbrl.md).
2.  Strips rendering noise that has no semantic value for the downstream
    section-tree builder or iXBRL fact extractor:

    * ``<style>`` elements (inline CSS blocks)
    * ``<script>`` elements (JavaScript)
    * ``<link>`` elements (external CSS / icon / prefetch references)
    * ``style=`` inline-style attributes on every element
    * JavaScript event-handler attributes (``onclick``, ``onload``, …)

3.  Preserves **all semantic content**: XBRL namespace declarations, heading
    hierarchy (``<h1>``–``<h6>``), anchor IDs (``id=``, ``name=``), anchor
    links (``<a href="…">``), inline iXBRL tags (``<ix:nonFraction>``,
    ``<ix:nonNumeric>``, ``<ix:header>``, …), table structure, and every
    human-readable text node.
4.  Normalises whitespace in prose text nodes: collapses runs of spaces,
    tabs, newlines, and ``&nbsp;`` (U+00A0) to a single ASCII space.
    Text inside iXBRL *fact* elements (``<ix:nonFraction>``,
    ``<ix:nonNumeric>``, ``<ix:fraction>``) is **not** altered so that
    numeric values remain byte-for-byte identical to the source.
5.  Writes the canonical HTML artifact as ``canonical.htm`` under the
    output root, mirroring the source path structure::

        input:   <input_root>/<TICKER>/<FY>/<FORM>/           (10-K / 20-F)
        output:  <output_root>/<TICKER>/<FY>/<FORM>/canonical.htm

        input:   <input_root>/<TICKER>/<FY>/<FORM>/Q<n>/      (10-Q)
        output:  <output_root>/<TICKER>/<FY>/<FORM>/Q<n>/canonical.htm

6.  Writes ``canonicalization_meta.json`` alongside the canonical HTML,
    recording source/output paths, file sizes, SHA-256 hashes, removal
    counts, and timestamp.

Three invocation scopes
-----------------------
* **All filings** under ``input_root`` — no ticker argument.
* **All filings for one ticker** — ``--ticker NVDA``.
* **One specific filing** — ``--ticker NVDA --year 2026 --form 10-K``
  **or** ``--filing-dir <path>``.

CLI usage::

    # All filings
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer

    # Single ticker
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer --ticker NVDA

    # Single filing (ticker + year + form)
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer \\
        --ticker NVDA --year 2026 --form 10-K

    # Single filing directory (absolute or relative path)
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer \\
        --filing-dir data/storage/sec_edgar/NVDA/2026/10-K

    # Preview without writing
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer --dry-run

    # Overwrite existing canonical.htm files
    python -m src.ingest.sec_edgar.xbrl.html_canonicalizer --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import lxml.etree as etree
import yaml

from src.ingest.sec_edgar.document_set import inline_document_names, is_inline_fact
from src.ingest.sec_edgar.storage import (
    FilingStore,
    LocalFilingStore,
    default_local_root,
    ensure_writable_root,
    local_root,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace URIs
# ---------------------------------------------------------------------------

XHTML_NS = "http://www.w3.org/1999/xhtml"
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"

# Clark-notation tags of iXBRL fact elements whose text values must not be
# altered during whitespace normalisation (they carry XBRL fact values).
_IX_FACT_TAGS: frozenset[str] = frozenset(
    {
        f"{{{IX_NS}}}nonFraction",
        f"{{{IX_NS}}}nonNumeric",
        f"{{{IX_NS}}}fraction",
        f"{{{IX_NS}}}numerator",
        f"{{{IX_NS}}}denominator",
    }
)

# ---------------------------------------------------------------------------
# Removal and strip rules
# ---------------------------------------------------------------------------

# Elements to remove entirely (element + all descendants).
# Clark-notation + bare-name fallback for namespace-less EDGAR documents.
_REMOVE_TAG_SET: frozenset[str] = frozenset(
    {
        f"{{{XHTML_NS}}}style",
        f"{{{XHTML_NS}}}script",
        "style",  # bare name (some older EDGAR docs omit the XHTML namespace)
        "script",
    }
)

# Attributes stripped from every element regardless of namespace context.
_STRIP_ATTR_SET: frozenset[str] = frozenset(
    {
        "style",
        # JavaScript event handlers — not semantically useful
        "onclick",
        "ondblclick",
        "onmousedown",
        "onmouseup",
        "onmouseover",
        "onmousemove",
        "onmouseout",
        "onkeydown",
        "onkeypress",
        "onkeyup",
        "onload",
        "onunload",
        "onfocus",
        "onblur",
        "onsubmit",
        "onreset",
        "onchange",
        "onselect",
    }
)

# Whitespace collapse regex: any run of space / tab / newline / NBSP → one space.
_WS_RE = re.compile(r"[\s ]+")

# ---------------------------------------------------------------------------
# Default paths (match the SEC EDGAR downloader defaults)
# ---------------------------------------------------------------------------

_DEFAULT_INPUT_ROOT = default_local_root("sec_edgar")
_DEFAULT_OUTPUT_ROOT = default_local_root("normalized")
_CANONICAL_FILENAME = "canonical.htm"
_META_FILENAME = "canonicalization_meta.json"

# Attributes that refer to one id, and to a space-separated list of ids, of the same filing.
_ID_REFERENCE_ATTRIBUTES = ("contextRef", "unitRef", "continuedAt")
_ID_LIST_ATTRIBUTES = ("fromRefs", "toRefs")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CanonResult:
    """Outcome of canonicalizing one filing directory."""

    ticker: str
    fiscal_year: int
    form: str
    quarter: int | None
    accession_no: str | None
    source_path: str
    canonical_path: str
    source_size_bytes: int
    canonical_size_bytes: int
    source_sha256: str
    canonical_sha256: str
    elements_removed: dict[str, int] = field(default_factory=dict)
    attrs_stripped: int = 0
    canonicalized_at: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""
    source_documents: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTMLCanonicalizer
# ---------------------------------------------------------------------------


class HTMLCanonicalizer:
    """Canonicalize SEC EDGAR iXBRL primary HTML documents.

    Parameters
    ----------
    input_root:
        Root directory of the SEC EDGAR local storage tree.
        Default: ``<repo>/data/storage/sec_edgar``.
    output_root:
        Root directory where canonical artifacts are written.
        Default: ``<repo>/data/storage/xbrl/normalized``.
    force:
        When ``True``, overwrite existing ``canonical.htm`` files.
        When ``False`` (default), existing outputs are skipped.
    """

    def __init__(
        self,
        input_root: str | Path = _DEFAULT_INPUT_ROOT,
        output_root: str | Path = _DEFAULT_OUTPUT_ROOT,
        force: bool = False,
    ) -> None:
        self._input_root = Path(input_root).expanduser().resolve()
        self._output_root = Path(output_root).expanduser().resolve()
        self._force = force
        # Read the raw filings, write the canonical tree (CL #4b). Local mode maps
        # each role to its own root (identical to the paths above); S3 mode maps
        # them to two prefixes in one bucket. Both directions are byte-keyed so the
        # canonicalizer never needs a local file except via the store.
        self._in_store = FilingStore.from_env(root=input_root, role="sec_edgar")
        self._out_store = FilingStore.from_env(root=output_root, role="normalized")
        logger.info(
            "HTMLCanonicalizer  input_root=%s  output_root=%s  force=%s",
            self._input_root,
            self._output_root,
            force,
        )
        # Preflight the *output* root only (the input tree is read-only). S3 has no
        # local dir to check. Without this the first write fails deep inside
        # pathlib.mkdir naming a leaf dir, which reads as a missing input rather
        # than a root-owned mount the container's uid cannot write.
        if isinstance(self._out_store, LocalFilingStore):
            ensure_writable_root(self._output_root, role="normalized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def canonicalize_all(self) -> list[CanonResult]:
        """Canonicalize every filing discovered under the input store.

        Raises
        ------
        FileNotFoundError
            If the tree holds no filings at all. A whole-tree run that discovers
            nothing means the root is wrong, not that there is no work — and
            reporting success would let the pipeline continue to
            ``dom-positions``, which then finds no ``canonical.htm`` and leaves
            ``fact_dom_position`` empty. Fail here, where the cause is legible.
        """
        keys = self._discover_filing_keys()
        logger.info("Discovered %d filing dir(s) under %s", len(keys), self._input_root)
        if not keys:
            raise FileNotFoundError(
                f"No filings found under {self._input_root}. Check SEC_EDGAR_ROOT — "
                f"a Windows-style default is a *relative* path on Linux, so it "
                f"resolves against the process cwd (note the root above starting "
                f"with the working directory). Or pass --input-root explicitly."
            )
        return [self.canonicalize_filing_dir(k) for k in keys]

    def canonicalize_ticker(self, ticker: str) -> list[CanonResult]:
        """Canonicalize all filings for *ticker* (e.g. ``"NVDA"``)."""
        keys = self._discover_filing_keys(ticker.upper())
        logger.info("Discovered %d filing dir(s) for %s", len(keys), ticker.upper())
        return [self.canonicalize_filing_dir(k) for k in keys]

    def canonicalize_filing_dir(self, filing_dir: str | Path) -> CanonResult:
        """Canonicalize a single filing directory / store key.

        Accepts a local path (absolute or relative to ``input_root``) or a
        store-relative key. The location must contain ``manifest.yaml`` with a
        ``primary_document`` key pointing to the iXBRL ``.htm`` file. Every document
        of ``inline_document_names`` is canonicalized and merged into one file; a
        listed document that is missing or fails is an error, never a skip. All I/O
        goes through the input/output stores, so the same code path serves both
        the local FS and S3 (deploy_mode=aws).
        """
        rel_key = self._to_rel_key(filing_dir)
        # display path: the real local path in local mode, else the store key
        in_display = str(self._in_store.local_path(rel_key) or rel_key)
        manifest = _load_manifest_via_store(self._in_store, rel_key)

        if manifest is None:
            return _make_skip(in_display, "no manifest.yaml found")

        ticker = str(manifest.get("ticker", "UNKNOWN"))
        fy = int(manifest.get("fiscal_year") or 0)
        form = str(manifest.get("form") or manifest.get("filing_type") or "UNKNOWN")
        quarter = manifest.get("quarter")
        accession = manifest.get("accession_no")

        primary = manifest.get("primary_document")
        if not primary:
            return _make_skip(
                in_display,
                "manifest missing primary_document",
                ticker,
                fy,
                form,
                quarter,
                accession,
            )

        source_key = f"{rel_key}/{primary}" if rel_key else str(primary)
        document_keys = {
            name: f"{rel_key}/{name}" if rel_key else name
            for name in inline_document_names(manifest)
        }
        # A manifest that lists its documents promises them; an older one does not.
        lists_documents = bool(manifest.get("ixbrl_documents"))
        if not self._in_store.exists(source_key) and not lists_documents:
            return _make_skip(
                in_display,
                f"primary_document {primary!r} not found in {in_display}",
                ticker,
                fy,
                form,
                quarter,
                accession,
            )
        if Path(str(primary)).suffix.lower() not in (".htm", ".html"):
            return _make_skip(
                in_display,
                f"primary_document {primary!r} is not an .htm/.html file",
                ticker,
                fy,
                form,
                quarter,
                accession,
            )

        canon_key = (
            f"{rel_key}/{_CANONICAL_FILENAME}" if rel_key else _CANONICAL_FILENAME
        )
        meta_key = f"{rel_key}/{_META_FILENAME}" if rel_key else _META_FILENAME
        source_display = str(self._in_store.local_path(source_key) or source_key)
        canon_display = str(self._out_store.local_path(canon_key) or canon_key)

        def failed(message: str, source_size: int = 0) -> CanonResult:
            return CanonResult(
                ticker=ticker,
                fiscal_year=fy,
                form=form,
                quarter=quarter,
                accession_no=accession,
                source_path=source_display,
                canonical_path=canon_display,
                source_size_bytes=source_size,
                canonical_size_bytes=0,
                source_sha256="",
                canonical_sha256="",
                canonicalized_at=_now_iso(),
                error=message,
                source_documents=list(document_keys),
            )

        missing = [n for n, key in document_keys.items() if not self._in_store.exists(key)]
        if missing:
            return failed(f"listed document(s) not found: {', '.join(missing)}")

        if self._out_store.exists(canon_key) and not self._force:
            logger.debug("Skip (exists): %s", in_display)
            return _make_skip(
                in_display,
                "already canonicalized (use force=True / --force to overwrite)",
                ticker,
                fy,
                form,
                quarter,
                accession,
                source_display,
                canon_display,
            )

        sources = {n: self._in_store.read_bytes(key) for n, key in document_keys.items()}
        src_bytes = b"".join(sources.values())

        # ── Canonicalize ────────────────────────────────────────────────────
        canonical: list[tuple[str, bytes]] = []
        elements_removed: Counter[str] = Counter()
        attrs_stripped = 0
        step = "merge"
        try:
            for name, source in sources.items():
                step = name
                document, stats = _canonicalize_html(source, name=name)
                canonical.append((name, document))
                elements_removed.update(stats["elements_removed"])
                attrs_stripped += stats["attrs_stripped"]
            step = "merge"
            canon_bytes = merge_canonical_documents(canonical)
        except Exception as exc:
            logger.exception("Canonicalization failed for %s", source_display)
            return failed(f"{step}: {exc}", len(src_bytes))

        self._out_store.write_bytes(canon_key, canon_bytes)

        result = CanonResult(
            ticker=ticker,
            fiscal_year=fy,
            form=form,
            quarter=quarter,
            accession_no=accession,
            source_path=source_display,
            canonical_path=canon_display,
            source_size_bytes=len(src_bytes),
            canonical_size_bytes=len(canon_bytes),
            source_sha256=hashlib.sha256(src_bytes).hexdigest(),
            canonical_sha256=hashlib.sha256(canon_bytes).hexdigest(),
            elements_removed=dict(elements_removed),
            attrs_stripped=attrs_stripped,
            canonicalized_at=_now_iso(),
            source_documents=list(document_keys),
        )
        self._out_store.write_text(
            meta_key, json.dumps(asdict(result), indent=2, default=str)
        )
        logger.info(
            "OK  %-5s %-7s FY%-4d  %s → %s  (%s → %s bytes, -%d elems, -%d attrs)",
            ticker,
            form,
            fy,
            str(primary),
            _CANONICAL_FILENAME,
            f"{result.source_size_bytes:,}",
            f"{result.canonical_size_bytes:,}",
            sum(result.elements_removed.values()),
            result.attrs_stripped,
        )
        return result

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_filing_keys(self, prefix: str = "") -> list[str]:
        """Return store-relative keys of every filing dir (a manifest's parent)
        under *prefix*, via the input store (works for local FS and S3)."""
        keys = set()
        for mkey in self._in_store.iter_manifest_keys(prefix):
            parent = mkey.rsplit("/", 1)[0] if "/" in mkey else ""
            keys.add(parent)
        return sorted(keys)

    def _discover_filing_dirs(self, root: Path) -> list[Path]:
        """Local-FS discovery (retained for the Path-based CLI scope helpers)."""
        return sorted(p.parent for p in root.rglob("manifest.yaml") if p.is_file())

    def _to_rel_key(self, filing_ref: str | Path) -> str:
        """Normalize a filing reference to a store-relative key.

        Accepts an absolute local path under ``input_root`` (relativized), a path
        outside it (last three components, matching :meth:`_output_dir`'s
        fallback), or an already-relative store key (passed through)."""
        p = Path(str(filing_ref))
        if p.is_absolute():
            try:
                return p.relative_to(self._input_root).as_posix()
            except ValueError:
                parts = p.parts
                return Path(*parts[-3:]).as_posix() if len(parts) >= 3 else p.name
        return str(filing_ref).replace("\\", "/").strip("/")

    # ------------------------------------------------------------------
    # Output path derivation
    # ------------------------------------------------------------------

    def _output_dir(self, filing_dir: Path) -> Path:
        """Map *filing_dir* (under ``input_root``) to a path under ``output_root``.

        The relative path is preserved::

            input_root/NVDA/2026/10-K  →  output_root/NVDA/2026/10-K
        """
        try:
            rel = filing_dir.relative_to(self._input_root)
        except ValueError:
            # filing_dir is outside input_root (e.g. passed via --filing-dir);
            # use the last three path components to avoid collisions.
            parts = filing_dir.parts
            rel = Path(*parts[-3:]) if len(parts) >= 3 else Path(filing_dir.name)
        return self._output_root / rel


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _load_manifest(filing_dir: Path) -> dict[str, Any] | None:
    mp = filing_dir / "manifest.yaml"
    if not mp.exists():
        return None
    try:
        return yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("manifest parse error: %s", mp)
        return None


def _load_manifest_via_store(store, rel_key: str) -> dict[str, Any] | None:
    """Load ``manifest.yaml`` for *rel_key* through a FilingStore (local or S3)."""
    key = f"{rel_key}/manifest.yaml" if rel_key else "manifest.yaml"
    if not store.exists(key):
        return None
    try:
        return yaml.safe_load(store.read_text(key)) or {}
    except Exception:
        logger.exception("manifest parse error: %s", key)
        return None


def _canonicalize_html(
    source_bytes: bytes, name: str = "document"
) -> tuple[bytes, dict[str, Any]]:
    """Parse, transform, and re-serialise the iXBRL HTML in *source_bytes*.

    Takes bytes (not a path) so the same code canonicalizes a local file or an
    S3 object — the caller reads via its FilingStore. *name* is used only in log
    messages. Returns ``(canonical_bytes, stats_dict)`` where *stats_dict* has
    keys ``elements_removed`` (dict of localname → count) and ``attrs_stripped``
    (int).
    """
    stats: dict[str, Any] = {"elements_removed": {}, "attrs_stripped": 0}

    # ── 1. Parse ─────────────────────────────────────────────────────────────
    # EDGAR iXBRL documents are well-formed XHTML (generated by Workiva / similar
    # tools), so the strict XML parser is the correct first choice. Bytes with an
    # encoding declaration parse fine (unlike a str, which lxml rejects).
    try:
        root = etree.fromstring(source_bytes)
    except etree.XMLSyntaxError:
        # Fallback: lxml HTML recovery parser (loses some namespace declarations
        # but keeps the document structure).
        logger.warning(
            "%s is not well-formed XML; retrying with HTML recovery parser", name
        )
        root = etree.fromstring(source_bytes, parser=etree.HTMLParser(encoding="utf-8"))

    # ── 2. Collect elements to remove ────────────────────────────────────────
    to_remove: list[etree._Element] = []
    for elem in root.iter():
        if not isinstance(elem.tag, str):  # skip PI / comments
            continue
        tag = elem.tag
        local = tag.split("}")[-1] if "}" in tag else tag

        # style / script — drop entirely
        if tag in _REMOVE_TAG_SET:
            to_remove.append(elem)
            continue

        # <link …> (XHTML head metadata — stylesheets, icons, prefetch refs)
        # This matches {XHTML_NS}link and bare "link" but NOT ix:schemaRef,
        # link:schemaRef, etc. (their local names are "schemaRef", not "link").
        if local == "link" and (tag == f"{{{XHTML_NS}}}link" or "}" not in tag):
            to_remove.append(elem)

    # ── 3. Remove collected elements ─────────────────────────────────────────
    _remove_elements(to_remove, stats)

    # ── 4. Strip attributes + normalise whitespace ───────────────────────────
    _transform(root, stats)

    # ── 5. Serialise ─────────────────────────────────────────────────────────
    # Output as UTF-8 XML so namespace declarations are preserved verbatim.
    # pretty_print=False avoids inserting whitespace that could interfere with
    # downstream char-offset calculations in the section-tree builder.
    canon_bytes: bytes = etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=False,
    )
    return canon_bytes, stats


def merge_canonical_documents(documents: Sequence[tuple[str, bytes]]) -> bytes:
    """Merge the canonical bytes of a filing's documents into one document.

    Args:
        documents: ``(file name, canonical bytes)`` in filing order, primary first.

    Returns:
        One document, one offset space. A single document comes back byte for byte.

    Raises:
        ValueError: Two documents carry the same inline fact id, a document has no body, or
            ``documents`` is empty.
    """
    if not documents:
        raise ValueError("No documents to merge")
    if len(documents) == 1:
        return documents[0][1]

    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    roots = [etree.fromstring(data, parser) for _, data in documents]
    names = [name for name, _ in documents]
    bodies = [_body_of(root, name) for root, name in zip(roots, names, strict=True)]
    # Everything that survives the merge: the whole first document, the body of each later one.
    kept = [
        [e for e in root.iter() if isinstance(e.tag, str)] if position == 0
        else [e for e in body.iterdescendants() if isinstance(e.tag, str)]
        for position, (root, body) in enumerate(zip(roots, bodies, strict=True))
    ]

    renames = _anchor_renames(kept, _fact_owners(kept, names))
    renames_by_name = dict(zip(names, renames, strict=True))
    for elements, own in zip(kept, renames, strict=True):
        _apply_anchor_renames(elements, own, renames_by_name)

    # lxml drops a declaration no tag uses when it moves an element, and a QName in an attribute
    # value (name="us-gaap:Assets") still needs it, so the merged root binds every prefix.
    nsmap: dict[str | None, str] = {}
    for root in roots:
        for prefix, uri in root.nsmap.items():
            nsmap.setdefault(prefix, uri)
    merged = etree.Element(roots[0].tag, attrib=dict(roots[0].attrib), nsmap=nsmap)
    merged.text = roots[0].text
    merged.extend(list(roots[0]))
    target = _body_of(merged, names[0])
    for body in bodies[1:]:
        if body.text:
            if len(target):
                target[-1].tail = (target[-1].tail or "") + body.text
            else:
                target.text = (target.text or "") + body.text
        target.extend(list(body))
    return etree.tostring(merged, encoding="utf-8", xml_declaration=True, pretty_print=False)


def _body_of(root: etree._Element, name: str) -> etree._Element:
    """Return a document's body element, or raise ``ValueError`` naming the document."""
    for child in root:
        if isinstance(child.tag, str) and child.tag.rpartition("}")[2].lower() == "body":
            return child
    raise ValueError(f"{name} has no body")


def _anchor_attributes(element: etree._Element) -> tuple[str, ...]:
    """Return the attributes that define a link target: ``id`` and, on ``<a>``, ``name``."""
    return ("id", "name") if element.tag.rpartition("}")[2].lower() == "a" else ("id",)


def _fact_owners(kept: Sequence[list[etree._Element]], names: Sequence[str]) -> dict[str, str]:
    """Map each inline fact id to its document, refusing an id two documents share."""
    owners: dict[str, str] = {}
    for elements, name in zip(kept, names, strict=True):
        for element in elements:
            fact_id = element.get("id")
            if fact_id and is_inline_fact(element):
                if owners.setdefault(fact_id, name) != name:
                    raise ValueError(
                        f"Fact id {fact_id!r} is in both {owners[fact_id]} and {name}"
                    )
    return owners


def _anchor_renames(
    kept: Sequence[list[etree._Element]], fact_ids: dict[str, str]
) -> list[dict[str, str]]:
    """Give each non-fact anchor an id no other document uses.

    The first document to use an anchor keeps it. A fact id is never renamed, so an anchor that
    shares one gives way wherever it is.

    Args:
        kept: Each document's surviving elements, in filing order.
        fact_ids: Every inline fact id of the filing.

    Returns:
        For each document, its anchors' new names.
    """
    taken = set(fact_ids)
    plans: list[dict[str, str]] = []
    for position, elements in enumerate(kept, start=1):
        anchors = dict.fromkeys(
            element.get(attribute)
            for element in elements
            if not is_inline_fact(element)
            for attribute in _anchor_attributes(element)
            if element.get(attribute)
        )
        plan: dict[str, str] = {}
        for anchor in anchors:
            if anchor not in taken:
                continue
            attempt = 1
            candidate = f"{anchor}-doc{position}"
            while candidate in taken or candidate in anchors:
                attempt += 1
                candidate = f"{anchor}-doc{position}-{attempt}"
            plan[anchor] = candidate
            taken.add(candidate)
        taken.update(anchors)
        plans.append(plan)
    return plans


def _apply_anchor_renames(
    elements: Sequence[etree._Element],
    own: dict[str, str],
    renames_by_name: dict[str, dict[str, str]],
) -> None:
    """Rename a document's anchors and point its references at the merged document.

    Args:
        elements: The document's surviving elements.
        own: This document's renames.
        renames_by_name: Every document's renames, by file name.
    """
    for element in elements:
        for attribute in () if is_inline_fact(element) else _anchor_attributes(element):
            value = element.get(attribute)
            if value in own:
                element.set(attribute, own[value])
        for attribute in _ID_REFERENCE_ATTRIBUTES:
            value = element.get(attribute)
            if value in own:
                element.set(attribute, own[value])
        for attribute in _ID_LIST_ATTRIBUTES:
            value = element.get(attribute)
            if value:
                element.set(attribute, " ".join(own.get(i, i) for i in value.split()))
        href = element.get("href")
        if href and "#" in href:
            target, _, fragment = href.partition("#")
            if not target or target in renames_by_name:
                renames = renames_by_name[target] if target else own
                element.set("href", f"#{renames.get(fragment, fragment)}")


def _remove_elements(
    to_remove: list[etree._Element],
    stats: dict[str, Any],
) -> None:
    """Remove *to_remove* from their parents, splicing tail text into the tree.

    Processes in reverse document order so child elements are dropped before
    their parents (avoids double-removal complications).
    """
    processed: set[int] = set()
    for elem in reversed(to_remove):
        eid = id(elem)
        if eid in processed:
            continue
        processed.add(eid)

        parent = elem.getparent()
        if parent is None:
            continue

        local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        stats["elements_removed"][local] = stats["elements_removed"].get(local, 0) + 1

        # The tail text is the text node that follows *this* element's closing
        # tag in the source; it belongs to the parent's flow, not to the
        # element being dropped.  Splice it into the preceding sibling's tail
        # (or the parent's text) so prose continuity is maintained.
        tail = elem.tail or ""
        if tail:
            prev = elem.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail

        parent.remove(elem)


def _transform(root: etree._Element, stats: dict[str, Any]) -> None:
    """Strip unwanted attributes and normalise whitespace.

    Uses ``etree.iterwalk`` with ``start``/``end`` events to track depth
    inside iXBRL fact elements.  Text inside fact elements (the actual
    XBRL-tagged values) is left unmodified.
    """
    inside_fact_depth = 0

    for event, elem in etree.iterwalk(root, events=("start", "end")):
        if not isinstance(elem.tag, str):
            continue

        is_fact = elem.tag in _IX_FACT_TAGS

        if event == "start":
            # ── Strip attributes ────────────────────────────────────────
            for attr in list(elem.attrib.keys()):
                if attr in _STRIP_ATTR_SET:
                    del elem.attrib[attr]
                    stats["attrs_stripped"] += 1

            if is_fact:
                inside_fact_depth += 1

            # ── Normalise .text (text between start tag and first child) ─
            # Skip if we are inside (or just entered) a fact element.
            if inside_fact_depth == 0 and elem.text:
                elem.text = _normalise_ws(elem.text)

        elif event == "end":
            if is_fact and inside_fact_depth > 0:
                inside_fact_depth -= 1

            # ── Normalise .tail (text after this element's closing tag) ──
            # .tail is in the parent's flow, not the fact's; normalise it
            # once we have left the fact element (depth back to 0).
            if inside_fact_depth == 0 and elem.tail:
                elem.tail = _normalise_ws(elem.tail)


def _normalise_ws(text: str) -> str:
    """Collapse any run of whitespace / NBSP characters to a single space."""
    return _WS_RE.sub(" ", text)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _make_skip(
    filing_dir: Path,
    reason: str,
    ticker: str = "?",
    fy: int = 0,
    form: str = "?",
    quarter: int | None = None,
    accession: str | None = None,
    source_path: str = "",
    canonical_path: str = "",
) -> CanonResult:
    logger.warning("Skip  %s : %s", filing_dir, reason)
    return CanonResult(
        ticker=ticker,
        fiscal_year=fy,
        form=form,
        quarter=quarter,
        accession_no=accession,
        source_path=source_path,
        canonical_path=canonical_path,
        source_size_bytes=0,
        canonical_size_bytes=0,
        source_sha256="",
        canonical_sha256="",
        canonicalized_at=_now_iso(),
        skipped=True,
        skip_reason=reason,
    )


def _dir_safe_form(form: str) -> str:
    """Convert a form type to a filesystem-safe directory name.

    Examples: ``10-K/A`` → ``10-K_A``,  ``10-K`` → ``10-K``.
    """
    return form.replace("/", "_")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.ingest.sec_edgar.xbrl.html_canonicalizer``."""
    p = argparse.ArgumentParser(
        prog="html_canonicalizer",
        description=(
            "Canonicalize SEC EDGAR iXBRL primary HTML files: strip style/script/link "
            "elements and inline style attributes, normalise whitespace, write "
            "canonical.htm + canonicalization_meta.json under the output root."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("CLI usage::")[1] if "CLI usage::" in __doc__ else "",
    )

    p.add_argument(
        "--input-root",
        default=local_root("sec_edgar"),
        metavar="DIR",
        help=f"Root of local SEC EDGAR storage. Default: {_DEFAULT_INPUT_ROOT}",
    )
    p.add_argument(
        "--output-root",
        default=local_root("normalized"),
        metavar="DIR",
        help=f"Root for canonical output artifacts. Default: {_DEFAULT_OUTPUT_ROOT}",
    )
    p.add_argument(
        "--ticker",
        default=None,
        metavar="TICKER",
        help="Limit to a single ticker (e.g. NVDA). Default: all tickers.",
    )
    p.add_argument(
        "--year",
        default=None,
        type=int,
        metavar="YEAR",
        help="Limit to a specific fiscal year (e.g. 2026). Requires --ticker.",
    )
    p.add_argument(
        "--form",
        default=None,
        metavar="FORM",
        help="Limit to a specific form type (e.g. 10-K). Requires --ticker and --year.",
    )
    p.add_argument(
        "--filing-dir",
        default=None,
        metavar="DIR",
        help="Canonicalize a specific filing directory. Overrides --ticker/--year/--form.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing canonical.htm files.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover filings and report what would be done; no files written.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = p.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    canonicalizer = HTMLCanonicalizer(
        input_root=args.input_root,
        output_root=args.output_root,
        force=args.force,
    )

    # ── Dry-run: discover and report ───────────────────────────────────────
    if args.dry_run:
        dirs = _resolve_scope(args, canonicalizer, Path(args.input_root))
        print(f"DRY RUN — would process {len(dirs)} filing dir(s):")
        for d in sorted(dirs):
            out = canonicalizer._output_dir(d) / _CANONICAL_FILENAME
            status = "  [exists]" if out.exists() else ""
            print(f"  {d}{status}")
            print(f"      -> {out}")
        return 0

    # ── Live run ───────────────────────────────────────────────────────────
    if args.filing_dir:
        results = [canonicalizer.canonicalize_filing_dir(args.filing_dir)]
    elif args.ticker:
        if args.year:
            dirs = _resolve_scope(args, canonicalizer, Path(args.input_root))
            results = [canonicalizer.canonicalize_filing_dir(d) for d in dirs]
        else:
            results = canonicalizer.canonicalize_ticker(args.ticker)
    else:
        results = canonicalizer.canonicalize_all()

    _print_summary(results)
    return 1 if any(r.error for r in results) else 0


def _resolve_scope(
    args: argparse.Namespace,
    canonicalizer: HTMLCanonicalizer,
    input_root: Path,
) -> list[Path]:
    """Return the list of filing directories matching the CLI scope arguments."""
    if args.filing_dir:
        return [Path(args.filing_dir).expanduser().resolve()]

    if args.ticker:
        ticker_dir = input_root / args.ticker.upper()
        if args.year:
            year_dir = ticker_dir / str(args.year)
            if not year_dir.is_dir():
                return []
            if args.form:
                form_dir = year_dir / _dir_safe_form(args.form)
                if not form_dir.is_dir():
                    return []
                dirs = canonicalizer._discover_filing_dirs(form_dir)
                # If form_dir itself contains a manifest (no quarter sub-dir)
                if not dirs and (form_dir / "manifest.yaml").exists():
                    dirs = [form_dir]
                return dirs
            return canonicalizer._discover_filing_dirs(year_dir)
        if ticker_dir.is_dir():
            return canonicalizer._discover_filing_dirs(ticker_dir)
        return []

    return canonicalizer._discover_filing_dirs(input_root)


def _print_summary(results: list[CanonResult]) -> None:
    done = [r for r in results if not r.skipped and not r.error]
    skipped = [r for r in results if r.skipped]
    errors = [r for r in results if r.error]

    print("\n=== HTML Canonicalization ===")
    print(f"  Canonicalized : {len(done)}")
    print(f"  Skipped       : {len(skipped)}")
    print(f"  Errors        : {len(errors)}")

    if done:
        total_removed = sum(sum(r.elements_removed.values()) for r in done)
        total_attrs = sum(r.attrs_stripped for r in done)
        size_before = sum(r.source_size_bytes for r in done)
        size_after = sum(r.canonical_size_bytes for r in done)
        pct = (1 - size_after / size_before) * 100 if size_before else 0.0
        print(f"  Elements removed : {total_removed:,}")
        print(f"  Attrs stripped   : {total_attrs:,}")
        print(
            f"  Size             : {size_before:,} -> {size_after:,} bytes "
            f"({pct:.1f}% smaller)"
        )

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for r in errors:
            print(f"    [{r.ticker} FY{r.fiscal_year} {r.form}] {r.error}")

    if skipped:
        reasons: dict[str, int] = {}
        for r in skipped:
            reasons[r.skip_reason] = reasons.get(r.skip_reason, 0) + 1
        print(f"\n  Skip reasons:")
        for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    ({cnt}×) {reason}")


if __name__ == "__main__":
    sys.exit(main())
