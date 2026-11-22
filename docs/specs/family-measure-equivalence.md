# family-measure-equivalence: OperatingExpenses and Debt hold only their own measure among their primaries

[concept-key-integrity](concept-key-integrity.md) fixed three families whose primaries held a
different measure from the family's own. Two more families break the same rule, "a family's
primaries are its own measure", in `FAMILY_ANCHOR_QNAMES` of
`src/ingest/util/generate_concept_mapping.py`:

1. **`OperatingExpenses`** lists `OperatingExpenses`, `OperatingCostsAndExpenses` and
   `CostsAndExpenses` as primaries. The v2 lexicon labels the family "Operating Expenses"
   (synonyms "total operating expenses", "operating costs"), the standard label of
   `us-gaap:OperatingExpenses`. `CostsAndExpenses` is total costs and expenses, which includes
   cost of revenue, so it is a larger measure. On `xbrl_db`, 36 of the 73 FY filer-years with an
   answer (10 of 21 filers) are answered with `CostsAndExpenses`. The facts-count override pass
   makes it rank 1 for each of those 10 filers. In every one of the 37 filer-years that tag
   `CostsAndExpenses`, the value equals the filer's own revenue minus operating income, which is
   total costs. In all 37 filer-years that tag `OperatingExpenses`, the value equals total costs
   minus cost of revenue.
2. **`Debt`** lists `LongTermDebt`, `LongTermDebtNoncurrent` and
   `LongTermDebtAndCapitalLeaseObligations`. The lexicon labels the family "Long-Term Debt"
   (synonyms "long-term debt", "total long-term debt"), the standard label of
   `us-gaap:LongTermDebt`, which includes current maturities. The other two primaries are
   noncurrent balances that exclude current maturities. On `xbrl_db`, 39 of 114 answered FY
   filer-years (11 of 32 filers) are answered with a noncurrent concept: 29 with
   `LongTermDebtNoncurrent` and 10 with `LongTermDebtAndCapitalLeaseObligations`. So a
   cross-filer screen or median compares totals with noncurrent balances. Where a filer tags both
   `LongTermDebt` and `LongTermDebtNoncurrent`, `LongTermDebt` is larger in 49 of 59
   filer-years. In 43 of the 59 it equals `LongTermDebtNoncurrent` plus `LongTermDebtCurrent`.

## Meaning of each QName (US GAAP taxonomy 2025)

| QName | Standard label | Definition (documentation label) |
|---|---|---|
| `OperatingExpenses` | Operating Expenses | Recurring costs of normal operations, except those clearly related to production and included in cost of sales or services. Includes SG&A. |
| `OperatingCostsAndExpenses` | Operating Costs and Expenses | The same, but **excludes** SG&A. |
| `CostsAndExpenses` | Costs and Expenses | Total costs of sales and operating expenses for the period. |
| `LongTermDebt` | Long-Term Debt | Long-term debt, net of unamortized premium (discount) and issuance cost. Excludes lease obligation. The current portion is included: the noncurrent and current elements below are its two parts. |
| `LongTermDebtNoncurrent` | Long-Term Debt, Excluding Current Maturities | Long-term debt classified as noncurrent. Excludes lease obligation. |
| `LongTermDebtCurrent` | Long-Term Debt, Current Maturities | Long-term debt classified as current. Excludes lease obligation. |
| `LongTermDebtAndCapitalLeaseObligations` | Long-Term Debt and Lease Obligation | Long-term debt and lease obligation, **classified as noncurrent**. |
| `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` | Long-Term Debt and Lease Obligation, Including Current Maturities | Long-term debt and lease obligation, including the portion classified as current. |

Note that `LongTermDebtAndCapitalLeaseObligations` is a noncurrent balance, despite having no
"Noncurrent" in its name. Its counterpart that includes current maturities is the
`...IncludingCurrentMaturities` element. No family maps that element today. In `xbrl_db`, 10 FY
filer-years tag it (4 filers), and every one of them also tags
`LongTermDebtAndCapitalLeaseObligations`. In 7 of the 10, the filer also tags `LongTermDebt`.
It includes lease obligations, which `LongTermDebt` excludes, so it is not the same measure.
`OperatingCostsAndExpenses` is tagged in no `xbrl_db` filer-year.

## Requirement

The recommended reading is used below; see "Decision for the maintainer" for the alternative.

