"""Classify a filer's extension elements by calculation parent and statement position.

Spec: docs/specs/extension-classification.md (nuance N2 of
docs/design/XbrlRag_Test_Strategy.md). SEC companyfacts omits extension facts, so the
filer's own linkbases and statements are the only evidence of what an extension measures.
`classify_extensions` is pure: it reads a filer's statement lines, calculation arcs and
consolidated values, and names a family only when an identity of the statements or an
equal-valued standard line with the same label says so; anything else stays unclassified
with a reason. `extension_mapping_rows` turns the results into cik-scoped mapping rows next
to the hand-listed `filers:` entries of concept_family_coverage.yaml, which always win.

Concepts are compared by a *key*: a standard concept by its short (prefix) form, e.g.
``us-gaap:NetIncomeLoss`` whatever the taxonomy year; an extension by ``("ext", local
name)`` only, since filers change their namespace URI every year. Every evidence rule runs
within one filer (cik) only.
"""

from __future__ import annotations

import functools
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

_STATEMENTS = ("IS", "BS", "CF")
_PREFIXES = ("us-gaap", "srt", "dei", "ifrs-full")
_STANDARD_NAMESPACES = (
    "http://fasb.org/",
    "http://xbrl.sec.gov/",
    "http://www.xbrl.org/",
    "http://xbrl.ifrs.org/",
    "https://xbrl.ifrs.org/",
)
_EVIDENCE_ROLES = ("primary", "substitute")
_RULE_TERM = "identity_term"
_RULE_TOTAL = "identity_total"
_RULE_LABEL = "label_value"
_LABEL_DROP = re.compile(r"[^a-z0-9]")

# A concept key: ("std", "<prefix>:<Local>") or ("ext", "<Local>").
_Key = tuple[str, str]
# A piece of evidence: (family, role, substitution_code).
_Claim = tuple[str, str, str | None]
# (family, short-form qname) -> the member rows in force for one filer.
_Members = dict[tuple[str, str], list[Mapping[str, Any]]]
# One calculation tree: (accession, parent key, {child key: weight}).
_Tree = tuple[str, _Key, dict[_Key, Decimal]]


def default_identities() -> tuple[dict[str, Any], ...]:
    """The statement identities of spec R3, in order.

    Returns:
        Each ``{"id", "statement", "total", "terms"}``; a term is
        ``{"family", "weight" (Decimal), "required" (bool)}``.
    """

    def term(family: str, weight: int, required: bool) -> dict[str, Any]:
        return {"family": family, "weight": Decimal(weight), "required": required}

    return (
        {"id": "net_income", "statement": "IS", "total": "NetIncome",
         "terms": (term("PretaxIncome", 1, True), term("TaxExpense", -1, True))},
        {"id": "pretax_income", "statement": "IS", "total": "PretaxIncome",
         "terms": (term("OperatingIncome", 1, True), term("OtherNonOperatingIncome", 1, True),
                   term("InterestIncome", 1, False), term("InterestExpense", -1, False))},
        {"id": "gross_profit", "statement": "IS", "total": "GrossProfit",
         "terms": (term("Revenue", 1, True), term("CostOfRevenue", -1, True))},
        {"id": "operating_income", "statement": "IS", "total": "OperatingIncome",
         "terms": (term("GrossProfit", 1, True), term("OperatingExpenses", -1, True))},
    )


def _is_standard(concept: str) -> bool:
    """R1: a prefix form or a standard taxonomy namespace."""
    prefix = concept.rpartition(":")[0]
    return prefix in _PREFIXES or concept.startswith(_STANDARD_NAMESPACES)


@functools.lru_cache(maxsize=65536)
def _key(concept: str) -> _Key:
    """The comparison key of a concept (R1 short form, or the extension's local name)."""
    namespace, _, local = concept.rpartition(":")
    if not _is_standard(concept):
        return ("ext", local)
    if namespace in _PREFIXES:
        return ("std", concept)
    for base in ("http://fasb.org/", "http://xbrl.sec.gov/"):
        if namespace.startswith(base):
            name = namespace[len(base):].split("/", 1)[0]
            return ("std", f"{name}:{local}")
    if namespace.rstrip("/").endswith("/ifrs-full"):
        return ("std", f"ifrs-full:{local}")
    return ("std", concept)


