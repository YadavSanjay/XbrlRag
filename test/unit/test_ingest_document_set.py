"""Edge cases of the multi-document path that the locked spec tests leave open.

The criteria live in test/spec/ingest-multidoc-ixbrl; these pin what the implementation decided
beyond them: broken HTML, ids that references depend on, and prefixes in attribute values.
"""

from __future__ import annotations

from types import SimpleNamespace

import lxml.etree as etree
import pytest

from src.ingest.sec_edgar.document_set import (
    DocumentSetMismatchError,
    assert_document_set_complete,
    inline_fact_ids,
    instance_name,
)
from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
    FilingRequest,
    SECFilingDownloader,
    select_filings,
)
from src.ingest.sec_edgar.xbrl.html_canonicalizer import merge_canonical_documents

XHTML = "http://www.w3.org/1999/xhtml"
IX = "http://www.xbrl.org/2013/inlineXBRL"


def _page(body: str, extra_namespaces: str = "") -> bytes:
    return (
        f'<html xmlns="{XHTML}" xmlns:ix="{IX}" {extra_namespaces}><head><title>t</title></head>'
        f"<body>{body}</body></html>"
    ).encode()


def _fact(fact_id: str, concept: str = "us-gaap:Assets", extra: str = "") -> str:
    return f'<ix:nonFraction id="{fact_id}" name="{concept}" {extra}>1</ix:nonFraction>'


def _by_id(merged: bytes) -> dict[str, etree._Element]:
    root = etree.fromstring(merged)
    return {e.get("id"): e for e in root.iter() if isinstance(e.tag, str) and e.get("id")}


def test_facts_are_found_in_html_that_is_not_xml():
    # The canonicalizer's recovery parser accepts such a document, so the check must as well.
    broken = f'<html xmlns:foo="{IX}"><body><br><foo:nonFraction id="f-1">1</foo:nonFraction>'
    assert inline_fact_ids(broken.encode()) == ["f-1"]


def test_older_inline_namespace_counts():
    document = _page(_fact("f-1")).replace(b"2013/inlineXBRL", b"2011/inlineXBRL")
    assert inline_fact_ids(document) == ["f-1"]


def test_a_broken_instance_is_a_mismatch_not_a_crash():
    with pytest.raises(DocumentSetMismatchError, match="cannot be parsed"):
        assert_document_set_complete([_page(_fact("f-1"))], b"<xbrl><unclosed>")


def test_instance_name_ignores_linkbases():
    assert instance_name({"files": ["a_cal.xml", "a_htm.xml", "a.htm"]}) == "a_htm.xml"
    assert instance_name({"files": ["a_cal.xml", "a.htm"]}) is None
    assert instance_name({}) is None


def test_a_prefix_only_an_attribute_value_uses_survives_the_merge():
    # The concept QName is text to lxml, so moving the fact would lose the declaration.
    first = _page(_fact("f-1", "us-gaap:Assets"), 'xmlns:us-gaap="urn:gaap"')
    second = _page(_fact("f-2", "acme:Widgets"), 'xmlns:acme="urn:acme"')
    merged = merge_canonical_documents([("a.htm", first), ("b.htm", second)])
    root = etree.fromstring(merged)
    assert root.nsmap["acme"] == "urn:acme"
    assert root.nsmap["us-gaap"] == "urn:gaap"
    assert root.nsmap["ix"] == IX


def test_a_renamed_context_keeps_its_references():
    # A fact that kept contextRef="c" would silently move to the first document's context.
    first = _page('<div id="c">first</div>' + _fact("f-1", extra='contextRef="c"'))
    second = _page('<div id="c">second</div>' + _fact("f-2", extra='contextRef="c"'))
    by_id = _by_id(merge_canonical_documents([("a.htm", first), ("b.htm", second)]))
    assert by_id["f-1"].get("contextRef") == "c"
    assert by_id["f-2"].get("contextRef") == "c-doc2"
    assert by_id["c-doc2"].text == "second"


def test_old_style_named_anchors_are_kept_apart():
    first = _page('<a name="x">first</a><a href="b.htm#x">to second</a>')
    second = _page('<a name="x">second</a>' + _fact("f-1"))
    merged = merge_canonical_documents([("a.htm", first), ("b.htm", second)])
    root = etree.fromstring(merged)
    anchors = {e.text: e for e in root.iter(f"{{{XHTML}}}a")}
    assert anchors["first"].get("name") == "x"
    assert anchors["second"].get("name") == "x-doc2"
    assert anchors["to second"].get("href") == "#x-doc2"


def test_a_fact_id_is_never_the_one_renamed():
    # The first document's anchor shares an id with a later fact: the anchor gives way.
    first = _page('<div id="f-2">anchor</div><a href="#f-2">link</a>')
    second = _page(_fact("f-2"))
    merged = merge_canonical_documents([("a.htm", first), ("b.htm", second)])
    by_id = _by_id(merged)
    assert by_id["f-2"].tag == f"{{{IX}}}nonFraction"
    assert by_id["f-2-doc1"].text == "anchor"
    assert b'href="#f-2-doc1"' in merged


def test_a_rename_avoids_an_id_already_in_the_document():
    first = _page('<div id="x">1</div>')
    second = _page('<div id="x">2</div><div id="x-doc2">3</div>')
    by_id = _by_id(merge_canonical_documents([("a.htm", first), ("b.htm", second)]))
    assert by_id["x-doc2"].text == "3"
    assert by_id["x-doc2-2"].text == "2"


def test_text_directly_in_a_later_body_is_kept():
    first = _page("<p>one</p>")
    second = b'<html xmlns="' + XHTML.encode() + b'"><body>loose<p>two</p></body></html>'
    merged = merge_canonical_documents([("a.htm", first), ("b.htm", second)])
    assert b"<p>one</p>loose<p>two</p>" in merged


def test_a_document_with_no_body_is_refused_by_name():
    with pytest.raises(ValueError, match="b.htm"):
        merge_canonical_documents(
            [("a.htm", _page("<p>one</p>")), ("b.htm", b"<html><head/></html>")]
        )


def test_an_empty_html_attachment_fails_the_download(tmp_path):
    # edgartools can hand back an empty body, and that is no more readable than an exception.
    attachment = SimpleNamespace(
        document="ex99.htm", document_type="EX-99", download=lambda: ""
    )
    filing = SimpleNamespace(accession_no="0000000001-26-000001", attachments=[attachment])
    downloader = SECFilingDownloader(output_root=tmp_path, identity="Unit Test unit@example.com")
    with pytest.raises(RuntimeError, match="ex99"):
        downloader._save_xbrl_attachments(filing, tmp_path, "10-K")


def test_a_request_outside_the_filers_index_keeps_its_lookup_error():
    company = SimpleNamespace(get_filings=lambda form: [])
    request = FilingRequest(ticker="ACME", filing_type="10-K", fiscal_year=2025)
    with pytest.raises(LookupError, match="ACME"):
        select_filings(company, request)
