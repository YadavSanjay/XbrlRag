# dimensional-s1 — the latest periodic report answers a dimensional fact

Status item P3 (`data/handover/v2_status.md`). [Evaluation semantics](../design/XbrlRag_Eval_Semantics.md)
S1 says "revenue for fiscal 2022" is the value for that period in the latest filing in the
corpus that reports it, and that the values of a comparison come from one filing.
[restatement-s1](restatement-s1.md) brought the non-dimensional lookup and the flat screen to S1
(`src/numerics/restatement.py`, re-exported by `src/retriever/xbrl/restatement.py`). Its
"Gap today": the multidimensional lookup, the segment, geographic and derived screens still
read the FY N filing only. This spec closes the gap for **dimensional** facts.

What the code does today (paths researched for this spec):

- **Multidimensional lookup** (2+ dimensions, `XbrlFactstoreLookup._query_multidim` on
  `mv_financial_facts_multidim`, and the one-hint neutral-member retry that reads through it,
  [multidim-recovery](multidim-recovery.md)): `WHERE fiscal_year = %(fy)s AND fiscal_period =
  ...`, no canonical filter, `period_start` selected as NULL. The FY N 10-K's value answers; a
  later 10-K's recast comparative is never a candidate. Intel FY2022 CCG notebook revenue
  (`StatementBusinessSegmentsAxis` x `ConsolidatedEntitiesAxis` x `ProductOrServiceAxis`) is
  $18,783M in the FY2022 10-K (0000050863-23-000006) and $18,781M in the FY2023 10-K
  (0000050863-24-000010).
- **One-dimension lookup** (`_query_flat` + `_select_s1`) already runs restatement-s1's rule,
  whose group is (concept, unit, `segment_axis`, `segment_member`, period). The flat view files a
  multi-dimension fact (a cell) under its first dimension only, so a later filing's **cell**
  falls into the group of the member's own single-dimension fact, wins it, and is then set aside
  (`_set_aside_cells`): Intel FY2022 "All other" operating income (-$5,614M as filed, -$5,977M
  in the FY2023 10-K) loses to the FY2024 10-K's cell "All other x Operating segments" ($1,174M,
  a different measure after Intel's 2024 reorganisation), and the neutral retry then answers
  that cell.
- **Segment and geographic screens** (`_query_segment`, `_query_geographic`, `_query_region`
  on `mv_segment_matrix` / `mv_geographic_facts`): the views hold only each canonical filing's
  current-period rows, and the screens rank the FY N filing's row. NVIDIA FY2023 Taiwan revenue
  ranks from the FY2023 10-K (0001045810-23-000017) though the FY2025 10-K (0001045810-25-000023)
  reports it later; Intel FY2022 CCG operating income ranks $6,266M where the FY2024 10-K reports
  $8,207M. The ranked row cites no accession.
