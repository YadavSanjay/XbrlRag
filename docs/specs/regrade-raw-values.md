# regrade-raw-values — a re-grade reads the answer's own values, not an earlier grader's filtered ones

Follows [eval-regrade](eval-regrade.md) (retired) and
[grading-stated-aggregate](grading-stated-aggregate.md) (grader 10).

**The defect.** Every grader since version 7 reads an answer through value filters
(`graded_answer`: period lengths, ranks, date parts, disclosure-only copies; and for a declining
sum or median, `_without_row_values`). The grade stores `produced = answer.to_dict()` of the
**filtered** answer (`src/eval/core/graders/dispatch.py::_base`). A re-grade rebuilds the answer
from that payload (`src/eval/core/regrade.py::answer_from_payload`), so it starts from values an
earlier grader already removed. While every rule only removed values, re-applying them was
idempotent and nothing was lost. Grader 10 is the first rule that **keeps** a value an earlier
rule removed (a stated median equal to a listed row), and a stored re-grade cannot bring that
value back.

Observed 2026-11-12. The stored grader-10 re-grade of the 13 K6 baseline runs reported 0 grade
changes. Grading the same answers with values rebuilt from their stored text gives:
- XbrlRag value-correct +1 on F3a (`int-bd7ee7bd1bce-0231b50a`), +1 on F7a, +2 on F3b and +1 on F7b;
- one REFUSED → HEDGED_MISS on each `naive_rag_filtered` run;
- no change on any other arm.

The verifier slice (`eval-7a77d7cd5f09` against `eval-2740206a1f8c`) reads +1 for the candidate
on stored values and −1 on rebuilt ones. So the error goes in one direction, against the system
whose answers most often list rows, and it decides baseline numbers that the final look is
compared with.

A wrong re-grade still prints a plausible accuracy, and the right one is stated independently: a
system's values are a deterministic function of its stored text, and re-grading a run at its own
grader version must reproduce its stored grades. Route: locked spec.

## Requirement

### 1. A new grade stores the values the system produced (`raw_numeric_values`)

`CaseGrade.produced` (from `grade_case`) gains the key `raw_numeric_values`: a list copy of the
`numeric_values` of the answer **as `grade_case` received it**, before any grader filter.
`produced["numeric_values"]` keeps today's meaning (the values as graded), so every reader of it
is unchanged. The key is present on every grade `grade_case` returns, including `ERROR` and
`REFUSED` ones.

### 2. The values a re-grade reads (`regrade_values`)

`regrade_values(produced, system)` returns a new list of floats:

1. `produced["raw_numeric_values"]` when it is a list (a run graded after this change);
2. else, rebuilt from `produced["text"]` as the system builds its values:
   - `system` is `"xbrlrag_full"`, or starts with `"xbrlrag_"`: `[n.value for n in
     extract_stated_numbers(text)]` (`src/eval/systems/xbrlrag_full.py`);
   - `system` is one of `"closed_book"`, `"naive_rag_filtered"`, `"naive_rag_plain"`,
     `"long_context_oracle"`, `"long_context_resolved"`, `"long_context_multi"`,
     `"frontier_sec_tools"`: `parse_structured_answer(text).numeric_values`;
3. else (an unknown system, `html_rag`, an empty or missing text with stored values, or `system`
   None): `produced["numeric_values"]` as stored (`[]` when absent).

It never mutates `produced`.

### 3. The answer a re-grade grades (`answer_for_regrade`)

`answer_for_regrade(produced, system)` returns `answer_from_payload(produced)` with its
`numeric_values` replaced by `regrade_values(produced, system)`. Every other field is as
`answer_from_payload` builds it.

### 4. The re-grade uses it (wiring, not locked)

`regrade_report` grades `answer_for_regrade(result["produced"], source["system"])`, and so does
`scripts/eval/compare_runs.py::regrade`. The re-grade provenance (`config["regrade"]`) records
`"values": "raw"` when every result had `raw_numeric_values`, `"rebuilt"` when every result was
rebuilt from text, `"mixed"` otherwise, and `"stored"` when step 3 applied to every result.

### 5. Grader version

Unchanged ("10"): grading rules do not change, only what a re-grade feeds them.

### 6. Wiring and follow-up (not locked; implementer, then the main session)

