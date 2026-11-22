# grader-synthesis-error — a failed synthesis grades alike for every kind; no rank or value from the universe sentence (grader version 8)

Follows [grading-dont-care-and-ties](grading-dont-care-and-ties.md) (retired; the grader is at
version 7), [peer-rank-grading](peer-rank-grading.md) and [screen-universe](screen-universe.md).
Found in the lost-5 diagnosis of 2026-11-11 (dev `int-ead777fd7df3-00cd2cd1`, META capital
expenditures FY2022 among the covered companies, F7a run `eval-d33940dc59b5`).

**The defects.**

1. **A failed synthesis is graded by kind.** When the synthesizer's reply cannot be used, the
   pipeline does not raise: `XbrlRagSynthesizer._degraded_response`
   (`src/retriever/xbrl/llm_xbrl_synthesizer.py`) returns a fixed fallback summary, sets
   `metadata["synthesis_error"]` and `fully_answered: false`, and the pipeline then appends the
   screen disclosure sentences. The eval system reads that as a hedge (`hedged=True`, and
   `refused=False` whenever a step returned values), and each grader treats the hedge its own
   way: in the stored F7a dev run (`eval-d76ad5c14764`, grader 7) the 22 answers that carry the
   fallback are `REFUSED` (8 compute, 1 trend), `HEDGED_MISS` (5 compute) and `INCORRECT`
   (8 peer-relative screens). The same failure in a baseline arm is graded `ERROR`: an empty or
   unusable reply raises, and `src/eval/systems/_stage_llm.py` returns
   `SystemAnswer(error=...)`; a provider refusal there becomes `SystemAnswer(refused=True)`.
   The stored `produced` payload keeps no metadata, so a $0 re-grade can only see the text.
2. **A universe count is read as a rank.** The fallback text above ends with the appended
   sentence `Universe: the covered companies (33 companies; 14 ranked for FY2022).` The system
   extracts 33.0 and 14.0 as produced values. The peer-relative grader finds no stated rank and
   no rows, so it falls back to the values (peer-rank-grading requirement 4.3) and grades
   "META is ranked 33.0 (values), not 3": `INCORRECT`. The same fallback credits a subject whose
   key rank happens to equal 14 or 33, and turns a refusal that carries only the sentence into a
   `HEDGED_MISS`.

**Relation to grading-dont-care-and-ties decision 6.** That decision is about ordinals and rank
numbers the *answer itself* writes ("Rank 1:", "ranked 3rd") being kept among produced values,
where they can match a count or a small key. Defect 2 is narrower and separate: the numbers come
from fixed sentences the *pipeline* appends after the verifier, which state the universe, never
an answer. This change removes those numbers only; decision 6 stays a later change of its own.

## Requirement

Terms are those of v2-grading and grading-dont-care-and-ties. A **v2 case** is one whose
`v2_subkind` is not `None`; every other case is graded by `grade_legacy`.

### 1. Synthesis failure text (`synthesis_failure`)

The two fallback summaries `_degraded_response` writes are, exactly:

- **E** (unusable reply: parse error, empty content, any other non-refusal failure):
  `This answer could not be completed: the synthesis step returned a response that was not
  valid JSON. The retrieved facts are preserved in execution_result; please retry the query.`
- **R** (provider refusal):
  `This answer could not be completed: the model declined to answer the synthesis step. The
  retrieved facts are preserved in execution_result.`

