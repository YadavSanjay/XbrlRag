"""Spec tests for detect_sections (docs/specs/html10k-strip-sections.md, part B; C7-C11).

Every document goes through ix_strip_html first, as in the flow. Expected offsets are read from
the marker the fixture puts on the element (an id or a class), so each start is known by
construction; expected keys and ranges are derived by hand from the fixture.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.html10k.sections import Html10kSection, detect_sections

XHTML = "http://www.w3.org/1999/xhtml"

# (key, item number, title, token in its body). Items 1B and 9 are not keys: they only end one.
ITEMS = [
    ("item_1", "1", "Business", "TOKENONE"),
    ("item_1a", "1A", "Risk Factors", "TOKENONEA"),
    (None, "1B", "Unresolved Staff Comments", "TOKENONEB"),
    ("item_7", "7", "Management’s Discussion and Analysis of Financial Condition", "TOKENSEVEN"),
    ("item_7a", "7A", "Quantitative and Qualitative Disclosures About Market Risk", "TOKENSEVENA"),
    ("item_8", "8", "Financial Statements and Supplementary Data", "TOKENEIGHT"),
    (None, "9", "Changes in and Disagreements with Accountants", "TOKENNINE"),
]
PADDING = " this paragraph is long enough not to look like a table of contents."


def _doc(body: str) -> bytes:
    return (f'<html xmlns="{XHTML}"><head><title>t</title></head><body>{body}</body></html>'
            ).encode()


def _run(source: bytes) -> tuple[bytes, dict[str, Html10kSection]]:
    doc = ix_strip_html(source)
    return doc, {s.key: s for s in detect_sections(doc)}


def _at(doc: bytes, marker: str) -> int:
    """Return the offset of the opening "<" of the element carrying *marker*."""
    return doc.rfind(b"<", 0, doc.index(marker.encode()))


def _id(doc: bytes, anchor: str) -> int:
    return _at(doc, f'id="{anchor}"')


def _class(doc: bytes, name: str) -> int:
    return _at(doc, f'class="{name}"')


def _slice(doc: bytes, section: Html10kSection) -> bytes:
    return doc[section.char_start:section.char_end]


def _tokens(doc: bytes, section: Html10kSection) -> set[str]:
    return set(re.findall(r"TOKEN[A-Z]+", _slice(doc, section).decode("utf-8")))


def _assert_tokens(doc: bytes, sections: dict[str, Html10kSection]) -> None:
    """Each section holds its own item's token and no other item's."""
    for key, _number, _title, token in ITEMS:
        if key in sections:
            assert _tokens(doc, sections[key]) == {token}, key


def _assert_invariants(doc: bytes, found: list[Html10kSection]) -> None:
    assert [s.char_start for s in found] == sorted(s.char_start for s in found)
    assert len({s.key for s in found}) == len(found)
    for s in found:
        assert 0 <= s.char_start < s.char_end <= len(doc)
    for a in found:
        for b in found:
            if a.key < b.key and {a.key, b.key} != {"item_8", "notes"}:
                assert a.char_end <= b.char_start or b.char_end <= a.char_start, (a.key, b.key)


@pytest.mark.criterion("C7")
def test_toc_ranges_from_a_workiva_shaped_filing(fixture_bytes):
    # oracle: toc_anchors.htm by hand: each section runs from its anchor to the next anchor that
    # is an item or a boundary title, so subsections and notes never end one (Requirement 18).
    doc, found = _run(fixture_bytes("toc_anchors.htm"))
    expected = {
        "item_1": ("i1", "i1a"),
        "item_1a": ("i1a", "i1b"),
        "item_7": ("i7", "i7a"),
        "item_7a": ("i7a", "i8"),
        "item_8": ("i8", "i9"),
        "notes": ("i8-notes", "i9"),
    }
    assert set(found) == set(expected)
    for key, (start, end) in expected.items():
        assert (found[key].char_start, found[key].char_end) == (_id(doc, start), _id(doc, end)), key
        assert found[key].source == "toc", key


@pytest.mark.criterion("C7")
def test_toc_labels_are_the_row_without_its_page_reference(fixture_bytes):
    # oracle: Requirements 13 and 19, by hand from the fixture's rows.
    _, found = _run(fixture_bytes("toc_anchors.htm"))
    assert found["item_1"].label == "Item 1. Business"
    assert found["item_1a"].label == "Item 1A. Risk Factors"
    assert found["notes"].label == "Notes to Consolidated Financial Statements"


@pytest.mark.criterion("C7")
def test_summary_row_and_body_links_do_not_move_a_section(fixture_bytes):
    # oracle: Requirement 14: "Risk Factors Summary" is a subsection, and the body link "Item 7"
    # to #i7-liq comes after the ToC's entry for Item 7, so the first entry wins.
    doc, found = _run(fixture_bytes("toc_anchors.htm"))
    assert b"SUMMARYBODY" not in _slice(doc, found["item_1a"])
    assert found["item_7"].char_start == _id(doc, "i7")


MCD_SHAPE = _doc(
    "<table>"
    '<tr><td>Business Summary</td><td><a href="#bs">3</a></td></tr>'
    "<tr><td>Management's Discussion and Analysis of Financial Condition and Results of Operations"
    '</td><td><a href="#mda">7</a></td></tr>'
    '<tr><td>Outlook</td><td><a href="#out">11</a></td></tr>'
    '<tr><td>Quantitative and Qualitative Disclosures About Market Risk</td>'
    '<td><a href="#qq">22</a></td></tr>'
    '<tr><td>Risk Factors</td><td><a href="#rf">27</a></td></tr>'
    '<tr><td>Legal Proceedings</td><td><a href="#lp">36</a></td></tr>'
    '<tr><td>Financial Statements and Supplementary Data</td><td><a href="#fs">38</a></td></tr>'
    '<tr><td>Notes to Consolidated Financial Statements</td><td><a href="#nt">44</a></td></tr>'
    '<tr><td>Controls and Procedures</td><td><a href="#ct">68</a></td></tr>'
    '<tr><td><a href="#ex">Exhibits and Financial Statement Schedules</a></td>'
    '<td><a href="#ex">70</a></td></tr>'
    '<tr><td><a href="#sg">Signatures</a></td><td><a href="#sg">73</a></td></tr>'
    "</table>"
    '<div id="bs"></div><div>Business Summary</div><p>about the company</p>'
    '<div id="mda"></div><div>MD&amp;A</div><p>sales rose</p>'
    '<div id="out"></div><div>Outlook</div><p>more of the same</p>'
    '<div id="qq"></div><div>Market risk</div><p>rates</p>'
    '<div id="rf"></div><div>Risk factors</div><p>demand</p>'
    '<div id="pb30"></div><p>still risk</p>'
    '<div id="lp"></div><div>Legal</div><p>none</p>'
    '<div id="fs"></div><div>Statements</div><p>income</p>'
    '<div id="nt"></div><div>Notes</div><p>policies</p>'
    '<div id="ct"></div><div>Controls</div><p>effective</p>'
    "<div>Form 10-K Cross-Reference Index</div><table>"
    '<tr><td>Item 1</td><td>Business</td><td><a href="#bs">Page 3</a></td></tr>'
    '<tr><td>Item 1A</td><td>Risk Factors</td><td><a href="#pb30">Page 27</a></td></tr>'
    '<tr><td>Item 8</td><td>Financial Statements and Supplementary Data</td>'
    '<td><a href="#pb30">Page 38</a></td></tr>'
    "</table>"
    '<div id="ex"></div><div>Exhibits</div><p>list</p>'
    '<div id="sg"></div><div>Signatures</div><p>signed</p>'
)


@pytest.mark.criterion("C7")
def test_page_number_toc_in_a_topic_ordered_filing():
    # oracle: MCD_SHAPE by hand. Only Exhibits and Signatures have text links, labels come from
    # the rows, the index rows contribute nothing, and "Business Summary" is no title of Item 1.
    doc, found = _run(MCD_SHAPE)
    listed = [s.key for s in detect_sections(doc)]
    assert listed == ["item_7", "item_7a", "item_1a", "item_8", "notes"]
    expected = {"item_7": ("mda", "qq"), "item_7a": ("qq", "rf"), "item_1a": ("rf", "lp"),
                "item_8": ("fs", "ct"), "notes": ("nt", "ct")}
    for key, (start, end) in expected.items():
        assert (found[key].char_start, found[key].char_end) == (_id(doc, start), _id(doc, end)), key
        assert found[key].source == "toc", key


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("title", ["Financial Statements", "Consolidated Financial Statements"])
def test_short_item_8_titles_in_a_title_only_toc(title):
    # oracle: Requirement 12's table; the shapes are those of two filers in the survey.
    doc, found = _run(_doc(
        f'<table><tr><td><a href="#a">{title}</a></td><td>38</td></tr>'
        '<tr><td><a href="#b">Controls and Procedures</a></td><td>68</td></tr></table>'
        '<div id="a"></div><p>statements</p><div id="b"></div><p>effective</p>'))
    assert (found["item_8"].char_start, found["item_8"].char_end) == (_id(doc, "a"), _id(doc, "b"))


@pytest.mark.criterion("C7")
def test_older_href_and_name_toc(fixture_bytes):
    # oracle: older_plain.htm by hand; the links are outside a table and name anchors are targets.
    source = fixture_bytes("older_plain.htm").decode("utf-8").encode("cp1252")
    doc, found = _run(source)
    assert set(found) == {"item_7", "item_8"}
    assert found["item_7"].source == "toc"
    assert found["item_7"].char_start == _at(doc, 'name="item7"')
    assert found["item_7"].char_end == _at(doc, 'name="item8"')
    assert found["item_8"].char_end == len(doc)


def _toc_row(shape: str, anchor: str, number: str, title: str) -> str:
    link = f'<a href="#{anchor}">'
    if shape == "all":
        return (f"<tr><td>{link}Item {number}.</a></td><td>{link}{title}</a></td>"
                f"<td>{link}12</a></td></tr>")
    if shape == "title":
        return f"<tr><td>Item {number}.</td><td>{link}{title}</a></td><td>12</td></tr>"
    if shape == "bare":
        return f"<tr><td>{link}{title}</a></td><td>12</td></tr>"
    return f"<tr><td>Item {number}.</td><td>{title}</td><td>{link}12</a></td></tr>"


@pytest.mark.criterion("C7")
@given(st.lists(st.tuples(st.sampled_from(range(len(ITEMS))),
                          st.sampled_from(["all", "title", "bare", "page"])),
                min_size=1, max_size=len(ITEMS), unique_by=lambda t: t[0]))
def test_property_toc_any_subset_and_row_shape(chosen):
    # oracle: Requirements 13, 14, 18; the generator knows which token is in which section.
    chosen = sorted(chosen)
    rows, body = [], []
    for index, shape in chosen:
        _key, number, title, token = ITEMS[index]
        anchor = f"a{index}"
        rows.append(_toc_row(shape, anchor, number, title))
        body.append(f'<div id="{anchor}"></div><div>Item {number}. {title}</div>'
                    f"<p>{token}{PADDING}</p>")
    doc, found = _run(_doc(f"<table>{''.join(rows)}</table>{''.join(body)}"))
    assert set(found) == {ITEMS[i][0] for i, _ in chosen} - {None}
    assert all(s.source == "toc" for s in found.values())
    _assert_tokens(doc, found)


@pytest.mark.criterion("C8")
def test_heading_ranges_without_a_toc(fixture_bytes):
    # oracle: no_toc.htm by hand: plain, split-row, title-less and PART-prefixed headings; the
    # "(continued)" lines and the index row are not headings (Requirement 15).
    doc, found = _run(fixture_bytes("no_toc.htm"))
    expected = {
        "item_1": ("h-item1", "h-item1a"),
        "item_1a": ("h-item1a", "h-item1b"),
        "item_7": ("h-item7", "h-item7a"),
        "item_7a": ("h-item7a", "h-item8"),
        "item_8": ("h-item8", "h-item9"),
        "notes": ("h-notes", "h-item9"),
    }
    assert set(found) == set(expected)
    for key, (start, end) in expected.items():
        assert found[key].char_start == _class(doc, start), key
        assert found[key].char_end == _class(doc, end), key
        assert found[key].source == "heading", key


@pytest.mark.criterion("C8")
def test_offsets_are_utf8_bytes_and_labels_are_the_block_text(fixture_bytes):
    # oracle: the fixture's first line holds multi-byte characters, so a character offset would
    # point earlier than the byte offset (Requirement "half-open UTF-8 byte range").
    doc, found = _run(fixture_bytes("no_toc.htm"))
    assert _slice(doc, found["item_1"]).decode("utf-8").count("BODYITEM1") == 1
    assert found["item_1"].label == "ITEM 1. Business"
    assert found["item_8"].label == "PART II, ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA"
    assert found["item_7a"].label == "ITEM 7A."


@pytest.mark.criterion("C8")
@pytest.mark.parametrize(("heading", "key"), [
    ("Items 1 and 2. Business and Properties", "item_1"),
    ("Item 1 &#8212; Business", "item_1"),
    ("ITEM&#160;1A: RISK FACTORS", "item_1a"),
    ("Item 7. Management's Discussion and Analysis", "item_7"),
    ("Item 8. Consolidated Financial Statements and Supplementary Data", "item_8"),
    ("PART II ITEM 7A QUANTITATIVE AND QUALITATIVE DISCLOSURE ABOUT MARKET RISKS", "item_7a"),
])
def test_heading_variants(heading, key):
    # oracle: Requirements 12 and 15, one variant of the numbering, separator or case each.
    doc, found = _run(_doc(f'<h2 class="here">{heading}</h2><p>{PADDING}</p>'
                           f'<div class="next">Item 12. Security Ownership</div><p>{PADDING}</p>'))
    assert set(found) == {key}
    assert found[key].char_start == _class(doc, "here")
    assert found[key].char_end == _class(doc, "next")


@pytest.mark.criterion("C8")
def test_older_filing_without_a_toc(fixture_bytes):
    # oracle: older_plain.htm with its two ToC lines removed; each heading is its own <P> block.
    lines = fixture_bytes("older_plain.htm").decode("utf-8").splitlines()
    source = "\n".join(line for line in lines if "HREF" not in line).encode("cp1252")
    doc, found = _run(source)
    assert set(found) == {"item_7", "item_8"}
    assert found["item_7"].source == "heading"
    name7, name8 = doc.index(b'name="item7"'), doc.index(b'name="item8"')
    assert found["item_7"].char_start == doc.rfind(b"<p", 0, name7)
    assert found["item_7"].char_end == doc.rfind(b"<p", 0, name8)
    assert b"OLDBODYSEVEN" in _slice(doc, found["item_7"])
    assert b"OLDBODYEIGHT" not in _slice(doc, found["item_7"])


def _heading(shape: int, number: str, title: str) -> str:
    return [
        f"<div>ITEM {number}. {title}</div>",
        f"<p>Item {number}: {title}</p>",
        f"<h2>Item {number} &#8212; {title}</h2>",
        f"<table><tr><td><div>Item {number}.</div></td><td><div>{title}</div></td></tr></table>",
        f"<div>Item {number}.</div><div>{title}</div>",
        f"<div>PART II, ITEM {number}. {title.upper()}</div>",
    ][shape]


SUBSET = st.lists(st.tuples(st.sampled_from(range(len(ITEMS))), st.sampled_from(range(6))),
                  min_size=1, max_size=len(ITEMS), unique_by=lambda t: t[0])


@pytest.mark.criterion("C8")
@given(SUBSET)
def test_property_headings_any_subset_and_shape(chosen):
    # oracle: Requirements 15, 17, 18; the generator knows which token is in which section.
    body = []
    for index, shape in sorted(chosen):
        _key, number, title, token = ITEMS[index]
        body.append(f"{_heading(shape, number, title)}<p>{token}{PADDING}</p>")
    doc, found = _run(_doc("".join(body)))
    assert set(found) == {ITEMS[i][0] for i, _ in chosen} - {None}
    assert all(s.source == "heading" for s in found.values())
    _assert_tokens(doc, found)


def _filing(front: str = "", in_item_1: str = "", in_item_7: str = "", in_item_8: str = "",
            back: str = "") -> bytes:
    """A filing with no ToC and a place for noise in each region."""
    parts = [front, "<div>PART I</div>"]
    extra = {"item_1": in_item_1, "item_7": in_item_7, "item_8": in_item_8}
    for key, number, title, token in ITEMS:
        parts.append(f"<div>Item {number}. {title}</div><p>{token}{PADDING}</p>")
        parts.append(extra.get(key, ""))
    parts.append(back)
    return _doc("".join(parts))


def _shape(source: bytes) -> dict[str, tuple[str, str, frozenset[str]]]:
    """Each section's label, source and item tokens: a comparison that ignores byte shift."""
    doc, found = _run(source)
    return {key: (s.label, s.source, frozenset(_tokens(doc, s))) for key, s in found.items()}


