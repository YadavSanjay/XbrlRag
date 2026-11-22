# ifrs-family-meaning — an IFRS filer's family answers the same measure as the us-gaap family, in its reporting currency or the one asked for

Area J of the test strategy, finding J6 and criteria C9 and C10 of the area-J investigation
(`data/eval/segments_ifrs/report.md`, 2026-11-07; not committed). Proven against SEC's
Financial Statement Data Sets (FSDS) for TSMC, the corpus's IFRS filer, 20-F FY2024
`0001193125-25-083423` (FSDS 2025q2), with FY2023 `0001193125-24-099840` (2024q2) and FY2025
`0001628280-26-025362` (2026q2). Amounts below are TWD unless marked; FY2024 unless marked.

Defects of the committed mapping (`family_member`, the `ifrs:` rows of
`configs/concept_mapping/concept_family_coverage.yaml`) and the lookup:

1. **The total including non-controlling interests (NCI) answers the parent's families.**
   `NetIncome` answers `ifrs-full:ProfitLoss` 1,157,523,900,000 ("NET INCOME", incl. NCI);
   the parent's figure, `ifrs-full:ProfitLossAttributableToOwnersOfParent` 1,158,380,200,000
   ("Shareholders of the parent"), ranks below it (101 against 100; rank 5 in the store's
   facts-count ranking for TSMC). The us-gaap family means the parent: `us-gaap:NetIncomeLoss`
   is "Net Income (Loss) Attributable to Parent", and `us-gaap:ProfitLoss` is rank 2 and not
   the same measure (revenue-equivalent-concepts). The same holds for `StockholdersEquity`
   (`ifrs-full:Equity` 4,279,271,600,000 against `EquityAttributableToOwnersOfParent`
   4,244,266,500,000; us-gaap "Equity, Attributable to Parent") and `ComprehensiveIncome`
   (1,229,109,600,000 against 1,230,949,100,000; us-gaap `ComprehensiveIncomeNetOfTax`,
   "Attributable to Parent").
