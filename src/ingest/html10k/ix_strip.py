# Ix-strip canonicalization for the HTML 10-K flow (docs/specs/html10k-strip-sections.md).
# The HTML flow is the baseline XbrlRag is measured against, so it must not see XBRL
# (docs/design/XbrlRag_v2_Plan.md 5.2).
#
# The output tree is built afresh from what the parser read, rather than edited in place, so the
# two parser paths and a document with or without its tags all serialize the same way: every
# element in the default XHTML namespace, names in lower case, no other declaration.

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

import lxml.etree as etree

from src.ingest.sec_edgar.xbrl.html_canonicalizer import XHTML_NS, _normalise_ws

logger = logging.getLogger(__name__)

_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

# Removed with their content. The last three only ever occur inside ix:header; they are listed so
# that a header the recovery parser closed early still cannot leak.
_IX_REMOVED = frozenset({"header", "hidden", "references", "resources"})
_HTML_REMOVED = frozenset({"style", "script", "link"})

# Compared in lower case. Namespace-qualified attributes go by rule, so xsi:nil is not listed.
_DROPPED_ATTRIBUTES = frozenset(
    {
        "style",
        "xmlns",
        "contextref",
        "unitref",
        "decimals",
        "precision",
        "scale",
        "sign",
        "format",
        "continuedat",
        "escape",
        "fromrefs",
        "torefs",
        "footnoterole",
        "footnoterefs",
        "footnoteid",
        "footnotelinkrole",
        "linkrole",
        "arcrole",
        "tupleref",
        "tupleid",
        "nil",
    }
)

# What a name must look like to be written out. A recovery parse can hand back anything.
_XML_NAME = re.compile(r"[a-z_][a-z0-9_.-]*")
# The recovery parser keeps a prefix as part of the tag, so a filer's choice of prefix is read here.
_IX_PREFIX_DECLARATION = re.compile(
    r"""xmlns:([\w.-]+)\s*=\s*["'][^"']*inlinexbrl[^"']*["']""", re.IGNORECASE
)


def _text_table() -> dict[int, str | None]:
    """Build the str.translate table that makes any text legal XML.

    U+0080-U+009F become their Windows-1252 equivalents, so ``&#146;`` reads as a quotation mark.
    The same table finishes a Latin-1 decode, which makes that decode Windows-1252 with the five
    undefined bytes dropped. Controls that XML forbids go, except the two that separate words.
    """
    table: dict[int, str | None] = {code: None for code in range(0x20) if code not in (9, 10, 13)}
    table[0x0B] = table[0x0C] = " "
    table[0xFFFE] = table[0xFFFF] = None
    for code in range(0x80, 0xA0):
        table[code] = bytes([code]).decode("cp1252", "ignore") or None
    return table


_TEXT_TABLE = _text_table()


def _decode(source_bytes: bytes) -> str:
    """Read the bytes as UTF-8 if they are valid UTF-8, otherwise as Windows-1252."""
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = source_bytes.decode("latin-1")
    return text.translate(_TEXT_TABLE)


def _parse(text: str, name: str) -> tuple[etree._Element | None, frozenset[str]]:
    """Parse the decoded document.

    Args:
        text: The decoded document.
        name: Used only in log messages.

    Returns:
        The root, ``None`` for a document with nothing in it, and the lower-case prefixes bound to
        inline XBRL. The prefixes are empty on the XML path, where namespaces do that job.
    """
    data = text.encode("utf-8")
    # The encoding argument overrides a declaration: the bytes are UTF-8 by now, whatever it says.
    xml_parser = etree.XMLParser(
        encoding="utf-8", resolve_entities=False, no_network=True, huge_tree=True
    )
    try:
        root = etree.fromstring(data, xml_parser)
    except etree.XMLSyntaxError:
        root = None
    # An undefined &nbsp; is no error when a DOCTYPE names an external DTD; it survives as an
    # entity node with no text, which would drop the character. Such a document is not well-formed.
    if root is not None and next(root.iter(etree.Entity), None) is None:
        return root, frozenset()

    logger.warning("%s is not well-formed XML; using the HTML recovery parser", name)
    prefixes = {"ix"} | {p.lower() for p in _IX_PREFIX_DECLARATION.findall(text)}
    try:
        recovered = etree.fromstring(
            data, etree.HTMLParser(encoding="utf-8", no_network=True, huge_tree=True)
        )
    except etree.XMLSyntaxError:  # raised for an input with no content at all
        return None, frozenset()
    return recovered, frozenset(prefixes)


def _disposition(element: etree._Element, ix_prefixes: frozenset[str]) -> str:
    """Classify an element as ``"keep"``, ``"unwrap"`` or ``"remove"``."""
    namespace, prefix = "", ""
    tag = element.tag
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
    else:
        prefix, _, local = tag.rpartition(":")
    local = local.lower()

    if "inlinexbrl" in namespace.lower() or prefix.lower() in ix_prefixes:
        return "remove" if local in _IX_REMOVED else "unwrap"
    if prefix or namespace not in ("", XHTML_NS) or not _XML_NAME.fullmatch(local):
        return "unwrap"
    return "remove" if local in _HTML_REMOVED else "keep"


