# compound-text-part — a compound's text part is judged, and its other parts decide the grade

An H-X `compound` case may have a `text` dependent ([hx-authoring](hx-authoring.md)
requirement 5): "Of the two largest filers by revenue, what does the top one name as its main
supply-chain risk?". The build writes it as a `parts` entry of kind `narrative` with
`expected_text` (the reference answer), `key_points` and `passages` (hx-authoring requirement
14). Two gaps make such a compound unable to pass:

- **Grading.** `_grade_compound` ([v2-grading](v2-grading.md) requirement 14) grades a
  `narrative` part with `grade_narrative`, which returns `PARTIAL` ("narrative grading needs an
  LLM judge ... or expected_chunk_ids") for a case without `expected_chunk_ids`. A compound
  passes only when every part is `CORRECT`, so it never passes.
- **Judging.** The judge runs only on a case that is itself text-graded (`is_text_graded`:
  a non-blank `expected_text` and at least one key point,
  [judge-runner-wiring](judge-runner-wiring.md)). A compound carries its `expected_text` in
  a part, not at the top, so it is never judged.

A standalone text case is not passed or failed by the judge: the judge attaches
`judge_scores`, which are reported as means once the judge version is calibrated
([judge-calibration](judge-calibration.md)). This change treats a compound's text part the
same way.

Decision (maintainer, 2026-11-10): **the numeric parts decide.** The text part is judged and
its scores are recorded on the compound; the compound's grade and `passed` come from its other
parts only.

## Requirement

1. **The text part.** `text_part(case)` returns the compound's text part as an `EvalCase`, or
   `None`:
   - only for a case whose `v2_subkind` is `"compound"`; any other case gives `None`;
   - the part is the **first** entry `p` of `case.parts` whose kind is `narrative` and that
     has a non-blank string `expected_text` and at least one key point (`key_points`);
   - it is built exactly as `_grade_compound` builds a part: `EvalCase.from_dict(p without
     "part", plus id f"{case.id}#{p['part']}" and the case's question)`;
   - no such part gives `None`.
2. **Grading** (`grade_v2` of a `compound`, replaces the narrative clause of v2-grading
   requirement 14 for this part only):
   - the part `text_part(case)` names (matched by its position in `case.parts`) is **not
     graded**: it adds no key to `checks`, takes no part in the overall grade, and `incomplete`
     is computed over the other parts;
   - the other parts are graded and combined exactly as v2-grading requirement 14 says
     (every part `CORRECT` → `CORRECT`; every part `REFUSED` → `REFUSED`; any `INCORRECT` →
     `INCORRECT`; else `PARTIAL`; `passed` only for `CORRECT`; `checks` `{name: passed}` and
     `"<name>.<key>"`);
   - the reason is `"; ".join` of the graded parts' `f"{name}: {reason}"`, followed by
     `f"{name}: judged, not graded"` for the text part;
   - when the text part is the only part, the grade is `PARTIAL`, `passed` false, `checks`
     `{}`, reason `f"{name}: judged, not graded"`;
   - a `narrative` part that is not a text part (no `expected_text` or no key points) is
     graded as today (`grade_narrative`).
3. **Judging.** `is_text_graded(case)` is true for a compound exactly when `text_part(case)`
   is not `None` (for any other case it is unchanged). `judge_case(judge, case, answer)` of
   such a compound calls the judge with `case.question`, the text part's `expected_text`, the
   text part's key points and `answer_claims(answer)`. The runner (`run_graded`) therefore
   judges it like a text case: one judge call, `judge_scores` and `judge_cost_usd` on its
   result, counted in `judge_summary`.
4. **Nothing else changes:** compounds without a text part (the v2 generator's
   `compound.rank_by_ratio` / `compound.leader_growth`, compound-shape), every other sub-kind,
   standalone text cases, and `grader_version()`.

## Acceptance criteria

Cases are hand-built dicts in hx-authoring's C7 built shape, fictional tickers (`DEVA`, ...),
round amounts. A compound with ranking DEVA 300m > DEVB 200m (universe DEVA, DEVB, DEVC, DEVD;
DEVD excluded, `not_tagged`) and a `text` dependent on DEVA FY2025 with two key points.

- **C1**: `text_part`.
  - The compound gives an `EvalCase` with id `"hx-c01#dependent"`, kind `narrative`, the
    compound's question, and the part's `expected_text` and key points.
  - `None` for: a compound whose dependent is a trend; a narrative part without key points;
    a narrative part with a blank `expected_text`; a standalone text case; a screen.
  - With two text parts, the first is returned.

  Property: moving the text part before or after the ranking part returns the same part.
  Oracle: requirement 1.
- **C2**: grading.
  - A correct ranking that names the universe and DEVD → `CORRECT`, `passed`; `checks` has
    only `ranking` keys (`ranking`, `ranking.ranking`, `ranking.universe`,
    `ranking.exclusions`), all true; the reason ends with `"dependent: judged, not graded"`.
  - A wrong ranking → `INCORRECT`; silent on DEVD → `PARTIAL`; a refusal → `REFUSED`.
  - That a compound whose dependent is a trend grades both parts as before is guarded by the
    retired v2-grading C7 tests (`pytest test/spec`), not repeated here.
  - A compound with only a text part → `PARTIAL`, `checks == {}`.

  Metamorphic: changing the text part's `expected_text`, key points or passages never changes
  the grade, `passed` or `checks`. Oracle: requirement 2, hand-derived grades.
- **C3**: judging.
  - `is_text_graded` is true for the compound with a text part, false for the trend compound;
    unchanged for a standalone text case (true) and a screen (false).
  - `run_graded` with a fake system and a judge on a fake provider adapter: the compound gets
    exactly one judge call whose payload has the compound's question, the text part's
    reference answer and its key points (indices 0 and 1, in order); its result carries
    `judge_scores`; its grade is the one C2 states. The trend compound gets no judge call.

  Property: for any 1–3 key points, the payload lists exactly those, in order. Oracle:
  requirement 3.

## Interface

New, in `src/eval/core/graders/v2.py`:

```python
def text_part(case: EvalCase) -> EvalCase | None: ...
```

Changed (implementer): `_grade_compound` (requirement 2), `is_text_graded` and `judge_case` in
`src/eval/core/judge_wiring.py` (requirement 3).

## Notes

- **Oracles.** Every expected grade is derived by hand from requirement 2 and v2-grading's
  rules. The judge sits behind a fake provider adapter on a real `LLMGateway` (the LLM
  boundary), as in judge-runner-wiring's tests; its reply covers every key point. Nothing
  reads `data/`, a database or the network.
- **No grader version change.** No stored grade changes: no H-X suite has been built, and the
  generated compounds have no narrative part. The version stays at `"5"` (peer-rank-grading),
  so this change does not touch that change's locked C6.
- **Why not a judge threshold.** Standalone text cases are measured by judge means, not
  pass/fail, and the means are reportable only for a calibrated judge version. A threshold
  would be a new policy; it can be added later for both kinds at once.
- **Pooling.** The compound's judge scores are pooled with the text cases' in
  `judge_summary` (one more judged case). The report's groups (r1-report) still count the
  compound under `compound` by its tags.
- **Retired specs.** v2-grading C1–C5 (compound parts with a screen and a compute or trend
  part, no text part) and judge-runner-wiring (top-level text cases) keep passing
  (`pytest test/spec`).
- **Template.** [configs/eval/hx_template.yaml](../../configs/eval/hx_template.yaml) flags the
  gap until this lands.
