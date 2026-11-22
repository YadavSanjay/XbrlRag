# concept-rank-integrity — one meaningful rank-1 concept per family and filer

Item 4 of the accuracy plan ([§4](../design/XbrlRag_Accuracy_Plan_Oct2026.md)), widened after
measurement. Phase 3(a), "score confidence within the winning concept", is safe only once rank 1
means the right thing. Today it does not, in two ways.

1. **The generic ranking ignores the declared order.** `generate_concept_mapping.py` declares
   each family's QNames "ordered: the first entry is the single most-important QName", then
   `rank_by_occurrences` re-sorts them by how often they occur across all filings. 15 of the 55
   families end up in a different order from the declared one. Several of those put a narrower
   or different measure at rank 1:
   - `DepreciationAndAmortization` rank 1 is `AmortizationOfIntangibleAssets`;
   - `ComprehensiveIncome` rank 1 is *other* comprehensive income;
   - `SellingAndMarketing` rank 1 is marketing and advertising;
   - `IntangibleAssets` rank 1 is finite-lived intangibles only.
2. **Per-filer overrides tie with rank 1 instead of replacing it.** For each (filer, fiscal
   year), `populate_concept_mapping_overrides` promotes whichever QName has the most facts (every
   dimension and comparative counted) to rank 1 *for that filer*. The generic rank-1 row stays,
   so the filer has two rank-1 concepts with different values:
   - Oracle and Tesla: `NetIncomeLoss` and `ProfitLoss`;
   - AMD Debt: `LongTermDebtNoncurrent` and `ShortTermBorrowings`, from two fiscal years;
   - 7 of 11 filers: `Depreciation` as "D&A", because it occurs in many PP&E notes.

The lookup refuses these ties today, so they cost answers rather than correctness. But
`mv_derived_metrics` breaks the same ties by `fact_id`, so the suite's EBITDA ground truth is
built from `Depreciation` or `AmortizationOfIntangibleAssets` for several filers.

## Requirement

1. **Two tiers per family.**
   - *Primary* QNames: the family's own measure. They are ranked 1..n in their declared order.
   - *Component* QNames: parts of the measure, or a neighbouring measure. They are ranked in a
     band starting at 50, so they never tie with a primary and never earn the rank-1 credit.
   - IFRS rows keep their own band (100+), unchanged.
2. **Overrides follow meaning.** For each filer and family, the filer's rank 1 is the **first
   eligible QName, in global rank order, that the filer reports**.
   - *Eligible* means not in the component band.
   - *Reported* means at least one undimensioned fact in one of its canonical annual filings.
   - When that QName is already the global best, there is no override.
   - Otherwise the filer gets a complete ranking: the winner at 1, the other eligible QNames
     after it in global order, and components at their global ranks. Nothing ties.
   - There is one decision per (filer, family), and it replaces the filer's earlier
     `auto_override` rows for that family.

## Acceptance criteria

- **C1**: `family_ranking(primaries, components, descendants)` returns `(qname, rank)` pairs:
  - each primary in the given order at ranks 1, 2, …;
  - then each descendant not already listed, in the given order, at the next ranks;
  - then each component not already listed, at 50, 51, … in the given order.

  A QName appears once, at its first position. If primaries and descendants together exceed 49
  it raises `ValueError`, because they would reach the component band. Point case:
  `DepreciationAndAmortization` gives `DepreciationDepletionAndAmortization` 1,
  `DepreciationAndAmortization` 2, `DepreciationAmortizationAndOther` 3, `Depreciation` 50 and
  `AmortizationOfIntangibleAssets` 51. Oracle: the definition.
- **C2**: `filer_ranking(global_ranking, reported)`. *Eligible* QNames are those with rank < 50
  or ≥ 100, ordered by global rank. The *winner* is the first eligible QName in `reported`.
  - It returns `None` when no eligible QName is reported, or when the winner is the first
    eligible QName.
  - Otherwise it returns the winner at rank 1, then every other eligible QName in global order at
    ranks 2, 3, …, then every component with its global rank unchanged.

  Point cases (from the 2026-10-14 corpus):
  - Oracle `NetIncome`: it reports `NetIncomeLoss` and `ProfitLoss`, so `None`.
  - TSMC `NetIncome`: it reports only `ifrs-full:ProfitLoss` (global 100). The result is that
    QName at 1, then `us-gaap:NetIncomeLoss` 2, `us-gaap:ProfitLoss` 3,
    `us-gaap:NetIncomeLossAttributableToParent` 4 and `ifrs-full:ProfitLossAttributableToOwnersOfParent` 5.
  - AMD `Debt`: it reports `LongTermDebtNoncurrent` and `ShortTermBorrowings` (a component),
    so `LongTermDebtNoncurrent` 1, `LongTermDebt` 2, `LongTermDebtAndCapitalLeaseObligations`
    3, and the components keep 50, 51, 52.
  - Alphabet `DepreciationAndAmortization`: it reports only components, so `None`.

  Oracle: the definition.
- **C3**: Properties of `filer_ranking` when it returns a ranking:
  - it is a permutation of the input QNames, with distinct ranks;
  - every component keeps its global rank;
  - the winner is reported, and no reported eligible QName has a better global rank;
  - applying it again to its own output with the same `reported` returns `None` (idempotent);
  - the result does not depend on the order of `global_ranking` or of `reported`.

  Oracle: the definitions.
- **C4**: Properties of `family_ranking`:
  - ranks are distinct;
  - primaries occupy 1..p in order;
  - every component rank is ≥ 50;
  - every input QName appears exactly once.

  Oracle: the definition.