@functools.lru_cache(maxsize=65536)
def _normalized_label(label: str) -> str:
    return _LABEL_DROP.sub("", label.casefold())


class _Filer:
    """Everything one filer's filings say, indexed by concept key."""

    def __init__(self, cik: str, filings: list[Mapping[str, Any]],
                 members: list[Mapping[str, Any]]) -> None:
        self.cik = cik
        # (accession, key) -> list of (stmt, report, line, normalized label)
        self.lines: dict[tuple[str, _Key], list[tuple[str, int, int, str]]] = defaultdict(list)
        # accession -> (link_role, parent key) -> {child key: weight}
        self.trees: dict[str, dict[tuple[str, _Key], dict[_Key, Decimal]]] = {}
        # key -> {(period, unit, value)} of non-zero values
        self.values: dict[_Key, set[tuple[Any, Any, Decimal]]] = defaultdict(set)
        self.accessions = sorted({str(f["accession_no"]) for f in filings})
        for f in filings:
            acc = str(f["accession_no"])
            for ln in f["lines"]:
                self.lines[(acc, _key(ln["concept"]))].append(
                    (ln["stmt"], int(ln["report"]), int(ln["line"]),
                     _normalized_label(ln["plabel"])))
            arcs: dict[tuple[str, _Key], dict[_Key, set[Decimal]]] = defaultdict(
                lambda: defaultdict(set))
            for a in f["arcs"]:
                arcs[(a["link_role"], _key(a["parent"]))][_key(a["child"])].add(
                    Decimal(a["weight"]))
            self.trees[acc] = {
                tree: {child: min(ws) for child, ws in kids.items()}
                for tree, kids in arcs.items()
            }
            for v in f["values"]:
                if v["value"] is not None and v["value"] != 0:
                    self.values[_key(v["concept"])].add(
                        (v["period"], v["unit"], Decimal(v["value"])))
        # key -> the trees (accession, parent, children) that hold it as a child / parent
        self.as_child: dict[_Key, list[_Tree]] = defaultdict(list)
        self.as_parent: dict[_Key, list[_Tree]] = defaultdict(list)
        for acc in self.accessions:
            for (_, parent), kids in self.trees[acc].items():
                self.as_parent[parent].append((acc, parent, kids))
                for child in kids:
                    self.as_child[child].append((acc, parent, kids))
        # (stmt, normalized label) -> standard key -> accessions presenting it so
        self.std_labels: dict[tuple[str, str], dict[_Key, set[str]]] = defaultdict(
            lambda: defaultdict(set))
        for (acc, key), lines in self.lines.items():
            if key[0] == "std":
                for stmt, _, _, label in lines:
                    self.std_labels[(stmt, label)][key].add(acc)
        self.members = self._effective_members(members)
        self.primary: dict[_Key, set[str]] = defaultdict(set)
        self.member_rows: dict[_Key, list[Mapping[str, Any]]] = defaultdict(list)
        for (family, qname), rows in self.members.items():
            self.member_rows[("std", qname)].extend(rows)
            if any(r["role"] == "primary" for r in rows):
                self.primary[("std", qname)].add(family)

    def _effective_members(self, members: list[Mapping[str, Any]]) -> _Members:
        """R2: the filer's scoped row of a (family, qname) replaces the generic one."""
        generic: _Members = defaultdict(list)
        scoped: _Members = defaultdict(list)
        for m in members:
            pair = (str(m["family"]), _key(str(m["qname"]))[1])
            if m["cik"] is None:
                generic[pair].append(m)
            elif m["cik"] == self.cik:
                scoped[pair].append(m)
        return {**generic, **scoped}

    def p(self, key: _Key) -> set[str]:
        """P(c): the families of which a standard concept is a primary member."""
        if key[0] == "ext":
            return set()
        return self.primary.get(key, set())

    def rows_of(self, key: _Key) -> list[Mapping[str, Any]]:
        """The member rows in force for this filer of a standard concept."""
        return self.member_rows.get(key, [])

    def presented(self, acc: str, key: _Key, stmt: str | None = None) -> list[tuple]:
        """The concept's statement lines in one filing, optionally on one statement."""
        return [ln for ln in self.lines.get((acc, key), []) if stmt is None or ln[0] == stmt]


