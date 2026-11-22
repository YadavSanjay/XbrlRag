"""C7: ordinary single-document filings do not change by one byte, and so nor do their chunk ids."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.chunking.xbrl_html_base_chunking import XBRLHTMLChunker
from src.ingest.sec_edgar.document_set import arelle_entry_name, inline_document_names
from src.ingest.sec_edgar.xbrl.html_canonicalizer import (
    HTMLCanonicalizer,
    _canonicalize_html,
    merge_canonical_documents,
)
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import extract_ixbrl_positions

REL_KEY = "ACME/2025/10-K"
ACCESSION = "0000000001-26-000001"
NAME = "acme-20251231.htm"

SOURCE = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    b"<head><title>Acme</title><style>p {color: red}</style></head>"
    b'<body><div style="color: red"><a href="#item8">Item 8. Financial Statements</a></div>'
    b'<div id="item8"><p>  Balance   sheet  </p></div>'
    b'<table><tr><td><ix:nonFraction id="f-1" name="us-gaap:Assets" contextRef="c-1" '
    b'unitRef="usd" decimals="0">1,234</ix:nonFraction></td></tr></table>'
    b"<p>The end.</p></body></html>"
)

# By hand from the canonicalizer's rules: lxml's declaration, <style> and style= gone, runs of
# whitespace outside a fact collapsed to one space, nothing else touched.
CANONICAL = (
    b"<?xml version='1.0' encoding='utf-8'?>\n"
    b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    b"<head><title>Acme</title></head>"
    b'<body><div><a href="#item8">Item 8. Financial Statements</a></div>'
    b'<div id="item8"><p> Balance sheet </p></div>'
    b'<table><tr><td><ix:nonFraction id="f-1" name="us-gaap:Assets" contextRef="c-1" '
    b'unitRef="usd" decimals="0">1,234</ix:nonFraction></td></tr></table>'
    b"<p>The end.</p></body></html>"
)

# The namespace chunk ids have used since Phase 7; every stored chunk_id hangs on it.
CHUNK_NAMESPACE = uuid.UUID("c8f9a6f0-2f0a-4f2e-9b1a-6a6b9f3a9e10")


def _chunk_id(start: int, end: int) -> str:
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{ACCESSION}:{start}:{end}"))


@pytest.mark.criterion("C7")
def test_merge_of_one_document_is_the_canonical_bytes():
    # oracle: CANONICAL, written by hand above from the canonicalizer's rules
    assert _canonicalize_html(SOURCE)[0] == CANONICAL
    assert merge_canonical_documents([(NAME, CANONICAL)]) == CANONICAL


@pytest.mark.criterion("C7")
def test_chunk_ids_and_offsets_are_pinned(tmp_path):
    # oracle: closed form — offsets are bytes.find on the hand-written CANONICAL, ids are uuid5 of
    # "<accession>:<start>:<end>". The heading's div opens the first chunk at 0 (the head folds
    # into it), Item 8 starts a section, its prose runs up to the table, the table is its own
    # chunk, and the last prose runs to the end of the file
    path = tmp_path / "canonical.htm"
    path.write_bytes(merge_canonical_documents([(NAME, CANONICAL)]))
    prose = CANONICAL.index(b"<p> Balance")
    table = CANONICAL.index(b"<table>")
    last = CANONICAL.index(b"<p>The end.")
    expected = [(0, prose), (prose, table), (table, last), (last, len(CANONICAL))]
    chunks = XBRLHTMLChunker().chunk_filing(path, ACCESSION)
    assert [(c.html_char_start, c.html_char_end) for c in chunks] == expected
    assert [c.chunk_id for c in chunks] == [_chunk_id(s, e) for s, e in expected]
    fact = extract_ixbrl_positions(path)[0]
    assert fact.char_start == CANONICAL.index(b"<ix:nonFraction")
    assert fact.char_end == CANONICAL.index(b"</ix:nonFraction>") + len(b"</ix:nonFraction>")


@pytest.mark.criterion("C7")
def test_canonicalizer_output_for_a_single_document_filing(acme, tmp_path):
    # oracle: CANONICAL by hand, through the whole stage; old and new manifests build the same file
    (tmp_path / "out").mkdir()
    new_manifest = acme.manifest(ixbrl_documents=[NAME], files=[NAME])
    old_manifest = acme.manifest(files=[NAME])
    del old_manifest["ixbrl_documents"]
    assert inline_document_names(new_manifest) == inline_document_names(old_manifest) == [NAME]
    assert arelle_entry_name(new_manifest) == arelle_entry_name(old_manifest) == NAME
    for key, manifest in (("A/2025/10-K", new_manifest), ("B/2025/10-K", old_manifest)):
        acme.write_filing(tmp_path / "in" / key, {NAME: SOURCE}, manifest)
        canonicalizer = HTMLCanonicalizer(input_root=tmp_path / "in", output_root=tmp_path / "out")
        result = canonicalizer.canonicalize_filing_dir(key)
        assert result.error == "" and result.skipped is False
        assert (tmp_path / "out" / key / "canonical.htm").read_bytes() == CANONICAL


words = st.text(alphabet="abcdefg ", min_size=1, max_size=30)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(st.lists(words, min_size=1, max_size=6))
def test_merge_of_one_document_is_identity_for_any_document(acme, paragraphs):
    # oracle: metamorphic — one document in, the same bytes out, and so the same chunk ids as
    # chunking the canonical file directly
    body = "".join(f"<p>{text}x</p>" for text in paragraphs)
    canonical = _canonicalize_html(acme.page(body))[0]
    merged = merge_canonical_documents([(NAME, canonical)])
    assert merged == canonical
    with tempfile.TemporaryDirectory() as tmp:
        direct, through = Path(tmp) / "a.htm", Path(tmp) / "b.htm"
        direct.write_bytes(canonical)
        through.write_bytes(merged)
        chunker = XBRLHTMLChunker()
        ids = [c.chunk_id for c in chunker.chunk_filing(direct, ACCESSION)]
        assert ids == [c.chunk_id for c in chunker.chunk_filing(through, ACCESSION)]