LINKLESS_TOC_PAGES = (
    "<table><tr><td>Item 1.</td><td>Business</td><td>3</td></tr>"
    "<tr><td>Item 1A.</td><td>Risk Factors</td><td>9</td></tr>"
    "<tr><td>Item 7.</td><td>Management’s Discussion and Analysis</td><td>25</td></tr>"
    "<tr><td>Item 8.</td><td>Financial Statements and Supplementary Data</td><td>F-1</td></tr>"
    "</table>")
LINKLESS_TOC_BARE = (
    "<div>Item 1. Business</div><div>Item 1A. Risk Factors</div>"
    "<div>Item 1B. Unresolved Staff Comments</div>"
    "<div>Item 7. Management’s Discussion and Analysis</div>"
    "<div>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</div>"
    "<div>Item 8. Financial Statements and Supplementary Data</div>")
PROSE = ("<p>Item 7 of this report discusses liquidity.</p>"
         "<p>Item 1A. Risk Factors describes these matters in more detail.</p>"
         "<p>Item 8, Financial Statements, has the numbers.</p>"
         "<p>See Item 7. Management’s Discussion and Analysis for more.</p>")
RUNNING_ITEM_7 = ("<div>PART II Item 7</div><p>TOPUP</p>"
                  "<div>Item 7. Management’s Discussion and Analysis (continued)</div>")
