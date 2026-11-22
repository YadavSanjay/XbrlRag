# grader-v9-number-reading — period lengths and ranks are not stated values; the judge skips a failed synthesis (grader version 9)

Follows [grader-synthesis-error](grader-synthesis-error.md) (retired; the grader is at version
8), [grading-dont-care-and-ties](grading-dont-care-and-ties.md) (its decision 6),
[period-year-length-answers](period-year-length-answers.md) and
[judge-runner-wiring](judge-runner-wiring.md).

**The defects.**

1. **A period length is read as a value.** Since period-year-length-answers, an answer that compares fiscal years of
   different lengths states them: `2023-10-01 to 2024-09-28 has 364 days and 2022-09-25 to
   2023-09-30 has 371 days`, and a growth screen appends `AAPL 2023-10-01 to 2024-09-28 (364
   days) against ...`. `extract_stated_numbers` (`src/eval/core/answer_parsing.py`) skips the
   ISO dates and the compound `53-week`, but returns 364.0 and 371.0. The system stores them
   among the answer's produced values (`src/eval/systems/xbrlrag_full.py`), where a grader can
   match them against a key, read them as a rank, or turn a decline into a `HEDGED_MISS`. A
   free-standing `52 weeks` / `53 weeks` is read the same way. The query pipeline's own reader
   (`src/numerics/stated_numbers.read_stated_numbers`, citation-integrity) already treats a
   count of days or weeks as a count, never an amount.
2. **A rank is read as a value** (grading-dont-care-and-ties decision 6). `ranked 3rd`, `#2`,
   `Rank 1:`, `ranked 2 of 32` give 3.0, 2.0, 1.0, 2.0 and 32.0. In the stored F3a dev run five
   declined sums (rows listed with `ranked 1st` ... `ranked 7th`) are `HEDGED_MISS` instead of
   `REFUSED` because of those numbers, as the v7 spec predicted. And the peer-relative reader's
   values fallback (peer-rank-grading requirement 4.3) can take *another company's* rank from
   the values: in one stored F3b validation answer the subject was not ranked at all, and the
   `2` of "COST ranked #2" matched the subject's key rank (graded `PARTIAL`, value-correct).
3. **The text judge still scores a failed synthesis** (grader-synthesis-error decision 4).
   Grader 8 grades the fixed fallback text `ERROR` / `REFUSED`, but `judge_case` and the graded
   runner skip only `answer.error`, so a text-graded case whose answer is the fallback is sent
   to the paid judge, which scores a sentence that says nothing.

A wrong grade here goes unnoticed (nothing else reads produced values), and an independent
source states the right reading: the citation-integrity exemption table (ranks, counts of
periods), peer-rank-grading's rank grammar, and the synthesizer's fixed sentences. Route:
locked spec.

## Requirement

Terms are those of v2-grading, grading-dont-care-and-ties and grader-synthesis-error. An
**integer** is `\d{1,3}(?:,\d{3})+` or `\d+` (commas are thousands separators); its value is its
digits without commas. Matching is case-insensitive. A **digit span** is the `(start, end)` of an
integer's digits in the text.

### 1. Period lengths (`period_length_spans`)

A **period-length phrase** is one or more integers joined by one of `/`, `-`, `–` (U+2013),
` or `, ` and `, ` to `, `- or `, `- and `, `- to `, then a space or `-`, then `day`, `days`,
`week` or `weeks`, not followed by a letter or digit; the first integer is not directly preceded
by a letter, a digit, `.`, `,` or `$`. Examples: `364 days`, `(371 days)`, `53 weeks`,
`52/53-week`, `52- or 53-week`, `13 or 14 weeks`, `1,092 days`. Not one: `45.5 days` (a decimal
is a measured amount), `$5 days`, `day 5`, `5 months`.

`period_length_spans(text) -> list[tuple[int, int]]` returns the digit span of every integer of
every period-length phrase, sorted, without duplicates.

### 2. Ranks (`rank_spans`)

A **rank number** is the integer of:

