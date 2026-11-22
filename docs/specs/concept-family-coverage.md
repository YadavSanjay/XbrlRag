# concept-family-coverage — every family resolves the filer's own line, says when it substitutes, and never answers with a different metric

Area A of the [test strategy](../design/XbrlRag_Test_Strategy.md) (nuances N1 concept choice,
N2 extensions, N3 totals versus components, and the `decimals` part of N7). The 42 decisions
D1–D42 of [XbrlRag_Concept_Decisions.md](../design/XbrlRag_Concept_Decisions.md) were agreed
by the maintainer on 2026-11-07, every one as recommended; the forks keep the recommendation
(D2 and D11 are `stated substitute`, not a derived sum; D16's cover-page count is a stated
substitute; `period_end` stays stored as is). This spec turns them into behaviour.

Five defects, each verified on `xbrl_db` (read-only) and against the filer's own statements:

1. **Same-measure elements are missing.** NVIDIA's FY2025 income statement shows "Interest
   expense" (247M), tagged `us-gaap:InterestExpenseNonoperating`; the `InterestExpense`
   family does not contain that element, so the metric looks untagged. Amazon (2,274M) and
   Home Depot (2,412M) drop out of a FY2025 retailer interest-expense screen the same way.
   Microsoft's D&A line is its extension `DepreciationAmortizationAndOther` (FY2026 38,534M),
   but the mapping holds `us-gaap:DepreciationAmortizationAndOther`, an element that does not
   exist, so "Microsoft's D&A" falls to `Depreciation` (34,300M).
2. **A different metric answers.** Eli Lilly's FY2025 "Debt" resolves to the current part,
   `DebtCurrent` 1,635M, against long-term debt of 40,868M; Coca-Cola's shares outstanding
   resolve to shares *issued* (7,040M against 4,301M on the cover).
3. **A substitute is silent.** Where a filer has no line for the family's own measure, the
   nearest line it does present (depreciation only, total costs and expenses, noncurrent debt,
   the cover-page share count) is today either refused or answered as if it were the measure.
4. **A notes-only fact beats the presented line.** Alphabet's FY2025 income statement shows
   "Other income (expense), net" 29,787M (`NonoperatingIncomeExpense`); the family's rank 1,
   `OtherNonoperatingIncomeExpense`, is tagged only in a note (1,667M) and wins.
5. **A coarser duplicate wins.** Intel tags FY2022 share-based compensation as 3,128M
   (`decimals` −6) and again as 3,100M (−8); the lookup's SQL orders duplicates by `fact_id`
   only, so the coarser fact comes first.

And per-share facts carry the stored unit string `shares*USD` where an answer should say
`USD/shares`.

## Requirement

### R1. Family membership (`family_member`)

`family_member(family, cik, concept)` is the committed concept mapping, read without a
database. It returns `None` when `concept` is not a member of `family` for the filer `cik`,
else a dict:

```python
{"rank": int,                       # the concept_mapping preference_rank for this filer
 "role": "primary" | "substitute" | "component",
 "substitution": None | {"code": str, "note": str}}   # set exactly when role == "substitute"
```

- **Inputs.** `cik` is the 10-digit, zero-padded CIK. `concept` is either the fact store's
  form `<namespace>:<LocalName>` (as in `mv_financial_facts.concept`) or a prefix form
  `us-gaap:X`, `srt:X`, `dei:X`, `ifrs-full:X`, `ext:X`.
- **Standard namespaces.** `http://fasb.org/us-gaap/<version>` is `us-gaap`,
  `http://fasb.org/srt/<version>` is `srt`, `http://xbrl.sec.gov/dei/<version>` is `dei`,
  and `http://xbrl.ifrs.org/taxonomy/<date>/ifrs-full` or the same with `https://` is
  `ifrs-full`. The version or date never matters: every dated namespace of a standard
  taxonomy gives the same answer as its prefix form.
- **Extensions.** Any other namespace (a filer's own, for example
  `http://www.microsoft.com/20260630`) is an extension; `ext:X` is the prefix form. An
  extension concept is a member only through a **cik-scoped** entry of this spec, and then
  only for that filer, whatever its namespace URI or year. An extension whose local name
  equals a standard element's (TSMC's extension `InvestmentIncomeInterest`) is never that
  standard element.
- **Roles.**
  - `primary`: the family's own measure (rank 1–49, or the IFRS band 100+).
  - `substitute`: the filer's nearest presented line when it has none for the measure; it
    may answer, and the answer states the substitution. A substitute's rank is ≥ 50.
  - `component`: a part or neighbouring measure that never answers (ranks 50–99), as
    concept-rank-integrity defines the band.
- **Scope of the source.** The generic families, the reviewed filer rankings and IFRS rows
  of the committed configuration, plus this spec's additions. The facts-count `auto_override`
  pass (layer-0) is data-driven and outside `family_member`; Notes say how the reload keeps
  the two consistent.

