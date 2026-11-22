"""The extension classifier's input and output for the reload (extension-classification R7).

Spec: docs/specs/extension-classification.md R7 (as amended 2026-11-11). The classifier
(`src/ingest/sec_edgar/xbrl/extension_classifier.py`) is pure; this module is the pure half
of its wiring, and ``Layer0Populator.populate_extension_classifications`` the database half.

The input never reads ``mv_financial_facts`` (whose family join reads ``concept_mapping``,
which this step writes), so there is no cycle and one MV build follows it:

- **lines and values** come from the cached SEC Financial Statement Data Sets (``pre``:
  IS/BS/CF lines with ``inpth`` 0; ``num``: rows with no ``segments`` and no ``coreg``),
  the shape the spec's survey and fixtures used. A filing not yet in a cached FSDS quarter
  gives no input; the hand list still covers it.
- **arcs** come from the raw calculation tables, with the SELECT of
  ``scripts/ddl/mv/12_mv_calculation_network.sql``.
- **members** and the hand list come from the committed mapping
  (``committed_mapping_rows()``): standard rows without the dimension-sliced
  ProductRevenue/ServiceRevenue, and the ``ext:`` rows of ``filers:``.

Rows are ranked against the filer's ranking as stored (its own rows over the generic ones):
a primary after every non-component rank it holds (IFRS rows included) and after the
last rank the facts-count pass can give a primary, so a later facts-count pass never ties
with it; a substitute after the last component-band row. See ``classifier_mapping_rows``.
"""

from __future__ import annotations

import collections.abc
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

EXTENSION_CLASSIFIER_SOURCE = "extension_classifier"

_STATEMENTS = frozenset({"IS", "BS", "CF"})
_SLICED_FAMILIES = frozenset({"ProductRevenue", "ServiceRevenue"})
_MEMBER_PREFIXES = ("us-gaap:", "srt:", "dei:", "ifrs-full:")
_EXT_PREFIX = "ext:"
# FSDS ``version`` taxonomy -> the classifier's prefix form.
_FSDS_PREFIX = {"us-gaap": "us-gaap", "ifrs": "ifrs-full", "srt": "srt", "dei": "dei"}
# Rank bands of concept_mapping (docs/specs/concept-rank-integrity.md).
_COMPONENT_BASE = 50
_IFRS_BASE = 100

# The arcs of mv_calculation_network (12_), read from the raw tables for some accessions.
CALCULATION_ARCS_SQL = """
SELECT DISTINCT
    a.filing_accession_number                                   AS accession_no,
    uri_role.uri                                                AS link_role_uri,
    parent_q.namespace || ':' || parent_q.local_name            AS parent_concept,
    child_q.namespace || ':' || child_q.local_name              AS child_concept,
    rel.calculation_weight::numeric                             AS weight
FROM network n
JOIN accession a       ON a.accession_id = n.accession_id
JOIN uri uri_arcrole   ON uri_arcrole.uri_id = n.arcrole_uri_id
JOIN uri uri_role      ON uri_role.uri_id    = n.extended_link_role_uri_id
JOIN relationship rel  ON rel.network_id = n.network_id
JOIN element parent_el ON parent_el.element_id = rel.from_element_id
JOIN qname   parent_q  ON parent_q.qname_id    = parent_el.qname_id
JOIN element child_el  ON child_el.element_id  = rel.to_element_id
JOIN qname   child_q   ON child_q.qname_id     = child_el.qname_id
WHERE uri_arcrole.uri IN ('http://www.xbrl.org/2003/arcrole/summation-item',
                          'https://xbrl.org/2023/arcrole/summation-item')
  AND a.filing_accession_number = ANY(%s)
"""


def fsds_concept(tag: str, version: str, adsh: str) -> str:
    """A FSDS tag as a classifier concept (spec R1 forms).

    Args:
        tag: The FSDS ``tag``.
        version: Its ``version``: the filing's ``adsh`` for a filer extension, else
            ``<taxonomy>/<year>``.
        adsh: The filing's accession number.

    Returns:
        ``urn:fsds:<adsh>:<tag>`` for an extension (its namespace never matters, R1),
        ``<prefix>:<tag>`` for us-gaap, ifrs, srt and dei, and
        ``http://xbrl.sec.gov/<version>:<tag>`` (standard, and no family's member) for
        any other taxonomy.
    """
    if version == adsh:
        return f"urn:fsds:{adsh}:{tag}"
    taxonomy = version.split("/", 1)[0]
    prefix = _FSDS_PREFIX.get(taxonomy)
    if prefix is None:
        return f"http://xbrl.sec.gov/{version}:{tag}"
    return f"{prefix}:{tag}"


