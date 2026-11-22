# eval-grader-honesty — make the eval scorecard say what actually happened

Phase 0 of the accuracy plan. No pipeline behavior changes; this only changes how answers
are graded and reported, so that later phases are measured honestly.

## Requirement

1. Every run records which grader produced it, so runs graded by different grader logic
   are never compared as if they were the same measurement.
2. A *hedged* answer that carries numbers but none of the expected ones ("I can't determine
   X; the nearest tagged fact is Y") is its own grade, `HEDGED_MISS` — not `INCORRECT`
   (it asserts nothing false) and not `REFUSED` (it did return numbers). Accuracy keeps its
   definition (`CORRECT / n`); the scorecard adds a hedged-miss rate and a *decline rate*
   (`REFUSED + HEDGED_MISS`) so declines are no longer hidden inside the error count.
3. The blanket breakdown exemption from silent-wrong stays (an answer at a different
   decomposition level is a relevance miss, not a false number), but it is no longer a
   blind spot: a failing, unhedged breakdown whose produced amounts include values that
   neither match an expected component, nor a sum of expected components, is flagged
   `needs_review` and counted on the scorecard.

## Acceptance criteria

- **C1**: `grader_version()` returns a non-empty string, stable across calls, and
  `Metrics.compute(...).to_dict()` and `RunReport.to_dict()` both carry it under
  `"grader_version"` (exact string equality). Oracle: requirement 1 (identity, no numeric
  tolerance).
- **C2**: `grade_scalar` on a `lookup`/`compute`/`dimensional` case returns a grade whose
  value is `"HEDGED_MISS"`, `passed=False`, `silent_wrong=False` when the answer is
  `hedged=True`, `refused=False`, has ≥1 produced numeric value and none is within the
  case's `rel_tol` of the expected value (or any `alt_values`). An *unhedged* non-matching
  answer is still `INCORRECT`; a hedged answer that matches is still graded as it is
  today; an answer with no produced values and `refused or hedged` is still `REFUSED`.
  Oracle: requirement 2, hand-built (case, answer) pairs in the test.
- **C3**: `Metrics.compute` reports (as attributes, and `n_hedged_miss` /
  `n_needs_review` also in `to_dict()`) `n_hedged_miss`, `hedged_miss_rate =
  n_hedged_miss / n` and `decline_rate = (n_refused + n_hedged_miss) / n`, exact to 1e-12
  (rational counts over small n; the tolerance only absorbs float division).
  `accuracy` is unchanged (`n_correct / n`). Invariant: `n_correct + n_partial +
  n_incorrect + n_refused + n_hedged_miss + n_error == n`. Oracle: counts of a hand-built
  results list.
- **C4**: `unexplained_breakdown_values(expected, produced, rel_tol)` returns the
  produced values with `abs(v) >= 1` that are within `rel_tol` of none of (a) an expected
  component value, (b) the sum of any non-empty subset of expected component values (the
  total included). Ratios/shares (`abs(v) < 1`) are never unexplained. Order of produced
  values does not change the result (compared as a sorted list). `compare_breakdown`
  exposes the same list as `BreakdownMetrics.unexplained_values`. Oracle: hand arithmetic
  on small component lists in the test; any value above the expected total by more than
  the tolerance is unexplainable by construction.
- **C5**: `CaseGrade.needs_review` is `True` exactly when the case is a failing breakdown
  (`incomplete=True`), the answer is unhedged, and `unexplained_values` is non-empty;
  `silent_wrong` stays `False` for every breakdown. `Metrics.compute` reports
  `n_needs_review`. Oracle: requirement 3, hand-built cases.

## Interface

New functions (stubbed in the spec PR, in a new module):

- `src.eval.core.graders.honesty.grader_version() -> str`
- `src.eval.core.graders.honesty.unexplained_breakdown_values(expected_values: Sequence[float], produced_values: Sequence[float], rel_tol: float) -> list[float]`

Behavior on existing code (the implementer adds these; a spec PR may only add stubs, so
the tests reach them by value — `grade.grade.value`, `getattr(..., None)`,
`to_dict().get(...)` — and fail by assertion until they exist):

- `Grade` gains a member with value `"HEDGED_MISS"`.
- `CaseGrade.needs_review -> bool` (property).
- `BreakdownMetrics.unexplained_values: list[float]` (field, default empty).
- `Metrics` gains `n_hedged_miss`, `hedged_miss_rate`, `decline_rate`, `n_needs_review`,
  and `to_dict()` gains `"grader_version"`, `"n_hedged_miss"`, `"n_needs_review"`,
  `"hedged_miss_rate"`, `"decline_rate"`.
- `RunReport.to_dict()` gains `"grader_version"`.

## Notes

- Persistence: `eval.eval_run` gains `grader_version TEXT`, `n_hedged_miss INT`,
  `hedged_miss_rate`, `decline_rate`, `n_needs_review INT` via
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `scripts/ddl/eval/eval_runs.sql`; the
  `eval_case_result.grade` column is free text, so `HEDGED_MISS` needs no DDL. Store
  round-trip is verified live, not in spec tests (DB is an I/O boundary).
- `/tech/eval` must render the new grade without crashing (an unknown grade string today
  falls through to a default badge — check, don't assume).
- Expected effect on the 2026-08-11 run `eval-9208cc232bb7`: ~60 INCORRECT → HEDGED_MISS,
  accuracy unchanged at 56.0%, decline rate ≈ 40%. Re-grading old runs is not required;
  `grader_version` NULL marks them as pre-versioned.
- Subset-sum search is bounded: breakdowns have ≤ 12 expected components in the suite
  (2^12 subsets); above that bound, fall back to (a) + the total only.
