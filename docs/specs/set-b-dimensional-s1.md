# set-b-dimensional-s1 — the key-check stores read dimensional facts by S1

[dimensional-s1](dimensional-s1.md) brought the product's dimensional answers to S1 (the latest
periodic report that states a fact, [Evaluation semantics](../design/XbrlRag_Eval_Semantics.md)):
a lone member (one-dimension lookup, multidimensional lookup, member screens) is answered by
`latest_dimensional_restatement` (its R2, with `dimension_identity`, R1), and a breakdown is one
filing's whole split, the first of `breakdown_filings` (its R5) that partitions. The v1 suite's
dimensional and breakdown templates now cite the same rows (`src/eval/suites/sql_derived.py`
`s1_dimensional_pick` and `_breakdown_rows`). Its D3 left the key-check stores for a set-B spec;
this is that spec.

What the code does today (paths traced for this spec):

- **Set B's store** (`src/eval/set_b/pg_store.py`). `PostgresKeyFactStore.facts_for` →
  `_read_candidates` → `_FILER_ROWS_SQL` → `key_candidates`. Flat rows follow S1
  (`latest_restatement(current_column(...))`, set-b-restatement). Dimensional rows keep the old
  rule twice over: the SQL pre-filter keeps a dimensional row only when `fiscal_year = fy AND
  is_current_period` (`_FILER_ROWS_SQL`, the `segment_axis IS NULL OR ...` conjunct), and
  `_dimensional_candidate` keeps only the canonical FY filing's current rows. The SQL selects
  `md.dimension_labels` but not `md.dimensions`, so a multi-axis fact's full dimension set (R1) is
  never read.
- **H-X's store** (`src/eval/set_b/hx_pg_store.py`). `PostgresHxKeyFactStore.facts_for` calls the
  same `_read_candidates`; `src/eval/set_b/hx.py` `_Fetcher.get` reads it for every item without an
  accession (cross-filer rows, cross-period rows, compound parts), with a member hint whenever the
  case names one (`_resolve_item`). `facts_in_filing` (an explicit accession) is not affected.
- **Set B's resolution** (`src/eval/set_b/core.py` `resolve_case`) reads one candidate list per
  filer-year for every kind: a `breakdown` row is matched against the same candidates as a lone
  `dimensional` case. The build (`scripts/eval/set_b.py` `LabelRecordingStore`) records breakdown
  labels from `facts_for`.

Consequences, measured read-only on the local `xbrl_db` against the rebuilt v1 suite (513 cases,
uncommitted, worktree `data-v1rb`):

- Today's store resolves 55 of 106 dimensional citations and 130 of 286 breakdown row citations;
  `test/unit/test_set_b_tooling.py::test_store_resolves_main_suite_facts_and_labels` fails with
  KeyError 14688 (AAPL FY2024 iPhone, cited from the FY2025 10-K 0000320193-25-000079).
- A store following R2 alone resolves 105 of 106 dimensional citations (the miss is AMZN fact
  7093, a quarterly cell: Notes, D5) but only 271 of 286 breakdown rows: 11 rows cite members that
  exist only in a later filing's recast split (NVDA FY2024 1208, 1211; MSFT FY2024 105771, 105774,
  105780, 105783, 105786, FY2025 105773, 105782; TSM FY2024 93209 in two cases) and 4 are GOOGL
  reconciling lines (D4).
- Adding R5's split (this spec, requirement 2) resolves 282 of 286 breakdown rows, every one with
  its expected entity label; the 4 misses are the GOOGL lines.
- **H-X inherits the read.** An H-X member key written from the restated value (what the product
  answers since dimensional-s1) is `not_found` today, and one written from the original filing
  verifies although the product answers the restated value.

## Requirement

Store rows are `mv_financial_facts` rows as Python mappings with the keys set-b-restatement
requirement 1 lists, plus `dimensions` (the `mv_financial_facts_multidim` dimension set, axis
QName → member QName, for a multi-axis fact; None or absent otherwise). Candidate dicts keep
set-b-tooling requirement 5's shape (`candidate_from_row`); nothing about labels, units or dates
changes.