- **Breakdown** (`XbrlFactstoreBreakdown`, [breakdown-op](breakdown-op.md),
  [breakdown-reconciling-items](breakdown-reconciling-items.md)): total, members, arcs and
  lines all from the FY N filing. NVIDIA's FY2025 10-K splits FY2023 revenue into five countries
  (Singapore split out of "Other countries": $3,623M + $2,288M for the FY2023 10-K's $5,911M).
- **YoY** (`mv_yoy_comparison`) already pairs on one accession for dimensional rows too
  (restatement-s1 R5); nothing to do.
- **Derived metrics** (`mv_derived_metrics`) are non-dimensional (`segment_axis IS NULL`
  inputs); their S1 question is "one filing for every input, averages included", a different
  problem. **Out of scope: a separate spec** (D4).
- **Keys.** The v2 suite generates no dimensional or breakdown shape (`SHAPE_CLASSES` in
  `src/eval/suites/v2_suite.py`; companyfacts carries no dimensions), so no v2 key disagrees.
  The v1 SQL-derived templates for segments, geography and breakdowns (`aq-3a`, `aq-3b`,
  `aq-4a` and the segment/geographic lookup templates in
  `configs/eval/sql_derived_templates.yaml`) read the views and mirror today's product; the
  set B / H-X key stores keep dimensional candidates on the FY filing
  ([set-b-restatement](set-b-restatement.md) requirement 1, locked C3). See Notes and D3.

## Requirement

Rows are `mv_financial_facts` or `mv_financial_facts_multidim` rows as Python mappings with the
keys restatement-s1 requirement 1 reads, plus `has_multiple_dimensions` (flat view) and
`dimensions` (multidim view: axis QName -> member QName). Local names are the text after the
last `:` or `}`.

1. **The identity of a dimensional fact** — `dimension_identity(row)` in
   `src/numerics/restatement.py`: the sorted tuple of `(axis, member)` local-name pairs of a
   non-empty `dimensions` mapping; else `()` when `segment_axis` is null (undimensioned); else
   `None` when `has_multiple_dimensions` is true (a flat-view row of a cell: its other dimensions
   are unknown); else the one pair of `segment_axis` / `segment_member`. Two facts are the same
   measure only with equal identities: a renamed member (NVIDIA `GraphicsMember` ->
   `GraphicsSegmentMember`) or another neutral axis (Intel `ConsolidatedEntitiesAxis` ->
   `ConsolidationItemsAxis`) is another fact (D2).
2. **The latest restatement, every dimension in the group** —
   `latest_dimensional_restatement(current, rows)`: restatement-s1 requirement 2 with the group
   (concept local name, `unit`, `dimension_identity`, `period_start`, `period_end`). A `current`
   row is returned unchanged when it lacks `concept`, `unit`, `period_start` or `period_end`, has
   neither `segment_axis` nor `dimensions`, has no `fiscal_year`, or its identity is `None`; a
   row of `rows` is a candidate only with a known identity and restatement-s1's candidate
   conditions (valued, `is_canonical_filing` true, `filed_date`, a greater `fiscal_year`). On
   rows without cells it equals `latest_restatement`. A member only a later filing reports (one
   the year's own column lacks) is never added (D1).
3. **S1 in the dimensional lookups.**
   a. *Two or more dimensions* (`_query_multidim`, no `as_of`): the query reads every stored
      multidim row of the filer for the family's concepts, period type and duration kind, from
      every filing of any fiscal period (no `fiscal_year`, `fiscal_period` or accession
      filter; `fy` is still passed as a parameter), with `fiscal_year`, `fiscal_period`,
      `filed_date`, `is_amendment`, `is_canonical_filing`, `document_period_end`, the real
      `period_start` and `dimensions`. The rows that may answer are
      `latest_dimensional_restatement(current_column(R, fy, fp), R)`, or with
      `as_originally_reported` `current_column(R, fy, fp, as_originally_reported=True)`; the
      existing hint matching, order, limit and result rules then run on them. A row without
      `document_period_end` (another spec's fake) is kept only when its `fiscal_year` and
      `fiscal_period` equal the request's, as today's SQL keeps it.
   b. The one-hint neutral-member retry reads through (a) and so follows it.
   c. *One dimension* (`_select_s1`): `latest_dimensional_restatement` replaces
      `latest_restatement`, so a later cell never restates a member's single-dimension fact.
   d. `as_of` lookups and the sum-of-cells path (`_sum_of_cells`, `_query_partition`) keep
      today's rows (the FY filing); the sum-of-cells proof needs one filing's partition and is a
      later spec.
4. **S1 in the member screens.** The segment, geographic (country) and region screens keep
   choosing each filer's row of fiscal year `fy` as today (views, `choose_member_row`, every
   exclusion reason), then replace it by `latest_dimensional_restatement([pick], R_filer)`, where
   `R_filer` is the filer's stored `mv_financial_facts` rows of the screened family and duration
   kind from every filing. The ranked row keeps `fiscal_year == fy`, its label and
   `member_match`, and carries the answering row's `value`, `unit`, `fact_id` and
   `citation.accession_no` (an unrestated row cites its own accession too); its `pct_of_total` is
   the answering value over **that filing's** undimensioned total (same concept local name,
   unit, `period_start`, `period_end`; one distinct value among the most precise, else None;
   None when zero). Ranking, `top_n` and `min_threshold` use the restated values. A note names
   the restated filers and accessions. The restatement query passes `fy`, the family, the
   duration kind and the tickers as parameters (no literal `unit = 'USD'`), and reads the
   pick's group keys by `fact_id` (the views carry no `period_start`/`period_end`/accession).
5. **A breakdown is one filing's split.** `breakdown_filings(rows, fy, axes, fiscal_period="FY",
   as_originally_reported=False)`: over one filer's rows of the measure (one family and
   duration kind, any filing), the period set P is the `(period_start, period_end)` of the
   undimensioned rows of `current_column(rows, fy, fp, as_originally_reported)`; empty P gives
   `[]`. The candidate filings are the own accession(s) of that column and, unless
   `as_originally_reported`, every accession with canonical, dated rows labelled after `fy`.
   A candidate qualifies when, at one period of P, it has a valued undimensioned row and a valued
   single-dimension row (identity of length 1) on an axis of `axes`. The result is the qualifying
   accessions, latest (`filed_date`, `accession_no`) first.
   `XbrlFactstoreBreakdown.breakdown` reads the filer's rows of the measure from
   `mv_financial_facts` (parameters `cik`, `concept_family`, `duration_kind`; no literal
   `segment_axis IS NULL` / `IS NOT NULL` conjunct, so totals and members come back together),
   and tries the filings of `breakdown_filings(..., axes, ..., step["as_originally_reported"])`
   in order (axes: segment `StatementBusinessSegmentsAxis`, product `ProductOrServiceAxis`,
   geography `StatementGeographicalAxis` / `GeographicalAreasAxis`). For each, the total is
   that filing's undimensioned row at a period of P (today's order within the filing), the
   members are its single-dimension rows on the axis at the total's period and unit, the arcs
   and line facts are its own, and `resolve` runs unchanged. The first OK result answers; every
   row cites that accession (`citation.accession_no`) and keeps `fiscal_year == fy`; a note
   names the filing when it is not the own one, and any later filing skipped for lack of a
   split. None OK: the latest filing's NOT_FOUND. Rows without `document_period_end` (another
   spec's fake) keep today's path.
6. Nothing else changes: the views' DDL, the flat lookup and screen (restatement-s1),
   `mv_yoy_comparison`, `choose_breakdown`, the keys, and every other spec's tests.

## Acceptance criteria

Fixtures are real excerpts committed under `test/spec/dimensional-s1/fixtures/` (Notes). Values
come from FSDS `num` of the named accession (segments as given), an independent source; filing
dates and period ends from each filing's DEI. Tolerances: amounts at the tagged precision of
millions (`abs=5e5`), shares of total as exact-integer ratios (`rel=1e-9`); accessions, fact ids
and dates exact.

- **C1**: the rule with every dimension. `dimension_identity` gives the sorted local-name pairs
  of a three-dimension cell, the one pair of a single fact, `()` for a total and `None` for a
  flat-view cell (URI and Clark names reduce alike). Over the realistic mix (flat rows of single
  facts, multidim rows of cells), `latest_dimensional_restatement(current_column(...))` answers
  Intel FY2022 CCG operating income $8,207M (-25-000009), "All other" -$5,977M (-24-000010, not
  the -25 cell), AXG -$1,716M (own), CCG notebook revenue $18,781M and CCG revenue $31,773M
  (-24-000010; the -25 CCG fact is on `ConsolidationItemsAxis`); NVIDIA FY2023 Graphics revenue
  $11,906M from -24-000029 (-25 renamed the member), Taiwan $6,986M and Other countries $3,623M
  from -25-000023; Singapore is not added. Properties: for any subset of later filings and any
  row order the answer is the hand rule (latest filing with the same identity, else own); on rows
  without cells it equals `latest_restatement`; a later recast wins, a later filing without the
  member or a non-canonical one changes nothing. Oracle: FSDS `num`, DEI; requirements 1-2.
- **C2**: the dimensional lookups. Intel FY2022 notebook revenue (three hints) answers $18,781M
  citing 0000050863-24-000010, as originally reported $18,783M (-23-000006); NVIDIA FY2023
  "Graphics" (one hint, neutral member) $11,906M citing 0001045810-24-000029, originally -23-000017;
  Intel "All other" operating income (one dimension) -$5,977M citing -24-000010. Properties: for
  any subset of the later 10-Ks the lookup cites the latest one with the same fact; a synthetic
  later recast moves the answer, a later filing without the fact does not, and
  `as_originally_reported` ignores both. Oracle: FSDS `num`; requirement 3.
- **C3**: the member screens. The segment screen ranks Intel FY2022 CCG operating income at
  $8,207M, fact and accession of -25-000009, `fiscal_year` 2022, share 8,207 / 2,334; the
  geographic screen ranks NVIDIA FY2023 Taiwan at $6,986M from -25-000023, share 6,986 / 26,974,
  and does not rank NVIDIA for Singapore (`member_not_found`). Properties: for any subset of
  Intel's later 10-Ks the screen ranks the latest one's fact with its own filing's share and
  agrees with the one-dimension lookup; a synthetic later 10-K reporting Taiwan at $7,000M
  ranks it with share 7,000 / 26,974, one reporting only the total does not move the rank.
  Oracle: FSDS `num`; requirement 4.
- **C4**: the breakdown. `breakdown_filings` lists NVIDIA FY2023 geography as -25, -24, -23 (only
  -23 as originally reported), Intel FY2022 segment operating income -25, -24, -23, Intel segment
  revenue none (cells only), and nothing for a year without an own column; a later filing
  missing the total or members is left out, in any row order. The NVIDIA FY2023 geography
  breakdown is -25-000023's five members (8,292, 6,986, 5,785, 3,623, 2,288), every row citing it;
  as originally reported the FY2023 10-K's four (8,292, 6,986, 5,911, 5,785). Intel's FY2022
  segment operating income is the FY2023 10-K's six members summing to 2,334 (the FY2024 10-K
  has no single-dimension split). Property: with any subset of NVIDIA's later 10-Ks and any row
  order, the rows come from the latest remaining filing and add up to 26,974. Oracle: FSDS `num`;
  requirement 5; `choose_breakdown` (breakdown-reconciling-items) for which members partition.

## Interface

New, stubbed in the spec PR (`src/numerics/restatement.py`; the implementer may re-export them
from `src/retriever/xbrl/restatement.py`):

- `dimension_identity(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...] | None`
- `latest_dimensional_restatement(current: Sequence[Mapping[str, Any]],
  rows: Sequence[Mapping[str, Any]]) -> list[dict]`
- `breakdown_filings(rows: Sequence[Mapping[str, Any]], fy: int, axes: Sequence[str],
  fiscal_period: str = "FY", as_originally_reported: bool = False) -> list[str]`

Behaviour changes, no signature change: `XbrlFactstoreLookup.lookup` (requirement 3),
`XbrlFactstoreScreen.screen` segment / geographic / region (4), `XbrlFactstoreBreakdown.breakdown`
(5; the step gains the optional `as_originally_reported`).

## Notes

- **Fixtures** (`test/spec/dimensional-s1/fixtures/`, built read-only from the local FSDS cache
  on 2026-11-10; the tests read nothing under the operator's data directory):
  - `fsds_segments.json`: FSDS `sub` and `num` rows (annual durations `qtrs` 4, USD, no
    co-registrant, segments kept) for `Revenues`,
    `RevenueFromContractWithCustomerExcludingAssessedTax` and `OperatingIncomeLoss` of Intel's
    10-Ks 0000050863-23-000006, -24-000010, -25-000009 at `ddate` 20221231 and NVIDIA's
    0001045810-23-000017, -24-000029, -25-000023 at 20230131; each quarter's archive URL
    (`https://www.sec.gov/files/dera/data/financial-statement-data-sets/<q>.zip`), SHA-256, size
    and retrieval date (the fsds-reader manifest of 2026-11-07).
  - `dei.json`: the six filings' DEI from their iXBRL instances, copied byte-for-byte in content
    from `test/spec/restatement-s1/fixtures/dei.json` (with its sources).
