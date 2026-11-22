# concept-families-wave1 — a reviewed, validated override list for the concept families

[v2 plan §4.5](../design/XbrlRag_v2_Plan.md): the concept families in
`src/ingest/util/generate_concept_mapping.py` were built for the tech filers. Wave 1 adds 18
filers from other sectors (retail, consumer staples, pharma, rail, restaurants, defense,
parcel). Their canonical metrics (revenue, cost of revenue, operating income, net income,
assets, equity, operating cash flow, capex) must resolve to the right concept, or be absent,
never to a near-miss. The taxonomy-driven rebuild of the families is a later phase (§6.1).
Until then, additions go into a **committed, reviewed override list** that
`generate_concept_mapping` loads and validates. The hardcoded dicts stay as they are.

The retail case shows why per-filer overrides are needed. Walmart's income statement has
net sales ($674,538M, FY2025), membership and other income ($6,447M), and total revenues
($680,985M). Costco has net sales ($249,625M, FY2024), membership fees ($4,828M) and total
revenue ($254,453M). Both are expected to tag total revenues as `us-gaap:Revenues` and net
sales as `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`, undimensioned, in
every year. The per-filer rule (concept-rank-integrity C2, filer-rank-coverage) then finds
both reported in every year and keeps the global rank 1,
`RevenueFromContractWithCustomerExcludingAssessedTax`. So "Walmart's revenue" would be
answered with net sales.

**Total revenue is the reported total.** `Revenue` means the filer's own total-revenue line.
It is never computed as a sum of parts: net sales plus membership fees, franchised plus
company-operated revenues, or freight plus other revenues. A sum would double count whenever
a part is already inside another tagged line. Where the filer tags a part with the standard
total concept, a reviewed per-filer override names the concept that carries the total.

## Requirement

1. **The override file** `configs/concept_mapping/concept_family_overrides.yaml` has this
   format:

   ```yaml
   version: 1
   families:                      # additions to the generic families
     Revenue:
       primaries: [us-gaap:SomeTotalRevenueConcept]   # appended after the base primaries
       components: []                                 # appended after the base components
       reason: why this concept is the same measure
     NewFamily:                   # a family the base does not have
       label: Canonical label     # required for a new family, rejected on an existing one
       primaries: [us-gaap:X]
       reason: ...
   filers:                        # reviewed per-filer rankings
     - cik: "0000104169"
       family: Revenue
       preferred: [us-gaap:Revenues]  # ranks 1..k for this filer, in this order
       reason: ...
   ```