1. **A lone member follows dimensional-s1 R2** — `key_candidates(rows, fiscal_year)`. Flat rows
   are unchanged (set-b-restatement requirement 1). The dimensional candidates are the rows of
   `latest_dimensional_restatement(current_column(D, fiscal_year, "FY"), D)`, where `D` is every
   row given with a non-null `segment_axis`, kept when `duration_kind` is `annual` or
   `period_type` is `instant`. So a member of the year's own column (the canonical filing labelled
   `fiscal_year`, rows ending on its DEI period end, its cover share counts included) is answered
   by the latest-filed canonical filing with a greater label that reports the same concept (local
   name), unit, period and full dimension set (`dimension_identity`), else by the own row. A
   member only a later filing reports is never added (dimensional-s1 D1). A flat-view row of a
   multi-axis fact without `dimensions` has no identity and stays as filed (R2's rule).
2. **A breakdown member follows dimensional-s1 R5** — new `breakdown_candidates(rows,
   fiscal_year)` in `src/eval/set_b/pg_store.py`. Over the rows with a non-null `concept_family`,
   `period_type` `duration`, `duration_kind` `annual` and a value, grouped by `concept_family`,
   and for each axis kind — segment (`StatementBusinessSegmentsAxis`), product
   (`ProductOrServiceAxis`), geography (`StatementGeographicalAxis`, `GeographicalAreasAxis`;
   the product's `BREAKDOWN_AXES`): when `breakdown_filings(group, fiscal_year, axes)` is
   non-empty, its **first** (latest-filed) accession contributes every row it holds whose
   `dimension_identity` is a single pair on those axes, at a `(period_start, period_end)` of an
   undimensioned row of `current_column(group, fiscal_year, "FY")`. The result is those rows as
   candidate dicts, each once, sorted by `fact_id`. No flat row and no cell is ever a breakdown
   candidate. (Which filing: D3.)
3. **Resolution reads the candidates of its kind.**
   - `KeyFactStore.breakdown_facts_for(ticker, fiscal_year)` (new, `src/eval/set_b/base.py`): not
     abstract; its default returns `self.facts_for(ticker, fiscal_year)`, so a store that knows no
     breakdown rule (every existing fake) behaves as today.
   - `PostgresKeyFactStore.breakdown_facts_for` returns `breakdown_candidates` over the same read
     as `facts_for` (copies; one read per filer-year may serve both). `PostgresHxKeyFactStore`
     inherits it and, like its `facts_for`, never logs the filer.
   - `resolve_case` (`src/eval/set_b/core.py`): the rows of a `breakdown` case resolve against
     `breakdown_facts_for` (read at most once per filer-year); `lookup`, `dimensional` and
     `compute` (with or without a member) against `facts_for`, as today. Statuses, classes, hints
     and the report do not change otherwise.
   - H-X (`src/eval/set_b/hx.py`) has no single-filer breakdown kind: every H-X item keeps reading
     `facts_for` (lone members, R1). No change to `hx.py`.
   - `scripts/eval/set_b.py` `LabelRecordingStore` also wraps `breakdown_facts_for`: it returns
     the inner store's `breakdown_facts_for` (never its `facts_for`) and records each member
     label, so `build_suite` labels a breakdown row with its breakdown member.
4. **The stores read every filing.** `_read_candidates` (and whatever serves
   `breakdown_facts_for`) reads the filer's rows through a dict cursor with named parameters
   including `ticker`, selects `md.dimensions` (as the column `dimensions`) beside
   `md.dimension_labels`, and drops the dimensional pre-filter (`segment_axis IS NULL OR
   (fiscal_year = fy AND is_current_period)`). A pre-filter may stay only where it never changes
   the result, e.g. `m.fiscal_year >= %(fiscal_year)s` (every rule above reads only the year's own
   filing and later-labelled ones). Caching, the read-only session and the timeouts stay.
5. Nothing else changes: `facts_in_filing`, `candidate_from_row`, `member_label`,
   `find_classes`, `build_suite`, H-X validation and resolution, the SEC check, the long-context
   oracle arm, the product, the suite builder and every other spec's tests (beyond the retired
   set-b-restatement C3, rewritten here to hold before and after; Notes).

## Acceptance criteria

Fixtures are SEC FSDS `num` rows (an independent source) committed under
`test/spec/set-b-dimensional-s1/fixtures/` (Notes). Expected rows are named by hand as (filing,
tag, FSDS `segments`, year); expected values are the FSDS values; the S1 rules are applied by hand
to the listed filings (filing dates and labels from DEI). Tolerances: amounts at the tagged
precision of millions (`abs=5e5` per value, summed over the members of a hand-added total);
accessions, fact ids and labels exact.

- **C1**: a lone member follows R2. NVDA FY2025: every own-column member the FY2026 10-K
  (0001045810-26-000021) also reports answers from it — seven products, United States $77,482M,
  Taiwan $23,600M, China $25,048M, Other countries $4,367M and both segment cells — while
  Singapore $23,684M and the corporate line stay on the FY2025 10-K (0001045810-25-000023): 15
  candidates. NVDA FY2024: Data Center from the FY2026 10-K; Compute and Networking (only later
  filings split them out) and Singapore are not candidates; Other countries is $2,147M (not
  $10,245M as filed); the renamed segment cells stay on the FY2024 10-K: 12 candidates. Intel
  FY2022 notebook revenue (a three-dimension cell) is $18,781M (FY2023 10-K), CCG operating
  income $8,207M (FY2024 10-K), "All other" -$5,977M (FY2023 10-K, never the FY2024 cell
  $1,174M); NVDA FY2023 Graphics cell $11,906M (FY2024 10-K). Properties: for any subset of
  later filings and any row order the dimensional candidates are the hand rule's and the flat
  candidates equal those of the flat rows alone; earlier-labelled and non-canonical rows change
  nothing. Oracle: FSDS `num`, DEI; requirement 1.
- **C2**: a breakdown is the latest splitting filing's members. NVDA FY2024: the FY2026 10-K's
  seven products (Compute $38,950M + Networking $8,575M + Gaming + Professional Visualization +
  Automotive + OEM = $60,922M, the total) and four countries; FY2025: the FY2026 10-K's seven
  products and four countries ($130,497M), Singapore not among them; FY2023: the FY2025 10-K's
  seven products and five countries (Singapore $2,288M); Intel FY2022: the FY2024 10-K's three
  single-dimension segments (operating income $8,207M, $4,198M, $1,532M; D3) and five countries;
  nothing for a year without an own column. Properties: for any subset of later filings and any
  row order the candidates are the hand rule's (dropping the latest splitting filing moves the
  breakdown to the next); non-canonical rows change nothing. Oracle: FSDS `num`, DEI;
  requirement 2.
