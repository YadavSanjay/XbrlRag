"""Spec tests for ix_strip_html (docs/specs/html10k-strip-sections.md, part A; C1-C6).

Inputs are the committed synthetic fixtures or documents built here. Expected text is derived by
hand from the fixture and shown in the test; no value comes from running the code under test.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import lxml.etree as etree
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.html10k.ix_strip import ix_strip_html
from src.ingest.sec_edgar.xbrl.html_canonicalizer import _canonicalize_html
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import build_section_tree

XHTML = "http://www.w3.org/1999/xhtml"
XML_NS = "{http://www.w3.org/XML/1998/namespace}"
IX_URI = "http://www.xbrl.org/2013/inlineXBRL"
XBRL_ATTRS = {"contextref", "unitref", "decimals", "precision", "scale", "sign", "format",
              "continuedat", "escape", "fromrefs", "torefs", "footnoterole", "nil"}


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _elements(root: etree._Element, name: str) -> list[etree._Element]:
    return [e for e in root.iter() if isinstance(e.tag, str) and _local(e) == name]


def _body(out: bytes) -> etree._Element:
    return _elements(etree.fromstring(out), "body")[0]


def _texts(out: bytes, name: str = "p") -> list[str]:
    return ["".join(e.itertext()) for e in _elements(_body(out), name)]


def _grid(table: etree._Element) -> list[list[str]]:
    return [["".join(td.itertext()) for td in _elements(tr, "td")] for tr in _elements(table, "tr")]


def _assert_no_xbrl(out: bytes, tokens: tuple[str, ...] = ()) -> None:
    """Check the leakage rule structurally, so prose such as 'Appendix: A' stays legal."""
    root = etree.fromstring(out)   # strict XML: an unbound prefix fails here
    for el in root.iter():
        assert isinstance(el.tag, str), f"comment or PI survived: {el!r}"
        assert etree.QName(el).namespace in (None, XHTML), el.tag
        assert set(el.nsmap) <= {None}, el.nsmap
        for attr in el.attrib:
            if attr.startswith("{"):
                assert attr == f"{XML_NS}lang", attr
            assert etree.QName(attr).localname.lower() not in XBRL_ATTRS, attr
    assert not re.search(rb"xmlns:", out)
    assert not re.search(rb"<!--", out)
    assert not re.search(rb"</?[A-Za-z][\w.-]*:", out)
    for token in tokens:
        assert token.encode() not in out, token


# Everything the ix_header fixture declares that must not reach the HTML flow.
HEADER_TOKENS = ("fact-hid-1", "fact-hid-2", "fact-vis-1", "ctx-fy2025", "unit-shares", "unit-usd",
                 "us-gaap", "dei:", "AmendmentFlag", "Revenues", "HIDDENFLAGVALUE", "4242424",
                 "exmp-20251231", "0000999999", "iso4217", "2025-01-01", "Workiva")


@pytest.mark.criterion("C1")
def test_no_xbrl_survives_a_workiva_shaped_document(fixture_bytes):
    # oracle: Requirements 4-8 and plan 5.2; the tokens are the ids the fixture declares.
    out = ix_strip_html(fixture_bytes("ix_header.htm"))
    _assert_no_xbrl(out, HEADER_TOKENS)


@pytest.mark.criterion("C1")
def test_no_xbrl_survives_the_recovery_parser(fixture_bytes):
    # oracle: Requirement 3. A bare &nbsp; is undefined in XML, so the document takes the HTML
    # recovery path, where ix: names are lowercased and carry no namespace.
    source = fixture_bytes("ix_header.htm").replace(b"Accounting policy.",
                                                    b"Accounting&nbsp;policy.")
    out = ix_strip_html(source)
    _assert_no_xbrl(out, HEADER_TOKENS)
    assert "Accounting policy." in _texts(out)


@pytest.mark.criterion("C1")
def test_link_to_a_fact_id_loses_its_href_and_keeps_its_text(fixture_bytes):
    # oracle: Requirement 8; fact-vis-1 is an ix element's id, keep-anchor a paragraph's.
    body = _body(ix_strip_html(fixture_bytes("ix_header.htm")))
    links = {"".join(a.itertext()): a.get("href") for a in _elements(body, "a")}
    assert links == {"the revenue figure": None, "the policy": "#keep-anchor"}


@pytest.mark.criterion("C1")
def test_other_attributes_stay_and_handlers_go(fixture_bytes):
    # oracle: Requirement 6; the fixture's lead paragraph has a class, the policy one an onclick.
    out = ix_strip_html(fixture_bytes("ix_header.htm"))
    body = _body(out)
    assert [p.get("class") for p in _elements(body, "p")][0] == "lead"
    policy = next(p for p in _elements(body, "p") if p.get("id") == "keep-anchor")
    assert policy.get("onclick") is None
    assert not any(e.get("style") for e in body.iter())


TEXT = st.text(alphabet="abcXY019,.$()  ", min_size=1, max_size=6).map(lambda s: f"q{s}")
IDS = st.integers(min_value=1, max_value=9999)


def _fact(prefix: str, text: str, n: int) -> str:
    return (f'<{prefix}:nonFraction contextRef="zzctx{n}" unitRef="zzunit{n}" name="zzg:Zz{n}" '
            f'id="zzfact{n}" decimals="-6" scale="6" format="zzt:num">{text}'
            f"</{prefix}:nonFraction>")


def _text_fact(prefix: str, text: str, n: int) -> str:
    return (f'<{prefix}:nonNumeric contextRef="zzctx{n}" name="zzg:Zy{n}" id="zzfact{n}b">'
            f'{text}</{prefix}:nonNumeric>')


@st.composite
def _inline(draw, prefix: str) -> tuple[str, str]:
    """One run of inline content, as (tagged, untagged)."""
    tagged, plain = [], []
    for text, kind, n in draw(st.lists(st.tuples(TEXT, st.sampled_from("pfns"), IDS),
                                       min_size=1, max_size=4)):
        plain.append(f"<span>{text}</span>" if kind == "s" else text)
        if kind == "p":
            tagged.append(text)
        elif kind == "f":
            tagged.append(_fact(prefix, text, n))
        elif kind == "n":
            tagged.append(_text_fact(prefix, _fact(prefix, text, n), n))
        else:
            tagged.append(f"<span>{_fact(prefix, text, n)}</span>")
    return "".join(tagged), "".join(plain)


@st.composite
def _block(draw, prefix: str) -> tuple[str, str]:
    """One block, as (tagged, untagged)."""
    kind = draw(st.sampled_from(["p", "table", "wrap", "cont", "foot", "excl"]))
    first_t, first_p = draw(_inline(prefix))
    second_t, second_p = draw(_inline(prefix))
    n = draw(IDS)
    if kind == "table":
        row = "<tr><td>{}</td><td colspan=\"2\">{}</td></tr>"
        return f"<table>{row.format(first_t, second_t)}</table>", \
            f"<table>{row.format(first_p, second_p)}</table>"
    para_t, para_p = f"<p>{first_t}</p><p>{second_t}</p>", f"<p>{first_p}</p><p>{second_p}</p>"
    if kind == "p":
        return para_t, para_p
    if kind == "wrap":
        return (f'<{prefix}:nonNumeric contextRef="zzctx{n}" name="zzg:Zx{n}" id="zzfact{n}c" '
                f'continuedAt="zzcont{n}">{para_t}</{prefix}:nonNumeric>'), para_p
    if kind == "cont":
        return f'<{prefix}:continuation id="zzcont{n}">{para_t}</{prefix}:continuation>', para_p
    if kind == "foot":
        return f'<{prefix}:footnote id="zzfoot{n}">{para_t}</{prefix}:footnote>', para_p
    return f"<{prefix}:exclude>{para_t}</{prefix}:exclude>", para_p


def _document(body: str, prefix: str, *, tagged: bool, malformed: bool) -> bytes:
    xmlns = f' xmlns:{prefix}="{IX_URI}"' if tagged else ""
    header = (f'<div style="display:none"><{prefix}:header><{prefix}:hidden>'
              f'<{prefix}:nonNumeric contextRef="zzctx0" name="zzg:Hid" id="zzfact0">zzhidden'
              f"</{prefix}:nonNumeric></{prefix}:hidden></{prefix}:header></div>") if tagged \
        else "<div></div>"
    tail = "<p>end&nbsp;mark</p>" if malformed else "<p>end mark</p>"
    return (f'<html xmlns="{XHTML}"{xmlns}><head><title>t</title></head><body>{header}{body}{tail}'
            f"</body></html>").encode()


@st.composite
def _pair(draw) -> tuple[bytes, bytes]:
    """A generated document and the same document with its XBRL taken out by hand."""
    prefix = draw(st.sampled_from(["ix", "inl"]))
    malformed = draw(st.booleans())
    blocks = draw(st.lists(_block(prefix), min_size=1, max_size=4))
    tagged = "".join(b[0] for b in blocks)
    plain = "".join(b[1] for b in blocks)
    return (_document(tagged, prefix, tagged=True, malformed=malformed),
            _document(plain, prefix, tagged=False, malformed=malformed))


@pytest.mark.criterion("C1")
@given(_pair())
def test_property_no_xbrl_survives_any_nesting(pair):
    # oracle: Requirements 3-8; every XBRL token the generator writes contains "zz", which its
    # text alphabet cannot produce.
    tagged, _ = pair
    _assert_no_xbrl(ix_strip_html(tagged), ("zz", "Zz"))


@pytest.mark.criterion("C2")
def test_paragraphs_of_nested_text_blocks(fixture_bytes):
    # oracle: hand reading of nested_ix_tables.htm under Requirements 5 and 9: tags unwrapped,
    # the double space and the NBSP collapsed, the fraction's slash kept.
    out = ix_strip_html(fixture_bytes("nested_ix_tables.htm"))
    assert _texts(out) == [
        "Note 7. Debt",
        "Long-term debt is carried at cost.",
        "Rate of 1/4 point.",
        "Page 12",
        "The loan matures in 2031.",
        "(1) Excludes leases.",
    ]


@pytest.mark.criterion("C2")
def test_table_grid_and_spans_under_nested_tags(fixture_bytes):
    # oracle: hand reading of the fixture's table: the NBSP cell becomes one space (Requirement 9).
    body = _body(ix_strip_html(fixture_bytes("nested_ix_tables.htm")))
    table = _elements(body, "table")[0]
    assert table.get("id") == "tbl-debt"
    assert _grid(table) == [
        [" ", "2025", "2024"],
        ["Term loan", "$", "1,234", "$", "987"],
        ["", "(56)", "", "—"],
        ["Accrued", "", "", "", ""],
    ]
    cells = _elements(table, "td")
    assert [c.get("colspan") for c in cells[:3]] == [None, "2", "2"]
    assert cells[3].get("rowspan") == "2"


@pytest.mark.criterion("C2")
def test_text_adjoining_a_fact_merges_before_normalising():
    # oracle: Requirement 9, by hand: "a " + " b" unwraps to "a  b", which collapses to "a b".
    source = (f'<html xmlns="{XHTML}" xmlns:ix="{IX_URI}"><body><p>a '
              '<ix:nonFraction contextRef="c" id="f"> b</ix:nonFraction></p></body></html>'
              ).encode()
    assert _texts(ix_strip_html(source)) == ["a b"]


@pytest.mark.criterion("C2")
@given(_pair())
def test_property_tagging_never_changes_the_output(pair):
    # oracle: Requirement 11: the untagged twin is built alongside the tagged document.
    tagged, plain = pair
    assert ix_strip_html(tagged) == ix_strip_html(plain)


@pytest.mark.criterion("C3")
def test_hidden_content_is_gone_and_the_wrapper_stays_empty(fixture_bytes):
    # oracle: Requirement 4; the wrapper is the div with id hidden-wrap in the fixture.
    body = _body(ix_strip_html(fixture_bytes("ix_header.htm")))
    wrapper = next(e for e in _elements(body, "div") if e.get("id") == "hidden-wrap")
    assert len(wrapper) == 0
    assert not (wrapper.text or "").strip()
    assert "HIDDEN" not in "".join(body.itertext())


@pytest.mark.criterion("C3")
def test_hidden_layout_cell_stays(fixture_bytes):
    # oracle: Requirement 4; the fixture's one table row has three cells, the middle one hidden.
    body = _body(ix_strip_html(fixture_bytes("ix_header.htm")))
    assert _grid(_elements(body, "table")[0]) == [["Label", "", "Value"]]


@pytest.mark.criterion("C3")
def test_prose_that_looks_like_xbrl_is_kept(fixture_bytes):
    # oracle: the fixture's paragraphs, read by hand; the fact's scale is not applied.
    out = ix_strip_html(fixture_bytes("ix_header.htm"))
    assert _texts(out) == [
        "Net revenue was $1,234 million.",
        "Filer status: Yes",
        "Matrix: see Appendix: A, where contextRef is an ordinary word.",
        "the revenue figure and the policy",
        "Accounting policy.",
    ]


@pytest.mark.criterion("C3")
@given(hidden=st.text(alphabet="BCDFG", min_size=8, max_size=12),
       visible=st.text(alphabet="JKLMN", min_size=8, max_size=12))
def test_property_hidden_tokens_never_appear(hidden, visible):
    # oracle: Requirement 4; the two alphabets are disjoint, so one cannot be part of the other.
    source = (f'<html xmlns="{XHTML}" xmlns:ix="{IX_URI}"><body><div style="display:none">'
              f'<ix:header><ix:hidden><ix:nonNumeric contextRef="c" id="f">{hidden}'
              f"</ix:nonNumeric></ix:hidden></ix:header></div><p>{visible}</p></body></html>").encode()
    out = ix_strip_html(source)
    assert hidden.encode() not in out
    assert _texts(out) == [visible]


@pytest.mark.criterion("C4")
def test_values_render_as_the_filing_displays_them(fixture_bytes):
    # oracle: Requirement 10 and the fixture: 1,234 at scale 6 is not 1,234,000,000, sign="-"
    # is already shown by the parentheses, the fixed-zero fact displays a dash, the nil is empty.
    body = _body(ix_strip_html(fixture_bytes("nested_ix_tables.htm")))
    grid = _grid(_elements(body, "table")[0])
    assert grid[1][2] == "1,234"
    assert grid[2][1] == "(56)"
    assert grid[2][3] == "—"
    assert grid[3][2] == ""


@pytest.mark.criterion("C4")
@given(scale=st.sampled_from(["0", "3", "6", "9"]), sign=st.sampled_from(["", ' sign="-"']),
       decimals=st.sampled_from(["0", "-3", "-6", "INF"]),
       fmt=st.sampled_from(["ixt:num-dot-decimal", "ixt:fixed-zero", "ixt:num-comma-decimal"]))
@pytest.mark.metamorphic
def test_metamorphic_fact_attributes_never_change_the_output(scale, sign, decimals, fmt):
    # oracle: Requirement 10: the plain document shows what the fact shows.
    source = (f'<html xmlns="{XHTML}" xmlns:ix="{IX_URI}"><body><p>$<ix:nonFraction contextRef="c" '
              f'id="f" scale="{scale}"{sign} decimals="{decimals}" format="{fmt}">1,234'
              "</ix:nonFraction></p></body></html>").encode()
    plain = f'<html xmlns="{XHTML}"><body><p>$1,234</p></body></html>'.encode()
    assert ix_strip_html(source) == ix_strip_html(plain)


def _older(fixture_bytes) -> bytes:
    """The committed older filing (UTF-8 in the repository), as the Windows-1252 bytes it models."""
    return fixture_bytes("older_plain.htm").decode("utf-8").encode("cp1252")


@pytest.mark.criterion("C5")
def test_older_filing_text_and_punctuation(fixture_bytes):
    # oracle: older_plain.htm by hand; 0x92/&#146; are RIGHT SINGLE QUOTATION MARK and
    # 0x97/&#151; EM DASH in the Windows-1252 code page.
    out = ix_strip_html(_older(fixture_bytes))
    _assert_no_xbrl(out)
    assert out.decode("utf-8")   # valid UTF-8
    paragraphs = [t.strip() for t in _texts(out)]
    assert paragraphs[:2] == [
        "EXAMPLE MILLS, INC.",
        "The Company’s net sales rose 5% in 2006—a record; the Company’s margin fell—slightly.",
    ]
    assert "ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS" in paragraphs


@pytest.mark.criterion("C5")
def test_older_filing_table_and_anchors(fixture_bytes):
    # oracle: older_plain.htm by hand: two rows of two unclosed cells; NBSP becomes a space.
    body = _body(ix_strip_html(_older(fixture_bytes)))
    grid = [[c.strip() for c in row] for row in _grid(_elements(body, "table")[0])]
    assert grid == [["Net sales", "$ 1,234"], ["Net income", "(56)"]]
    assert [a.get("name") for a in _elements(body, "a") if a.get("name")] == ["item7", "item8"]
    assert [a.get("href") for a in _elements(body, "a") if a.get("href")] == ["#item7", "#item8"]


@pytest.mark.criterion("C5")
@given(st.lists(st.sampled_from([(0x91, "‘"), (0x92, "’"), (0x93, "“"), (0x94, "”"), (0x96, "–"),
                                 (0x97, "—"), (0x85, "…"), (0x99, "™")]), min_size=1, max_size=5))
def test_property_cp1252_bytes_and_references_read_the_same(punctuation):
    # oracle: the Windows-1252 code page; each pair above is the code page's own mapping.
    expected = "".join(ch for _, ch in punctuation)
    raw = bytes(code for code, _ in punctuation)
    refs = "".join(f"&#{code};" for code, _ in punctuation).encode()
    for inner in (raw, refs):
        source = b"<HTML><BODY><P>" + inner + b"</P></BODY></HTML>"
        assert _texts(ix_strip_html(source)) == [expected]


CANON_SOURCE = (
    b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    b"<body><style>p{}</style>\n"
    b'<p id="a1" style="color:red">Net   sales&#160;of '
    b'<ix:nonFraction contextRef="c" id="f-1">1  234&#160;5</ix:nonFraction> rose</p>\n'
    b"</body></html>"
)
# By hand, from the module docstring of html_canonicalizer.py: the style element goes and its
# tail stays, the style attribute goes, prose whitespace and NBSP collapse to one space, and the
# fact's text is byte-identical (NBSP is 0xC2 0xA0 in UTF-8).
CANON_EXPECTED = (
    b"<?xml version='1.0' encoding='utf-8'?>\n"
    b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    b'<body> <p id="a1">Net sales of '
    b'<ix:nonFraction contextRef="c" id="f-1">1  234\xc2\xa05</ix:nonFraction> rose</p> '
    b"</body></html>"
)


@pytest.mark.criterion("C6")
def test_xbrl_canonical_bytes_are_unchanged_and_the_modes_differ():
    # oracle: CANON_EXPECTED above, by hand from the canonicalizer's docstring.
    stripped = ix_strip_html(CANON_SOURCE)
    canonical, _ = _canonicalize_html(CANON_SOURCE)
    assert canonical == CANON_EXPECTED
    assert b"ix:nonFraction" in canonical
    _assert_no_xbrl(stripped, ("f-1",))
    assert _texts(stripped) == ["Net sales of 1 234 5 rose"]


@pytest.mark.criterion("C6")
def test_xbrl_section_tree_offsets_are_unchanged():
    # oracle: SectionNode's contract: char_start is the opening "<" of the anchor in canonical.htm.
    source = (
        f'<html xmlns="{XHTML}" xmlns:ix="{IX_URI}"><body>'
        '<p><a href="#i1">Item 1. Business</a></p><p><a href="#i7">Item 7. MD&amp;A</a></p>'
        '<p id="i1">Item 1. Business</p><p>Sales of '
        '<ix:nonFraction contextRef="c" id="f-1">1,234</ix:nonFraction>.</p>'
        '<p id="i7">Item 7. MD&amp;A</p><p>More.</p></body></html>'
    ).encode()
    _assert_no_xbrl(ix_strip_html(source))
    canonical, _ = _canonicalize_html(source)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "canonical.htm"
        path.write_bytes(canonical)
        tree = build_section_tree(path)
    starts = {n.html_anchor: n.char_start for n in tree.nodes.values() if n.html_anchor}
    assert starts == {"i1": canonical.index(b'<p id="i1"'), "i7": canonical.index(b'<p id="i7"')}


@pytest.mark.criterion("C6")
@given(st.text(alphabet="ab1,  ", min_size=1, max_size=8).map(lambda s: f"x{s}y"))
@pytest.mark.metamorphic
def test_metamorphic_only_the_new_mode_normalises_fact_text(fact_text):
    # oracle: the canonicalizer's docstring (fact text unaltered) against Requirement 9.
    source = (f'<html xmlns="{XHTML}" xmlns:ix="{IX_URI}"><body><p><ix:nonNumeric contextRef="c" '
              f'id="f">{fact_text}</ix:nonNumeric></p></body></html>').encode()
    canonical, _ = _canonicalize_html(source)
    collapsed = re.sub(r"[\s ]+", " ", fact_text)
    assert f">{fact_text}<".encode() in canonical
    assert _texts(ix_strip_html(source)) == [collapsed]