RUNNING_NOTES = "<div>Notes to Consolidated Financial Statements (continued)</div>"
INDEX = ("<div>Form 10-K Cross-Reference Index</div><table>"
         "<tr><td>Item 7A</td><td>Quantitative and Qualitative Disclosures About Market Risk</td>"
         "<td>Page 22</td></tr>"
         "<tr><td>Item 1C</td><td>Cybersecurity</td><td>Pages 34-35</td></tr>"
         "<tr><td>Item 6</td><td>[Reserved]</td><td>Not applicable</td></tr></table>")
SPLIT_INDEX = ("<table><tr><td>Item 1.</td><td>Business:</td></tr>"
               "<tr><td>General development of business</td><td>Pages 3-5, 18</td></tr>"
               "<tr><td>Item 7.</td><td>Management's Discussion and Analysis of Financial "
               "Condition and Results of Operations:</td></tr>"
               "<tr><td>Results of operations</td><td>Pages 18-29</td></tr>"
               "<tr><td>Notes to Consolidated Financial Statements</td></tr></table>")


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(("region", "noise"), [
    ("front", LINKLESS_TOC_PAGES), ("front", LINKLESS_TOC_BARE), ("in_item_1", PROSE),
    ("in_item_7", RUNNING_ITEM_7), ("in_item_8", RUNNING_NOTES), ("back", INDEX),
])
def test_noise_does_not_move_a_section(region, noise):
    # oracle: Requirements 15-17: the same filing without the noise. _filing() is the control.
    assert _shape(_filing(**{region: noise})) == _shape(_filing())


