# revenue-subfamily-fallback — a sub-family revenue lookup the filer does not tag is retried as Revenue

[Accuracy plan §0 item 1 / §4 item 9](../design/XbrlRag_Accuracy_Plan_Oct2026.md), the
deterministic part of the `aq-8b` / segment planner work.

A segment question such as "How much revenue did Meta Platforms report for Service, Other in
fiscal 2025?" asks for `Revenue` with a member hint. Since 2026-10-16 the planner has drifted
on some runs to a revenue **sub-family**:
- META asks for `ServiceRevenue` with `{"product": "Service, Other"}` (sometimes
  `{"service": "Service, Other"}`).
- Meta tags no `ServiceRevenue` concept, so the lookup is NOT_FOUND and the case is refused.
  META FY2025 has been refused 3 of 3 times since 2026-10-16.
- The same hint under `Revenue` resolves the right fact in every stored trace (FY2025
  2,584,000,000 = fact 41640; FY2024 1,722,000,000).

The fix is a retry like `derived-gross-profit`'s fallback. A NOT_FOUND `ServiceRevenue` or
`ProductRevenue` lookup is retried once as `Revenue` with the same hints.

It is safe only when a hint itself names the sub-family. Then the hint already carries the
"service" or "product" qualifier, and dropping the sub-family loses nothing.
- "ServiceRevenue for the Americas" retried as `Revenue {Americas}` would answer with the
  *total* Americas revenue: a confident wrong answer.
- So that step is not retried: none of its hints contains "service".

## Requirement

1. **`subfamily_fallback_step(step)`** returns the step to retry, or None.
   - It returns a **new** dict equal to `step` with only `concept_family` replaced by
     `"Revenue"`. This happens when all of the following hold:
     - `step["op"] == "lookup"`;
     - `step["concept_family"]` is `ServiceRevenue` (sub-family word `service`) or
       `ProductRevenue` (sub-family word `product`);
     - `step["dimensions"]` is a non-empty dict;
     - at least one of its values, lowercased, contains the sub-family word. The keys do not
       matter: the planner writes both `product` and `service`.
   - Otherwise it returns None: `"NONE"` or missing dimensions, other families, and
     non-lookup ops.
   - `step` itself is never modified.
2. **The executor.** When a `lookup` step's result is NOT_FOUND and
   `subfamily_fallback_step(step)` is not None, the executor runs one more lookup with the
   returned step.
   - If that lookup is OK, its result becomes the step's result. The step id, value, unit,
     citation and confidence are those of the Revenue lookup, and the value is bound under
     the step id for later `compute` steps.
   - Otherwise (NOT_FOUND, AMBIGUOUS, ERROR) the step keeps its original NOT_FOUND result.
     The fallback never turns a miss into an ambiguous or guessed answer.
3. **Scope.** An OK or AMBIGUOUS sub-family lookup, and any step for which the helper
   returns None, makes exactly the plan's own lookup call: no retry.

## Acceptance criteria

- **C1**: `subfamily_fallback_step` point cases. Here `S` is a META FY2025 lookup step
  (`id`, `op`, `cik`, `fy`, `fiscal_period`, `concept_family`, `dimensions`):
  - `ServiceRevenue`, `{"product": "Service, Other"}` → `S` with `concept_family`
    `"Revenue"`, every other key equal;
  - `ServiceRevenue`, `{"service": "Service, Other"}` → the same, with the hints unchanged;
  - `ProductRevenue`, `{"product": "Product"}` (AAPL) → `concept_family` `"Revenue"`;
  - `ServiceRevenue`, `{"product": "SERVICES"}` → retried (case-insensitive);
  - → None:
    - `ProductRevenue` with dimensions `"NONE"`;
    - `ServiceRevenue` with `{}`;
    - `ServiceRevenue` with `{"geography": "Americas"}`;
    - `ServiceRevenue` with `{"product": "Product"}`;
    - `Revenue` with `{"product": "Service, Other"}`;
    - an `op` other than `lookup`.

  Properties:
  - the input is never mutated;
  - a non-None result differs from the input only in `concept_family`;
  - None for every family other than the two sub-families.

  Oracle: Requirement 1.
- **C2**: The executor on Meta FY2025's plan: step `s25`, `ServiceRevenue`,
  `{"product": "Service, Other"}`, followed by a `compute` step `x2 = s25 * 2`. The fact store
  is faked at the DB boundary: `ServiceRevenue` is NOT_FOUND, and `Revenue` with that hint is
  OK, 2,584,000,000 USD, fact 41640, confidence 0.8.
  - `s25` is OK, value 2,584,000,000, unit USD, citation fact 41640, confidence 0.8.
  - `bindings["s25"] == 2,584,000,000`, and `x2` is 5,168,000,000.
  - Truth table: over Revenue retry statuses {OK, NOT_FOUND, AMBIGUOUS, ERROR}, `s25` is OK
    exactly when the retry is OK; otherwise it is NOT_FOUND with value None.

  Oracle: Requirement 2; the value is the corpus fact (2026-10-17 trace `6e5849cd4ac847c4`).
- **C3**: Calls made to the fact store, in order, by concept family:
  - NOT_FOUND `ServiceRevenue` with a qualifying hint →
    `["ServiceRevenue", "Revenue"]`, both with the step's hints and id;
  - OK or AMBIGUOUS `ServiceRevenue` → `["ServiceRevenue"]`;
  - NOT_FOUND `ServiceRevenue` `{"geography": "Americas"}` → `["ServiceRevenue"]`;
  - NOT_FOUND `ProductRevenue` `"NONE"` → `["ProductRevenue"]`;
  - NOT_FOUND `OperatingIncome` with a "service" hint → `["OperatingIncome"]`.

  Metamorphic: adding a non-qualifying step to a plan adds exactly one call.

  Oracle: Requirement 3.

## Interface

- `src.retriever.xbrl.plan_executor.subfamily_fallback_step(step: dict) -> dict | None`

## Notes

- Facts are from the local corpus (public SEC filings). Stored traces checked 2026-10-17:
  - every NOT_FOUND is a `ServiceRevenue`/`ProductRevenue` lookup;
  - every `Revenue` lookup with the same hint resolved the suite's expected value
    (`aq-3d-segment-member-lookup:fy=2025-segment=Service_Other-ticker=META` expects fact
    41640, 2,584,000,000).
- **Out of scope:**
  - A sub-family lookup with **no** hint (one AAPL FY2025 trace planned `ProductRevenue`
    with `"NONE"`). Inventing a hint from the family name would guess the member.
  - Other sub-family pairs.
  - The prompt-side causes: spurious hints and decomposer splits. Those are prompt changes,
    judged by repeats of the working set (§4 item 9).
- **Validation after the merge:** the META FY2024/FY2025 "Service, Other" and AAPL Product
  segment cases, plus a regression sample (`build_slice_suite.py --prefix aq-3d --stable 60`).
