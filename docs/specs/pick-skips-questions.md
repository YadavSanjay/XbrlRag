# pick-skips-questions — a question is never the pick sentence, and "by how much did X change" also accepts the absolute change (grader version 11)

Two grader rules, shipped in one grader bump (grader 11):

- **Rule A** (requirements 1–3) amends [compound-shape](compound-shape.md) requirement 6 ("Which
  filer the answer names as highest"): a question is never the pick sentence.
- **Rule B** (requirements 4–6) adds an **absolute-change reading** to growth keys whose question
  asks for a "change" without a percent cue.

compound-shape is retired; its text and tests are not edited.

## Rule A: the defect

`picked_filer` (`src/eval/core/graders/v2.py`, used by `_grade_pick`) reads the filer a
`rank_by_ratio` answer names as the highest. The **pick sentence** is the first sentence holding a
superlative and one of the ratio's surface forms, else the last sentence holding a superlative; in
it, the candidate whose mention ends nearest before the superlative (else the first named after
it) is the pick.

A compound answer merged by `XbrlRetrievalPipeline._merge_responses` starts each part with the
sub-question it answers, echoed as a header line:

> [sq2] Among AAPL, ORCL, AVGO, which company has the highest operating profit margin for fiscal
> 2024?
> For fiscal 2024, AAPL's operating profit margin was 0.31510222870075566 pure, ORCL's was … AAPL
> therefore has the highest operating profit margin among AAPL, ORCL, and AVGO.

The header holds a superlative ("highest") and the ratio's name, and it comes first, so it is the
pick sentence. The ticker ending nearest before "highest" in it is AVGO, the last one the question
lists. The answer itself names AAPL, the winner, and is graded `INCORRECT` ("the answer does not
name AAPL as the highest"). Observed ($0 trace, `data/handover/s4sdm_compound_trace/`) on dev
`int-684520183450-08bb76ce` ("which company has the highest operating profit margin"),
`int-684520183450-10cb126b` ("which had the highest operating margin percentage") and validation
`int-684520183450-03e5d45d` ("which company had the best operating profit margin"), slice runs
`eval-67dfcc7839c2` (dev) and `eval-8ae493b49f30` (validation), 2026-11-13. With the header lines
removed, the same function picks AAPL on all three. Whether a right answer scores depends on
whether the system echoes the question, a grading error nobody would notice.

## Rule B: the defect

Dev `int-21e0eddbbb5b-0f25e25f` (compound `leader_growth`, template `lg-conv-2`) asks "Looking at
the top 3 of the covered companies by total debt in FY24, by how much did the leader's capital
expenditures change from FY23?". The answer states Apple's capital expenditures for both years
(10,959,000,000 and 9,447,000,000 USD) and "a change of -1,512,000,000 USD". The key accepts only
the growth rate (-0.138, or -13.8 in percent form), so the dependent part fails ("no produced value
matches -0.13796879277306323") and the case is `PARTIAL`. "By how much did X change" asks for an
amount as fairly as for a rate. Grading only the rate scores a correct reading as a miss.

Both right answers can be stated independently of the code: Rule A's by reading the answer,
Rule B's from the key's own two inputs (later minus earlier). A wrong grade goes unnoticed. Route:
locked spec.

## Requirement

### 1. Sentences (unchanged, restated so Rule A is exact)

`picked_filer(text, candidates, ratio_forms)` splits `text` at every **sentence end**. A sentence
end is a match of `_SENTENCE_END` (`.`, `!` or `?` followed by whitespace or the end of the text)
whose mark does not lie inside a candidate mention (a ticker or one of its names, matched as
today). A sentence runs from the end of the previous split (or the start of the text) to the end
of its own split mark. The text after the last split is the last sentence. A newline alone does
not split, but a header line ending in `?` is followed by a newline, so it ends at its `?`. None
of this changes.

### 2. Interrogative sentences

A sentence is **interrogative** when the split that ends it is a `?`. Exactly that:

- The closing mark decides, not the words. "Which filer led?" is interrogative; "Which filer led
  is not stated." is not. A sentence starting `[sqN]` is interrogative only when it ends at a `?`
  split.
- A `?` that is not a split (followed by `)`, `"`, a letter or a digit, as in "(highest?)") makes
  nothing interrogative and does not split, as today.
- When the text does not end at a split, its last sentence is never interrogative. A text that
  ends in `?` does end at a split, since a `?` at the end of the text is one.

### 3. The pick sentence skips interrogative sentences

Compound-shape requirement 6 is amended. The pick sentence is the first **non-interrogative**
sentence that contains a superlative and names the ratio; when none does, it is the last
**non-interrogative** sentence that contains a superlative. An interrogative sentence is never the
pick sentence, in either step.

- Splits are unchanged: an interrogative sentence still ends the sentence before it and begins
  the one after it at its own bounds.
- Mentions are read in the pick sentence only, as today; a filer named only in a question is never
  picked.
- When every sentence with a superlative is interrogative, there is no pick sentence:
  `picked_filer` returns `None` and `"dependent.pick"` is false.
- Superlatives, ratio forms, mention matching and the nearest-before / first-after rule are
  unchanged.

### 4. The absolute-change reading (`absolute_change_reading`)

`absolute_change_reading(question, key, growth)` returns the **absolute change** a growth key
also accepts, or `None` when the reading does not apply. It applies only when all of these hold:

1. **A growth key.** `key` is a mapping whose `definition` is `"growth"`, and `growth` is a finite
   number.
2. **The question asks for a change.** `question` contains a **change word**: `change`, `changed`
   or `changes`, case-insensitive, as a whole word (a letter or digit on either side blocks it, so
   "exchange" and "changeover" are not change words).
3. **No percent cue.** `question` contains none of these **percent cues**, case-insensitive:
   - the character `%`, anywhere;
   - any of these as a whole word: `percent`, `percentage`, `percentages`, `per cent`, `pct`,
     `rate`, `rates`, `growth`, `grow`, `grows`, `grew`, `grown`, `growing`, `cagr`.

   "Whole word" means no letter or digit on either side, so "corporate", "rated" and "pctile" are
   not cues. `year-over-year` and `yoy` are **not** cues: "the year-over-year change in X" asks for
   an amount as fairly as for a rate.
4. **Exactly one leg pair.** A **leg pair** is two entries `a`, `b` of `key["inputs"]` with the
   same `concept`, the same `unit` and the same `cik` (an absent `cik` counts as equal only to
   another absent one), with `int(a["fiscal_year"]) < int(b["fiscal_year"])`, `a["value"] != 0`,
   and `(b["value"] - a["value"]) / abs(a["value"])` within a relative 1e-9 of `growth`. The pair
   reproduces the key's own growth, so it is the growth's two legs, wherever they sit in the
   inputs. A compound `leader_growth` key lists the ranking's inputs first and then the
   dependent's, and a single growth key lists only its two legs. The candidate changes are the
   distinct values `b["value"] - a["value"]` over all leg pairs. The reading applies when there is
   exactly one; otherwise it returns `None` (no pair, or pairs that disagree).

The result is that one change, `later − earlier`, with its sign (a decline is negative). Inputs
missing `concept`, `unit`, `fiscal_year` or a numeric `value` are never part of a pair. The
function reads nothing but its arguments.

### 5. Grading with the absolute-change reading

The reading is offered to two kinds of case (v2 shape tags; H-X cases carry none and are not
covered; CAGR is not covered, as it is a multi-year rate and not a "change"):

- **(a)** A non-compound case whose shape is `compute.yoy_growth`, graded today by `grade_scalar`
  through `grade_legacy`. Arguments: `case.question`, `case.key`, `case.expected_value`.
- **(b)** A compound `leader_growth` dependent part, the part whose shape tag is
  `compute.yoy_growth`, graded today by `_grade_scalar_in_context`. Arguments: the compound's
  `question`, the compound's own `key` (the part carries none), and the part's `expected_value`.

When the key's own grade (rate or percent form, as today) is not value-correct and
`absolute_change_reading` returns a value `A`, the answer is graded again against a probe: the
same case (or part) with `expected_value = A`, no `alt_values` or `alt_readings`, the shape tag
`compute.difference` in place of `compute.yoy_growth`, and `key = {"inputs": [the leg pair]}`. A
compute.difference key is graded at the coarsest `decimals` of its inputs (the aggregate rule,
grading-precision requirement 1), so `A` is matched exactly as the same change asked as a
`compute.difference` case would be. When the probe's grade is value-correct, it is the grade, and
its reason ends with `; reading: absolute change`. Otherwise the key's grade stands. The key is
tried first and wins when both match. A case with `expect_not_meaningful` is never offered the
reading. For (b), the part's checks (`"value"`) and the compound's combination of parts are
otherwise unchanged.

### 6. Grader version

`grader_version()` returns `"11"`, with one history line in `src/eval/core/graders/honesty.py`
covering both rules ("11: a question (a sentence ending at `?`, such as an echoed `[sqN]`
sub-question) is never a compound's pick sentence; a growth key asked as a 'change' without a
percent cue also accepts the absolute change"). The retired grading-stated-aggregate C4 tests,
which pinned `"10"`, are relaxed in this spec PR to "at least 10", as grading-stated-aggregate
relaxed grader-v9-number-reading's pin.

### 7. Wiring (not locked; implementer)

1. `picked_filer` applies requirements 2 and 3, and its docstring says so.
2. `absolute_change_reading` lives in `src/eval/core/graders/v2.py`. `grade_legacy` (or
   `grade_scalar`'s caller for case (a)) and `_grade_compound` / `_grade_part` (case (b), which
   needs the compound's question and key passed down) apply requirement 5.
3. Wherever `docs/design/XbrlRag_Eval_Semantics.md` or the grading section of
   `docs/design/XbrlRag_Evaluation.md` describes the pick rule and growth grading, add one
   sentence each.
4. Re-grade ($0, `regrade_run.py`) the two s4sdm slice runs and the 13 grader-10 K6 baseline runs
   at grader 11, and report every grade that moves, per run. The dry run in Notes predicts them.

## Acceptance criteria

Fixtures are hand-written answer texts and keys over synthetic tickers (`DEVA`, `DEVB`, `DEVC`;
names "Deva Corp" and so on), with the same structure as the real answers. The pick and the
absolute change each test expects are read by hand from the answer's words and the key's inputs.
Nothing reads `data/`, a database, the network or the clock.

- **C1**: `picked_filer` skips interrogative sentences (requirements 2, 3). Point tests, each
  pick stated by hand:
  - The three real shapes. Each has a `[sq1]` ranking question and its answer, then a `[sq2]`
    question "Among DEVA, DEVC, DEVB, which company has the highest operating profit margin …?"
    (the winner DEVA listed first, a loser nearest before "highest"), then an answer naming DEVA
    as the highest. The three wordings are a "therefore has the highest …" summary; a leading
    "Deva Corp (DEVA) had the highest operating margin …" with a trailing "the lowest" for another
    filer; and a question asking for the "best" ratio with an answer saying "highest". Each →
    `DEVA`.
  - A question alone, or questions plus declarative sentences none of which has a superlative →
    `None`.
  - Fallback: a declarative "DEVA led on that measure." followed by the question "Which filer led
    the group?" (a superlative, no ratio form) → `DEVA`, the last non-interrogative superlative
    sentence.
  - A `?` that is not a split: "[sq2] Which of DEVB, DEVC had the highest operating margin?"
    then "DEVA (the highest operating margin?) ranked first." → `DEVA`. The second sentence is
    declarative, because its `?` is followed by `)`.
  - A question naming the winner nearest before the superlative, while the answer names another
    filer as the highest → the other filer. Skipping a question never favours the key.

  Oracle: requirements 2–3, read by hand. Metamorphic (`@given`): take a declarative answer body
  whose pick is stated by construction. Inserting one or more question lines before the body and
  at random sentence boundaries inside it never changes the pick. Each question line ends in `?`
  and a newline and holds a superlative, the ratio's name and candidates.
- **C2**: Rule A in grading (`grade_case` on a `compound.rank_by_ratio` case whose dependent part
  carries a `pick` block, winner DEVA at 0.315). The three real shapes, each stating DEVA's ratio:
  `"dependent.pick"` true, `"dependent.value"` true, the dependent part passes, and the case is
  `CORRECT`. An answer whose question names DEVA nearest before the superlative while the answer
  names DEVC as the highest (DEVA's ratio still stated) fails the pick. Oracle: compound-shape
  requirement 6 as amended here, by hand. Metamorphic: prefixing an answer with `[sqN]` question
  lines never changes its grade or its checks.
- **C3**: `grader_version()` is `"11"`, on an empty and on a graded scorecard; the version does not
  depend on the graded cases. Oracle: requirement 6.
- **C4**: `absolute_change_reading` (requirement 4). The real shape: the question "Looking at the
  top 3 of the covered companies by total debt in FY24, by how much did the leader's capital
  expenditures change from FY23?". The key has `definition` `"growth"`, three ranking inputs
  (`s4:debt_total`, FY2024) and DEVA's capex legs (FY2023 10,959,000,000; FY2024 9,447,000,000),
  and the growth is (9,447,000,000 − 10,959,000,000) / 10,959,000,000. The result is
  −1,512,000,000, and the same for the legs alone (a single growth key) and for the inputs in any
  order. `None` in each of these cases, each beside the applying variant: the question carries
  each percent cue in turn (inserted as a word, and `%`); the question has no change word ("How
  much did … capital expenditures move …"); `definition` is not `"growth"`; `growth` matches no
  pair; two leg pairs reproduce the growth with different changes (100 → 90 and 200 → 180, both
  −10%). "Corporate expenses changed" has a change word and no cue ("corporate" is not "rate"), so
  the reading applies; "exchange" is not a change word. Oracle: requirement 4 by hand
  (later − earlier from the stated legs). Properties (`@given`): for random legs `a ≠ 0` and `b`
  with growth `(b − a)/|a|`, a no-cue change question gives `b − a` (`approx`, rel 1e-12: float
  subtraction of the stated values); adding any percent cue anywhere in it gives `None`; and
  permuting the inputs never changes the result.
- **C5**: Rule B in grading (requirement 5). These cases use DEVA's capex legs above:
  - Case (b): a compound `leader_growth` case (dependent key −0.13797…, alt −13.797…) whose
    answer states the change as −1,512,000,000 USD. `"dependent.value"` is true, the dependent
    passes, and the grade reason names the absolute-change reading.
  - The same answer under a compound question with a percent cue ("… state the growth in its
    capital expenditures …") fails the dependent value.
  - An answer stating −13.8% passes under both questions.
  - An answer stating a wrong change (−1,400,000,000) fails.
  - Case (a): a non-compound `compute.yoy_growth` case "By how much did Deva Corp's capital
    expenditures change from fiscal 2023 to fiscal 2024?" answered "−1,512,000,000 USD" is
    `CORRECT`; asked "By what percentage did … change …" it is not.

  Oracle: requirement 5 by hand. Metamorphic (`@given` over random legs, decimals −6, and values
  on the reported step): an answer stating the absolute change is value-correct exactly when the
  question has the change word and no percent cue, and an answer stating the growth in percent
  form is value-correct under either question.

## Interface

New (stub in this PR):

```python
# src/eval/core/graders/v2.py
def absolute_change_reading(question: str, key: Mapping[str, Any] | None,
                            growth: float | None) -> float | None: ...
```

Changed in behaviour, not signature (implementer): `src/eval/core/graders/v2.py::picked_filer`,
`_grade_compound` / `_grade_part` (case (b)), `src/eval/core/graders/dispatch.py::grade_legacy`
or its scalar path (case (a)), `src/eval/core/graders/honesty.py::GRADER_VERSION` and its
history comment.

## Notes

- **Retired test touched:** `test/spec/grading-stated-aggregate/test_gsa_c4_grader_version.py`
  pins `"10"`; this PR relaxes it to `int(...) >= 10`. No compound-shape test contains a `?` or
  a change question, so none of them conflicts.
- **Why the closing mark, not `[sqN]`.** The header tag is the merge's format; a question mark is
  the language's. Keying on `?` also covers a system that restates the question without a tag,
  and it needs no knowledge of the merge. A header that is not a question (an imperative echo,
  "[sq2] Identify the one with the highest operating margin.") stays eligible.
- **Why find the legs by reproducing the growth.** A compound key's inputs are the ranking's
  followed by the dependent's (`_Keyer._key_compound`), and the ranking can include the leader's
  own input at the later year. Pairing by concept, unit, cik and fiscal year, and confirming
  against the key's own growth, finds the legs without relying on their position or on the
  builder.
- **Frozen suites the rule reaches (read-only count, 2026-11-14).** Of the non-compound
  `compute.yoy_growth` cases (dev 20, validation 10), every "change" question also says
  "percentage", so none is offered the reading. Of the compound `leader_growth` cases, 18 of 87
  dev and 2 of 3 validation ask "by how much did … change" (template `lg-conv-2`) and are offered
  it. `compute.difference` cases are not growth keys and are untouched.
- **Dry re-grade ($0, nothing stored).** `regrade_run.py --no-store --no-grounding` was run once
  on `main` (grader 10) and once with a throwaway implementation of requirements 2–6, over the 13
  K6 runs (the grader-10 ids under "G-a DONE" in v2_status) and the two s4sdm slice runs, and the
  per-case grades and checks were diffed (2026-11-14). The `main` pass reproduced every stored
  grade (0 changes in all 15 runs). With the throwaway, 4 grades move, all toward the key, none
  away:

  | Run | Case | Before → after | Check that moved | Rule |
  |---|---|---|---|---|
  | `eval-67dfcc7839c2` (dev slice) | `int-21e0eddbbb5b-0f25e25f` | PARTIAL → CORRECT | `dependent.value` | B |
  | `eval-67dfcc7839c2` | `int-684520183450-08bb76ce` | INCORRECT → CORRECT | `dependent.pick` | A |
  | `eval-67dfcc7839c2` | `int-684520183450-10cb126b` | INCORRECT → PARTIAL | `dependent.pick` | A |
  | `eval-8ae493b49f30` (val slice) | `int-684520183450-03e5d45d` | INCORRECT → CORRECT | `dependent.pick` | A |

  `10cb126b` stays PARTIAL because its ranking part declined; that is unrelated to this spec. The
  13 K6 baseline runs do not move: they carry no generated compound parts. The stored answers hold
  22 `[sqN]` header lines. 21 end in `?`, and one is an imperative ranking header ("[sq1] Among
  the covered companies, identify the 3 companies with the largest Total Debt for FY24.", on a
  `leader_growth` case with no pick).

## Review points (decide before implementing)

1. **Interrogative by closing `?` only.** Recommendation: keep. An imperative header is not
   skipped; widening to "every `[sqN]` line" would couple the grader to the merge format.
2. **No pick when only questions hold a superlative.** Recommendation: keep. An answer that never
   names a filer as the highest has not answered the dependent.
3. **The cue list.** `year-over-year` is not a cue, `rate` is (so "rate of change" asks for a
   rate), and "how much did X grow" is never offered the reading (no change word, and a cue).
   Recommendation: keep. Growth wording reads as a rate; only the neutral "change" is ambiguous.
4. **The sign.** The absolute change is signed (later − earlier); an unsigned "decreased by
   1,512,000,000" is read by the existing direction words (a down word makes the stated value
   negative). Recommendation: keep, the same reading a `compute.difference` case gets.
5. **Re-grade scope.** Recommendation: re-grade and re-store all 15 runs at grader 11. Only the
   slice runs carry generated compound parts, so the baseline runs should not move.
