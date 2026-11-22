# The inline XBRL documents of one filing, and what each ingest stage reads from them.
#
# A filing's inline XBRL can span several HTML documents. This module is the one place that says
# which documents those are, which file Arelle loads, and whether the saved documents hold every
# fact of the filing (docs/specs/ingest-multidoc-ixbrl.md).

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import lxml.etree as etree

INSTANCE_SUFFIX = "_htm.xml"

_FACT_LOCAL_NAMES = frozenset({"nonFraction", "nonNumeric", "fraction"})
_FACT_LOCAL_NAMES_LOWER = frozenset(name.lower() for name in _FACT_LOCAL_NAMES)

# The root's element children of an instance that are not facts.
_NOT_INSTANCE_FACTS = frozenset(
    {"schemaRef", "context", "unit", "footnoteLink", "roleRef", "arcroleRef", "linkbaseRef"}
)

# The recovery parser keeps a prefix as part of the tag, so a filer's choice of prefix is read here.
_IX_PREFIX_DECLARATION = re.compile(
    rb"""xmlns:([\w.-]+)\s*=\s*["'][^"']*inlinexbrl[^"']*["']""", re.IGNORECASE
)


class DocumentSetMismatchError(ValueError):
    """The saved inline documents do not hold every fact of the filing's instance."""


def _strict_parser() -> etree.XMLParser:
    return etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)


def is_inline_fact(element: etree._Element) -> bool:
    """Whether a parsed element is an inline XBRL fact.

    Args:
        element: An element of a namespace-aware parse.

    Returns:
        True for ``ix:nonFraction``, ``ix:nonNumeric`` and ``ix:fraction``, in any version of the
        inline namespace.
    """
    tag = element.tag
    if not isinstance(tag, str) or not tag.startswith("{"):
        return False
    namespace, _, local = tag[1:].partition("}")
    return local in _FACT_LOCAL_NAMES and "inlinexbrl" in namespace.lower()


def _recovered_fact_ids(document: bytes) -> list[str | None]:
    """Read fact elements from a document that is not well-formed XML."""
    declared = _IX_PREFIX_DECLARATION.findall(document)
    prefixes = {prefix.decode("ascii", "ignore").lower() for prefix in declared}
    try:
        root = etree.fromstring(document, etree.HTMLParser(encoding="utf-8", huge_tree=True))
    except etree.XMLSyntaxError:  # raised for an input with no content at all
        return []
    found: list[str | None] = []
    for element in root.iter():
        if isinstance(element.tag, str):
            prefix, _, local = element.tag.lower().rpartition(":")
            if prefix in prefixes and local in _FACT_LOCAL_NAMES_LOWER:
                found.append(element.get("id"))
    return found


def inline_fact_ids(document: bytes) -> list[str | None]:
    """Return one entry per inline fact element of a document.

    Args:
        document: The raw bytes of an HTML document.

    Returns:
        Each fact's ``id``, or None for a fact without one, in document order. A document that only
        mentions the inline namespace, or binds it and tags nothing, gives an empty list.
    """
    if b"inlinexbrl" not in document.lower():
        return []  # most exhibits: no need to parse HTML that is often not XML
    try:
        root = etree.fromstring(document, _strict_parser())
    except etree.XMLSyntaxError:
        return _recovered_fact_ids(document)
    return [element.get("id") for element in root.iter() if is_inline_fact(element)]


def _instance_fact_ids(instance: bytes) -> tuple[int, set[str]]:
    """Return an instance's fact count and the ids those facts carry."""
    try:
        root = etree.fromstring(instance, _strict_parser())
    except etree.XMLSyntaxError as exc:
        raise DocumentSetMismatchError(f"The extracted instance cannot be parsed: {exc}") from exc
    facts = [
        child
        for child in root
        if isinstance(child.tag, str) and etree.QName(child).localname not in _NOT_INSTANCE_FACTS
    ]
    return len(facts), {fact.get("id") for fact in facts if fact.get("id")}


def inline_document_names(manifest: Mapping[str, Any]) -> list[str]:
    """Return a filing's inline XBRL documents, primary first.

    Args:
        manifest: The filing's ``manifest.yaml``. One written before this change has no
            ``ixbrl_documents`` key and names its primary document only.

    Returns:
        File names in filing order.
    """
    listed = manifest.get("ixbrl_documents")
    if listed:
        return [str(name) for name in listed]
    primary = manifest.get("primary_document")
    return [str(primary)] if primary else []


def instance_name(manifest: Mapping[str, Any]) -> str | None:
    """Return the extracted instance a manifest lists.

    Args:
        manifest: The filing's ``manifest.yaml``.

    Returns:
        The ``*_htm.xml`` in ``files``, or None when it lists none.
    """
    names = sorted(str(name) for name in manifest.get("files") or [])
    return next((name for name in names if name.lower().endswith(INSTANCE_SUFFIX)), None)


def arelle_entry_name(manifest: Mapping[str, Any]) -> str:
    """Return the file every Arelle stage loads for a filing.

    Args:
        manifest: The filing's ``manifest.yaml``.

    Returns:
        The primary document of a single-document filing, otherwise the extracted instance.

    Raises:
        ValueError: A multi-document filing's manifest lists no ``*_htm.xml`` instance, or the
            manifest names no document at all.
    """
    names = inline_document_names(manifest)
    if not names:
        raise ValueError("The manifest names no primary document")
    if len(names) == 1:
        return names[0]
    instance = instance_name(manifest)
    if instance is None:
        raise ValueError(
            f"Filing with {len(names)} inline documents lists no extracted instance "
            f"(*{INSTANCE_SUFFIX})"
        )
    return instance


def assert_document_set_complete(inline_documents: Sequence[bytes], instance: bytes) -> int:
    """Check that the saved inline documents hold every fact of the extracted instance.

    Args:
        inline_documents: The raw bytes of each saved inline document.
        instance: The raw bytes of the extracted instance (``*_htm.xml``).

    Returns:
        The number of facts in the instance.

    Raises:
        DocumentSetMismatchError: The documents carry fewer facts than the instance, or an instance
            fact id appears in none of them.
    """
    fact_count, instance_ids = _instance_fact_ids(instance)
    inline_ids = [fact_id for document in inline_documents for fact_id in inline_fact_ids(document)]
    if len(inline_ids) < fact_count:
        raise DocumentSetMismatchError(
            f"The saved documents hold {len(inline_ids)} fact elements; "
            f"the instance has {fact_count} facts"
        )
    absent = sorted(instance_ids.difference(inline_ids))
    if absent:
        raise DocumentSetMismatchError(
            f"{len(absent)} instance fact ids are in none of the saved documents: "
            f"{', '.join(absent[:5])}"
        )
    return fact_count
