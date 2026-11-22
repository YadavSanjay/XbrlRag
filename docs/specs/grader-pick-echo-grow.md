# grader-pick-echo-grow — an echoed `[sqN]` line is never the pick sentence, and "how much did X grow" also accepts the absolute change (grader version 12)

Two grader rules, shipped in one grader bump (grader 12). Both amend
[pick-skips-questions](pick-skips-questions.md) (grader 11), which is implemented and retired;
its text is not edited. Its tests that this change contradicts are updated in this spec PR (see
Notes).

- **Rule A** (requirements 1–2) amends pick-skips-questions requirement 3: a sub-query echo line
  is never the pick sentence, whatever its final punctuation.
- **Rule B** (requirements 3–4) amends pick-skips-questions requirement 4: a growth key asked
  "how much did X grow / increase / decrease / rise / fall / decline" without a percent cue also
  accepts the absolute change, exactly as "change" does.

## Rule A: the defect

A compound answer merged by `XbrlRetrievalPipeline._merge_responses` starts each part with the
sub-question it answers, as a header line `[<id>] <sub-question>` (`[sq1]`, `[sq2]`, …). Grader 11
skips a header that is a question (it ends at a `?`). A header that is an **imperative** echo is
not a question and stays eligible. Observed in a $0 trace of dev compound run
`eval-18b83b48fb4d`:

> [sq2] Among AMZN, HD, WMT, identify the company with the highest working capital ratio ….
> The Home Depot (HD) had the highest working capital ratio …

The header holds a superlative and the ratio's name and comes first, so it is the pick sentence,
and the ticker ending nearest before "highest" in it is WMT, the last one listed. The answer
itself names HD. A right answer is graded `INCORRECT` because the system echoed its sub-question
in the imperative mood, a grading error nobody would notice.

## Rule B: the defect

In the same run, a compound `leader_growth` case asks "… how much did the leader's … grow …".
The answer states the correct absolute change (+5,392 million USD). Grader 11 offers the
absolute-change reading only to a question holding a change word (`change`, `changed`,
`changes`), and it lists `grow`, `grows`, `grew`, `grown` and `growing` as percent cues, so
the reading is never offered and a correct number is graded as a miss (silent-wrong in the
scorecard). The sibling "by how much did … change" passes. "How much did X grow" asks for an
amount as plainly as "by how much did X change" does.

Both right answers can be stated independently of the code: Rule A's by reading the answer's
own declarative words, Rule B's from the key's two legs (later minus earlier). A wrong grade goes
unnoticed. Route: locked spec.

## Requirement

### 1. Sub-query echo lines (`without_subquery_echoes`)

`without_subquery_echoes(text)` returns `text` with every **sub-query echo line** deleted.

- `text` is read as **lines**: it is cut after every `\n`. Each line keeps the `\n` that ends it;
  the last line has none when `text` does not end in `\n`. (`\r` is an ordinary character.)
- A line is a **sub-query echo line** when it begins, after optional spaces and tabs, with the
  label `[sq<N>]`: the characters `[sq` (lowercase, as the merge writes them), one or more ASCII
  digits, and `]`. What follows the label is irrelevant: a question, an imperative, a line with
  no final punctuation or an empty rest are all echo lines.
- Not echo lines: a label anywhere but the line's start (`See [sq2] above.`), `[sq]` (no
  digits), `[SQ2]`, `[q2]`, `[sq2a]`, `sq2]`.
- Each echo line is deleted with the `\n` that ends it (when it has one). Every other character
  is kept, in order. So the result is the concatenation of the non-echo lines, each as written.
- The function reads nothing but its argument. It is idempotent, and `""` gives `""`.

### 2. The pick sentence ignores echo lines

`picked_filer(text, candidates, ratio_forms)` returns what grader 11's rule (pick-skips-questions
requirements 1–3, compound-shape requirement 6) returns for `without_subquery_echoes(text)`. That
is, echo lines are removed **before** sentences are split; the sentence, interrogative,
superlative, ratio-form, mention and nearest-before / first-after rules are applied to what
remains, unchanged.

- A filer named only in an echo line is never picked.
- When the only sentences with a superlative were in echo lines (or are questions), there is no
  pick sentence: `picked_filer` returns `None` and `"dependent.pick"` is false.
- Only the pick changes. Produced values, the value check, ranks, disclosures and every other
  reading of the answer text are unchanged (a separate change, if ever needed).

