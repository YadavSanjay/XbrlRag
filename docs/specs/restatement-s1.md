# restatement-s1 — the latest periodic report answers a restated period

Found in the area-G investigation of fiscal periods and restatements (2026-11-07, findings
N5a, N5b and G1; the read-only report and its CSVs are in the operator's local eval data,
`fiscal_periods/report.md`). Approved by the user on 2026-11-07, with the policy: **a
split-adjusted or restated value from the latest periodic report answers (S1), and the value
as originally reported is available on request.**

[Evaluation semantics](../design/XbrlRag_Eval_Semantics.md) S1 says "revenue for fiscal 2022"
is the value for that period in the latest filing in the corpus that reports it, and that the
values of a comparison come from one filing. The answer keys follow S1
(`fiscal_year_key` → `latest_filed_key`); the product does not:

- **Lookup and flat screen serve the original.** Both filter `fiscal_year = fy`, the label of
  the filing whose own period it is, so a later 10-K's restated or split-adjusted comparative
  (labelled `fy + 1` or `fy + 2`) is never a candidate. NVDA FY2023 diluted EPS answers $1.74
  (0001045810-23-000017); the FY2025 10-K (0001045810-25-000023) reports $0.17 after the
  10-for-1 split. WMT FY2023 EPS $4.27 vs $1.42 (0000104169-24-000056 and -25-000021); AVGO
  FY2022 $26.53 vs $2.65 (0001730168-24-000139); INTC FY2022 financing cash flow $1,361M vs
  $1,115M (0000050863-24-000010, -25-000009); AMD receivables at 2023-12-30 $5,376M vs $4,323M
  (0000002488-25-000012); HD FY2023 D&A $3,247M vs $3,061M (0000354950-25-000085,
  0001628280-26-019436).
- **The cross-check cannot see it.** `v2_suite._Keyer._cross_check` asks the fact store for the
  value **at the key's own accession**; the store holds the restating filing's comparative, so
  the key shows "verified by fact_store" while the product answers the other value.
- **`mv_yoy_comparison` mixes bases.** It pairs this filing's current column with the **prior
  filing's** current column; after a split or a recast the two sides are on different bases.
  NVDA FY2025 EPS growth is stored as -75.4% (2.94 vs 11.93); the FY2025 10-K's own columns give
  +147.1% (2.94 vs 1.19). WMT FY2024 -55.3% vs +34.5%; INTC FY2023 financing +524.9% vs +662.8%.
  58 family-mapped FY YoY rows in 20 filers are on mixed bases (55 off by 1 pp or more).
- **Part III-only 10-K/As are cited by keys.** The corpus has four 10-K/As that carry only the
  cover facts `dei:EntityCommonStockSharesOutstanding` and `dei:EntityPublicFloat` (AMD
  0000002488-26-000021, MRK 0001193125-24-093194, TSLA 0001104659-25-042659 and
  0001104659-26-053166). The store is right (each is `superseded_by` its 10-K,
  [annual-amendment-handling](annual-amendment-handling.md)), but `latest_filed_key` has no
  form rule and cites the 10-K/A for 7 of the 8 cover periods (AMD's is filed the same day as
  its 10-K and wins the accession tie). The values are equal today; the cited accession is
  not canonical.
- **A stale comparative answers for an empty year (G1).** When the requested year's own column
  has no fact for the concept, the lookup (order: `period_end DESC`) and
  `labelled_fiscal_year_key` (`period_end = max(end)`) both fall back to a prior-year
  comparative of the same filing and present it as the requested year, so key and product
  agree on a wrong period: INTC FY2025 dividends declared per share $0.38 is FY2024's
  (0000050863-26-000011); COST FY2025 goodwill $994M is dated 2024-09-01
  (0000909832-25-000101); KO FY2025 restructuring `BusinessExitCosts1` $32M is FY2023's
  (0001628280-26-010047); QCOM FY2024 restructuring $712M is FY2023's (0000804328-24-000075).

