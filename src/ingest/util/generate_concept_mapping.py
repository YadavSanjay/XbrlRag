#!/usr/bin/env python3
"""
Generate concept_mapping_hifi_kpi_bootstrap.csv from HiFi-KPI data files.

Reads:
  data/storage/datasets/hifikpi/concept_synonyms.json   (total_occurrences per concept)
  data/storage/datasets/hifikpi/concept_family_table.json  (taxonomy hierarchy)

Writes:
  concept_mapping_hifi_kpi_bootstrap.csv  (in project root by default)

Output columns:
  concept_family, qname, preference_rank, cik_override, is_deprecated, source,
  concept_role, substitution_code, substitution_note

Roles, substitutions, IFRS rows and cik-scoped members (docs/specs/concept-family-coverage.md):
  configs/concept_mapping/concept_family_coverage.yaml is applied last
  (apply_family_coverage); src/retriever/xbrl/family_resolution.family_member reads the
  same rows (committed_mapping_rows).

Strategy (docs/specs/concept-rank-integrity.md):
  For each concept_family the anchor QNames are its primaries, ranked 1..n in
  their declared order: that order is a reviewed decision about which concept
  the family means, so it is never re-sorted. Taxonomy descendants (only with
  --max-depth > 0) follow, ranked by total_occurrences in concept_synonyms.json.
  FAMILY_COMPONENT_QNAMES (parts of the measure, or neighbouring measures) are
  ranked from COMPONENT_RANK_BASE (50), so they never tie with a primary.

  FreeCashFlow is DERIVED (not a tagged fact) and produces no rows by design.

Reviewed overrides (docs/specs/concept-families-wave1.md):
  configs/concept_mapping/concept_family_overrides.yaml (--overrides) is validated and
  its family additions are appended after the base QNames before ranking. Each filer
  entry adds that filer's reviewed ranking as rows with cik_override set and
  source = reviewed_override.

Run as a module so the ``src`` package resolves:
  python -m src.ingest.util.generate_concept_mapping --output <csv>
"""

import argparse
import collections.abc
import csv
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from src import PROJECT_ROOT

# The committed, reviewed additions to the families below
# (docs/specs/concept-families-wave1.md).
DEFAULT_OVERRIDES_PATH = (
    Path(PROJECT_ROOT) / "configs" / "concept_mapping" / "concept_family_overrides.yaml"
)