2. **Validation.** `validate_family_overrides(raw, primaries, components)` checks the parsed
   file against the base families. `primaries` and `components` are family →
   ordered QNames: the hardcoded `FAMILY_ANCHOR_QNAMES` and `FAMILY_COMPONENT_QNAMES` in
   production. It raises `ValueError` when any of these holds:
   - **V1 shape.** The top-level keys are not a subset of {`version`, `families`,
     `filers`}, or `version` is not 1. A family entry has a key outside {`label`,
     `primaries`, `components`, `reason`}. A filer entry has a key outside {`cik`,
     `family`, `preferred`, `reason`}, or lacks one of them.
   - **V2 QName form.** A QName is not `prefix:LocalName`, where prefix is one of
     `us-gaap`, `srt`, `dei`, `ifrs-full` (the prefixes `concept_mapping_ns_expand.sql`
     expands) and LocalName matches `[A-Za-z][A-Za-z0-9]*`. A custom extension can never
     match a fact through the expansion, so it would be a silent no-op.
   - **V3 reason.** A family or filer entry has no non-empty `reason`.
   - **V4 labels.** A family that is not in the base has no non-empty `label`, has no
     primary, or has a name that does not match `[A-Z][A-Za-z0-9]*`. A family that is in the
     base has a `label`.
   - **V5 empty entry.** A family entry adds no QName at all.
   - **V6 duplicates.** An added QName is already in the same family, either in the base
     or earlier in the file, as a primary or a component.
   - **V7 same rank in two families.** After the merge (Requirement 3), an added QName has
     the same rank in two families. Ranks are those of
     `family_ranking(merged primaries, merged components, [])`.
   - **V8 primary elsewhere.** An added **primary** is a primary of another family, in the
     base or added. Such a QName would answer two different metrics at full credit: for
     example, pretax income as operating income for a pharma filer that has no
     operating-income line.
   - **V9 bands.** A merged family has more than 49 primaries, which would reach the
     component band, or more than 50 components, which would reach the IFRS band at 100.
   - **V10 filers.**
     - `cik` is not a 10-digit, zero-padded string of digits.
     - `family` is not a merged family.
     - `preferred` is empty, repeats a QName, or names a QName that is not a merged
       **primary** of that family. A component is never promoted (concept-rank-integrity).
     - Two entries have the same (`cik`, `family`).

   Otherwise it returns the normalized overrides:

   ```python
   {"families": {name: {"label": str | None, "primaries": [...], "components": [...]}},
    "filers": {(cik, family): [preferred qnames]}}
   ```

   Missing `primaries` and `components` become `[]`, and a missing `families` or `filers`
   becomes `{}`. `label` is None for an existing family. Reasons are not carried over. A
   file that is empty, or contains only `version: 1`, normalizes to
   `{"families": {}, "filers": {}}`.
3. **Merge.** `merge_family_overrides(primaries, components, overrides)` returns
   `(merged_primaries, merged_components)`:
   - Each base family keeps its base QNames in their base order. The file's additions
     follow, in file order.
   - Families the file does not name are unchanged.
   - New families come after every base family, in file order.
   - The inputs are not modified.

   So no base QName changes rank, and the per-filer winner is unchanged for every filer
   that reports a base primary (C3).
4. **Reviewed filer ranking.** `reviewed_filer_ranking(global_ranking, preferred)` returns:
   - the preferred QNames at ranks 1..k, in the given order;
   - then every other eligible QName (rank < 50 or ≥ 100) in global-rank order, at
     k+1, k+2, …;
   - then every component (rank 50–99) at its global rank, unchanged.

   It raises `ValueError` when `preferred` is empty, repeats a QName, or names a QName that
   is not an eligible member of `global_ranking`. This is the same shape as `filer_ranking`,
   with a reviewed winner list in place of the reported-facts winner.
5. **The committed wave-1 file**:
   - validates against the base families;
   - prefers `us-gaap:Revenues` for `Revenue` for Walmart (`0000104169`) and Costco
     (`0000909832`);
   - names none of the 15 corpus filers that existed before wave 1 (C4);
   - adds no new family. A new family would also need `KNOWN_CONCEPT_FAMILIES` and the
     planner prompt, which are out of scope.

   The implementer may add further entries that the loaded wave-1 facts show are needed
   (Notes). Each entry needs a `reason`, and the implementation PR reviews it.

## Acceptance criteria

- **C1**: `validate_family_overrides` on synthetic files, with base families
  `Revenue` = [RFCWC-ExclTax, Revenues, SalesRevenueNet] (components []),
  `OperatingIncome` = [OperatingIncomeLoss], `PretaxIncome` = [IncomeLossBeforeTaxes…],
  `Debt` = [LongTermDebt] with components [DebtCurrent]:
  - A valid file normalizes exactly as in Requirement 2:
    - Revenue gains primary `RevenueFromContractWithCustomerIncludingAssessedTax`;
    - Debt gains component `ShortTermBorrowings`;
    - a new family `AcquiredInProcessResearchAndDevelopment` with label and primary
      `ResearchAndDevelopmentInProcess`;
    - one filer entry (`0000104169`, Revenue, [Revenues]).
  - Empty and `version`-only files normalize to empty overrides.
  - One `ValueError` case for each of V1–V10, starting from the valid file and breaking
    exactly one rule. Examples:
    - V8: adding the pretax-income QName to OperatingIncome;
    - V10: promoting the Debt component `DebtCurrent`;
    - V10: the 9-digit CIK `104169`.
  - Property: for generated valid files, breaking any one rule raises `ValueError`.
  - Property: the result does not depend on the order of the `filers` list, and validating
    twice gives the same result.

  Oracle: Requirement 2, applied by hand.