- (a) a **numeric ordinal**: `\d+` followed by `st`, `nd`, `rd` or `th`, neither preceded nor
  followed by a letter or digit (`3rd`, `21st`, `4th`). An ordinal is never an amount
  (citation-integrity exempts it the same way).
- (b) a **numbered rank**: the `n` of peer-rank-grading's numbered form (`_NUMBERED` in
  `src/eval/core/graders/v2.py`): not preceded by a letter or digit, one of `#`, `# `, `No.`,
  `No. `, `number `, or `rank` / `ranks` / `ranked` / `ranking` + a space + optionally one of
  `at `, `of `, `is `, `No. `, `#`, `number `; then `n`, one to three digits, not followed by a
  digit, by `.` or `,` and a digit, or by a letter. A rank word with a first-person subject
  (`is_count_phrase`, peer-rank-count-phrases: "I ranked 32 of 33") is a count, not a rank, and
  gives no rank number.
- (c) the **universe of a rank**: directly after the end of an (a) or (b) match, ` of ` or
  ` out of ` + an integer not followed by a digit, by `.` or `,` and a digit, or by a letter or
  digit (`ranked 2 of 32`, `3rd out of 15`, `#1 of 33 companies`): that integer too.

`rank_spans(text) -> list[tuple[int, int]]` returns the digit spans of every rank number,
sorted, without duplicates. A bare `2 of 32` with no rank form before it is not a rank (it can
be a count: "14 of 33 companies reported"); see decision 3.

### 3. Reading (`extract_stated_numbers`, `extract_numbers`)

An **exempt number** is an amount candidate of the reader (`_scan`) whose integer digits span
is in `period_length_spans(text)` or `rank_spans(text)` and that is **bare**: no currency, no
decimal part, no percent, no scale word or letter, and not a parenthesised negative (an opening
and a closing parenthesis). `extract_stated_numbers` and `extract_numbers` never return an exempt
number. Every other number reads exactly as before: years, ISO dates (`2024-09-28`), written-date
parts, form names and compounds (`53-week`) stay skipped, and amounts stay amounts
(`$3 billion`, `45.5 days`, `3.0%`).

### 4. Stored values (`unexempt_values`)