- **Store rows from the fixtures** (`conftest.py`): one row per `num` row; labels from DEI;
  period from DEI and companyfacts (Intel FY2022 2021-12-26..2022-12-31, NVIDIA FY2023
  2022-01-31..2023-01-29, as restatement-s1's fixtures give them; FSDS rounds `ddate` to the
  month end), stored end +1 day; axis QNames from FSDS's axis names
  (`BusinessSegments` -> `us-gaap:StatementBusinessSegmentsAxis`, `Geographical`,
  `ProductOrService`, `ConsolidationItems`, `ConsolidatedEntities` -> `srt:`); member QNames
  reconstructed (`country:XX` for two-letter geography, the standard consolidation members in
  `us-gaap:`, else `<ticker>:<Name>Member`); member labels are synthetic (CamelCase words +
  `[Member]`, countries by name) and are never oracle values; a cell is filed in the flat view
  under its first FSDS dimension. Synthetic later filings (`0000050863-26-900001`,
  `0001045810-26-900001`) are not real filings.
- **Fake-database contract** (`psycopg2.connect` is the I/O boundary; each test builds the store):
  - probes (`to_regclass`) answer "absent"; `entity_master` gives the roster (INTC, NVDA);
    `filing_registry` gives the filer's filings by (`filed_date`, `accession_no`);
    `concept_mapping` gives every stored concept QName of the family at rank 1;
  - `mv_financial_facts` returns **every** stored row (flat shape, no `dimensions`) matching the
    parameters `cik`, `ticker`/`tickers`, `ciks`, `concept_family`, `period_type`,
    `duration_kind`, `fiscal_period`, `accession_no`, `unit`, `period_start`, `period_end` when
    passed, and the member predicates as today's SQL writes them (`segment_axis IS [NOT] NULL`
    as a conjunct, `unit = 'USD'`, single-dimension, `axis_like`, `member_qname`,
    `member_like`, `member_key`, `segment_axes`); it ignores `fiscal_year`,
    `is_canonical_filing`, `is_current_period`, `ORDER BY` and `LIMIT`. Filing selection must
    therefore happen in Python;
  - `mv_financial_facts_multidim` returns every stored cell (segment columns null) matching the
    same parameters and `concept_qnames`; it ignores fiscal year, `pit_accession`, hints and
    `LIMIT`;
  - `mv_segment_matrix` / `mv_geographic_facts` are today's views: canonical current-period
    duration rows (geographic: `StatementGeographicalAxis`), honouring `fiscal_year <= / =
    %(fy)s`, the parameters and member predicates above and `geo_code`; each row carries every
    `mv_financial_facts` column (the real views do not: the screens must read `period_start`,
    `period_end` and `accession_no` by `fact_id`), the view's share of the same filing's total,
    and the breakdown's `member_label` / `member` aliases.