@pytest.mark.criterion("C9")
def test_clean_filing_finds_the_five_items():
    # oracle: _filing()'s own construction: five keys, each with its own token; guards the
    # comparison in test_noise_does_not_move_a_section against both sides being empty.
    doc, found = _run(_filing())
    assert set(found) == {"item_1", "item_1a", "item_7", "item_7a", "item_8"}
    _assert_tokens(doc, found)


@pytest.mark.criterion("C9")
@pytest.mark.parametrize("index", [INDEX, SPLIT_INDEX])
def test_index_rows_alone_create_no_section(index):
    # oracle: Requirement 15: an index row is no candidate, even for a key nothing else provides.
    # SPLIT_INDEX is the INTC shape: the item's own row has no page, its sub-rows do.
    _, found = _run(_doc(f"<div>Item 1A. Risk Factors</div><p>{PADDING}</p>{index}"))
    assert set(found) == {"item_1a"}


@pytest.mark.criterion("C9")
def test_index_row_for_the_notes_is_not_the_notes_heading(fixture_bytes):
    # oracle: no_toc.htm has the row "Notes to Consolidated Financial Statements | 40" before the
    # real heading; the row ends in a page reference.
    doc, found = _run(fixture_bytes("no_toc.htm"))
    assert b"BODYBS" not in _slice(doc, found["notes"])


