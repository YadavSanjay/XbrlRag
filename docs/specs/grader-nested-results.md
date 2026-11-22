# grader-nested-results — the grader reads decomposed answers and nested component ids

The numeric grader (`src/eval/core/graders/numeric.py`) has two blind spots. Both turn
correct, cited answers into misses.

1. **Decomposed answers.** When the query decomposer splits a question into sub-questions,
   `XbrlRetrievalPipeline._merge_responses` puts each sub-answer's execution result under
   `execution_result["subqueries"][<id>]`. The grader reads only
   `execution_result["steps"]`, so a decomposed answer has no values and no citations in its
   eyes and is graded **REFUSED**.
   - In runs `eval-f2ae98c8358b` and `eval-9a74fa9183cf`, every decomposed answer (6 per run)
     was graded REFUSED or INCORRECT, and none was ever CORRECT.
   - Example: NVIDIA FY2025 "operating income for Graphics Segment and Operating Segments".
     Sub-question 1 resolved fact 65496, 5,085,000,000, which is the expected answer, and the
     case was graded REFUSED.
2. **Nested component ids.** Since `alternative-inputs-grounding`, a `component_fact_ids`
   input may be an array of alternatives (`{"revenue": 19102, "cost_of_revenue": [19111,
   19114]}`). `_iter_component_ids` returns the array as one item. `_as_fact_id` rejects it, so
   the alternatives are never counted as cited.
   - The two `aq-7b` gross-margin screens fell from CORRECT to PARTIAL in
     `eval-9a74fa9183cf`.

## Requirement

- **The grader sees every step of a decomposed answer:** the top-level `steps`, then each
  sub-question's steps in sub-question order, recursively.
- **Component ids flatten one array level:** a nested array's members count as cited ids.

Grading rules are otherwise unchanged. An answer still has to contain a matching audited value
to be CORRECT. A decomposed answer whose sub-answers are wrong is still graded wrong.

## Acceptance criteria

- **C1**: `execution_steps(execution_result)` returns:
  - the `steps` list (when it is a list);
  - followed by `execution_steps(sub)` for each `sub` in
    `execution_result["subqueries"].values()` (when that is a dict), in insertion order.

  Non-dict inputs, a missing `steps` and `None` sub-results contribute nothing.

  Point cases:
  - `{"steps": [a]}` gives `[a]`;
  - `{"subqueries": {"sq1": {"steps": [a]}, "sq2": {"steps": [b, c]}}}` gives `[a, b, c]`;
  - `{"steps": [a], "subqueries": {"sq1": None}}` gives `[a]`;
  - `None` gives `[]`.

  Oracle: the definition.
- **C2**: `component_fact_ids(value)` returns, in order:
  - an object's values or an array's elements;
  - with every element that is itself an array replaced by its members (one level);
  - a scalar becomes `[value]`, and `None` becomes `[]`.

  Point cases:
  - `{"revenue": 19102, "cost_of_revenue": [19111, 19114]}` gives `[19102, 19111, 19114]`;
  - `[1, [2, 3]]` gives `[1, 2, 3]`;
  - `{"a": 1, "b": 2}` gives `[1, 2]` (unchanged from today);
  - `7` gives `[7]`.

  Oracle: the definition.
- **C3**: The existing public grader functions over responses (`lookup_step_values`,
  `all_numeric_values`, `all_fact_ids`, `has_fact_citation`):
  - a decomposed response yields its sub-answers' OK values and fact ids. For the NVIDIA
    response above, `lookup_step_values` contains 5,085,000,000 and `all_fact_ids` contains
    65496;
  - a compute step with `component_fact_ids = {"revenue": 19102, "cost_of_revenue": [19111,
    19114]}` makes `all_fact_ids` contain all three ids;
  - a non-decomposed response is graded exactly as before.

  Oracle: the definitions, by hand.
- **C4**: Properties:
  - wrapping a response's execution result as a single sub-question of an otherwise empty
    decomposed result leaves `all_numeric_values` and `all_fact_ids` unchanged;
  - `component_fact_ids` of an object of scalars equals its values, which was the behaviour
    before;
  - the order of sub-questions does not change the *set* of values or fact ids.

  Oracle: the definitions.

## Interface

New functions (stubbed in the spec PR):

- `src.eval.core.graders.numeric.execution_steps(execution_result: Any) -> list[dict]`
- `src.eval.core.graders.numeric.component_fact_ids(value: Any) -> list[Any]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `_steps(resp)` returns `execution_steps(_execution_result(resp))`.
- Every use of `_iter_component_ids` uses `component_fact_ids`, and `_iter_component_ids` is
  removed.
- The effect shows only in a **new** run. `compare_runs.py` re-grades from the values and fact
  ids extracted when a run was graded, and for decomposed answers those were already empty.

## Notes

- **Expected effect:** the 6 decomposed answers per run are graded on their content (some are
  right), and the 2 gross-margin screens return to CORRECT.
- **Out of scope:** making the decomposer split less often. Phase 6 measurement comes first.