# ---------------------------------------------------------------------------
# Anchor QNames per concept_family.
# Each list is ordered: the first entry is the single most-important QName for
# that family; later entries are alternatives / deprecated predecessors.
# The descendants() walk expands each anchor into the full subtree of the
# taxonomy hierarchy, so siblings and specialisations are automatically
# captured without hand-enumeration.
# ---------------------------------------------------------------------------
FAMILY_ANCHOR_QNAMES: dict[str, list[str]] = {
    # ── Income Statement ────────────────────────────────────────────────────
    "Revenue": [
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",          # deprecated post-ASC-606 (2018)
        "us-gaap:SalesRevenueGoodsNet",     # deprecated
        "us-gaap:SalesRevenueServicesNet",  # deprecated
    ],
    # ProductRevenue / ServiceRevenue are Revenue sliced by dimension; the same
    # QNames apply but they are accessed via dimensions:{"product":...} /
    # {"service":...} in the plan. We still map them to Revenue-family QNames
    # so the resolver knows which concepts to query.
    "ProductRevenue": [
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueGoodsNet",
    ],
    "ServiceRevenue": [
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueServicesNet",
    ],
    "CostOfRevenue": [
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:CostOfGoodsSold",
        "us-gaap:CostOfServices",
    ],
    "GrossProfit": [
        "us-gaap:GrossProfit",
    ],
    # Only us-gaap:OperatingExpenses measures "operating expenses"
    # (docs/specs/family-measure-equivalence.md): OperatingCostsAndExpenses (no SG&A)
    # and CostsAndExpenses (total costs) are components below.
    "OperatingExpenses": [
        "us-gaap:OperatingExpenses",
    ],
    "ResearchAndDevelopment": [
        "us-gaap:ResearchAndDevelopmentExpense",
        "us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "SellingGeneralAndAdministrative": [
        "us-gaap:SellingGeneralAndAdministrativeExpense",
    ],
    "SellingAndMarketing": [
        "us-gaap:SellingAndMarketingExpense",
    ],
    "GeneralAndAdministrative": [
        "us-gaap:GeneralAndAdministrativeExpense",
    ],
    "RestructuringCharges": [
        "us-gaap:RestructuringCharges",
        "us-gaap:RestructuringAndRelatedCostIncurredCost",
        "us-gaap:BusinessExitCosts1",
    ],
    # `DepreciationAmortizationAndOther` is not a us-gaap element: Microsoft's line of that
    # name is its own extension, a cik-scoped member in concept_family_coverage.yaml
    # (docs/specs/concept-family-coverage.md, D4).
    "DepreciationAndAmortization": [
        "us-gaap:DepreciationDepletionAndAmortization",
        "us-gaap:DepreciationAndAmortization",
    ],
    "OperatingIncome": [
        "us-gaap:OperatingIncomeLoss",
    ],
    # InterestExpenseNonoperating is the same measure, tagged where the line sits below
    # operating income; it ranks right after the total so a filer tagging both keeps
    # InterestExpense (docs/specs/concept-family-coverage.md, D1).
    "InterestExpense": [
        "us-gaap:InterestExpense",
        "us-gaap:InterestExpenseNonoperating",
        "us-gaap:InterestAndDebtExpense",
        "us-gaap:InterestExpenseDebt",
    ],
    "InterestIncome": [
        "us-gaap:InvestmentIncomeInterest",
        "us-gaap:InterestIncomeOperating",
        "us-gaap:InterestAndDividendIncomeOperating",
    ],
    "OtherNonOperatingIncome": [
        "us-gaap:OtherNonoperatingIncomeExpense",
        "us-gaap:NonoperatingIncomeExpense",
        "us-gaap:OtherNonoperatingIncome",
        "us-gaap:OtherNonoperatingExpense",
    ],
    "PretaxIncome": [
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "TaxExpense": [
        "us-gaap:IncomeTaxExpenseBenefit",
    ],
    "IncomeFromContinuingOperations": [
        "us-gaap:IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
        "us-gaap:IncomeLossFromContinuingOperations",
    ],
    "IncomeFromDiscontinuedOperations": [
        "us-gaap:IncomeLossFromDiscontinuedOperationsNetOfTax",
        "us-gaap:DiscontinuedOperationGainLossOnDisposalOfDiscontinuedOperationNetOfTax",
    ],
    "NetIncome": [
        "us-gaap:NetIncomeLoss",
        "us-gaap:ProfitLoss",
        "us-gaap:NetIncomeLossAttributableToParent",
    ],
    "NetIncomeToCommon": [
        "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic",
        "us-gaap:NetIncomeLossAvailableToCommonStockholdersDiluted",
    ],
    "ComprehensiveIncome": [
        "us-gaap:ComprehensiveIncomeNetOfTax",
        "us-gaap:ComprehensiveIncomeNetOfTaxIncludingPortionAttributableToNoncontrollingInterest",
    ],

    # ── Per-share & share counts ────────────────────────────────────────────
    "EarningsPerShareBasic": [
        "us-gaap:EarningsPerShareBasic",
    ],
    "EarningsPerShareDiluted": [
        "us-gaap:EarningsPerShareDiluted",
    ],
    "WeightedAverageSharesBasic": [
        "us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "WeightedAverageSharesDiluted": [
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    "DividendsPerShare": [
        "us-gaap:CommonStockDividendsPerShareDeclared",
        "us-gaap:CommonStockDividendsPerShareCashPaid",
    ],
    "SharesOutstanding": [
        "us-gaap:CommonStockSharesOutstanding",
    ],

    # ── Balance Sheet ───────────────────────────────────────────────────────
    "TotalAssets": [
        "us-gaap:Assets",
    ],
    "CurrentAssets": [
        "us-gaap:AssetsCurrent",
    ],
    "CashAndEquivalents": [
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:CashCashEquivalentsAndShortTermInvestments",
        "us-gaap:CashAndCashEquivalentsAndShortTermInvestments",
    ],
    "ShortTermInvestments": [
        "us-gaap:ShortTermInvestments",
        "us-gaap:AvailableForSaleSecuritiesCurrent",
        "us-gaap:MarketableSecuritiesCurrent",
    ],
    "AccountsReceivable": [
        "us-gaap:AccountsReceivableNetCurrent",
        "us-gaap:ReceivablesNetCurrent",
    ],
    "Inventory": [
        "us-gaap:InventoryNet",
        "us-gaap:InventoryGross",
    ],
    "PropertyPlantAndEquipment": [
        "us-gaap:PropertyPlantAndEquipmentNet",
        "us-gaap:PropertyPlantAndEquipmentGross",
    ],
    "Goodwill": [
        "us-gaap:Goodwill",
    ],
    "IntangibleAssets": [
        "us-gaap:IntangibleAssetsNetExcludingGoodwill",
        "us-gaap:FiniteLivedIntangibleAssetsNet",
    ],
    "TotalLiabilities": [
        "us-gaap:Liabilities",
    ],
    "CurrentLiabilities": [
        "us-gaap:LiabilitiesCurrent",
    ],
    "AccountsPayable": [
        "us-gaap:AccountsPayableCurrent",
        "us-gaap:AccountsPayableTradeCurrent",
    ],
    "DeferredRevenue": [
        "us-gaap:ContractWithCustomerLiability",
        "us-gaap:ContractWithCustomerLiabilityCurrent",
        "us-gaap:ContractWithCustomerLiabilityNoncurrent",
        "us-gaap:DeferredRevenueCurrent",
        "us-gaap:DeferredRevenueNoncurrent",
        "us-gaap:DeferredRevenue",
    ],
    # Long-term debt including current maturities (us-gaap:LongTermDebt). The
    # noncurrent-only balances are components below, and lease obligations are not
    # Debt (docs/specs/family-measure-equivalence.md).
    "Debt": [
        "us-gaap:LongTermDebt",
    ],
    "StockholdersEquity": [
        "us-gaap:StockholdersEquity",
        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "RetainedEarnings": [
        "us-gaap:RetainedEarningsAccumulatedDeficit",
    ],

    # ── Cash Flow Statement ─────────────────────────────────────────────────
    "CashFromOperations": [
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    ],
    "CashFromInvesting": [
        "us-gaap:NetCashProvidedByUsedInInvestingActivities",
    ],
    "CashFromFinancing": [
        "us-gaap:NetCashProvidedByUsedInFinancingActivities",
    ],
    "Capex": [
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:PaymentsForCapitalImprovements",
        "us-gaap:PaymentsToAcquireProductiveAssets",
    ],
    "StockRepurchases": [
        "us-gaap:PaymentsForRepurchaseOfCommonStock",
        "us-gaap:TreasuryStockValueAcquiredCostMethod",
        "us-gaap:PaymentsForRepurchaseOfEquity",
    ],
    "DividendsPaid": [
        "us-gaap:PaymentsOfDividends",
        "us-gaap:PaymentsOfDividendsCommonStock",
        "us-gaap:PaymentsOfOrdinaryDividends",
    ],
    "DebtIssued": [
        "us-gaap:ProceedsFromIssuanceOfLongTermDebt",
        "us-gaap:ProceedsFromLongTermLinesOfCredit",
        "us-gaap:ProceedsFromDebtNetOfIssuanceCosts",
    ],
    "DebtRepaid": [
        "us-gaap:RepaymentsOfLongTermDebt",
        "us-gaap:RepaymentsOfDebt",
        "us-gaap:RepaymentsOfLongTermLinesOfCredit",
        "us-gaap:RepaymentsOfNotesPayable",
    ],
    "ShareBasedCompensation": [
        "us-gaap:ShareBasedCompensation",
        "us-gaap:AllocatedShareBasedCompensationExpense",
    ],

    # ── Ratios, rates & disclosure metrics ──────────────────────────────────
    "ConcentrationRiskPercentage": [
        "us-gaap:ConcentrationRiskPercentage1",
    ],
    "EffectiveTaxRate": [
        "us-gaap:EffectiveIncomeTaxRateContinuingOperations",
        "us-gaap:EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate",
    ],
    "NumberOfSegments": [
        "us-gaap:NumberOfReportableSegments",
        "us-gaap:NumberOfOperatingSegments",
    ],
    "EmployeeCount": [
        "us-gaap:EntityNumberOfEmployees",
        "dei:EntityNumberOfEmployees",
    ],

    # FreeCashFlow is intentionally absent: it is DERIVED (not a tagged XBRL
    # fact) and the plan generator decomposes it into CashFromOperations - Capex.
}

# First rank of the component tier: parts of a family's measure, or a neighbouring
# measure, that may answer for it only below every primary (never at rank 1).
COMPONENT_RANK_BASE = 50

# Component tier per concept_family (docs/specs/concept-rank-integrity.md). These are
# ranked from COMPONENT_RANK_BASE, after every anchor above, and a per-filer override
# never promotes them: a filer that tags only depreciation has no D&A total, and
# answering "D&A" with depreciation alone would be a confident wrong number.
FAMILY_COMPONENT_QNAMES: dict[str, list[str]] = {
    "DepreciationAndAmortization": [
        "us-gaap:Depreciation",
        "us-gaap:AmortizationOfIntangibleAssets",
    ],
    "Debt": [
        "us-gaap:DebtCurrent",
        "us-gaap:ShortTermBorrowings",
        "us-gaap:NotesPayable",
        "us-gaap:LongTermDebtNoncurrent",
        "us-gaap:LongTermDebtAndCapitalLeaseObligations",
    ],
    "OperatingExpenses": [
        "us-gaap:OperatingCostsAndExpenses",
        "us-gaap:CostsAndExpenses",
    ],
    "SellingAndMarketing": [
        "us-gaap:MarketingAndAdvertisingExpense",
    ],
    "ComprehensiveIncome": [
        "us-gaap:OtherComprehensiveIncomeLossNetOfTax",
    ],
    "IntangibleAssets": [
        "us-gaap:IndefiniteLivedIntangibleAssetsExcludingGoodwill",
    ],
    "SharesOutstanding": [
        "us-gaap:CommonStockSharesIssued",
    ],
    "AccountsPayable": [
        "us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent",
    ],
}


def descendants(qname: str, tree: dict, max_depth: int = 0) -> list[str]:
    """
    Return descendant QNames of *qname* up to *max_depth* levels below the
    anchor (depth-first, iterative).  Default max_depth=0 returns an empty
    list — i.e. anchors-only mode, which is the correct default.

    Why NOT walk the tree by default?
    The HiFi-KPI concept_family_table.json is a XBRL *calculation-arc* tree,
    not a semantic income-statement classification tree.  In this hierarchy,
    NetIncomeLoss is the *starting point of operating cash flows*, so its
    children are non-cash add-backs and working capital changes
    (DepreciationAndAmortization, AllocatedShareBasedCompensation, …).
    Walking those children would put SBC as a candidate for the NetIncome
    family, which is semantically wrong for concept_mapping purposes.

    Use max_depth=1 or 2 only for families whose immediate tree children are
    genuine close variants (e.g. Revenues → RevenueFromRelatedParties).
    """
    if max_depth <= 0:
        return []
    result: list[str] = []
    # Stack entries: (qname, current_depth)
    stack = [(child, 1) for child in tree.get(qname, {}).get("children", [])]
    visited: set[str] = set()
    while stack:
        node, depth = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        result.append(node)
        if depth < max_depth:
            children = tree.get(node, {}).get("children", [])
            stack.extend((child, depth + 1) for child in reversed(children))
    return result


def build_family_to_qnames(
    tree: dict,
    max_depth: int = 3,
    family_anchors: collections.abc.Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """
    Expand each family's anchors via the taxonomy hierarchy.
    Returns {family: [ordered unique QNames]} preserving anchor order, with
    tree descendants appended after each anchor (breadth-first within anchor).
    ``family_anchors`` defaults to FAMILY_ANCHOR_QNAMES; pass the merged families
    to expand reviewed additions too.
    """
    if family_anchors is None:
        family_anchors = FAMILY_ANCHOR_QNAMES
    family_qnames: dict[str, list[str]] = {}
    for family, anchors in family_anchors.items():
        seen: set[str] = set()
        ordered: list[str] = []
        for anchor in anchors:
            if anchor not in seen:
                seen.add(anchor)
                ordered.append(anchor)
            for desc in descendants(anchor, tree, max_depth=max_depth):
                if desc not in seen:
                    seen.add(desc)
                    ordered.append(desc)
        family_qnames[family] = ordered
    return family_qnames


def rank_by_occurrences(
    qnames: list[str],
    synonyms: dict,
) -> list[tuple[str, int]]:
    """
    Filter *qnames* to those present in *synonyms* and sort by total_occurrences
    descending.  Returns [(qname, total_occurrences), ...].
    """
    candidates = [
        (q, synonyms[q]["total_occurrences"]) for q in qnames if q in synonyms
    ]
    return sorted(candidates, key=lambda x: -x[1])


def family_ranking(
    primaries: Sequence[str],
    components: Sequence[str],
    descendants: Sequence[str],
) -> list[tuple[str, int]]:
    """A family's generic ranking (docs/specs/concept-rank-integrity.md).

    Args:
        primaries: The family's own measure, most important first.
        components: Parts of the measure or neighbouring measures.
        descendants: Taxonomy descendants, already ordered.

    Returns:
        ``(qname, preference_rank)`` pairs: primaries from 1, then descendants, then
        components from 50.

    Raises:
        ValueError: If primaries and descendants would reach the component band.
    """
    ranking: list[tuple[str, int]] = []
    seen: set[str] = set()
    for qname in [*primaries, *descendants]:
        if qname not in seen:
            seen.add(qname)
            ranking.append((qname, len(ranking) + 1))
    if len(ranking) >= COMPONENT_RANK_BASE:
        raise ValueError(
            f"{len(ranking)} primaries and descendants reach the component band "
            f"(rank {COMPONENT_RANK_BASE})"
        )
    rank = COMPONENT_RANK_BASE
    for qname in components:
        if qname not in seen:
            seen.add(qname)
            ranking.append((qname, rank))
            rank += 1
    return ranking


def validate_family_overrides(
    raw: collections.abc.Mapping[str, Any],
    primaries: collections.abc.Mapping[str, Sequence[str]],
    components: collections.abc.Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Validate a parsed override file against the base families.

    See docs/specs/concept-families-wave1.md, Requirement 2.

    Args:
        raw: The parsed YAML file.
        primaries: Base family -> ordered primary QNames.
        components: Base family -> ordered component QNames.

    Returns:
        ``{"families": {name: {"label", "primaries", "components"}},
        "filers": {(cik, family): [preferred qnames]}}``.

    Raises:
        ValueError: If any rule V1-V10 is broken.
    """
    if not isinstance(raw, collections.abc.Mapping):
        raise ValueError(f"V1: the override file must be a mapping, got {type(raw).__name__}")
    unknown = set(raw) - _OVERRIDE_TOP_KEYS
    if unknown:
        raise ValueError(f"V1: unknown top-level keys {sorted(unknown)}")
    version = raw.get("version")
    if raw and (type(version) is not int or version != 1):
        raise ValueError(f"V1: version must be 1, got {version!r}")

    families = _optional(raw, "families", collections.abc.Mapping, "a mapping")
    filers = _optional(raw, "filers", collections.abc.Sequence, "a list")
    if isinstance(filers, str):
        raise ValueError("V1: filers must be a list")

    base = set(primaries) | set(components)
    added: dict[str, dict[str, Any]] = {}
    for name, entry in families.items():
        added[name] = _validate_family_entry(name, entry, name in base)

    for name, entry in added.items():
        seen = set(primaries.get(name, ())) | set(components.get(name, ()))
        for qname in [*entry["primaries"], *entry["components"]]:
            if qname in seen:
                raise ValueError(f"V6: {qname} is already in family {name}")
            seen.add(qname)

    merged_p, merged_c = merge_family_overrides(
        primaries, components, {"families": added, "filers": {}}
    )
    for name in added:
        if len(merged_p[name]) >= COMPONENT_RANK_BASE:
            raise ValueError(
                f"V9: {name} has {len(merged_p[name])} primaries; they reach the "
                f"component band at rank {COMPONENT_RANK_BASE}"
            )
        if len(merged_c.get(name, [])) > _IFRS_RANK_BASE - COMPONENT_RANK_BASE:
            raise ValueError(
                f"V9: {name} has {len(merged_c[name])} components; they reach the "
                f"IFRS band at rank {_IFRS_RANK_BASE}"
            )

    ranks = {
        name: dict(family_ranking(merged_p[name], merged_c.get(name, []), []))
        for name in merged_p
    }
    for name, entry in added.items():
        for qname in [*entry["primaries"], *entry["components"]]:
            rank = ranks[name][qname]
            for other, other_ranks in ranks.items():
                if other != name and other_ranks.get(qname) == rank:
                    raise ValueError(
                        f"V7: {qname} has rank {rank} in both {name} and {other}"
                    )
        for qname in entry["primaries"]:
            for other, other_primaries in merged_p.items():
                if other != name and qname in other_primaries:
                    raise ValueError(
                        f"V8: {qname}, added as a primary of {name}, is a primary of {other}"
                    )

    reviewed: dict[tuple[str, str], list[str]] = {}
    for entry in filers:
        key, preferred = _validate_filer_entry(entry, merged_p, merged_c)
        if key in reviewed:
            raise ValueError(f"V10: two filer entries for {key}")
        reviewed[key] = preferred
    return {"families": added, "filers": reviewed}


_OVERRIDE_TOP_KEYS = frozenset({"version", "families", "filers"})
_FAMILY_KEYS = frozenset({"label", "primaries", "components", "reason"})
_FILER_KEYS = frozenset({"cik", "family", "preferred", "reason"})
# The prefixes concept_mapping_ns_expand.sql expands to namespace URIs; any other
# prefix could never match a fact.
_QNAME_RE = re.compile(r"(?:us-gaap|srt|dei|ifrs-full):[A-Za-z][A-Za-z0-9]*")
_FAMILY_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9]*")
_CIK_RE = re.compile(r"[0-9]{10}")
# IFRS rows rank from 100 (scripts/ddl); components must stay below it.
_IFRS_RANK_BASE = 100


def _optional(
    raw: collections.abc.Mapping[str, Any], key: str, kind: type, what: str
) -> Any:
    """``raw[key]``, empty when absent or null; ValueError when of the wrong kind."""
    value = raw.get(key)
    if value is None:
        return {} if kind is collections.abc.Mapping else []
    if not isinstance(value, kind):
        raise ValueError(f"V1: {key} must be {what}")
    return value


def _reason(entry: collections.abc.Mapping[str, Any], where: str) -> None:
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"V3: {where} has no reason")


def _qnames(entry: collections.abc.Mapping[str, Any], key: str, where: str) -> list[str]:
    """The entry's QName list under ``key``, each checked against V2."""
    value = entry.get(key)
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, collections.abc.Sequence):
        raise ValueError(f"V1: {where}.{key} must be a list")
    for qname in value:
        if not isinstance(qname, str) or not _QNAME_RE.fullmatch(qname):
            raise ValueError(f"V2: {where}.{key} has a malformed QName {qname!r}")
    return list(value)


def _validate_family_entry(name: Any, entry: Any, in_base: bool) -> dict[str, Any]:
    """One ``families`` entry checked against V1-V5, in normalized form."""
    where = f"family {name}"
    if not isinstance(entry, collections.abc.Mapping):
        raise ValueError(f"V1: {where} must be a mapping")
    unknown = set(entry) - _FAMILY_KEYS
    if unknown:
        raise ValueError(f"V1: {where} has unknown keys {sorted(unknown)}")
    added_p = _qnames(entry, "primaries", where)
    added_c = _qnames(entry, "components", where)
    _reason(entry, where)
    label = entry.get("label")
    if in_base:
        if "label" in entry:
            raise ValueError(f"V4: {where} is a base family and takes no label")
    else:
        if not isinstance(name, str) or not _FAMILY_NAME_RE.fullmatch(name):
            raise ValueError(f"V4: new family name {name!r} is not CamelCase")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"V4: new {where} has no label")
        if not added_p:
            raise ValueError(f"V4: new {where} has no primary")
    if not added_p and not added_c:
        raise ValueError(f"V5: {where} adds no QName")
    return {"label": None if in_base else label, "primaries": added_p, "components": added_c}


def _validate_filer_entry(
    entry: Any,
    merged_p: collections.abc.Mapping[str, Sequence[str]],
    merged_c: collections.abc.Mapping[str, Sequence[str]],
) -> tuple[tuple[str, str], list[str]]:
    """One ``filers`` entry checked against V1, V3 and V10."""
    if not isinstance(entry, collections.abc.Mapping):
        raise ValueError("V1: a filer entry must be a mapping")
    if set(entry) != _FILER_KEYS:
        raise ValueError(
            f"V1: a filer entry needs exactly the keys {sorted(_FILER_KEYS)}, "
            f"got {sorted(entry)}"
        )
    cik, family = entry["cik"], entry["family"]
    where = f"filer {cik!r} {family!r}"
    _reason(entry, where)
    if not isinstance(cik, str) or not _CIK_RE.fullmatch(cik):
        raise ValueError(f"V10: {where}: cik must be a 10-digit zero-padded string")
    if not isinstance(family, str) or family not in merged_p:
        raise ValueError(f"V10: {where}: unknown family")
    preferred = _qnames(entry, "preferred", where)
    if not preferred:
        raise ValueError(f"V10: {where}: preferred is empty")
    if len(set(preferred)) != len(preferred):
        raise ValueError(f"V10: {where}: preferred repeats a QName")
    for qname in preferred:
        if qname not in merged_p[family]:
            kind = "a component" if qname in merged_c.get(family, ()) else "not a primary"
            raise ValueError(f"V10: {where}: {qname} is {kind} of {family}")
    return (cik, family), preferred


def merge_family_overrides(
    primaries: collections.abc.Mapping[str, Sequence[str]],
    components: collections.abc.Mapping[str, Sequence[str]],
    overrides: collections.abc.Mapping[str, Any],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Merge validated overrides into the base families (spec Requirement 3).

    Args:
        primaries: Base family -> ordered primary QNames.
        components: Base family -> ordered component QNames.
        overrides: The result of ``validate_family_overrides``.

    Returns:
        ``(merged_primaries, merged_components)``; base QNames keep their order and
        additions follow, new families last.
    """
    merged_p = {name: list(qnames) for name, qnames in primaries.items()}
    merged_c = {name: list(qnames) for name, qnames in components.items()}
    for name, entry in overrides.get("families", {}).items():
        merged_p.setdefault(name, []).extend(entry["primaries"])
        if entry["components"]:
            merged_c.setdefault(name, []).extend(entry["components"])
    return merged_p, merged_c


def reviewed_filer_ranking(
    global_ranking: Sequence[tuple[str, int]],
    preferred: Sequence[str],
) -> list[tuple[str, int]]:
    """One filer's reviewed ranking of a family (spec Requirement 4).

    Args:
        global_ranking: The family's generic ``(qname, preference_rank)`` pairs.
        preferred: The reviewed QNames for ranks 1..k, in order.

    Returns:
        The preferred QNames at 1..k, the other eligible QNames after them in global
        order, and every component at its global rank.

    Raises:
        ValueError: If ``preferred`` is empty, repeats a QName, or names a QName that is
            not an eligible member of ``global_ranking``.
    """
    ordered = sorted(global_ranking, key=lambda qr: (qr[1], qr[0]))
    eligible = [q for q, r in ordered if not _is_component_rank(r)]
    if not preferred:
        raise ValueError("preferred is empty")
    if len(set(preferred)) != len(preferred):
        raise ValueError(f"preferred repeats a QName: {list(preferred)}")
    missing = [q for q in preferred if q not in eligible]
    if missing:
        raise ValueError(f"not eligible members of the family: {missing}")
    rest = [q for q in eligible if q not in preferred]
    ranking = [(q, rank) for rank, q in enumerate([*preferred, *rest], start=1)]
    return ranking + [(q, r) for q, r in ordered if _is_component_rank(r)]


def _is_component_rank(rank: int) -> bool:
    """Whether a rank is in the component band, which is never promoted."""
    return COMPONENT_RANK_BASE <= rank < _IFRS_RANK_BASE


def load_family_overrides(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the reviewed override file (spec Requirement 2).

    Args:
        path: The YAML file; None loads the committed
            ``configs/concept_mapping/concept_family_overrides.yaml``.

    Returns:
        The normalized overrides, validated against ``FAMILY_ANCHOR_QNAMES`` and
        ``FAMILY_COMPONENT_QNAMES``.

    Raises:
        FileNotFoundError: If the file does not exist; it is never treated as empty.
        ValueError: If the file breaks a validation rule.
    """
    path = DEFAULT_OVERRIDES_PATH if path is None else Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return validate_family_overrides(
        {} if raw is None else raw, FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES
    )


REVIEWED_OVERRIDE_SOURCE = "reviewed_override"


def concept_mapping_rows(
    tree: dict,
    synonyms: dict,
    overrides: collections.abc.Mapping[str, Any],
    max_depth: int = 0,
    top_k: int | None = 30,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The concept_mapping CSV rows: generic rankings, then reviewed filer rankings.

    Args:
        tree: The taxonomy hierarchy (concept_family_table.json).
        synonyms: QName -> stats with ``total_occurrences`` (concept_synonyms.json).
        overrides: The result of ``validate_family_overrides``.
        max_depth: Levels of taxonomy descendants to walk below each primary.
        top_k: Cap on primaries plus descendants per family; None for no cap.

    Returns:
        ``(rows, stats)``: one dict per CSV row, ``hifi_kpi`` rows for the merged
        families followed by ``reviewed_override`` rows per reviewed (cik, family);
        and the generic row count per family.
    """
    merged_p, merged_c = merge_family_overrides(
        FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES, overrides
    )
    family_to_qnames = build_family_to_qnames(
        tree, max_depth=max_depth, family_anchors=merged_p
    )
    rows: list[dict[str, Any]] = []
    stats: dict[str, int] = {}
    rankings: dict[str, list[tuple[str, int]]] = {}
    for family_name, qname_list in family_to_qnames.items():
        primaries = merged_p[family_name]
        # Primaries are emitted in declared order even without an occurrence count;
        # only taxonomy descendants are occurrence-ranked, capped by --top-k and kept
        # below the component band.
        room = COMPONENT_RANK_BASE - 1 - len(primaries)
        if top_k is not None:
            room = min(room, max(top_k - len(primaries), 0))
        found = [
            q for q, _ in rank_by_occurrences(qname_list, synonyms) if q not in primaries
        ]
        ranked = family_ranking(primaries, merged_c.get(family_name, []), found[:room])
        rankings[family_name] = ranked
        # An empty cik_override is read as SQL NULL: the row applies to every filer.
        rows.extend(_mapping_row(family_name, q, r, "", "hifi_kpi") for q, r in ranked)
        stats[family_name] = len(ranked)
    for (cik, family_name), preferred in sorted(overrides.get("filers", {}).items()):
        reviewed = reviewed_filer_ranking(rankings[family_name], preferred)
        rows.extend(
            _mapping_row(family_name, q, r, cik, REVIEWED_OVERRIDE_SOURCE)
            for q, r in reviewed
        )
    return rows, stats


def _mapping_row(
    family: str, qname: str, rank: int, cik_override: str, source: str
) -> dict[str, Any]:
    return {
        "concept_family": family,
        "qname": qname,
        "preference_rank": rank,
        "cik_override": cik_override,
        "is_deprecated": False,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Roles, substitutions, IFRS rows and cik-scoped members
# (docs/specs/concept-family-coverage.md)
# ---------------------------------------------------------------------------

DEFAULT_COVERAGE_PATH = (
    Path(PROJECT_ROOT) / "configs" / "concept_mapping" / "concept_family_coverage.yaml"
)
IFRS_SOURCE = "ifrs"
FAMILY_COVERAGE_SOURCE = "family_coverage"
ROLE_PRIMARY = "primary"
ROLE_SUBSTITUTE = "substitute"
ROLE_COMPONENT = "component"
# The CSV columns, the coverage columns last (the loader reads the first six by name).
MAPPING_CSV_COLUMNS = (
    "concept_family",
    "qname",
    "preference_rank",
    "cik_override",
    "is_deprecated",
    "source",
    "concept_role",
    "substitution_code",
    "substitution_note",
)

_COVERAGE_TOP_KEYS = frozenset(
    {
        "version", "substitutions", "substitutes", "ifrs", "same_measure", "parts",
        "fallback", "filers",
    }
)
_COVERAGE_FILER_KEYS = frozenset(
    {"cik", "family", "concept", "role", "position", "substitution", "reason"}
)
# A cik-scoped member: a standard QName, or `ext:<LocalName>` for a filer extension.
_COVERAGE_QNAME_RE = re.compile(r"(?:us-gaap|srt|dei|ifrs-full|ext):[A-Za-z][A-Za-z0-9]*")


def default_role(rank: int) -> str:
    """The role a rank implies when nothing names one (concept-rank-integrity's bands)."""
    return ROLE_COMPONENT if _is_component_rank(rank) else ROLE_PRIMARY


def load_family_coverage(
    path: Path | None = None,
    overrides: collections.abc.Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load and validate the committed coverage file.

    Args:
        path: The YAML file; None loads
            ``configs/concept_mapping/concept_family_coverage.yaml``.
        overrides: The validated override file the coverage applies on top of; None loads
            the committed one.

    Returns:
        ``{"substitutes": {(family, qname): {"code", "note"}},
        "same_measure": {family: QNames credited as the measure at rank 2+},
        "ifrs": {family: [qnames]}, "parts": {family: [part families]},
        "fallback": {family: frozenset of fallback-only primary QNames},
        "filers": [entry, ...]}``, each filer entry with
        ``cik``, ``family``, ``concept``, ``role``, ``position`` (``"first"``, ``"last"``,
        ``("after", qname)`` or None) and ``substitution`` (``{"code", "note"}`` or None).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If an entry is malformed or contradicts the families.
    """
    path = DEFAULT_COVERAGE_PATH if path is None else Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if overrides is None:
        overrides = load_family_overrides()
    merged_p, merged_c = merge_family_overrides(
        FAMILY_ANCHOR_QNAMES, FAMILY_COMPONENT_QNAMES, overrides
    )
    return validate_family_coverage({} if raw is None else raw, merged_p, merged_c)


def validate_family_coverage(
    raw: collections.abc.Mapping[str, Any],
    primaries: collections.abc.Mapping[str, Sequence[str]],
    components: collections.abc.Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Validate a parsed coverage file against the merged families.

    Args:
        raw: The parsed YAML file.
        primaries: Merged family -> primary QNames.
        components: Merged family -> component QNames.

    Returns:
        The normalized coverage (see ``load_family_coverage``).

    Raises:
        ValueError: If the file is malformed, a substitute is neither a component of its
            family nor an IFRS element outside it, a code has no note, or a filer entry
            is inconsistent.
    """
    if not isinstance(raw, collections.abc.Mapping):
        raise ValueError("coverage file must be a mapping")
    unknown = set(raw) - _COVERAGE_TOP_KEYS
    if unknown:
        raise ValueError(f"coverage file has unknown keys {sorted(unknown)}")
    if raw and raw.get("version") != 1:
        raise ValueError(f"coverage version must be 1, got {raw.get('version')!r}")
    notes = raw.get("substitutions") or {}
    for code, note in notes.items():
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"substitution {code!r} has no note")

    def substitution(value: Any, where: str) -> dict[str, str]:
        if isinstance(value, str):
            value = {"code": value}
        if not isinstance(value, collections.abc.Mapping) or "code" not in value:
            raise ValueError(f"{where}: substitution must be a code or {{code, note}}")
        code = value["code"]
        if code not in notes:
            raise ValueError(f"{where}: unknown substitution code {code!r}")
        note = value.get("note", notes[code])
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"{where}: empty note")
        return {"code": code, "note": " ".join(note.split())}

    ifrs_raw = raw.get("ifrs") or {}
    substitutes: dict[tuple[str, str], dict[str, str]] = {}
    for family, entries in (raw.get("substitutes") or {}).items():
        for qname, value in entries.items():
            where = f"substitute {family} {qname}"
            # An IFRS element that is no member of the family joins it as a substitute
            # only (apply_family_coverage ranks it in the component band).
            ifrs_only = (
                qname.startswith("ifrs-full:") and bool(_QNAME_RE.fullmatch(qname))
                and family in primaries and qname not in primaries[family]
                and qname not in ifrs_raw.get(family, ())
            )
            if qname not in components.get(family, ()) and not ifrs_only:
                raise ValueError(f"{where}: not a component of {family}")
            substitutes[(family, qname)] = substitution(value, where)

    parts: dict[str, list[str]] = {}
    for family, names in (raw.get("parts") or {}).items():
        if family not in primaries:
            raise ValueError(f"parts: unknown family {family!r}")
        if not isinstance(names, list) or len(names) < 2 or len(set(names)) != len(names):
            raise ValueError(f"parts {family}: needs two or more distinct families")
        for name in names:
            if name not in primaries or name == family:
                raise ValueError(f"parts {family}: {name!r} is not another family")
        parts[family] = list(names)

    ifrs: dict[str, list[str]] = {}
    for family, qnames in ifrs_raw.items():
        if family not in primaries:
            raise ValueError(f"ifrs: unknown family {family!r}")
        if len(set(qnames)) != len(qnames):
            raise ValueError(f"ifrs {family}: repeats a QName")
        for qname in qnames:
            if not qname.startswith("ifrs-full:") or not _QNAME_RE.fullmatch(qname):
                raise ValueError(f"ifrs {family}: {qname!r} is not an ifrs-full QName")
            if qname in primaries[family] or qname in components.get(family, ()):
                raise ValueError(f"ifrs {family}: {qname} is already a member")
        ifrs[family] = list(qnames)

    filers = [
        _validate_coverage_filer(entry, primaries, components, substitution)
        for entry in raw.get("filers") or []
    ]
    seen: set[tuple[str, str, str]] = set()
    for entry in filers:
        key = (entry["cik"], entry["family"], entry["concept"])
        if key in seen:
            raise ValueError(f"filer entry {key} appears twice")
        seen.add(key)
    same: dict[str, frozenset[str]] = {}
    for family, qnames in (raw.get("same_measure") or {}).items():
        members = set(primaries.get(family, ())) | set(ifrs.get(family, ()))
        for qname in qnames:
            if qname not in members:
                raise ValueError(f"same_measure {family}: {qname} is not a primary of it")
        same[family] = frozenset(qnames)
    # Fallback-only primaries (docs/specs/screen-candidate-order.md R3): the facts-count
    # pass never makes one a filer's rank 1.
    fallback: dict[str, frozenset[str]] = {}
    fallback_raw = raw.get("fallback") or {}
    if not isinstance(fallback_raw, collections.abc.Mapping):
        raise ValueError("fallback must be a mapping of family -> [QName, ...]")
    for family, qnames in fallback_raw.items():
        if family not in primaries:
            raise ValueError(f"fallback: unknown family {family!r}")
        if isinstance(qnames, str) or not isinstance(qnames, collections.abc.Sequence):
            raise ValueError(f"fallback {family}: must be a list of QNames")
        if len(set(qnames)) != len(qnames):
            raise ValueError(f"fallback {family}: repeats a QName")
        for qname in qnames:
            if qname not in primaries[family]:
                raise ValueError(f"fallback {family}: {qname} is not a primary of it")
        fallback[family] = frozenset(qnames)
    return {"substitutes": substitutes, "ifrs": ifrs, "filers": filers, "same_measure": same,
            "parts": parts, "fallback": fallback}


def _validate_coverage_filer(
    entry: Any,
    primaries: collections.abc.Mapping[str, Sequence[str]],
    components: collections.abc.Mapping[str, Sequence[str]],
    substitution: collections.abc.Callable[[Any, str], dict[str, str]],
) -> dict[str, Any]:
    """One ``filers`` entry of the coverage file, in normalized form."""
    if not isinstance(entry, collections.abc.Mapping):
        raise ValueError("a coverage filer entry must be a mapping")
    unknown = set(entry) - _COVERAGE_FILER_KEYS
    if unknown:
        raise ValueError(f"coverage filer entry has unknown keys {sorted(unknown)}")
    cik, family, concept = entry.get("cik"), entry.get("family"), entry.get("concept")
    where = f"filer {cik!r} {family!r} {concept!r}"
    _reason(entry, where)
    if not isinstance(cik, str) or not _CIK_RE.fullmatch(cik):
        raise ValueError(f"{where}: cik must be a 10-digit zero-padded string")
    if not isinstance(family, str) or family not in primaries:
        raise ValueError(f"{where}: unknown family")
    if not isinstance(concept, str) or not _COVERAGE_QNAME_RE.fullmatch(concept):
        raise ValueError(f"{where}: malformed concept")
    if concept in primaries[family] or concept in components.get(family, ()):
        raise ValueError(f"{where}: already a member of {family} for every filer")
    role = entry.get("role")
    position: Any = None
    sub = None
    if role == ROLE_PRIMARY:
        if "substitution" in entry:
            raise ValueError(f"{where}: a primary states no substitution")
        position = entry.get("position", "last")
        if isinstance(position, collections.abc.Mapping) and set(position) == {"after"}:
            position = ("after", position["after"])
        elif position not in ("first", "last"):
            raise ValueError(f"{where}: position must be first, last or {{after: QName}}")
    elif role == ROLE_SUBSTITUTE:
        if "position" in entry:
            raise ValueError(f"{where}: a substitute ranks after the component band")
        sub = substitution(entry.get("substitution"), where)
    else:
        raise ValueError(f"{where}: role must be primary or substitute")
    return {"cik": cik, "family": family, "concept": concept, "role": role,
            "position": position, "substitution": sub}


def apply_family_coverage(
    rows: Sequence[collections.abc.Mapping[str, Any]],
    coverage: collections.abc.Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Add roles, substitutions, IFRS rows and cik-scoped members to the mapping rows.

    Args:
        rows: ``concept_mapping_rows`` output (generic and reviewed rows).
        coverage: The result of ``load_family_coverage``.

    Returns:
        Every row with ``concept_role``, ``substitution_code`` and ``substitution_note``;
        the IFRS rows (generic, source ``ifrs``) after them, an IFRS substitute that is
        no member of its family at the family's next component-band rank; then the
        cik-scoped rows
        (source ``family_coverage``). A ``first`` member rewrites the filer's whole
        ranking of the family as cik-scoped rows, as a reviewed ranking does.

    Raises:
        ValueError: If a cik-scoped member's rank would leave its band or collide.
    """
    out = demote_revenue_slices([dict(r) for r in rows])
    for family, qnames in coverage["ifrs"].items():
        out.extend(
            _mapping_row(family, q, _IFRS_RANK_BASE + i, "", IFRS_SOURCE)
            for i, q in enumerate(qnames)
        )
    index = {(r["concept_family"], r["qname"], r["cik_override"]): r for r in out}
    for family, qname in coverage["substitutes"]:
        if (family, qname, "") in index:
            continue  # a component of the family: only its role changes below
        band = [
            r["preference_rank"] for (fam, _, cik), r in index.items()
            if fam == family and cik == "" and _is_component_rank(r["preference_rank"])
        ]
        rank = max(band, default=COMPONENT_RANK_BASE - 1) + 1
        if rank >= _IFRS_RANK_BASE:
            raise ValueError(f"{family} {qname}: component band is full")
        index[(family, qname, "")] = _mapping_row(family, qname, rank, "", IFRS_SOURCE)
    for entry in coverage["filers"]:
        cik, family, concept = entry["cik"], entry["family"], entry["concept"]
        ranking = _filer_ranking(index, cik, family)
        used = set(ranking.values())
        if entry["role"] == ROLE_SUBSTITUTE:
            band = [r for r in used if _is_component_rank(r)]
            rank = max(band, default=COMPONENT_RANK_BASE - 1) + 1
            if rank >= _IFRS_RANK_BASE:
                raise ValueError(f"{cik} {family} {concept}: component band is full")
        elif entry["position"] == "first":
            for qname, old in sorted(ranking.items(), key=lambda qr: (qr[1], qr[0])):
                new = old + 1 if old < COMPONENT_RANK_BASE else old
                if old < COMPONENT_RANK_BASE <= new:
                    raise ValueError(f"{cik} {family}: {qname} would reach the component band")
                index[(family, qname, cik)] = _mapping_row(
                    family, qname, new, cik, FAMILY_COVERAGE_SOURCE
                )
            rank = 1
        elif entry["position"] == "last":
            rank = max((r for r in used if r < COMPONENT_RANK_BASE), default=0) + 1
        else:
            after = entry["position"][1]
            if after not in ranking:
                raise ValueError(f"{cik} {family} {concept}: {after} is not a member")
            rank = ranking[after] + 1
        if not (rank < COMPONENT_RANK_BASE or rank >= _IFRS_RANK_BASE) and (
            entry["role"] == ROLE_PRIMARY
        ):
            raise ValueError(f"{cik} {family} {concept}: rank {rank} leaves the primary band")
        if rank in used and entry["position"] != "first":
            raise ValueError(f"{cik} {family} {concept}: rank {rank} is taken")
        index[(family, concept, cik)] = _mapping_row(
            family, concept, rank, cik, FAMILY_COVERAGE_SOURCE
        )
        index[(family, concept, cik)]["concept_role"] = entry["role"]
        if entry["substitution"] is not None:
            index[(family, concept, cik)]["substitution_code"] = entry["substitution"]["code"]
            index[(family, concept, cik)]["substitution_note"] = entry["substitution"]["note"]
    result = list(index.values())
    for row in result:
        sub = coverage["substitutes"].get((row["concept_family"], row["qname"]))
        if "concept_role" not in row:
            row["concept_role"] = (
                ROLE_SUBSTITUTE if sub is not None else default_role(row["preference_rank"])
            )
        if sub is not None:
            row["substitution_code"] = sub["code"]
            row["substitution_note"] = sub["note"]
        row.setdefault("substitution_code", "")
        row.setdefault("substitution_note", "")
    return result


# How far a Revenue slice family steps back from a rank it shares with Revenue
# (scripts/ddl/populate/concept_mapping_ns_expand.sql, FIX 2).
_REVENUE_SLICE_DEMOTION = {"ProductRevenue": 1, "ServiceRevenue": 2}


def demote_revenue_slices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step ProductRevenue and ServiceRevenue back where they tie with Revenue.

    The same rule as FIX 2 of scripts/ddl/populate/concept_mapping_ns_expand.sql,
    applied here so the committed rows (and ``family_member``) equal what the reload
    leaves in the database: a row of a slice family that shares its QName,
    ``cik_override`` and rank with a Revenue row moves back 1 (ProductRevenue) or 2
    (ServiceRevenue), so a total-revenue fact is a Revenue fact in the view.

    Args:
        rows: Mapping rows; changed in place.

    Returns:
        ``rows``.
    """
    revenue = {
        (r["qname"], r["cik_override"], r["preference_rank"])
        for r in rows
        if r["concept_family"] == "Revenue"
    }
    for row in rows:
        step = _REVENUE_SLICE_DEMOTION.get(row["concept_family"])
        if step and (row["qname"], row["cik_override"], row["preference_rank"]) in revenue:
            row["preference_rank"] += step
    return rows


def _filer_ranking(
    index: collections.abc.Mapping[tuple[str, str, str], collections.abc.Mapping[str, Any]],
    cik: str,
    family: str,
) -> dict[str, int]:
    """A filer's effective ranking of a family: its cik-scoped rows over the generic ones."""
    ranking: dict[str, int] = {}
    for (fam, qname, row_cik), row in index.items():
        if fam == family and row_cik == "":
            ranking.setdefault(qname, row["preference_rank"])
    for (fam, qname, row_cik), row in index.items():
        if fam == family and row_cik == cik:
            ranking[qname] = row["preference_rank"]
    return ranking


def committed_mapping_rows() -> list[dict[str, Any]]:
    """The concept_mapping rows of the committed configuration, without a database.

    The declared families, ``concept_family_overrides.yaml`` and
    ``concept_family_coverage.yaml``, as ``main`` writes them with its default
    ``--max-depth 0`` (anchors only: no taxonomy tree or occurrence counts enter).
    The data-driven ``auto_override`` pass of layer-0 is not part of it.

    Returns:
        The rows ``apply_family_coverage`` returns.
    """
    overrides = load_family_overrides()
    rows, _ = concept_mapping_rows({}, {}, overrides)
    return apply_family_coverage(rows, load_family_coverage(overrides=overrides))


_ROLE_ORDER = {ROLE_PRIMARY: 0, ROLE_SUBSTITUTE: 1, ROLE_COMPONENT: 2}


def fact_family(
    rows: collections.abc.Iterable[collections.abc.Mapping[str, Any]], cik: str, qname: str
) -> collections.abc.Mapping[str, Any] | None:
    """The one concept_mapping row that gives a fact its family in ``mv_financial_facts``.

    The rule of the view's family join (scripts/ddl/mv/10_mv_financial_facts.sql):
    within a family the filer's own row replaces the generic one; across families the
    best role (primary, substitute, component) wins, then the lowest rank, the filer's
    row breaking a tie, then the family name. So a cik-scoped ranking of one family
    never takes a fact that is a better-ranked member of another
    (docs/specs/concept-family-coverage.md).

    Args:
        rows: concept_mapping rows (``concept_family``, ``qname``, ``preference_rank``,
            ``cik_override``, optional ``concept_role``).
        cik: The fact's filer.
        qname: The fact's concept, in the form the rows hold it.

    Returns:
        The winning row, or None when no row maps the QName for the filer.
    """
    per_family: dict[str, collections.abc.Mapping[str, Any]] = {}
    for row in rows:
        row_cik = row.get("cik_override") or ""
        if row["qname"] != qname or row_cik not in ("", cik):
            continue
        family = row["concept_family"]
        if family not in per_family or (row_cik and not per_family[family].get("cik_override")):
            per_family[family] = row

    def key(row: collections.abc.Mapping[str, Any]) -> tuple[Any, ...]:
        role = row.get("concept_role") or default_role(row["preference_rank"])
        return (
            _ROLE_ORDER[role],
            row["preference_rank"],
            0 if row.get("cik_override") else 1,
            row["concept_family"],
        )

    return min(per_family.values(), key=key, default=None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--synonyms",
        default="data/storage/datasets/hifikpi/concept_synonyms.json",
        help="Path to concept_synonyms.json",
    )
    parser.add_argument(
        "--family-table",
        default="data/storage/datasets/hifikpi/concept_family_table.json",
        help="Path to concept_family_table.json",
    )
    parser.add_argument(
        "--output",
        default="concept_mapping_hifi_kpi_bootstrap.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=0,
        help=(
            "Max levels to walk below each anchor in the taxonomy tree. "
            "Default 0 = anchors-only (recommended). "
            "The HiFi-KPI tree is a calculation-arc hierarchy, not a semantic "
            "classification tree — walking it deep injects noise "
            "(e.g. SBC appears as a NetIncome child via OCF reconciliation arcs). "
            "Use 1 only if you want immediate close variants."
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=30,
        help=(
            "Maximum primaries + taxonomy descendants per concept_family; primaries "
            "are always kept and descendants fill the rest by total_occurrences. "
            "Components are extra. Set 0 for unlimited. Default: 30"
        ),
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help=(
            "Reviewed override YAML (docs/specs/concept-families-wave1.md). "
            "Default: the committed configs/concept_mapping/concept_family_overrides.yaml."
        ),
    )
    args = parser.parse_args()

    synonyms_path = Path(args.synonyms)
    family_table_path = Path(args.family_table)
    output_path = Path(args.output)

    print(f"Loading {synonyms_path} ...")
    with synonyms_path.open(encoding="utf-8") as f:
        synonyms: dict = json.load(f)
    print(f"  {len(synonyms):,} concepts in synonym index.")

    print(f"Loading {family_table_path} ...")
    with family_table_path.open(encoding="utf-8") as f:
        tree: dict = json.load(f)
    print(f"  {len(tree):,} nodes in taxonomy hierarchy.")

    overrides_path = None if args.overrides is None else Path(args.overrides)
    overrides = load_family_overrides(overrides_path)
    print(
        f"Loaded reviewed overrides from {overrides_path or DEFAULT_OVERRIDES_PATH}: "
        f"{len(overrides['families'])} family additions, "
        f"{len(overrides['filers'])} filer rankings."
    )

    print(
        f"Building family -> QName sets "
        f"(max_depth={args.max_depth}, top_k={args.top_k}) ..."
    )
    top_k = args.top_k if args.top_k > 0 else None
    rows, stats = concept_mapping_rows(
        tree, synonyms, overrides, max_depth=args.max_depth, top_k=top_k
    )

    coverage = load_family_coverage(overrides=overrides)
    rows = apply_family_coverage(rows, coverage)
    print(
        f"Applied {DEFAULT_COVERAGE_PATH.name}: {len(coverage['substitutes'])} substitutes, "
        f"{sum(len(q) for q in coverage['ifrs'].values())} IFRS rows, "
        f"{len(coverage['filers'])} cik-scoped members."
    )

    # Write CSV
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(MAPPING_CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows):,} rows to {output_path}")
    reviewed = [r for r in rows if r["source"] == REVIEWED_OVERRIDE_SOURCE]
    print(
        f"  of which {len(reviewed)} reviewed_override rows for "
        f"{len({(r['cik_override'], r['concept_family']) for r in reviewed})} "
        f"(cik, family) pairs"
    )
    print(f"\nRows per concept_family:")
    families_with_zero = []
    for family, count in stats.items():
        if count == 0:
            families_with_zero.append(family)
        else:
            print(f"  {family:<40s} {count:>3d} QNames")

    if families_with_zero:
        print(f"\nFamilies with NO HiFi-KPI coverage (no rows emitted):")
        for name in families_with_zero:
            print(f"  {name}")
        print(
            "  These families need manual rows or are DERIVED (e.g. FreeCashFlow)."
        )


if __name__ == "__main__":
    main()