@pytest.mark.criterion("C9")
@given(st.lists(st.sampled_from(["front_pages", "front_bare", "prose", "running", "notes",
                                 "index"]), unique=True))
@pytest.mark.metamorphic
def test_metamorphic_any_mix_of_noise(noise):
    # oracle: Requirements 15-17: adding what is not a heading changes nothing but byte offsets.
    front = (LINKLESS_TOC_PAGES if "front_pages" in noise else "") \
        + (LINKLESS_TOC_BARE if "front_bare" in noise else "")
    noisy = _filing(
        front=front,
        in_item_1=PROSE if "prose" in noise else "",
        in_item_7=RUNNING_ITEM_7 if "running" in noise else "",
        in_item_8=RUNNING_NOTES if "notes" in noise else "",
        back=INDEX if "index" in noise else "",
    )
    assert _shape(noisy) == _shape(_filing())


def _stub_item_8() -> bytes:
    return _doc(
        f"<div>Item 8. Financial Statements and Supplementary Data</div><p>STUBEIGHT is in Item 15."
        f"{PADDING}</p>"
        '<div class="i9">Item 9. Changes in and Disagreements with Accountants</div>'
        f"<p>{PADDING}</p>"
        f"<div>Item 15. Exhibits and Financial Statement Schedules</div><p>{PADDING}</p>"
        "<table><tr><td>Consolidated Balance Sheets</td><td>52</td></tr>"
        "<tr><td>Notes to Consolidated Financial Statements</td><td>56</td></tr></table>"
        f"<p>STATEMENTS{PADDING}</p>"
        '<div class="nt">Notes to the Consolidated Financial Statements</div>'
        f"<p>NOTEBODY{PADDING}</p>"
        f'<div class="sg">Signatures</div><p>SIGNED{PADDING}</p>')