The decisions (CIKs in Notes):

**Same measure** (role `primary`):

| D | Family | Element(s) | Scope | Rank |
|---|---|---|---|---|
| D1 | InterestExpense | `us-gaap:InterestExpenseNonoperating` | global | exactly: InterestExpense 1, InterestExpenseNonoperating 2, InterestAndDebtExpense 3, InterestExpenseDebt 4 |
| D4 | DepreciationAndAmortization | MSFT `ext:DepreciationAmortizationAndOther` | MSFT | < 50. `us-gaap:DepreciationAmortizationAndOther` is removed: a member for no filer |
| D7 | DepreciationAndAmortization | `us-gaap:OtherDepreciationAndAmortization` | AMD only | < 50; no member for any other filer |
| D13 | OtherNonOperatingIncome | `us-gaap:NonoperatingIncomeExpense` stays | global | unchanged: OtherNonoperatingIncomeExpense 1, NonoperatingIncomeExpense 2 |
| D14 | OtherNonOperatingIncome | ORCL `ext:NonoperatingIncomeExpenseIncludingEliminationOfNetIncomeLossAttributableToNoncontrollingInterests`; TSM `ext:NonOperatingIncomeExpenseNet`, `ext:NonOperatingIncomeExpense1`; `us-gaap:InterestAndOtherIncome` | ORCL; TSM; global | < 50 |
| D17 | DebtIssued | `us-gaap:ProceedsFromIssuanceOfDebt`, `us-gaap:ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet` | global | < 50 |
| D18 | DebtRepaid | `us-gaap:RepaymentsOfLongTermDebtAndCapitalSecurities`, `us-gaap:RepaymentsOfOtherLongTermDebt`, `us-gaap:RepaymentsOfDebtAndCapitalLeaseObligations` | global | < 50; the lease-inclusive element after the other two |
| D19 | DebtRepaid | `us-gaap:RepaymentsOfConvertibleDebt` | global | < 50, after every D18 element |
| D20 | DebtRepaid / DebtIssued | `us-gaap:RepaymentsOfDebtMaturingInMoreThanThreeMonths` / `us-gaap:ProceedsFromDebtMaturingInMoreThanThreeMonths`; ABT `ext:RepaymentsOfLongTermDebtAndDebtMaturingInMoreThanThreeMonths` / `ext:ProceedsFromLongTermDebtAndDebtMaturingInMoreThanThreeMonths` | global; ABT | < 50 |
| D22 | DebtIssued / DebtRepaid | `us-gaap:ProceedsFromIssuanceOfMediumTermNotes` / `us-gaap:RepaymentsOfAssumedDebt`; TSLA `ext:RepaymentsOfConvertibleAndOtherDebt` (DebtRepaid) | global; TSLA | < 50, after every other global primary of its family |
| D24 | InterestIncome | `us-gaap:InvestmentIncomeInterestAndDividend` | global | < 50 |
| D27 | PretaxIncome | ORCL `ext:IncomeLossFromContinuingOperationsIncludingNoncontrollingInterestBeforeIncomeTaxesExtraordinaryItems`; MCD and PG `ext:IncomeLossFromContinuingOperationsBeforeIncomeTaxes` | ORCL; MCD; PG | < 50 |
| D28 | ServiceRevenue | ORCL `ext:SalesRevenueServicesNet1` | ORCL | 1 for ORCL, ahead of the family's revenue-total primaries (an undimensioned ServiceRevenue lookup must not answer total revenue); never a member of `Revenue` or `ProductRevenue` |
| D29 | IncomeFromDiscontinuedOperations | `us-gaap:IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity` | global | < 50 |
| D32 | SellingAndMarketing | `us-gaap:MarketingExpense` | global | < 50 |
| D34 | ShareBasedCompensation, AccountsReceivable, GeneralAndAdministrative, AccountsPayable, Revenue, InterestIncome | `ifrs-full:AdjustmentsForSharebasedPayments`, `ifrs-full:CurrentTradeReceivables`, `ifrs-full:GeneralAndAdministrativeExpense`, `ifrs-full:TradeAndOtherCurrentPayablesToTradeSuppliers`, `ifrs-full:RevenueFromContractsWithCustomers`, `ifrs-full:RevenueFromInterest` (one each, in that order) | global | < 50 or ≥ 100 |
| D39 | ShortTermInvestments | `us-gaap:OtherShortTermInvestments`, `us-gaap:HeldToMaturitySecuritiesCurrent`; NVDA `ext:MarketableSecuritiesAndEquitySecuritiesFVNI` | global; NVDA | < 50 |
| D41 | Capex | NVDA `ext:PurchasesOfPropertyAndEquipmentAndIntangibleAssets` | NVDA | < 50 |
| 2026-11-07 | InterestIncome | TSM `ext:InvestmentIncomeInterest` (its interest-income line, FY2022-23) | TSM | after `ifrs-full:RevenueFromInterest` for TSM (TSM tags both, equal, in FY2022-23); `< 50` or `≥ 100` |
| 2026-11-07 | Debt | `us-gaap:DebtLongtermAndShorttermCombinedAmount` (total debt) | global | exactly 2, right after `LongTermDebt`; with R2 a `LongTermDebt` total (presented or not) keeps answering, and the combined total answers before any substitute |

