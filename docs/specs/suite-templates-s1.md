# suite-templates-s1 — the v1 suite's single-fact lookups key the S1 answer

Approved by the maintainer on 2026-11-08. Follows [restatement-s1](restatement-s1.md), which
moved the product's flat lookup and the v1 templates `aq-1a`/`aq-1c` onto rule S1
([evaluation semantics](../design/XbrlRag_Eval_Semantics.md) S1: the latest periodic report
that states the period's value answers; the value is the filing's own column, ending exactly
on its DEI `DocumentPeriodEndDate`; a year with no own column has no answer).

Three more single-fact templates of `configs/eval/sql_derived_templates.yaml` still key the
filing **labelled** with the fiscal year (`fiscal_year = %(fy)s`, latest period first):
`aq-5a-balance-sheet-lookup`, `aq-6a-cash-flow-lookup` and `aq-10a-tsm-twd-lookup`.
Confirmed read-only against the local `xbrl_db` on 2026-11-09 (the committed templates
against `aq-1c`'s S1 shape with each template's measure filter, every one of the 33 filers in
the store, fiscal years 2022–2026):

- **Restated values keyed as originally reported** (aq-6a): INTC FY2022 financing cash flow
  $1,361M from 0000050863-23-000006 where S1 (and the product) answers $1,115M from
  0000050863-25-000009; INTC FY2022 investing -$10,477M vs -$10,231M (same filings); META
  FY2022 capex $31,431M (0001326801-23-000013) vs $31,186M (0001326801-25-000017); META FY2023
  $27,266M (0001326801-24-000012) vs $27,045M (0001628280-26-003942); TSLA FY2023 and FY2024
  capex ($8,898M vs $8,899M, $11,339M vs $11,342M, 0001628280-26-003952). LLY and MCD
  balances and cash flows differ in the last digits because the later 10-K rounds to millions
  (LLY FY2024 total assets $78,714.9M in 0000059478-25-000067, $78,715M in
  0000059478-26-000013).
- **A stale prior-year column keyed as the year** (aq-5a, no exact own-column check): COST
  FY2025 goodwill is keyed $994M from 0000909832-25-000101 at 2024-09-01, the FY2024 balance;
  that 10-K tags no goodwill at its own period end 2025-08-31. The product refuses
  (restatement-s1 C4).
- **A different citation for the same value**: 726 aq-5a, 492 aq-6a and 18 aq-10a
  (ticker, year, concept) combinations key the labelled filing's fact where the product cites
  the latest restating filing's (AAPL FY2022 total assets: 0000320193-22-000108 vs
  0000320193-23-000106; TSM FY2023 net income TWD: 0001193125-24-099840 vs
  0001628280-26-025362). Citations are graded within one filing's equivalence class, so these
  cases grade a correct product answer as a wrong citation.

The set-B consistency check reads only the first lookup case, so it did not see this.

## Requirement

Store rows are rows of `mv_financial_facts` as Python mappings, with the keys
restatement-s1 names (`accession_no`, `fiscal_year`, `fiscal_period`, `filed_date`,
`is_amendment`, `is_canonical_filing`, `document_period_end`, `concept`, `concept_family`,
`unit`, `segment_axis`, `segment_member`, `period_start`, `period_end` as the store's
next-midnight end, `value`, `fact_id`) plus the order keys `concept_role`,
`concept_pref_rank`, `presented` and `decimals` (null for an exact fact).

1. **The pick** — `s1_lookup_pick(rows, fy)` in `src/eval/suites/sql_derived.py` returns the
   one row a v1 single-fact lookup case keys for fiscal year `fy`, or `None`:
   - Rows with a non-null `segment_axis` are ignored.
   - The candidates are `latest_restatement(current_column(rows, fy, "FY"), rows)` as
     restatement-s1 requirements 1–2 define them: the own column of the canonical filing
     labelled `fy`/`FY`, ending exactly on its `document_period_end` + 1 day, each measure
     replaced by the rows of the latest (`filed_date`, `accession_no`) later-labelled
     canonical filing that reports it. No own column means `None`, never an earlier column
     and never a later filing's comparative alone.
   - Component rows (`family_resolution.row_role`) and rows with a null `value` never answer.
   - The rest are ordered as the v1 lookup order (`LOOKUP_ORDER_KEYS`): role (primary
     first), later `period_end`, **reporting currency** first, `presented` first, lower
     `concept_pref_rank` (null last), valued first, finer precision (`decimals` null = exact
     first, then larger), larger `fact_id`. The reporting currency is a unit for which `rows`
     hold a row of the own filing (the canonical filing labelled `fy`/`FY`) of the same
     concept family, no segment, ending before its own period end — TSM's TWD, not the USD
     convenience translation of the current year only. (Ranking the unit by the candidate's
     own filing, as the SQL macro does by `fiscal_year`, fails on the earliest comparative of
     a later 20-F, which has no earlier column.)
   - The result is the first row, as a dict equal to the input mapping.