def _match(filer: _Filer, pairs: Iterable[tuple[_Key, Decimal]],
           terms: list[Mapping[str, Any]]) -> set[int] | None:
    """R3 match: the indexes of the terms assigned, or None when the match fails."""
    assigned: set[int] = set()
    for key, weight in pairs:
        fits = [i for i, t in enumerate(terms)
                if t["family"] in filer.p(key) and Decimal(t["weight"]) == weight]
        if len(fits) != 1 or fits[0] in assigned:
            return None
        assigned.add(fits[0])
    return assigned


def _below(e_lines: list[tuple], other_lines: list[tuple]) -> bool:
    """True when E's line is lower than the other's wherever both share a report."""
    return all(e[2] < o[2] for e in e_lines for o in other_lines if e[1] == o[1])


def _identity_evidence(filer: _Filer, e: _Key,
                       identities: list[Mapping[str, Any]]) -> list[tuple[_Claim, str, str]]:
    """R4 rules 1 and 2: (claim, rule, accession) from the filer's trees."""
    out: list[tuple[_Claim, str, str]] = []
    # Rule 1: E is a term of a standard total whose other children are all standard.
    term_trees = [(acc, parent, kids) for acc, parent, kids in filer.as_child.get(e, [])
                  if parent[0] == "std" and all(k == e or k[0] == "std" for k in kids)]
    # Rule 2: E is the total of standard children.
    total_trees = [(acc, parent, kids) for acc, parent, kids in filer.as_parent.get(e, [])
                   if kids and all(k[0] == "std" for k in kids)]
    for ident in identities:
        terms = list(ident["terms"])
        stmt = ident["statement"]
        for acc, parent, kids in term_trees:
            e_lines = filer.presented(acc, e, stmt)
            if not e_lines or ident["total"] not in filer.p(parent):
                continue
            assigned = _match(filer, [(k, w) for k, w in kids.items() if k != e], terms)
            if assigned is None:
                continue
            open_required = [i for i, t in enumerate(terms)
                             if t["required"] and i not in assigned]
            if (len(open_required) == 1
                    and Decimal(terms[open_required[0]]["weight"]) == kids[e]
                    and _below(e_lines, filer.presented(acc, parent))):
                out.append(((terms[open_required[0]]["family"], "primary", None),
                            _RULE_TERM, acc))
        for acc, _, kids in total_trees:
            e_lines = filer.presented(acc, e, stmt)
            if not e_lines:
                continue
            assigned = _match(filer, kids.items(), terms)
            if (assigned is not None
                    and all(i in assigned for i, t in enumerate(terms) if t["required"])
                    and all(_below(filer.presented(acc, k), e_lines) for k in kids)):
                out.append(((ident["total"], "primary", None), _RULE_TOTAL, acc))
    return out


def _label_evidence(filer: _Filer, e: _Key) -> tuple[list[tuple[_Claim, str, str]], bool]:
    """R4 rule 3: (claim, rule, accession) evidence, and whether E is label only."""
    out: list[tuple[_Claim, str, str]] = []
    label_only = False
    e_values = filer.values.get(e, set())
    for acc in filer.accessions:
        for stmt, _, _, label in filer.presented(acc, e):
            for s, bs in filer.std_labels.get((stmt, label), {}).items():
                if not e_values & filer.values.get(s, set()):
                    label_only = True
                    continue
                for row in filer.rows_of(s):
                    if row["role"] in _EVIDENCE_ROLES:
                        claim = (str(row["family"]), str(row["role"]), row["substitution_code"])
                        out.extend((claim, _RULE_LABEL, x) for x in {acc} | bs)
    return out, label_only


def _guards(filer: _Filer, e: _Key) -> tuple[set[str], set[str]]:
    """R5 component_of(E) and contains(E) over every tree of the filer."""
    component_of: set[str] = set()
    contains: set[str] = set()
    for _, parent, _ in filer.as_child.get(e, []):
        if parent[0] == "std":
            component_of |= filer.p(parent)
    for _, _, kids in filer.as_parent.get(e, []):
        for k in kids:
            contains |= filer.p(k)
    return component_of, contains