def _kept_attributes(element: etree._Element) -> dict[str, str]:
    """Return the attributes that survive, with lower-case names."""
    kept: dict[str, str] = {}
    for raw_name, value in element.attrib.items():
        name = raw_name.lower()
        if raw_name == _XML_LANG or name == "xml:lang":
            name = _XML_LANG
        elif name in _DROPPED_ATTRIBUTES or name.startswith("on") or not _XML_NAME.fullmatch(name):
            continue
        kept.setdefault(name, value.translate(_TEXT_TABLE))
    return kept


def _output_tag(local: str) -> str:
    return f"{{{XHTML_NS}}}{local}"


def _local_lower(element: etree._Element) -> str:
    return element.tag.rpartition("}")[2].lower()


def _append_text(target: etree._Element, text: str | None) -> None:
    """Add text after whatever ``target`` already ends with."""
    if not text:
        return
    cleaned = text.translate(_TEXT_TABLE)
    if not cleaned:
        return
    if len(target):
        target[-1].tail = (target[-1].tail or "") + cleaned
    else:
        target.text = (target.text or "") + cleaned


def _copy(root: etree._Element, ix_prefixes: frozenset[str]) -> tuple[etree._Element, set[str]]:
    """Build the output tree from the parsed one.

    Args:
        root: The parsed document.
        ix_prefixes: The inline-XBRL prefixes, on the recovery path.

    Returns:
        The output root, and the ids that left the document and that nothing kept still carries.
    """
    output = etree.Element(_output_tag("html"), nsmap={None: XHTML_NS})
    kept_ids: set[str] = set()
    gone_ids: set[str] = set()

    # Each frame: the source children still to read, where they go, and the text to add to
    # ``after`` once they are done (the tail of the element they belong to). A stack and not
    # recursion, because nesting depth is the filer's to choose.
    frames: list[tuple[Iterator[etree._Element], etree._Element, etree._Element, str | None]]
    if root.tag.rpartition("}")[2].lower() == "html":
        output.attrib.update(_kept_attributes(root))
        _append_text(output, root.text)
        frames = [(iter(root), output, output, None)]
    else:  # a fragment: give it the document it lacks, so the root is never an unwrapped element
        body = etree.SubElement(output, _output_tag("body"))
        frames = [(iter((root,)), body, body, None)]

    while frames:
        children, target, after, tail = frames[-1]
        child = next(children, None)
        if child is None:
            frames.pop()
            _append_text(after, tail)
            continue
        if not isinstance(child.tag, str):  # comment or processing instruction
            _append_text(target, child.tail)
            continue

        disposition = _disposition(child, ix_prefixes)
        if disposition == "remove":
            gone_ids.update(e.get("id") for e in child.iter() if e.get("id"))
            _append_text(target, child.tail)
        elif disposition == "unwrap":
            if child.get("id"):
                gone_ids.add(child.get("id"))
            _append_text(target, child.text)
            frames.append((iter(child), target, target, child.tail))
        else:
            attributes = _kept_attributes(child)
            if "id" in attributes:
                kept_ids.add(attributes["id"])
            inner = etree.SubElement(target, _output_tag(_local_lower(child)), attributes)
            _append_text(inner, child.text)
            frames.append((iter(child), inner, target, child.tail))

    return output, gone_ids - kept_ids


def _drop_links_to(root: etree._Element, gone_ids: set[str]) -> None:
    """Take the href off every link whose target left the document."""
    for element in root.iter():
        href = element.get("href")
        if href and href.startswith("#") and href[1:] in gone_ids:
            del element.attrib["href"]


def _normalise_text(root: etree._Element) -> None:
    """Collapse whitespace in every text run outside ``head``."""
    in_head = {node for head in root.iter(_output_tag("head")) for node in head.iter()}
    for element in root.iter():
        if element.text and element not in in_head:
            element.text = _normalise_ws(element.text)
        if element.tail and element.getparent() not in in_head:
            element.tail = _normalise_ws(element.tail)


def ix_strip_html(source_bytes: bytes, name: str = "document") -> bytes:
    """Remove every trace of XBRL from a primary document, keeping what a reader sees.

    Args:
        source_bytes: The primary document, iXBRL or plain HTML, as downloaded.
        name: Used only in log messages.

    Returns:
        UTF-8 bytes that a strict XML parser accepts.
    """
    root, ix_prefixes = _parse(_decode(source_bytes), name)
    if root is None:
        output = etree.Element(_output_tag("html"), nsmap={None: XHTML_NS})
    else:
        output, gone_ids = _copy(root, ix_prefixes)
        _drop_links_to(output, gone_ids)
        _normalise_text(output)
    return etree.tostring(output, encoding="utf-8", xml_declaration=True, pretty_print=False)
