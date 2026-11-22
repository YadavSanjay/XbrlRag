# judge-baseline-claim-text — a baseline arm's claim is its answer text, not its JSON reply

Amends requirement 3 of [judge-runner-wiring](judge-runner-wiring.md) ("no structured
statements").

## Why

Baseline arms (`naive_rag_*`, `closed_book`, `long_context_*`, `frontier_sec_tools`) reply
with a small JSON object (`value`, `unit`, `rows`, `refused`, `hedged`, `cited_chunks`,
`text`). `parse_structured_answer` (`src/eval/core/answer_parsing.py`) reads the values and
flags from it but keeps the **whole reply** as `SystemAnswer.text`, because several graders
read numbers and phrases from that text. `answer_claims` (`src/eval/core/judge_wiring.py`)
turns `answer.text` into the single uncited claim of an answer without structured
statements, so the text judge, and a person labelling for judge calibration
([judge-calibration](judge-calibration.md)), see a claim such as

    {"value": null, "unit": null, "rows": [], "refused": false, "hedged": false,
     "cited_chunks": ["b138…"], "text": "Apple disclosed that on March 25, 2024 …"}

instead of the answer's sentence. judge-runner-wiring requirement 3 assumed "a baseline
arm's prose is one uncited claim"; the reply is JSON, not prose. Found in the E3 export
smoke run (2026-11-12): both `naive_rag_filtered` and `closed_book` answers became JSON
claims.

## Requirement

1. **The prose of a reply.** A new function `answer_prose(text)` in
   `src/eval/core/answer_parsing.py` returns:
   - the value of the `"text"` key of the JSON object that `parse_structured_answer` reads
     from `text` (the first object, bare, fenced or within prose), **when that value is a
     `str`**, returned unchanged (not stripped);
   - otherwise `text` unchanged: no object, an object without `"text"`, or a `"text"` that
     is not a string (number, null, list, object, bool).
2. **Claims without structured statements.** In `answer_claims(answer)`, when there are no
   structured statements (judge-runner-wiring requirement 3, first bullet), the claim text is
   `answer_prose(answer.text)`: one claim `Claim("s0", answer_prose(answer.text), [])` when
   that has non-whitespace content, else no claims. A baseline reply whose `"text"` is blank
   therefore gives no claims, as blank prose already does.
3. **Nothing else changes.** The structured path of `answer_claims` (an `answer_details`
   list) is unchanged. `parse_structured_answer` still returns the whole reply as
   `SystemAnswer.text`; graders that read `answer.text` see exactly what they saw before.
   Prose answers (no JSON object) give the same claims as before.

## Acceptance criteria

- **C1**: `answer_prose` returns the `"text"` string of a JSON reply, exactly, whether the
  object is bare, in a ```` ```json ```` fence, or follows a prose lead-in. Oracle: the
  string the test itself put under `"text"` (hand-built replies). Property: for any string
  `s`, `answer_prose(json.dumps({..., "text": s}))` is `s`. Metamorphic: fencing the reply or
  prefixing prose leaves the result unchanged.
- **C2**: `answer_prose` returns its input unchanged when there is no JSON object, when the
  object has no `"text"` key, or when `"text"` is not a string. Oracle: the input itself
  (requirement 1). Property: for any text without a `{`, `answer_prose(t) == t`.
- **C3**: `answer_claims` on an answer without structured statements whose text is a
  baseline JSON reply gives `[("s0", <the reply's "text">, [])]`, and no claims when that
  `"text"` is blank. The reply goes through the real `parse_structured_answer`, as a
  baseline arm's does. Oracle: requirement 2 and the hand-built reply. Metamorphic: the
  claims of a JSON reply equal the claims of an answer whose text is the reply's `"text"`.

## Interface

- `src.eval.core.answer_parsing.answer_prose(text: str) -> str` (new)
- `src.eval.core.judge_wiring.answer_claims(answer: SystemAnswer) -> list[Claim]`
  (existing; behaviour amended by requirement 2)

## Notes

- All test data is synthetic: hand-built JSON replies in the shape of the baseline prompt's
  answer object.
- The judge-runner-wiring C2 tests (prose without JSON, blank text, structured statements)
  must keep passing; requirement 3 here guarantees it.
- After this merges and is implemented, the E3 calibration export
  (`scripts/eval/judge_calibration.py export`) is run; no answer set exists yet, so nothing
  needs re-labelling.
