# arm-neutral-grading — grade every system by the same rules

Part of external test v1. No pipeline behaviour changes; this changes how answers are
graded and reported so that baseline systems (naive RAG, long-context, closed-book) can be
scored on the same suite as XbrlRag without the grader favouring XbrlRag's output format.

## Requirement

1. **Confident-wrong.** One failure metric applies identically to every system: the answer
   asserts a wrong number and neither refused nor hedged, **whether or not it cites anything**.
   Today's `silent_wrong` additionally requires a `fact_id` citation, which a baseline never
   produces, so baselines could never register a silent-wrong answer. `silent_wrong` stays
   as it is (it remains XbrlRag's release gate). The breakdown and "not meaningful"
   exemptions keep their meaning: real component facts at a different decomposition level
   are a relevance miss. But a breakdown flagged `needs_review` (an unhedged amount no
   expected component accounts for) **is** confident-wrong.
2. **Value-correct.** Accuracy today counts only `CORRECT`. When a case requires a citation,
   a right number with no `fact_id` is graded `PARTIAL`, so a baseline that states every
   number correctly would score 0%. A second, arm-neutral measure says whether the
   **numbers** were right, regardless of citations and grounding. `accuracy` keeps its
   definition.
3. **Scorecard.** `Metrics` reports the count and rate of confident-wrong answers and the
   value-correct rate, alongside the existing metrics.
4. **Numbers from free text.** Baselines answer in prose. A deterministic extractor turns
   the numbers in a text into raw values, applying the scale words and signs analysts use
   ("$391B", "391,035 million", "(1,234)", "12.5%"), and it skips numbers that are not
   amounts: years, fiscal-period labels, dates and form names.
5. **Structured answers.** Baselines are asked to answer in a small JSON object. A parser
   turns that object (bare, fenced or embedded in prose) into a `SystemAnswer`. A reply with
   no parseable object falls back to the free-text extractor.
6. **Grader version.** These rules change the scorecard, so `grader_version()` becomes `"3"`.

## Acceptance criteria

- **C1**: for any graded case, `CaseGrade.confident_wrong` is `True` exactly when all of
  these hold:
  - `grade` is `INCORRECT`;
  - `produced["refused"]` is false and `produced["hedged"]` is false;
  - either `incomplete` is false, or `needs_review` is true.

  Further properties:
  - **Citations don't matter:** removing every `fact_id` from a wrong scalar answer does not
    change `confident_wrong`.
  - **Implication:** for an answer that wasn't refused, `silent_wrong` implies
    `confident_wrong`. A refused answer that still asserts a wrong cited number can be
    silent-wrong, because `silent_wrong` ignores `refused` and stays unchanged, but it is
    never confident-wrong.
  - **Declines aren't wrong:** `HEDGED_MISS` and `REFUSED` are never confident-wrong.

  Oracle: requirement 1, hand-built (case, answer) pairs.
- **C2**: `CaseGrade.value_correct` is `True` when the produced numbers match the ground
  truth as the grader judges them, ignoring citations and grounding:
  - **Scalar cases:** the grade is `CORRECT`, or `PARTIAL` with a matched value
    (`matched_value is not None`).
  - **Screen and breakdown cases:** the ranking or components passed, whether the final grade
    is `CORRECT` or a citation/grounding `PARTIAL`.
  - **Otherwise** (wrong number, refusal, hedged miss, no expected value, error) it is
    `False`.

  Further properties:
  - **Citations don't matter:** stripping every `fact_id` from an answer never changes
    `value_correct`.
  - **Implication:** `CORRECT` implies `value_correct`.

  Oracle: requirement 2, hand-built pairs.
- **C3**: `Metrics.compute(results)` exposes these as attributes, and `to_dict()` carries
  them under the same names:
  - `n_confident_wrong` (an int);
  - `confident_wrong_rate = n_confident_wrong / n`;
  - `value_accuracy = n_value_correct / n`.

  Exact to 1e-12: rational counts over small n, and the tolerance only absorbs float
  division. `accuracy` is unchanged (`n_correct / n`). Invariants:
  - `confident_wrong_rate >= silent_wrong_rate`;
  - `value_accuracy >= accuracy`;
  - all rates are 0 for an empty list.

  Oracle: counts of a hand-built results list.
- **C4**: `extract_numbers(text)` returns the amounts in `text` as floats, in order of
  appearance, exact to 1e-12 relative. The rules:
  - **Number:** an optional sign (`-`, `−` U+2212, `+`), optional currency (`$`, `US$`,
    `USD`, `NT$`, `TWD`, optionally followed by one space), and digits with optional comma
    thousands-groups and an optional decimal part.
  - **Parentheses negate:** `(1,234)` → −1234.
  - **Scale words multiply**, after one optional space (also after a closing parenthesis),
    case-insensitive: `thousand(s)` 1e3;
    `million(s)`, `mn`, `mm` 1e6; `billion(s)`, `bn` 1e9; `trillion(s)`, `tn` 1e12.
  - **Single-letter suffixes** `k`/`K`, `m`/`M`, `b`/`B`, `t`/`T` scale the same way, but
    only when directly attached (`391B`, `$1.2bn`).
  - **Percent** (`%` or ` percent`): the number as written (`12.5%` → 12.5).
  - **Not amounts, so skipped:**
    - a bare four-digit integer from 1900 to 2100 with no currency, separator, decimal,
      scale or percent (a year);
    - digits directly preceded by a letter (`FY2025`, `Q3`);
    - a number directly followed by a hyphen and a letter (`10-K`, `20-F`);
    - every number in an ISO date (`2025-09-27`).
  - **No amounts:** a text with none yields `[]`.

  Oracle: the example table in Notes (hand arithmetic). Properties:
  - **Integer round-trip:** formatting a random integer amount with commas, currency and a
    scale word and extracting it returns the amount times the scale.
  - **Negation:** wrapping in parentheses negates.
  - **Insensitivity:** surrounding words without digits don't change the result.
- **C5**: `parse_structured_answer(text)` returns a `SystemAnswer`. When `text` contains a
  JSON object, the first top-level object that parses is used (bare, in a fenced
  `json` block, or with prose around it). Its fields map as follows:

  | JSON field | `SystemAnswer` field | Rule |
  |---|---|---|
  | `value` (number, numeric string, or null) | `numeric_values` | first entry; a string goes through `extract_numbers`, and its first amount is used |
  | `rows` (list of `{entity, value}`) | `rows` | each row becomes `{"entity": str, "value": float}`; each row value is also appended to `numeric_values`, after `value` |
  | `refused` (bool, default false) | `refused` | |
  | `hedged` (bool, default false) | `hedged` | |
  | `cited_chunks` (list of str) | `chunk_ids` | |
  | — | `fact_ids` | always `[]` |
  | — | `text` | always the input text |

  **When no object parses:** `numeric_values = extract_numbers(text)`, `rows = []`,
  `hedged = False`, and `refused` is true exactly when no amount was extracted.

  Properties:
  - **Round-trip:** `json.dumps` of a random object, then parsing, returns its numbers, rows
    and flags (1e-12 relative).
  - **Wrapping:** fencing the object or surrounding it with prose doesn't change the
    result.

  Oracle: requirement 5, hand-built strings.
- **C6**: `grader_version()` returns `"3"`. `Metrics.compute(results).grader_version` and
  `to_dict()["grader_version"]` are `"3"` for any results list. Oracle: requirement 6
  (identity).

## Interface

New functions (stubbed in the spec PR, in a new module):

- `src.eval.core.answer_parsing.extract_numbers(text: str) -> list[float]`
- `src.eval.core.answer_parsing.parse_structured_answer(text: str) -> SystemAnswer`

Behaviour on existing code, which the implementer adds. A spec PR may only add stubs, so the
tests reach these by value (`getattr(grade, "confident_wrong", None)`,
`to_dict().get(...)`) and fail by assertion until they exist:

- `CaseGrade.confident_wrong -> bool` (property) and `CaseGrade.value_correct -> bool`
  (property), both also in `CaseGrade.to_dict()`.
- `Metrics` gains `n_confident_wrong`, `confident_wrong_rate` and `value_accuracy`, and
  `to_dict()` gains the same keys.
- `GRADER_VERSION` becomes `"3"`, with a history line in `src/eval/core/graders/honesty.py`.

## Notes

**C4 example table (the point oracle):**

| Text | Expected |
|---|---|
| `Revenue was $391B.` | `[391e9]` |
| `Revenue was $391.035 billion in FY2024.` | `[391.035e9]` |
| `391,035 million` | `[391035e6]` |
| `Net loss of (1,234) thousand` | `[-1234e3]` |
| `Operating margin was 12.5%` | `[12.5]` |
| `margin of 31.2 percent` | `[31.2]` |
| `NT$2,894.31 billion` | `[2894.31e9]` |
| `US$ 1.2bn and -3.5 million` | `[1.2e9, -3.5e6]` |
| `−7.5%` (U+2212) | `[-7.5]` |
| `In fiscal 2025, per the 10-K filed 2025-10-31, Q3 sales were 94.9 billion` | `[94.9e9]` |
| `FY2023 revenue: 383,285` | `[383285]` |
| `No figure is available.` | `[]` |

**Scope boundaries:**
- The extractor doesn't interpret ranges, ratios like "1.5x", or written-out numbers
  ("five billion"). Those yield whatever digits they contain, or nothing.
- Units (shares vs dollars) aren't tracked. Grading compares raw values.

**Operational follow-up, not in spec tests:**
- After merge, re-grade the stored reference run `eval-84a9a08f3945` with
  `scripts/eval/compare_runs.py` (no LLM calls) and record XbrlRag's confident-wrong rate
  under grader version 3. A non-zero value is reported as it is.
- Persisting the new metrics in `eval.eval_run` (via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
  in `scripts/ddl/eval/eval_runs.sql`) and showing them at `/tech/eval` is verified live,
  not in spec tests; the database is an I/O boundary.

**Oracles:** hand-built synthetic cases and strings only. No suite files, no database, no
network.
