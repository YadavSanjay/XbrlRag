"""Spec tests for toc-page-number-links: a filing whose ToC links are page numbers gets its
section labels from the ToC row (docs/specs/toc-page-number-links.md).

Every document is synthetic XHTML built here (Workiva-style empty ``<div id="i…_N"/>``
anchors, as Intel's filings use) and written to ``tmp_path``; the parser runs for real.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import build_section_tree

HEX = "0ab1c04f4cd944ffa1b64a7738970255"
TOC_ID = f"i{HEX}_16"
INTEL = ["Overview", "Our Business", "Risk Factors", "Exhibits"]
PAGES = ["3", "6", "53 - 67", "81–87"]   # single pages and both range dashes
BACKLINK = "Table of Contents"


def _aid(k: int) -> str:
    return f"i{HEX}_{100 + 10 * k}"


def _page_row(label: str, target: str, page: str) -> str:
    """An Intel-style ToC row: label in its own cell, the link text is the page number."""
    return (f"<tr><td colspan=\"3\"><div><span>{label}</span></div></td><td colspan=\"3\"/>"
            f"<td colspan=\"3\"><div><span><a href=\"#{target}\">{page}</a></span></div></td></tr>")


def _text_row(label: str, target: str, page: str) -> str:
    """A text-labelled ToC row: the label is the link, a page-number link sits alongside."""
    return (f"<tr><td><div><span><a href=\"#{target}\">{label}</a></span></div></td>"
            f"<td><div><span><a href=\"#{target}\">{page}</a></span></div></td></tr>")


def _doc(labels, pages, *, style="page", backlinks=True, xref=False, extra_page_link=False):
    """Build the document. Returns (xhtml, body token per label, page-break token per label).

    Each section is an empty anchor div, a heading div and a body; a page-break anchor sits in
    the middle of each body (Intel's page-break divs), with distinct text after it.
    """
    row = _page_row if style == "page" else _text_row
    pairs = enumerate(zip(labels, pages, strict=True))
    rows = "".join(row(lbl, _aid(k), pg) for k, (lbl, pg) in pairs)
    if extra_page_link:   # a page-number-only row to an anchor no text link reaches
        rows += _page_row("Unlinked Extra", f"i{HEX}_999", "99")
    parts = [f"<div id=\"{TOC_ID}\"/><div><span>Table of Contents</span></div>",
             f"<div><table>{rows}</table></div>"]
    bodies, breaks = {}, {}
    for k, lbl in enumerate(labels):
        bodies[lbl], breaks[lbl] = f"BODYTOKEN{k}X", f"AFTERBREAK{k}X"
        back = (f"<div><a href=\"#{TOC_ID}\">{BACKLINK}</a></div>" if backlinks else "")
        parts.append(f"<div id=\"{_aid(k)}\"/><div><span>{lbl}</span></div>"
                     f"<div><span>Prose of {bodies[lbl]} section.</span></div>{back}"
                     f"<div id=\"{_aid(k)}5\"/><hr/>"
                     f"<div><span>More prose {breaks[lbl]} here.</span></div>")
    if extra_page_link:
        parts.append(f"<div id=\"i{HEX}_999\"/><div><span>EXTRATOKEN</span></div>")
    if xref:
        xrows = "".join(
            f"<tr><td><span>Item {k}.</span></td><td><span>{lbl}</span></td>"
            f"<td><span>Pages {k} - {k + 4}</span></td>"
            f"<td><a href=\"#{_aid(k)}5\">{k + 4}</a></td></tr>"
            for k, lbl in enumerate(labels))
        xrows += (f"<tr><td><span>Signatures</span></td><td><span>Page 117</span></td>"
                  f"<td><a href=\"#i{HEX}_888\">117</a></td></tr>")
        parts.append(f"<div id=\"i{HEX}_880\"/><div><span>Form 10-K Cross-Reference Index</span>"
                     f"</div><div><table>{xrows}</table></div>"
                     f"<div id=\"i{HEX}_888\"/><div><span>SIGTOKEN</span></div>")
    html = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<html xmlns=\"http://www.w3.org/1999/xhtml\" "
            "xmlns:ix=\"http://www.xbrl.org/2013/inlineXBRL\"><head><title>t</title></head>"
            f"<body>{''.join(parts)}</body></html>")
    return html, bodies, breaks


def _tree(html: str, where: Path):
    path = where / "canonical.htm"
    path.write_bytes(html.encode("utf-8"))
    return build_section_tree(path), path.read_bytes()


def _labels(tree) -> list[str]:
    nodes = sorted((n for n in tree.nodes.values() if n.section_path != "ROOT"),
                   key=lambda n: n.char_start)
    return [n.label for n in nodes]


def _shape(tree) -> list[tuple[str, int, int]]:
    return [(n.section_path, n.char_start, n.char_end)
            for n in sorted(tree.nodes.values(), key=lambda n: n.char_start)]


def _at(tree, raw: bytes, token: str) -> str | None:
    return tree.find_section(raw.index(token.encode()))


LABELS = st.lists(st.text("abcdefghjkmnopqrstuvwxyz", min_size=1, max_size=8)
                  .map(lambda w: f"Topic {w}"), min_size=1, max_size=8, unique=True)
PAGE = st.one_of(st.integers(1, 300).map(str),
                 st.tuples(st.integers(1, 150), st.integers(151, 300))
                 .map(lambda t: f"{t[0]} - {t[1]}"))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_intel_style_toc_labels_each_section_from_its_row(tmp_path):
    # oracle: built by construction — rows label the sections, in document order (Req. 1-3)
    html, bodies, _ = _doc(INTEL, PAGES)
    tree, raw = _tree(html, tmp_path)
    assert _labels(tree) == [BACKLINK, *INTEL]
    for k, lbl in enumerate(INTEL):
        node = tree.nodes[lbl]
        assert node.char_start == raw.index(f"<div id=\"{_aid(k)}\"".encode())
        assert node.html_anchor == _aid(k)
        assert _at(tree, raw, bodies[lbl]) == lbl
    assert _at(tree, raw, bodies["Risk Factors"]) == "Risk Factors"


@pytest.mark.criterion("C1")
def test_page_number_toc_without_backlinks_still_labels_sections(tmp_path):
    # oracle: Requirement 2 — zero text-linked targets is also "no text-labelled ToC"
    html, bodies, _ = _doc(INTEL, PAGES, backlinks=False)
    tree, raw = _tree(html, tmp_path)
    assert _labels(tree) == INTEL
    assert _at(tree, raw, bodies["Exhibits"]) == "Exhibits"


@pytest.mark.criterion("C1")
@given(LABELS, st.data())
def test_any_page_number_toc_yields_its_labels_in_order(labels, data):
    # oracle: by construction — labels are the ToC rows, bodies resolve to their own section
    pages = data.draw(st.lists(PAGE, min_size=len(labels), max_size=len(labels)))
    html, bodies, _ = _doc(labels, pages)
    with tempfile.TemporaryDirectory() as d:
        tree, raw = _tree(html, Path(d))
    assert _labels(tree) == [BACKLINK, *labels]
    for lbl in labels:
        assert _at(tree, raw, bodies[lbl]) == lbl


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_cross_reference_index_adds_no_sections(tmp_path):
    # oracle: Requirement 4 — index rows (with a "Page(s) n" cell) give no label
    plain, _, _ = _doc(INTEL, PAGES)
    html, bodies, breaks = _doc(INTEL, PAGES, xref=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base, _ = _tree(plain, tmp_path / "a")
    tree, raw = _tree(html, tmp_path / "b")
    assert _labels(tree) == [BACKLINK, *INTEL]
    assert [n.char_start for n in sorted(tree.nodes.values(), key=lambda n: n.char_start)] == \
        [n.char_start for n in sorted(base.nodes.values(), key=lambda n: n.char_start)]
    # A byte after a page break the index links to still belongs to the enclosing section.
    assert _at(tree, raw, breaks["Risk Factors"]) == "Risk Factors"
    assert "Signatures" not in tree.nodes


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(LABELS, st.data())
def test_adding_an_index_leaves_any_page_number_tree_unchanged(labels, data):
    # oracle: metamorphic — Requirement 4; the plain tree is pinned to the labels by C1
    pages = data.draw(st.lists(PAGE, min_size=len(labels), max_size=len(labels)))
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d, "a"), Path(d, "b")
        a.mkdir()
        b.mkdir()
        plain, _ = _tree(_doc(labels, pages)[0], a)
        html, _, breaks = _doc(labels, pages, xref=True)
        indexed, raw = _tree(html, b)
    assert _labels(plain) == [BACKLINK, *labels]
    assert [(p, s) for p, s, _ in _shape(indexed)] == [(p, s) for p, s, _ in _shape(plain)]
    for lbl in labels:
        assert _at(indexed, raw, breaks[lbl]) == lbl


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_text_labelled_toc_ignores_page_only_links_and_matches_its_twin(tmp_path):
    # oracle: Requirement 2 — today's text-ToC behaviour; the twin carries the same rows
    (tmp_path / "text").mkdir()
    (tmp_path / "page").mkdir()
    html, bodies, _ = _doc(INTEL, PAGES, style="text", extra_page_link=True)
    text_tree, raw = _tree(html, tmp_path / "text")
    assert _labels(text_tree) == [BACKLINK, *INTEL]
    assert "Unlinked Extra" not in text_tree.nodes
    assert _at(text_tree, raw, "EXTRATOKEN") == "Exhibits"   # not a section of its own
    twin, _ = _tree(_doc(INTEL, PAGES)[0], tmp_path / "page")
    assert _labels(twin) == _labels(text_tree)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(LABELS, st.data())
def test_page_only_links_never_change_a_text_labelled_tree(labels, data):
    # oracle: metamorphic — Requirement 2; the twin's labels equal the text document's
    pages = data.draw(st.lists(PAGE, min_size=len(labels), max_size=len(labels)))
    with tempfile.TemporaryDirectory() as d:
        paths = [Path(d, n) for n in ("t", "x", "p")]
        for p in paths:
            p.mkdir()
        text, _ = _tree(_doc(labels, pages, style="text")[0], paths[0])
        extra, raw = _tree(_doc(labels, pages, style="text", extra_page_link=True)[0], paths[1])
        twin, _ = _tree(_doc(labels, pages)[0], paths[2])
    assert _labels(text) == [BACKLINK, *labels]
    # The extra row shifts byte offsets, so compare the sections, not their starts.
    assert [p for p, _, _ in _shape(extra)] == [p for p, _, _ in _shape(text)]
    assert _at(extra, raw, "EXTRATOKEN") == labels[-1]
    assert _labels(twin) == _labels(text)
