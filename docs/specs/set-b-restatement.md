# set-b-restatement — the key-check stores and the oracle document follow S1

[restatement-s1](restatement-s1.md) made the product's flat lookup and flat screen
answer a period as **the latest periodic report that states it** (S1,
[Evaluation semantics](../design/XbrlRag_Eval_Semantics.md)), and the v1 suite's lookup
templates `aq-1a` / `aq-1c` cite the same rows. Rebuilding the v1 suite moved 104 citations to
later filings (no value changed). The tools that check or serve those keys still read only the
filing **labelled** with the fiscal year, so they now disagree with the suite:

- **Set B's store** (`src/eval/set_b/pg_store.py`, `PostgresKeyFactStore.facts_for`) returns
  the current-period facts of the canonical FY filing labelled `fy` (`fiscal_year = fy AND
  is_current_period`). A restated period's S1 row sits in a later-labelled filing and is never
  a candidate, so a key written from the latest report is `not_found` and a key written from the
  original resolves to a fact the product no longer cites. Proof:
  `test/unit/test_set_b_tooling.py::test_store_resolves_main_suite_facts_and_labels` fails
  against the rebuilt suite (the first lookup case's cited class is not among the candidates).
- **H-X's store** (`src/eval/set_b/hx_pg_store.py`, `PostgresHxKeyFactStore.facts_for`) runs the
  same query (it copies set B's SQL to log differently), with the same defect. Its
  `facts_in_filing` (an explicit accession, used for H-X's `as_originally_reported` / `latest`
  pairs) is correct and does not change.
- **The long-context oracle arm** (`src/eval/systems/long_context.py`,
  `PostgresFilingResolver.canonical_key`) hands the model the canonical annual filing labelled
  `fy`. For a restated period that document states the original value (NVDA FY2023 diluted EPS
  1.74 in 0001045810-23-000017), while the key, under S1, is the later filing's (0.17 in
  0001045810-25-000023): the "oracle" document cannot contain the keyed value.
- **The SEC check** (`src/eval/sec_check/`) was examined and needs no change here: its store
  reads cited facts **by fact id** (any filing), and `check_fact` matches a fact against
  companyfacts at the fact's own accession, so a citation that moved to the restating filing is
  still checked at that filing. Its only fiscal-year read, `metric_components`, reads
  `mv_derived_metrics`, the same view the suite's derived templates read (S1 does not reach the
  derived metrics yet; restatement-s1 "Gap today"). See Notes for the S1 check it lacks.

## Requirement

Store rows are rows of `mv_financial_facts` as Python mappings, with the keys restatement-s1
requirement 1 reads (`fact_id`, `accession_no`, `fiscal_year`, `fiscal_period`, `filed_date`,
`is_amendment`, `is_canonical_filing`, `document_period_end`, `concept`, `unit`, `segment_axis`,
`segment_member`, `period_start`, `period_end` stored next-midnight, `value`) plus
`concept_family`, `period_type`, `duration_kind`, `is_current_period`, `segment_label`,
`has_multiple_dimensions` and `dimension_labels` (the `mv_financial_facts_multidim` labels, axis
→ label, or None). Dates may be `datetime.date` or ISO strings.

