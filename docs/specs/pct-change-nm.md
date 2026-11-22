# pct-change-nm — percentage change from a non-positive base is "not meaningful"

Phase 1 of the accuracy plan.

## Requirement

A percentage change `(new − old) / old` is not meaningful when `old ≤ 0`: Micron's
operating income went from −$5,745M (FY2023) to +$1,304M (FY2024), and the formula gives
−122.7% for a large improvement. Today the calculator returns that number, the synthesizer
states it, and the eval case expects +122.7% (an `abs(old)` convention) — so the system's
only silent-wrong is a convention clash that neither side handles well.

Financial-reporting practice is to show such a change as "n/m" (not meaningful). After
this change, the compute stage recognizes a percentage-change expression whose base is
non-positive and marks the step not meaningful instead of returning a number; the eval
suite expects that outcome for such cases.

## Acceptance criteria

- **C1**: `XbrlCalculatorTool.pct_change_base(expr)` returns the base step id for the
  percentage-change forms `(a - b) / b`, `a / b - 1` and `(a - b) / b * 100`, with any
  whitespace and redundant parentheses, and `None` for anything else (`a / b`,
  `(a - b) / c`, `a - b`, `(a + b) / b`). Oracle: definition of percentage change.
- **C2**: `XbrlCalculatorTool.not_meaningful_reason(expr, bindings)` returns a non-empty
  string when `pct_change_base(expr)` is a step whose bound value is `≤ 0`, and `None`
  when the base is `> 0` or the expression is not a percentage change. Oracle: the n/m
  convention above; Micron FY2023→FY2024 operating income (−5,745,000,000 →
  1,304,000,000, from its FY2024 10-K) as the point case.
- **C3**: For a positive base, `evaluate` on a percentage-change expression is unchanged:
  equals `(a − b) / b` within `rel=1e-12` (float division only). Oracle: closed form
  computed in the test.
- **C4**: Metamorphic: scaling both bindings by the same positive factor leaves both
  `not_meaningful_reason`'s truthiness and the evaluated value unchanged (rel=1e-12);
  flipping the base's sign from positive to negative turns a `None` reason into a
  non-empty one. Oracle: scale invariance of a ratio; the n/m rule.
- **C5**: For an `EvalCase` loaded with `EvalCase.from_dict({..., "expect_not_meaningful":
  true, "expected_value": v})` — `v` being the change under the |base| convention, kept so
  the grader knows which numbers would be a percentage claim — `grade_scalar` returns:
  - `CORRECT` (passed) when the answer is refused or hedged and none of its numbers is
    within the case's `rel_tol` of `v`, `-v`, `100·v` or `-100·v`. Numbers that are not a
    percentage claim — the two component values it reports alongside "n/m" — are allowed.
  - `INCORRECT` when any produced number is within `rel_tol` of `±v` or `±100·v`
    (hedged or not: stating the ratio is the error), or when the answer is neither
    refused nor hedged (it asserted a value where none is meaningful).
  - Never `HEDGED_MISS`, and `silent_wrong` only for an unhedged, cited answer that states
    `±v`/`±100·v`.
  A case loaded without the key grades exactly as today. Oracle: requirement (n/m is the
  right answer; the components may be shown), hand-built answers in the test.

## Interface

New functions (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_calculator_tool.XbrlCalculatorTool.pct_change_base(expr: str) -> str | None`
  (new staticmethod)
- `src.retriever.xbrl.xbrl_calculator_tool.XbrlCalculatorTool.not_meaningful_reason(self, expr: str, bindings: dict[str, float | None]) -> str | None`
  (new method)

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `EvalCase` gains `expect_not_meaningful: bool = False`, read by `EvalCase.from_dict`
  from the key of the same name and written by `to_dict`; the tests reach it through
  `from_dict`, which ignores the key until it exists.

## Notes

- Integration (implementer): `PlanExecutor`'s compute step calls `not_meaningful_reason`
  before `evaluate`; a non-`None` reason yields a step result with no value and that
  reason (status naming is the implementer's choice, consistent with existing step
  statuses), which the synthesizer reports as "not meaningful (negative prior-year base)".
- Suite (implementer): `build_sql_derived_suite.py` sets `expect_not_meaningful=True` on
  growth cases whose base value is ≤ 0, and the suite is rebuilt; the corpus fingerprint is
  unchanged, so runs stay comparable.
- Out of scope: other ratios with a negative denominator (e.g. ROE on negative equity) —
  candidates for the same rule once `derived-lookup` lands.
