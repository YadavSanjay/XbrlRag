"""C5, C6: one canonical document, one offset space, covering every document of the filing."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import lxml.etree as etree
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.chunking.xbrl_html_base_chunking import XBRLHTMLChunker
from src.ingest.sec_edgar.xbrl.html_canonicalizer import (
    HTMLCanonicalizer,
    _canonicalize_html,
    merge_canonical_documents,
)
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    build_section_tree,
    extract_ixbrl_positions,
)

XHTML = "{http://www.w3.org/1999/xhtml}"
REL_KEY = "ACME/2025/10-K"


def _canonical(document: bytes) -> bytes:
    return _canonicalize_html(document)[0]


def _merged(acme: SimpleNamespace) -> bytes:
    return merge_canonical_documents(
        [(acme.main, _canonical(acme.main_bytes)), (acme.second, _canonical(acme.second_bytes))]
    )


def _written(directory: Path, merged: bytes) -> Path:
    path = directory / "canonical.htm"
    path.write_bytes(merged)
    return path


def _element_with_text(root: etree._Element, text: str) -> etree._Element:
    elements = (e for e in root.iter() if isinstance(e.tag, str))
    return next(e for e in elements if (e.text or "").startswith(text))


@pytest.mark.criterion("C5")
def test_facts_of_both_documents_resolve_to_their_own_bytes(acme, tmp_path):
    # oracle: the fixture by eye — f-1 in the main document, f-2 and f-3 in the second. Each
    # position must slice the merged bytes to that fact's own element and value
    merged = _merged(acme)
    path = _written(tmp_path, merged)
    positions = {p.xml_id: p for p in extract_ixbrl_positions(path)}
    assert sorted(positions) == ["f-1", "f-2", "f-3"]
    for fact_id, value in (("f-1", b"Acme Corp"), ("f-2", b"1,234"), ("f-3", b"567")):
        element = merged[positions[fact_id].char_start : positions[fact_id].char_end]
        assert element.startswith(b"<ix:non")
        assert f'id="{fact_id}"'.encode() in element
        assert value in element
    assert positions["f-1"].char_start < positions["f-2"].char_start < positions["f-3"].char_start


@pytest.mark.criterion("C5")
def test_one_document_in_filing_order(acme):
    # oracle: requirement 5 — a single root, a single body, the second document after the first
    merged = _merged(acme)
    root = etree.fromstring(merged)
    assert len(root.findall(f"{XHTML}body")) == 1
    assert len(list(root.iter(f"{XHTML}html"))) == 1
    assert merged.index(b"Acme makes anvils") < merged.index(b"Inventories are stated")


@pytest.mark.criterion("C5")
def test_a_link_into_the_second_document_makes_a_section(acme, tmp_path):
    # oracle: the fixture — the main document's ToC links to acme-20251231_d2.htm#i8, so Item 8
    # starts at <div id="i8"> and the balance sheet's fact falls inside it
    path = _written(tmp_path, _merged(acme))
    tree = build_section_tree(path, acme.accession)
    item_8 = next(n for n in tree.nodes.values() if n.html_anchor == "i8")
    assert "Item 8" in item_8.label
    positions = {p.xml_id: p for p in extract_ixbrl_positions(path)}
    assert tree.find_section(positions["f-2"].char_start) == item_8.section_path
    assert tree.find_section(positions["f-3"].char_start) == item_8.section_path
    assert tree.find_section(positions["f-1"].char_start) != item_8.section_path


@pytest.mark.criterion("C5")
def test_chunks_cover_the_second_document(acme, tmp_path):
    # oracle: requirement 5 — text and tables of the second document are chunks, the gap-free
    # coverage of chunk_filing holds across the join, and a table fact sits in a table chunk
    merged = _merged(acme)
    path = _written(tmp_path, merged)
    chunks = XBRLHTMLChunker().chunk_filing(path, acme.accession)
    assert chunks[0].html_char_start == 0 and chunks[-1].html_char_end == len(merged)
    assert all(
        a.html_char_end == b.html_char_start for a, b in zip(chunks, chunks[1:], strict=False)
    )
    assert any("Inventories are stated" in c.text for c in chunks)
    fact = next(p for p in extract_ixbrl_positions(path) if p.xml_id == "f-2")
    holder = next(c for c in chunks if c.html_char_start <= fact.char_start < c.html_char_end)
    assert holder.contains_table is True and "Total assets" in holder.text
    assert "Item 8" in holder.section_path


@pytest.mark.criterion("C5")
def test_a_colliding_anchor_stays_with_its_own_document(acme):
    # oracle: requirement 5 — both documents use id="top" for their own heading. The first keeps
    # it. The second's heading gets a new id, and each link to it, local or cross-document, follows
    first = acme.page(
        '<div id="top">First heading</div>'
        '<p><a href="#top">to first</a></p>'
        f'<p><a href="{acme.second}#top">to second</a></p>'
    )
    second = acme.page(
        '<div id="top">Second heading</div>'
        '<p><a href="#top">back to second</a></p>'
        f'<p>{acme.number("f-2")}</p>'
    )
    root = etree.fromstring(
        merge_canonical_documents(
            [(acme.main, _canonical(first)), (acme.second, _canonical(second))]
        )
    )
    ids = [e.get("id") for e in root.iter() if isinstance(e.tag, str) and e.get("id")]
    assert len(ids) == len(set(ids))
    assert _element_with_text(root, "First heading").get("id") == "top"
    renamed = _element_with_text(root, "Second heading").get("id")
    assert renamed != "top"
    assert _element_with_text(root, "to first").get("href") == "#top"
    assert _element_with_text(root, "to second").get("href") == f"#{renamed}"
    assert _element_with_text(root, "back to second").get("href") == f"#{renamed}"
    assert "f-2" in ids


@pytest.mark.criterion("C5")
def test_the_same_fact_id_in_two_documents_is_refused(acme):
    # oracle: requirement 5 — fact.xml_id joins a fact to one position; two would be a guess
    one = _canonical(acme.document_with(["f-1"]))
    with pytest.raises(ValueError, match="f-1"):
        merge_canonical_documents([(acme.main, one), (acme.second, one)])


@pytest.mark.criterion("C5")
def test_no_documents_is_refused():
    # oracle: the interface — nothing to merge is a caller's error, not an empty document
    with pytest.raises(ValueError):
        merge_canonical_documents([])


fact_counts = st.lists(st.integers(min_value=0, max_value=4), min_size=2, max_size=4)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(fact_counts)
def test_every_fact_of_any_split_is_found_exactly_once(acme, counts):
    # oracle: metamorphic — however facts split across documents, the merged positions are all of
    # them in filing order, each slicing to its own id, and merging is deterministic
    documents, expected, next_id = [], [], 0
    for index, count in enumerate(counts):
        fact_ids = [f"f-{next_id + i}" for i in range(count)]
        next_id += count
        expected += fact_ids
        documents.append((f"doc{index}.htm", _canonical(acme.document_with(fact_ids))))
    merged = merge_canonical_documents(documents)
    assert merged == merge_canonical_documents(documents)
    with tempfile.TemporaryDirectory() as tmp:
        positions = extract_ixbrl_positions(_written(Path(tmp), merged))
    assert [p.xml_id for p in positions] == expected
    for position in positions:
        assert f'id="{position.xml_id}"'.encode() in merged[position.char_start : position.char_end]


def _canonicalizer(root: Path, force: bool = False) -> HTMLCanonicalizer:
    (root / "out").mkdir(exist_ok=True)
    return HTMLCanonicalizer(input_root=root / "in", output_root=root / "out", force=force)


def _files(acme: SimpleNamespace) -> dict[str, bytes]:
    return {
        acme.main: acme.main_bytes,
        acme.second: acme.second_bytes,
        acme.instance: acme.instance_bytes,
    }


@pytest.mark.criterion("C6")
def test_canonicalizer_writes_the_merged_document(acme, tmp_path):
    # oracle: requirement 6 — canonical.htm is the merge of the canonical documents; the meta file
    # names the sources in order, so the artefact says what it was built from
    acme.write_filing(tmp_path / "in" / REL_KEY, _files(acme), acme.manifest())
    result = _canonicalizer(tmp_path).canonicalize_filing_dir(REL_KEY)
    assert result.error == "" and result.skipped is False
    written = tmp_path / "out" / REL_KEY / "canonical.htm"
    assert written.read_bytes() == _merged(acme)
    meta = json.loads((tmp_path / "out" / REL_KEY / "canonicalization_meta.json").read_text())
    assert meta["source_documents"] == [acme.main, acme.second]


@pytest.mark.criterion("C6")
def test_missing_listed_document_is_an_error_not_a_skip(acme, tmp_path):
    # oracle: requirement 6 — a skip reads as work already done; main() exits 1 only on an error
    acme.write_filing(
        tmp_path / "in" / REL_KEY,
        {acme.main: acme.main_bytes, acme.instance: acme.instance_bytes},
        acme.manifest(),
    )
    result = _canonicalizer(tmp_path).canonicalize_filing_dir(REL_KEY)
    assert acme.second in result.error
    assert not (tmp_path / "out" / REL_KEY / "canonical.htm").exists()


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_force_rebuilds_a_canonical_written_from_the_main_document_alone(acme, tmp_path):
    # oracle: metamorphic — the rollout path: canonical.htm from the old download, a new download,
    # then --force. The result equals a first build, and without --force the old file stays
    old_manifest = acme.manifest(files=[acme.main, acme.instance])
    del old_manifest["ixbrl_documents"]
    acme.write_filing(
        tmp_path / "in" / REL_KEY,
        {acme.main: acme.main_bytes, acme.instance: acme.instance_bytes},
        old_manifest,
    )
    _canonicalizer(tmp_path).canonicalize_filing_dir(REL_KEY)
    written = tmp_path / "out" / REL_KEY / "canonical.htm"
    old = written.read_bytes()
    assert old == merge_canonical_documents([(acme.main, _canonical(acme.main_bytes))])

    acme.write_filing(tmp_path / "in" / REL_KEY, _files(acme), acme.manifest())
    assert _canonicalizer(tmp_path).canonicalize_filing_dir(REL_KEY).skipped is True
    assert written.read_bytes() == old
    _canonicalizer(tmp_path, force=True).canonicalize_filing_dir(REL_KEY)
    assert written.read_bytes() == _merged(acme)