def _classify_one(filer: _Filer, e: _Key, accessions: list[str],
                  identities: list[Mapping[str, Any]]) -> dict[str, Any]:
    evidence = _identity_evidence(filer, e, identities)
    label, label_only = _label_evidence(filer, e)
    evidence += label
    component_of, contains = _guards(filer, e)
    claims = {c for c, _, _ in evidence}
    result: dict[str, Any] = {
        "cik": filer.cik, "local_name": e[1], "family": None, "role": None,
        "substitution_code": None, "rules": (), "evidence": (),
        "component_of": tuple(sorted(component_of)), "contains": tuple(sorted(contains)),
        "reason": None, "accessions": tuple(sorted(accessions)),
    }
    if any(c[0] in component_of | contains for c in claims):
        result["reason"] = "conflict"
    elif len(claims) > 1:
        result["reason"] = "ambiguous"
    elif claims:
        (claim,) = claims
        result.update(
            family=claim[0], role=claim[1], substitution_code=claim[2],
            rules=tuple(sorted({r for _, r, _ in evidence})),
            evidence=tuple(sorted({a for _, _, a in evidence})))
    else:
        result["reason"] = "label_only" if label_only else "no_evidence"
    return result


def classify_extensions(
    filings: Iterable[Mapping[str, Any]],
    members: Iterable[Mapping[str, Any]],
    identities: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Classifies every extension presented on a filer's IS, BS or CF (spec R1-R5).

    Args:
        filings: Each ``{accession_no, cik, lines, arcs, values}`` as spec R2 defines them.
        members: Standard family memberships ``{family, qname, role, substitution_code,
            cik}``.
        identities: The statement identities; None means ``default_identities()``.

    Returns:
        One result per (cik, local name), sorted by both.

    Raises:
        ValueError: A members row names an extension (``ext:``) QName.
    """
    member_rows = list(members)
    for m in member_rows:
        if not _is_standard(str(m["qname"])):
            raise ValueError(f"members row names a non-standard concept: {m['qname']!r}")
    idents = list(default_identities() if identities is None else identities)
    by_cik: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for f in filings:
        by_cik[str(f["cik"])].append(f)
    results: list[dict[str, Any]] = []
    for cik, cik_filings in by_cik.items():
        filer = _Filer(cik, cik_filings, member_rows)
        subjects: dict[_Key, set[str]] = defaultdict(set)
        for (acc, key), lines in filer.lines.items():
            if key[0] == "ext" and any(ln[0] in _STATEMENTS for ln in lines):
                subjects[key].add(acc)
        for e, accs in subjects.items():
            results.append(_classify_one(filer, e, sorted(accs), idents))
    results.sort(key=lambda r: (r["cik"], r["local_name"]))
    return results


def extension_mapping_rows(
    results: Iterable[Mapping[str, Any]],
    listed: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Mapping rows for the classified extensions that the hand list does not cover (R6).

    Args:
        results: ``classify_extensions`` results.
        listed: Hand-listed rows ``{cik, family, qname: "ext:<LocalName>", role}``.

    Returns:
        ``{"rows", "agreements", "disagreements", "unclassified"}``.
    """
    listed_by: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in listed:
        local = str(row["qname"]).rpartition(":")[2]
        listed_by[(str(row["cik"]), local)].add((str(row["family"]), str(row["role"])))
    rows: list[dict[str, Any]] = []
    agreements: list[tuple[str, str]] = []
    disagreements: list[tuple[str, str]] = []
    unclassified: list[tuple[str, str, str]] = []
    for r in results:
        key = (str(r["cik"]), str(r["local_name"]))
        if r["family"] is None:
            unclassified.append((*key, str(r["reason"])))
        elif key not in listed_by:
            rows.append({"cik": key[0], "family": r["family"], "qname": f"ext:{key[1]}",
                         "role": r["role"], "substitution_code": r["substitution_code"]})
        elif (r["family"], r["role"]) in listed_by[key]:
            agreements.append(key)
        else:
            disagreements.append(key)
    rows.sort(key=lambda x: (x["cik"], x["qname"], x["family"]))
    return {
        "rows": rows,
        "agreements": tuple(sorted(agreements)),
        "disagreements": tuple(sorted(disagreements)),
        "unclassified": tuple(sorted(unclassified)),
    }
