"""Concept families and sub-families for browsing, from the committed concept mapping.

The rows are ``committed_mapping_rows()`` (the declared families, the reviewed per-filer
overrides and the coverage file), and a fact's family is decided by the view's own rule,
``fact_family``, so the tool groups a raw fact exactly as ``mv_financial_facts`` would. The
grouping into statements and the parent/sub-family tree below are for browsing only; they
change nothing in the mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from src.ingest.util.generate_concept_mapping import committed_mapping_rows, fact_family

STANDARD_PREFIXES = frozenset({"us-gaap", "ifrs-full", "dei", "srt"})

# Browsing tree: statement -> parent family -> sub-families. A family not listed here is
# shown under "Other".
_TREE: dict[str, dict[str, list[str]]] = {
    "Income statement": {
        "Revenue": ["ProductRevenue", "ServiceRevenue"],
        "CostOfRevenue": [],
        "GrossProfit": [],
        "OperatingExpenses": ["ResearchAndDevelopment", "SellingGeneralAndAdministrative",
                              "SellingAndMarketing", "GeneralAndAdministrative",
                              "RestructuringCharges", "DepreciationAndAmortization"],
        "OperatingIncome": [],
        "InterestExpense": [],
        "InterestIncome": [],
        "OtherNonOperatingIncome": [],
        "PretaxIncome": [],
        "TaxExpense": ["EffectiveTaxRate"],
        "IncomeFromContinuingOperations": [],
        "IncomeFromDiscontinuedOperations": [],
        "NetIncome": ["NetIncomeToCommon"],
        "ComprehensiveIncome": [],
    },
    "Per share and shares": {
        "EarningsPerShareBasic": [],
        "EarningsPerShareDiluted": [],
        "WeightedAverageSharesBasic": [],
        "WeightedAverageSharesDiluted": [],
        "DividendsPerShare": [],
        "SharesOutstanding": [],
    },
    "Balance sheet": {
        "TotalAssets": ["CurrentAssets", "CashAndEquivalents", "ShortTermInvestments",
                        "AccountsReceivable", "Inventory", "PropertyPlantAndEquipment",
                        "Goodwill", "IntangibleAssets"],
        "TotalLiabilities": ["CurrentLiabilities", "AccountsPayable", "DeferredRevenue",
                             "Debt"],
        "StockholdersEquity": ["RetainedEarnings"],
    },
    "Cash flow": {
        "CashFromOperations": ["ShareBasedCompensation"],
        "CashFromInvesting": ["Capex"],
        "CashFromFinancing": ["StockRepurchases", "DividendsPaid", "DebtIssued",
                              "DebtRepaid"],
    },
    "Other": {
        "ConcentrationRiskPercentage": [],
        "NumberOfSegments": [],
        "EmployeeCount": [],
    },
}


@lru_cache(maxsize=1)
def mapping_rows() -> tuple[Mapping[str, Any], ...]:
    """The committed concept_mapping rows (read once)."""
    return tuple(committed_mapping_rows())


def family_names() -> list[str]:
    """Every family the mapping declares, sorted."""
    return sorted({str(r["concept_family"]) for r in mapping_rows()})


def family_tree() -> list[dict[str, Any]]:
    """The browsing tree: ``[{statement, families: [{family, subs: [...]}, ...]}]``.

    Families the mapping declares but the tree does not place go under "Other"; names in
    the tree the mapping does not declare are dropped.
    """
    known = set(family_names())
    placed: set[str] = set()
    out: list[dict[str, Any]] = []
    for statement, parents in _TREE.items():
        fams = []
        for parent, subs in parents.items():
            if parent not in known:
                continue
            kept = [s for s in subs if s in known]
            placed.update([parent, *kept])
            fams.append({"family": parent, "subs": kept})
        out.append({"statement": statement, "families": fams})
    rest = sorted(known - placed)
    if rest:
        out[-1]["families"].extend({"family": f, "subs": []} for f in rest)
    return out


def mapping_qname(concept: str) -> str:
    """A raw concept QName in the mapping's form (a filer extension becomes ``ext:``)."""
    prefix, _, local = concept.partition(":")
    if not local:
        return concept
    return concept if prefix in STANDARD_PREFIXES else f"ext:{local}"


def cik10(cik: int | str) -> str:
    """A CIK as the mapping writes it: ten digits, zero-padded."""
    return str(int(cik)).zfill(10)


def family_of(concept: str, cik: int | str) -> Mapping[str, Any] | None:
    """The mapping row that gives a raw fact its family (the view's ``fact_family`` rule).

    Args:
        concept: The raw concept QName as filed.
        cik: The filer's CIK.

    Returns:
        The winning row (``concept_family``, ``preference_rank``, ``concept_role`` ...),
        or None when the concept is in no family for this filer.
    """
    return _family_of(mapping_qname(concept), cik10(cik))


@lru_cache(maxsize=50_000)
def _family_of(qname: str, cik: str) -> Mapping[str, Any] | None:
    return fact_family(mapping_rows(), cik, qname)


def shadowing(family: str, cik: int | str) -> dict[str, str]:
    """The family's QNames that the view's rule gives to another family, for this filer.

    A sub-family such as ProductRevenue lists Revenue's concepts at a worse rank, so an
    undimensioned fact of those concepts belongs to Revenue in ``mv_financial_facts``;
    the product share then lives in Revenue's members (``ProductOrServiceAxis``).

    Returns:
        ``{qname: winning family}`` for every member QName won by another family.
    """
    c10 = cik10(cik)
    out: dict[str, str] = {}
    for m in family_members(family, cik):
        win = _family_of(m["qname"], c10)
        if win is not None and win["concept_family"] != family:
            out[m["qname"]] = str(win["concept_family"])
    return out


def family_members(family: str, cik: int | str) -> list[dict[str, Any]]:
    """The QNames that map to ``family`` for this filer, best first.

    Args:
        family: The family name.
        cik: The filer's CIK.

    Returns:
        ``[{qname, rank, role, filer_specific, substitution_note}]``; a filer's own row
        replaces the generic row of the same QName.
    """
    c10 = cik10(cik)
    chosen: dict[str, Mapping[str, Any]] = {}
    for row in mapping_rows():
        if row["concept_family"] != family:
            continue
        row_cik = row.get("cik_override") or ""
        if row_cik not in ("", c10):
            continue
        q = str(row["qname"])
        if q not in chosen or (row_cik and not chosen[q].get("cik_override")):
            chosen[q] = row
    members = [{
        "qname": q,
        "rank": int(r["preference_rank"]),
        "role": r.get("concept_role") or "",
        "filer_specific": bool(r.get("cik_override")),
        "substitution_note": r.get("substitution_note") or "",
    } for q, r in chosen.items()]
    return sorted(members, key=lambda m: (m["rank"], m["qname"]))
