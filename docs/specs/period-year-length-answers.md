# period-year-length-answers — an answer that compares fiscal years of different lengths says so

Follows [period-disclosure](period-disclosure.md) (retired), whose R7.4 asks that "answers that
compare fiscal years (a YoY or growth compute, `RevenueGrowthPct` / `NetIncomeGrowthPct`
screens, the bridge) carry `year_length_disclosure`'s note when `differs`". R7.4a (done) put both
periods and their day counts in `mv_yoy_comparison` and the note in the v1 variance bridge
(`src/api/v1/service.py::variance_bridge`, `year_length_note`). This change is R7.4b: the note in
the query pipeline's answers.

**The gap.** A 53-week fiscal year is compared with a 52-week one without a word. AAPL FY2024
revenue growth is +2.02% on the reported basis (FY2024 364 days, FY2023 371 days); per day it is
+3.98%. The number is right, but the reader is not told that the two years differ by a week
(period-disclosure G6). In the store (`xbrl_db`, read-only, 2026-11-11), 12 of 88
`RevenueGrowthPct` FY rows and 12 of 88 `NetIncomeGrowthPct` FY rows compare years 6 or more
days apart (AAPL, AMD, AVGO, COST, HD, INTC, MU, QCOM); a growth screen for FY2024 ranks 32
filers, 5 of them on unequal years.

A missing note goes unnoticed (nothing reads it), and an independent rule states the right
answer: period-disclosure C3, a note when the two day counts are 6 or more apart, with
`53-week` when either has 370–372 days. Route: locked spec.

## Requirement

### 1. One rule, from the cited facts

Whether two compared periods differ in length, and the note's words, are period-disclosure's
`year_length_disclosure(current, prior)` and nothing else (R3 there: `days = period_end -
period_start` on stored rows, `differs` at 6 or more days apart, `53-week` at 370–372). The two
periods are the **cited facts' stored periods** (`mv_financial_facts.period_start`,
`period_end`, the end one day after the period's last day), never a fiscal-year label, never a
date the LLM writes. The note never changes a number: the value stays on the reported basis.

### 2. Compute and cagr steps (`compared_year_lengths`)

`compared_year_lengths(inputs)` takes the input steps of one `compute` or `cagr` step, in the
order the step references them. Each input is a mapping with `step_id`, `op`, `status`, `cik`,
`concept_family`, `fiscal_period`, `ytd`, `fy`, `fact_id`, `period_start` and `period_end`
(the cited fact's stored period).

1. **Eligible inputs:** `op == "lookup"`, `status == "OK"`, `period_start` and `period_end`
   both not None (an instant has no length), `fy` an int (not a bool).
2. **Compared years:** eligible inputs are grouped by `(cik, concept_family, fiscal period,
   ytd)`, the fiscal period being `fiscal_period` upper-cased (None is `"FY"`) and ytd
   `bool(ytd)`. Within a group, each fiscal year is represented by its first input in input
   order; the years are sorted ascending and each consecutive pair is compared, the later year
   being `current` and the earlier `prior`. A group with one fiscal year compares nothing; two
   metrics, two filers, a year with a quarter, or a quarter with a year-to-date are never
   compared.
3. **Entries:** one per compared pair for which `year_length_disclosure(current, prior)`
   `differs`, with keys exactly `current_step`, `prior_step` (the step ids), `fact_ids`
   (`[current fact_id, prior fact_id]`), and `days_current`, `days_prior`, `current_period`,
   `prior_period`, `note` equal to `year_length_disclosure`'s. Order: groups in the order of
   their first eligible input, pairs in ascending year. No pair differs → `[]`.

Any duration is compared, not only years: a 14-week quarter against a 13-week one (AAPL Q1
FY2023, 98 days, against Q1 FY2022, 91 days) differs by 7 days and is stated the same way. The
note's words are period-disclosure's ("fiscal periods"), so they fit a quarter too.

### 3. Growth screens (`screen_year_lengths`)

A growth screen ranks a derived metric whose row cites two facts of one filer, the current and
the prior year (`mv_derived_metrics.component_fact_ids`, e.g. `{"revenue_current": …,
"revenue_prior": …}`).

`screen_year_lengths(rows, periods)` takes the screen's ranked rows (each with `ticker` and
`component_fact_ids`) and `periods`, fact id (int) → that fact's stored `period_start` and
`period_end`. A row is **compared** when its `component_fact_ids` is a mapping with exactly one
key ending in `_current` and exactly one ending in `_prior`, each value an int (not a bool) or a
string of digits, and both ids are in `periods` with `period_start` and `period_end` not None.
For each compared row whose `year_length_disclosure(periods[current], periods[prior])`
`differs`, one entry with keys exactly `ticker` (`str`), `fact_ids` (`[current, prior]` as
ints) and `days_current`, `days_prior`, `current_period`, `prior_period`, `note` from
`year_length_disclosure`, in row order. Any other row adds nothing.