### 3. Amount wording (`asks_for_amount`)

`asks_for_amount(question)` is whether a question asks for an amount: it holds **amount wording**
and no **percent cue**. All matching is case-insensitive, and a **whole word** has no ASCII letter
or digit on either side; a multi-word phrase matches with any run of whitespace between its words.

1. **Amount wording** is either of:
   - a **change word** (grader 11, unchanged): `change`, `changed`, `changes`, as a whole word;
   - a **how-much movement**: the phrase `how much` as whole words, followed anywhere later in the
     question by a **movement verb** as a whole word:
     `grow`, `grows`, `grew`, `grown`, `growing`,
     `increase`, `increases`, `increased`, `increasing`,
     `decrease`, `decreases`, `decreased`, `decreasing`,
     `rise`, `rises`, `rose`, `risen`, `rising`,
     `fall`, `falls`, `fell`, `fallen`, `falling`,
     `decline`, `declines`, `declined`, `declining`.
     "By how much did X grow" contains "how much"; so does "State how much X grew". A movement
     verb **before** "how much" ("Capex grew; by how much?") is not a how-much movement, and a
     movement verb without "how much" ("Did X grow?", "What was the increase in X?") is not
     amount wording.
2. **Percent cues** (grader 11's list less the five verb forms of *grow*):
   - the character `%`, anywhere;
   - any of these as a whole word: `percent`, `percentage`, `percentages`, `per cent`, `pct`,
     `rate`, `rates`, `growth`, `cagr`.

   So "by what percent / percentage", "growth rate", "rate of change", "in %" are cues.
   `grow`, `grows`, `grew`, `grown`, `growing` are **no longer cues**: a verb of movement names the
   direction, not the unit, exactly as `increase` never was a cue. `year-over-year` and `yoy` are
   not cues (unchanged). "corporate", "rated", "pctile", "overgrowth" hold no cue word.
3. **`growth` the noun stays a cue.** In filings and in the suites' templates "growth" names the
   rate ("revenue growth of 8%", "state the growth in its capital expenditures"), and it is the
   word "growth rate" is built on. A question asking for "the growth in X" asks for a rate, and
   "by how much did X's growth change" asks about a rate. Only the verb forms are freed, because
   they are what "how much did X grow" uses.

### 4. The absolute-change reading under amount wording

pick-skips-questions requirement 4 is amended: its conditions 2 ("the question asks for a
change") and 3 ("no percent cue") are replaced by **`asks_for_amount(question)` is true**.
Conditions 1 (a growth key with a finite growth) and 4 (exactly one leg pair reproducing the
growth) are unchanged, and so is the result: the signed change `later − earlier`. The movement
verb's direction never flips the sign: "how much did X grow" over a decline reads a negative
change, and "how much did X fall" over a rise a positive one (an answer's own direction words are
read as today, grading-precision).

pick-skips-questions requirement 5 (the probe: grading the answer against the absolute change as
a `compute.difference` with the leg pair as its key, tried only after the key's own grade is not
value-correct, reason ending `; reading: absolute change`) is unchanged and applies to every
question requirement 4 now admits, for both case (a) (a non-compound `compute.yoy_growth` case)
and case (b) (a compound `leader_growth` dependent part).

### 5. Grader version

`grader_version()` returns `"12"`, with one history line in `src/eval/core/graders/honesty.py`:
"12: a `[sqN]` sub-query echo line is never a compound's pick sentence, whatever its final
punctuation; 'how much did X grow / increase / decrease / rise / fall / decline' without a
percent cue also accepts the absolute change (grow verbs are no longer percent cues)". The version
does not depend on the graded cases.

### 6. Wiring (not locked; implementer)

1. `without_subquery_echoes` and `asks_for_amount` live in `src/eval/core/graders/v2.py`.
2. `picked_filer` applies requirement 2 (first statement: `text = without_subquery_echoes(text)`),
   and its docstring says so.
3. `_change_legs` (behind `absolute_change_reading` and `_with_absolute_change`) replaces its
   change-word / percent-cue test with `asks_for_amount(question)`. `PERCENT_CUE_WORDS` loses the
   five grow verbs; the movement verbs and the how-much phrase are new module constants.
4. `GRADER_VERSION = "12"` and its history line (requirement 5).
5. One sentence each wherever `docs/design/XbrlRag_Eval_Semantics.md` or the grading section of
   `docs/design/XbrlRag_Evaluation.md` describes the pick rule and the absolute-change reading.
6. Re-grade ($0, `regrade_run.py`) `eval-18b83b48fb4d` and the runs pick-skips-questions re-graded,
   and report every grade that moves, per run.

## Acceptance criteria

Fixtures are hand-written answer texts and keys over synthetic tickers (`DEVA`, `DEVB`, `DEVC`;
names "Deva Corp" and so on), with the structure of the real answers. Every expected pick, string
and change is read by hand from the fixture's words and the key's inputs. Nothing reads `data/`,
a database, the network or the clock.

- **C1**: `without_subquery_echoes` (requirement 1). Point: a text of two `[sqN]` echo lines (one
  imperative ending `.`, one with no final punctuation), each followed by its answer lines →
  exactly the answer lines; indented labels and multi-digit `N` are echo lines; a text without
  echo lines is returned unchanged; `See [sq2] above.`, `[sq]`, `[SQ2]`, `[q2]`, `[sq2a]` lines
  are kept; an echo as the last line without `\n` leaves the preceding `\n`. Oracle: requirement 1
  by hand. Property (`@given`): for random non-echo lines each ending in `\n`, inserting echo lines
  (random label number, random rest, with or without final punctuation) at random line positions
  gives back exactly the original text; the result is idempotent.
- **C2**: `picked_filer` ignores echo lines (requirement 2). Point: the real shape — `[sq1]` an
  imperative ranking echo ("identify the 3 companies with the largest total debt for FY24."), its
  answer, then `[sq2] Among DEVB, DEVA, DEVC, identify the company with the highest working
  capital ratio for fiscal 2024.` (a loser nearest before "highest"), then an answer "Devb Corp
  (DEVB) had the highest working capital ratio …" → `DEVB`, for the echo ending in `.`, in `…`,
  in `?` and in nothing. An echo naming the winner nearest before the superlative while the
  answer names another filer as the highest → the other filer (skipping an echo never favours the
  key). Echo lines as the only superlative sentences → `None`. Oracle: requirement 2 by hand.
  Metamorphic (`@given`): a declarative body whose pick is stated by construction; inserting echo
  lines (each holding a superlative, the ratio's name and candidates, a loser nearest before the
  superlative, any final punctuation) at the start and at random line boundaries never changes the
  pick.
- **C3**: Rule A in grading (`grade_case` on a `compound.rank_by_ratio` case whose dependent part
  carries a `pick` block, winner DEVB at 1.35). The real shape, stating DEVB's ratio:
  `"dependent.pick"` true, `"dependent.value"` true, the case `CORRECT`. An answer whose echo
  names DEVB nearest before the superlative while the answer names DEVC as the highest fails the
  pick. Oracle: requirement 2, by hand. Metamorphic: prefixing an answer with imperative echo lines
  never changes its grade or its checks.
- **C4**: `asks_for_amount` (requirement 3). Point: each how-much movement form ("how much did X
  grow", "by how much did X increase / decrease / rise / fall / decline", "how much has X grown /
  risen / fallen", "State how much X grew", upper case, a newline between "how" and "much") and the
  change words → true; each percent cue added to a true question → false; "the growth in X" (noun),
  "By how much did X's growth change", "How fast did X grow?", "Did X grow?", "What was the increase
  in X?", "How much revenue did X report?", "How much enterprise value …" (no whole-word "rise"),
  "Capex grew; by how much?" (verb before) → false; "corporate" and "overgrowth" are not cues.
  Oracle: requirement 3 by hand. Properties (`@given`): for random subjects from a cue-free
  vocabulary and any movement verb, "how much did <subject> <verb> …?" is true; inserting any
  percent cue at any word boundary makes it false; changing letter case never changes the result.
- **C5**: Rule B in reading and grading (requirement 4). Legs: DEVA's capex FY2023
  9,447,000,000 and FY2024 10,959,000,000 (decimals −6): change +1,512,000,000, growth
  1,512 / 9,447 (16.0%). Point: `absolute_change_reading` on "… how much did the leader's capital
  expenditures grow from FY23?" over the compound key → +1,512,000,000, and over the legs reversed
  (a decline) → −1,512,000,000 under the same "grow" question and under "fall"; each of
  increase / decrease / rise / fall / decline gives the same signed change; a percent cue or the
  noun "growth" gives `None`. Grading: case (b) a compound `leader_growth` case answered "grew by
  1,512,000,000 USD" → `"dependent.value"` true, `CORRECT`, reason naming the absolute-change
  reading; under "… what was the growth in the leader's capital expenditures from FY23?" it fails
  the dependent value; an answer stating 16.0% passes under both; a wrong change (1,400,000,000)
  fails. Case (a): a `compute.yoy_growth` case "How much did Deva Corp's capital expenditures grow
  from fiscal 2023 to fiscal 2024?" answered "1,512,000,000 USD" is `CORRECT`; "By what percent did
  … grow …" is not. Oracle: requirement 4 by hand (later − earlier of the stated legs).
  Metamorphic (`@given` over random legs on the reported step, a movement verb and compound or
  not): an answer stating the absolute change is value-correct exactly when the question has amount
  wording and no cue, and the growth in percent form is value-correct under every question.
- **C6**: `grader_version()` is `"12"`, on an empty and on a graded scorecard; the version does not
  depend on the graded cases. Oracle: requirement 5.

## Interface

New (stubs in this PR):

```python
# src/eval/core/graders/v2.py
def without_subquery_echoes(text: str) -> str: ...
def asks_for_amount(question: str) -> bool: ...
```

Changed in behaviour, not signature (implementer): `src/eval/core/graders/v2.py::picked_filer`,
`absolute_change_reading` / `_change_legs` (and so `_with_absolute_change`,
`grade_growth_scalar`), `PERCENT_CUE_WORDS`, `src/eval/core/graders/honesty.py::GRADER_VERSION` and
its history comment.

## Notes

- **Retired tests updated in this PR** (they pin grader 11's behaviour that this spec changes, and
  retired tests are graduated, so CI would fail on the implementation without the update):
  - `test/spec/pick-skips-questions/test_psq_c3_grader_version.py`: the `"11"` pins (in
    `test_grader_version_is_11` and `test_version_does_not_depend_on_the_graded_cases`) are relaxed
    to `int(...) >= 11`, as pick-skips-questions relaxed grading-stated-aggregate's pin.
  - `test/spec/pick-skips-questions/test_psq_c4_absolute_change.py`: `CUES` drops `grow`, `grows`,
    `grew`, `grown`, `growing` (requirement 3.2), and "How much did Deva Corp's capital
    expenditures grow from fiscal 2023?" moves from the no-reading list to the reading list
    (requirement 4). Nothing else in that file changes.
  No other spec or unit test pins grader 11, holds a `[sqN]` line outside pick-skips-questions, or
  asks a how-much movement question of a growth key. compound-shape's tests hold no `[sqN]` line.
- **Why the `[sqN]` label, now.** pick-skips-questions chose the closing `?` so the grader would not
  depend on the merge format (its review point 1). The imperative echo shows a question mark is not
  enough: the merge echoes whatever the decomposer wrote. The label is the merge's own marker that a
  line is the question, not the answer, so it is the precise test; the `?` rule stays for a system
  that restates a question without a label. Lowercase `sq` and digits are what `QueryDecomposer`
  ids and `_merge_responses` produce (`f"[{sq.id}] {resp.query}\n…"`). A decomposer id that is not
  `sq<N>` is not covered; none is produced today.
- **Why delete whole lines.** An echo without final punctuation would otherwise run into the
  answer's first sentence (a newline does not split, pick-skips-questions requirement 1), so
  removing sentences would not remove it. A sub-question containing a newline would leave its
  later lines; the decomposer emits one-line queries.
- **Why "how much" + verb, and only after it.** The maintainer named "how much did X grow" and "by
  how much did X grow / increase / decrease / rise / fall". `decline` is added as the same wording
  as `decrease` (the `DOWN_WORDS` lexicon lists it). The verb must follow "how much" so the phrase
  is the question's ask; "X grew; by how much?" is rare and is left out rather than widening the
  rule to any co-occurrence. Other amount wordings ("what was the increase in X", "how much higher")
  are not covered.
- **Frozen suites.** This spec reads no suite. The implementer's re-grade (requirement 6.6) reports
  what moves.

## Review points (decide before implementing)

1. **Lowercase `[sq<N>]` only.** Recommendation: keep; it is exactly what the merge writes.
2. **`growth` the noun stays a cue.** Recommendation: keep (requirement 3.3).
3. **`decline` added to the movement verbs.** Recommendation: keep; drop it if the maintainer wants
   the list exactly as named.
4. **Verb must follow "how much".** Recommendation: keep.