def classifier_filings(
    pre: Iterable[Mapping[str, str]],
    num: Iterable[Mapping[str, str]],
    arcs: Iterable[Mapping[str, Any]],
    ciks: Mapping[str, str],
) -> list[dict[str, Any]]:
    """The classifier's filings (spec R2) from FSDS rows and calculation arcs.

    Args:
        pre: FSDS ``pre`` rows (raw strings).
        num: FSDS ``num`` rows (raw strings).
        arcs: Calculation arcs, each ``{accession_no, link_role_uri, parent_concept,
            child_concept, weight}`` (``CALCULATION_ARCS_SQL``).
        ciks: Accession number -> 10-digit CIK of the filings to build.

    Returns:
        One filing per accession of ``ciks`` with at least one IS/BS/CF line, sorted by
        accession: ``lines`` (non-parenthetical IS/BS/CF ``pre`` rows), ``values``
        (consolidated ``num`` rows: no segments, no co-registrant; an empty value is
        nil), ``arcs``.
    """
    lines: dict[str, list[dict[str, Any]]] = {}
    for r in pre:
        adsh = r["adsh"]
        if adsh not in ciks or r["stmt"] not in _STATEMENTS or r["inpth"] != "0":
            continue
        lines.setdefault(adsh, []).append({
            "stmt": r["stmt"],
            "report": int(r["report"]),
            "line": int(r["line"]),
            "concept": fsds_concept(r["tag"], r["version"], adsh),
            "plabel": r["plabel"],
        })
    values: dict[str, list[dict[str, Any]]] = {}
    for r in num:
        adsh = r["adsh"]
        if adsh not in lines or r["segments"] or r["coreg"]:
            continue
        values.setdefault(adsh, []).append({
            "concept": fsds_concept(r["tag"], r["version"], adsh),
            "period": f"{r['ddate']}/{r['qtrs']}",
            "unit": r["uom"],
            "value": Decimal(r["value"]) if r["value"] != "" else None,
        })
    by_accession: dict[str, list[dict[str, Any]]] = {}
    for a in arcs:
        if a["accession_no"] in lines:
            by_accession.setdefault(a["accession_no"], []).append({
                "link_role": a["link_role_uri"],
                "parent": a["parent_concept"],
                "child": a["child_concept"],
                "weight": Decimal(a["weight"]),
            })
    return [
        {
            "accession_no": adsh,
            "cik": ciks[adsh],
            "lines": lines[adsh],
            "arcs": by_accession.get(adsh, []),
            "values": values.get(adsh, []),
        }
        for adsh in sorted(lines)
    ]