**Stated substitute** (role `substitute`, rank ≥ 50). The `code` and `note` are exact; the
eval keys will use the same text (area D, not here):

| D | Family | Element(s) | Scope | `code` | `note` |
|---|---|---|---|---|---|
| D2, D3 | DepreciationAndAmortization | `us-gaap:Depreciation`, `ifrs-full:DepreciationExpense` | global | `depreciation_only` | `Depreciation only; amortization is not included.` |
| D2 | DepreciationAndAmortization | GOOGL `ext:DepreciationAndImpairmentOnDispositionOfPropertyAndEquipment` | GOOGL | `depreciation_and_impairment_only` | `Depreciation and impairment of property and equipment only; amortization is not included.` |
| D5 | DepreciationAndAmortization | TSLA `ext:DepreciationAmortizationAndImpairment` | TSLA | `includes_impairment` | `Depreciation and amortization including impairment charges.` |
| D6 | DepreciationAndAmortization | `us-gaap:DepreciationAmortizationAndAccretionNet` | global | `includes_accretion` | `Depreciation and amortization including accretion.` |
| D9 | OperatingExpenses | `us-gaap:CostsAndExpenses` | global | `includes_cost_of_revenue` | `Total costs and expenses, including cost of revenue.` |
| D11 | Debt | `us-gaap:LongTermDebtNoncurrent` | global | `excludes_current_portion` | `Long-term debt excluding the current portion.` |
| D11 | Debt | `us-gaap:LongTermDebtAndCapitalLeaseObligations` | global | `excludes_current_portion` | `Long-term debt and lease obligations excluding the current portion.` |
| D16 | SharesOutstanding | `dei:EntityCommonStockSharesOutstanding` | global | `cover_page_count` | `Shares outstanding on the cover-page date of the annual report, not at the balance-sheet date.` |
| D21 | DebtRepaid | `ifrs-full:RepaymentsOfBondsNotesAndDebentures`, `ifrs-full:RepaymentsOfNoncurrentBorrowings` (ranked after the bonds element), `us-gaap:RepaymentsOfLinesOfCredit`; VST `ext:RepaymentsOfDebtShortTermInNature` | global; VST | `one_of_several_lines` | `One of several gross debt lines; the filer reports no total.` |
| D21 | DebtIssued | `ifrs-full:ProceedsFromIssueOfBondsNotesAndDebentures` | global | `one_of_several_lines` | `One of several gross debt lines; the filer reports no total.` |
| D25 | InterestIncome | `us-gaap:InvestmentIncomeNet` | global | `includes_investment_gains` | `Net investment income, which can include gains and losses besides interest.` |
| D26 | RestructuringCharges | `us-gaap:RestructuringSettlementAndImpairmentProvisions`, `us-gaap:RestructuringCostsAndAssetImpairmentCharges`; TSLA `ext:RestructuringAndOtherExpenses`; TXN `ext:RestructuringChargesAndOtherNet`; LMT `ext:SeveranceAndRestructuringCosts` | global; TSLA; TXN; LMT | `includes_impairment_or_other` | `Restructuring combined with impairment or other charges on one line.` |
| D31 | CashFromOperations, CashFromInvesting, CashFromFinancing | `us-gaap:NetCashProvidedByUsedIn{Operating,Investing,Financing}ActivitiesContinuingOperations` (each to its family) | global | `continuing_operations_only` | `Continuing operations only; cash flows of discontinued operations are reported separately.` |
| D33 | AccountsPayable | `us-gaap:AccountsPayableAndOtherAccruedLiabilitiesCurrent` | global | `includes_accrued_liabilities` | `Accounts payable together with accrued and other liabilities.` |
| D35 | IntangibleAssets | `ifrs-full:IntangibleAssetsAndGoodwill` | global | `includes_goodwill` | `Intangible assets including goodwill.` |

`us-gaap:Depreciation`, `us-gaap:CostsAndExpenses`, `us-gaap:LongTermDebtNoncurrent` and
`us-gaap:LongTermDebtAndCapitalLeaseObligations` keep their component-band ranks (50, 51,
53, 54); only their role changes.

**Different metric** (`family_member` is `None`, or role `component` for an element that is
already a component; it never answers):

