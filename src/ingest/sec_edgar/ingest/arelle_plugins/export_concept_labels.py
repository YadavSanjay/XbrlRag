"""Arelle plugin: export concept-to-label arcs and dimension member labels.

Two independent exports, each controlled by its own CLI option:

1. --concept-labels-output <path>
   Exports all conceptLabel arcs for the loaded DTS.
   Used by: ConceptLabelsPopulator → concept_labels table.

2. --dimension-members-output <path>
   Exports axis+member pairs found in XBRL contexts with their English labels.
   Used by: DimensionMembersPopulator → patches member_label in dimension_catalog.

Both can be requested in the same arelleCmdLine invocation.


This plugin is executed by the Arelle CLI (arelleCmdLine.exe) inside its own
Python 3.14 runtime.  It does NOT connect to the database directly; it writes
a JSON file that the ConceptLabelsPopulator class then reads and upserts.

Usage via arelleCmdLine.exe:

    arelleCmdLine.exe \
        -f path/to/filing.htm \
        --plugin path/to/export_concept_labels.py \
        --concept-labels-output /tmp/labels_accno.json \
        --concept-labels-cik 0001045810

Output JSON schema:
    {
      "count": <int>,
      "cik": "<10-digit CIK or null>",
      "source_file": "<entry_url>",
      "rows": [
        {
          "concept":      "<namespaceURI:localName>",
          "cik_override": "<10-digit CIK or null>",
          "xml_lang":     "en-US",
          "label_role":   "http://www.xbrl.org/2003/role/label",
          "label":        "Human readable label text"
        },
        ...
      ]
    }

concept format:
    Full namespace URI + ':' + local name, matching what Arelle's xbrlDB stores
    in the qname table (q.namespace || ':' || q.local_name).
    e.g. "http://fasb.org/us-gaap/2024-01-31:Revenues"
         "http://www.nvidia.com/20260125:DataCenterMember"

cik_override:
    NULL (None) for standard taxonomy concepts (us-gaap, dei, srt, ifrs-full, etc.)
    10-digit CIK string for filer extension concepts.
    This mirrors concept_labels.cik_override semantics.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

# arelle is available because this runs inside Arelle's own Python runtime.
from arelle import XbrlConst  # noqa: E402 (Arelle runtime import)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Label roles we care about — matches the design in XbrlRag_xbrl_materialized_views
LABEL_ROLES: frozenset = frozenset({
    "http://www.xbrl.org/2003/role/label",
    "http://www.xbrl.org/2003/role/terseLabel",
    "http://www.xbrl.org/2003/role/verboseLabel",
    "http://www.xbrl.org/2003/role/totalLabel",
    "http://www.xbrl.org/2003/role/periodStartLabel",
    "http://www.xbrl.org/2003/role/periodEndLabel",
})

# Namespace URI prefixes that identify standard (non-filer-extension) taxonomies.
# Concepts from these namespaces get cik_override = NULL — one row covers all filers.
STANDARD_NS_PREFIXES: tuple = (
    "http://fasb.org/us-gaap/",
    "http://fasb.org/srt/",
    "http://xbrl.sec.gov/",        # dei:, country:, exch:, stpr:, naics:, sic:
    "http://www.xbrl.org/",        # XBRL core
    "http://xbrl.ifrs.org/taxonomy/",
    "https://xbrl.ifrs.org/taxonomy/",
)


def _is_standard_ns(namespace_uri: str) -> bool:
    return any(namespace_uri.startswith(p) for p in STANDARD_NS_PREFIXES)


# ---------------------------------------------------------------------------
# Core export function (called from the CLI.Xbrl.Loaded hook)
# ---------------------------------------------------------------------------

def _export(modelXbrl, output_path: str, cik: str | None) -> None:
    """Walk conceptLabel arcs and write rows to *output_path* as JSON."""
    rows = []
    error_path = output_path + ".error"

    try:
        rel_set = modelXbrl.relationshipSet(XbrlConst.conceptLabel)
        for rel in rel_set.modelRelationships:
            concept = rel.fromModelObject
            lbl     = rel.toModelObject

            # Guard: concept must have a QName
            if not hasattr(concept, "qname") or concept.qname is None:
                continue
            ns_uri = str(concept.qname.namespaceURI or "")
            local  = str(concept.qname.localName  or "")
            if not ns_uri or not local:
                continue

            # Guard: label must be English
            lang = str(getattr(lbl, "xmlLang", "") or "")
            if not lang.lower().startswith("en"):
                continue

            # Guard: label role must be one we track
            role = str(getattr(lbl, "role", "") or "")
            if role not in LABEL_ROLES:
                continue

            # Guard: label text must be non-empty
            label_text = str(getattr(lbl, "stringValue", "") or "").strip()
            if not label_text:
                continue

            rows.append({
                "concept":      f"{ns_uri}:{local}",
                "cik_override": None if _is_standard_ns(ns_uri) else cik,
                "xml_lang":     lang,
                "label_role":   role,
                "label":        label_text,
            })

    except Exception:
        # Write error trace alongside the output file for diagnosis
        with open(error_path, "w", encoding="utf-8") as ef:
            ef.write(traceback.format_exc())
        modelXbrl.error(
            "XbrlRag.conceptLabels",
            f"export_concept_labels: exception during arc walk; "
            f"trace written to {error_path}",
        )
        return

    payload = {
        "count":       len(rows),
        "cik":         cik,
        "source_file": str(modelXbrl.modelDocument.uri if modelXbrl.modelDocument else ""),
        "rows":        rows,
    }

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        with open(error_path, "w", encoding="utf-8") as ef:
            ef.write(traceback.format_exc())
        modelXbrl.error(
            "XbrlRag.conceptLabels",
            f"export_concept_labels: failed to write output file {output_path}; "
            f"trace written to {error_path}",
        )
        return

    modelXbrl.info(
        "XbrlRag.conceptLabels",
        f"export_concept_labels: wrote {len(rows)} label rows to {output_path}",
    )


# ---------------------------------------------------------------------------
# Dimension member export
# ---------------------------------------------------------------------------

def _build_label_map(modelXbrl) -> dict:
    """Build {namespaceURI:localName -> standard English label} from conceptLabel arcs.

    Used by both concept-labels export and dimension-members export so the arc
    walk happens at most once per Arelle invocation when both options are set.
    Cached on modelXbrl._arRagLabelMap to avoid double traversal.
    """
    if hasattr(modelXbrl, "_arRagLabelMap"):
        return modelXbrl._arRagLabelMap

    label_map = {}
    try:
        rel_set = modelXbrl.relationshipSet(XbrlConst.conceptLabel)
        for rel in rel_set.modelRelationships:
            concept = rel.fromModelObject
            lbl     = rel.toModelObject
            if not hasattr(concept, "qname") or concept.qname is None:
                continue
            ns_uri = str(concept.qname.namespaceURI or "")
            local  = str(concept.qname.localName  or "")
            if not ns_uri or not local:
                continue
            lang = str(getattr(lbl, "xmlLang", "") or "")
            if not lang.lower().startswith("en"):
                continue
            role = str(getattr(lbl, "role", "") or "")
            if role not in LABEL_ROLES:
                continue
            label_text = str(getattr(lbl, "stringValue", "") or "").strip()
            if not label_text:
                continue
            key = f"{ns_uri}:{local}"
            # Prefer the standard label role; only store first hit per key+role
            if key not in label_map or role == "http://www.xbrl.org/2003/role/label":
                label_map[key] = label_text
    except Exception:
        pass  # partial map is still useful

    modelXbrl._arRagLabelMap = label_map
    return label_map


def _export_dimension_members(modelXbrl, output_path: str) -> None:
    """Walk XBRL contexts, extract explicit dimension axis+member pairs, resolve labels.

    For each unique (axis_qname, member_qname) pair found in any context:
    - axis_qname and member_qname use the full namespaceURI:localName format,
      matching what dimension_catalog.axis_qname / member_qname stores.
    - axis_label and member_label come from the in-memory conceptLabel arc set.

    Only EXPLICIT dimensions are included (member is a concept, not a typed value).
    Typed dimensions have no member concept and are skipped.

    Output JSON schema:
        {
          "count": <int>,
          "source_file": "<entry_url>",
          "rows": [
            {
              "axis_qname":   "http://fasb.org/srt/2024:ProductOrServiceAxis",
              "member_qname": "http://www.nvidia.com/20260125:DataCenterMember",
              "axis_label":   "Product or Service [Axis]",
              "member_label": "Data Center"
            },
            ...
          ]
        }
    """
    error_path = output_path + ".error"

    try:
        label_map = _build_label_map(modelXbrl)

        seen: set = set()
        rows = []

        for ctx in modelXbrl.contexts.values():
            # qnameDims: {dim_qname -> ModelDimensionValue}
            q_dims = getattr(ctx, "qnameDims", None)
            if not q_dims:
                continue

            for dim_qname, dim_value in q_dims.items():
                # Skip typed dimensions — they have no member concept
                is_explicit = getattr(dim_value, "isExplicit", False)
                if not is_explicit:
                    continue

                member_qname_obj = getattr(dim_value, "memberQname", None)
                if member_qname_obj is None:
                    continue

                axis_ns    = str(dim_qname.namespaceURI or "")
                axis_local = str(dim_qname.localName    or "")
                mem_ns     = str(member_qname_obj.namespaceURI or "")
                mem_local  = str(member_qname_obj.localName    or "")

                if not (axis_ns and axis_local and mem_ns and mem_local):
                    continue

                axis_key   = f"{axis_ns}:{axis_local}"
                member_key = f"{mem_ns}:{mem_local}"
                pair_key   = (axis_key, member_key)

                if pair_key in seen:
                    continue
                seen.add(pair_key)

                rows.append({
                    "axis_qname":   axis_key,
                    "member_qname": member_key,
                    "axis_label":   label_map.get(axis_key),
                    "member_label": label_map.get(member_key),
                })

    except Exception:
        with open(error_path, "w", encoding="utf-8") as ef:
            ef.write(traceback.format_exc())
        modelXbrl.error(
            "XbrlRag.dimensionMembers",
            f"export_dimension_members: exception; trace written to {error_path}",
        )
        return

    payload = {
        "count":       len(rows),
        "source_file": str(modelXbrl.modelDocument.uri if modelXbrl.modelDocument else ""),
        "rows":        rows,
    }

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        with open(error_path, "w", encoding="utf-8") as ef:
            ef.write(traceback.format_exc())
        modelXbrl.error(
            "XbrlRag.dimensionMembers",
            f"export_dimension_members: failed to write {output_path}; trace in {error_path}",
        )
        return

    modelXbrl.info(
        "XbrlRag.dimensionMembers",
        f"export_dimension_members: wrote {len(rows)} axis+member pairs to {output_path}",
    )


# ---------------------------------------------------------------------------
# Arelle plugin hooks
# ---------------------------------------------------------------------------

def cmdLineOptionExtender(parser, *args, **kwargs):
    """Add XbrlRag export options to arelleCmdLine."""
    parser.add_option(
        "--concept-labels-output",
        action="store",
        dest="conceptLabelsOutput",
        default=None,
        help=(
            "XbrlRag: path for concept-labels JSON output file. "
            "When set, exports all conceptLabel arcs after XBRL load."
        ),
    )
    parser.add_option(
        "--concept-labels-cik",
        action="store",
        dest="conceptLabelsCik",
        default=None,
        help=(
            "XbrlRag: 10-digit CIK applied as cik_override on filer-extension "
            "concept labels (non-standard namespaces)."
        ),
    )
    parser.add_option(
        "--dimension-members-output",
        action="store",
        dest="dimensionMembersOutput",
        default=None,
        help=(
            "XbrlRag: path for dimension-members JSON output file. "
            "When set, exports all explicit axis+member pairs with their English "
            "labels after XBRL load. Used to patch dimension_catalog.member_label."
        ),
    )


def cmdLineXbrlLoaded(cntlr, options, modelXbrl, *args, **kwargs):
    """Hook: called by arelleCmdLine after the XBRL instance is loaded.
    Both exports run in the same invocation if both output paths are set,
    sharing the label_map traversal via _build_label_map caching.
    """
    concept_labels_path   = getattr(options, "conceptLabelsOutput",   None)
    dimension_members_path = getattr(options, "dimensionMembersOutput", None)

    if concept_labels_path:
        cik = getattr(options, "conceptLabelsCik", None)
        _export(modelXbrl, concept_labels_path, cik)

    if dimension_members_path:
        _export_dimension_members(modelXbrl, dimension_members_path)


# ---------------------------------------------------------------------------
# Plugin registration (Arelle reads __pluginInfo__ to discover hooks)
# ---------------------------------------------------------------------------

__pluginInfo__ = {
    "name":        "XbrlRag Export Concept Labels",
    "version":     "1.0",
    "description": (
        "Exports XBRL concept-to-label arcs and dimension axis+member pairs "
        "to JSON files for loading into XbrlRag Layer-0 tables "
        "(concept_labels and dimension_catalog.member_label)."
    ),
    "author":      "XbrlRag",
    "license":     "Apache-2",
    # Hook registrations
    "CntlrCmdLine.Options":      cmdLineOptionExtender,
    "CntlrCmdLine.Xbrl.Loaded": cmdLineXbrlLoaded,
}
