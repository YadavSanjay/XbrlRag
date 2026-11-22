# verifier-derived-disclosures — the verifier judges a screen claim against what the screen derived

The faithfulness verifier checks each answer statement against its own cited evidence
(`src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py`): first the deterministic citation
checks (`citation_integrity.py`, docs/specs/citation-integrity.md), then one LLM pass over what
`_resolve_fact_evidence` shows it. For a cross-filer screen that evidence is thin. A screen row
shows `value`, `unit`, `citation`, `ticker`, `fiscal_year`, `pct_of_total` and `label`; a
statement citing the screen's step id sees `value: null, citation: null`. What the screen
derived in code is not shown: each row's `rank` and stated `period_end`, and the step's
`disclosure` (universe, members, ranked filers, every exclusion with its reason, the period ends
and whether they differ, docs/specs/screen-universe.md). The number check has the same gap:
its candidates are the cited values (`_record_candidates`), so a count the screen produced is
never one.

**Two defects follow.**

1. **The count in a rank statement.** "Walmart ranks 2nd of the 4 retailers in the fiscal 2023
   accounts payable screen" cites the four rows. "2nd" is exempt (an ordinal), "4" is not (it is
   the number of ranked members, `len(disclosure["ranked"])`), and no cited value is 4, so rule
   3 drops the statement as `number_mismatch` before any LLM sees it (F3a,
   `int-f4d823788172-00694060`). A correct peer-rank answer loses its rank sentence and gains
   "(Note: 1 statement(s) were removed ...)".
2. **Disclosure and rank claims judged without the data.** "The screen's fiscal 2023 period end
   dates differ across the four companies" (same answer) and "The screen covered four retailers
   (AMZN, WMT, COST, HD) with no exclusions, and the filers' fiscal period end dates differ"
   (`int-196485cadfb9-012dd2ec`, F7b) are true by the screen's own disclosure, and the LLM
   marks them UNSUPPORTED: "The fact evidence contains no period end dates". Rank and
   period-end claims inside a row's statement are marked PARTIALLY_SUPPORTED for the same reason.

**C13 is not this.** `int-839c491333a4-06e7fc1c` (bottom 3 by PP&E, FY2025) is PARTIAL in F3a
and F7a for the `period_ends` check. Both stored answers carry the screen's own period-end
sentence (appended after the verifier, which never judges it): "Fiscal years end on different
dates: ADBE 2025-11-28, … AMZN 2025-12-31." for the 23 filers the system ranked. The key ranks
30 filers, among them HD (period end 2026-02-01) and MU (2025-08-28), which the system excludes
(HD: no FY2025 figure in the store; MU: metric not tagged). Re-running the grader's
`disclosure_checks` on the stored F3a answer: `{"universe": True, "exclusions": True,
"period_ends": False}`, missing exactly HD 2026-02-01 and MU 2025-08-28. It is a universe
difference between key and corpus (classes C4/C7 of `failure_classes_2026-11-10.md`), not a
verifier drop. The F3a verifier did drop the synthesizer's own "Fiscal period-end dates differ"
statement (defect 2), but the appended sentence already carries the dates, so the grade does
not depend on it. Recorded here so C13 is reclassified, not "fixed".

**The general rule.** What the code derived for a cited step is evidence of that step. A
statement citing a screen, by its step id or by any of its rows, is checked against the
screen's rows **and** the screen's derived facts: the deterministic number check accepts the
screen's counts, and the LLM is shown the ranks, period ends, universe, members, ranked filers
and exclusions. This is the pattern period-year-length-answers used for `year_lengths` (a compute's derived period
lengths shown as its evidence, docs/specs/period-year-length-answers.md), and the one
`_aggregate_count_candidates` already applies to an aggregate's `n` and group size
(docs/specs/screen-aggregates.md R10).