1. `_base` (or `grade_case`) adds `raw_numeric_values`. `SystemAnswer.to_dict` is unchanged.
2. Unit tests: `produced` carries the key; `compare_runs` uses `answer_for_regrade`.
3. **Exactness check ($0, read-only, implementer).** For every stored run graded at grader ≤ 6
   (before any value filter existed), rebuilding from text must equal the stored
   `numeric_values` for every case. Report the count of matching cases per system, and stop and
   report any mismatch. This is the evidence that step 2's per-system rebuild is the system's own.
4. After merge (main session): re-run the grader-10 re-grade of the 13 K6 runs from their v9
   sources, store it, and diff against the estimate above.

## Acceptance criteria

Fixtures are hand-built `EvalCase` / `SystemAnswer` / `produced` dicts. The real case is
`int-bd7ee7bd1bce-0231b50a` (dev split; public SEC values), with the same text and rows as
`test/spec/grading-stated-aggregate/fixtures/int_bd7ee7bd1bce_0231b50a.json`; copy it into this
spec's fixtures. Nothing reads `data/`, a database, the network or the clock.

- **C1**: a new grade stores raw values (Requirement 1). Grading the real case's answer (values
  as `extract_stated_numbers` reads its text, `hedged` True) gives `produced["raw_numeric_values"]`
  equal to those values, with 21,244,000,000 included. A hedged answer listing rows 100, 200 and
  300 under a median-200 key: `raw_numeric_values` keeps 200, `numeric_values` drops it.
  `raw_numeric_values` is present on an `ERROR` grade too (an answer with `error` set).
  - Oracle: Requirement 1 by hand.
  - Property: for random answers, `raw_numeric_values` equals the input values and is not the
    same list object.
- **C2**: `regrade_values` (Requirement 2).
  - Each branch: a stored raw list wins over the text.
  - `xbrlrag_full` and `xbrlrag_custom_arm` rebuild with `extract_stated_numbers`.
  - Each listed baseline arm rebuilds with `parse_structured_answer`, both for a JSON object
    reply and for a prose reply.
  - `html_rag`, an unknown system or None return the stored values.
  - The stored, filtered real-case payload (`numeric_values` [7, 8, 7, 7, 7, 7, 8, 7]) rebuilds
    to a list containing 21,244,000,000.
  - Oracle: Requirement 2 by hand.
  - Properties: `produced` is never mutated, and for any text, the rebuild equals what the
    system's own parser returns.
- **C3**: `answer_for_regrade` and grading (Requirement 3).
  - The stored real-case payload (filtered values, `hedged` True), graded at today's grader:
    through `answer_from_payload` it is `HEDGED_MISS`; through `answer_for_regrade(…,
    "xbrlrag_full")` it is `CORRECT`.
  - Every other field equals `answer_from_payload`'s.
  - Oracle: grading-stated-aggregate C3 and Requirement 3 by hand.
  - Metamorphic: a grade's own `produced`, passed back through `answer_for_regrade` and graded
    again with the same grader, gives the same grade (re-grading reproduces); for answers no
    filter touches, `answer_for_regrade` and `answer_from_payload` grade alike.

## Interface

```python
# src/eval/core/regrade.py
def regrade_values(produced: Mapping[str, Any], system: str | None) -> list[float]: ...
def answer_for_regrade(produced: Mapping[str, Any], system: str | None) -> SystemAnswer: ...
```

Changed, not locked: `src/eval/core/graders/dispatch.py::_base` (the `raw_numeric_values` key),
`regrade_report`, `scripts/eval/compare_runs.py::regrade`.

## Notes

- **Evidence ($0, read-only, 2026-11-12):** the counts above came from grading each stored
  result twice at grader 10, with stored values and with values rebuilt per Requirement 2. The
  sources are the 13 K6 baseline runs at grader 10 (`eval-b91dbe173c19` … `eval-82a42d45f803`,
  listed in the status change log) and the four verifier-slice runs.
- **Why both A (store raw) and B (rebuild):** storing fixes every future run at the source.
  Rebuilding recovers every stored run without re-answering, which is what the baseline needs
  before the system freeze.
- **Not here:** `rows` (no grader filters them); judge scores (unchanged).

## Review points

1. **The rebuild depends on each arm's parser not changing.** The exactness check (Wiring 3)
   proves it today. A future parser change makes `raw_numeric_values` the only exact source,
   which is why new grades store it. Recommendation: accept.
2. **The system name decides the rebuild.** An arm renamed or added later falls back to the
   stored values (step 3), never to a wrong parser. Recommendation: accept, and add new arms to
   the list in their own spec.
