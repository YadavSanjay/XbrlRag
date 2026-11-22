"""Unit tests for what docs/specs/html10k-strip-sections.md leaves to the implementation.

The spec's own criteria are in test/spec/html10k-strip-sections/. These cover inputs it does not
name: odd documents ix_strip_html must still turn into strict XML, and how detect_sections treats
a notes heading with no Item 8 or an input that is not ix_strip_html output.
"""

from __future__ import annotations

import lxml.etree as etree
import pytest

from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.html10k.sections import detect_sections

XHTML = "http://www.w3.org/1999/xhtml"
IX_URI = "http://www.xbrl.org/2013/inlineXBRL"


def _text(out: bytes) -> str:
    return "".join(etree.fromstring(out).itertext())


def test_nbsp_under_an_external_doctype_is_a_space_and_not_lost():
    # A DOCTYPE naming an external DTD makes libxml2 accept &nbsp; without resolving it.
    source = (
        b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        b'"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>a&nbsp;b</p></body></html>'
    )
    assert _text(ix_strip_html(source)) == "a b"


def test_declared_encoding_does_not_override_valid_utf8():
    source = '<?xml version="1.0" encoding="ISO-8859-1"?><html><body><p>café</p></body></html>'
    assert _text(ix_strip_html(source.encode("utf-8"))) == "café"


@pytest.mark.parametrize("source", [b"", b"   ", b"<!-- nothing -->"])
def test_a_document_with_nothing_in_it_still_gives_xml(source):
    assert _text(ix_strip_html(source)).strip() == ""


def test_controls_xml_forbids_are_gone_and_a_form_feed_separates_words():
    source = b"<html><body><p>a\x00b\x0cc&#1;d</p></body></html>"
    assert _text(ix_strip_html(source)) == "ab cd"


def test_bytes_windows_1252_leaves_undefined_are_dropped():
    assert _text(ix_strip_html(b"<html><body><p>a\x81b\x92</p></body></html>")) == "ab’"


def test_a_root_that_is_itself_inline_xbrl_keeps_its_text():
    source = (
        f'<ix:nonNumeric xmlns:ix="{IX_URI}" contextRef="c" id="f"><p>kept</p></ix:nonNumeric>'
    ).encode()
    out = ix_strip_html(source)
    assert _text(out) == "kept"
    assert b"contextRef" not in out


def test_an_element_of_any_other_namespace_is_unwrapped_on_both_paths():
    # A strict-XML output cannot carry the prefix; the text is what a reader sees.
    well_formed = b'<html xmlns:o="urn:o"><body><p>a<o:p>b</o:p></p></body></html>'
    malformed = b"<html><body><p>a<o:p>b</o:p>&nbsp;</p></body></html>"
    for source in (well_formed, malformed):
        out = ix_strip_html(source)
        assert b"o:p" not in out
        assert _text(out).strip() == "ab"


def test_a_stray_ix_hidden_outside_a_header_does_not_leak():
    source = (
        f'<html xmlns:ix="{IX_URI}"><body><ix:hidden><p>SECRET</p></ix:hidden><p>seen</p>'
        "</body></html>"
    ).encode()
    assert _text(ix_strip_html(source)) == "seen"


def test_an_id_kept_elsewhere_keeps_the_link():
    source = (
        f'<html xmlns:ix="{IX_URI}"><body><ix:nonFraction id="x">1</ix:nonFraction>'
        '<p id="x">target</p><a href="#x">link</a></body></html>'
    ).encode()
    assert b'href="#x"' in ix_strip_html(source)


def test_nesting_deeper_than_the_recursion_limit():
    # Past Python's limit of 1,000, under the 2,048 at which libxml2 2.14 drops what lies deeper.
    depth = 1500
    source = (
        "<html><body>" + "<div>" * depth + "needle" + "</div>" * depth + "</body></html>"
    ).encode()
    out = ix_strip_html(source)
    # The output is as deep as the filing, so reading it back needs huge_tree too.
    assert b"needle" in out
    assert "".join(etree.fromstring(out, etree.XMLParser(huge_tree=True)).itertext()) == "needle"


def test_notes_heading_with_no_item_8_is_found_anywhere():
    # Requirement 17 ties the notes heading to Item 8 only "when item_8 was found".
    out = ix_strip_html(
        f'<html xmlns="{XHTML}"><body><div>Notes to Consolidated Financial Statements</div>'
        "<p>policies</p></body></html>".encode()
    )
    assert [section.key for section in detect_sections(out)] == ["notes"]


def test_input_that_is_not_ix_strip_output_fails_loud():
    # A comment holding a tag would shift every offset, silently.
    with pytest.raises(ValueError, match="not ix_strip_html output"):
        detect_sections(b"<html><body><!-- <a> --><p>x</p></body></html>")