| D | Family | Element(s) |
|---|---|---|
| D8 | DepreciationAndAmortization | `us-gaap:AdjustmentForAmortization` |
| D10 | Debt | `us-gaap:DebtCurrent`, `us-gaap:ShortTermBorrowings` (stay components) |
| D12 | Debt | `us-gaap:LongTermDebtCurrent`, `us-gaap:CommercialPaper` |
| D15 | SharesOutstanding | `us-gaap:CommonStockSharesIssued` (stays a component) |
| D20 (reason) | DebtIssued, DebtRepaid | `us-gaap:ProceedsFromRepaymentsOfShortTermDebtMaturingInThreeMonthsOrLess` (the 90-day net line) |
| D23 | DebtRepaid | `us-gaap:PaymentsOfDebtRestructuringCosts`, `ifrs-full:PaymentsForDebtIssueCosts` |
| D28 | Revenue, ProductRevenue | ORCL `ext:SalesRevenueServicesNet1` |
| D30 | IncomeFromDiscontinuedOperations | `us-gaap:DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerBasicShare`, `...PerDilutedShare`, `us-gaap:IncomeLossFromDiscontinuedOperationsNetOfTaxPerBasicShare`, `...PerDilutedShare` |
| D36 | AccountsReceivable | TSM `ext:CurrentReceivablesFromRelatedPartiesRelatedToCoreBusiness`, `ext:OtherCurrentReceivablesFromRelatedParties`; `ifrs-full:TradeAndOtherCurrentReceivablesDueFromRelatedParties` |
| D37 | OtherNonOperatingIncome | `ifrs-full:OtherGainsLosses`, `ifrs-full:OtherIncome` |
| D38 | Inventory | `us-gaap:EnergyRelatedInventoryNaturalGasInStorage`, `us-gaap:InventoryRawMaterialsAndSuppliesNetOfReserves` |
| D40 | ShortTermInvestments | `us-gaap:MarketableSecurities` |
| D42 | CostOfRevenue; InterestExpense; SellingGeneralAndAdministrative | LLY `ext:CostOfSalesOperatingExpensesAndOtherNet`; CEG `ext:RelatedPartyTransactionInterestExpense`; `us-gaap:OtherSellingGeneralAndAdministrativeExpense` |

### R2. Which candidate answers (`candidate_order`)

`candidate_order(rows)` takes the candidate fact rows of one lookup (dicts as the lookup's
SQL returns them) and returns the indices of the rows that **may answer**, best first.
Keys read (a missing key takes the default in brackets):

- `concept_role` ['component' when 50 ≤ `concept_pref_rank` < 100, else 'primary'];
- `concept_pref_rank` [None]; `period_end` (ISO date string) [None]; `presented` [False];
  `value` [None]; `decimals` [None]; `fact_id`.

Rows whose role is `component` are left out. The others are ordered by, in turn:

1. role: `primary` before `substitute`;
2. later `period_end` first; None last;
3. `presented` True first (D13: a line on a primary statement beats a notes-only fact);
4. lower `concept_pref_rank` first; None last;
5. a row with a value before a nil row (`value` None);
6. finer precision first: `decimals` None (exact: `INF` or no attribute, as
   rounded-duplicates defines it) before any int, then the larger int;
7. larger `fact_id` first.

So a primary of the family always beats a substitute (Union Pacific's balance-sheet
`CommonStockSharesOutstanding` beats its later cover-page count), and a coarser duplicate of
one concept and period never beats the finer one, whatever their fact ids.

**Presented.** A row's `presented` is True when its concept is a line item of one of the
filing's primary statements: the concept is a child in the filing's presentation linkbase
(`mv_presentation_network`) under a role whose definition reads `<sort> - Statement -
<title>` (EDGAR Filer Manual role-definition convention) and whose title does not contain
"Parenthetical". The lookup's SQL selects it; the cover page (`Document` / `Cover` roles) is
never presented.

### R3. The lookup applies R1 and R2 (`XbrlFactstoreLookup._build_result`)

For candidate rows as the lookup's SQL returns them, in any order:

- the answer (`value`, `unit`, `citation`) is the row `candidate_order` puts first, after the
  existing unit restriction (`_restrict_to_one_unit`) and exact-member selection;
- components stay candidates for the ambiguity and confidence checks (the Debt component
  guard of within-concept-confidence keeps working); they are only never the answer. No
  answerable row → `NOT_FOUND` (or `UNRESOLVED_DIMENSION` for a dimensioned lookup), as an
  empty result is today;
- rounded duplicates of the winning concept collapse to the most precise as today
  (rounded-duplicates);
- the result is **OK** for every real-filer case of C2–C5 (a same-measure element, a
  substitute, or a presented line winning on rank 2 or below);
- `FactLookupResult` gains `substitution: dict | None`: when the answer row's role is
  `substitute`, `{"code": <code>, "note": <note>, "concept": <the answer row's concept as
  stored>}` (from the row keys `substitution_code`, `substitution_note`); otherwise None.
  `to_dict()` includes `"substitution"`;
