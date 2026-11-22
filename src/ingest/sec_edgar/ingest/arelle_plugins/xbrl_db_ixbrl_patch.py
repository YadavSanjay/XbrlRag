"""Arelle plugin: compatibility shim for xbrlDB + iXBRL (ModelInlineFact).

Problem
-------
Arelle's XbrlPublicPostgresDB.insertFacts() accesses ``fact.xAttributes``
to read precision/decimals values.  ``xAttributes`` is populated on
``ModelFact`` instances during standard XBRL XML parsing, but
``ModelInlineFact`` (used for inline XBRL / .htm filings) does not always
initialise it, causing::

    AttributeError: 'ModelInlineFact' object has no attribute 'xAttributes'

Fix
---
Registers a ``CntlrCmdLine.xbrl.loaded`` hook that fires AFTER the XBRL
model is built but BEFORE xbrlDB's ``CntlrCmdLine.xbrl.run`` calls
``insertFacts()``.  The hook walks every fact in the model and, for any
``ModelInlineFact`` that is missing ``xAttributes``, synthesises the dict
from the element's own XML attributes (``precision`` / ``decimals``).

This is structurally identical to what ``ModelObject.init()`` does for
standard XBRL facts — we are just replicating the missed initialisation
for inline facts.

Load this plugin BEFORE xbrlDB::

    arelleCmdLine --plugins "path/to/xbrl_db_ixbrl_patch.py|xbrlDB" ...

This plugin never writes to the database or modifies XBRL validation logic.
"""

from __future__ import annotations


class _XAttr:
    """Minimal stand-in for Arelle's ModelAttribute, compatible with xbrlDB.

    xbrlDB only ever reads ``.xValue`` and checks:
      * ``isinstance(xValue, int)``  — numeric precision/decimals
      * ``xValue == 'INF'``          — infinite precision/decimals
    """

    __slots__ = ("xValue",)

    def __init__(self, raw: str) -> None:
        if raw == "INF":
            self.xValue: object = "INF"
        else:
            try:
                self.xValue = int(raw)
            except (ValueError, TypeError):
                self.xValue = raw


def _ensure_xAttributes(fact) -> None:
    """Populate fact.xAttributes if it is missing (inline XBRL facts only)."""
    try:
        _ = fact.xAttributes      # already present — do nothing
        return
    except AttributeError:
        pass

    attrs: dict = {}
    for attr_name in ("precision", "decimals"):
        raw = fact.get(attr_name)  # lxml _Element.get() → str | None
        if raw is not None:
            attrs[attr_name] = _XAttr(raw)

    # Assign directly on the instance.  ModelObject subclasses store extra
    # Python attributes on the lxml proxy's __dict__, exactly as
    # ModelObject.init() does when it sets self.xAttributes = {}.
    fact.xAttributes = attrs


def xbrl_loaded(cntlr, options, modelXbrl, *args, **kwargs) -> None:  # noqa: ANN001
    """CntlrCmdLine.xbrl.loaded hook — fires before xbrlDB's xbrl.run hook.

    Walks the full fact set and ensures every fact has xAttributes so that
    XbrlPublicPostgresDB.insertFacts() does not raise AttributeError.
    """
    patched = 0
    try:
        for fact in modelXbrl.facts:
            try:
                _ = fact.xAttributes
            except AttributeError:
                _ensure_xAttributes(fact)
                patched += 1
        if patched:
            cntlr.addToLog(
                f"[xbrl_db_ixbrl_patch] Populated xAttributes on {patched} inline fact(s)."
            )
    except Exception as exc:
        cntlr.addToLog(f"[xbrl_db_ixbrl_patch] Warning: xAttributes patch failed: {exc}")


# Arelle's PluginManager requires __pluginInfo__ to recognise a file as a
# valid plugin.  The hook mapping tells Arelle when to call our function.
__pluginInfo__ = {
    "name":                    "xbrl_db_ixbrl_patch",
    "version":                 "1.1",
    "description": (
        "Populates missing xAttributes on ModelInlineFact instances "
        "before xbrlDB stores facts to PostgreSQL."
    ),
    "license":                 "Apache-2",
    "author":                  "XbrlRag",
    "copyright":               "(c) 2024",
    "CntlrCmdLine.Xbrl.Loaded": xbrl_loaded,
}