Out of scope, coordinated elsewhere: a sign disagreement between filings (`v` in one, `-v` in a
later one) is the `sign-and-nil` spec's; this spec takes the later value as tagged and its
fixtures hold no sign flip. The ten fiscal-period items of the same report (10-Q labels, quarter
labels, calendar bounds, screen durations, 53-week disclosure, average fallbacks, Q4) are later
specs.

## Requirement

Store rows below are rows of `mv_financial_facts` (Layer 1b) as Python mappings. The keys this
spec reads are `accession_no`, `fiscal_year` (the filing's label), `fiscal_period`,
`filed_date`, `is_amendment`, `is_canonical_filing`, `document_period_end` (the filing's DEI
`DocumentPeriodEndDate`, an inclusive SEC date; new on the view, from `mv_dei_metadata`),
`concept`, `unit`, `segment_axis`, `segment_member`, `period_start`, `period_end` (the store's
next-midnight end: SEC's inclusive end plus one day, for durations and instants), `value` and
`fact_id`. Dates may be `datetime.date` or ISO strings; the functions accept both.

A **group** is one measure at one period: (the concept's local name, `unit`, the local name of
`segment_axis`, the local name of `segment_member`, `period_start`, `period_end`). Local names
(after the last `:`) are used because filers re-declare the us-gaap namespace every year
(`http://fasb.org/us-gaap/2022:EarningsPerShareDiluted` in one 10-K, `.../2024:...` in the
next), as `mv_yoy_comparison` already does.

1. **The filing's own column** — `current_column(rows, fy, fiscal_period="FY",
   as_originally_reported=False)` in a new module `src/retriever/xbrl/restatement.py`:
   - The **own filings** are the accessions of rows with `fiscal_year == fy` and
     `fiscal_period == fiscal_period`. By default they are those with `is_canonical_filing`
     true. With `as_originally_reported`, it is the single own accession with `is_amendment`
     false and the smallest (`filed_date`, `accession_no`); none means an empty result.
   - It returns the own filings' rows with a non-null `value` whose `period_end` is exactly
     `document_period_end` + 1 day (**exact day**; never the nearest end, never an earlier
     column). One exception keeps the cover-page share count that
     [shares-outstanding-reading](shares-outstanding-reading.md) reads as a disclosed
     substitute: a row whose concept's local name is `EntityCommonStockSharesOutstanding` is
     kept when its inclusive end (`period_end` - 1 day) is after `document_period_end` and on
     or before `filed_date`.
   - The result is empty when no row qualifies. Rows keep their identity (the same mappings,
     or equal dicts); order is not significant.
2. **The latest restatement** — `latest_restatement(current, rows)`: for each group of
   `current`, the **candidates** are the rows of `rows` in the same group with a non-null
   `value`, `is_canonical_filing` true, a non-null `filed_date` and a `fiscal_year` greater
   than the `fiscal_year` of the group's `current` rows (a later-labelled filing: a later 10-K,
   or a 10-Q of a later year, reporting the period as a comparative). With candidates, the
   group's result is every candidate row of the accession with the greatest (`filed_date`,
   `accession_no`); without, the group's `current` rows unchanged. The result is the union over
   the groups. A row lacking any of these keys is never a candidate, and a `current` row
   lacking a group key (`concept`, `unit`, `period_end`, ...) is returned unchanged (the
   screens' existing fakes return such rows).
3. **S1 in the lookup.** For a lookup step on `mv_financial_facts` (no dimension or one), the
   rows that may answer are `latest_restatement(current_column(R, fy, fp), R)`, where `R` is
   every stored row of the filer for the step's measure (its concept family, period type,
   duration kind and dimension), from every filing of any fiscal period: a later 10-Q's
   comparative of a year-end balance counts, as it does for `latest_filed_key`. With
   `"as_originally_reported": true` on the step they are `current_column(R, fy, fp,
   as_originally_reported=True)` (the step option exists today; its meaning is now this). The
   existing order, unit, rounded-duplicate, confidence and ambiguity rules then run on those
   rows unchanged, and the citation is the answering row's accession. No rows means
   `NOT_FOUND`, with a note naming the year (the G1 fix). The answer must be the same however
   many extra rows the store returns beyond `R` (see the fake-database contract in Notes).
   `as_of` lookups keep today's behaviour; the multidimensional path
   (`mv_financial_facts_multidim`) is a later spec.
4. **S1 in the flat screen.** The flat (non-dimensional, `metric_kind: value`) screen keeps
   picking each filer's row of fiscal year `fy` as today (the fy filing's own column), then
   replaces it by `latest_restatement([row], R_filer)` for that filer's stored rows of the
   measure (as `R` in requirement 3). The ranked row carries the restated value, its `fact_id` and its accession in
   `citation.accession_no`, and keeps `fiscal_year == fy`. Segment, geographic and derived
   screens are a later spec.
5. **YoY on one basis** — `yoy_pairs(rows, fy, fiscal_period="FY")`: for each row `c` of
   `current_column(rows, fy, fiscal_period)` (cover-page share counts excluded), the prior row
   `p` is a row of `c`'s accession in the same group except the period, with a non-null
   `value`, whose `period_end` is 350 to 380 days (inclusive) before `c`'s, and, for a
   duration, whose length differs from `c`'s by at most 14 days (an instant needs a null
   `period_start`). Several such rows: the latest `period_end`, then the greatest `fact_id`.
   That is the filer's own comparison, `same_filing` true. Without one in `c`'s filing, the
   prior is the same group (except the period) in `current_column(rows, fy - 1,
   fiscal_period)`, `same_filing` false; without that either, `c` has no pair. Each pair is a
   dict `{"current": c, "prior": p, "same_filing": bool, "yoy_pct_change": float | None}`,
   with `yoy_pct_change = (c.value - p.value) / abs(p.value)`, `None` when `p.value` is 0.
   `scripts/ddl/mv/33_mv_yoy_comparison.sql` implements the same pairing: the prior side is
   joined on the current side's accession (`prev.accession_no = curr.accession_no`), with the
   prior filing's current column only as the fallback, and a `same_filing` column says which.
6. **Part III-only amendments are never canonical, never the key.**
   `carries_financial_data(company_facts, accession)` in `src/eval/keys/core.py` is true
   exactly when the companyfacts document has a non-nil entry of that accession in a taxonomy
   other than `dei` (us-gaap, ifrs-full, srt, a filer extension...). A Part III-only amendment
   tags only the `dei` cover facts. `latest_filed_key`, and through it `fiscal_year_key` /
   `labelled_fiscal_year_key`, ignore every entry whose form ends in `/A` and whose accession
   carries no financial data, as if absent: such an entry never wins S1, never breaks a tie,
   and a period only it reports is `KeyNotFoundError`. Any other amendment (a restating 10-K/A,
   or one that re-files a single note) is a normal report and wins S1 when filed last; the
   locked sec-keys and v2-suite-build tests rely on such amendments (their synthetic 10-K/As
   tag one statement concept and no balance sheet), which is why the key rule is "cover only"
   and not `has_primary_statements` (Notes). On the product side the store's supersession
   (annual-amendment-handling C3) already makes a Part III-only amendment non-canonical;
   requirements 1–2 read only canonical rows.
7. **No stale comparative in keys.** `labelled_fiscal_year_key` (and `fiscal_year_key`) key the
   period that ends exactly on the labelled filing's own period end: the latest end among that
   accession's annual-length durations (350–380 days, any concept) not after its `filed` date,
   the rule `annual_filing_labels` already uses. An entry set without that end (for an
   instant, an entry at that date; for a duration, an annual-length one ending then) is
   `KeyNotFoundError`, never an earlier column. A filing with no annual-length duration falls
   back to today's rule.
8. Nothing else changes: `as_reported_key`, `cover_shares_key`, `calendar_year_key`, the key
   record, and every other spec's tests.

## Acceptance criteria

Fixtures are real excerpts committed under `test/spec/restatement-s1/fixtures/` (Notes). Store
rows are built from the companyfacts entries of corpus filings, with each filing's DEI from its
iXBRL instance; oracle values come from FSDS `num` rows of the named accession (same tag, `qtrs`
4 for durations, 0 for instants), an independent source. Tolerances: per-share values to the
cent (`abs=0.005`, half a cent), amounts at the fact's tagged precision (`decimals` -6:
`abs=5e5`), percentages to 0.1 pp (`abs=1e-3` on the fraction), accessions and dates exact.

- **C1**: S1 answers a restated period, the original on request. `latest_restatement` over
  `current_column` returns: NVDA FY2023 diluted EPS 0.17 from 0001045810-25-000023; WMT FY2023
  1.42 from 0000104169-25-000021 (the latest of two restating 10-Ks); AVGO FY2022 2.65 from
  0001730168-24-000139; INTC FY2022 financing cash flow $1,115M from 0000050863-25-000009; AMD
  receivables at 2023-12-30 $4,323M from 0000002488-25-000012; HD FY2023 D&A $3,061M from
  0001628280-26-019436. With `as_originally_reported` they are 1.74 (-23-000017), 4.27
  (-23-000020), 26.53 (-22-000118), $1,361M (-23-000006), $5,376M (-24-000012), $3,247M
  (-24-000062). `XbrlFactstoreLookup.lookup` returns the same values and cites the same
  accessions, `OK`; the flat screen ranks INTC FY2022 financing, HD FY2023 D&A and AMD FY2023
  receivables at the restated values and accessions with `fiscal_year` the requested year.
  Properties: adding an older filing, removing a filing older than the answer's, or permuting
  the rows never changes the answer; the answer's accession equals `latest_filed_key`'s on the
  same filings; the screen and the lookup agree. Oracle: FSDS `num` of the restating and
  original accessions; requirements 1–4.
- **C2**: YoY on one basis. `yoy_pairs` gives NVDA FY2025 diluted EPS +147.1% (2.94 / 1.19 - 1),
  WMT FY2024 +34.5% (1.91 / 1.42 - 1), INTC FY2023 financing +662.8% (8,505 / 1,115 - 1), each
  with both rows from the current filing (0001045810-25-000023, 0000104169-24-000056,
  0000050863-24-000010) and `same_filing` true; never -75.4%, -55.3% or +524.9%. The YoY DDL
  joins the prior side on the current accession. Properties: the pair depends only on the
  current filing when it has the prior column (adding or removing other filings changes
  nothing); without the prior column the fallback is the prior filing's own column with
  `same_filing` false. Oracle: FSDS `num` of the current filing; requirement 5.
- **C3**: Part III-only amendments. `carries_financial_data` is false for the four corpus
  amendments (and for an accession with no entries) and true for their 10-Ks and for TSLA's
  2012 full-statement 10-K/A 0001193125-12-137560 (538 entries, with `Assets` and
  `NetIncomeLoss`); one valued non-`dei` entry makes an amendment financial, a nil one does
  not. `latest_filed_key`
  cites the 10-K, never the 10-K/A, for all 8 cover periods (AMD shares 2026-01-30 and public
  float 2024-06-28 → 0000002488-26-000018; MRK 2024-01-31 and 2023-06-30 →
  0001628280-24-006850; TSLA 2025-01-22 and 2024-06-28 → 0001628280-25-003063; TSLA
  2026-01-23 and 2025-06-30 → 0001628280-26-003952). A synthetic restating 10-K/A for TSLA
  FY2025, built in the 2012 one's shape (balance sheet and income statement re-filed, net income
  changed), wins both the key and the product's S1, while `as_originally_reported` still
  answers the 10-K. The product never answers from a Part III-only amendment's rows.
  Properties: adding a Part III-only amendment, filed on any day at or after its 10-K, never
  changes a key or the product's answer; adding a full-statement amendment filed last always
  makes it the cited accession. Oracle: companyfacts entries and EDGAR forms; requirement 6;
  annual-amendment-handling C3 for which filing is canonical.
- **C4**: no stale comparative. `current_column` is empty for INTC FY2025 dividends declared per
  share, COST FY2025 goodwill, KO FY2025 `BusinessExitCosts1` and QCOM FY2024
  `RestructuringAndRelatedCostIncurredCost`; the lookup returns `NOT_FOUND` for each; and
  `fiscal_year_key` raises `KeyNotFoundError` for each. The same filings' own columns still
  answer (INTC FY2025 financing $11,587M ending 2025-12-27; COST FY2026 goodwill $994M at
  2026-08-30), each ending exactly on the DEI `DocumentPeriodEndDate`. Properties: every row
  `current_column` returns ends exactly on its filing's DEI period end; removing a year's own
  column makes both the product and the key refuse rather than fall back. Oracle: DEI of the
  iXBRL instance (exact day); companyfacts and FSDS hold no entry at that end; requirements 1,
  3 and 7.

## Interface

New, stubbed in the spec PR:

`src/retriever/xbrl/restatement.py` (new module):
- `current_column(rows: Sequence[Mapping[str, Any]], fy: int, fiscal_period: str = "FY",
  as_originally_reported: bool = False) -> list[dict]`
- `latest_restatement(current: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]])
  -> list[dict]`
- `yoy_pairs(rows: Sequence[Mapping[str, Any]], fy: int, fiscal_period: str = "FY")
  -> list[dict]`

`src/eval/keys/core.py`:
- `carries_financial_data(company_facts: dict, accession: str) -> bool`

Behaviour changes, no signature change: `XbrlFactstoreLookup.lookup` (requirement 3),
`XbrlFactstoreScreen.screen` flat view (4), `latest_filed_key` and `labelled_fiscal_year_key`
(6, 7), `scripts/ddl/mv/33_mv_yoy_comparison.sql` (5), and `mv_financial_facts` gains
`document_period_end` (needed by requirement 1; the lookup and screen read it).

## Notes

- **Fixtures** (`test/spec/restatement-s1/fixtures/`, built read-only from the local SEC cache on
  2026-11-07; nothing under the operator's data directory is read by the tests):
  - `companyfacts.json`: per filer, the companyfacts entries of report forms (10-K, 10-K/A,
    10-Q, 20-F and amendments) filed from 2021-06-01 for the concepts used here plus
    `NetIncomeLoss`, `Assets` and the two cover facts; TSLA also keeps the 2012 10-K
    (0001193125-12-081990) and 10-K/A (0001193125-12-137560). Each filer records the source URL
    `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, the SHA-256 and size of
    the full document (equal to the 2026-11-07 refresh record) and the retrieval date.
  - `dei.json`: per corpus filing of these filers (56 filings), the form, filing date, DEI
    `DocumentType`, `DocumentPeriodEndDate`, `DocumentFiscalYearFocus`,
    `DocumentFiscalPeriodFocus` and the us-gaap namespace from the filing's iXBRL instance,
    with the EDGAR URL of the primary document and the SHA-256 of the instance.
  - `fsds.json`: FSDS `sub` and `num` rows (no segments, no co-registrant) of the oracle
    filings, with each quarter's archive URL
    (`https://www.sec.gov/files/dera/data/financial-statement-data-sets/<q>.zip`), SHA-256 and
    retrieval date. FSDS rounds `period` and `ddate` to the month end, so it is the value oracle
    and the DEI instance is the date oracle. The four Part III-only amendments are not in FSDS.
  - The synthetic restating amendment is built in `conftest.py` from TSLA's real FY2025
    entries; its accession `0001318605-26-900001` is not a real filing.