2. **SG&A answers the marketing line alone.** `SellingGeneralAndAdministrative` has one IFRS
   member, `ifrs-full:SalesAndMarketingExpense`, so TSMC's SG&A is OK 13,143,600,000, its
   "Marketing" line. TSMC presents no SG&A total; "General and administrative" is
   83,745,000,000, so the us-gaap measure ("selling ... as well as all other general and
   administrative expenses") would be 96,888,600,000. FY2025: 16,918,100,000 against
   G&A 82,304,300,000.
3. **Operating expenses are not found though presented.** TSMC's "Total operating expenses"
   301,070,400,000 is `ifrs-full:OperatingExpenseExcludingCostOfSales` ("the amount of
   operating expense excluding the cost of sales"), the measure of `us-gaap:OperatingExpenses`
   ("... except for the portion ... included in cost of sales"). The family's IFRS member
   `ifrs-full:OperatingExpense` is not tagged, so the lookup is `NOT_FOUND`.
4. **Interest income answers "Other income".** `InterestIncome` ranks
   `ifrs-full:FinanceIncome` (100) above `ifrs-full:RevenueFromInterest` (101). TSMC's
   `FinanceIncome` is its "Other income" line, 566,900,000 (equal to "Dividend income" on its
   cash-flow statement); its "Interest Income" line is 87,213,400,000 (the income-statement
   extension `InvestmentsIncomeInterest`, and `RevenueFromInterest` on the cash-flow
   statement, equal). Same in FY2023 (480,000,000 against 60,293,900,000).
5. **A per-share figure cannot be asked for in USD.** TSMC files diluted EPS as TWD 44.67 and
   a USD convenience translation 1.36 per share; the store's unit is `shares*TWD` /
   `shares*USD`, and `choose_lookup_unit` compares a requested `USD` with those strings, so a
   lookup with `unit: "USD"` is `NOT_FOUND` (and `TWD` too). Monetary families work: Revenue
   with `unit: "USD"` is 88,268,000,000.

## Requirement

### R1. Parent figures

For `NetIncome`, `StockholdersEquity` and `ComprehensiveIncome`, the element "attributable to
owners of the parent" (`ifrs-full:ProfitLossAttributableToOwnersOfParent`,
`ifrs-full:EquityAttributableToOwnersOfParent`,
`ifrs-full:ComprehensiveIncomeAttributableToOwnersOfParent`) is a primary member that is the
family's own measure (`same_measure` true) and ranks before the NCI-inclusive total
(`ifrs-full:ProfitLoss`, `ifrs-full:Equity`, `ifrs-full:ComprehensiveIncome`), which is not the
same measure. A filer that reports both gets the parent's figure: in the committed mapping,
and in the facts-count per-filer ranking of layer 0 (`filer_ranking`), which must not move the
NCI-inclusive total ahead of it. What answers when only the total is filed is left to the
maintainer (Notes, decision U4).

### R2. SG&A is never the marketing line on its own

`ifrs-full:SalesAndMarketingExpense` is not a primary member of
`SellingGeneralAndAdministrative`. A lookup of the family for a filer that presents marketing
and G&A as separate lines and no total is never OK with the marketing value and no stated
substitution. Whether it answers G&A + marketing as a stated sum, the marketing line as a
stated substitute, or nothing, is the maintainer's decision (Notes, U1); this spec locks only
the refusal of the silent part.

### R3. Operating expenses from the presented total

`ifrs-full:OperatingExpenseExcludingCostOfSales` is a primary member of `OperatingExpenses`
that is the same measure. TSMC's "Total operating expenses" answers, OK, with no substitution.

### R4. Interest income from the interest line

Where a filing tags `ifrs-full:RevenueFromInterest`, `InterestIncome` answers its value (the
filer's "Interest income" line), OK, with no substitution; `ifrs-full:FinanceIncome` never
answers ahead of it, whatever its value or presentation. What `FinanceIncome` is when no
interest element is filed is left to the maintainer (Notes, U2).

### R5. Reporting currency by default, the asked currency on request, per-share included

For every family of R1–R4 and for `Revenue`, `EarningsPerShareBasic` and
`EarningsPerShareDiluted`:

- with no requested unit, the answer is the reporting-currency fact (TWD for TSMC; the unit
  in the filing's comparatives, unit-aware-lookup C2), and the result's unit is `TWD`, or
  `TWD/shares` for a per-share family (`display_unit`);
- with `unit: "<ISO 4217 code>"`, the answer is the fact filed in that currency, including a
  per-share fact (stored `shares*USD`, `USD*shares` or `USD/shares`), and the result's unit is
  the code, or `<code>/shares`; a currency the filing does not carry is `NOT_FOUND`;
- the result never carries a value of one currency under another's unit, so a compute
  (`expr_unit`, units-currency-integrity) over a TSMC answer and a USD filer's answer is
  accepted exactly when the TSMC answer is in USD. Screens already rank only a filer's USD
  reporting currency (screen-reporting-currency); this spec does not change them.

### R6. The rules do not depend on the filer

R1–R5 are rules of the standard IFRS elements, not of TSMC: the same rows under another CIK,
another dated IFRS taxonomy namespace and another extension namespace give the same answers.

## Acceptance criteria

Fixtures: the FSDS rows of the three TSMC 20-Fs (Notes). Expected values are FSDS `num` values
at the filing's fiscal-period end, chosen by FSDS `pre` (the line the filer presents) and the
taxonomy definitions in FSDS `tag`. The lookup's candidate rows are the filing's undimensioned
annual FSDS facts whose family (`fact_family` over the committed mapping) is the looked-up
family, with rank, role and substitution from `family_member`, as the reloaded store returns
them; `_build_result` runs on them unfaked. Tolerance: half a unit of the fact's `decimals`
(−5 for these monetary facts, 2 for EPS; Notes).

- **C1**: Parent figures (R1). For FY2023, FY2024 and FY2025, `NetIncome`,
  `StockholdersEquity` and `ComprehensiveIncome` are OK with the parent element's value and
  fact, unit `TWD`, no substitution: FY2024 1,158,380,200,000; 4,244,266,500,000;
  1,230,949,100,000 (FY2023 851,740,000,000; 3,429,522,400,000; 843,751,900,000; FY2025
  1,697,604,000,000; 5,355,038,700,000; 1,640,975,300,000). `same_measure` is true for each
  parent element and false for each NCI-inclusive total, and the parent ranks first; layer 0's
  `filer_ranking` over the committed generic ranking, with the elements TSMC reports, puts the
  parent element first among the eligible reported ones. Properties: under any CIK, IFRS
  taxonomy date and extension namespace, any row order and any fact ids, the answer is the
  parent's value. Oracle: FSDS `num`/`pre` ("Shareholders of the parent", "Equity attributable
  to shareholders of the parent"); the us-gaap definitions in FSDS `tag`.
- **C2**: SG&A is never silently marketing (R2). `family_member` gives no primary role to
  `ifrs-full:SalesAndMarketingExpense` in `SellingGeneralAndAdministrative`, for any CIK. For
  FY2023, FY2024 and FY2025 the lookup is either not OK, or OK with a value that is the
  marketing line (10,590,700,000; 13,143,600,000; 16,918,100,000) only together with a
  substitution, or the G&A + marketing sum (71,463,500,000; 96,888,600,000; 99,222,400,000)
  within the sum of the two facts' half-units; never anything else. Property: the same under
  any CIK, namespaces, order and fact ids. Oracle: FSDS `pre` (separate "General and
  administrative" and "Marketing" lines, no total) and the us-gaap SG&A definition in FSDS
  `tag`.
- **C3**: Operating expenses (R3). `OperatingExpenses` is OK with the
  `OperatingExpenseExcludingCostOfSales` fact, no substitution, unit `TWD`: FY2023
  253,833,700,000; FY2024 301,070,400,000; FY2025 345,649,600,000; `same_measure` is true for
  the element. Property: the same under any CIK, namespaces, order and fact ids. Oracle: FSDS
  `pre` ("Total operating expenses") and the two definitions in FSDS `tag`.
- **C4**: Interest income (R4). `InterestIncome` is OK with the value of the filer's
  "Interest income" line, no substitution, and never a `FinanceIncome` or `OtherIncome` fact:
  FY2023 60,293,900,000; FY2024 87,213,400,000; FY2025 105,739,100,000. Properties: under any
  CIK, namespaces, order and fact ids the same; adding a presented `FinanceIncome` fact of any
  value at the current period never changes the answer. Oracle: FSDS `pre`/`num` (the
  income-statement "Interest Income" line equals `RevenueFromInterest`; "Other income" is
  `FinanceIncome`/`OtherIncome`); the IFRS definitions in FSDS `tag`.
- **C5**: Currency (R5). For each filing and each of no unit, `TWD` and `USD`, the families
  `NetIncome`, `StockholdersEquity`, `ComprehensiveIncome`, `OperatingExpenses`,
  `InterestIncome`, `Revenue`, `EarningsPerShareBasic` and `EarningsPerShareDiluted` are OK
  with the FSDS value in that currency (TWD by default) and unit `TWD`/`USD` or
  `TWD/shares`/`USD/shares`: for example FY2024 diluted EPS 44.67 TWD/shares by default and
  1.36 USD/shares with `USD`, revenue 2,894,307,700,000 TWD and 88,268,000,000 USD, net income
  35,327,200,000 USD; FY2025 diluted EPS 2.09 USD/shares; `EUR` is `NOT_FOUND`. Properties:
  storing the per-share unit as `shares*C`, `C*shares` or `C/shares` never changes the answer
  or its unit; any CIK, namespaces, order and fact ids give the same. Oracle: FSDS `num` (uom
  per fact, the filing's two currencies); unit-aware-lookup R1–R2.
- **C6**: No currency mixing downstream (R5). The units of the answers make a compute with a
  USD filer's answer valid exactly when TSMC's answer is in USD: `expr_unit("t - a")` with
  TSMC's diluted EPS in USD and a USD filer's `USD/shares` is `USD/shares`, and refused
  (`CalculatorError`) with TSMC's default `TWD/shares`; revenue likewise (`USD`; refused with
  `TWD`); TSMC's own net margin in its reporting currency is `pure`. Property: for any
  requested currency and family of C5, the answer's unit names the requested currency, and
  the compute with a USD counterpart is accepted iff that currency is USD. Oracle:
  units-currency-integrity Requirement 4 (two currencies never meet); R5.

## Interface

No new signature (bug-fix mode). Changed behaviour:

- the committed mapping: the `ifrs:` rows and `same_measure` lists of
  `configs/concept_mapping/concept_family_coverage.yaml` (or wherever the implementer puts
  them), so that `family_member`, `same_measure` and `committed_mapping_rows` (read by
  `fact_family` and by layer 0's generic ranking) give R1–R4;
- `XbrlFactstoreLookup._restrict_to_one_unit` / `choose_lookup_unit` (or the lookup around
  them), so a requested currency matches a per-share unit in that currency (R5).

## Notes

- **Fixtures** (`test/spec/ifrs-family-meaning/fixtures/`): per quarter, SEC's four FSDS files
  with their header and only TSMC's rows of one filing, copied byte-for-byte from the quarter
  zip (checked line by line against the zip; LF line ends): `sub` the filing's row, `num`
  every row of the filing with empty `segments` and `coreg` (undimensioned), `pre` every row
  of the filing, `tag` the (tag, version) pairs those `num` rows use, plus in 2025q2 the
  `us-gaap/2024` rows of the eight us-gaap elements whose definitions the tests quote
  (`provenance.json` lists them). `provenance.json` names
  each accession, quarter, zip URL, zip SHA-256 and size (from
  `data/cache/sec_fsds/raw/manifest.json`) and the retrieval time.

  | Filing | Accession | Quarter | Zip SHA-256 |
  |---|---|---|---|
  | TSMC 20-F FY2023 | 0001193125-24-099840 | 2024q2 | in `provenance.json` |
  | TSMC 20-F FY2024 | 0001193125-25-083423 | 2025q2 | `81bf19bd…5d7` |
  | TSMC 20-F FY2025 | 0001628280-26-025362 | 2026q2 | in `provenance.json` |

- **From FSDS to candidate rows.** A `num` row's concept is `ifrs-full:<tag>` for version
  `ifrs/<year>` (store form `https://xbrl.ifrs.org/taxonomy/<date>/ifrs-full:<tag>`, the dates
  TSMC's filings use: 2023-03-23, 2024-03-27, 2025-03-27), and an extension (store form
  `http://www.tsmc.com/<yyyymmdd>:<tag>`) when its version is the accession. `qtrs` 4 is the
  annual duration, 0 an instant; the store's `period_end` is the FSDS `ddate` plus one day
  (end-exclusive, concept-family-coverage Notes). A per-share tag (FSDS `datatype`
  `perShare`) is stored as `shares*<uom>`, as `mv_financial_facts` holds TSMC's EPS.
  `presented` is a `pre` row of the tag with `stmt` BS, IS, CF, EQ or CI and `inpth` 0.
  `decimals` is the store's (read-only, 2026-11-07, `fact.decimals_value` for every TSMC fact
  of these tags in the three filings): −5 for every monetary fact of the tested tags, 2 for
  EPS; other facts carry None (exact), which only affects tie-breaks between duplicates.
- **Store versus committed mapping.** The tests read the committed mapping, as
  concept-family-coverage does. Today the store also holds TSMC `auto_override` rows
  (`ProfitLoss` rank 1, the parent element rank 5), which C1's `filer_ranking` check covers:
  after the reload they must follow the committed generic order.
- **Eval keys.** The committed suite `test/eval/suites/sql_derived_v1.json` expects TSMC
  FY2024 net income 1,157,523,900,000 (the NCI-inclusive total) in
  `aq-1c-pnl-line-lookup:concept=NetIncome-fy=2024-ticker=TSM` and
  `aq-10a-tsm-twd-lookup:concept=NetIncome-fy=2024`; a suite rebuild after the change moves
  them to the parent's figure. Rebuild and diff the suite after the reload.
- **Not in this spec.** Per-share screens: the flat screen selects `unit = 'USD'`, so no
  per-share fact (`shares*USD`) is ranked, and `select_reporting_currency` would read
  `shares*USD` as a foreign currency; a separate change. TSMC's FY2024 income-statement
  extension `InvestmentsIncomeInterest` needs no mapping row (it equals `RevenueFromInterest`).

### Decisions for the maintainer (not locked)

None of the locked criteria changes a row of `docs/design/XbrlRag_Concept_Decisions.md`: C1
follows that doc's "Equity attributable to the parent is the family's meaning" and the
coverage file's note that `ProfitLoss` is a near-miss; C3 and C4 add IFRS equivalents beside
D9 and D34 without changing them. These open points are left out of the tests:

| ID | Question | Options | Recommendation |
|---|---|---|---|
| U1 | SG&A for an IFRS filer with separate G&A and marketing lines and no total (TSMC) | (a) a stated sum G&A + marketing (96,888.6M), a new derived answer; (b) the marketing line as a stated substitute ("Marketing only; general and administrative reported separately"); (c) not tagged | (b), as D2/D11/D21 chose a stated substitute over a derived sum; (a) is the analyst's figure and would be the first derived family answer |
| U2 | `ifrs-full:FinanceIncome` in `InterestIncome` when no interest element is filed | (a) a primary fallback below `RevenueFromInterest`; (b) a stated substitute ("finance income; can include dividends and other financing income"); (c) not a member | (b): the IFRS definition is broader than interest, and TSMC's is dividend income |
| U3 | `ifrs-full:SalesAndMarketingExpense` in `SellingAndMarketing` | same measure (as D32 did for `MarketingExpense`), or not | same measure; note that under U1 (b) one fact belongs to one family (`fact_family`), so U1 (b) and U3 together need the lookup to read the substitute from the other family |
| U4 | The NCI-inclusive totals when the parent element is not filed | (a) a primary fallback, as `us-gaap:ProfitLoss` is; (b) a stated substitute ("including non-controlling interests") | (a) for consistency with us-gaap; (b) is more honest when NCI is material |
