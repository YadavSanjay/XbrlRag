# debt-ifrs-elements — the Debt rule matches the IFRS borrowing elements by definition

Content route 1 of [statement-rules-by-element](statement-rules-by-element.md) (requirement 6,
"Debt by element (TSM)"; decision 7: no judging sample) and K3 of the v2 status. Follows
[key-definitions](key-definitions.md) (the S4 `Debt` definition and its locked rule) and
[key-currency-and-definition](key-currency-and-definition.md) (a non-USD filer is never ranked
in a screen).

**The defect.** S4 Debt is "every borrowing line the balance sheet presents, summed"
(key-definitions requirement 1). TSM, the corpus's one IFRS filer, presents its current
borrowings as "Long-term liabilities - current portion", tagged
`ifrs-full:CurrentPortionOfLongtermBorrowings`. The label names neither debt nor borrowings,
so the label rule leaves it out (rightly, as a label: key-definitions review point "TSM current
debt"), and TSM's Debt key omits it every year: FY2024 NT$958,428.9M instead of
NT$1,018,286.8M, FY2025 NT$896,062.0M instead of NT$1,032,987.7M. The element's definition,
"the current portion of non-current borrowings", *is* a borrowing. statement-rules-by-element
added the `elements` field so a rule can say so; this change writes the `Debt` list.

**The trap.** Listing TSM's one element would be a rule written from one filer. The list is
the IFRS taxonomy's borrowing elements as a whole, chosen from their definitions, with every
borrowing-adjacent element the taxonomy holds either listed or left out for a stated reason
(requirement 2); a dry run over 723 out-of-corpus IFRS filers checks it before the corpus is
read (Notes).

## Requirement

Terms are those of statement-rules-by-element (`elements`, element match, `line_is_candidate`)
and key-definitions (`debt_key`, the S4 Debt definition, its locked rule).

### 1. The rule

`configs/eval/v2/statement_lines.yaml`'s `Debt` entry loads equal to the locked copy
`test/spec/debt-ifrs-elements/fixtures/statement_lines_debt.yaml`: key-definitions' locked
`Debt` rule unchanged in every field, plus `elements` = the 29 elements of requirement 2, in the
copy's order. `DeferredRevenue` and every other family are unchanged. No family gains
`strip_qualifiers`.

### 2. The element list (from the IFRS taxonomy definitions)

Source: IFRS Accounting Standards Taxonomy 2024-03-27, standard label (role `label`),
documentation (role `documentation`) and core schema (Notes, "Taxonomy excerpt").

**Listed** — every non-abstract instant credit monetary element whose documentation states
an amount of borrowings, loans received, bonds, notes and debentures, commercial paper or debt
instruments issued by the entity; total, current, current-portion and non-current-portion
forms alike, since a balance sheet presents each as its own line:

| Group | Elements (`ifrs-full:`) | Definition (IFRS taxonomy 2024) |
|---|---|---|
| Borrowings | `Borrowings` | "The amount of outstanding funds that the entity is obligated to repay." |
| | `ShorttermBorrowings` | "The amount of current borrowings." |
| | `CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings` | "The amount of current borrowings and current portion of non-current borrowings." |
| | `CurrentPortionOfLongtermBorrowings` | "The current portion of non-current borrowings." |
| | `LongtermBorrowings` | "The non-current portion of non-current borrowings." |
| | `OtherBorrowings`, `OtherCurrentBorrowingsAndCurrentPortionOfOtherNoncurrentBorrowings`, `NoncurrentPortionOfOtherNoncurrentBorrowings` | "The amount of borrowings that the entity does not separately disclose …"; its current and non-current parts |
| Loans received | `LoansReceived`, `CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived`, `NoncurrentPortionOfNoncurrentLoansReceived` | "The amount of loans received."; its parts |
| | `SecuredBankLoansReceived`, `CurrentSecuredBankLoansReceivedAndCurrentPortionOfNoncurrentSecuredBankLoansReceived`, `NoncurrentPortionOfNoncurrentSecuredBankLoansReceived` | "The amount of loans received from banks that have been secured by collateral."; its parts |
| | `UnsecuredBankLoansReceived`, `CurrentUnsecuredBankLoansReceivedAndCurrentPortionOfNoncurrentUnsecuredBankLoansReceived`, `NoncurrentPortionOfNoncurrentUnsecuredBankLoansReceived` | "The amount of loans received from banks that have not been secured by collateral."; its parts |
| Debt securities issued | `BondsIssued`, `CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued`, `NoncurrentPortionOfNoncurrentBondsIssued` | "The amount of bonds issued by the entity."; its parts |
| | `NotesAndDebenturesIssued`, `CurrentNotesAndDebenturesIssuedAndCurrentPortionOfNoncurrentNotesAndDebenturesIssued`, `NoncurrentPortionOfNoncurrentNotesAndDebenturesIssued` | "The amount of notes and debentures issued by the entity."; its parts |
| | `CommercialPapersIssued`, `CurrentCommercialPapersIssuedAndCurrentPortionOfNoncurrentCommercialPapersIssued`, `NoncurrentPortionOfNoncurrentCommercialPapersIssued` | "The amount of commercial paper issued by the entity."; its parts |
| | `DebtSecurities` ("Debt instruments issued"), `CurrentDebtInstrumentsIssued`, `NoncurrentDebtInstrumentsIssued` | "The amount of instruments issued by the entity that represent indebtedness."; its parts |

Every listed element is declared in the 2022, 2023, 2024 and 2025 core schemas, so TSM's
`ifrs/2021`-`ifrs/2025` filings name the same local names.

**Left out**, by definition (each in the excerpt with its reason):

| Reason | Elements (`ifrs-full:`) | Why |
|---|---|---|
| `lease` | `LeaseLiabilities`, `CurrentLeaseLiabilities`, `NoncurrentLeaseLiabilities` | key-definitions: finance leases only inside a line the filer calls debt, operating leases never; IFRS 16 has one lease model, so a lease element is never a borrowing by itself |
| `cash_component` | `BankOverdraftsClassifiedAsCashEquivalents` | an overdraft netted inside cash and cash equivalents (IAS 7.8), not a liability line |
| `net_of_assets` | `NetDebt` | debt net of cash, a different measure |
| `measurement_category` | `DebtInstrumentsIssuedAtAmortisedCost`, `DebtInstrumentsIssuedAtFairValueThroughProfitOrLossDesignatedUponInitialRecognitionOrSubsequently` | IFRS 9 categories of the same instruments, disclosed in the financial-instruments note (2024 and 2025 schemas only) |
| `undiscounted_cash_flows` | `BondsIssuedUndiscountedCashFlows`, `BankBorrowingsUndiscountedCashFlows` | maturity-analysis cash flows, not a balance |
| `business_combination` | `BorrowingsRecognisedAsOfAcquisitionDate` | an acquisition-date disclosure |
| `not_drawn` | `UndrawnBorrowingFacilities`, `GrossLoanCommitments` | facilities and commitments not drawn |
| `subset_disclosure` | `LoansPayableInDefault`, `LoansPayableInBreachWhichPermittedLenderToDemandAcceleratedRepayment` | IFRS 7.18-19 subsets of loans already presented on their own lines |

Not considered: deposits (a bank's deposits from customers or banks are its funding, not
borrowings in key-definitions' sense; its exclusion of deposit lines stands), derivative and
other financial liabilities (broader than borrowings), and every us-gaap element (decision 2).

### 3. Generality

- A line on a listed element is a Debt line whatever its label (statement-rules-by-element
  requirement 2); a line on a left-out element, or on an extension or us-gaap element with
  the same local name, is a Debt line only through the label rule, as today.
- The list names only `ifrs-full:` elements, so no us-gaap filing's selection changes.
- The list is not a `Debt` substitute anywhere (rule or committed concept mapping), so
  `merge_mapping_substitutes` accepts it.

### 4. What changes downstream (implementation, not exercised by these tests)

- **Keys.** TSM's Debt keys (`debt_key` through `KeyWitness`) gain the current portion, every
  year (Notes, "Corpus dry run"). No other corpus filer moves.
- **Answers.** The system's S4 Debt lookup reads the same live rule
  (`src/retriever/xbrl/defined_measure_rows.py` loads `statement_lines.yaml`; concepts carry
  the `ifrs-full:` prefix), so a TSM Debt answer that reaches the defined measure gains the
  same line. Today TSM has no Debt family row (below), so no stored answer moves.
- **Screens.** TSM is excluded from every USD screen (key-currency-and-definition K-R1); a
  Debt screen does not change.

### 5. TSM Debt intents stay off in this change

They are off today because the corpus inventory has no TSM Debt row: `read_corpus_inventory`
reads `mv_financial_facts`, whose `Debt` family maps the IFRS elements `ifrs-full:Borrowings`
and `ifrs-full:NoncurrentBorrowings` (`configs/concept_mapping/concept_family_coverage.yaml`,
`ifrs:` section). TSM tags neither, and **`NoncurrentBorrowings` is not an element of the IFRS
taxonomy 2022-2025** (it is declared in none of the four core schemas). Checked read-only on
`xbrl_db`: no `mv_financial_facts` row of TSM has family `Debt`.

Turning them on takes, in order (each its own change; decision 3):

1. this change (the key is right before any case is keyed);
2. an inventory row for TSM Debt: a concept-mapping change for the IFRS `Debt` row (replace
   the non-existent `NoncurrentBorrowings`), or an inventory rule that enumerates a defined
   family's filer-years from its defined key rather than from a mapped element; then the
   concept reload of guide 04 §4.6.1 so `mv_financial_facts` carries the row;
3. K6, the dev re-freeze: the dry rebuild adds TSM Debt intents (lookups FY2021-FY2025 in TWD,
   trends, compute shapes; its peer-relative intents drop as `subject_not_ranked`, as for
   every non-USD member) and the suite diff, split assignment by intent hash and the
   pre-registration amendment record them. Expected keys (NT$ millions, Notes): FY2021
   732,867.7; FY2022 858,410.3; FY2023 927,576.1; FY2024 1,018,286.8; FY2025 1,032,987.7.

## Acceptance criteria

All figures are hand-checked against the rendered primary document (Notes, "Hand checks").

- **C1** (the list): the live `Debt` rule's `elements` are exactly the 29 listed elements in
  the copy's order; its other fields are key-definitions' locked rule; it loads equal to the
  locked copy, and `DeferredRevenue` is unchanged; each listed element is, in the excerpt, a
  non-abstract instant credit monetary item of the 2022-2025 schemas whose documentation names
  a borrowing and no lease, undiscounted flow, undrawn facility or net debt; each of the 14
  left-out elements carries a stated reason its documentation shows and is not listed; the
  excerpt's sources are the pinned files. Property: no listed element is a `Debt` substitute
  in the rule or the committed mapping, and the merge keeps the list. Oracle: the IFRS
  taxonomy 2024 (excerpt); requirements 1-2.
- **C2** (TSM): the live `Debt` rule selects TSM FY2024 lines 38, 42, 43: NT$59,857.9M +
  926,604.5M + 31,824.4M = 1,018,286.8M (2023: 9,293.3 + 913,899.8 + 4,383.0 = 927,576.1;
  US$ 1,825.5 + 28,258.7 + 970.5 = 31,054.7), rel 1e-12; `debt_key` is `defined`, basis
  `presented`, line 38 `current`, 42/43 `noncurrent`, no reading, key `s4:debt_total` in TWD
  from companyfacts; key-definitions' rule without `elements` keys 958,428.9M, the new rule
  adds exactly line 38. Property: relabelling the three lines to any non-total text keeps
  1,018,286.8M; the lease line (45) never joins by element. Oracle: TSM's printed balance
  sheet; key-definitions requirement 2.
- **C3** (generality): on a synthetic IFRS balance sheet with every listed and every left-out
  element on a neutral label, exactly the 29 listed lines are selected (sum 29,406); an
  extension or us-gaap line with a listed local name is not; property: for any mix of listed
  and left-out elements on any of six labels, in any order, the selection is the listed lines
  and the key their hand sum; metamorphic: on key-definitions' eight us-gaap filings the live
  rule selects exactly what key-definitions' rule selects. Oracle: requirements 2-3; hand
  sums.

## Interface

No new function and no signature change. The implementation changes one file:
`configs/eval/v2/statement_lines.yaml` (requirement 1: the `elements` list added to `Debt`,
with a comment naming the taxonomy and this spec). Follow-ups the implementer does **not** do
here: the concept-mapping row and the inventory (requirement 5 step 2), K6.

## Notes

### Fixtures (`test/spec/debt-ifrs-elements/fixtures/`)

- `fsds/2025q2/*.tsv`: byte copies of statement-rules-by-element's TSM rows (the `sub` row,
  every BS `pre` row, the consolidated `num` rows of those lines, their `tag` rows).
- `companyfacts_excerpt.json`: SEC companyfacts of TSM (CIK 0001046179, local copy
  `data/sec_companyfacts/CIK0001046179.json`), entries of accession 0001193125-25-083423 only,
  for `CurrentPortionOfLongtermBorrowings`, `NoncurrentPortionOfNoncurrentBondsIssued`,
  `LongtermBorrowings` and `NoncurrentLeaseLiabilities` (TWD 2023-12-31 and 2024-12-31, USD
  2024-12-31).
- `taxonomy_excerpt_ifrs.json`: requirement 2's 29 listed and 14 left-out elements with
  standard label, documentation, period type, balance, type, abstract flag and the core
  schemas (2022, 2023, 2024, 2025) that declare them.
- `statement_lines_debt.yaml`: the locked `Debt` and `DeferredRevenue` rules.
- `provenance.json`: every file's SHA-256 and the sources.

### Taxonomy excerpt

Read from the official IFRS Foundation files (fetched 2026-11-11; the core schema is
byte-identical to the local Arelle cache copy):

| File | URL | SHA-256 |
|---|---|---|
| label linkbase | `https://xbrl.ifrs.org/taxonomy/2024-03-27/full_ifrs/labels/lab_full_ifrs-en_2024-03-27.xml` | `c43bb860e64d7a9fbb9995c34c7926be80b213dedcef17c11620c006dbe05ae1` |
| documentation linkbase | `https://xbrl.ifrs.org/taxonomy/2024-03-27/full_ifrs/labels/doc_full_ifrs-en_2024-03-27.xml` | `a825ded2c3daccd523e54d0272c6f42da13da449668127dbf235b82bd5ba3d6b` |
| core schema | `https://xbrl.ifrs.org/taxonomy/2024-03-27/full_ifrs/full_ifrs-cor_2024-03-27.xsd` | `50f853021fd4268e4944ef01bc21aad84cb5f8e69d85fcb2b0ceec3951fe384c` |

The 2022-03-24, 2023-03-23 and 2025-03-27 core schemas (local Arelle cache) give the
`taxonomies` column. The candidates were every non-abstract instant credit monetary element
whose name or standard label contains borrowing, bond, loan, debt, commercial paper,
debenture, overdraft or notes (43), plus the three lease-liability elements: 29 listed, 11
left out with a reason (the excerpt also holds the three lease elements), and 3 that name
no liability of the entity to lenders and are not in the excerpt
(`LoansAndAdvancesAtAmortisedCostAllowanceForExpectedCreditLosses`, an allowance on loans the
entity granted; `ProvisionsForDoubtfulDebtsRelatedToOutstandingBalancesOfRelatedPartyTransaction`;
`ExposureToCreditRiskOnLoanCommitmentsAndFinancialGuaranteeContracts`).

### Hand checks

TSM FY2024 20-F (`data/storage/sec_edgar/TSM/2024/20-F/d896993d20f.htm`), consolidated
balance sheet, as statement-rules-by-element recorded them: "Long-term liabilities - current
portion" NT$9,293.3 / 59,857.9 / US$1,825.5; "Bonds payable" 913,899.8 / 926,604.5 /
US$28,258.7; "Long-term bank loans" 4,383.0 / 31,824.4 / US$970.5; "Lease liabilities"
(non-current) 28,681.8 / 28,755.3 / US$877.0. The companyfacts excerpt carries the same values.

### Out-of-corpus dry run (before the corpus; read-only)

Every 20-F and 40-F in the raw FSDS quarters 2023q1-2025q4 that presents an `ifrs/` balance-
sheet line, minus the corpus CIKs: 2,751 filing-units (filing × reporting currency) of 723
filers. These are foreign private issuers filing 20-F/40-F; no held-out filer is among them
(the held-out sets are drawn from 10-K filers) and none is in a judging sample (US 10-K filers
only). The live
`Debt` rule against the rule with the list:

- 405 filing-units of 178 filers gain lines; **none loses a line**;
- the 190 distinct added labels are borrowing lines ("Interest-bearing loans and borrowings",
  "Current portion of long-term debt", "Debentures", "Credit facilities", "Loans from banks",
  "Debt securities in issue", "Convertible notes", …), plus a few the filer tagged on a
  borrowing element with a generic or odd label ("Non-current portion", "Other long-term
  liabilities", "Conversion option", "Convertible SAFE"); one filer's "Security deposits
  received" is tagged `NoncurrentPortionOfNoncurrentSecuredBankLoansReceived` (review point 2);
- no added line is a subtotal of other selected lines (no value equals the sum of 2-5 other
  selected lines), so the sum double-counts nothing;
- no line is added on a left-out element.

### Corpus dry run (read-only, once, after the list was fixed)

Every annual filing in the FSDS cache (`data/cache/sec_fsds/filtered`, 166 filing-units): the
only change is TSM, every year, by exactly its "Long-term liabilities - current portion" line:

| 20-F | Key today (TWD M) | With the list | US$ M today → with |
|---|---|---|---|
| FY2021 | 728,301.0 | 732,867.7 | 26,254.5 → 26,419.1 |
| FY2022 | 839,096.4 | 858,410.3 | 27,305.3 → 27,933.8 |
| FY2023 | 918,282.8 | 927,576.1 | 29,989.5 → 30,293.0 |
| FY2024 | 958,428.9 | 1,018,286.8 | 29,229.2 → 31,054.7 |
| FY2025 | 896,062.0 | 1,032,987.7 | 28,564.3 → 32,929.2 |

No added line is an asset, lease-only, deposit or equity line; no us-gaap filing moves.
**The per-filer diff above is for the maintainer's review** (key-definitions procedure).

### Split check

TSM has no Debt key in any v2 split (no inventory row, requirement 5). No held-out data, no
judging sample and no LLM was used; `xbrl_db` was read once (TSM's Debt rows), read-only.

### Retired tests touched by this spec PR

The live `Debt` entry stops being key-definitions' locked copy once implemented. Each retired
test that pinned it now accepts either the old or the new locked entry, so it passes before
and after the implementation:

- `test/spec/key-definitions/test_c7_config.py`: live `Debt` equal to key-definitions' copy
  **or** this spec's copy.
- `test/spec/witness-rules-r2-1/test_c5_live_rules_r2_1.py`, `test_c6_record.py`: the
  `Debt`/`DeferredRevenue` alternatives gain this spec's copy (`conftest.py` names it).
- `test/spec/statement-rules-by-element/test_c5_compatibility.py`: "no live family uses the
  new fields" becomes "only `Debt` does, with `elements` exactly this spec's copy's", and the
  legacy-equality check drops `elements` from `Debt`; `test_c4_qualifiers.py`'s TSM
  stripping check runs on the live rule without `elements`, which is what it tests.

### Decisions for the maintainer (recommendation first)

1. **The list is the taxonomy's borrowing elements at every level** (totals, current,
   current-portion and non-current-portion forms), not only TSM's. Recommended: a balance
   sheet presents each as one line, and the dry run found no double count. Alternative: only
   the component forms (would miss filers presenting one `Borrowings` line with a non-debt
   label).
2. **A mis-tagged line is taken** (one out-of-corpus filer's "Security deposits received" on
   a secured-bank-loans element). Recommended: accept; the element is the filer's own
   statement of what the line is, the same evidence the oracle trusts elsewhere, and no corpus
   line is affected. Alternative: keep `exclude` applying to element lines (statement-rules-
   by-element decision 1 rejected it).
3. **TSM Debt intents stay off here**; requirement 5's steps turn them on. Recommended: fix the
   mapping's IFRS `Debt` row first (it names an element that does not exist) as its own
   reviewed change, then K6.
4. **No us-gaap elements.** The us-gaap Debt lines are witnessed by the label rule (32 of 33
   filers, key-definitions); adding us-gaap elements would move US filers and needs its own
   per-filer review. Recommended: not now.