@pytest.mark.criterion("C10")
def test_notes_in_item_15_with_a_stub_item_8():
    # oracle: the NVDA and QCOM shape, by hand: item_8 stops at Item 9; the index row is skipped;
    # notes run from their heading to Signatures.
    doc, found = _run(_stub_item_8())
    assert found["item_8"].char_end == _class(doc, "i9")
    notes = found["notes"]
    assert (notes.char_start, notes.char_end) == (_class(doc, "nt"), _class(doc, "sg"))
    assert found["notes"].source == "heading"


@pytest.mark.criterion("C10")
def test_notes_inside_item_8_from_the_toc(fixture_bytes):
    # oracle: toc_anchors.htm by hand; the notes lie inside Item 8 and hold Note 1.
    doc, found = _run(fixture_bytes("toc_anchors.htm"))
    notes, item_8 = found["notes"], found["item_8"]
    assert item_8.char_start < notes.char_start < notes.char_end <= item_8.char_end
    assert b"BODYNOTES" in _slice(doc, notes)
    assert b"BODYBS" not in _slice(doc, notes)
    assert b"BODYITEM9" not in _slice(doc, notes)


NOTES_TITLES = ["Notes to Consolidated Financial Statements",
                "Notes to the Consolidated Financial Statements",
                "Combined Notes to Consolidated Financial Statements",
                "Notes to financial statements",
                "NOTES TO CONSOLIDATED FINANCIAL STATEMENTS",
                "Notes to Consolidated Financial Statements:"]


@pytest.mark.criterion("C10")
@given(st.sampled_from(NOTES_TITLES), st.booleans())
def test_property_any_notes_title(title, from_toc):
    # oracle: the table of Requirement 12: the section holds the notes and stops at Item 9.
    toc = ""
    anchor = ""
    if from_toc:
        toc = (f'<table><tr><td><a href="#n">{title}</a></td><td>40</td></tr>'
               '<tr><td>Item 9.</td><td><a href="#i9">Changes in</a></td><td>70</td></tr></table>')
        anchor = '<div id="n"></div>'
    doc, found = _run(_doc(
        f"{toc}<div>Item 8. Financial Statements</div><p>TOKENSTATEMENT{PADDING}</p>"
        f"{anchor}<div>{title}</div><p>TOKENNOTE{PADDING}</p>"
        '<div id="i9"></div><div>Item 9. Changes in and Disagreements</div>'
        f"<p>TOKENLATER{PADDING}</p>"))
    text = _slice(doc, found["notes"])
    assert b"TOKENNOTE" in text
    assert b"TOKENSTATEMENT" not in text
    assert b"TOKENLATER" not in text
    assert found["notes"].source == ("toc" if from_toc else "heading")