- **C3**: a key verifies against the candidates of its kind. A set B breakdown key written from
  the FY2026 10-K's FY2024 split verifies with that filing's facts as classes; one written from the
  FY2025 10-K's own FY2025 countries is `not_found` on every row, the FY2026 10-K's four verify;
  a `dimensional` Singapore FY2025 $23,684M verifies (FY2025 10-K), Taiwan $23,600M verifies and
  $20,573M does not, FY2024 Compute $38,950M does not (a breakdown row, not a lone member); a
  breakdown with Singapore $23,684M is `not_found` on that row. An H-X cross-period trend of
  NVDA Taiwan revenue FY2023-FY2025 (6,986; 14,912; 23,600) verifies with the FY2025 and FY2026
  10-Ks' facts; as first filed (6,986; 13,405; 20,573) it is `not_found`. Properties: with any
  subset of NVDA's later 10-Ks, a key written from the latest remaining filing's FY2024 product
  split verifies to exactly its facts; row order never changes a resolution. Oracle: FSDS `num`;
  requirements 1-3.
- **C4**: the wiring. Over a fake database (Notes), `PostgresKeyFactStore` and
  `PostgresHxKeyFactStore` answer Intel FY2022 notebook revenue at $18,781M (only possible when
  the read selects `dimensions`), give the FY2026 10-K's FY2024 split from
  `breakdown_facts_for`, pass `ticker` as a named parameter and return no other filer's row; a
  `KeyFactStore` with only `facts_for` answers `breakdown_facts_for` with it and resolves a
  breakdown as before; the build's `LabelRecordingStore` returns and labels the breakdown
  members ("Compute", "Networking") and `build_suite` writes them as the rows' entities.
  Property: permuting the rows and adding another filer's copies never changes either read.
  Oracle: FSDS `num`, requirements 3-4.