2. **The templates** — `aq-1a-revenue-lookup`, `aq-1c-pnl-line-lookup`,
   `aq-5a-balance-sheet-lookup`, `aq-6a-cash-flow-lookup` and `aq-10a-tsm-twd-lookup` key
   each case on `s1_lookup_pick` applied to the rows their SQL returns for the bound
   (`ticker`, `fy`, `concept`): the case's expected value, expected fact id and unit are the
   picked row's; no pick means no case. Each template's SQL returns the filer's rows of its
   measure (concept family, period type, annual duration, aq-10a's `TWD`) from **every**
   filing of any fiscal period, with the columns the pick reads; the pick, not the SQL,
   selects the year. The builder's precise-duplicate substitution and fact-equivalence
   classes then run on the picked fact unchanged. How a template declares this (a new
   template key, e.g. `pick: s1_lookup`) is the implementer's choice.
3. Nothing else changes: the screens, `aq-2c` (YoY, on `mv_yoy_comparison`), the derived,
   dimensional and breakdown templates, restatement-s1's functions and every other spec's
   tests. `scripts/xbrl/analyst_queries.sql` #1a/#1c/#5a/#6a/#10a may note the S1 rule; it is
   a reference document and not read by the build.

## Acceptance criteria

Fixtures are real excerpts committed under `test/spec/suite-templates-s1/fixtures/` (Notes).
Store rows are built from the companyfacts entries of corpus filings, with each filing's DEI
from its iXBRL instance; values are checked against FSDS `num` rows of the named accession
(same tag, `qtrs` 4 for durations, 0 for instants), an independent source, and dates against
DEI. Tolerances: amounts at the tagged precision (`decimals` -6 and -5: `abs=5e4`, half of
$0.1M, which separates every pair of values below), accessions and dates exact.

- **C1**: restated durations (aq-6a's measures). `s1_lookup_pick` gives INTC FY2022
  financing $1,115M and investing -$10,231M from 0000050863-25-000009 (never $1,361M or
  -$10,477M from 0000050863-23-000006); META FY2022 capex $31,186M from 0001326801-25-000017
  (never $31,431M); META FY2023 capex $27,045M from 0001628280-26-003942 (never $27,266M);
  INTC FY2025 financing $11,587M from its own 0000050863-26-000011 (no later report). The
  picked row always ends exactly on the fy filing's DEI period end + 1 day. Properties:
  permuting the rows, or removing any filing other than the fy filing and the answer's,
  never changes the pick; adding a later-labelled canonical filing, filed last, that reports
  the period with another value makes it the pick. Oracle: FSDS `num` of the restating and
  original accessions; DEI; requirement 1.
- **C2**: balances and the exact own column (aq-5a's measures). LLY FY2024 total assets
  $78,715M from 0000059478-26-000013 (never $78,714.9M from 0000059478-25-000067); COST
  FY2025 goodwill has no pick (its 10-K 0000909832-25-000101 tags goodwill only at
  2023-09-03 and 2024-09-01, never at its DEI end 2025-08-31); COST FY2026 goodwill $994M at
  2026-08-30 from 0000909832-26-000093. Properties: every pick ends on its fy filing's DEI end
  + 1 day; removing the fy filing's own-column rows makes the pick `None` whatever other
  filings report. Oracle: FSDS `num` (LLY), companyfacts entries (COST), DEI (exact day);
  requirement 1.
- **C3**: TSM in TWD (aq-10a's measure; the reporting currency). Over TSM's net income rows
  in both units, `s1_lookup_pick` gives FY2022 TWD 993,294.7M from 0001193125-25-083423 (the
  20-F filed in 2026 covers 2023–2025 only), FY2023 TWD 851,027.7M and FY2024 TWD
  1,157,523.9M from 0001628280-26-025362, unit `TWD` each, never the USD convenience
  translation the fy filing also tags (FY2023 USD 27,793.2M). Property: adding or removing
  USD rows of any of TSM's filings never changes the pick. Oracle: FSDS `num` (TWD and USD
  rows of each 20-F); requirement 1.
- **C4**: the templates. `SqlDerivedBuilder` over the five templates as committed in
  `configs/eval/sql_derived_templates.yaml` and the fixture store (the fake database of
  Notes) emits: aq-6a INTC FY2022 CashFromFinancing $1,115M and CashFromInvesting -$10,231M
  citing 0000050863-25-000009's facts, META FY2022/FY2023 Capex $31,186M / $27,045M; aq-5a
  LLY FY2024 TotalAssets $78,715M citing 0000059478-26-000013, COST FY2026 Goodwill $994M, and
  **no** COST FY2025 Goodwill case; aq-10a TSM FY2023 NetIncome TWD 851,027.7M citing
  0001628280-26-025362, unit `TWD`; aq-1c TSM FY2023 NetIncome the same TWD fact; aq-1a INTC
  FY2022 Revenue $63,054M citing 0000050863-25-000009. Properties: every case of the five
  templates cites a fact of a canonical filing ending exactly on the DEI end + 1 day of the
  filer's fy filing, and no other filing of the store, labelled later and filed later,
  reports that concept, unit and period (S1 checked by hand in the test); permuting the
  store's rows never changes any case's value or cited accession. Oracle: FSDS `num`, DEI,
  the S1 rule applied by hand; requirement 2.

## Interface

New, stubbed in the spec PR:

`src/eval/suites/sql_derived.py`:
- `s1_lookup_pick(rows: Sequence[collections.abc.Mapping[str, Any]], fy: int) -> dict | None`

Behaviour changes, no signature change: `SqlDerivedBuilder` (requirement 2), the five
templates' SQL in `configs/eval/sql_derived_templates.yaml`, and whatever template key
declares the pick (`Template.parse`).

## Notes

- **Fixtures** (`test/spec/suite-templates-s1/fixtures/`, built read-only from the local SEC
  cache on 2026-11-09; nothing under the operator's data directory is read by the tests):
  - `companyfacts.json`: per filer (INTC, META, LLY, COST, TSM), the companyfacts entries of
    report forms filed from 2021-06-01 for the tags used here (INTC financing, investing and
    `RevenueFromContractWithCustomerExcludingAssessedTax`; META
    `PaymentsToAcquirePropertyPlantAndEquipment`; LLY `Assets`; COST `Goodwill`; TSM
    `ifrs-full:ProfitLoss` in TWD and USD), with the source URL
    `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, the SHA-256 and size
    of the full document and its date in the local cache (INTC and COST are byte-identical to
    restatement-s1's). TSM's document holds no entry of its 2026 20-F.
  - `dei.json`: the 23 corpus filings of these filers (as `mv_financial_facts` lists them on
    2026-11-09): form, filing date, DEI `DocumentType`, `DocumentPeriodEndDate`,
    `DocumentFiscalYearFocus`, `DocumentFiscalPeriodFocus` and the us-gaap / ifrs-full
    namespace from the filing's iXBRL instance, with the EDGAR URL of the primary document
    and the SHA-256 of the instance.
  - `fsds.json`: FSDS `sub` and `num` rows (no segments, no co-registrant) of the oracle
    filings, with each quarter's archive URL, SHA-256 and retrieval date. FSDS holds no
    COST goodwill row (and the FY2026 10-K, filed 2026-10-07, is in no cached quarter); COST's
    value oracle is companyfacts, its date oracle DEI.
- **Store rows from the fixtures** (`conftest.py`), as in restatement-s1: one row per
  companyfacts entry of a corpus filing (TSM: per FSDS `num` row with `qtrs` 4, because the
  companyfacts document holds no entry of the 2026 20-F 0001628280-26-025362, which the store
  holds; TSM's fiscal year is the calendar year, so FSDS's month-end date is exact); `fiscal_year` / `fiscal_period` from DEI;
  `period_end` = `end` + 1 day; concept `<namespace>:<tag>` with the filing's own namespace;
  all filings canonical (none of these filers has an amendment in the corpus);
  `concept_pref_rank` 1, `concept_role` null, `presented` false, `decimals` null.
- **Fake-database contract** (C4; `SqlDerivedBuilder` takes the connection, the I/O
  boundary):
  - a query with parameter `ids` is the fact-equivalence query: per anchor, the ids of the
    store rows of the anchor's accession, concept, value, period, member and unit;
  - a query with parameter `id` is the precision query: the rows of the anchor's accession,
    concept, period, member and unit, as columns `fact_id`, `value`, `decimals`;
  - a query on `publish_manifest` or `entity_master` returns nothing;
  - any other query mentioning `mv_financial_facts` returns **every** store row (all keys
    as columns) satisfying each equality predicate of its SQL of the form
    `[alias.]<col> = %(<param>)s` or `[alias.]<col> = '<literal>'` for `<col>` in `ticker`,
    `concept_family`, `unit`, `period_type`, `duration_kind`, and ignores every other
    predicate, join, `DISTINCT ON`, `ORDER BY` and `LIMIT`. Year selection therefore has to
    happen in `s1_lookup_pick`. The template SQL must not restrict `fiscal_year` or
    `fiscal_period` in Postgres either (a later filing's comparative belongs to the rows);
    the fake cannot check that, the live agreement test below does.
- **Why a Python pick.** restatement-s1 converted aq-1a/aq-1c in SQL and could not test
  them (no Postgres in tests). Moving the year selection into one Python function, shared by
  the five templates, makes the templates testable through the builder and keeps one
  definition (as `LOOKUP_ORDER_KEYS` does for the order). The unit test
  `test/unit/test_sql_derived_suite.py::TestLookupOrder` asserts `@LOOKUP_ORDER(f)` in these
  templates' SQL; the implementer updates it (a unit test, not locked).
- **COST FY2025 goodwill.** The FY2026 10-K tags goodwill at 2025-08-31 as a comparative.
  Read literally, S1 would answer $994M from it; restatement-s1 requirement 1 (no own column,
  no answer) makes the product refuse, and `fiscal_year_key` raises `KeyNotFoundError`. The
  suite follows the product: no case.
- **Live check after implementation** (not a test, the maintainer runs it after the
  rebuild): `pytest -m integration test/integration/test_v1_suite_lookup_agreement_live.py`
  (every v1 lookup case cites the product's answer); the comparison of 2026-11-09 should then
  show no aq-5a/6a/10a difference.
