# concept-key-integrity — a family's primaries are its own measure, and suite keys never come from a component

A scan of the frozen v2 dev and validation suites (2026-11-02, 300 intents) found 12 intents whose
answer key is a different measure from the one the question names, while the same filer reports
the named measure for the same period. Two causes are generic, and this spec fixes them:

1. **Three families hold a different measure among their primaries** in
   `src/ingest/util/generate_concept_mapping.py`. The fact store ranks it like the family's own
   measure, so both the suite's keys and XbrlRag's lookups can take it:
   - `SharesOutstanding` lists `CommonStockSharesIssued`. Issued shares include treasury shares,
     so they are not outstanding shares: Procter & Gamble FY2023-25 is keyed at about 4.0 billion
     issued against about 2.35 billion outstanding.
   - `AccountsPayable` lists `AccountsPayableAndAccruedLiabilitiesCurrent`, a broader measure, and
     not `AccountsPayableTradeCurrent`, which is accounts payable. Coca-Cola FY2025 is keyed at
     $14.813B (payables and accrued liabilities); its trade payables are $5.649B.
   - `OtherNonOperatingIncome` ranks `NonoperatingIncomeExpense` first. That element is total
     non-operating income and expense; the family, as the v2 lexicon names it ("Other
     Nonoperating Income (Expense)"), is `OtherNonoperatingIncomeExpense`. Amazon FY2024 is keyed
     at +$21M (the total) against −$2.25B (other income and expense).
2. **The v2 suite keys from the component band.** `INVENTORY_SQL` in
   `src/eval/suites/v2_sources.py` takes each filer-year's lowest-ranked concept per family, and a
   filer that reports no primary gets its component (concept-rank-integrity: ranks 50-99, "parts of
   the measure, or a neighbouring measure"). XbrlRag never answers from that band: the lookup's
   component guard refuses, `screen-concept-rank` drops it, and `mv_derived_metrics` excludes it.
   So the suite expects an answer XbrlRag must refuse: AMD "sales and marketing" is keyed with
   `MarketingAndAdvertisingExpense` (two intents), and Oracle "total debt" with `DebtCurrent`.

3. **Three filers tag a part with a concept that outranks their total**, and need reviewed
   filer entries in `configs/concept_mapping/concept_family_overrides.yaml`:
   - Vistra and Constellation tag their "Operating revenues" total as `us-gaap:Revenues` and the
     part from contracts with customers as `RevenueFromContractWithCustomerExcludingAssessedTax`,
     which ranks first (Vistra FY2023 14,779M vs 13,804M; Constellation FY2025 25,533M vs
     22,663M). concept-families-wave1 noted both and left them out of scope, because its file
     may not name a filer that existed before wave 1.
   - McDonald's tags its cash-flow "Depreciation and amortization" as
     `us-gaap:DepreciationAndAmortization` (FY2025 2,199M) and a much smaller amount as
     `DepreciationDepletionAndAmortization` (457M), which ranks first.

The other scan findings are wording (debt labels, "cash", "receivables"): a reviewed change to
`configs/eval/v2/lexicon.yaml`, not part of this spec.

## Requirement

1. **`SharesOutstanding`.** Primaries: `us-gaap:CommonStockSharesOutstanding` only.
   `us-gaap:CommonStockSharesIssued` moves to the family's components, so it is ranked from 50.
2. **`AccountsPayable`.** Primaries, in order: `us-gaap:AccountsPayableCurrent`,
   `us-gaap:AccountsPayableTradeCurrent`. `us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent`
   moves to the family's components.
3. **`OtherNonOperatingIncome`.** Primaries, in order: `us-gaap:OtherNonoperatingIncomeExpense`,
   `us-gaap:NonoperatingIncomeExpense`, `us-gaap:OtherNonoperatingIncome`,
   `us-gaap:OtherNonoperatingExpense`. `NonoperatingIncomeExpense` stays a primary, because many
   filers tag their "Other income (expense), net" line with it; it answers only when the filer
   does not report `OtherNonoperatingIncomeExpense`.
4. **No other family changes.** Every other family's primaries and components, and their order,
   stay as they are.
5. **The suite never keys from the component band.** `INVENTORY_SQL` returns
   `concept_pref_rank` as a seventh column. `read_corpus_inventory` passes its rows through
   `drop_component_rows`, which leaves out every row whose rank is in the component band
   (50 ≤ rank < 100) and returns the other rows, in order, as the six-column rows
   `inventory_from_rows` takes. A filer-year whose best concept for a family is a component
   therefore has no entry for that metric, and no intent is built on it. Ranks below 50 and the
   IFRS band (100 and above) are kept. `inventory_from_rows` is unchanged.
6. **Reviewed filer entries.** The committed override file adds, each with a `reason` citing the
   filer's figures:
   - Vistra (`0001692819`) and Constellation (`0001868275`), family `Revenue`, preferred
     `[us-gaap:Revenues]`;
   - McDonald's (`0000063908`), family `DepreciationAndAmortization`, preferred
     `[us-gaap:DepreciationAndAmortization]`.

   concept-families-wave1 requirement 5 ("names none of the 15 corpus filers that existed
   before wave 1") now allows exactly the two `Revenue` entries for Vistra and Constellation;
   any other entry for a pre-wave filer is still refused by its tests. Its two committed-file
   tests are updated accordingly in this spec's PR. The file still adds no family.

## Acceptance criteria

- **C1**: The generic ranking of `SharesOutstanding`, from
  `concept_mapping_rows(tree={}, synonyms={}, overrides=<no overrides>)`, is exactly
  `CommonStockSharesOutstanding` 1 and `CommonStockSharesIssued` 50. Oracle: the us-gaap
  definitions (issued shares include shares held in treasury; outstanding shares do not).
- **C2**: The generic ranking of `AccountsPayable` is exactly `AccountsPayableCurrent` 1,
  `AccountsPayableTradeCurrent` 2 and `AccountsPayableAndAccruedLiabilitiesCurrent` 50. A filer
  ranking (`reviewed_filer_ranking`) can never promote
  `AccountsPayableAndAccruedLiabilitiesCurrent`. Oracle: the us-gaap definitions and standard
  labels ("Accounts Payable, Trade, Current" vs "Accounts Payable and Accrued Liabilities,
  Current").
- **C3**: The generic ranking of `OtherNonOperatingIncome` is exactly
  `OtherNonoperatingIncomeExpense` 1, `NonoperatingIncomeExpense` 2, `OtherNonoperatingIncome` 3,
  `OtherNonoperatingExpense` 4, with no component. Oracle: the us-gaap standard labels ("Other
  Nonoperating Income (Expense)" vs "Nonoperating Income (Expense)") and the lexicon's label for
  the family.
- **C4**: `drop_component_rows` keeps exactly the rows with rank < 50 or rank ≥ 100, in their
  input order, each as its first six columns; it never changes a kept row's values.
  `read_corpus_inventory` over a connection whose cursor returns seven-column rows builds the
  inventory of the kept rows only. Oracle: the band definition of concept-rank-integrity
  (requirement 1), applied by hand.
- **C5**: `load_family_overrides()` on the committed file holds the three entries of
  requirement 6, and `concept_mapping_rows` with it gives each of those filers rank 1 for the
  preferred QName in that family, with every component at its global rank. The generic rankings
  are the same with and without the committed file's filer entries. Oracle: the filers' 10-K
  figures quoted in the Notes (income statement totals and cash-flow add-back), and
  concept-families-wave1 requirement 4.

## Interface

- `src.eval.suites.v2_sources.drop_component_rows(rows: Iterable[Sequence[Any]]) -> list[tuple[Any, ...]]`
- Changed data: `FAMILY_ANCHOR_QNAMES` and `FAMILY_COMPONENT_QNAMES` in
  `src/ingest/util/generate_concept_mapping.py` (requirements 1-4); `INVENTORY_SQL` and
  `read_corpus_inventory` in `src/eval/suites/v2_sources.py` (requirement 5).

## Notes

- Oracles: the US GAAP taxonomy's standard labels and definitions for the QNames named above, and
  the band rule of [concept-rank-integrity](concept-rank-integrity.md). The tests compute rankings
  through `concept_mapping_rows` with no taxonomy tree, no occurrence counts and no overrides, so
  only the declared lists decide them.
- Values quoted above come from SEC companyfacts in the local cache, read for the scan; no test
  reads them. Full figures for requirement 6: Vistra `Revenues` 14,779M / 17,224M / 17,738M and
  contract revenue 13,804M / 14,761M / 17,586M (FY2023-25); Constellation 24,918M / 23,568M /
  25,533M and 20,841M / 18,964M / 22,663M; McDonald's `DepreciationAndAmortization` 1,978.2M /
  2,097M / 2,199M and `DepreciationDepletionAndAmortization` 381.7M / 447M / 457M.
- Changed data also includes `configs/concept_mapping/concept_family_overrides.yaml`
  (requirement 6), which the implementer edits.
- **Not in spec tests.** After the implementation merges, the rollout regenerates the concept
  mapping, reloads `concept_mapping`, rebuilds the materialized views on `xbrl_db` (and on
  `xbrl_heldout` with the maintainer's OK), rebuilds and diffs the v1 `sql_derived` suite, and
  rebuilds the frozen v2 dev and validation suites. Their new hashes go into the pre-registration
  amendment. The filer overrides and lexicon wording go in a separate reviewed change.
- Effect on XbrlRag: a filer that reports only `CommonStockSharesIssued` or only
  `AccountsPayableAndAccruedLiabilitiesCurrent` now gets a refusal from the lookup's component
  guard, instead of a different measure presented as the asked one.