## Interface

New, stubbed in the spec PR:

- `src/eval/set_b/pg_store.py`: `breakdown_candidates(rows: Sequence[dict[str, Any]],
  fiscal_year: int) -> list[dict]`
- `src/eval/set_b/base.py`: `KeyFactStore.breakdown_facts_for(self, ticker: str,
  fiscal_year: int) -> list[dict]` (concrete; default `facts_for`)
- `src/eval/set_b/pg_store.py`: `PostgresKeyFactStore.breakdown_facts_for(self, ticker: str,
  fiscal_year: int) -> list[dict]`

Behaviour changes, no signature change: `key_candidates` (1), `PostgresKeyFactStore.facts_for` /
`_read_candidates` and `PostgresHxKeyFactStore.facts_for` (1, 4), `resolve_case` (3),
`scripts/eval/set_b.py` `LabelRecordingStore` (3, a new method).

## Notes

- **Fixtures** (`test/spec/set-b-dimensional-s1/fixtures/`, SHA-256 checked by `conftest.py`):
  - `fsds_segments.json` `5652a018…19dd`: a byte copy of
    `test/spec/dimensional-s1/fixtures/fsds_segments.json` (Intel's FY2022-FY2024 and NVIDIA's
    FY2023-FY2025 10-Ks at the FY2022 / FY2023 period; its provenance is recorded inside it and
    in dimensional-s1's Notes).
  - `fsds_nvda_recast.json` `9a24b404…88ca5`: built for this spec on 2026-11-12, read-only, from
    the local FSDS cache (`data/cache/sec_fsds/filtered/`, the fsds-reader manifest of
    2026-11-07): `sub` rows and `num` rows (`Revenues`, `qtrs` 4, USD, no co-registrant, `ddate`
    20240131 and 20250131, segments kept; field values byte-for-byte) of NVIDIA's 10-Ks
    0001045810-24-000029 (2024q1), -25-000023 (2025q1) and -26-000021 (2026q1), with each
    quarter archive's URL
    (`https://www.sec.gov/files/dera/data/financial-statement-data-sets/<q>.zip`), SHA-256, size
    and retrieval date.
  - `dei.json` `2e261b57…436a1f`: a byte copy of `test/spec/set-b-restatement/fixtures/dei.json`
    (each corpus filing's DEI from its iXBRL instance).
- **Store rows from the fixtures** (`conftest.py`), as dimensional-s1's Notes describe: one row
  per `num` row; labels, filing dates and DEI period ends from `dei.json`; the period end is the
  own filing's `DocumentPeriodEndDate` (FSDS rounds `ddate` to the month end), the start of a
  NVIDIA year the day after the prior own filing's (Intel FY2022 and NVIDIA FY2023 starts are
  companyfacts', as dimensional-s1 uses them), stored end +1 day; axis QNames from FSDS's axis
  names, member QNames reconstructed, member labels synthetic (words + `[Member]`, countries by
  name) and never oracle values; a cell is filed under its first FSDS dimension with
  `has_multiple_dimensions` and its full `dimensions` / `dimension_labels`.
- **Fake database** (C3, C4; the connection passed to the store is the I/O boundary):
  `cursor(cursor_factory=<any non-None>)` yields dict rows; `execute(sql, params)` with a mapping
  holding `ticker` returns every stored row of that filer and ignores every other predicate; the
  `dimensions` key comes back only when the SQL names the column (`\bdimensions\b`); a query
  without a dict cursor or a `ticker` parameter returns nothing. Selection must therefore happen
  in `key_candidates` / `breakdown_candidates`. The fake cannot see a SQL pre-filter: the live
  check below is what catches one that changes the result.
- **Retired spec tests.** The whole spec suite was run against a throwaway reference
  implementation of requirements 1-4 (not committed). One retired test pinned the old rule:
  `test/spec/set-b-restatement/test_c3_dimensional.py` (C3, "a later filing's dimensional row does
  not replace the FY row", and its property "later filings never change the dimensional
  candidates"). It is rewritten in this PR to assert only what holds before and after this change
  (one candidate per member, cleaned labels, never a prior-period or non-canonical row, a later
  row only for a member the FY filing reports), with a comment naming this spec. set-b-restatement
  C4 (AMD flat receivables through both stores), set-b-tooling (fake stores with only
  `facts_for`; its breakdown tests assert one `facts_for` call, which the seam's default keeps)
  and hx-authoring (fake stores; `hx.py` unchanged) pass unchanged; set-b-restatement's spec text
  for requirement 1's dimensional bullet and C3 is superseded by requirement 1 here.
- **Wiring the implementer owns (not spec-tested; `test/unit`):**
  - `test/unit/test_set_b_tooling.py::test_store_resolves_main_suite_facts_and_labels` checks one
    lookup and one breakdown case, which is how this gap hid. Replace or extend it with a live-DB
    check (skips when `xbrl_db` is unreachable) over **every** `dimensional` and `breakdown` case
    of `test/eval/suites/sql_derived_v1.json`: each cited class has a member in `facts_for` (a
    dimensional case) or `breakdown_facts_for` (a breakdown row), and each breakdown row's
    candidate label equals its expected entity. Two named exclusions, asserted as such rather than
    skipped silently: rows whose fact is undimensioned (reconciling lines, D4: GOOGL FY2024/FY2025
    aq-3b and aq-4a, facts 34690/34691 and their duplicates) and quarterly multidim citations
    (D5: AMZN fact 7093, aq-8b). Measured with the reference implementation against the rebuilt
    suite: 105/106 dimensional and 282/286 breakdown rows resolve, labels equal, exactly those
    5 excluded.
  - `test/unit/test_hx_tooling.py::test_facts_for_runs_set_b_query_without_logging_the_filer`
    pins `params == {"ticker", "fiscal_year"}` and one query for two reads: adjust only if the
    implementation changes the parameters or the caching.
  - `test/unit/test_set_b_tooling.py::test_label_recording_store`: add the breakdown read.
  - Module docstrings of `pg_store.py` / `hx_pg_store.py` and the set-B guide
    (`docs/guides/xbrl-flow/12-heldout-set-b.md`) describe the dimensional rule; update them.
- **Consumers traced** (`grep` for `facts_for`, `_read_candidates`, `key_candidates`,
  `KeyFactStore`): `src/eval/set_b/core.py` (`_FactCache`, `resolve_case`), `src/eval/set_b/hx.py`
  (`_Fetcher`), `src/eval/set_b/hx_pg_store.py` (inherits the read), `scripts/eval/set_b.py`
  (`LabelRecordingStore`, `_postgres_store`), `scripts/eval/hx.py` (`_postgres_store`), the unit
  tests above, and the spec tests of set-b-tooling, hx-authoring and set-b-restatement. Not
  consumers, checked: the SEC check (`src/eval/sec_check/pg_store.py` reads cited facts by fact
  id from both views, and marks dimensional facts for hand check: no S1 read), the long-context
  oracle arm (`cited_filing_key` over `_FACT_FILINGS_SQL`, which reads both views by fact id, so a
  multi-axis or recast citation resolves to its own filing), `src/eval/suites/sql_derived.py`
  (already on dimensional-s1).
- **Live check after merge (not a test):** rebuild nothing; run the new unit check against the
  rebuilt v1 suite, and `scripts/eval/hx.py check-keys` on the H-X draft (the maintainer runs it:
  agents never read H-X).

## Decisions for the maintainer

- **D1. Both rules, scoped by kind — not a union.** Recommended (and tested). Both R2 and R5 are
  needed: a lone member and a breakdown can cite different filings (Intel FY2022 CCG operating
  income: $8,207M as a lone member, $5,569M inside the product's breakdown; NVDA FY2024 Compute
  is a breakdown row but no lone member). A single list holding both would let a key verify
  against the rule the product does not use for that kind. Measured on the rebuilt suite's 63
  breakdown cases: of the own filings' revenue member rows outside each case's split, 661 would
  verify as breakdown rows against a union and 439 against the scoped read (222 more, mostly
  orphans of a recast, which R2 keeps); and against a union every recast-only member would verify
  as a lone member though the product refuses it (dimensional-s1 D1). Scoping costs one seam
  method with a default, so no existing fake changes.
- **D2. The double-match risk.** With D1 the remaining overlap is deliberate: an orphan member (an
  own-column member a later recast drops, e.g. NVDA FY2025 Singapore) verifies as a lone member,
  because the product's lookup and screens answer it from the own filing (dimensional-s1 D1), and
  does not verify as a breakdown row, because the product's breakdown is the recast. So within
  each kind a key verifies when the product would answer that value, with the two exceptions
  below and in D3. Recommendation: allow (no flag); the alternative, forbidding orphans, would
  fail keys the product answers. One residual inside a breakdown: the store cannot see the
  filing's arcs, so a parent member of the chosen filing (NVDA FY2024 Data Center $47,525M, the
  parent of Compute and Networking) is a candidate although the product's finest partition
  lists its children; a breakdown key written at the parent level still verifies. Acceptable for
  set B (the rows exist in the answering filing); name it in the guide.
- **D3. Which filing for a breakdown: the latest that splits, not the first that partitions.**
  Recommended (and tested, Intel point in C2). The product takes the first of `breakdown_filings`
  whose members partition the total with the filing's arcs and at most one reconciling line
  (`choose_breakdown`); reading arcs and line facts would copy the product's breakdown into the
  key check (and `choose_breakdown` lives in `src.retriever`, which the set-B CLI must not import).
  The latest splitting filing holds every member row the rebuilt suite cites in its 63 breakdown
  cases (the suite's builder applies the product's rule). Where it differs (Intel FY2022 segment
  operating income: the FY2024 10-K reports three single-dimension segments that do not
  partition, so the product falls back to the FY2023 10-K's six), a correct key is `not_found`,
  which the author sees; only a key copied from the latest filing's partial split would pass
  wrongly. Alternatives measured on the same suite: every filing of `breakdown_filings` (also
  holds all 282 cited rows, but lets a key written from a superseded split verify: silent, and
  the likeliest author error, reading the year's own 10-K); the first filing whose members alone
  partition the total (also holds all 282; picks an older filing than the latest for AVGO FY2024
  segment operating income, not a v1 case and not checked against the product, which may use a
  reconciling line there). H-X has no breakdown kind, so this choice reaches set B only.
- **D4. GOOGL reconciling lines: out of scope.** Cause: the v1 breakdown templates add a
  reconciling line (breakdown-reconciling-items), and GOOGL's is an undimensioned fact of another
  concept, `RevenueNotFromContractWithCustomer` (hedging gains), with no concept family. Fact
  34690 (FY2024, cited from the FY2025 10-K 0001652044-26-000018) is not a candidate because the
  FY2024 10-K never tagged that concept, so S1 adds nothing (restatement-s1 D1); fact 34691
  (FY2025) is a flat candidate with `member_label` None, while the suite's entity is the concept
  label. Set B cannot key a line at all (a breakdown row must name a member and matches
  dimensional candidates only), and H-X has no breakdown kind, so no key can hit it.
  Recommendation: out of scope here; the unit check excludes such rows by name. If set B ever
  needs line rows, that is a set-b-tooling change (a line kind with a concept label).
- **D5. AMZN quarterly multidim (fact 7093, aq-8b): out of scope.** The fact is a Q4 duration
  (2024-10-01..2024-12-31) of a two-dimension cell, and the store keeps annual durations and
  instants only (set-b-tooling requirement 5). Fiscal quarters wait for G1 (decision K5: annual
  only); the suite template's lack of a duration filter is the template's to fix. The unit check
  excludes it by name.
- **D6. Logging.** `PostgresHxKeyFactStore.breakdown_facts_for` (inherited) must not name the
  filer, as its `facts_for` does not; H-X never calls it, so this is not spec-tested.
