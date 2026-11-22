"""A small, independent reader of the facts a filer tagged in its inline XBRL documents.

It reads the raw iXBRL HTML as filed: contexts and units from ``ix:resources``, and every
``ix:nonFraction`` (numbers) and ``ix:nonNumeric`` (dates, text) fact, with its element
``id``. It does not use the fact store, Arelle or our concept mapping, so a value read
here is the filer's own figure, not ours. Numbers apply the iXBRL rules: the displayed
text read through its ``format`` transform, times ``10 ** scale``, negated when
``sign="-"``. Dates are the contexts' dates as written (no Arelle one-day shift).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree  # type: ignore[import-untyped]  # lxml ships no stubs

IX_NS = ("http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL")
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

_TEXT_CAP = 400
_ZERO_FORMATS = frozenset({"zerodash", "fixed-zero", "fixedzero", "num-dash", "zerodot"})
_EMPTY_FORMATS = frozenset({"fixed-empty", "nocontent"})
_COMMA_DECIMAL = ("comma-decimal", "commadecimal", "numdotcomma", "numspacecomma",
                  "num-dot-comma", "num-space-comma")


@dataclass(frozen=True)
class Context:
    """One ``xbrli:context``.

    Attributes:
        context_id: The context id.
        start: ISO start date of a duration, else None.
        end: ISO end date of a duration, or the instant.
        instant: Whether the period is an instant.
        dims: ``((axis, member), ...)`` sorted by axis; empty for an undimensioned fact.
    """

    context_id: str
    start: str | None
    end: str | None
    instant: bool
    dims: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RawFact:
    """One tagged fact, as filed.

    Attributes:
        fact_id: The element ``id`` (the anchor to jump to in the document).
        concept: The concept QName as written, e.g. ``us-gaap:Revenues``.
        numeric: True for ``ix:nonFraction``.
        value: The numeric value (scale and sign applied); None for text, nil or an
            unreadable number.
        text: The displayed text (capped for text facts).
        nil: Whether the fact is nil.
        unit: The unit as ``USD``, ``shares``, ``USD/shares`` ...; None for text.
        decimals: The ``decimals`` attribute (``INF`` or an int as text), or None.
        scale: The ``scale`` attribute as an int (0 when absent).
        sign: ``-`` when the sign attribute negates the value, else empty.
        context: The fact's context.
        document: The file name of the document holding the fact.
    """

    fact_id: str
    concept: str
    numeric: bool
    value: float | None
    text: str
    nil: bool
    unit: str | None
    decimals: str | None
    scale: int
    sign: str
    context: Context
    document: str

    @property
    def local_name(self) -> str:
        """The concept's local name."""
        return self.concept.split(":", 1)[-1]

    @property
    def prefix(self) -> str:
        """The concept's namespace prefix as written."""
        return self.concept.split(":", 1)[0] if ":" in self.concept else ""


def _local(tag: object) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _ns(tag: object) -> str:
    return etree.QName(tag).namespace or "" if isinstance(tag, str) else ""


def _text_of(el: etree._Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_MULT = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}


def parse_number_words(text: str) -> float | None:
    """English number words (``ixt-sec:numwordsen``): ``"three"``, ``"twenty-one"``, ``"no"``.

    Returns:
        The number, or None when a word is not a number word.
    """
    words = re.findall(r"[a-z]+", text.lower().replace("-", " "))
    if not words:
        return None
    if words in (["no"], ["none"], ["nil"]):
        return 0.0
    total = current = 0
    for w in words:
        if w == "and":
            continue
        if w in _WORDS:
            current += _WORDS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w in _MULT:
            total += max(current, 1) * _MULT[w]
            current = 0
        else:
            return None
    return float(total + current)