@pytest.mark.criterion("C11")
def test_items_end_at_items_that_are_not_keys(fixture_bytes):
    # oracle: no_toc.htm: 1B and 9 are not keys but they end Item 1A and Item 8 (Requirement 18).
    doc, found = _run(fixture_bytes("no_toc.htm"))
    assert b"BODYITEM1B" not in _slice(doc, found["item_1a"])
    assert b"BODYITEM9" not in _slice(doc, found["item_8"])


@pytest.mark.criterion("C11")
def test_dangling_toc_link_falls_back_to_the_heading_for_that_key_only():
    # oracle: Requirements 13, 15, 18, by hand: #gone has no element, so Item 7A comes from its
    # heading, and Item 7, from the ToC, ends there.
    doc, found = _run(_doc(
        '<table><tr><td>Item 7.</td><td><a href="#i7">Management’s Discussion and Analysis</a></td>'
        '<td>25</td></tr><tr><td>Item 7A.</td><td><a href="#gone">Quantitative and Qualitative '
        'Disclosures About Market Risk</a></td><td>33</td></tr><tr><td>Item 8.</td><td>'
        '<a href="#i8">Financial Statements and Supplementary Data</a></td><td>34</td></tr></table>'
        '<div id="i7"></div><div>Item 7. Management’s Discussion and Analysis</div>'
        f"<p>{PADDING}</p>"
        '<div class="h7a">Item 7A. Quantitative and Qualitative Disclosures About Market Risk</div>'
        f'<p>{PADDING}</p><div id="i8"></div><div>Item 8. Financial Statements</div>'
        f"<p>{PADDING}</p>"))
    assert found["item_7"].source == "toc"
    assert found["item_7a"].source == "heading"
    assert found["item_8"].source == "toc"
    assert found["item_7"].char_end == _class(doc, "h7a")
    item_7a = found["item_7a"]
    assert (item_7a.char_start, item_7a.char_end) == (_class(doc, "h7a"), _id(doc, "i8"))


@pytest.mark.criterion("C11")
def test_missing_item_is_absent_and_last_section_runs_to_the_end():
    # oracle: Requirement B's preamble and 18: no Item 7A means no key, nothing is invented.
    doc, found = _run(_doc("<div>Item 7. Management’s Discussion and Analysis</div>"
                           f"<p>{PADDING}</p>"))
    assert set(found) == {"item_7"}
    assert found["item_7"].char_end == len(doc)


@pytest.mark.criterion("C11")
def test_part_iii_amendment_gives_no_sections():
    # oracle: Requirement B's preamble: an amendment with only Part III has none of the keys.
    # red-exempt: empty the correct answer for this document is the empty list
    doc = ix_strip_html(_doc(
        "<div>Item 10. Directors, Executive Officers and Corporate Governance</div>"
        f"<p>{PADDING}</p>"
        f"<div>Item 11. Executive Compensation</div><p>{PADDING}</p>"))
    assert detect_sections(doc) == []


@pytest.mark.criterion("C11")
@given(SUBSET, st.booleans())
def test_property_section_invariants(chosen, with_notes):
    # oracle: Requirement B's preamble and 18: the structure holds for any filing.
    body = []
    for index, shape in sorted(chosen):
        _key, number, title, token = ITEMS[index]
        body.append(f"{_heading(shape, number, title)}<p>{token}{PADDING}</p>")
        if with_notes and number == "8":
            body.append(f"<div>Notes to Financial Statements</div><p>NOTEBODY{PADDING}</p>")
    doc = ix_strip_html(_doc("".join(body)))
    found = detect_sections(doc)
    keys = {ITEMS[i][0] for i, _ in chosen} - {None}
    has_item_8 = "item_8" in keys
    assert {s.key for s in found} == (keys | {"notes"} if with_notes and has_item_8 else keys)
    _assert_invariants(doc, found)
    assert detect_sections(doc) == found
