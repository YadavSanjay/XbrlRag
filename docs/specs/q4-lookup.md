# q4-lookup — a Q4 lookup answers the filed fourth quarter, or FY minus the nine-month year-to-date

[`period-disclosure`](period-disclosure.md) R7.6 (deferred), finding G9: no stored row has
`fiscal_period = 'Q4'`, because a 10-K is labelled `FY`, so a lookup with
`fiscal_period = "Q4"` is always `NOT_FOUND` (`_derive_duration_kind("Q4")` pins
`quarter`, and `current_column(rows, fy, "Q4")` finds no filing). The fourth quarter is
nevertheless stated or derivable: MSFT FY2026 revenue 331,839M − 241,832M = 90,007M
(period-disclosure C5), and some 10-Ks tag the fourth quarter themselves (MSFT FY2026 cash
dividends declared, 6,759M).

`derive_q4` (period-disclosure R5, locked and graduated) already does the arithmetic and its
refusals on two given rows. This change decides **which rows**: the year's row and the
nine-month row of the same fiscal-year label, concept and unit, under the S1 rule and the
reporting-currency rule, and when a filed fourth quarter answers instead.

**Why a filed Q4 wins.** MSFT FY2026 cash dividends: the 10-K tags the fourth quarter
(6,759M, FSDS `qtrs` 1 at 2026-06-30) and the year (27,035M); the Q3 10-Q tags the nine
months (20,278M). FY − 9M = 6,757M, not 6,759M: the 10-K restates the third quarter from
6,760M (10-Q) to 6,758M, so the 10-Q's nine-month total is on an earlier basis than the
year. The filed figure is the filer's own statement on one basis; the derivation is a
reconstruction across two filings. Derivation is the fallback, never the override.

