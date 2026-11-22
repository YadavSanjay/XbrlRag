# alternative-inputs-grounding — a derived metric may cite either of two valid inputs

**Gross margin can be computed two ways:**
- `(Revenue − CostOfRevenue) / Revenue`; or
- `GrossProfit / Revenue`, from the filer's reported gross profit.

Both give the same margin from audited facts. In run `eval-f2ae98c8358b`, the system used
`GrossProfit` in 9 of the 10 gross-margin cases it answered, and the cost-of-revenue route in
1 (Apple FY2024, graded CORRECT).

**The suite expects only the cost-of-revenue route.** So all 9 GrossProfit answers were graded
**PARTIAL**: "value matched but grounding is wrong".

**Switching the ground truth to GrossProfit would not fix it.** It would only move the miss to
the answers that use cost of revenue. Grounding has to accept either valid input.

The citation grader already works on equivalence classes ("cite any one of these"). The gap is
in how the suite builder reads a derived metric's `component_fact_ids`. It flattens every id
into its own required class, so it cannot say "cost of revenue *or* gross profit".

## Requirement

A derived metric's `component_fact_ids` may give an **array** of fact ids for one input. That
input is satisfied by citing any one of them, or any fact equivalent to one of them.

- Each top-level entry of the JSON is one required input:
  - an object's values, or an array's elements;
  - a scalar id is one fact;
  - a nested array is a set of alternatives.
- The suite builder makes **one class per input**: the union of the equivalence classes
  (`fact_equivalence_classes`) of its alternatives.
- `mv_derived_metrics.GrossMarginPct` lists `[cost_of_revenue, gross_profit]` as alternatives
  for its second input when the filer reports `GrossProfit`. The value is unchanged.

## Acceptance criteria

- **C1**: `fact_id_groups_from_json(value)` returns one group (a list of ints) per top-level
  entry, in order:
  - a scalar int, or a string of digits, gives a one-element group;
  - a nested list of those gives a group of its members;
  - booleans, other strings, `None` and empty nested lists are skipped;
  - a value that is neither an object nor an array gives `[]`.

  Point cases:
  - `{"revenue": 10, "cost_of_revenue": [11, 12]}` gives `[[10], [11, 12]]`;
  - `[5, "6", [7, "8"]]` gives `[[5], [6], [7, 8]]`;
  - `{"a": true, "b": "x", "c": []}` gives `[]`;
  - `None` gives `[]`.

  Oracle: the definition.
- **C2**: `group_classes(groups, classes)`. `classes` maps a fact id to its equivalence class.
  For each group, it returns the sorted union of its members' classes; an id missing from
  `classes` is its own class.

  Point case: groups `[[10], [11, 12]]` with classes
  `{10: [10, 99], 11: [11], 12: [12, 13]}` give `[[10, 99], [11, 12, 13]]`.

  Oracle: the definition.
- **C3**: Composed with `grade_grounding` (unchanged), the AMD FY2024 gross-margin case is
  satisfied with recall 1.0 for **both** routes:
  - groups `[[revenue], [cogs 19111, gross_profit 19114]]`;
  - citing `{revenue, 19114}` (the GrossProfit route);
  - citing `{revenue, 19111}` (the cost-of-revenue route).

  Citing only `{revenue}` is not satisfied.

  Oracle: the definition and `grade_grounding`'s class rule.
- **C4**: Properties:
  - `group_classes` gives exactly one class per group;
  - each class contains every member of its group;
  - classes never shrink when `classes` grows;
  - a JSON object of scalars gives one singleton group per id, in order. That is the old
    behaviour: today's suites grade the same.

  Oracle: the definitions.

## Interface

New functions (stubbed in the spec PR):

- `src.eval.suites.sql_derived.fact_id_groups_from_json(value: Any) -> list[list[int]]`
- `src.eval.suites.sql_derived.group_classes(groups: Sequence[Sequence[int]], classes: Mapping[int, Sequence[int]]) -> list[list[int]]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- **Suite builder:** a case's `fact_id_classes` come from `group_classes`. JSON-sourced ids are
  grouped by `fact_id_groups_from_json`; ids from plain columns are one-element groups.
  `expected_fact_ids` stays the flat list of every id.
- **`scripts/ddl/mv/34_mv_derived_metrics.sql`:** `GrossMarginPct`'s components are
  `{"revenue": rev, "cost_of_revenue": [cogs, gross_profit]}` when `GrossProfit` is reported,
  else `{"revenue": rev, "cost_of_revenue": cogs}`. The value is unchanged.
- **After merge:** the maintainer rebuilds the MVs, then the suite is rebuilt and diffed. Only
  `GrossMarginPct` classes should change.

## Notes

- **Expected effect:** the 9 gross-margin PARTIALs in `eval-f2ae98c8358b` become CORRECT on
  re-grading, and Apple FY2024 (cost-of-revenue route) stays CORRECT.
- **Evidence:** AMD FY2024 fact 19111 is `CostOfGoodsAndServicesSold` (13,060M) and fact 19114
  is `GrossProfit` (12,725M), both from filing `0000002488-25-000012`.
