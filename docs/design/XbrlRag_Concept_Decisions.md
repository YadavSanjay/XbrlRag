# XbrlRag concept decisions

The decision record for which XBRL elements answer each canonical metric family, and how.
It is for anyone changing `concept_mapping`, the family coverage file, or the lookup's
candidate order, and for anyone reading why a filer's number resolves the way it does. Every
decision below was made by the maintainer (D1–D44 on 2026-11-07, D45–D50 on 2026-11-08,
D51–D52 on 2026-11-11) and is implemented. The IDs are stable: the coverage file and specs
cite them.

**In this doc:** [Overview](#overview) · [Markings](#markings) ·
[Totals versus components](#1-totals-versus-components-and-the-largest-gaps) ·
[Cash flow debt lines](#2-cash-flow-debt-lines) · [Income statement](#3-income-statement-lines) ·
[Balance sheet, capex, IFRS](#4-balance-sheet-capex-and-ifrs-lines) ·
[Families already right](#component-flags-where-the-family-is-already-right) ·
[Cross-cutting rules](#cross-cutting-rules) · [IFRS](#ifrs-family-meaning) ·
[Later refinements](#later-refinements) · [Related](#related)

## Overview

The decisions came from a zero-cost coverage audit of `xbrl_db` (123 canonical annual
filings, 33 development filers, no held-out data). Its 281 hard gaps (a statement line whose
family resolved nothing, only a component, or a child of the line) and its component-for-total
flags were grouped into rows, each checked against the filer's own labels, presentation and
calculation linkbases and SEC companyfacts. This is area A of the
[test strategy](XbrlRag_Test_Strategy.md): concept choice, extensions, totals versus
components.

Where they live in code:

- `configs/concept_mapping/concept_family_coverage.yaml` — roles, stated substitutions, IFRS
  rows and cik-scoped members, read by `src/ingest/util/generate_concept_mapping.py` and by
  `src/retriever/xbrl/family_resolution.py`, so the loaded mapping and its executable reading
  cannot drift apart;
- the lookup (`src/retriever/xbrl/xbrl_factstore_lookup.py`) applies the candidate order and
  states substitutes in the answer;
- specs: [concept-family-coverage](../specs/concept-family-coverage.md) (D1–D44 and the
  cross-cutting rules), [ifrs-family-meaning](../specs/ifrs-family-meaning.md) (D45–D50),
  [debt-ifrs-elements](../specs/debt-ifrs-elements.md) (D51–D52).

## Markings

| Marking | Means |
|---|---|
| `same measure` | The element joins the family; the answer is the filer's number for that metric |
| `stated substitute` | The element answers, and the answer states the substitution (for example "depreciation; amortization not separately reported") |
| `different metric` | Not mapped; the family is correctly "not tagged" or correctly resolves another line |

"Cik-scoped" means a `cik_override` row for that filer, not a global one.

## 1. Totals versus components, and the largest gaps

| ID | Family | Element(s) | Decision | Why |
|---|---|---|---|---|
| D1 | InterestExpense | `us-gaap:InterestExpenseNonoperating` | `same measure`, ranked just after `InterestExpense` | The filer's interest expense line since the 2024 taxonomy (23 filers); a filer tagging both keeps the total |
| D2 | DepreciationAndAmortization | `Depreciation` where depreciation and amortization are separate lines (also `ifrs-full:DepreciationExpense`, GOOGL extensions) | `stated substitute` | Answer depreciation and state the amortization line; a derived D&A sum was declined (it would be our definition, and AVGO's amortization includes ROU assets) |
| D3 | DepreciationAndAmortization | `Depreciation` where only depreciation is presented | `stated substitute` | "Depreciation; amortization not separately reported" |
| D4 | DepreciationAndAmortization | MSFT `ext:DepreciationAmortizationAndOther` | `same measure` (cik-scoped) | The mapping named a non-existent us-gaap element; the extension is MSFT's D&A line |
| D5 | DepreciationAndAmortization | TSLA `ext:DepreciationAmortizationAndImpairment` | `stated substitute` (cik-scoped) | TSLA's only D&A line, but it includes impairment |
| D6 | DepreciationAndAmortization | `us-gaap:DepreciationAmortizationAndAccretionNet` | `stated substitute` | Includes accretion (CEG); state it |
| D7 | DepreciationAndAmortization | AMD `us-gaap:OtherDepreciationAndAmortization` | `same measure` (cik-scoped) | AMD's D&A line; globally the element means "other" D&A |
| D8 | DepreciationAndAmortization | `us-gaap:AdjustmentForAmortization` | `different metric` | Amortization only, the second half of D2 |
| D9 | OperatingExpenses | `us-gaap:CostsAndExpenses` | `stated substitute` | The filer's total, stated as "includes cost of revenue" |
| D10 | Debt | `DebtCurrent`, `ShortTermBorrowings` | `different metric` | A current part must never stand for total debt |
| D11 | Debt | `LongTermDebtNoncurrent`, `LongTermDebtAndCapitalLeaseObligations` | `stated substitute` | Noncurrent debt, stated as excluding the current portion (see [later refinements](#later-refinements)) |
| D12 | Debt | `LongTermDebtCurrent`, `CommercialPaper` | `different metric` | Current parts; they matter only inside a total |
| D13 | OtherNonOperatingIncome | `NonoperatingIncomeExpense` against `OtherNonoperatingIncomeExpense` | `same measure`, with a presented-line rule | A line on the income statement beats a note-only fact in the same family |
| D14 | OtherNonOperatingIncome | ORCL, TSM extensions; `us-gaap:InterestAndOtherIncome` | `same measure` (cik-scoped for extensions) | Each is the filer's single non-operating line (ORCL in every year) |
| D15 | SharesOutstanding | `us-gaap:CommonStockSharesIssued` | `different metric` | Issued is not outstanding (KO off by 64%); removed from the family |
| D16 | SharesOutstanding | `dei:EntityCommonStockSharesOutstanding` | `stated substitute` | The cover-page count, stated with its date; a derived issued-minus-treasury count was declined |

## 2. Cash flow debt lines

| ID | Family | Element(s) | Decision | Why |
|---|---|---|---|---|
| D17 | DebtIssued | `ProceedsFromIssuanceOfDebt`, `ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet` | `same measure` | Gross debt proceeds; the family is debt issued of any term, not long-term only ([key-currency-and-definition](../specs/key-currency-and-definition.md)) |
| D18 | DebtRepaid | `RepaymentsOfLongTermDebtAndCapitalSecurities`, `RepaymentsOfDebtAndCapitalLeaseObligations`, `RepaymentsOfOtherLongTermDebt` | `same measure` | The filer's main repayment line; the lease-inclusive element at a lower rank |
| D19 | DebtRepaid | `RepaymentsOfConvertibleDebt` | `same measure`, ranked below D18 | TSLA's repayment line, without replacing CSCO's main line |
| D20 | DebtIssued, DebtRepaid | debt maturing in more than three months (MSFT us-gaap, ABT extensions) | `same measure` (ABT cik-scoped) | Gross flows; the 90-day net line is a separate measure |
| D21 | DebtIssued, DebtRepaid | several gross lines and no total (TSM IFRS, VST) | `stated substitute` | Answer the ranked line and name the others; a sum of all gross lines was declined |
| D22 | DebtIssued, DebtRepaid | medium-term notes (CRM), assumed debt (QCOM), TSLA extension | `same measure` (low rank; TSLA cik-scoped) | Debt principal flows |
| D23 | DebtRepaid | `PaymentsOfDebtRestructuringCosts`, `ifrs-full:PaymentsForDebtIssueCosts` | `different metric` | Costs and premiums, not principal |

## 3. Income statement lines

| ID | Family | Element(s) | Decision | Why |
|---|---|---|---|---|
| D24 | InterestIncome | `InvestmentIncomeInterestAndDividend` | `same measure` | The filer's interest income line |
| D25 | InterestIncome | `InvestmentIncomeNet` | `stated substitute` | Net investment income can include gains |
| D26 | RestructuringCharges | combined restructuring-and-impairment elements and extensions (LLY, LMT, MU, TSLA, TXN) | `stated substitute` | The filer's line, which bundles impairment or other items |
| D27 | PretaxIncome | ORCL, MCD, PG pretax extensions | `same measure` (cik-scoped) | Extension copies of the standard element |
| D28 | ServiceRevenue | ORCL `ext:SalesRevenueServicesNet1` | `same measure` (cik-scoped, ServiceRevenue only) | A component of revenue, never revenue |
| D29 | IncomeFromDiscontinuedOperations | `...NetOfTaxAttributableToReportingEntity` | `same measure` | The parent's share |
| D30 | IncomeFromDiscontinuedOperations | per-share discontinued-operations elements | `different metric` | Per-share amounts |
| D31 | CashFromOperations / Investing / Financing | `...ContinuingOperations` totals | `stated substitute` | "Continuing operations"; equal to the total when discontinued cash flow is 0 |
| D32 | SellingAndMarketing | `us-gaap:MarketingExpense` | `same measure` | The filer's selling and marketing line |

## 4. Balance sheet, capex and IFRS lines

| ID | Family | Element(s) | Decision | Why |
|---|---|---|---|---|
| D33 | AccountsPayable | `AccountsPayableAndOtherAccruedLiabilitiesCurrent` | `stated substitute` | CRM's only payables line; "includes accrued expenses" |
| D34 | six families | TSM one-for-one IFRS equivalents (share-based payments, trade receivables, G&A, trade payables, revenue, interest revenue) | `same measure` | IFRS names for the same measures |
| D35 | IntangibleAssets | `ifrs-full:IntangibleAssetsAndGoodwill` | `stated substitute` | Includes goodwill although the label does not say so |
| D36 | AccountsReceivable | TSM related-party receivables | `different metric` | Separate, minor lines beside trade receivables |
| D37 | OtherNonOperatingIncome | `ifrs-full:OtherGainsLosses`, `ifrs-full:OtherIncome` | `different metric` | Components of TSM's non-operating total (D14) |
| D38 | Inventory | CEG's two inventory lines with no total | `different metric` | Each line is a part; a derived sum was declined |
| D39 | ShortTermInvestments | `OtherShortTermInvestments`, `HeldToMaturitySecuritiesCurrent`, NVDA extension | `same measure` (NVDA cik-scoped) | Each is the filer's short-term investments line |
| D40 | ShortTermInvestments | `us-gaap:MarketableSecurities` (KO) | `different metric` | KO's own subtotal leaves it out |
| D41 | Capex | NVDA `ext:PurchasesOfPropertyAndEquipmentAndIntangibleAssets` | `same measure` (cik-scoped) | NVDA's capex line; intangibles are minor |
| D42 | CostOfRevenue, InterestExpense, SG&A | LLY total-costs extension, CEG affiliate interest, MCD "Other" SG&A | `different metric` | A total or a part of another family |
| D43 | InterestIncome | `us-gaap:InvestmentIncomeInterest` (TSM FY2023) | `same measure` (cik-scoped) | TSM's interest-income line that year |
| D44 | Debt | `us-gaap:DebtLongtermAndShorttermCombinedAmount` | `same measure`, ranked below any presented total | A note-only total; once D10 applies, LLY has no total on its statements |

## Component flags where the family is already right

No change; recorded so the exception list keeps them.

| Family | Resolves | Filer's total line | Why it is right |
|---|---|---|---|
| StockholdersEquity | `StockholdersEquity` | the NCI-inclusive total | The family means equity attributable to the parent |
| CashAndEquivalents | `CashAndCashEquivalentsAtCarryingValue` | `CashCashEquivalentsAndShortTermInvestments` | The total includes short-term investments |
| AccountsPayable | `AccountsPayableCurrent` / `...TradeCurrent` | `AccountsPayableAndAccruedLiabilitiesCurrent` | The part is the payables figure (contrast D33) |
| AccountsReceivable | `AccountsReceivableNetCurrent` | `ReceivablesNetCurrent` | Trade receivables, not all receivables |
| CostOfRevenue | `CostOfGoodsAndServicesSold` | LLY's total-costs extension (D42) | The line is total costs |

## Cross-cutting rules

| Rule | Decision |
|---|---|
| Presented line first | Within one family, a line presented on a primary statement beats a note-only fact (D13, D44) |
| Precision tie-break | Among duplicate facts, the finer `decimals` wins before `fact_id` |
| Per-share units | Per-share facts are shown as `USD/shares` (or the filing currency per share), not `shares*USD` |
| `period_end` | Stored as Arelle's end-exclusive date (SEC's end date + 1 day) and left as is; a day is subtracted wherever a date is shown or compared with SEC |
| Substitution | A `stated substitute` carries its note on the mapping, the answer states it, and eval keys use the same text |

## IFRS family meaning

An IFRS element answers a us-gaap family only when it means the same measure; one fact
belongs to one family.

| ID | Family | Element | Decision |
|---|---|---|---|
| D45 | SellingGeneralAndAdministrative | TSM presents marketing and G&A with no total | Not answered: the lookup refuses and names both parts with their fact ids, so a plan can add them in an audited compute step |
| D46 | InterestIncome | `ifrs-full:FinanceIncome` | `stated substitute` (can include dividends); `ifrs-full:RevenueFromInterest` is the primary |
| D47 | SellingAndMarketing | `ifrs-full:SalesAndMarketingExpense` | `same measure`; no longer a member of SG&A |
| D48 | NetIncome, StockholdersEquity, ComprehensiveIncome | parent-attributable IFRS elements; NCI-inclusive totals | Parent elements are the primary; the NCI-inclusive total is a `stated substitute` only where no parent element is filed |
| D49 | OperatingExpenses | `ifrs-full:OperatingExpenseExcludingCostOfSales` | `same measure`, ranked after `ifrs-full:OperatingExpense` |
| D50 | EarningsPerShareBasic / Diluted | `ifrs-full:BasicEarningsLossPerShare`, `DilutedEarningsLossPerShare` | `same measure` |
| D51 | Debt | the 29 IFRS borrowing elements (replacing `ifrs-full:NoncurrentBorrowings`, which no IFRS taxonomy declares) | `ifrs-full:Borrowings` (the total) is `same measure`; the other 28 are ranked near-miss rows (non-current before current, totals before parts), never substitutes |
| D52 | Capex | `ifrs-full:AcquisitionOfPropertyPlantAndEquipment` | Removed: declared in no IFRS taxonomy 2022–2025; the investing-activities PP&E element stays |

A unit test (`test/unit/test_mapping_ifrs_elements_exist.py`) checks every `ifrs-full:`
element of the mapping against the IFRS core schemas 2022–2025.

## Later refinements

Decisions taken after this table, which change how some rows answer:

- **Debt is a defined measure.** Debt (and DeferredRevenue) now answer by one stated
  definition, total debt as presented, built from cited parts, in both the keys
  ([key-definitions](../specs/key-definitions.md)) and the system
  ([s4-lookup-deferred-revenue-debt](../specs/s4-lookup-deferred-revenue-debt.md)). The
  Debt rows above (D10–D12, D44, D51) now admit a filer-year and rank its filing and column;
  the defined measure gives the answer.
- **Shares outstanding** keys mean the year-end balance, with the cover-page count of D16
  graded as a disclosed alternate reading
  ([shares-outstanding-reading](../specs/shares-outstanding-reading.md)).

## Related

- [Test strategy](XbrlRag_Test_Strategy.md) — the nuance areas this record belongs to
- [Evaluation semantics](XbrlRag_Eval_Semantics.md) — S4 defined measures
- [Materialized views](XbrlRag_Materialized_Views.md) — `mv_financial_facts` and the mapping
- [Ingest module notes](../../src/ingest/sec_edgar/CLAUDE.md)