- **Other specs' fakes.** segment-lookup-screens serves multidim rows without
  `document_period_end`, filtered by `fiscal_year = %(fy)s` only when the SQL says so: hence the
  legacy rule in 3a. breakdown-op answers any `mv_financial_facts` query with one total row
  without S1 columns: hence the legacy path in 5. The flat path's legacy rule
  (restatement-s1) is unchanged.
- **Reference check.** A throwaway reference implementation of requirements 1-5 (pure functions
  plus wiring patched over today's methods; not committed) passed all 28 tests here. Today's
  code fails every test with an assertion (C2-C4 wiring) or the stub's `NotImplementedError`.
- **Rollout.** No view is rebuilt for the product: the lookup and screens read the S1 columns
  from `mv_financial_facts` / `mv_dei_metadata` / `filing_registry` as the flat path does
  (`_S1_COLUMNS`). Adding `is_canonical_filing`, `filed_date`, `is_amendment` and
  `document_period_end` to `mv_financial_facts_multidim` (rebuild `11_...sql` only; nothing
  depends on it) is the implementer's option for speed. The v1 SQL-derived suite's segment,
  geographic and breakdown templates read `mv_segment_matrix` / `mv_geographic_facts` at the FY
  filing; after this change they disagree with the product on restated members and recast
  splits. The implementer brings them to requirements 2, 4 and 5 (SQL over `mv_financial_facts`
  with the same rules), then the suite is rebuilt and diffed (root `CLAUDE.md`); no paid run.
  Live check after merge (not a test): Intel FY2022 notebook revenue, NVIDIA FY2023 geography
  breakdown and Taiwan screen, Intel "All other" operating income answer as in C2-C4.
- **Implementation in parts** (one lock, `impl/dimensional-s1--part1..`): part 1 the pure
  functions and the lookup (C1, C2); part 2 the member screens (C3); part 3 the breakdown and the
  v1 templates (C4). Each part is small enough to review alone.

## Decisions for the maintainer

- **D1. Members only a later filing reports.** Recommended (and tested): mirror restatement-s1 —
  a member the year's own column lacks is not answered (NVIDIA FY2023 Singapore:
  `member_not_found` in the screen, `NOT_FOUND` in the lookup), while a breakdown takes the
  latest filing's whole split and does show it. The alternative (period fixed by the own DEI end,
  value from any later filing) is closer to key rule 7 of restatement-s1 but should change the
  flat lookup at the same time; a follow-up spec if wanted.
