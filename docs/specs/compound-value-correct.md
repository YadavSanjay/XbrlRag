# compound-value-correct: a compound's value-correctness does not depend on citations

**Route:** locked spec (eval grading: a wrong grade goes unnoticed, and an independent answer
exists). **Decided:** 2026-11-19, after the G1 look; disclosed by Amendment 4 of
`docs/eval/heldout_v2/preregistration.md`.

## Requirement

`value_correct` is the arm-neutral metric (`docs/specs/arm-neutral-grading.md`): it says whether
an answer's numbers were right, ignoring citations, so arms that cite no `fact_id`
(`frontier_sec_tools`, `long_context_oracle`, `closed_book`) are compared with XbrlRag on the
same footing. Every non-compound shape honours that: an uncited answer with the right numbers is
`PARTIAL` and still carries the evidence of the match (`matched_value` or `screen_metrics`).

The compound grader (`_grade_compound` in `src/eval/core/graders/v2.py`, v2-grading
requirement 14) breaks it. It grades every part against the answer, and when any part is
`PARTIAL` it builds a new, bare `PARTIAL` grade for the compound with neither field, which
`CaseGrade.value_correct` never accepts. A part is `PARTIAL` whenever the answer cites no
`fact_id`, so **an uncited compound answer is never value-correct, however right its numbers.**

Measured on development data only, at no cost (2026-11-19): the `xbrlrag_full` dev compound run
`eval-c78dd0934732` has 118 of 136 answers value-correct as stored; re-graded with only their
`fact_ids` removed, 0 of 136. The full dev suite's 593 non-compound answers (`eval-afb40ef42cb9`)
give 500 value-correct with or without `fact_ids`. At the G1 look both tools agents scored 0 of 51
H-F and 0 of 18 H-X compound cases; no non-XbrlRag arm had ever been graded on a compound case in
development.

When this change is done:

1. A compound case with at least one graded part is value-correct exactly when every graded part
   is value-correct, each part's value-correctness being the one its own part grade carries
   (`CaseGrade.value_correct` of the part). A judged text part (`compound-text-part`) is not a
   graded part and does not count.
2. Removing every `fact_id` from an answer never changes a compound's `value_correct`.
3. Nothing else about a compound's grade changes: `grade`, `passed`, `checks`, `reason`,
   `incomplete` and `confident_wrong` are what they were. A compound still passes only when every
   graded part is `CORRECT`, so citations still decide XbrlRag's pass rate and grounding.
4. A compound with no graded part (only a judged text part) is not value-correct, as before.
5. A compound's `value_correct` survives the checkpoint payload: a `CaseResult` rebuilt by
   `CaseResult.from_dict(checkpoint_payload(result), case)` has the same `grade.value_correct`.
   (Resumed runs and re-grades read it back from there; the `eval` store writes the property.)
6. The grader version rises to at least 13, so runs graded before and after the change are
   distinguishable.

Non-compound grading is unchanged.

Requirement 1 applies to every arm. The same roll-up also hides the value-correctness of a cited
compound that misses only a disclosure check (a ranking that does not state its universe or
exclusions): a standalone screen like that is value-correct, and a compound part like that becomes
value-correct too. So XbrlRag's compound `value_correct` can rise as well, never fall; its
`passed`, grounding and confident-wrong do not move.

## Acceptance criteria

- **C1**: An uncited compound answer whose every graded part has the right values is
  value-correct; the same answer cited is `CORRECT` and value-correct; uncited, its `grade` stays
  `PARTIAL`, `passed` stays false and `confident_wrong` stays false. This holds for an H-X compound
  (ranking + trend), an H-X compound with a judged text part, and both generated shapes
  (`compound.rank_by_ratio`, `compound.leader_growth`). Oracle: hand-keyed synthetic cases whose
  right answers are stated in the fixtures (the compound-shape C7 and compound-text-part fixtures'
  keys), and requirements 1 and 3.
- **C2**: For any combination of right or wrong ranking, ranking that states or omits its
  universe and exclusions, right or wrong dependent values, and any subset of cited `fact_ids`
  (including none), `value_correct` equals (ranking rows right) and (dependent values right), and is
  the same as for the answer with every `fact_id` removed. Oracle: requirement 1 applied by hand to
  each generated combination (a screen part's value-correctness ignores its disclosure checks, as
  for a standalone screen, arm-neutral-grading C2); requirement 2 as the metamorphic relation.
- **C3**: A compound with a value-wrong graded part (reversed ranking, wrong trend value, wrong
  pick or growth) is not value-correct, cited or not; a refused compound is not value-correct; a
  compound with only a judged text part is not value-correct. `grade` and `confident_wrong` are as
  before (a reversed ranking cited or uncited is `INCORRECT` and confident-wrong). Oracle:
  requirements 1, 3 and 4 with the fixtures' keys.
- **C4**: `CaseResult.from_dict(checkpoint_payload(r), case).grade.value_correct ==
  r.grade.value_correct` for compound results, value-correct and not, cited and uncited. Oracle:
  requirement 5 (round trip identity).
- **C5**: `int(grader_version()) >= 13`, and the run metrics carry the same version. Oracle:
  requirement 6.

## Interface

No new public interface. The criteria read the existing `src.eval.core.graders.v2.grade_v2`,
`src.eval.core.graders.dispatch.grade_case`, `CaseGrade.value_correct`,
`src.eval.core.checkpoint.run.checkpoint_payload`, `src.eval.core.report.CaseResult.from_dict`
and `src.eval.core.graders.honesty.grader_version`. How the compound grade carries its
value-correctness (a field, or evidence the property reads) is the implementer's choice, as long as
the checkpoint round trip (C4) keeps it.

## Notes

- Fixtures are synthetic (fictional DEVA..DEVE filers). `generated_cases.json` holds one
  `compound.rank_by_ratio` and one `compound.leader_growth` case built by the compound-shape spec's
  synthetic builder (FY2024 revenue DEVA 500m > DEVB 400m > DEVC 300m of five members, DEVE
  excluded; DEVB's operating margin 30.0% is the highest of the three; DEVA's net income growth is
  20.0%), as inputs only; their right answers are those of compound-shape C7.
- **After the merge of the implementation** (not part of this spec): the maintainer re-grades the
  stored G1 and G2 answers at no cost with `scripts/eval/regrade_run.py`, and the G1 results are
  recomputed and published with Amendment 4. The dev check above is repeated on the re-graded
  dev compound run, where 118 of 136 must be value-correct with and without `fact_ids`.