1. **`OperatingExpenses`.** Primaries: `us-gaap:OperatingExpenses` only. Components, in order:
   `us-gaap:OperatingCostsAndExpenses`, `us-gaap:CostsAndExpenses`. They are ranked from 50, and
   no filer ranking ever promotes them. `CostsAndExpenses` is a neighbouring, larger measure.
   `OperatingCostsAndExpenses` is, by definition, operating expenses without SG&A, which is a
   part of the measure.
2. **`Debt`** is long-term debt **including current maturities**, the standard meaning of
   `us-gaap:LongTermDebt` and the lexicon's label. Primaries: `us-gaap:LongTermDebt` only.
   Components, in order: the existing `us-gaap:DebtCurrent`, `us-gaap:ShortTermBorrowings` and
   `us-gaap:NotesPayable`, so their ranks 50-52 do not change, then
   `us-gaap:LongTermDebtNoncurrent` and `us-gaap:LongTermDebtAndCapitalLeaseObligations`
   (ranks 53, 54). `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` stays
   unmapped (see D2).
3. **Filer rankings follow the bands.** The facts-count override pass (`filer_ranking` in
   `src/ingest/sec_edgar/ingest/layer0_populator.py`, unchanged) and a reviewed filer entry
   (`reviewed_filer_ranking`, unchanged) can make only a primary of the two families rank 1. With
   the new lists, a filer that reports only `CostsAndExpenses`, or only noncurrent debt, gets no
   override and no eligible concept. XbrlRag's component guard then refuses instead of
   answering with another measure.
4. **No other family changes.** Every other family's primaries and components, and their order,
   stay as they are. IFRS rows are unchanged (see Notes).

## Acceptance criteria

- **C1**: The generic ranking of `OperatingExpenses`, from
  `concept_mapping_rows(tree={}, synonyms={}, overrides=<no overrides>)`, is exactly
  `OperatingExpenses` 1, `OperatingCostsAndExpenses` 50 and `CostsAndExpenses` 51, for any
  descendant depth and cap. Oracle: the us-gaap 2025 standard labels and definitions (table
  above) and the lexicon's label for the family.