- **D2. Identity is exact.** Recommended (and tested): equal local names of every axis and
  member. Renames (NVIDIA `GraphicsSegmentMember`) and a changed neutral axis (Intel
  `ConsolidationItemsAxis` for `ConsolidatedEntitiesAxis`) do not restate; equivalence by label
  or a reviewed mapping could, at the risk of merging measures a reorganisation changed (Intel
  DCAI revenue FY2022: $19,445M on the old axes, $16,856M on the new).
- **D3. Keys.** Set B / H-X key stores keep dimensional candidates on the FY filing
  (set-b-restatement, locked C3); bringing them to this rule changes that retired test and
  belongs in a set-B spec. Hand keys for dimensional cases (v2 plan 3.3) should be read from the
  latest filing that reports the member, so they agree with this spec.
- **D4. Derived metrics** stay on the FY filing here; a separate spec for one-filing inputs to
  `mv_derived_metrics` (margins, averages, growth) is recommended.
- **Consistency to know.** A lone member and a breakdown can cite different filings: Intel
  FY2022 CCG operating income is $8,207M (FY2024 10-K) as a lookup, $5,569M inside the FY2023
  10-K's breakdown, because the FY2024 10-K's split is not single-dimension. Both follow S1
  (latest report of the fact; one filing for a comparison).