1. **The filer-year candidates** — `key_candidates(rows, fiscal_year)` in
   `src/eval/set_b/pg_store.py`, over every stored row of one filer (every filing, every fiscal
   period, flat and dimensional), returns set B's candidate dicts (set-b-tooling requirement 5:
   `fact_id`, `concept`, `concept_family`, `unit`, `value` as float, `period_start`, `period_end`
   as ISO strings with the stored end moved back one day, `dimensional`, `member_label`), sorted
   by `fact_id`:
   - **Flat rows** (`segment_axis` null) follow S1 exactly as the product's lookup does
     (restatement-s1 requirements 1–3): the rows of
     `latest_restatement(current_column(F, fiscal_year, "FY"), F)`, where `F` is every flat row
     given, kept when `duration_kind` is `annual` or `period_type` is `instant`. So a period is
     answered by the latest-filed canonical filing with a greater fiscal-year label that reports
     it (a later 10-K, or a later year's 10-Q comparative of a year-end balance), else by the
     year's own filing; the own column ends exactly on the filing's DEI
     `DocumentPeriodEndDate` (no earlier column ever stands in, restatement-s1 G1), and the
     cover-page share count of the own filing is kept (shares-outstanding-reading). Rows of a
     non-canonical filing (a superseded 10-K, a Part III-only 10-K/A) never answer.
   - **Dimensional rows** (`segment_axis` not null) keep today's rule unchanged: `fiscal_year ==
     fiscal_year`, `fiscal_period == "FY"`, `is_canonical_filing` and `is_current_period` true,
     non-null `value` and `period_end`, annual duration or instant. (S1 for dimensional facts
     waits for the product's multidimensional / segment spec; Decisions.)
   - `member_label` is `pg_store.member_label(segment_label, segment_member, dimension_labels
     if has_multiple_dimensions else None)` for a dimensional row and None for a flat one;
     `dimensional` is `segment_axis is not None`.
   - The pure S1 functions have **one definition**: `key_candidates` uses the same
     `current_column` / `latest_restatement` the product uses, never a copy. Set B's CLI must
     not import `src.retriever` (the quarantine test
     `test_cli_imports_no_pipeline_llm_or_prompts`), so the implementer moves the pure
     functions of `src/retriever/xbrl/restatement.py` to a module outside `src.retriever`,
     `src.pipelines`, `src.llm` and `prompts` (it imports only the standard library), and
     `src.retriever.xbrl.restatement` re-exports them unchanged.
2. **The stores use it.** `PostgresKeyFactStore.facts_for(ticker, fiscal_year)` and
   `PostgresHxKeyFactStore.facts_for` return `key_candidates(<every mv_financial_facts row of
   the filer>, fiscal_year)`, with `dimension_labels` joined from `mv_financial_facts_multidim`
   for a multi-axis fact. One definition of the read (H-X logs counts only, never the filer).
   They read the rows through a dict cursor (`cursor(cursor_factory=...)`, e.g.
   `psycopg2.extras.RealDictCursor`) whose columns are named as the row keys above, with
   **named** parameters including `ticker`; SQL may pre-filter rows (for speed) but never in a
   way that changes the result. Caching (one read per filer-year), the read-only session and the
   timeouts stay. `facts_in_filing` does not change.
3. **The oracle document is the cited filing.** A new I/O seam `CitedFilingResolver` (in
   `src/eval/systems/base.py`) maps fact ids to their filings: `fact_filings(fact_ids)` returns,
   per fact id found, `{"accession_no", "form_type", "canonical", "key"}` (`canonical`: the
   filing is not `superseded_by` another; `key`: its `canonical.htm` store key, or None). The
   pure rule `cited_filing_key(fact_ids, filings, forms=ANNUAL_FORMS)` in
   `src/eval/systems/long_context.py` returns the `key` of the single filing that holds every
   cited fact, when: `fact_ids` is non-empty, every id is in `filings`, they all share one
   `accession_no`, its `form_type` is in `forms`, it is `canonical`, and its `key` is not None.
   Otherwise None. Duplicates and order of `fact_ids` do not matter.
   `LongContextSystem` (the oracle arm): when its resolver is also a `CitedFilingResolver` and
   the case has `expected_fact_ids`, the document is `cited_filing_key(case.expected_fact_ids,
   resolver.fact_filings(case.expected_fact_ids))`; when that is None (or the resolver is not a
   `CitedFilingResolver`, or the case cites no facts) it is `canonical_key(ticker, fy)` as
   today. Exclusions, the size limit, prompt caching and serialization per filing are
   unchanged. `PostgresFilingResolver` implements both seams (fact id →
   `mv_financial_facts` / `mv_financial_facts_multidim` accession → `filing_registry` form and
   `superseded_by` → `accession.entry_url` → `canonical_key_from_entry_url`).
4. Nothing else changes: set B's and H-X's authoring, validation, `find_classes`,
   `resolve_case`, `build_suite`, `facts_in_filing`, the SEC check, and the question-chosen
   long-context arms (`long_context_arms.py`, which pick a filing from the question).

## Acceptance criteria

Fixtures are the restatement-s1 fixtures, copied byte for byte (Notes): companyfacts entries of
corpus filings, each filing's DEI from its iXBRL instance, and FSDS `num` rows. Store rows are
built from them as restatement-s1's Notes describe. Oracle accessions are the S1 rule applied
**by hand** to the companyfacts entries listed in each test (which filings report the period,
their filing dates and fiscal-year labels); oracle values are those filings' companyfacts
values, cross-checked against FSDS `num` where FSDS has the filing. Tolerances: per-share values
to half a cent (`abs=0.005`), amounts at the tagged precision of millions (`abs=5e5`), dates,
accessions, fact ids and keys exact.

- **C1**: flat candidates follow S1. `key_candidates` gives, for the concept and year, exactly
  the rows of the S1 filing at the year's own period, with the value it reports: NVDA diluted
  EPS FY2023 0.17 (0001045810-25-000023), FY2024 1.19 and FY2025 2.94 (0001045810-26-000021),
  FY2026 4.90 (its own 0001045810-26-000021); AVGO FY2022 2.65 (0001730168-24-000139), FY2023
  3.30 (0001730168-25-000121); AMD receivables FY2023 $4,323M (0000002488-25-000012), FY2024
  $6,192M (0000002488-26-000018), FY2025 $6,315M (the Q2 FY2026 10-Q 0000002488-26-000123,
  filed 2026-08-05). Each candidate ends on the own filing's DEI period end (inclusive ISO), and
  the original value, when it differs (1.74, 11.93, 26.53, 32.98, $5,376M), is not a candidate.
  Properties: permuting the rows, dropping filings filed before the S1 filing (other than the
  year's own), or adding an older filing never changes the candidates; the candidate's accession
  and value equal `fiscal_year_key`'s on the same filings' companyfacts entries. Oracle: the
  companyfacts entries and DEI (S1 by hand), FSDS `num`; requirement 1.
- **C2**: the own column, exactly. No candidate stands in for a year whose own filing lacks the
  period: INTC FY2025 dividends declared per share and COST FY2025 goodwill have none, while
  COST FY2026 goodwill ($994M at 2026-08-30) is present. The cover-page share count is kept from
  the canonical 10-K, never the Part III-only 10-K/A: AMD FY2025 1,630,410,843 at 2026-01-30
  (0000002488-26-000018, not -26-000021); TSLA FY2025 3,752,431,984 at 2026-01-23
  (0001628280-26-003952, not 0001104659-26-053166). Properties: every flat candidate other than
  the cover count ends on the DEI period end of the year's canonical own filing; removing the
  own filing's rows leaves no flat candidate (refuse, never fall back). Oracle: DEI
  `DocumentPeriodEndDate` of the iXBRL instances; companyfacts; requirement 1.
- **C3**: dimensional candidates are unchanged. Over synthetic dimensional rows on real filings
  (NVDA FY2025 10-K 0001045810-25-000023 and FY2026 10-K 0001045810-26-000021), the candidates
  are the FY filing's current-period rows only, with cleaned labels (`"Compute & Networking"`,
  and `"Data Center | Compute & Networking"` for a two-axis fact ordered by axis); a later
  filing's row of the same member and period, a prior-period row and a non-canonical filing's
  row are not candidates. Property: adding later-labelled filings' rows of the same dimensional
  groups, with any values and filing dates, never changes the dimensional candidates. Oracle:
  set-b-tooling requirement 5 and the store's rule today (hand-built rows); requirement 1.
- **C4**: the stores wire it. Over a fake database (Notes), `PostgresKeyFactStore.facts_for`
  and `PostgresHxKeyFactStore.facts_for` return the C1 candidates for AMD FY2023 receivables
  ($4,323M, 0000002488-25-000012) and no other filer's rows; `resolve_case` on a lookup case
  written as `"4,323"` millions of `AccountsReceivable` is `verified` with exactly that filing's
  rows as its class, and the same case written from the original (`"5,376"`) is `not_found`.
  Property: permuting the store's rows and adding another filer's rows never changes
  `facts_for`. Oracle: companyfacts and S1 by hand; requirements 1–2.
- **C5**: the oracle document is the cited filing. `cited_filing_key` returns the cited filing's
  key for facts of one canonical annual filing (NVDA FY2023 EPS cited from
  0001045810-25-000023 → that 10-K's key), and None for facts of two filings, a 10-Q
  (AMD FY2025 receivables from 0000002488-26-000123), a non-canonical filing (the Part III-only
  10-K/A 0000002488-26-000021), an unknown fact id, or no ids. The oracle arm, given a resolver
  that is also a `CitedFilingResolver`, sends the cited filing's text for a case that cites
  facts, and the FY filing's text (`canonical_key`) for a case that cites none or whose
  citation has no single annual filing. Properties: the order and repetition of the ids never
  change the key; adding an id of another filing always makes it None; the arm reads the
  filing holding the case's cited facts whatever the case's fiscal-year label. Oracle: S1 by
  hand (restatement-s1 C1: NVDA FY2023 EPS 0.17 is cited from 0001045810-25-000023); EDGAR
  forms; requirement 3.

## Interface

New, stubbed in the spec PR:

`src/eval/set_b/pg_store.py`:
- `key_candidates(rows: Sequence[dict[str, Any]], fiscal_year: int) -> list[dict]`

`src/eval/systems/base.py`:
- `class CitedFilingResolver(ABC)` with
  `@abstractmethod fact_filings(self, fact_ids: list[int]) -> dict[int, dict]`

`src/eval/systems/long_context.py`:
- `cited_filing_key(fact_ids: Sequence[int], filings: dict[int, dict[str, Any]],
  forms: Sequence[str] = ANNUAL_FORMS) -> str | None`

Behaviour changes, no signature change: `PostgresKeyFactStore.facts_for`,
`PostgresHxKeyFactStore.facts_for` (requirement 2), `LongContextSystem.answer` (3);
`PostgresFilingResolver` also implements `CitedFilingResolver` (not spec-tested: SQL over
Postgres). `src/retriever/xbrl/restatement.py`'s functions move to a neutral module and are
re-exported (requirement 1; their signatures and behaviour do not change).

## Notes

- **Fixtures** (`test/spec/set-b-restatement/fixtures/`): byte copies of
  `test/spec/restatement-s1/fixtures/` as merged at commit 2334f83 of the development repository, whose provenance
  (SEC companyfacts URLs, full-document SHA-256 and size, retrieval dates; EDGAR primary
  document URLs and instance SHA-256; FSDS archive URLs and SHA-256) is recorded inside each
  file and in [restatement-s1](restatement-s1.md) Notes. SHA-256 of the copies, checked by
  `conftest.py` when the suite loads:
  - `companyfacts.json` `9ecd720efe49e1ae8af857003d3fc606499a69089c0c6af51528b9c9d42f5255`
  - `dei.json` `2e261b57dd0843f2b4999df274ad4691bfd2d495972721f6c91a6552a8436a1f`
  - `fsds.json` `15bfc9524dce3bb7f0e301742e8c2f5fb409adb76703c61db51490ec2491b536`

  No set B, H-X or `xbrl_heldout` content is used; every filer and filing here is a main-suite
  or development filing. The dimensional rows of C3 are synthetic (hand-written values and
  labels) on real filings' accessions and DEI dates.
- **Fake database** (C4; `PostgresKeyFactStore(conn=...)` takes the connection, the I/O
  boundary): `cursor(cursor_factory=<any non-None>)` yields dict rows; `execute(sql, params)`
  with a mapping `params` holding `ticker` returns **every** stored row of that ticker (all
  filings, periods, flat and dimensional, with `dimension_labels`) and ignores every other
  predicate; a query without a dict cursor or without a `ticker` mapping parameter returns no
  rows. The selection must therefore happen in `key_candidates`.
- **The suite's other lookup templates are not on S1 (found while writing this spec; not part
  of it).** restatement-s1 brought `aq-1a` and `aq-1c` to S1, but `aq-5a` (balance-sheet
  lookup), `aq-6a` (cash-flow lookup) and `aq-10a` (TSMC TWD lookup) still select `fiscal_year =
  %(fy)s` with the latest period first, i.e. the labelled filing, while the product's lookup
  answers S1 for every flat lookup (restatement-s1 requirement 3). On a restated period they
  disagree in value (restatement-s1's INTC FY2022 financing cash flow: the product answers
  $1,115M, an `aq-6a`-style key $1,361M; INTC is not a suite filer, the shape is the same), and
  on every period a later filing repeats they cite a different accession (as the 104 moved
  `aq-1a`/`aq-1c` citations did). `aq-5a` also lacks the exact own-column end, so it can still
  key a stale comparative (restatement-s1 G1). Such cases grade correct S1 answers as wrong, and
  set B's consistency test (`test_store_resolves_main_suite_facts_and_labels`, which only looks
  at the **first** lookup case) would fail on them once the store follows S1. Bringing
  `aq-5a`/`aq-6a`/`aq-10a` to the `aq-1a` form and rebuilding and diffing the suite belongs
  before the v1 suite commit (a `Spec-Skip: reviewed` template change). Not verified against
  the database here (no database access was used); the templates are read as committed.
- **Per-share units** (found, not part of this spec): set B accepts the unit `USD/shares`
  (set-b-tooling requirement 1) and `find_classes` compares it to the stored unit, which the
  store writes `shares*USD` (`v2_sources.py`, `family_resolution.display_unit`). A set B
  per-share key can then never resolve. A follow-up should compare units in their shown form.
- **The SEC check's missing S1 test.** `check_fact` says `match` for a cited fact whose value
  companyfacts confirms at its accession, even when a later filing reports the period (it lists
  those as `restatements`). It never asks whether the cited accession is the S1 one, which is
  why the 104 moved citations did not show up there. A follow-up could flag a citation that is
  not companyfacts' latest-filed report of the period (`latest_filed_key`); it would change the
  locked sec-companyfacts-check statuses, so it is not done here.
- **Retired spec tests.** None encode the old behaviour: set-b-tooling and hx-authoring test the
  pure functions over fake `KeyFactStore`s; long-context-arm's fake resolver is only a
  `FilingResolver` and its cases cite no facts, so requirement 3 leaves them unchanged.
  `test/unit/test_set_b_tooling.py::test_store_resolves_main_suite_facts_and_labels` (live
  database, not a spec test) is the integration check: it must pass against the rebuilt suite
  after the implementation (and the template fix above).

### Decisions for the maintainer

1. **Dimensional facts stay on the labelled filing** (requirement 1). Recommendation: yes for
   now. The product's multidimensional lookup and segment/geographic breakdowns, and the suite's
   breakdown templates (`aq-3a/3b/4a/10b`, `aq-8b`), still read the FY filing; moving set B's
   dimensional candidates alone would make the main-suite breakdown citations unresolvable.
   They move together with the product in the later dimensional S1 spec.
2. **The oracle arm gets the cited filing only, not both** (requirement 3). Recommendation: the
   cited (S1) filing alone. It holds the keyed value (a later 10-K carries the year as a
   comparative column), costs one document like today, and keeps the one-filing prompt cache.
   Sending both doubles the input cost and makes the arm choose between two columns that may
   differ, which is a different test from "given the right document". A case whose citation is
   a 10-Q (an instant whose latest report is a later 10-Q's comparative) or spans filings keeps
   today's FY filing; its keyed value may then differ from the document's, and such cases can
   be counted from the run (`selection` of the arm) if they matter.
3. **The cover-page share count is a set B candidate** (requirement 1, through
   `current_column`). Today's `is_current_period` filter usually excludes it (its date is after
   the period end). Recommendation: include it, as the product answers it as a disclosed
   substitute (shares-outstanding-reading); a `shares` key then resolves to whichever of the
   balance-sheet count or the cover count carries the written value.