**Per member, not one universe note.** A ranked growth screen compares filers with each other;
a 53-week year adds about 1.9 points of growth to its filer alone and can move that filer's
rank. In the store a minority of each year's filers is affected (3 to 5 of 15–32), so a single
"some years differ in length" would tell the reader nothing about which rows to discount, and
naming every filer would hide the few that matter. The answer names exactly the members whose
two years differ, with both periods.

### 4. The screen's sentence (`screen_year_length_text`)

For entries `e1 … en` (n ≥ 1), in the given order:

```
Growth compares fiscal years of different lengths: <part 1>; …; <part n>. Growth is on the reported basis, not adjusted for the different number of days.
```

where each part is `f"{ticker} {cs} to {ce} ({dc} days) against {ps} to {pe} ({dp} days)"`
with `(cs, ce) = current_period`, `(ps, pe) = prior_period`, `dc = days_current`,
`dp = days_prior`, followed by `, one is a 53-week year` when `dc` or `dp` is 370, 371 or 372.
No entry → `""`.

Every number in the sentence is an ISO date or a count followed by `days`, and `53-week` is a
compound word, so `src/numerics/stated_numbers.read_stated_numbers` (the citation-integrity
reader) returns none: the sentence can never make citation integrity drop a statement.

### 5. Appending it (`append_year_length_disclosures`)

`append_year_length_disclosures(summary, steps)` (steps as `StepResult.to_dict()` dicts)
appends, in step order, `screen_year_length_text(step["year_lengths"])` for every step with
`op == "screen"` and a non-empty list `year_lengths`, skipping a sentence the summary already
contains or that is already being appended; sentences are one space apart and one space after
the summary (stripped of trailing whitespace; no leading space for an empty summary). With
nothing to add the summary is returned unchanged. It mirrors `append_screen_disclosures`
(screen-universe), and for the same reason: the sentence is built from the screen's data, so
no member or period can be dropped or misquoted.

### 6. Wiring (not locked; implementer)