def parse_number(text: str, fmt: str | None) -> float | None:
    """Reads a displayed number through its iXBRL ``format`` transform.

    Args:
        text: The displayed text, e.g. ``"1,234.5"``, ``"—"`` or ``"1.234,5"``.
        fmt: The ``format`` attribute (``ixt:num-dot-decimal`` ...), or None.

    Returns:
        The unsigned, unscaled number, or None when the text cannot be read.
    """
    name = (fmt or "").split(":", 1)[-1].lower()
    if name in _ZERO_FORMATS:
        return 0.0
    if name in _EMPTY_FORMATS:
        return None
    if name in ("numwordsen", "num-words-en"):
        return parse_number_words(text)
    comma_decimal = any(tag in name for tag in _COMMA_DECIMAL)
    cleaned = text.strip()
    if comma_decimal:
        cleaned = re.sub(r"[^0-9,]", "", cleaned).replace(",", ".")
    else:
        cleaned = re.sub(r"[^0-9.]", "", cleaned)
    if not cleaned or cleaned == ".":
        # A dash or blank in a numeric cell reads as zero only under a zero format;
        # without one, an all-punctuation text such as "—" is the common zero display.
        return 0.0 if re.fullmatch(r"[\s\-–—]*", text) and text.strip() else None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _unit_text(unit: etree._Element) -> str:
    def measures(el: etree._Element | None) -> list[str]:
        if el is None:
            return []
        return [(m.text or "").split(":", 1)[-1].strip()
                for m in el.iter(f"{{{XBRLI}}}measure")]

    divide = unit.find(f"{{{XBRLI}}}divide")
    if divide is not None:
        num = measures(divide.find(f"{{{XBRLI}}}unitNumerator"))
        den = measures(divide.find(f"{{{XBRLI}}}unitDenominator"))
        return f"{'*'.join(num)}/{'*'.join(den)}"
    return "*".join(measures(unit))


def _context(el: etree._Element) -> Context:
    period = el.find(f"{{{XBRLI}}}period")
    start = end = None
    instant = False
    if period is not None:
        inst = period.find(f"{{{XBRLI}}}instant")
        if inst is not None:
            instant, end = True, (inst.text or "").strip()[:10]
        else:
            s = period.find(f"{{{XBRLI}}}startDate")
            e = period.find(f"{{{XBRLI}}}endDate")
            start = (s.text or "").strip()[:10] if s is not None else None
            end = (e.text or "").strip()[:10] if e is not None else None
    dims: list[tuple[str, str]] = []
    for member in el.iter(f"{{{XBRLDI}}}explicitMember"):
        dims.append((member.get("dimension", ""), (member.text or "").strip()))
    for member in el.iter(f"{{{XBRLDI}}}typedMember"):
        dims.append((member.get("dimension", ""), _text_of(member)))
    return Context(el.get("id", ""), start, end, instant, tuple(sorted(dims)))


def parse_documents(paths: list[Path]) -> list[RawFact]:
    """Reads every fact of a filing's inline documents.

    Args:
        paths: The filing's iXBRL documents (contexts may sit in any of them).

    Returns:
        The facts in document order; facts whose context is missing are left out.
    """
    parser = etree.XMLParser(huge_tree=True, recover=True, resolve_entities=False,
                             no_network=True)
    roots = [(p.name, etree.parse(str(p), parser).getroot()) for p in paths if p.exists()]
    contexts: dict[str, Context] = {}
    units: dict[str, str] = {}
    for _, root in roots:
        for el in root.iter(f"{{{XBRLI}}}context"):
            ctx = _context(el)
            contexts[ctx.context_id] = ctx
        for el in root.iter(f"{{{XBRLI}}}unit"):
            units[el.get("id", "")] = _unit_text(el)
    facts: list[RawFact] = []
    for name, root in roots:
        for el in root.iter():
            if _ns(el.tag) not in IX_NS:
                continue
            local = _local(el.tag)
            if local not in ("nonFraction", "nonNumeric"):
                continue
            fact_ctx = contexts.get(el.get("contextRef", ""))
            if fact_ctx is None:
                continue
            nil = el.get(XSI_NIL) == "true"
            text = _text_of(el)
            numeric = local == "nonFraction"
            scale = int(el.get("scale") or 0) if (el.get("scale") or "0").lstrip(
                "-").isdigit() else 0
            sign = "-" if el.get("sign") == "-" else ""
            value = None
            if numeric and not nil:
                number = parse_number(text, el.get("format"))
                if number is not None:
                    value = number * (10 ** scale)
                    if sign:
                        value = -value
            facts.append(RawFact(
                fact_id=el.get("id", ""),
                concept=el.get("name", ""),
                numeric=numeric,
                value=value,
                text=text if numeric else text[:_TEXT_CAP],
                nil=nil,
                unit=units.get(el.get("unitRef", "")) if numeric else None,
                decimals=el.get("decimals"),
                scale=scale,
                sign=sign,
                context=fact_ctx,
                document=name,
            ))
    return facts
