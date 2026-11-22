"""C10: the HTML flow can read a multi-document filing with the same merge."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.html10k.sections import detect_sections
from src.ingest.sec_edgar.xbrl.html_canonicalizer import merge_canonical_documents


def _toc_page(acme, target: str) -> bytes:
    return acme.page(
        "<table>"
        '<tr><td><a href="#i1">Item 1. Business</a></td></tr>'
        '<tr><td><a href="#i7">Item 7. Management Discussion</a></td></tr>'
        f'<tr><td><a href="{target}#i8">Item 8. Financial Statements</a></td></tr>'
        "</table>"
        '<div id="i1">Item 1. Business</div><p>Anvils.</p>'
        '<div id="i7">Item 7. Management Discussion</div><p>Sales rose.</p>'
    )


@pytest.mark.criterion("C10")
def test_item_8_in_the_second_document_is_found_after_stripping(acme):
    # oracle: the fixture — the ToC links Item 8 into the second document. Once stripped and
    # merged, Item 8 starts at the second document's <div id="i8"> and the output carries no XBRL
    merged = merge_canonical_documents(
        [
            (acme.main, ix_strip_html(_toc_page(acme, acme.second), acme.main)),
            (acme.second, ix_strip_html(acme.second_bytes, acme.second)),
        ]
    )
    assert b"ix:" not in merged and b"contextRef" not in merged
    item_8 = next(s for s in detect_sections(merged) if s.key == "item_8")
    assert item_8.source == "toc"
    assert item_8.char_start == merged.index(b'<div id="i8">')
    assert b"Total assets" in merged[item_8.char_start : item_8.char_end]


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(st.sampled_from(["Sales rose.", "Costs fell.", "Margins held."]))
def test_one_stripped_document_is_unchanged(acme, sentence):
    # oracle: metamorphic — the flow's single-document output stays what it is today
    stripped = ix_strip_html(acme.page(f"<p>{sentence}</p>"), acme.main)
    assert merge_canonical_documents([(acme.main, stripped)]) == stripped