- **C2**: `merge_family_overrides`.
  - Point case on the C1 base and valid file:
    - Revenue → [ExclTax, Revenues, SalesRevenueNet, InclTax];
    - Debt components → [DebtCurrent, ShortTermBorrowings];
    - OperatingIncome and PretaxIncome unchanged;
    - the new family last.
  - Property (regression): for generated base families and valid additions:
    - every base QName keeps its `family_ranking` rank;
    - unnamed families are identical;
    - the inputs are unchanged;
    - the merge is deterministic.

  Oracle: Requirement 3.
- **C3**: `reviewed_filer_ranking`.
  - Point case, Walmart Revenue on the production global ranking (ExclTax 1, Revenues 2,
    SalesRevenueNet 3, SalesRevenueGoodsNet 4, SalesRevenueServicesNet 5,
    `ifrs-full:Revenue` 100) with preferred [Revenues] gives:
    - Revenues 1, ExclTax 2, SalesRevenueNet 3, SalesRevenueGoodsNet 4,
      SalesRevenueServicesNet 5, `ifrs-full:Revenue` 6.

    This is the ranking `filer_ranking` already wrote for NVIDIA and Tesla.
  - The D&A family with preferred [DepreciationAndAmortization] keeps `Depreciation` 50 and
    `AmortizationOfIntangibleAssets` 51.
  - `ValueError` for:
    - an empty `preferred`;
    - a repeated QName;
    - a component;
    - a QName not in the family.
  - Properties:
    - the result is a permutation of the input QNames, with distinct ranks;
    - the preferred QNames occupy 1..k in order;
    - components keep their global ranks;
    - the result does not depend on the order of `global_ranking`;
    - for a single preferred QName that is not the first eligible one, the result equals
      `filer_ranking(global_ranking, {q})`.

  Oracle: Requirement 4; concept-rank-integrity C2.
- **C4**: The committed file, `load_family_overrides()` with no path:
  - It validates.
  - Its `filers` map (`0000104169`, Revenue) and (`0000909832`, Revenue) to lists that start
    with `us-gaap:Revenues`.
  - No filer CIK is one of the 15 pre-wave corpus CIKs.
  - It adds no family outside the base.
  - `load_family_overrides(path)` on a synthetic YAML file written by the test gives the
    same result as `validate_family_overrides` on the parsed dict, against the module's base
    families.
  - Metamorphic (regression on the real file): merging the committed overrides into the
    production base keeps every base QName at its `family_ranking` rank. Every
    production-family winner for a filer reporting a base primary is unchanged.

  Oracle: SEC EDGAR CIKs (Walmart Inc. 104169, Costco Wholesale Corp. 909832; the corpus
  CIKs are listed in Notes); Requirement 3.

## Interface

New functions in `src/ingest/util/generate_concept_mapping.py` (stubbed in the spec PR):

- `validate_family_overrides(raw: Mapping[str, Any], primaries: Mapping[str, Sequence[str]], components: Mapping[str, Sequence[str]]) -> dict[str, Any]`
- `merge_family_overrides(primaries: Mapping[str, Sequence[str]], components: Mapping[str, Sequence[str]], overrides: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, list[str]]]`
- `reviewed_filer_ranking(global_ranking: Sequence[tuple[str, int]], preferred: Sequence[str]) -> list[tuple[str, int]]`
- `load_family_overrides(path: Path | None = None) -> dict[str, Any]`: reads the YAML and
  validates it against `FAMILY_ANCHOR_QNAMES` and `FAMILY_COMPONENT_QNAMES`. None means the
  committed `configs/concept_mapping/concept_family_overrides.yaml` under `PROJECT_ROOT`.
  A missing file raises; it is never treated as empty.

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **`generate_concept_mapping.main()`**
  - It loads the overrides (a new `--overrides` flag, defaulting to the committed file).
  - It ranks the **merged** families.
  - It appends one row per QName of each reviewed filer ranking, with
    `cik_override` = the CIK and `source` = `reviewed_override`.