- **C2**: The generic ranking of `Debt` is exactly `LongTermDebt` 1, `DebtCurrent` 50,
  `ShortTermBorrowings` 51, `NotesPayable` 52, `LongTermDebtNoncurrent` 53 and
  `LongTermDebtAndCapitalLeaseObligations` 54, for any descendant depth and cap. Oracle: the
  us-gaap 2025 standard labels and definitions ("Long-Term Debt" vs "... Excluding Current
  Maturities" and "... classified as noncurrent") and the lexicon's label.
- **C3**: For every set of QNames a filer may report from either family,
  `filer_ranking(<generic ranking>, reported)` is None or puts one of that family's C1/C2
  primaries at rank 1, with every component at its generic rank. A filer reporting only
  components gets None. `reviewed_filer_ranking` raises `ValueError` for any component of the two
  families. Oracle: the band rule of concept-rank-integrity (requirement 2), applied by hand to
  the C1/C2 rankings.

## Interface

- No new function. Changed data: `FAMILY_ANCHOR_QNAMES` and `FAMILY_COMPONENT_QNAMES` in
  `src/ingest/util/generate_concept_mapping.py` (requirements 1-2).

## Decision for the maintainer

**D1: what "long-term debt" means (the main choice).** The spec locks reading **R**: the total
including current maturities. The alternative is reading **N**: the noncurrent balance-sheet line
"Long-term debt, net of current portion".

| Reading (Debt family) | FY filer-years whose answer changes | ... become refusals | ... answered with another concept | answered after |
|---|---|---|---|---|
| Today | – | – | – | 114 (LTD 75, LTDNC 29, LTDCLO 10) |
| **R (locked)**: primary `LongTermDebt`; both noncurrent concepts to components | 39 (11 filers) | 28 (11 filers) | 11 (4 filers) | 86 (LTD 86) |
| R+ (D2): R plus `...IncludingCurrentMaturities` as primary 2 | 39 (11 filers) | 25 (9 filers) | 14 (5 filers) | 89 (LTD 83, ICM 6) |
| N: primaries `LongTermDebtNoncurrent`, `LongTermDebtAndCapitalLeaseObligations`; `LongTermDebt` dropped | 75 (22 filers) | 8 (3 filers) | 67 (19 filers) | 106 (LTDNC 79, LTDCLO 27) |

The 11 filers whose answer changes under R are the 11 filers answered with a noncurrent concept
today. Each loses its Debt answer in some years. Four of them have `LongTermDebt` in other years,
and those 11 filer-years move to it.

Counts are filer-years with FY, current-period, undimensioned, non-extended facts in annual
filings on `xbrl_db`, measured on 2026-11-05. Each filer's ranking is simulated with the layer-0
rule: `consistent_concepts` over the filer's years, then `filer_ranking`. Under today's lists the
simulation reproduces exactly the 11 Debt and 10 OperatingExpenses `auto_override` filers in
`concept_mapping`. Ranks come from base rows (`source <> 'ns_expanded'`).

- **Why R.** The lexicon label "Long-Term Debt" and the synonym "total long-term debt" are the
  standard label of `us-gaap:LongTermDebt`. Debt ratios (`DebtToEquity`, `NetDebt`, ROIC in
  `mv_derived_metrics`) usually take total debt including the current portion. R refuses
  28 filer-years whose filer tags only a noncurrent line, rather than mixing measures.
- **Cost of R.** 28 filer-years (11 filers) lose their Debt answer, and their `DebtToEquity`,
  `NetDebt` and ROIC rows disappear with it.
- **Alternative to recover those answers.** Add a separate family, `LongTermDebtNoncurrent`
  (primaries `LongTermDebtNoncurrent`, `LongTermDebtAndCapitalLeaseObligations`; lexicon label
  "Long-Term Debt, Excluding Current Maturities"). It would answer in 106 filer-years, and a
  question naming the noncurrent line would get its own measure. A new family needs a lexicon
  entry, a plan-validator family name and an IFRS mapping decision. It is additive and can follow
  this spec, in a reviewed data change or a spec of its own.
- **If N is chosen instead,** C2 and C3 must be rewritten for N on this branch before the spec
  PR merges. In that case `LongTermDebt` should become the separate family above (as
  "long-term debt including current maturities"), not a component, because it is the larger
  measure.

**D2: `...IncludingCurrentMaturities` as a second primary (R+).** It is a total that includes
current maturities, the same reading as `LongTermDebt`, but it also includes lease obligations,
which `LongTermDebt` excludes. As primary 2 it would answer 3 filer-years (2 filers) that R
refuses. It does not answer only where `LongTermDebt` is missing, though. The facts-count
override promotes the concept a filer reports in the most years, so for one filer it would
displace `LongTermDebt` in 3 filer-years where both are tagged, and a screen would again mix a
lease-inclusive total with totals that exclude leases. The spec locks R, which leaves the element
unmapped. Choosing R+ means adding it to C2's ranking at 2 and to C3's primaries on this branch
before the spec PR merges.

**D3: `OperatingCostsAndExpenses` to components.** By definition it excludes SG&A. It is tagged
nowhere in `xbrl_db`, so moving it changes no answer today. It prevents a future filer from being
answered with opex minus SG&A. The spec locks the move.

### Impact on OperatingExpenses (requirement 1)

| | FY filer-years whose concept changes | ... and become refusals | answered after |
|---|---|---|---|
| Today | – | – | 73 (OE 37, CAE 36) |
| Requirement 1 | 36 (10 filers) | 36 (10 filers) | 37 (OE 37) |

No filer that tags `CostsAndExpenses` also tags `OperatingExpenses`, except one filer-year, so
the 36 filer-years become refusals instead of moving to another concept. The 10 filers'
`auto_override` rows (`CostsAndExpenses` at rank 1) are removed by the regenerated mapping.

### Existing v2 intents whose keys change

Question text stays as it is; only keys and inputs change, in the frozen dev and validation
suites (`data/eval/v2_frozen`). Eight intents touch the two families, three of them in both
splits:

- `int-129c2e523971` (dev, lookup, UPS FY2025 "Operating Expenses"): keyed with
  `CostsAndExpenses`. Its filer-year no longer has an eligible concept, so concept-key-integrity's
  `drop_component_rows` leaves it out of a rebuild. The intent drops out; it is not reworded.
- `int-aadc983a27a0` (dev + validation, screen, AMD vs covered companies, FY2025 operating
  expenses): 10 of its 21 inputs are `CostsAndExpenses`. They leave the comparison set.
- `int-f3ebd8edcbe2` (dev, compute, retailers' combined operating costs FY2025): 1 of 2 inputs is
  `CostsAndExpenses`.
- `int-60f310828b3f` (dev, screen, QCOM rank on long-term debt FY2025): 11 of 31 inputs are
  noncurrent (8 LTDNC, 3 LTDCLO).
- `int-addfb630e7ce` (dev + validation, compute, semiconductors' combined long-term debt FY2025):
  2 of 7 inputs are noncurrent.
- `int-ea9b6382ce38` (dev + validation, screen, top 3 retailers by long-term debt FY2023): 1 of 4
  inputs is noncurrent.
- `int-556d7f04ac46` (validation, compute, median total long-term debt FY2022): 5 of 14 inputs are
  noncurrent.
- `int-a6fa57305fc3` (validation, compute, median long-term debt FY2023): 10 of 31 inputs are
  noncurrent.

Under R, each noncurrent input either moves to the filer's `LongTermDebt` for that year or
leaves the set. Under N, the `LongTermDebt` inputs change
instead.

## Notes

- **Oracles.** The US GAAP Financial Reporting Taxonomy 2025: standard labels from
  `us-gaap-lab-2025.xml` and definitions from the documentation linkbase `us-gaap-doc-2025.xml`
  (FASB, `https://xbrl.fasb.org/us-gaap/2025/elts/`, read from the local Arelle cache). The band
  rule comes from [concept-rank-integrity](concept-rank-integrity.md), and the family labels from
  `configs/eval/v2/lexicon.yaml`. The tests compute rankings through `concept_mapping_rows` with
  no taxonomy tree, no occurrence counts and no overrides, so only the declared lists decide
  them.
- **Measurement.** All counts above come from read-only SELECTs on `xbrl_db`
  (`mv_financial_facts`, `concept_mapping` base rows), on 2026-11-05. The empirical checks of what
  each concept measures use the filer's own `Revenues`/`RevenueFromContract...`,
  `OperatingIncomeLoss`, `GrossProfit`/`CostOfRevenue` and `LongTermDebtCurrent`, within 0.5%. No
  test reads them. `xbrl_heldout` was not queried.
- **Rank drift.** The `ns_expanded` rows of `concept_mapping` currently drift from the base rows
  (a separate spec, ns-expand-rank-sync). The counts here use base rows only.
- **IFRS (out of scope).** `ifrs-full:Borrowings` (100) and `ifrs-full:NoncurrentBorrowings` (101)
  in the Debt family's IFRS band also mix a total with a noncurrent balance, and `Borrowings`
  includes short-term borrowings. No `xbrl_db` filer-year reports either one undimensioned, so
  this spec leaves the IFRS band alone. A later IFRS wave should apply the same rule.
- **Locked tests elsewhere.** concept-key-integrity, concept-rank-integrity and
  concept-families-wave1 stay green with this change: concept-rank-integrity's `DEBT_GLOBAL` is a
  hand-written ranking passed to `filer_ranking`, and none of their tests reads either family's
  declared lists. `test/unit/test_concept_mapping_ranks.py::test_reviewed_rank_ones` expects
  `Debt` rank 1 `LongTermDebt`, which R keeps. It would need updating only under reading N.
- **Not in spec tests.** After the implementation merges, the rollout regenerates the concept
  mapping and reloads `concept_mapping`, which removes the 10 `CostsAndExpenses` and 11 noncurrent
  Debt `auto_override` filers. It then rebuilds the materialized views on `xbrl_db` (and on
  `xbrl_heldout` with the maintainer's OK), rebuilds and diffs the v1 `sql_derived` suite, and
  rebuilds the frozen v2 dev and validation suites. Their new hashes go into the
  pre-registration amendment.
- **Lexicon wording** goes in a separate reviewed data change, not this spec. "Operating costs"
  as an OperatingExpenses synonym invites the total-costs reading. "Long-Term Debt" could say
  "including current maturities" once D1 is settled.
- **Effect on XbrlRag.** A filer that tags only total costs and expenses, or only noncurrent
  long-term debt, now gets a refusal from the lookup's component guard for "operating expenses"
  or "long-term debt", instead of a different measure presented as the one asked for. Screens
  (`screen-concept-rank`) and `mv_derived_metrics` already exclude the component band, so they
  stop mixing the measures.