Stored runs keep `numeric_values` as produced by the old reader. `unexempt_values(text, values)
-> list[float]`: let the **exempt** numbers be the values of the integers at
`period_length_spans(text) ∪ rank_spans(text)` and the **stated** numbers
`[n.value for n in extract_stated_numbers(text)]` (requirement 3's reading). For each distinct
value v of `values`, with n_P its copies in `values` and n_A its copies among the stated
numbers: when some exempt number equals |v|, the earliest `n_P - n_A` copies (if positive) are
dropped. Every other value is kept, in order. Equality is exact. It has the shape of
`graded_values` (grading-dont-care-and-ties requirement 7) and `undisclosed_values`
(grader-synthesis-error requirement 4): only removes, never a copy the text also states as an
amount, idempotent. |v| because the old reader signed a number a down word governed ("fell to
#3").

`graded_answer` (and so `grade_v2` and `grade_legacy`) reads `numeric_values` as
`undisclosed_values(text, graded_values(text, unexempt_values(text, numeric_values)))`. Rows and
`text` are unchanged. The peer-relative reader's values fallback reads these values; its stated
ranks (`stated_ranks`) are read from the text as before, so a rank the answer attributes to the
subject still decides.

### 5. The judge skips a failed synthesis (`judge_skip_reason`)

`judge_skip_reason(case, answer) -> str | None` (`src/eval/core/judge_wiring.py`), checked in
this order:

1. `"not_text_graded"` when `is_text_graded(case)` is False;
2. `"answer_error"` when `answer.error` is set;
3. `"synthesis_error"` when `synthesis_failure(answer.text)` is `"error"`;
4. `"synthesis_refusal"` when it is `"refusal"`;
5. otherwise `None`: the case is judged.

`judge_case(judge, case, answer)` returns `None` and makes no call whenever
`judge_skip_reason` is not `None`. The graded runner (`run_graded`, `run_graded_many`) makes no
judge call for such a case, charges it nothing (`judge_cost_usd` 0.0 for the case), leaves its
`judge_scores` `None` (not judged; it is not a judge_error), and with a judge records the reason
of every skipped text-graded case answered in the run in
`report.config["judge_skipped"]`: `{case_id: reason}` for reasons 2-4, keys sorted, `{}` when
none. `"not_text_graded"` cases are not listed. `judge_summary`'s `n_judged` counts no skipped
case.

### 6. Grader version

These rules move grades, so `grader_version()` becomes `"9"`, and the history in
`src/eval/core/graders/honesty.py` gains:
`9 — counts of days or weeks and rank numbers (ordinals, #N, rank N, "of M") are never
produced values.` (Requirement 5 moves no grade; it is recorded with the judge, not the grader.)

### 7. Retired version pin

grader-synthesis-error C6 pins `"8"` exactly. It is changed in this spec PR to "at least 8"
(with a comment naming this change); this spec's C6 pins `"9"`.

## Acceptance criteria

Texts are hand-written; cases and answers are hand-built in the shapes the v2 builder emits
(fictional tickers `DEVA` ... `DEVZ`, round amounts) with a citation, so only the rule under
test decides. The note text is `year_length_disclosure`'s (period-disclosure C3, AAPL FY2024
against FY2023), and the screen part is period-year-length-answers C5's. No test reads `data/`,
a database, the network or the clock; nothing in `src` is faked (the judge is faked at its
provider adapter, the system under test at its answers, as judge-runner-wiring does).

- **C1**: period lengths (requirements 1 and 3). The note `The compared fiscal periods differ in
  length (one is a 53-week year): 2023-10-01 to 2024-09-28 has 364 days and 2022-09-25 to
  2023-09-30 has 371 days; the change is on the reported basis, not adjusted for the different
  number of days.` reads no number; with `Revenue grew 2.02% to $391.0 billion.` before it,
  exactly `[2.02, 391.0e9]`. The screen part `(364 days) against ... (371 days)` reads none;
  `fiscal 2024 had 53 weeks`, `a 52- or 53-week fiscal year`, `52/53 weeks`, `13 or 14 weeks`,
  `1,092 days` read none; `45.5 days` reads 45.5; `5 months` reads 5; `$5 days` reads 5.
  `period_length_spans` gives the exact digit spans (the note's `53` of `53-week` included).
  Properties: for any integer N, ISO dates
  D1, D2 and unit in {day(s), week(s)}, `"D1 to D2 has N <unit>"` reads nothing and
  `"Revenue was $X million. " + that` reads exactly X·1e6; inserting a period-length phrase into
  any text leaves its other numbers unchanged. Oracle: requirement 1, the period-disclosure
  note, the citation-integrity count exemption.
- **C2**: ranks (requirements 2 and 3). `DEVA ranked 3rd` reads none; `DEVB is #2`, `No. 4`,
  `Rank 1: DEVA`, `ranked 2 of 32`, `ranked 3rd out of 15 companies`, `#1 of 33` read none;
  `I ranked 32 of 33 companies` reads `[32.0, 33.0]` (a count phrase); `14 of 33 companies
  reported` reads `[14.0, 33.0]`; `DEVA ranked 3rd with revenue of $3 billion` reads
  `[3.0e9]`; `ranked 2.5 points` and `a margin of 2.5%` keep their amounts. `rank_spans` gives the exact digit
  spans. Properties: for any rank n (1-999) and universe m, every form (a)-(c) around a
  sentence with an amount reads only the amount; a numeric ordinal never reads. Oracle:
  requirement 2, peer-rank-grading's rank grammar, citation-integrity's ordinal and rank
  exemptions.
- **C3**: stored values (requirement 4). For the note text, `[364.0, 371.0]` → `[]`; for
  `DEVA ranked 3rd; it has 3 stores`, `[3.0, 3.0]` → `[3.0]`; `[-3.0]` for `fell to #3` → `[]`;
  order kept; values not exempt kept. Properties: a sub-multiset of `values` in order;
  idempotent; the identity for a text with no exempt number. Oracle: requirement 4, hand-read.
- **C4**: grades (requirement 4). A hedged sum that lists rows `ranked 1st` ... `ranked 4th`
  with no aggregate is `REFUSED` (not `HEDGED_MISS`); a lookup with key 371 answered `$364`
  plus the note (values 364, 364, 371 as stored) is `INCORRECT`, never credited by the day
  count; a peer answer where the subject is not ranked and another company is `#2` (key rank 2), with
  values as the old reader stored them, is not credited (`INCORRECT` or `REFUSED`, never
  `CORRECT`/`PARTIAL`, never value-correct); a peer answer stating `DEVS ranked 3rd` is still
  `CORRECT`. Metamorphic: appending the note (and its 364/371 to `numeric_values`, as the old
  reader stored them) to a graded answer leaves its grade, value-correct and confident-wrong
  unchanged. Oracle: requirements 2-4 and peer-rank-grading, hand-ranked.
- **C5**: the judge skips a failed synthesis (requirement 5). `judge_skip_reason` gives each of
  the five outcomes in order of precedence. `judge_case` with a fallback (E or R) answer returns
  `None` and the fake provider records no call. `run_graded` over three text cases (a plain
  answer, E, R) with a fake judge: one judge call; `judge_scores` set on the first only;
  `config["judge_skipped"] == {E-case: "synthesis_error", R-case: "synthesis_refusal"}`;
  `config["judge_cost_usd"]` is one call's cost from pricing.yaml; `judge_summary` `n_judged`
  is 1. Property: for any mix of plain, E, R and error answers, the number of judge calls equals
  the number of plain answers, and `judge_skipped` lists exactly the others. Oracle:
  requirement 5, grader-synthesis-error's fixed sentences, pricing.yaml.
- **C6**: `grader_version()` is `"9"` on every scorecard (requirement 6). Oracle: identity.

## Interface

New (stubs in this PR):

```python
# src/eval/core/answer_parsing.py
def period_length_spans(text: str) -> list[tuple[int, int]]: ...
def rank_spans(text: str) -> list[tuple[int, int]]: ...
def unexempt_values(text: str, values: typing.Sequence[float]) -> list[float]: ...

# src/eval/core/judge_wiring.py
def judge_skip_reason(case: EvalCase, answer: SystemAnswer) -> str | None: ...
```

Changed in behaviour, not signature (implementer): `_scan` / `extract_stated_numbers` /
`extract_numbers` (requirement 3), `graded_answer` (requirement 4), `judge_case`,
`_GradedRunner._after_grade` and `finish_graded` (requirement 5; a `CaseResult.judge_skipped`
field kept in the checkpoint payload so a resumed run's `judge_skipped` is exact, like
`judge_error`), `GRADER_VERSION` and its history (requirement 6). No pipeline or system code
changes; the systems pick up requirement 3 through `extract_stated_numbers`.

## Notes

- **Oracles.** The period-length and rank exemptions follow `read_stated_numbers`'
  (citation-integrity) count and rank exemptions and peer-rank-grading's numbered-rank grammar,
  restated here; the note and screen texts are copied from `year_length_disclosure` and
  period-year-length-answers C5. Expected values and grades are derived by hand.
- **Dates.** ISO dates and written-date parts were already never values (arm-neutral-grading,
  grading-dont-care-and-ties requirement 6); C1 checks them inside the note, with the day
  counts, so the test fails before the change.
- **Measured on a discarded prototype** ($0, offline, nothing stored): `scripts/eval/regrade_run.py
  --no-store --no-grounding --out <scratch>` over the 13 stored v8 reports
  (`data/eval/regrade/2026-11-11_v8/`) against today's frozen suites. Today's grader reproduces
  every stored grade (0 changes on all 13); the prototype's moves are below. Only dev answers
  were read for cause; validation is reported in aggregate (one mechanism named), held-out not
  touched.
- **Re-grade.** After the implementation merges, every stored baseline run is re-graded at $0
  (`eval-regrade`), with before/after counts per run in the implementation PR.
- **Retired grader specs stay green** except requirement 7's pin. The spec author ran
  `pytest test/spec test/unit -n auto` against a discarded prototype of requirements 1-6
  (outside the repository): this spec and every retired grader spec pass. The only other
  failures are unrelated: the active `q4-lookup` stubs and the local capex-strip-qualifiers
  fixture errors (both the same on `main`), and five live-database unit tests that failed in
  the full parallel run and pass when run alone.
- **Bounds.** Three regexes and one pass over the text; pure and deterministic.

### Measured movement (prototype dry re-grade of the stored v8 runs)

| Run (arm, split) | Grade moves | Value acc. | Conf.-wrong | Silent-wrong |
|---|---|---|---|---|
| F3a `eval-4cb2ec8189a9` (XbrlRag OpenAI, dev) | 5 `HEDGED_MISS` → `REFUSED` (declined sums listing `ranked 1st` ... rows) | 0 | 0 | 0 |
| F3b `eval-6df1981a8dc2` (XbrlRag OpenAI, validation; aggregate) | 2 `HEDGED_MISS` → `REFUSED`; 1 `PARTIAL` → `INCORRECT` (peer case credited by another company's `#2`) | −1 | 0 | 0 |
| F7a `eval-5c068c793219`, F7b `eval-4a0b63db48d1` (XbrlRag DeepSeek) | 0 | 0 | 0 | 0 |
| F1a, F1b, F2, F4, F6a, F6b, F6c, A7, A8 (every baseline arm) | 0 | 0 | 0 | 0 |

No stored answer carries a period-length phrase yet (period-year-length-answers merged after these runs), so
requirement 1 moves nothing today; it guards the next runs. Requirement 5 moves nothing either:
no stored run was judged (`judge_version` is None on all 13), and F7a/F7b carry the fallback on
22 and 19 answers that a judged run would have sent to the judge.

### Decisions for the maintainer (with recommendations)

1. **Every count of days or weeks is a period length, wherever it stands.** The alternative
   reads it as a period length only next to a date range (`D1 to D2 has N days`, `(N days)`),
   which misses "fiscal 2024 had 53 weeks" and "a 52- or 53-week year". The cost: a metric
   stated in days (days sales outstanding "45 days") would not be read. No eval intent is
   measured in days, and the pipeline's own citation check already exempts "N days" (so it could
   never verify such a statement). A decimal ("45.5 days") is still read. Recommended: as
   specified; revisit with a unit-aware rule if a days metric enters the suite.
2. **Months, quarters and years are left out.** `read_stated_numbers` also exempts "12
   months", "4 quarters", "5 years" (a CAGR "over 3 years" reads 3 today). Recommended: leave
   them for now (asked scope: days and weeks); fold them in at the next grader change if a
   stored answer shows a grade moved by them.
3. **A bare "N of M" is not a rank.** Only after a rank form (`ranked 2 of 32`, `#2 of 32`,
   `3rd of 32`). A bare "14 of 33" is how an answer states a count ("14 of 33 companies
   reported"), and a count aggregate's key is such a number. Recommended: keep.
4. **Numbered-list markers stay values.** A line "1. DEVA ..." still reads 1.0. They are not
   rank expressions in peer-rank-grading's grammar, and no stored grade was traced to one.
   Recommended: leave out; same follow-up as decision 2.
5. **The universe count of a rank ("of 32") is dropped too.** It states the universe, never an
   answer value, like the disclosure sentence's count in grader 8. Recommended: keep.
6. **The F3b peer case loses its `PARTIAL` (value-correct −1 on validation).** That credit came
   from another company's rank. Recommended: accept; it was never earned.
7. **Skipped judge cases are recorded per run (`config["judge_skipped"]`), not as judge errors.**
   They are not judge failures and must not lower the judge's error count or its calibration
   view; the grade (`ERROR` / `REFUSED`) already says what happened. Recommended: as
   specified.
