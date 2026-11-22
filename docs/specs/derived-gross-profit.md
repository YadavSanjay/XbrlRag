# derived-gross-profit — gross profit for a filer that does not tag it

Meta, Alphabet and Amazon do not tag `GrossProfit`. They report revenue and cost of revenue,
and the reader subtracts. A gross-margin question is planned as a `GrossProfit` lookup, a
`Revenue` lookup and a compute step (`gp / rev`). For these filers the first lookup is
NOT_FOUND, the compute step errors, and the answer is a refusal. Two suite cases never pass
in four runs (2026-10-16): Meta FY2024 and Alphabet FY2025 `GrossMarginPct`.

The data to answer is there. `mv_derived_metrics` already defines gross margin as
`(Revenue − CostOfRevenue) / Revenue`, with `component_fact_ids` `{"revenue": …,
"cost_of_revenue": …}`; the suite's ground truth uses those facts. All three filers tag
`CostOfRevenue` (Meta FY2024: revenue 164,501M, fact 40054; cost of revenue 30,161M, fact
40057).

## Requirement

1. **Fallback.** When a `lookup` step with `concept_family` `GrossProfit` resolves
   NOT_FOUND, the executor looks up the step's two components. Each is the same step with
   `concept_family` replaced and every other key kept: `Revenue` with id `<id>.revenue`,
   and `CostOfRevenue` with id `<id>.cost_of_revenue`.
2. **Acceptance.** The derived value is used only when both components are OK, both have a
   value, and both have the same non-null unit. Then the step's result is:
   - `op` `lookup`, status OK, value **revenue − cost of revenue**, computed by the
     sandboxed calculator, and the components' unit;
   - `component_fact_ids` `{"revenue": <fact id>, "cost_of_revenue": <fact id>}`, in
     `to_dict()` as well;
   - `citation` None, since no single fact is the gross profit;
   - confidence the lower of the two;
   - a `derived_from` list `[<id>.revenue, <id>.cost_of_revenue]`.
   Otherwise the step's NOT_FOUND result stands unchanged. A guess is never made from one
   component or from an AMBIGUOUS one.
3. **Bindings.** The step id binds to the derived value, so a later compute step
   (`gp / rev`) uses it.
4. **Scope.** Only `GrossProfit`, and only on NOT_FOUND. A `GrossProfit` lookup that
   resolves (OK or AMBIGUOUS) makes no component lookups. No other step changes.
5. **Trace (not locked).** A `decision` event `derived_family_fallback` records the step,
   the component statuses and fact ids, and whether the value was accepted.

## Acceptance criteria

- **C1**: On Meta FY2024's plan (`gp24` GrossProfit, `rev24` Revenue,
  `gm24 = (gp24 / rev24) * 100`), with a fact store where GrossProfit is NOT_FOUND:
  - `gp24` is OK with value 134,340,000,000 (164,501M − 30,161M) and `component_fact_ids`
    `{"revenue": 40054, "cost_of_revenue": 40057}`, also in `to_dict()`;
  - `gm24` is OK with value 81.6651570507 (134,340 / 164,501 × 100), within `rel=1e-9`.

  Oracle: hand arithmetic on the corpus facts (2026-10-16), matching `mv_derived_metrics`'
  0.81665157050717.
- **C2**: The fallback never guesses. If either component is NOT_FOUND or AMBIGUOUS, or
  the units differ, `gp24` stays NOT_FOUND with no `component_fact_ids`, and `gm24` stays
  an error.

  Oracle: Requirement 2.
- **C3**: Scope. A GrossProfit lookup that is OK (or AMBIGUOUS) produces exactly the plan's
  lookups, with no `.revenue` or `.cost_of_revenue` calls, and keeps its own value and
  citation. A NOT_FOUND lookup of any other family triggers nothing.

  Property: for any revenue R and cost C (USD, R > C > 0), the derived value is R − C, and
  `gm` is (R − C) / R × 100.

  Oracle: Requirement 4, and arithmetic.

## Interface

No new functions. The behaviour changes in `PlanExecutor.execute`, and `StepResult` gains
a `component_fact_ids` field (serialised by `to_dict()` when set). This spec is in
bug-fix mode, with no stubs.

## Notes

- The fake fact store stands at the DB boundary (as in `multidim-recovery`); the executor
  and calculator run for real.
- The facts are from the local corpus (public SEC filings), checked 2026-10-16.
- **Out of scope:** other derivable families (e.g. operating expenses), and screens, which
  read `mv_derived_metrics` directly.
- **Validation after the merge:** live gross-margin cases for META, GOOGL and AMZN, plus a
  regression slice of compute cases.