- **Store rows from the fixtures** (`conftest.py`): one row per companyfacts entry of a corpus
  filing; `fiscal_year` and `fiscal_period` from the filing's DEI (the store's label equals DEI
  for these filings, report G10); `period_end` = companyfacts `end` + 1 day; concept
  `<us-gaap namespace>:<tag>` with the filing's own namespace (cover facts use
  `http://xbrl.sec.gov/dei/<year>`); `is_canonical_filing` false only for the Part III-only
  amendments (as `filing_registry.superseded_by` holds them) or, in the synthetic scenario, for
  the filings the restating amendment supersedes; `concept_pref_rank` 1; `decimals` unknown.
- **Fake-database contract** (lookup and screen wiring tests; `psycopg2.connect` is the I/O
  boundary):
  - the publish-manifest and coverage-column probes (`to_regclass`) answer "absent";
  - a query mentioning `entity_master` gets the roster (`cik`, `ticker`);
  - a query mentioning `filing_registry` gets every registry row of the filer named by a `cik`
    parameter, ordered by (`filed_date`, `accession_no`);
  - a query on `mv_financial_facts` whose SQL contains the literal `unit = 'USD'` is the flat
    screen's candidate query: per filer, every canonical own-column row of its latest
    `fiscal_year <= fy`, and the screen picks one (changed by
    [screen-candidate-order](screen-candidate-order.md): the pick left the SQL's `DISTINCT ON`);
  - every other query on `mv_financial_facts` returns **every** stored row that matches the
    equality parameters `cik`, `ticker`/`tickers`, `concept_family`, `period_type`,
    `duration_kind` and `fiscal_period` when the query passes them, and ignores every other
    predicate (`fiscal_year`, `accession_no`, `is_canonical_filing`, `is_current_period`,
    `LIMIT`). The selection therefore has to happen in `current_column` /
    `latest_restatement`, not in SQL alone. The query that reads `R` must not pass
    `fiscal_period` (a 10-Q comparative belongs to `R`), and a restatement query in the screen
    must not carry the literal `unit = 'USD'` (pass the unit as a parameter).
- **Other specs' fakes.** The screens' locked fakes (screen-exact-fiscal-year,
  screen-universe, screen-reporting-currency, screen-full-universe) answer any query by
  `params["fy"]` and return rows of the requested year or earlier, without `filed_date` or
  `is_canonical_filing`: the screen's restatement query must pass `fy` as a parameter, and
  requirement 2 then leaves those rows unchanged. A throwaway reference implementation of
  requirements 1–7 (not committed) passed every restatement-s1 test except the DDL check, and
  showed that a `has_primary_statements` key rule fails sec-keys C1 (`test_same_day_ties`) and
  v2-suite-build C4/C6, whose synthetic 10-K/As tag one statement concept only.
- **Two amendment rules.** The store decides canonical by `has_primary_statements` (balance
  sheet plus an income-statement total, annual-amendment-handling); the keys ignore only
  cover-only amendments (requirement 6). They agree on every corpus amendment and on TSLA's
  2012 one. An amendment that re-files one note without the statements would stay
  non-canonical in the store (its original answers) but win in the keys; none is in the
  corpus, and aligning the two rules is a decision for the user.
- **Why the later-labelled rule** (requirement 2) and not "filed later": a restating 10-K/A of
  the same year is already the canonical own filing (requirement 1), and the screens' existing
  fakes return rows of the requested year without filing dates, which therefore never replace.
- **The v1 SQL-derived suite.** `configs/eval/sql_derived_templates.yaml` `aq-1a`/`aq-1c` mirror
  today's lookup (`fiscal_year = %(fy)s`, latest period first). After this change they disagree
  with the product on restated periods and stale comparatives. They are SQL over Postgres and
  not testable here; the implementer brings them to requirements 1–3 and the suite is rebuilt
  and diffed (root `CLAUDE.md`).
- **Cross-check.** With requirement 3, the store's S1 answer and the key's accession coincide
  whenever the store holds the restating filing, so `_cross_check` at the key's accession is
  again a check of what the product answers. Checking the store's S1 pick directly (a new
  `FactStoreSource` method) is a possible follow-up, not part of this spec: it would change
  the locked v2-suite-build seam.
- **YoY fallback.** Requirement 5 keeps a pair when the current filing has no prior column,
  flagged `same_filing` false, instead of dropping it; the view exposes the same flag.
- **Live check after implementation** (not a test): rerun the report's N5a queries; the 58
  mixed-basis FY YoY rows and the 107 family-mapped restated periods should answer on the
  latest basis, and the 4 G1 family-years refuse.