- **`layer0_populator.populate_concept_mapping_overrides`** skips every (cik, family) that
  has `reviewed_override` rows. Neither the auto pass's delete nor its insert may touch
  them, or their `ns_expanded` copies. A reviewed decision is never overwritten by the
  facts-count rule.
- **`populate_concept_mapping`**, mode `replace_source`, replaces `reviewed_override` rows
  together with `hifi_kpi` when the CSV carries them.
- The new file `configs/concept_mapping/concept_family_overrides.yaml` holds the wave-1
  entries (Requirement 5).

## Notes

- **Data checked (2026-10-29).** None of the 18 wave-1 filers had facts in `xbrl_db` yet
  (the load was running), and no companyfacts JSON for them is cached under `data/`. The
  Walmart and Costco figures above come from their published 10-K income statements. Their
  tagging is expected, not observed. The implementer confirms on the loaded facts that both
  report `us-gaap:Revenues` (total) and `RevenueFromContractWithCustomerExcludingAssessedTax`
  (net sales) undimensioned, with Revenues = net sales + membership income. If the tagging
  differs, they write a dispute.
- **To check on the loaded facts, before the implementation PR** (one dry run of the
  override pass, plus `mv_concept_coverage` for the 18 CIKs):
  - Revenue for MCD (franchised + company-operated) and UNP (freight + other): is a part
    tagged undimensioned with the total-revenue concept? If so, add a filer entry.
  - CostOfRevenue for UNP, UPS and MCD: they present operating expenses, not cost of sales.
    It stays **absent**. Never map `OperatingExpenses` or `CostsAndExpenses` into it.
  - OperatingIncome for LLY and MRK: there is no operating-income line, so it stays
    **absent**. V8 blocks mapping pretax income into it.
  - Cost of revenue for ABT ("cost of products sold, excluding amortization"): a candidate
    component, never a primary.
  - Membership fees (COST) and franchised revenues (MCD) are parts of revenue. They are
    answered through a `breakdown`/member hint where the filer dimensions them, and never
    joined into `Revenue`.
- **Pre-wave corpus CIKs** (no filer entry may name them): 0000002488 AMD, 0000050863 INTC,
  0000320193 AAPL, 0000723125 MU, 0000789019 MSFT, 0001018724 AMZN, 0001045810 NVDA,
  0001046179 TSM, 0001318605 TSLA, 0001326801 META, 0001341439 ORCL, 0001652044 GOOGL,
  0001692819 VST, 0001730168 AVGO, 0001868275 CEG.
- **Observed, out of scope.** VST and CEG already tag both concepts undimensioned for the
  same period with different values:
  - CEG FY2025: `Revenues` 25,533M (operating revenues) vs
    `RevenueFromContractWithCustomerExcludingAssessedTax` 22,663M;
  - VST FY2025: 17,738M vs 17,586M.

  Their Revenue rank 1 is today the generic `RevenueFromContractWithCustomer…`. A filer entry
  for them would change existing answers and the suite's ground truth. It needs its own
  change and a suite diff, not this one.
- **Reload** (maintainer, after the implementation merges):
  1. regenerate the CSV;
  2. `populate_concept_mapping` with `replace_source`;
  3. the override pass;
  4. `concept_mapping_ns_expand.sql`;
  5. `rebuild_mvs.py`;
  6. rebuild and diff the suite.

  The diff must show no change for the 15 pre-wave filers.