`synthesis_failure(text) -> str | None` returns `"error"` when `text` contains E as a substring,
else `"refusal"` when it contains R, else `None`. Exact, case-sensitive substrings: a prefix, a
paraphrase or another "could not be completed" note (the verifier's `(Note: automated
fact-checking could not be completed (LLM_ERROR); ...)`, a compute step's "The CAGR computation
could not be completed because ...") is not a synthesis failure. Anywhere in the text: in a
decomposed answer each sub-query's summary is its own block, so a failed sub-query's fallback
sits mid-text, and the answer is then a failed synthesis too.

### 2. Grading a failed synthesis (`grade_case`)

`grade_case(case, answer)`, after its existing `answer.error` rule (which still wins) and before
dispatching to `grade_v2` or `grade_legacy`:

- `synthesis_failure(answer.text) == "error"`: grade **`ERROR`**, `passed` false, `matched_value`
  `None`, and `error` a non-empty string that `error_origin` (`src/eval/core/checkpoint/run.py`)
  classifies as the arm's own (`"arm"`), not transport. The reason names the synthesis failure.
- `synthesis_failure(answer.text) == "refusal"`: grade **`REFUSED`**, `passed` false,
  `matched_value` `None`.

This holds for every case kind and sub-kind, whatever the answer's values, rows, citations and
`refused` / `hedged` flags. Neither grade is value-correct, confident-wrong or silent-wrong.

Why these grades (the oracle). `Grade.ERROR` is "the system failed" and `Grade.REFUSED` "declined
to answer" (`src/eval/core/graders/base.py`). The arm-neutral rule (arm-neutral-grading) is that
the same failure grades the same on every arm, and the baseline arms already map it:
`_stage_llm.run_traced` turns an unusable reply into `SystemAnswer(error=...)` (`ERROR`) and a
provider refusal into `SystemAnswer(refused=True)` (graded as a refusal). E is XbrlRag's
unusable reply; R is its provider refusal. `ERROR` counts in every rate's denominator and in
`error_rate`, not in accuracy, value accuracy, decline rate, or confident- or silent-wrong.

### 3. Disclosure sentences (`disclosure_spans`)

The pipeline appends, after the verifier, the sentences `screen_disclosure_text`
(`src/retriever/xbrl/screen_disclosure.py`) builds from a screen's data. A **disclosure
sentence** is a match of one of these three forms (case-sensitive; `T` is a ticker, one or more
characters other than whitespace, `(`, `)` and `;`):

- **U:** `Universe: the ` + (`covered companies` or a label of one or more characters other than
  `(`, `)` and newline, then ` group`) + ` (` + digits + ` company` or ` companies` + `; ` +
  digits + ` ranked for FY` + digits + `).`
- **N:** `Not ranked: ` + one or more entries joined by `; `, each `T (` + a phrase + `)`, then
  `.`. A phrase has no newline, and its parentheses, if any, are one level deep and balanced.
- **F:** `Fiscal years end on different dates: ` + one or more `T YYYY-MM-DD` joined by `, `,
  then `.`.

`disclosure_spans(text) -> list[tuple[int, int]]` returns the `(start, end)` of every disclosure
sentence, sorted by start, non-overlapping (an overlapping later match is dropped). For any
disclosure `d` that `build_screen_disclosure` returns, the spans of
`summary + " " + screen_disclosure_text(d)` (for a summary with no disclosure sentence) are
exactly the sentences of `screen_disclosure_text(d)`, in order.

The aggregate sentence (`Total of 5 companies: ...`, `screen_aggregate.py`) is **not** a
disclosure sentence here; see decision 3.

### 4. Values a disclosure sentence states are not produced values (`undisclosed_values`)

`undisclosed_values(text, values) -> list[float]`: let the **disclosed** numbers be
`[n.value for each span (s, e) of disclosure_spans(text) for n in extract_stated_numbers(text[s:e])]`
and the **stated** numbers `[n.value for n in extract_stated_numbers(b)]`, where `b` is `text`
with every span replaced by the same number of spaces. For each distinct value v of `values`,
with n_P its copies in `values`, n_D its copies among the disclosed numbers and n_A its copies
among the stated numbers: when n_D > 0, the earliest `n_P - n_A` copies (if positive) are
dropped. Every other value is kept, and order is unchanged. Equality is exact (`==`). The rule
only removes entries, never removes a copy the text also states outside a disclosure sentence,
and is idempotent. It has the shape of grading-dont-care-and-ties requirement 7, for the same
reason: stored runs keep `numeric_values` as produced.

`grade_v2` and `grade_legacy` read `numeric_values` as
`undisclosed_values(answer.text, graded_values(answer.text, answer.numeric_values))`. Rows and
`text` are unchanged.

### 5. A rank is never read from a disclosure sentence

The peer-relative reader (`_answer_rank`, used for v2 `peer_relative` and H-X `rank` cases)
reads stated ranks from `answer.text` with every disclosure span replaced by spaces, and its
values fallback (peer-rank-grading requirement 4.3) uses the values of requirement 4. Nothing else
in the reader changes: a rank stated outside the sentences, and the rows' values, decide as
before.

`disclosure_checks` still reads the **whole** text: the disclosure sentence is how an answer
states the universe, its exclusions and its period ends (S2, S3), and it still satisfies those
checks.

### 6. Grader version

These rules move grades, so `grader_version()` becomes `"8"`, and the history in
`src/eval/core/graders/honesty.py` gains:
`8 — a failed synthesis graded ERROR (refusal: REFUSED) for every kind; numbers in the appended
universe sentences are neither produced values nor ranks.`

### 7. Retired version pin

grading-dont-care-and-ties C8 pinned `"7"` exactly. It is changed in this spec PR to "at least
7" (with a comment naming this change); this spec's C6 pins `"8"`. The other retired pins already
read "at least 5" or "at least 6".

## Acceptance criteria

Cases are hand-built records in the shapes the v2 builder emits (fictional tickers `DEVA` ...
`DEVZ`, round amounts); answers are hand-built `SystemAnswer`s with a citation, so only the rule
under test decides. The META text is shortened from the stored dev answer named above. No test
reads `data/`, a database, the network or the clock; nothing in `src` is faked.

- **C1**: `synthesis_failure` (requirement 1). E → `"error"`, R → `"refusal"`, both → `"error"`;
  each mid-text after a sub-query block; `None` for the verifier note, the CAGR note, E's first
  sentence alone, E in lower case, and plain prose. The real `_degraded_response` output, for an
  empty-content `ValueError`, a `JSONDecodeError` and an `LLMRefusalError`, with a screen
  disclosure appended, reads `"error"`, `"error"`, `"refusal"`. Property: any prose without E or
  R reads `None`, and inserting E (or R) anywhere in it reads `"error"` (`"refusal"`). Oracle: the
  synthesizer's fixed sentences, requirement 1.
- **C2**: a failed synthesis grades `ERROR` for every kind (requirement 2). The META answer
  (hedged, values 33 and 14) is `ERROR`, not `INCORRECT`; a legacy lookup, a ratio, a sum, a
  median, a trend, a top screen and a peer case whose rows and values would be `CORRECT` without
  the fallback are `ERROR`; `passed`, `value_correct`, `confident_wrong` and `silent_wrong` are
  false, `matched_value` is `None`, and `error_origin(grade.error) == "arm"`. A system error
  still wins (its own reason). Property: over every case builder and any flags, values, rows and
  citations, a text containing E grades `ERROR`. Oracle: requirement 2, base.py and
  `_stage_llm.run_traced`.
- **C3**: a refused synthesis grades `REFUSED` for every kind (requirement 2), never `CORRECT`,
  never value-correct or confident-wrong. Property: as C2, with R. Oracle: requirement 2.
- **C4**: `disclosure_spans` (requirement 3). Each form alone, the three together as
  `screen_disclosure_text` writes them, a group label, `1 company`, a `Not ranked` phrase with a
  `;` and with parentheses, `FY2023` labels; none for prose ("33 companies were ranked"), an
  aggregate sentence, or a sentence missing its final `.`. Property: for random disclosures built
  by `build_screen_disclosure`, appended to prose, the spans are exactly the built sentences.
  Oracle: requirement 3 and the builder's fixed sentence.
- **C5**: `undisclosed_values` (requirement 4). The META text: `[33.0, 14.0]` → `[]`; a value
  also stated outside the sentence is kept; order kept. Properties: idempotent; returns a
  sub-multiset of `values` in order; the identity for a text without a disclosure sentence.
  Oracle: requirement 4, hand-read.
- **C6**: no rank or value from a disclosure (requirement 5) and version `"8"` (requirement 6).
  A hedged peer answer whose only numbers are the sentence's is `REFUSED` (not `INCORRECT`); an
  unhedged one is `INCORRECT` with reason "no rank in the answer"; a subject whose key rank is 14
  is not credited by "14 ranked"; a stated "3rd" with the sentence appended is `CORRECT` and its
  `universe` check passes on the sentence alone; a hedged lookup whose only numbers are the
  sentence's is `REFUSED`, not `HEDGED_MISS`. Metamorphic: appending a disclosure sentence (and its
  numbers to `numeric_values`, as the system extracts them) to an answer that already states the
  universe and exclusions leaves its grade unchanged. `grader_version()` is `"8"` on every
  scorecard. Oracle: requirements 5 and 6, hand-ranked.

## Interface

New (stubs in this PR):

```python
# src/eval/core/answer_parsing.py
def synthesis_failure(text: str) -> str | None: ...
def disclosure_spans(text: str) -> list[tuple[int, int]]: ...
def undisclosed_values(text: str, values: typing.Sequence[float]) -> list[float]: ...
```

Changed in behaviour, not signature (implementer): `grade_case` (requirement 2),
`graded_answer` / `grade_v2` / `grade_legacy` (requirement 4), `_answer_rank` (requirement 5),
`GRADER_VERSION` and its history (requirement 6). No pipeline or system code changes.

## Notes

- **Oracles.** E and R are copied from `_degraded_response`, and C1 checks them against its real
  output, so a later edit of the sentences fails a test instead of silently un-grading the rule.
  The disclosure forms are read from `screen_disclosure_text` and checked against
  `build_screen_disclosure` output. Expected grades are derived by hand from the requirements.
  Only dev answers were read; validation runs were counted in aggregate only, and no held-out
  answer was read.
- **Measured on a discarded prototype** ($0, offline: the 13 stored v7 re-grades in
  `data/eval/regrade/2026-11-11_v7/` against today's frozen suites; today's grader reproduces
  every stored grade exactly): see "Measured movement" below.
- **Re-grade.** After the implementation merges, every stored baseline run is re-graded at $0
  (`eval-regrade`), with before/after counts per run in the implementation PR.
- **Retired grader specs stay green** except for requirement 7's pin. The spec author ran
  `pytest test/spec -n auto --hypothesis-profile tdd-gate` against a discarded prototype of
  requirements 1-6 (outside the repository): every test passes, this spec's included.
- **Bounds.** Three regexes and one pass over the text; pure and deterministic.

### Measured movement

Grade transitions per stored run (rule 2 = requirement 2, rule 4/5 = requirements 4 and 5):

| Run (arm, split) | Rule 2: → `ERROR` | Rule 4/5 | Value acc. | Conf.-wrong | Silent-wrong |
|---|---|---|---|---|---|
| F7a `eval-d76ad5c14764` (XbrlRag DeepSeek, dev) | 22: 8 `INCORRECT` (all peer ranks read as 33.0), 9 `REFUSED`, 5 `HEDGED_MISS` | 1 `HEDGED_MISS` → `REFUSED` | 0 | 0 | 0 |
| F7b `eval-2a937631db31` (XbrlRag DeepSeek, validation; aggregate only) | 19: 6 `INCORRECT`, 9 `REFUSED`, 4 `HEDGED_MISS` | 3 `HEDGED_MISS` → `REFUSED` | 0 | 0 | 0 |
| F3a `eval-c5366b539a2e`, F3b `eval-01fb3f452bbe` (XbrlRag OpenAI) | 0 | 0 | 0 | 0 | 0 |
| F1a, F1b, F2, F4, F6a, F6b, F6c, A7, A8 (every baseline arm) | 0 | 0 | 0 | 0 | 0 |

**Arm-neutral on the comparison metrics.** No run's value accuracy, confident-wrong or
silent-wrong moves (the 8 + 6 `INCORRECT`s were hedged, so neither counted), and no baseline
arm moves at all: only XbrlRag writes the fallback and the disclosure sentences. What moves is
XbrlRag's split of its failures: F7a dev error rate +22/628 and decline rate −14/628 (`REFUSED`
−8, `HEDGED_MISS` −6), `INCORRECT` −8. The F3a/F3b runs carry the disclosure sentence on 105 and
77 answers and no fallback; nothing in them moves, so requirements 4 and 5 move only answers whose
own text states nothing else. No answer was credited by a disclosure number in these runs (the
"14 ranked" direction of defect 2 is latent).

### Decisions for the maintainer (with recommendations)

1. **`ERROR` for an unusable synthesis, `REFUSED` for a refused one.** Recommended: as specified;
   it is what the baseline arms already do with the same failures. The alternative, `REFUSED`
   for both, would count an empty reply as a decline (decline rate) where every other arm counts
   it as an error, and would make `--rerun-failed --rerun-grade ERROR` miss it.
2. **A decomposed answer with one failed sub-query is `ERROR` as a whole.** Recommended: keep.
   Its other parts may be right, but the case asked for all of them, and no stored answer is in
   this shape today (every E sits at the start of its text).
3. **The aggregate sentence is left out.** `Count of 14 companies: ...` states a count
   aggregate's own result, so dropping its number would take a correct count from XbrlRag when
   the summary does not restate it. No stored answer carries an aggregate sentence. Recommended:
   leave it out; revisit, with its own rule for `count`, if a stored answer shows a grade moved
   by it.
4. **The judge still scores a failed synthesis on text cases** (`judge_wiring` skips only
   `answer.error`). Out of scope here (no text case carries E in the stored runs); recommended as
   a wiring follow-up: skip the judge when `synthesis_failure` is not `None`.
5. **The verifier's "automated fact-checking could not be completed" note is not a failure.**
   The answer exists and is graded on what it states; the note only marks it unverified.
