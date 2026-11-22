# shares-outstanding-reading — a shares-outstanding key is the year-end balance, and the cover-page count of the same 10-K is a disclosed alternate

**"Shares outstanding" has two exact readings in one 10-K:**
- the balance sheet's `us-gaap:CommonStockSharesOutstanding` at the fiscal year end;
- the cover page's `dei:EntityCommonStockSharesOutstanding`, dated a few weeks after the year
  end (the latest practicable date before filing).

Both are audited facts of the same filing, and an honest answer may give either. They differ
by more than the key tolerance (1e-4): Union Pacific FY2025 is 593,245,884 at 2025-12-31 and
593,391,460 on the cover (2026-01-30), 2.5e-4 apart; Qualcomm FY2025 is 1,074M and 1,071M,
2.8e-3 apart. Without an alternate, a correct cover-page answer is graded wrong.

**Evidence.** In the Anthropic smoke of 2026-11-05, the Procter & Gamble case was keyed with
`CommonStockSharesIssued` (4,009.2M; the rank bug that spec ns-expand-rank-sync fixes).
`long_context_oracle` answered about 2,362M, `frontier_sec_tools` 2,357.3M and `closed_book`
about 2.36B. Those figures are P&G's **FY2023** numbers (Notes). The two exact readings
(2,362.1M issued minus treasury, 2,357.3M cover) are 0.2% apart.

**Decision (maintainer).** A shares-outstanding key means common shares outstanding at the
fiscal year end: `us-gaap:CommonStockSharesOutstanding` for the FY period end. The cover-page
count of the same 10-K is accepted as a disclosed alternate answer.

**Mechanism today.** `EvalCase.alt_values` lists "accepted alternate forms" (a ratio as 0.75 or
75.0). `grade_scalar` accepts a value within `rel_tol` of any of `targets()`, but the grade record
does not say which target matched, and `_match_rows` (trend, point-in-time) reads only each row's
`value`. A cover-page count put in `alt_values` would be accepted silently, as if it were the key.
This spec adds the smallest marked mechanism: **alternate readings**, each with a name, graded
separately and recorded in the grade.

## Requirement

1. **The key.** A `SharesOutstanding` intent is keyed with `us-gaap:CommonStockSharesOutstanding`,
   unit `shares`, at the instant the fiscal year ends (`labelled_fiscal_year_key`, as today: S1 and
   S2 apply). The suite builder keys a `SharesOutstanding` intent **only** when the inventory's
   concept for that filer-year is `us-gaap:CommonStockSharesOutstanding`. Any other concept
   (`CommonStockSharesIssued`, a dei concept, anything a rank slip puts first) drops the intent
   with reason `concept_mismatch`, which the manifest counts under `dropped` and
   `dropped_by_shape`. This holds for every shape that keys the metric.
2. **No outstanding-shares fact, no intent.** A filer-year that reports no
   `CommonStockSharesOutstanding` has no `SharesOutstanding` key. The cover count never becomes a
   key on its own, and issued minus treasury is never computed into a key. With
   concept-key-integrity the inventory has no entry for such a filer-year (the issued count is a
   component), and requirement 1 drops any entry that slips through.