**Why not re-append the dropped sentences in code.** The disclosure sentences are already
appended after the verifier (`append_screen_disclosures`), so the disclosure itself is never
lost; what is lost is the synthesizer's statements that restate a rank, a count or a period
end. Keeping a statement because it *looks like* a disclosure would let a wrong count or date
through, and a rank sentence is the answer to a peer question, not a disclosure. Showing the
evidence keeps the verifier strict: a statement stating 5 retailers when the screen ranked 4
still drops.

A wrong drop goes unnoticed (the answer still reads well; the grade rarely moves, see Notes),
and the right verdict is stated independently by the screen's own data. Route: locked spec.

## Requirement

### 1. The screen's derived facts (`screen_summary`)

`screen_summary(step)` takes one step as `StepResult.to_dict()` holds it. For a step whose
`op` is not `"screen"` it returns None. For a screen it returns a new dict with keys exactly
`step_id`, `universe`, `fiscal_year`, `members`, `ranked_count`, `ranked`, `rows_returned`,
`exclusions`, `period_ends`, `period_ends_differ`:

- `step_id`: `step["step_id"]`; `rows_returned`: `len(step["rows"])` (0 when absent or None).
- **With a disclosure** (`step["disclosure"]` is a mapping): `universe`, `fiscal_year`,
  `members` are the disclosure's (`.get`, None when absent); `ranked` is
  `list(disclosure["ranked"])` (`[]` when absent or None); `ranked_count` is `len(ranked)`;
  `exclusions` is a list of dict copies of `disclosure["exclusions"]` in their order (`[]` when
  absent or None); `period_ends` is a dict copy of `disclosure["period_ends"]` (`{}` when absent
  or None); `period_ends_differ` is `bool(disclosure.get("period_ends_differ"))`.
- **Without one** (an older or an error screen): `universe`, `fiscal_year` and `members` are
  None; `ranked` is the rows' `ticker`s in row order (rows with a None or missing ticker
  skipped, each `str`); `ranked_count` is `row_cap["rows_before_cap"]` when `step["row_cap"]` is
  a mapping whose `rows_before_cap` is an int (not a bool), else `rows_returned`; `exclusions` is
  a list of dict copies of `step["excluded"]` (`[]` when absent or None); `period_ends` maps each
  row's `str(ticker)` to its `period_end`, for rows where both are not None, in row order;
  `period_ends_differ` is None.

The step is never mutated. `ranked_count` is the number of members the screen ranked, before
`top_n` or a row cap: `build_screen_disclosure` takes "every ranked member's row in rank order,
before `top_n`" (screen-universe), so a bottom-3 screen over 23 ranked filers has
`ranked_count` 23 and `rows_returned` 3.

### 2. The screen's counts (`screen_count_values`)

`screen_count_values(step)` returns the counts a statement citing that screen may state: the
sorted distinct ints among `ranked_count`, `members` (when an int and not a bool),
`rows_returned` and `len(exclusions)` of `screen_summary(step)`; `[]` for a step that is not a
screen.

### 3. The number check (`citation_integrity.unmatched_numbers`)

A statement **cites a screen** when one of its `fact_ids` equals (as a string) the screen's
`step_id` or the `fact_id` of one of the screen's rows. For every screen the statement cites,
each value of `screen_count_values(step)` is a plain, exact candidate (rule step 0, matched by
non-percent numbers only); so is the `rank` (an int, not a bool) of every row the statement
cites by its `fact_id`. Nothing else changes: a stated number matching no candidate is still
unmatched; the counts of a screen the statement does not cite are not candidates; a percent is
never matched by a count. Compound answers resolve against their own sub-query's
`execution_result`, as today.

### 4. What the verifier LLM sees (`XbrlFaithfulnessVerifier`)

For each statement sent to the LLM, its `fact_evidence`:

1. **Rows:** an entry for a cited screen row carries the row's `rank` and `period_end` (keys
   `rank`, `period_end`) when the row has them, besides today's keys.