- the result's `unit` is `display_unit(<the answer row's stored unit>)` (R4); candidates
  keep their stored units.

### R4. Per-share units (`display_unit`)

`display_unit(unit)` returns `"<C>/shares"` for the store's rendering of a per-share unit:
exactly two `*`-separated factors, one `shares` and one currency `C` matching `^[A-Z]{3}$`, in
either order (`shares*USD`, `shares*TWD`, `USD*shares`). Every other string, and None, is
returned unchanged. This is the form units-currency-integrity's `expr_unit` already reads
(`"A/B"` → A over B), so a compute over a per-share lookup keeps its unit (`USD/shares`).

### Not in this spec

`period_end` stays the stored end-exclusive date (SEC end + 1 day); subtracting the day where
a date is shown or compared with SEC is the fiscal-periods change (area G). The eval keys'
substitution text is area D.

## Acceptance criteria

- **C1**: `family_member` gives the same-measure memberships of R1 exactly: role `primary`,
  the ranks the table states (InterestExpense 1–4 exactly; NonoperatingIncomeExpense 2 under
  OtherNonoperatingIncomeExpense 1; the D18/D19/D22 orderings; ORCL's services extension 1 in
  ServiceRevenue; `< 50` or `≥ 100` otherwise),
  `substitution` None, for real filers' concepts in the store's namespace form. The bogus
  `us-gaap:DepreciationAmortizationAndOther` is a member for no filer, and CSCO's extension
  of that name is not MSFT's. Properties: any dated namespace version gives the prefix form's
  answer; a cik-scoped extension is a member for its filer under any extension namespace URI
  and for no other CIK; a standard element's local name in an extension namespace is not a
  member for a filer without a cik-scoped entry. Oracle: the decision table (maintainer,
  2026-11-07) and the filers' own presentation linkbases (Notes).
- **C2**: Same-measure lines answer on real filings. `_build_result` over the committed
  rows of each case (rows as the post-change SQL would return them: members only, ranks and
  roles from `family_member`) is OK with the stated value and fact id, and no substitution:
  NVDA FY2025 interest expense 247,000,000 (fact 64369) and its FY2024 control 257,000,000
  (62956); the FY2025 retailers (COST 154,000,000; WMT 2,249,000,000; AMZN 2,274,000,000; HD
  2,412,000,000), ranked HD > AMZN > WMT > COST; MSFT FY2026 D&A 38,534,000,000; AMD D&A
  FY2022 4,174,000,000, FY2024 671,000,000, FY2025 750,000,000; TSM non-operating FY2023 TWD
  57,850,900,000 and FY2025 TWD 105,563,000,000; ORCL FY2022 −522,000,000 and FY2025
  60,000,000; COST FY2026 711,000,000; the debt flows, interest income, pretax, services
  revenue, discontinued operations, marketing, IFRS, short-term investment and capex cases of
  the test table, with controls where a filer's standard line must keep answering (NVDA
  FY2025 marketable securities, NVDA FY2024 capex, ORCL FY2025 total revenue). Values exact
  (stored integers; `rel=1e-12`). Property: permuting the rows never changes the result.
  Oracle: the filer's statement line (label and value, Notes), cross-checked with SEC
  companyfacts where it holds the fact (fixture `companyfacts` field).
- **C3**: A different metric never answers. `family_member` is None (or role `component`)
  for every element of R1's different-metric table, with the family's own primary still a
  member (anchor). On real filings the answer is never the different metric's fact: LLY
  FY2025 Debt is not 1,635,000,000 (it is the total, 42,503,000,000: C8); KO, MCD and TXN
  FY2025 shares outstanding are not the
  issued count; AMD FY2024 and MRK FY2025 D&A are not the amortization line; LLY FY2024 cost
  of revenue is 8,418,300,000, not 32,362,300,000; CEG FY2024 interest expense is
  506,000,000; TSM, KO, QCOM and MSFT non-members are skipped; CEG FY2024 inventory and MCD
  FY2023 SG&A are `NOT_FOUND`. Properties: adding any component or non-member row (any value,
  period, presentation, fact id) never changes `candidate_order`'s pick; in `_build_result`
  it never changes the answer's value. Oracle: the decision table; the filers' statements.
- **C4**: A substitute answers and says so. `family_member` gives role `substitute`, rank
  ≥ 50 and the exact `code` and `note` of R1's table. On real filings `_build_result` is OK
  with the substitute's value and `substitution == {"code", "note", "concept"}`: AVGO,
  GOOGL, INTC, ABT FY2025, TSM FY2024 and MRK FY2025 depreciation; GOOGL FY2022's extension;
  TSLA FY2025 6,148,000,000; CEG FY2025 2,601,000,000; WMT FY2026 14,203,000,000; GOOGL, AMZN,
  UPS FY2025 total costs and expenses; PG, NFLX FY2025-26, AAPL FY2022, COST FY2026 and KO
  FY2025 noncurrent debt; KO 4,300,723,069, MCD 710,398,642 and TXN 907,550,774
  cover-page shares; TSM FY2025 bonds repaid and issued; MU FY2026 and UPS FY2023 investment
  income; LLY, TSLA, TXN FY2024 restructuring; MRK FY2023 continuing-operations cash flows;
  CRM FY2026 payables; TSM FY2024 intangibles. Controls: UNP FY2025 shares outstanding answer
  `CommonStockSharesOutstanding` 593,245,884 and MSFT FY2025 operating expenses 65,365,000,000
  with no substitution. Properties: adding a primary row at any period makes a primary the
  answer with no substitution; the substitution is set exactly when the answer's role is
  `substitute`. Oracle: the decision table; the filers' statements and companyfacts.
- **C5**: The presented line wins within a role. `candidate_order` and `_build_result`:
  GOOGL FY2025 other income 29,787,000,000 (not the note's 1,667,000,000), INTC FY2025
  3,257,000,000, META FY2025 2,656,000,000; NVDA FY2026 (both lines presented) keeps the
  rank-1 "other" line 9,022,000,000 and AMZN FY2025 15,229,000,000; KO FY2025 interest expense
  is the presented `InterestExpenseNonoperating` fact 195390; KO FY2025 accounts payable is
  the trade line 5,649,000,000 (a presented component never answers). Properties:
  `candidate_order` equals the R2 key order written out in the test, for generated rows; at
  one role and period, a presented row always precedes a notes-only one. Oracle: R2; the
  filers' presentation linkbases.
- **C6**: The finer duplicate wins. INTC FY2022 share-based compensation 3,128,000,000 (fact
  151009, `decimals` −6) over 3,100,000,000 (152546, −8); MRK FY2025 depreciation
  3,045,000,000 over 3,000,000,000; WMT FY2026 D&A 14,203,000,000 over 14,200,000,000; MCD
  FY2025 D&A fact 217763 (−5) over 218235 (−6), equal values. Properties: a coarser duplicate
  (same role, period, presentation and rank) is never first whatever its fact id; an exact
  (`decimals` None) row precedes every finite one; a nil row never precedes a valued one;
  relabelling fact ids keeps the pick when precisions differ. Oracle: R2 rule 6; XBRL 2.1
  §4.6.5 (`decimals`) and §4.6.7.
- **C7**: `display_unit` maps `shares*USD` to `USD/shares`, `shares*TWD` to `TWD/shares` and
  `USD*shares` to `USD/shares`, and leaves `USD`, `TWD`, `shares`, `pure`, `Rate`,
  `USD/shares`, `shares*shares`, `shares*usd` and None unchanged. Properties: idempotent; any
  ISO-shaped currency maps by the rule; every other string is unchanged. LLY FY2025 diluted
  EPS through `_build_result` is OK, 22.95 with unit `USD/shares`. Oracle: R4; SEC
  companyfacts reports EPS under unit `USD/shares`.
- **C8**: The two additions decided 2026-11-07 (R1, last two same-measure rows).
  `family_member`: TSMC's `ext:InvestmentIncomeInterest` is an InterestIncome primary for
  TSMC only, ranked after `ifrs-full:RevenueFromInterest`;
  `us-gaap:DebtLongtermAndShorttermCombinedAmount` is a Debt primary at generic rank 2, after
  `LongTermDebt` for every affected filer. `_build_result`, OK with no substitution: TSM
  interest income FY2022 22,422,200,000, FY2023 60,293,900,000, FY2024 87,213,400,000 (the
  IFRS element, equal to the extension where both exist); total debt LLY FY2025
  42,503,000,000, AMD FY2022-25 2,467,000,000 / 2,468,000,000 / 1,721,000,000 /
  3,222,000,000, AVGO FY2025 67,120,000,000, MRK FY2023-25 35,100,000,000 / 37,100,000,000 /
  49,300,000,000, ORCL FY2023-26 90,481,000,000 / 86,869,000,000 / 92,568,000,000 /
  129,541,000,000; controls where `LongTermDebt` keeps answering: LLY FY2023 19,104,600,000
  and FY2024 29,474,000,000, QCOM FY2023-25 14,484,000,000 / 13,270,000,000 /
  14,811,000,000. Properties: dropping TSMC's IFRS rows leaves the extension answering the
  same value in any row order; an added `LongTermDebt` row at the latest period always
  outranks the combined total; without the combined total, LLY, AMD, AVGO and MRK fall back
  to the C4 noncurrent substitute. Oracle: the maintainer's decision (2026-11-07); the filers'
  facts (accession per fixture case) and SEC companyfacts (`match` for every LLY, AMD, AVGO,
  MRK, ORCL and TSM fact used; QCOM not cached).

## Interface

New module `src/retriever/xbrl/family_resolution.py` (stubbed in the spec PR):

- `family_member(family: str, cik: str, concept: str) -> dict[str, Any] | None`
- `candidate_order(rows: Sequence[Mapping[str, Any]]) -> list[int]`
- `display_unit(unit: str | None) -> str | None`

Changed behaviour, no new signature:

- `XbrlFactstoreLookup._build_result` (R3) and the lookup SQL (`_query_flat`,
  `_query_multidim`), which also select `presented`, `concept_role`, `substitution_code` and
  `substitution_note`; `FactLookupResult.substitution` and its `to_dict()` key.
- The committed mapping: `FAMILY_ANCHOR_QNAMES` (D1 order; D4 removes the bogus element),
  the override file or a new committed file for the additions, roles, substitutions and
  cik-scoped extensions, and the IFRS rows; `concept_mapping` and `mv_financial_facts` carry
  role and substitution so the SQL can select them. The design is the implementer's;
  `family_member` is its executable reading.
- The synthesizer states a lookup's `substitution["note"]` with the number (prompt change,
  versioned).

## Notes

- **Oracles.** Expected values are the filers' own statement lines: label and value from the
  filing's presentation and label linkbases and statement HTML (as read for the decision
  doc, `data/storage/sec_edgar/`), and SEC companyfacts where it has the fact (cached
  `data/sec_companyfacts/`, 23 of 33 filers; no extension facts; no TSM FY2025). Each fixture
  row carries `companyfacts`: `match` (same accession, end − 1 day, start and value),
  `absent`, `mismatch:<SEC values>` (the coarser duplicates of C6, which SEC does not carry),
  `extension` or `not_cached`. The memberships come from the decision table, which the
  maintainer agreed; never from our `concept_mapping` or views.
- **Fixtures.** `test/spec/concept-family-coverage/filings.json`: rows of `mv_financial_facts`
  (undimensioned, `FY`, canonical filing, the family's period type, the listed concepts) read
  from `xbrl_db` on 2026-11-07, read-only, with `presented` computed as R2 defines it from
  `mv_presentation_network`, and `decimals` as rounded-duplicates defines it. Every case has
  its accession number. No held-out data.
- **CIKs.** ABT 0000001800, AMD 0000002488, AAPL 0000320193, AMZN 0001018724, AVGO
  0001730168, CEG 0001868275, COST 0000909832, CRM 0001108524, CSCO 0000858877, GOOGL
  0001652044, HD 0000354950, INTC 0000050863, KO 0000021344, LLY 0000059478, LMT 0000936468,
  MCD 0000063908, META 0001326801, MRK 0000310158, MSFT 0000789019, MU 0000723125, NFLX
  0001065280, NVDA 0001045810, ORCL 0001341439, PG 0000080424, QCOM 0000804328, TSLA
  0001318605, TSM 0001046179, TXN 0000097476, UNP 0000100885, UPS 0001090727, VST 0001692819,
  WMT 0000104169.
- **Test rows and ranks.** The `_build_result` tests feed each case's member rows with
  `concept_pref_rank`, `concept_role` and the substitution keys taken from `family_member`, as
  the reloaded store will return them. The reload must therefore leave `concept_mapping`
  consistent with `family_member` for these filers: a facts-count `auto_override` ranking
  must not reorder a filer's cik-scoped or reviewed rows, and the reload script checks
  `family_member` against the loaded rows for every fixture filer. When a same-measure
  element or a presented line wins at rank 2 or below (NVDA FY2025 interest, GOOGL FY2025
  other income, NVDA FY2025 marketable securities), the result must still be OK: the
  implementer extends the rank credit for these members (for example per-family equivalence
  sets as revenue-equivalent-concepts does for Revenue) without changing it for families
  that revenue-equivalent-concepts pins (`Revenue`, `NetIncome`, `DeferredRevenue`, `Debt`).
- **Debt guard.** within-concept-confidence's component guard for `Debt` refuses a primary
  answer when a disagreeing component sits at the same period (Tesla's dimensioned "debt
  current"). It must not refuse a substitute answer (PG FY2026: `LongTermDebtNoncurrent`
  beside `DebtCurrent`), which states that the current portion is excluded, nor the combined
  total of C8 (LLY FY2025, AMD FY2023-25), which contains the current part.
- **Graduated tests.** `test/spec/concept-key-integrity/test_concept_key_integrity.py::
  test_each_filer_ranks_its_preferred_concept_first` expected McDonald's reviewed D&A ranking
  to be exactly five rows with `us-gaap:DepreciationAmortizationAndOther` at 3. D4 removes
  that element and D6/D2 add substitutes, so this spec PR relaxes the test to what stays
  true before and after: DepreciationAndAmortization 1, DepreciationDepletionAndAmortization
  2, and Depreciation 50 and AmortizationOfIntangibleAssets 51 present (the removal itself is
  this spec's C1). Generic rankings pinned by graduated tests stay as they
  are: SharesOutstanding, AccountsPayable and OtherNonOperatingIncome (concept-key-integrity),
  OperatingExpenses and Debt (family-measure-equivalence) read the declared lists with no
  override file, so their additions here (the cover count, D33, D14's `InterestAndOtherIncome`)
  go through the committed override file or a new file, never the declared lists; the roles
  change without changing those ranks. revenue-equivalent-concepts' `rank_credit` property
  pins `Revenue`, `NetIncome`, `DeferredRevenue` and `Debt`.
- **Screens.** `xbrl_factstore_screen` ranks the primary band only (screen-concept-rank), so
  D1 adds AMZN and HD to a FY2025 interest-expense screen (C2 checks the per-filer picks) and
  substitutes stay out of screens. Disclosing a substituted filer in a screen is a follow-up.
- **Substitution amounts.** D2 recommends naming the separate amortization amount and D21 the
  other gross lines. Those facts are outside the family (D8's `AdjustmentForAmortization`, an
  AVGO extension), so this spec states a fixed note; naming the amounts is a follow-up.
- **`period_end` and dates.** Not changed here (area G). The decision doc's D16 examples quote
  stored dates: MCD's cover date is 2026-01-31 at SEC (stored 2026-02-01), KO's likely
  2026-02-18 (stored 2026-02-19; KO is not in the companyfacts cache).
- **D14 in every ORCL year.** ORCL tags its non-operating line with the D14 extension in
  every year (FY2025 60,000,000, where the family resolved the notes-only `OtherNonoperating
  IncomeExpense` 91,000,000), so D14 moves ORCL FY2023-26 too, not only FY2022; C2 covers
  FY2022 and FY2025.
- **Decided 2026-11-07 (C8).** Two gaps found while building the fixtures, both marked
  `same measure` by the maintainer:
  - **TSMC interest income.** TSMC tagged its "Interest income" line with an extension named
    `InvestmentIncomeInterest` in its FY2022 (`0001193125-23-107214`) and FY2023
    (`0001193125-24-099840`) 20-Fs, and with `ifrs-full:RevenueFromInterest` in every year
    FY2022-25, with equal values where both exist (22,422,200,000 and 60,293,900,000 TWD). The
    extension ranks after the IFRS element, so no answer changes today; it keeps an answer if
    TSMC drops the IFRS tag (C8's metamorphic test).
  - **Total debt** `us-gaap:DebtLongtermAndShorttermCombinedAmount`, a note-only fact for
    every corpus filer that tags it (`presented` False in all 21 filer-years). Read-only scan of
    `xbrl_db` (canonical FY filings, undimensioned, current period), and what it changes:
    - LLY FY2025: 42,503,000,000 instead of the noncurrent substitute 40,868,000,000 (the
      current part 1,635,000,000 no longer answers, D10). LLY FY2023-24 keep `LongTermDebt`
      (19,104,600,000; 29,474,000,000), which ranks first.
    - AMD FY2022-25, AVGO FY2025, MRK FY2023-25: the total instead of the C4 noncurrent
      substitute (AMD FY2023 2,468,000,000 = 1,717,000,000 noncurrent + 751,000,000 short-term;
      MRK's totals are tagged at `decimals` −8, e.g. 35,100,000,000 for 1,372 + 33,683 $M).
    - ORCL FY2023-26: an answer where only the current part (a component) existed.
    - QCOM FY2023-25: unchanged; its presented `LongTermDebt` keeps answering.
    - ORCL FY2022 is left out of the tests: it tags `LongTermDebt` = 0 (`decimals` INF, a note
      fact, fact 66814) beside the total 75,859,000,000, and rank 1 answers 0 today and after
      this change. Not decided; flagged for the maintainer.
    The combined total and `LongTermDebt` differ in scope (the total includes short-term
    borrowings and commercial paper), so a screen mixes them across filers; the decision
    accepts that. The Debt component guard (Notes, "Debt guard") must not refuse these
    primary answers either: an undimensioned lookup whose winner is the combined total states
    a total that already contains the disagreeing current part.
  Both additions go through the committed override file (or a new file): family-measure-
  equivalence pins the declared `Debt` list.
- **Parts.** Too large for one implementation PR. Suggested split: `impl/concept-family-coverage--part1`
  the mapping and `family_member` (C1, C3's membership tests, C4's membership test), plus
  `candidate_order` and `display_unit` (their tests in C2–C7); then `impl/concept-family-coverage`
  the lookup wiring, SQL, rank credit and synthesizer note (the `_build_result` tests), and the
  reload.
- **Reload** (maintainer, after the merge): regenerate the mapping CSV, populate, override and
  IFRS passes, `--sync-ns-expanded`, rebuild the MVs, check `family_member` against the loaded
  rows for the fixture filers, then rebuild and diff the v1 suite and the frozen v2 suites.
  Expected moves: interest expense for the 21 filers of D1, D&A for MSFT/AMD and the
  substitutes, OtherNonOperatingIncome for GOOGL, INTC, META, MSFT, ORCL, TSM, Debt and
  SharesOutstanding answers becoming substitutes.