3. **The cover reading.** `cover_shares_key(company_facts, labels, fiscal_year, period_end)`
   returns the cover count of the filing labelled `fiscal_year`, or `None`:
   - candidates: the entries of `dei:EntityCommonStockSharesOutstanding` in unit `shares` with a
     form in `ANNUAL_FORMS`, `fp` `FY`, no `start`, a non-nil `val`,
     `filed_under_year(entry, labels, fiscal_year)`, and `period_end < end <= filed`;
   - of those, only the entries of the **original** annual report: the accession with the
     smallest `(filed, accn)`. A later amendment's cover is a different date and is ignored;
   - when those entries carry more than one distinct value (two share classes or two dates in
     companyfacts' non-dimensional view), `None`: the reading is ambiguous;
   - no candidate, a missing concept or a missing unit: `None`, never an error.

   The returned `AnswerKey` has the entry's `val`, `unit "shares"`, `decimals None`,
   `concept "dei:EntityCommonStockSharesOutstanding"`, the 10-digit CIK, `period_start None`,
   `period_end` the cover date (the entry's `end`), `fiscal_year` set, `calendar_year None`, the
   entry's `accn`, `filed` and `form`, `key_source "sec_companyfacts"`, `selection "cover_page"`,
   `policies ["S2", "S6"]`, `status "single-source"`, `verified_by []`.
4. **Alternate readings in the suite.** An alternate reading is a dict
   `{"reading", "value", "concept", "period_end", "accession"}`, taken from a cover key
   (`reading "cover_page"`). The v2 builder emits them for `SharesOutstanding` only:
   - **lookup:** the case carries `alt_readings: [<cover reading>]` when `cover_shares_key`
     finds one for the intent's year (with the key's `period_end`), else no `alt_readings`;
   - **trend:** each expected row whose year has a cover reading carries
     `alt_readings: [<that year's reading>]`; a row never carries another year's reading;
   - the case record's `key` gains `alternates`: the cover keys used, in year order. A
     `SharesOutstanding` lookup or trend with none found has `alternates: []`;
   - every other metric and shape (point-in-time, comparisons, ratios, screens) is emitted
     exactly as today: no `alt_readings`, no `alternates`, the same record and hash. Cover
     values never go into `alt_values` or `expected_value`.
5. **Case field.** `EvalCase` gains `alt_readings: list[dict]` (default empty), read by
   `from_dict` and written by `to_dict` when not empty. `targets()` is unchanged.
6. **Matching a reading.** `match_reading(value, targets, readings, rel_tol)` returns `"key"`
   when `value` is within `rel_tol` of one of `targets` (`close_enough`); otherwise the
   `reading` name of the first reading, in order, whose `value` is not `None` and within
   `rel_tol` of `value`; otherwise `None`. The tolerance is applied to each reading separately
   and is never widened: a value between the two readings that is close to neither matches
   nothing.
7. **Scalar grading.** For a case with `alt_readings`, `grade_scalar` accepts a produced value
   that matches a reading as it accepts one that matches the key: the same citation and
   grounding rules decide CORRECT or PARTIAL. A value matching the key (or an `alt_values` form)
   wins over one matching a reading. The grade records `matched_value` and
   `matched_readings {"value": "key" | <reading name>}`; when nothing matched,
   `matched_readings` is `None`.
8. **Trend grading.** A trend row is matched by an unused produced value within `rel_tol` of the
   row's `value`, or else of one of the row's own `alt_readings`; for each row in order, a key
   match is preferred over a reading match. Each row is graded separately, so an answer may give
   one year's balance-sheet count and another year's cover count. When any expected row carries
   `alt_readings`, the grade records `matched_readings {<row name>: "key" | <reading name>}` for
   the matched rows (`{}` when none matched). The `checks` keep their names and meaning.
9. **The grade record.** `CaseGrade` gains `matched_readings: dict[str, str] | None` (default
   `None`); `to_dict` includes `"matched_readings"` only when it is not `None`. A case without
   alternate readings grades exactly as before, with an unchanged record.
10. **Issued minus treasury.** An arm that computes issued minus treasury shares is graded on
    the number alone: it counts only when it equals the key or the cover reading within
    `rel_tol` (marked `"key"` or `"cover_page"` accordingly), and is wrong otherwise. No
    reading is added for it.

## Acceptance criteria

- **C1**: `cover_shares_key` selects the cover count of the original annual report labelled the
  year, as requirement 3 states, field for field. Point cases on a synthetic filer modelled on
  Union Pacific FY2023-25 (keys 609,703,814 / 604,241,260 / 593,245,884; covers 609,777,914 /
  604,286,378 / 593,391,460):
  - FY2025 with period end 2025-12-31 gives 593,391,460, cover date 2026-01-30, the FY2025 10-K;
  - a later 10-K/A for FY2025 with a different cover count does not change the result;
  - a cover entry dated on or before the period end, or after its filing date, is never chosen;
  - two distinct values in the original filing give `None`; a document without the dei concept,
    or without the `shares` unit, gives `None`.

  Properties: the result never depends on entries of other fiscal years or of later
  amendments; when not `None`, its value is one of the original filing's candidate values.
  Oracle: the definition (requirement 3) and the payload values, read by hand.
- **C2**: `match_reading` follows requirement 6. Point cases with `rel_tol` 1e-4, key 593,245,884
  and reading `cover_page` 593,391,460: the key gives `"key"`; the cover gives `"cover_page"`;
  593,318,672 (the midpoint, 1.2e-4 from each) gives `None`; a value within 1e-4 of both gives
  `"key"`. Properties: the result is `"key"` exactly when some target is within tolerance; it is
  never a reading whose value is outside tolerance; appending readings never changes a `"key"`
  result or an earlier reading's match. Oracle: the definition and `close_enough` by hand.
- **C3**: A v2 lookup case with key 593,245,884 and a `cover_page` reading of 593,391,460, cited:
  - the cover value grades CORRECT with `matched_value` 593,391,460 and
    `matched_readings {"value": "cover_page"}`;
  - the key grades CORRECT with `{"value": "key"}`;
  - issued minus treasury equal to the key (1,113,161,191 − 519,915,307) grades CORRECT with
    `"key"`; a derived 593,600,000 (6.0e-4 from the key, 3.5e-4 from the cover) grades
    INCORRECT with no `matched_readings`;
  - the cover value without a citation grades PARTIAL, `value_correct`, marked `"cover_page"`.

  Properties: for any produced value, the grade with readings is CORRECT exactly when the value
  is within `rel_tol` of the key or of a reading; removing the readings leaves the grade of a
  key match unchanged. Oracle: requirements 7 and 9 and the hand-computed distances.
- **C4**: A v2 trend case over FY2023-25 whose rows carry their own cover readings: an answer of
  the three covers, or of two keys and one cover, grades CORRECT with each row marked; an answer
  giving FY2023's cover for FY2024's row (and nothing within tolerance of FY2024) is INCORRECT
  with `checks["FY2024"]` false. Property: the per-row marks name, for each row, a reading whose
  value is within tolerance of a produced value. Oracle: requirement 8 and the values by hand.
- **C5**: `build_v2_suites` over a synthetic inventory and companyfacts:
  - a `SharesOutstanding` lookup case carries the cover reading of its year's original 10-K and
    `key.alternates` with that cover key; `EvalCase.from_dict` keeps `alt_readings`, and
    `to_dict` writes them back;
  - a `SharesOutstanding` trend case carries one reading per row, each from its own year's
    10-K (not the latest filing the key's value came from);
  - a `Revenue` lookup case of the same filer has neither `alt_readings` nor `key.alternates`;
  - a filer whose inventory lists `CommonStockSharesIssued` for `SharesOutstanding` yields no
    `SharesOutstanding` case, and the manifest counts `concept_mismatch` drops for it.

  Oracle: requirements 1, 4 and 5 and the payload values, read by hand.

## Interface

New functions (stubbed in the spec PR):

- `src.eval.keys.core.cover_shares_key(company_facts: dict, labels: dict[str, int], fiscal_year: int, period_end: str) -> AnswerKey | None`
- `src.eval.core.graders.numeric.match_reading(value: float, targets: Sequence[float], readings: Sequence[Mapping[str, Any]], rel_tol: float) -> str | None`

Behaviour on existing code (added by the implementer; a spec PR may only add stubs):

- `src.eval.core.case.EvalCase`: the `alt_readings` field (requirement 5).
- `src.eval.core.graders.base.CaseGrade`: the `matched_readings` field and its `to_dict` entry
  (requirement 9).
- `src.eval.core.graders.dispatch.grade_scalar` and `src.eval.core.graders.v2._match_rows` /
  `_grade_trend` (requirements 7-8).
- `src.eval.suites.v2_suite`: the `concept_mismatch` drop (requirement 1), the readings and
  `key.alternates` on lookup and trend cases (requirement 4).

## Notes

- **Figures** (SEC companyfacts in the local cache, read for this spec on 2026-11-05; no test
  reads them):
  - **Procter & Gamble** (CIK 0000080424) reports **no** `us-gaap:CommonStockSharesOutstanding`
    at all. Year-end `CommonStockSharesIssued` is 4,009.2M every year; treasury shares
    (`TreasuryStockShares` / `TreasuryStockCommonShares`) are 1,647.1M (FY2023), 1,652.2M
    (FY2024), 1,667.3M (FY2025), so issued minus treasury is 2,362.1M / 2,357.0M / 2,341.9M.
    The cover counts are 2,357,306,187 (2023-07-31), 2,354,050,987 (2024-07-31) and
    2,342,371,488 (2025-07-31). The smoke figures (about 2,362M and 2,357.3M) are FY2023's, so
    the arms answered the first year of the FY2023-25 trend (or read the wrong year); the
    FY2025 cover is 2,342.4M. Under requirement 2 P&G has **no** `SharesOutstanding` intent.
  - **Union Pacific** (CIK 0000100885): outstanding 609,703,814 / 604,241,260 / 593,245,884 at
    2023-12-31 / 2024-12-31 / 2025-12-31; covers 609,777,914 (2024-02-02), 604,286,378
    (2025-01-31), 593,391,460 (2026-01-30); issued 1,112,854,806 / 1,113,018,733 /
    1,113,161,191. No treasury-share tag in companyfacts; issued minus outstanding is the
    implied treasury count.
  - **Qualcomm** (CIK 0000804328): outstanding 1,114M / 1,113M / 1,074M at FY2023-25 year ends;
    covers 1,113M / 1,111M / 1,071M.
- **Fact store** (`xbrl_db`, read-only): 11 of 33 corpus filers tag no
  `CommonStockSharesOutstanding` (ABT, KO, LLY, MCD, META, MRK, PG, TSM, TXN, UPS, WMT). The
  store still ranks `CommonStockSharesIssued` 2 in `SharesOutstanding` until the
  concept-key-integrity rollout regenerates the mapping; ns-expand-rank-sync covers the slip.
- **Frozen suites** (`data/eval/v2_frozen`, dev and validation only): three `SharesOutstanding`
  intents. Dev: Qualcomm FY2025 lookup (`CommonStockSharesOutstanding` 1,074M; cover 1,071M),
  Union Pacific FY2023-25 trend, and Procter & Gamble FY2023-25 trend keyed with
  `CommonStockSharesIssued` 4,009.2M (no intent under requirement 2). Validation: the P&G trend
  again (other phrasings). The weighted-average share families (6 dev intents, 1 validation)
  are durations and have no cover reading.
- **Other share-count families.** `SharesOutstanding` is the only point-in-time share-count
  family: `WeightedAverageSharesBasic` / `Diluted` are period averages with one reading.
  `EmployeeCount` has the same shape of problem (a dei cover count beside a us-gaap one, dated
  differently) and is left out of scope. The lexicon's shorthand "share count" for
  `SharesOutstanding` collides with "basic share count" / "diluted share count" for the
  weighted averages (the Qualcomm question reads "How much share count did Qualcomm report");
  that is a wording change for `configs/eval/v2/lexicon.yaml`, not part of this spec.
- **Not in spec tests.** After the implementation merges, the frozen v2 dev and validation
  suites are rebuilt and diffed; only `SharesOutstanding` cases should change (readings added,
  P&G removed). Their new hashes go into the pre-registration amendment.