2. **The screen:** for every screen the statement cites (by step id or by a row), exactly one
   entry has the key `screen`, its value equal to `screen_summary(step)` and its `step_id`
   equal to the screen's `step_id`. When the statement cites the step id, that is the step's own
   entry; otherwise one entry `{"step_id", "op": "screen", "status", "screen"}` is added.
   A statement that cites no screen has no entry with `screen`.

Every other evidence key, the batching, the deterministic checks (with Requirement 3) and the
verdict handling are unchanged.

### 5. Wiring (not locked; implementer)

1. `_index_fact_steps` keeps `rank` and `period_end` in each row's evidence dict and the parent
   step's id, so `_resolve_fact_evidence` can add Requirement 4.2's entry once per screen
   (dedupe on the screen's `step_id`, as entries are deduped today).
2. `citation_integrity._cited_candidates` adds Requirement 3's candidates. A row record in
   `evidence_index` is `{**row, "op"}` and has no parent; resolve the parent screen from
   `execution_result["steps"]` (every screen whose `step_id` or a row `fact_id` is cited).
3. **Prompt.** New `_base/faithfulness_verifier/v2` (v1 unchanged; registry default → v2). One
   paragraph in the system prompt: a screen's evidence also carries `rank` and `period_end` per
   row and a `screen` entry (universe, members, ranked filers in rank order and their count,
   rows returned, each exclusion with its reason, period ends and whether they differ), all
   derived in code from the cited facts; a claim of a rank, a count, an exclusion or a period
   end is supported when it matches them. The user template and the response format do not
   change. Today's v1 describes `fact_evidence` as "each with value/unit/citation" and so
   invites a model to ignore the new keys.
4. **Docs.** `src/retriever/xbrl/CLAUDE.md` "Citations are checked deterministically" bullet:
   the screen counts and cited ranks are candidates; the verifier's screen evidence. The module
   docstring of the verifier (step 1).
5. **Unit tests** (`test/unit/`): `_index_fact_steps` keeps `rank`/`period_end`; the prompt
   registry resolves v2; v2's placeholders match `_build_messages`.
6. Measure on a slice (CLAUDE.md "Validate on slices"): the screen and peer cases of the dev
   split, before and after, with `compare_runs.py`; count verifier drops per answer as well
   as grades.

## Acceptance criteria

The fixtures are hand-built `StepResult.to_dict()` steps. Two carry real values from the
stored traces (read-only `audit.query_event` / `query_audit`, `xbrl_db`, 2026-11-11; the values
are public SEC XBRL facts): the retailers' fiscal 2023 accounts payable screen `ap_screen`
(AMZN 6971 84,981,000,000, WMT 205130 53,742,000,000, COST 208864 17,483,000,000, HD 211999
10,037,000,000; ranks 1-4; period ends 2023-12-31, 2023-01-31, 2023-09-03, 2024-01-28; universe
`retailers`, 4 members, no exclusions, ends differ) and the covered companies' FY2025 PP&E
bottom-3 screen `ppe_screen` (ADBE 171776 1,873,000,000, NFLX 188452 2,004,350,000, CSCO 265954
2,113,000,000; 33 members, 23 ranked, 10 exclusions). The verifier is the real one; its LLM is
a scripted adapter behind the real gateway (the only fake). Nothing reads `data/`, a database
or the clock.

- **C1**: `screen_summary` (Requirement 1). `ap_screen`: exactly `{"step_id": "ap_screen",
  "universe": "retailers", "fiscal_year": 2023, "members": 4, "ranked_count": 4, "ranked":
  ["AMZN", "WMT", "COST", "HD"], "rows_returned": 4, "exclusions": [], "period_ends": {…the four
  ends}, "period_ends_differ": True}`. `ppe_screen`: `ranked_count` 23, `rows_returned` 3,
  `members` 33, the ten exclusions in order. A screen without a disclosure: tickers from the
  rows, `ranked_count` from `row_cap.rows_before_cap` (else the row count), `exclusions` from
  `excluded`, `period_ends` from the rows, `period_ends_differ` None. A lookup, compute or
  aggregate step: None. Oracle: Requirement 1 applied by hand to the stored steps. Properties:
  for any generated disclosure, the fields equal the disclosure's, `ranked_count ==
  len(ranked)`, `rows_returned == len(rows)`; the step is never mutated; a returned value never
  aliases the step's lists or dicts.
- **C2**: `screen_count_values` (Requirement 2). `ap_screen` → `[0, 4]`; `ppe_screen` → `[3,
  10, 23, 33]`; no disclosure with a row cap of 12 over 5 rows and 1 exclusion → `[1, 5, 12]`;
  a lookup → `[]`. Oracle: Requirement 2 by hand. Property: for generated screens, equals the
  sorted set of the four counts computed in the test from the inputs.
- **C3**: The number check (Requirement 3). "Walmart ranks 2nd of the 4 retailers …" citing the
  four rows: no unmatched number; the same with "5": `["5"]` unmatched; citing `ap_screen` by
  step id: none unmatched. "ADBE had the lowest net PP&E of the 23 ranked covered companies"
  citing ADBE's row: none; "of the 24 ranked": `["24"]`. "Walmart is in position 2" citing WMT's
  row: none (its rank); citing AMZN's row only: `["2"]`. A count of a screen not cited
  (`ppe_screen`'s 23 with only `ap_screen` rows cited): unmatched. "4%" is never matched by a
  count. Oracle: Requirement 3 and the screens' counts by hand. Metamorphic: for any N, a
  statement stating N of a cited screen is unmatched exactly when N is not among that screen's
  counts, the cited rows' ranks or values; citing one more row of the same screen never adds an
  unmatched number.
- **C4**: The verifier keeps or drops (Requirements 3-4, the LLM answering SUPPORTED for all).
  The stored F3a statements on `ap_screen` plus the WMT lookup: "Walmart ranks 2nd of the 4
  retailers in the fiscal 2023 accounts payable screen, ordered from highest to lowest." is kept
  (it was dropped as `number_mismatch` on "4"); with "5 retailers" it is dropped, verdict
  `UNSUPPORTED`, `check` `number_mismatch`, `unmatched_numbers` `["5"]`, never sent to the LLM.
  Oracle: Requirement 3 by hand on the stored statements. Property: for any N, the statement
  "… of the N retailers …" (citing the four rows) is kept exactly when N is one of the
  screen's counts (0, 4) or a cited row's rank (1-4), whatever the other statements of the
  answer.
- **C5**: What the LLM sees (Requirement 4). From the prompt the fake adapter received: the row
  entries of "Amazon ranks 1st with accounts payable of 84981000000.0 USD (period ended
  2023-12-31)." carry `rank` 1 and `period_end` "2023-12-31"; the statement citing the four rows
  has one `screen` entry for `ap_screen` equal to C1's dict (period ends included, differ True);
  a statement citing `ap_screen` by id has one, on the step's own entry; a statement citing the
  WMT lookup only has none; a statement citing rows of both screens has two, one each. Oracle:
  Requirement 4 and C1's dicts by hand. Metamorphic: permuting a statement's `fact_ids`, or
  repeating one, never changes its set of `(step_id, screen)` pairs; adding a lookup's id adds no
  `screen` entry.

## Interface

New module `src/retriever/xbrl/screen_evidence.py` (pure; no I/O):

- `screen_summary(step: Mapping[str, Any]) -> dict[str, Any] | None`
- `screen_count_values(step: Mapping[str, Any]) -> list[int]`

Changed, not locked: `citation_integrity._cited_candidates`,
`XbrlFaithfulnessVerifier._index_fact_steps` / `_resolve_fact_evidence`, prompt
`_base/faithfulness_verifier/v2`.

## Notes

- **Evidence ($0, read-only).** Stored answers of the four traced `xbrlrag_full` runs, graded at
  K6/v9 (sources `eval-4cb2ec8189a9` F3a dev, `eval-5c068c793219` F7a dev, `eval-6df1981a8dc2`
  F3b validation, `eval-4a0b63db48d1` F7b validation; answers and verdicts from
  `audit.query_audit`, `xbrl_db`). An UNSUPPORTED verdict on a statement citing a screen is
  classed `count` when it is a `number_mismatch` whose every unmatched number is one of that
  screen's counts (Requirement 2), and by the LLM's reason otherwise (rank, period end,
  exclusion/universe):

  | run | screen answers | answers losing ≥1 such statement | `count` drops (answers) | LLM rank | LLM period end | LLM exclusion/universe |
  |---|---|---|---|---|---|---|
  | F3a dev | 98 | 73 | 68 (59) | 1 | 23 | 9 |
  | F7a dev | 94 | 54 | 18 (18) | 13 | 33 | 13 |
  | F3b val | 68 | 51 | 45 (42) | 1 | 21 | 4 |
  | F7b val | 70 | 31 | 17 (15) | 7 | 21 | 7 |

  So most screen answers lose at least one true statement today; the `count` drops are fully
  deterministic and fixed by Requirement 3; the LLM classes need Requirement 4 (and the prompt).
- **Which grades move** (re-grading each stored answer with the classified statements
  restored, current graders): the deterministic part moves **1** case, F7b
  `int-786b9828565b-06075105` INCORRECT → CORRECT. Restoring the LLM-class statements too (an
  upper bound: it assumes the LLM keeps them all) adds failure-split moves only, HEDGED_MISS →
  REFUSED on aggregate-group compute cases (F3a 2, F7a 7, F3b 2, F7b 6). Grades barely move
  because the screen's own sentences (appended after the verifier) already state the universe,
  exclusions and period ends, and grader v9 reads a rank from the summary. The gain is the
  answer itself: fewer true statements removed, fewer "(Note: N statement(s) were removed …)"
  caveats, `fully_answered` less often False for a right answer. Held-out sets were not read.
- **C13** (see above): reclassify to a key/corpus universe difference (HD FY2025 not in the
  store; MU PP&E not tagged), not a verifier or synthesizer defect.
- **Not here:** breakdown and aggregate steps (an aggregate's counts are already candidates; a
  breakdown's member count is not, and could follow the same rule later); lookups' period ends
  (a lookup's evidence carries no `period_end` either, the same gap for a single-filer
  statement; see review point 3).

## Review points (decide before implementing)

1. **Evidence, not exemption.** The counts become candidates of the cited screen only; the
   shared number reader (`src/numerics/stated_numbers.py`, also the grader's) is not widened to
   exempt "of N". Recommendation: keep; an exemption would let a wrong count through.
2. **Prompt v2.** Requirement 4 is locked; the prompt paragraph is wiring. Without it a small
   model may still ignore the new keys. Recommendation: ship v2 with the change and measure on
   the slice (Wiring 6); the PR is then partly `reviewed` in nature for the prompt text.
3. **Lookups' period ends.** A single-filer statement "(period ended 2023-01-31)" citing a
   lookup is PARTIALLY_SUPPORTED for the same reason (kept, so no drop). Recommendation: a
   follow-up showing the cited fact's `period_end` for lookups (it is on the candidate,
   `plan_executor.cited_period`), not in this change.
4. **Evidence size.** A covered-universe screen's summary lists up to ~33 tickers, exclusions
   and period ends, once per statement citing it (batches of 8). Recommendation: accept; the
   verifier is the small tier, and measure the token change on the slice.
5. **C13.** Recommendation: move `int-839c491333a4-06e7fc1c` to the key/universe classes (C4/C7)
   in the D3 failure-class table; no product change can make the answer state period ends of
   filers it does not rank.