1. **Where a lookup's period comes from.** `PlanExecutor._execute_lookup` has the
   `FactLookupResult`; the cited fact is the candidate in `detail.candidates` whose `fact_id`
   equals `citation.fact_id` (`FactCandidate.period_start` / `period_end`, stored). No such
   candidate (a derived or summed lookup with no single cited fact) → the input's periods are
   None, so it is not eligible (Requirement 2.1). Do not add the period to the lookup's
   `to_dict()` (every lookup would grow the synthesizer's input); keep it on the `StepResult`
   or read it from `detail` when the compute runs.
2. **Compute and cagr.** In `_execute_compute`, after an `OK` result, build one input per
   referenced step id (`XbrlCalculatorTool.referenced_step_ids(expr)`, in that order) from
   the plan step (`cik`, `concept_family`, `fiscal_period`, `ytd`, `fy`) and its result
   (`op`, `status.value`, cited `fact_id`, periods), and set `StepResult.year_lengths =
   compared_year_lengths(inputs)`. `_execute_cagr` does the same over `[start, end]`. A step
   that is not OK carries none. `to_dict()` emits `"year_lengths"` only when non-empty. Log the
   entries on the step's `numeric_lineage` event (`year_lengths=`).
3. **Growth screens.** `XbrlFactstoreScreen._query_derived`: for derived rows of the screened
   year, read `period_start, period_end` of every component fact id from `mv_financial_facts`
   (extend `_query_fact_units`, or a sibling query that also passes `fy` so the existing spec
   fakes answer it), call `screen_year_lengths(ranked_rows, periods)` on the ranked rows, and
   carry the entries to the step as `year_lengths` (`ScreenResult` → `StepResult`, emitted by
   `to_dict()` only when non-empty). Each ranked row whose ticker has an entry may also carry
   it as `year_length`, so the synthesizer can say it in that company's statement.
4. **Synthesizer.** A new prompt version `_base/synthesizer/v7` (v6 unchanged; bump the
   default the stage resolves): a `compute` or `cagr` entry with `year_lengths` states each
   entry's `note` **verbatim** in the statement that states the value, citing the step;
   never restates the value per day, never writes the day counts in other words (`53 week`
   without the hyphen is read as a number by the stated-number reader and the statement would
   be dropped). A screen's year-length sentence is appended automatically; the synthesizer does
   not list those members itself.
5. **Pipeline.** `XbrlRetrievalPipeline` calls `append_year_length_disclosures(summary,
   steps)` right after `append_screen_disclosures` and before `append_aggregate_disclosures`,
   per sub-query, as those are called.
6. **Torch-free image.** `year_length_answers` is imported on the serving path: add it to
   `TORCH_FREE_MODULES` (`test/unit/test_torch_free_contract.py`) if that list is explicit.
7. **Docs.** `src/retriever/xbrl/CLAUDE.md` "Period lengths" bullet: replace "Not yet wired:
   the note in pipeline YoY/growth answers" with the wiring above; guide 6.
8. **Unit tests** (`test/unit/`): the executor sets `year_lengths` on an OK compute and cagr
   over two lookup results with the AAPL periods, not on an ERROR compute; the screen's derived
   path carries them (fake at `psycopg2.connect`); the pipeline appends the sentence once.

## Acceptance criteria

Inputs and rows are hand-built from the stored periods period-disclosure C3 already pinned
(AAPL fact 14262 FY2024 2023-10-01 to stored 2024-09-29; 12096 FY2023 2022-09-25 to stored
2023-10-01; 10919 FY2022 2021-09-26 to stored 2022-09-25; MSFT 105742 FY2026 2025-07-01 to
stored 2026-07-01; 104052 FY2025 2024-07-01 to stored 2025-07-01) plus the AAPL first quarters
(FY2023 Q1 2022-09-25 to stored 2023-01-01, 98 days; FY2022 Q1 2021-09-26 to stored
2021-12-26, 91 days). Nothing reads `data/`, a database or the clock; nothing in `src` is faked.

- **C1**: Compared years (Requirement 2). AAPL revenue FY2024 (`s1`, 14262) and FY2023 (`s2`,
  12096), referenced in either order: one entry, `current_step` `s1`, `prior_step` `s2`,
  `fact_ids` `[14262, 12096]`, 364 and 371 days, periods ("2023-10-01", "2024-09-28") and
  ("2022-09-25", "2023-09-30"), a note with `364`, `371`, `53-week` and `reported basis`.
  FY2022–FY2024 together: two entries, FY2023 against FY2022 (371, 364) then FY2024 against
  FY2023 (364, 371). MSFT FY2026 against FY2025 (365, 365) in the same step as the AAPL pair:
  only the AAPL entry. Exact. Oracle: period-disclosure C3 (SEC companyfacts start/end), day
  counts by hand. Properties: for two eligible inputs of any lengths, an entry exists exactly
  when the counts are 6 or more apart, with `days_current` the later year's; permuting the
  inputs of one group never changes the result; shifting all dates by the same days changes
  only the periods.
- **C2**: Eligibility and grouping (Requirement 2.1–2.2). No entry when either input is not
  `OK`, is not a lookup, is an instant, has no int `fy` (or a bool), or differs in `cik`,
  `concept_family`, fiscal period or `ytd`; none for two inputs of the same year. A `None`
  fiscal period is `"FY"` and `"fy"` is `"FY"`. Quarters: AAPL Q1 FY2023 against Q1 FY2022 is
  an entry with 98 and 91 days and no `53-week`. Oracle: Requirement 2, AAPL 10-Q cover
  periods (14 weeks to 2022-12-31, 13 weeks to 2021-12-25) by hand. Property: adding any number
  of ineligible inputs anywhere in the list never changes the result.
- **C3**: One definition (Requirement 1). For any two eligible inputs of one group, each entry's
  `days_current`, `days_prior`, `current_period`, `prior_period` and `note` equal
  `year_length_disclosure(current, prior)`'s and the entry has exactly the eight keys; the
  note's numbers are invisible to `read_stated_numbers`. Oracle: period-disclosure C3 (locked
  and graduated) and the stated-number reader's exemptions (ISO dates, counts of days,
  compound words).
- **C4**: Growth screens (Requirement 3). Rows AAPL (`revenue_current` 14262, `revenue_prior`
  12096), MSFT (105742, 104052), a row with a component missing from `periods`, a row with a
  list-valued component and a row with two `_current` keys: one entry, AAPL, `fact_ids`
  `[14262, 12096]`, 364 / 371, with C3's periods. Ids given as digit strings read the same.
  Oracle: C3's periods by hand. Properties: entries follow row order; a row has an entry
  exactly when its two counts are 6 or more apart; rows without a comparable pair never change
  the result.