## Interface

New functions (stubbed in the spec PR):

- `src.ingest.util.generate_concept_mapping.family_ranking(primaries: Sequence[str], components: Sequence[str], descendants: Sequence[str]) -> list[tuple[str, int]]`
- `src.ingest.sec_edgar.ingest.layer0_populator.filer_ranking(global_ranking: Sequence[tuple[str, int]], reported: Collection[str]) -> list[tuple[str, int]] | None`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **`generate_concept_mapping.py`**
  - `main()` ranks with `family_ranking`. Declared QNames are emitted even when HiFi-KPI has
    no occurrence count for them. Descendants (only with `--max-depth` > 0) stay
    occurrence-ordered.
  - A new `FAMILY_COMPONENT_QNAMES` holds the component tier. The primary lists change as in
    the table below.
- **`layer0_populator.populate_concept_mapping_overrides`**
  - It computes *reported* per filer across its canonical annual filings, counting
    undimensioned numeric facts only.
  - It applies `filer_ranking` once per (filer, family).
  - It deletes the filer's earlier `auto_override` rows for the family, together with their
    `ns_expanded` copies.
  - It then writes the full filer ranking as `auto_override` rows.
- **`scripts/ddl/mv/34_mv_derived_metrics.sql`**: its `prim` and `inst` CTEs ignore
  component-band ranks (50–99), so a derived metric is never built from a component.
- **`scripts/ddl/populate/concept_mapping_ns_expand.sql`** is unchanged. It never updates an
  existing row, so the reload (Notes) deletes `ns_expanded` rows first.

Primary and component lists (changes only; every other family keeps its declared order and has
no components):

| Family | Primaries, in order | Components (50+) | Rank 1 before → after |
|---|---|---|---|
| DepreciationAndAmortization | DepreciationDepletionAndAmortization, DepreciationAndAmortization, DepreciationAmortizationAndOther | Depreciation, AmortizationOfIntangibleAssets | AmortizationOfIntangibleAssets → DepreciationDepletionAndAmortization |
| Debt | LongTermDebt, LongTermDebtNoncurrent, LongTermDebtAndCapitalLeaseObligations | DebtCurrent, ShortTermBorrowings, NotesPayable | LongTermDebt (unchanged) |
| SellingAndMarketing | SellingAndMarketingExpense | MarketingAndAdvertisingExpense | MarketingAndAdvertisingExpense → SellingAndMarketingExpense |
| ComprehensiveIncome | ComprehensiveIncomeNetOfTax | OtherComprehensiveIncomeLossNetOfTax | OtherComprehensiveIncomeLossNetOfTax → ComprehensiveIncomeNetOfTax |
| IntangibleAssets | IntangibleAssetsNetExcludingGoodwill, FiniteLivedIntangibleAssetsNet | IndefiniteLivedIntangibleAssetsExcludingGoodwill | FiniteLivedIntangibleAssetsNet → IntangibleAssetsNetExcludingGoodwill |
| ShareBasedCompensation | ShareBasedCompensation, AllocatedShareBasedCompensationExpense | — | AllocatedShareBasedCompensationExpense → ShareBasedCompensation (the cash-flow add-back) |
| CostOfRevenue, InterestExpense, IncomeFromContinuingOperations, IncomeFromDiscontinuedOperations, Capex | declared order | — | declared rank 1 |

## Notes

- **Measured effect per filer** (proposed rule vs today's effective rank 1, 2026-10-15). Rank-1
  ties disappear:
  - NetIncome: Oracle, Tesla and Broadcom resolve to `NetIncomeLoss`.
  - Debt: Apple, Alphabet, Meta, Microsoft, NVIDIA and Oracle resolve to `LongTermDebt`; AMD
    resolves to `LongTermDebtNoncurrent`.

  D&A resolves to a total for Apple, Amazon, Meta, Micron and NVIDIA
  (`DepreciationDepletionAndAmortization`) and Microsoft (`DepreciationAmortizationAndOther`).
  It is refused for AMD, Broadcom, Alphabet, Oracle and Tesla, which tag only components.
  Refusing is the intended outcome: today they answer "D&A" with depreciation alone, or with
  amortization alone.
- **Phase 3(a) evidence.** Of the low-confidence AMBIGUOUS lookups in run
  `eval-b99a265be9f0`:
  - about 13 would resolve correctly under within-concept scoring with a same-rank guard;
  - 4 EBITDA cases would become confident-wrong through the D&A mis-ranking, which this spec
    removes.

  Phase 3(a) follows this spec. The `Debt` family still folds current and long-term debt into
  one family ("debt current" questions), and that must be handled there.
- **The suite's ground truth moves with the ranks.** It reads the same `concept_mapping`, so the
  suite is rebuilt after the reload:
  - expected values change for share-based compensation (10 filers), comprehensive income,
    intangibles and D&A/EBITDA;
  - EBITDA cases for component-only filers leave the suite.

  The suite diff is reviewed as part of the implementation PR.
- **Reload, run by the maintainer after the implementation merges.** The implementation PR
  carries the exact PowerShell commands:
  1. regenerate the CSV;
  2. `populate_concept_mapping` with `replace_source` for `hifi_kpi`;
  3. delete `auto_override` and `ns_expanded` rows;
  4. run the overrides;
  5. run `concept_mapping_ns_expand.sql`;
  6. rebuild the MVs.
- **TSMC** keeps `ProfitLoss` as net income (maintainer decision, 2026-10-15).