**Why a filed Q4 is checked against its year.** ORCL's FY2022 10-K
(`0001564590-22-023675`) tags its annual `Revenues` (42,440M, the year's revenue) with a
three-month context (2022-03-01 to 2022-05-31): a quarter-length fact equal to the year.
A quarter that equals its own year with a non-zero remainder of the year is a filer's
mis-tag. A filed quarter equal to its year is therefore set aside and the derivation
decides (it answers when the nine-month value is zero, and refuses without one).

**Instants.** "Total assets at Q4 FY2026" is the balance at the end of the fourth quarter,
which is the fiscal year's end: the same date, the same fact. There is nothing to derive
and nothing to refuse: the lookup answers the FY instant with a note that Q4 ends on the
fiscal year's end. `select_q4` itself derives durations only and refuses an instant-only
input (it is never asked one once wired; the refusal keeps a wrong call loud).

## Requirement

`select_q4(rows, fy, unit=None, as_originally_reported=False)` takes the stored
`mv_financial_facts` rows of one filer and one measure (any concept of the family, any
filing, any period, as the flat lookup's S1 query reads them, with `fiscal_year`,
`fiscal_period`, `filed_date`, `is_amendment`, `is_canonical_filing`,
`document_period_end`, `accession_no`, `concept_role`, `concept_pref_rank`, `presented`,
`decimals`, `fact_id`, `concept`, `unit`, `value`, `period_start`, `period_end`,
`segment_axis`, `segment_member`) and returns the fourth quarter of fiscal-year label `fy`.

1. **Rows considered.** Only rows with no dimension (`segment_axis` None) and a duration
   (`period_start` not None). When `unit` is given, only rows whose shown unit
   (`display_unit`) equals `display_unit(unit)`. When every non-dimensional row is an
   instant, the result is a refusal whose reason contains `instant`.
2. **The year.** The year's own rows are `current_column(rows, fy, "FY")` (with
   `as_originally_reported`, the original filing's) of class `annual`
   (`duration_class`). With no `unit`, when they carry more than one shown unit, the
   result is a refusal whose reason contains `unit` (the caller decides the unit: the
   requested one, else the reporting currency, `answer_unit`). The year's row is the first
   one `candidate_order` keeps; none is a refusal.
3. **A filed fourth quarter answers first.** A filed Q4 is a row of the year's own column
   (as in 2) of class `quarter`, with the year's row's concept (ignoring a taxonomy release
   year in the namespace) and shown unit, ending on the year's row's stored end, valued,
   and **not equal to the year's row's value** (a quarter equal to its year is set aside,
   see above). When there is one, the first `candidate_order` keeps answers: `status
   "filed"`, its value, `fact_ids = (its fact_id,)`, `accessions = (its accession,)`, its
   stated period (start; stored end − 1 day), `days` = stored end − stored start, its
   `decimals`, and a `note` that contains `filed`. When a derivation (5) also succeeds and
   gives a different value, the note also names the derived value (its digits, with or
   without thousands separators).
4. **The nine months.** The nine-month rows are `current_column(rows, fy, "Q3")` (with
   `as_originally_reported`, the original filing's) of class `nine_month`, with the year's
   row's concept, shown unit and `period_start`. The nine-month row is the first one
   `candidate_order` keeps; none is a refusal whose reason contains `nine-month`.
5. **The derivation.** `derive_q4(year, nine_month)`; its refusal is the result's refusal
   (its reason kept). Derived: `status "derived"` and `derive_q4`'s `value`, `period_start`,
   `period_end`, `days`, `decimals`, `fact_ids` (year first), `accessions` and a note that
   contains `derived`.
6. **One basis (S1).** Without `as_originally_reported`, each side's row is its latest
   restatement (`latest_restatement` over the considered rows; the first row
   `candidate_order` keeps). A side is *restated* when that row's value differs from its
   own column's value.
   - neither or both sides restated: the latest rows, `basis "latest"`;
   - exactly one side restated: both own-column rows (the two filings as filed, one basis),
     `basis "as_filed"`, and the note says which side was later restated (it contains
     `restated`). A year restated without its nine months (or the reverse) would put the
     whole restatement into Q4.
   - `as_originally_reported`: both original rows, `basis "original"`.
   A filed Q4 (3) is likewise its latest restatement, `basis "latest"`, or `"original"`.
7. **Result.** Keys exactly `status` (`"filed"`, `"derived"`, `"refused"`), `value`,
   `unit` (the shown unit), `period_start`, `period_end` (stated ISO dates), `days`,
   `decimals`, `fact_ids`, `accessions`, `basis`, `note`, `reason`. `reason` is None unless
   refused; a refusal has `value` None, a non-empty `reason`, and every other key None.

### Wiring (not locked; implementer, with unit tests over a fake connection and a live test)

1. `XbrlFactstoreLookup.lookup` with `fiscal_period == "Q4"`:
   - an **instant** family: answer the step as `fiscal_period="FY"` (every existing rule:
     S1, reporting currency, the S4 defined-measure path for `Debt` / `DeferredRevenue`,
     which are instants) and add the resolution note "Q4 ends on the fiscal year's end; the
     FY-end balance answers";
   - a **dimensional** step (any dimension): `NOT_FOUND`, reason "a fourth quarter is
     derived only for a non-dimensional measure" (the member views have no S1 column
     selection yet; a later change);
   - a **duration** family with no dimension: read the measure as the flat S1 query does
     (every filing and period) with **no** `duration_kind` pin, decide the unit as today
     (`step["unit"]` when given, else `answer_unit` over the year's own candidates and the
     observations), apply `as_of` by keeping rows with `filed_date <= as_of` before the
     call, then `select_q4`. `filed` / `derived` → `OK` with the value, the shown unit,
     the citation of `fact_ids[0]` and every fact id carried (as `sum_of` carries its
     parts: a new `derived_from` field, or `sum_of` with the note; the executor and
     citation integrity must accept both ids), the note in `resolution_notes`; `refused` →
     `NOT_FOUND` with `error` = the reason, also in the notes.
2. `_derive_duration_kind("Q4")` is no longer used for durations (no stored row is
   labelled Q4).
3. `plan_validator.VALID_FISCAL_PERIODS` already contains `Q4`, and the router and
   `fiscal_date` already map "fourth quarter" / "Q4": no change. `ytd` with `Q4` is the
   year (a planner should ask `FY`); the lookup ignores `ytd` for Q4.
4. The synthesizer states the derivation note with the number (as it states a
   definition's disclosure).
5. `src/retriever/xbrl/CLAUDE.md`: the Q4 line under "Period lengths".

## Acceptance criteria

- **C1**: Derived Q4 (R4, R5). MSFT FY2026 revenue
  (`RevenueFromContractWithCustomerExcludingAssessedTax`), from the 10-K
  `0001193125-26-323660` and Q3 10-Q `0001193125-26-191507` store rows: `derived`,
  90,007,000,000 USD, period ("2026-04-01", "2026-06-30"), 91 days, decimals −6, fact ids
  (a year row, a nine-month row) of those values and accessions, basis `latest`, a note
  with `derived`. Exact (integer values). Oracle: FSDS `num` (`qtrs` 4 at 20260630 =
  331,839,000,000; `qtrs` 3 at 20260331 = 241,832,000,000), by hand; period-disclosure C5.
  Properties: the result does not depend on the rows' order, or on added rows of other
  labels, dimensions or units (with `unit` given); over synthetic corpus-shaped pairs, a
  derived Q4 plus the nine months is the year and cites (year, nine months).
- **C2**: Filed Q4 first (R3). MSFT FY2026 cash dividends (`DividendsCommonStockCash`):
  `filed` 6,759,000,000 (fact 105540), period ("2026-04-01", "2026-06-30"), 91 days, fact
  ids (105540,), basis `latest`, a note with `filed` that names the derived 6,757,000,000
  (27,035,000,000 − 20,278,000,000). Dividends per share declared: `filed` 0.91 (fact
  105539), unit `USD/shares`, where FY − 9M = 3.64 − 2.73 = 0.91 agrees. A filed quarter
  equal to its year (ORCL FY2022 shape) is set aside: with a nine-month row the answer is
  derived; without one it is refused. Exact. Oracle: FSDS `num` (10-K `qtrs` 1 and 4 at
  20260630, 10-Q `qtrs` 3 at 20260331) by hand; the ORCL mis-tag from the 10-K's context
  (2022-03-01 to 2022-05-31, 42,440M, equal to its FY2022 revenue). Properties: with a
  filed quarter (≠ the year) present, the answer is it whatever the nine-month value;
  without it, the derivation.
- **C3**: Refusals (R1, R2, R4, R5, R7). Refused, value None, a non-empty reason, every
  other key None: MSFT revenue without the 10-Q (no nine months; reason has `nine-month`);
  with only the 10-Q's discrete third quarter; for a label with no rows (FY2025); a
  nine-month row with another start; instants only (reason has `instant`); dimensional
  rows only; `unit` that no row carries. Oracle: R1–R5 by hand. Property: any subset of the
  MSFT revenue rows gives either the C1 value or a refusal, never another value.
- **C4**: One basis (R6). Synthetic corpus-shaped filer: FY2024 10-K year 1,000,000,000,
  FY2024 Q3 10-Q nine months 700,000,000. A FY2025 10-K restating the year to 1,030,000,000
  alone → `as_filed`, 300,000,000, own fact ids, a note with `restated`; with a FY2025 Q3
  10-Q also restating the nine months to 720,000,000 → `latest`, 310,000,000, the later
  fact ids; restatements that repeat the values → `latest`, 300,000,000;
  `as_originally_reported` with both restated → `original`, 300,000,000, own ids. Exact.
  Oracle: R6 and S1 (`XbrlRag_Eval_Semantics.md`: values in a comparison come from one
  basis) by hand. Property: for any changes of the two sides, the basis and value follow
  R6's table.
- **C5**: Units (R1, R2). With no `unit`, a year's own column in USD and a EUR convenience
  translation → refused (reason has `unit`); `unit="USD"` → the C1 answer. Per-share rows
  stored `shares*USD` answer `unit="USD/shares"` and show `USD/shares`. Oracle: R1/R2 and
  `display_unit` (ifrs-family-meaning R5) by hand. Property: renaming every row's unit to
  one code and passing it gives the same value; passing an absent unit refuses.

## Interface

New module `src/retriever/xbrl/q4_lookup.py` (pure; no I/O):

- `select_q4(rows: Sequence[Mapping[str, Any]], fy: int, unit: str | None = None, as_originally_reported: bool = False) -> dict[str, Any]`
  — keys exactly `status`, `value`, `unit`, `period_start`, `period_end`, `days`,
  `decimals`, `fact_ids`, `accessions`, `basis`, `note`, `reason`.

It reuses `duration_class` and `derive_q4` (period-disclosure), `current_column` and
`latest_restatement` (`src.numerics.restatement`), `candidate_order`
(`family_resolution`) and `display_unit`; it adds no second definition of any of them.

## Notes

- **Fixtures** (`test/spec/q4-lookup/fixtures/`):
  - `store_rows.json`: the 44 non-dimensional `mv_financial_facts` rows of MSFT for the
    three concepts in the two filings (read-only `xbrl_db`, 2026-11-11), with the S1 and
    order columns.
  - `fsds_num.json`: the 13 FSDS `num` lines of the same filings and tags (no segment, at
    20260630 / 20260331), copied unchanged from `data/cache/sec_fsds/filtered/<q>/num.tsv`,
    with the zips' URLs and SHA-256 (2026q2, 2026q3; the filtered `num.tsv` SHA-256 match
    period-disclosure's fixture).
  - C2's mis-tag case and C4's restatements are synthetic rows shaped like the store's
    (built in the tests' `conftest.py`).
- **Corpus evidence** (read-only `xbrl_db`, 2026-11-11):
  - Q3 10-Qs in the corpus: 5 filer-years, all label 2026 (AAPL, MU, MSFT, ORCL, AVGO).
    Non-dimensional FY `annual` + Q3 `nine_month` pairs in own columns with the same
    concept, unit and start: 273 rows over **2 filer-years** (MSFT and ORCL FY2026);
    remainders 91 or 92 days. AAPL, MU and AVGO have no FY2026 10-K in the corpus yet.
  - Filed quarter-length rows in a 10-K's own column (non-dimensional): 66 rows, 15 filers;
    by family: unmapped 46, `DividendsPerShare` 16, `DividendsPaid` 2, `Revenue` 1 (the ORCL
    FY2022 mis-tag above), `RestructuringCharges` 1 (MCD FY2023 Q4, 30.0M).
  - Where both exist: 2 concepts; DPS agrees, cash dividends differs by 2M (above).
  - Restated values by a later-labelled filing: 2 annual own-column rows, 0 nine-month.
  - `nine_month` rows: 1,519, and `other` exists (2,599 rows): period-disclosure R7.1's
    bands are live.
- **Suites.** No case of the frozen v2 dev, validation, dev-compound and
  validation-compound suites nor of `sql_derived_v1` asks a quarter (`quarter`, `Q4`,
  `fourth quarter` occur 0 times; every case is annual, per K5). **Expected movement on
  every frozen suite: none.** A Q4 question today is always `NOT_FOUND`; after the change
  MSFT and ORCL FY2026 durations answer, filed fourth quarters answer where tagged, and
  everything else is `NOT_FOUND` with its reason.
- **Out of scope:** dimensional Q4 (segment, geographic, multidimensional), Q4 screens, and
  a Q4 `compute` (growth of Q4 over the prior Q4). Each needs the same row choice per member
  or filer and is a later change.