- **C5**: The sentence and its append (Requirements 4–5). AAPL alone: exactly
  `Growth compares fiscal years of different lengths: AAPL 2023-10-01 to 2024-09-28 (364 days)
  against 2022-09-25 to 2023-09-30 (371 days), one is a 53-week year. Growth is on the reported
  basis, not adjusted for the different number of days.`; a quarter part has no `53-week`; two
  entries join with `; ` in the given order; `""` for none. `read_stated_numbers` of any such
  sentence is empty. Append: the sentence follows the summary after one space; a second append
  adds nothing (idempotent); a compute step's or non-screen step's `year_lengths` adds nothing;
  two screens append in step order. Oracle: Requirements 4–5, written by hand.

## Interface

New module `src/retriever/xbrl/year_length_answers.py` (pure; no I/O):

- `compared_year_lengths(inputs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]`
- `screen_year_lengths(rows: Sequence[Mapping[str, Any]], periods: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]`
- `screen_year_length_text(entries: Sequence[Mapping[str, Any]]) -> str`
- `append_year_length_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str`

Changed, not locked: `StepResult.year_lengths` (compute, cagr, screen), the screen's derived
path, prompt `_base/synthesizer/v7`, `XbrlRetrievalPipeline`.

## Notes

- **Evidence: eval cases with unequal years** (`data/eval/v2_frozen/`, key inputs' SEC
  start/end, days = end − start + 1, read 2026-11-11). Dev (628 cases): 52 compute cases
  compare two or more duration years of one concept (14 `yoy_growth`, 15 `cagr`, 15
  `difference`, 8 `trend` breakdowns); **none** spans unequal years. Validation (272): 26
  (10 `yoy_growth`, 6 `difference`, 10 `trend`); **6** span unequal years, two intents of three
  paraphrases each: AAPL FY2024 − FY2023 difference (364 / 371) and QCOM FY2024 growth
  (371 / 364). No dev or validation case screens `RevenueGrowthPct` or `NetIncomeGrowthPct`.
  Held-out sets were not read.
- **Evidence: the store** (`xbrl_db`, read-only): growth rows with unequal years by fiscal
  year, Revenue and NetIncome alike: FY2023 3 of 15 (AAPL, AMD, INTC), FY2024 5 of 32 (AAPL,
  AVGO, COST, HD, QCOM), FY2025 3 of 32 (AVGO, HD, QCOM), FY2026 1 of 9 (MU).
- **Expected eval effect.** Disclosure text only; no value, rank or citation changes. The six
  validation answers gain a sentence; dev answers are unchanged. The eval's
  `extract_stated_numbers` (`src/eval/core/answer_parsing.py`) does **not** exempt "N days" the
  way `read_stated_numbers` does: for the C3 note it returns `[364.0, 371.0]`. So the six
  answers' produced values gain the two day counts. Graders match a key against any produced
  value, and no key in those cases is near 364 or 371, so no grade should move; a hedged answer
  whose only numbers were the note's would turn REFUSED into HEDGED_MISS, but the note is only
  attached to an OK compute. Grader v8's `disclosure_spans` (grader-synthesis-error, locked,
  impl pending) covers the three screen-universe sentences only; neither this note nor the
  screen sentence matches them. Not needed now (no growth screen case, compute notes carry no
  key-like number); see review point 3.
- **Not here:** per-day restatement of a growth rate (the reported basis stays the default,
  period-disclosure R3), the bridge (done in R7.4a), a Q4 lookup (R7.6, `q4-lookup`).

## Review points (decide before implementing)

1. **Who states the compute note: the synthesizer or code?** Briefed and specified: the
   synthesizer, verbatim, in the value's statement (prompt v7), like a substitution note.
   Risk: it may omit or paraphrase it, and an omission is silent. Alternative: append the note
   in code after the verifier for compute steps too (the screen pattern). Recommendation: keep
   the synthesizer for the compute note (it belongs in the value's sentence, and there is at
   most one per step), and add a unit-tested check that logs a `decision` event when an OK
   step's `year_lengths` note is absent from the final summary; switch to code if the six
   validation cases show omissions.
2. **Quarters included.** Requirement 2 compares any same-kind durations (a 14-week quarter
   differs as a 53-week year does). Recommendation: keep; the C3 rule is length-generic and the
   note's words say "fiscal periods".
3. **Grader.** Recommendation: no grader change in this PR. A later grader version should
   either exempt "<n> days/weeks" counts in `extract_stated_numbers` (as the shared
   `read_stated_numbers` already does, so citation integrity and grading read numbers alike)
   or add the year-length sentence to `disclosure_spans`. The first is the general fix.
4. **cagr included.** A CAGR whose end points differ in length gets the note too (Requirement
   2 takes the cagr step's `[start, end]`). Recommendation: keep; it is the same comparison.
5. **Older-year screen rows.** `screen_year_lengths` reads whatever rows it is given; the
   wiring passes only the ranked rows of the screened year. Recommendation: keep it so.