def classifier_members(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The classifier's members (spec R2) from committed mapping rows.

    Args:
        rows: ``committed_mapping_rows()`` (``concept_family``, ``qname``,
            ``cik_override`` ("" for generic), ``concept_role``, ``substitution_code``).

    Returns:
        ``{family, qname, role, substitution_code, cik}`` for each row of a prefix-form
        standard QName outside ProductRevenue/ServiceRevenue; ``cik`` and
        ``substitution_code`` None when empty.
    """
    return [
        {
            "family": r["concept_family"],
            "qname": r["qname"],
            "role": r["concept_role"],
            "substitution_code": r.get("substitution_code") or None,
            "cik": r.get("cik_override") or None,
        }
        for r in rows
        if r["qname"].startswith(_MEMBER_PREFIXES)
        and r["concept_family"] not in _SLICED_FAMILIES
    ]


def listed_extensions(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The hand-listed extensions (spec R6 ``listed``) from committed mapping rows.

    Args:
        rows: ``committed_mapping_rows()``.

    Returns:
        ``{cik, family, qname, role}`` of every cik-scoped ``ext:`` row.
    """
    return [
        {
            "cik": r["cik_override"],
            "family": r["concept_family"],
            "qname": r["qname"],
            "role": r["concept_role"],
        }
        for r in rows
        if r["qname"].startswith(_EXT_PREFIX) and r.get("cik_override")
    ]


def classifier_mapping_rows(
    rows: Iterable[Mapping[str, Any]],
    stored: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank ``extension_mapping_rows(...)["rows"]`` into concept_mapping rows.

    A primary ranks after every non-component rank of the filer's ranking (its IFRS
    rows from 100 included), so a classified extension never outranks a standard
    member or a hand-listed one (TSMC's listed interest-income extension sits after
    ``ifrs-full:RevenueFromInterest``), and after the number of generic non-component
    rows of the family, the highest rank the facts-count pass gives a primary
    (docs/specs/concept-rank-integrity.md), so re-running that pass never ties with
    it. A substitute ranks after the filer's last component-band row, as a listed
    substitute does. Several rows of one (family, cik) rank in ``qname`` order.

    Args:
        rows: The classifier's mapping rows (``cik``, ``family``, ``qname``, ``role``,
            ``substitution_code``).
        stored: The base concept_mapping rows as stored (no ``ns_expanded`` and no
            ``extension_classifier`` rows): ``concept_family``, ``qname``,
            ``preference_rank``, ``cik_override`` (None or "" for generic),
            ``substitution_code``, ``substitution_note``.

    Returns:
        concept_mapping rows with source ``extension_classifier``, sorted by
        (``cik_override``, ``concept_family``, ``qname``).

    Raises:
        ValueError: A rank would leave its band, or a substitute's code has no note in
            ``stored``.
    """
    stored = list(stored)
    notes: dict[str, str] = {}
    for r in stored:
        if r.get("substitution_code") and r.get("substitution_note"):
            notes.setdefault(r["substitution_code"], r["substitution_note"])
    out: list[dict[str, Any]] = []
    used: dict[tuple[str, str], set[int]] = {}
    for row in sorted(rows, key=lambda r: (r["cik"], r["family"], r["qname"])):
        cik, family = row["cik"], row["family"]
        if (family, cik) not in used:
            used[(family, cik)] = set(_filer_ranking(stored, family, cik).values())
        ranks = used[(family, cik)]
        if row["role"] == "substitute":
            band = [r for r in ranks if _COMPONENT_BASE <= r < _IFRS_BASE]
            rank = max(band, default=_COMPONENT_BASE - 1) + 1
            if rank >= _IFRS_BASE:
                raise ValueError(f"{cik} {family} {row['qname']}: component band is full")
            code = row["substitution_code"]
            if code not in notes:
                raise ValueError(f"{cik} {family} {row['qname']}: no note for {code!r}")
            note = notes[code]
        else:
            generic = sum(
                1 for r in stored
                if r["concept_family"] == family and not r.get("cik_override")
                and not _COMPONENT_BASE <= r["preference_rank"] < _IFRS_BASE
            )
            last = max(
                (r for r in ranks if not _COMPONENT_BASE <= r < _IFRS_BASE), default=0
            )
            rank = max(last, generic) + 1
            if _COMPONENT_BASE <= rank < _IFRS_BASE:
                raise ValueError(
                    f"{cik} {family} {row['qname']}: rank {rank} leaves the primary band"
                )
            code, note = None, None
        ranks.add(rank)
        out.append({
            "concept_family": family,
            "qname": row["qname"],
            "preference_rank": rank,
            "cik_override": cik,
            "is_deprecated": False,
            "source": EXTENSION_CLASSIFIER_SOURCE,
            "concept_role": row["role"],
            "substitution_code": code,
            "substitution_note": note,
        })
    return sorted(out, key=lambda r: (r["cik_override"], r["concept_family"], r["qname"]))


def _filer_ranking(
    stored: collections.abc.Sequence[Mapping[str, Any]], family: str, cik: str
) -> dict[str, int]:
    """A filer's ranking of a family as stored: its own rows over the generic ones."""
    ranking: dict[str, int] = {}
    for r in stored:
        if r["concept_family"] == family and not r.get("cik_override"):
            ranking.setdefault(r["qname"], r["preference_rank"])
    for r in stored:
        if r["concept_family"] == family and r.get("cik_override") == cik:
            ranking[r["qname"]] = r["preference_rank"]
    return ranking
