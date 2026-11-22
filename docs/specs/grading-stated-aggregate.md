# grading-stated-aggregate — a declining answer's stated median or sum is read even when it equals a listed row (grader version 10)

Follows [grading-dont-care-and-ties](grading-dont-care-and-ties.md) requirement 4 (grader 7) and
[grader-v9-number-reading](grader-v9-number-reading.md) (grader 9).

**The rule today.** For a `sum` or `median` case, when the answer is `refused` or `hedged`, every
produced value that matches one of the answer's own rows is removed before grading
(`_without_row_values`, `src/eval/core/graders/v2.py`). The aim is right: an answer that declines
and lists the members must not be credited with the aggregate because one member happens to equal
it, and for a median of an odd number of members one always does (the middle one).

**The defect.** The rule removes the value even when the answer states it *as the aggregate*. A
median of an odd count is, by definition, a member's value, so a hedged answer that states the
median correctly and also lists the ranked members loses the median. Observed on
`int-bd7ee7bd1bce-0231b50a` ("median total current assets, the semiconductor companies, fiscal
2023", key 21,244,000,000 = MU's value), 2026-11-12 slice run `eval-d789be154ba8`:

> The median total current assets across the 7 ranked semiconductor companies for fiscal 2023 was
> 21,244,000,000 USD. The median value corresponds to Micron's (MU) total current assets of
> 21,244,000,000 USD … The 7 ranked companies' total current assets for fiscal 2023 were: Intel
> (INTC) 43,269,000,000 USD, NVIDIA (NVDA) 23,073,000,000 USD, Qualcomm (QCOM) 22,464,000,000 USD,
> Micron (MU) 21,244,000,000 USD, Broadcom (AVGO) 20,847,000,000 USD, AMD 16,768,000,000 USD …

The answer was `hedged` only because the verifier marked a statement `PARTIALLY_SUPPORTED` (see
verifier-period-evidence). Every copy of 21,244,000,000 was removed, the remaining values were the
counts 7 and 8, and the case was graded `HEDGED_MISS`. The control run's answer said the same
without listing the rows and was `CORRECT`. Whether a right answer scores depends on whether it
also lists the members: a grading error nobody would notice.

**The rule this spec adds.** A value matching a listed row is still read as the aggregate when the
answer **states it as the aggregate**: a sentence that names the aggregate (its word: "median",
or a sum word), does not decline, and states exactly one number matching a listed row value, that
number. Listing the members, or naming the aggregate while declining, still never counts.

The right grade is stated independently by the key and the answer's own words; a wrong one goes
unnoticed. Route: locked spec.

## Requirement

### 1. Aggregate sentences (`aggregate_sentences`)

`aggregate_sentences(text, sub_kind)` returns, in order, the sentences of `text` that **name the
aggregate and do not decline**, as `(start, end)` spans into `text`:

- **Sentences.** `text` is split at a `.`, `!` or `?` followed by whitespace or the end of the
  text, and at a newline; a `.` between two digits (a decimal point) or inside a number such as
  `21,244,000,000.5` never splits. Spans exclude the surrounding whitespace; empty sentences are
  skipped.
- **Names the aggregate.** Case-insensitive, on word boundaries. `median`: the word `median`.
  `sum`: one of `sum`, `combined`, `aggregate`, `altogether`, `together`, `in total`, `a total
  of`, `totaled`, `totalled`, `totaling`, `totalling`, `add up to`, `adds up to`. The bare word
  `total` is **not** a sum word (it names metrics: "total current assets", "total revenue").
- **Declines.** Case-insensitive, on word boundaries: `not`, `no`, `cannot`, `can't`, `couldn't`,
  `unable`, `unavailable`, `insufficient`, `n/a`, `without`, or a word ending `n't`.
- Any other `sub_kind` → `[]`.

### 2. A declined aggregate is not matched on a listed row, unless stated as the aggregate

Grading-dont-care-and-ties requirement 4 is amended. For `sum` and `median` (v2 and H-X), when the
answer is `refused` or `hedged`, a produced value that matches a row value (the same test as
today: it would match a key equal to that row value, under the case's own rule, no alternate
readings) is removed, **except** when it is **stated as the aggregate**: some span of
`aggregate_sentences(answer.text, sub_kind)` contains exactly one stated number (as
`extract_stated_numbers` reads it, over that sentence alone) that matches any row value, and that
number equals the produced value (`==`). Such a value is kept, every copy of it. Everything else
in requirement 4 is unchanged: when no value remains the answer is `REFUSED`, an answer that
neither refuses nor hedges is graded as before, and rows and every other sub-kind are unchanged.

A kept value is then graded by `grade_scalar` as any other: right is `CORRECT` (a `hedged` answer
stating the right aggregate is right), wrong is `HEDGED_MISS` for a hedged answer as today.

### 3. Grader version

`grader_version()` returns `"10"`. The retired grader-v9-number-reading C6 tests, which pinned
`"9"`, are relaxed in this spec PR to "at least 9" (as grader-v9 relaxed grader-synthesis-error's
pin).

### 4. Wiring (not locked; implementer)

1. `_without_row_values` applies Requirement 2; `aggregate_sentences` lives with the other text
   readers in `src/eval/core/answer_parsing.py`.
2. `docs/design/XbrlRag_Eval_Semantics.md` / the grading section of
   `docs/design/XbrlRag_Evaluation.md`: one sentence on the exception.
3. Re-grade ($0, `rerun-grade`) the 2026-11-12 slice runs and the 11-10 baseline xbrlrag runs
   (F3a/F3b/F7a/F7b) at grader 10 and report every grade that moves, per system: the rule is
   system-neutral and applies to every arm's text, so every arm is re-graded.

## Acceptance criteria

Fixtures are hand-built `EvalCase` / `SystemAnswer` values. The real one is
`int-bd7ee7bd1bce-0231b50a` above (dev split; public SEC XBRL values; key 21,244,000,000, median
of the seven listed rows; rows INTC 43,269,000,000, NVDA 23,073,000,000, QCOM 22,464,000,000, MU
21,244,000,000, AVGO 20,847,000,000, AMD 16,768,000,000, TXN 15,122,000,000). Nothing reads
`data/`, a database, the network or the clock.

- **C1**: `aggregate_sentences` (Requirement 1). The C3 text (stored verbatim in the fixture):
  for `median`, exactly the sentences that contain "median" and no decline word, hand-listed in
  the test; for `sum`, exactly those containing a sum word ("The aggregate covers 7 of the 8 …"
  does), hand-listed. "The median could
  not be computed; MU reported 21,244,000,000 USD." → `[]`. "Combined revenue was $5.2 billion."
  → one span for `sum`, none for `median`. "Total revenue was $5.2 billion." → `[]` for `sum`.
  "The median is 1.5. The sum is 3." splits into two sentences (the decimal point does not split).
  A newline splits. `rank` → `[]`. Oracle: Requirement 1 by hand. Properties: spans are in order,
  disjoint, within `text`, and `text[s:e]` has no leading or trailing whitespace; adding a
  sentence without an aggregate word never adds a span; inserting `not` into a span's sentence
  removes that span.
- **C2**: the declined-aggregate rule, unchanged parts (grading-dont-care-and-ties C5 still holds).
  Median 200 of rows 100, 200, 300: a hedged answer listing only the rows ("A had 100, B had 200,
  C had 300.") is `REFUSED`; a refused one with sum key 600 is `REFUSED`; an unhedged answer
  stating 200 with the rows is `CORRECT`; a hedged answer stating "The median could not be
  determined; B had 200." is `REFUSED`. Property: for random odd-count rows, a declining answer
  that lists only its rows, with or without a declining median sentence, is never value-correct.
  Oracle: grading-dont-care-and-ties requirement 4, by hand.
- **C3**: stated as the aggregate (Requirement 2). The `int-bd7ee7bd1bce-0231b50a` answer
  (`hedged` True, the seven rows, produced values as `extract_stated_numbers` reads the text):
  `CORRECT`, `matched_value` 21,244,000,000. The same answer stating the median as 22,464,000,000
  (QCOM's) in its median sentences: `HEDGED_MISS` (kept, wrong). A hedged answer "The median is
  200. A had 100, B had 200, C had 300." with key 200: `CORRECT`. A sentence "The median is
  between 100 and 300." (two row-matching numbers) states no single value: the answer is graded as
  today. A sum case: "Combined, the three had 600." with rows 100, 200, 300 and key 600 is graded
  as today (600 matches no row); with rows 600 and 0 (a sum equal to a row), hedged, "The
  combined total was 600." → `CORRECT`. Oracle: Requirement 2 by hand. Metamorphic: appending a
  sentence that lists the rows never changes the grade of a hedged answer that states the
  aggregate; changing the answer from hedged to not hedged never changes the grade of an answer
  that states the right aggregate.
- **C4**: `grader_version()` is `"10"`. Oracle: Requirement 3.

## Interface

```python
# src/eval/core/answer_parsing.py
def aggregate_sentences(text: str, sub_kind: str) -> list[tuple[int, int]]: ...
```

Changed, not locked: `src/eval/core/graders/v2.py::_without_row_values`,
`src/eval/core/graders/honesty.py::grader_version`.

## Notes

- **Evidence ($0).** Stored slice runs `eval-a887bbc1df30` (control, CORRECT: no rows in the
  answer) and `eval-d789be154ba8` (candidate, `HEDGED_MISS`: rows listed), 2026-11-12, re-graded
  by `compare_runs.py` at grader 9; the trace (`audit.query_audit` 950f1d80dd184f55) shows the
  median statement was kept by the verifier and the answer was flagged `fully_answered = False`
  only by a `PARTIALLY_SUPPORTED` verdict on the fiscal year.
- **Why not count occurrences** (the median appears three times, MU's row once): the answer text
  the grader reads joins the summary, the statements and the appended disclosures, so a value's
  copies do not map to roles. A sentence that names the aggregate and states one row-matching
  number is the reading that survives that.
- **Why not read the system's own aggregate step**: the grader must treat every system's answer
  alike (frontier and naive arms have no steps), and stored answers keep text, rows and values
  only.
- **Interaction with verifier-period-evidence:** that change makes such answers hedged less often;
  this one makes the grade right when they are. Independent; either may land first.

## Review points (decide before implementing)

1. **Sum words exclude bare `total`.** Recommendation: keep; "total revenue" would otherwise make
   every row sentence an aggregate statement. A sum stated only as "the total was 600" is then
   still removed when it equals a row, which needs a sum equal to one member (a zero elsewhere):
   rare.
2. **Decline words are broad** (`no`, `without`). A sentence "The median, with no exclusions, was
   200" is then not read. Recommendation: accept; erring toward the rule as it is today (removal)
   keeps a declining answer from being credited.
3. **Re-grade scope.** Recommendation: re-grade the baseline runs at grader 10 and publish the
   moves; a grader change is a new measurement (honesty.py), not a correction of old numbers.
