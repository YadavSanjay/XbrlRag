# glued-hint-split — a NOT_FOUND lookup whose hint glues two members is retried with them split

[Accuracy plan §1 "Planner round"](../design/XbrlRag_Accuracy_Plan_Oct2026.md), the last
deterministic `aq-8b` fix.

The planner sometimes writes two members the question names as **one** hint:
- `{"product": "Advertising and Family of Apps"}` (Meta FY2025);
- `{"product": "Family of Apps and Service, Other"}` (Meta FY2025);
- `{"instrument": "certificates of deposit and debt securities", "fair_value_level": "level 2"}`
  (Microsoft FY2024).

No member label contains the glued text, so each lookup is NOT_FOUND
(`UNRESOLVED_DIMENSION`) and the case is refused. Planner prompt v5 makes this rarer but does
not stop it.

Replayed through the real lookup on 2026-10-17 (SQL only), the same steps with the glued hint
split into two hints under keys the lookup does not map to an axis resolve OK at confidence
0.8, to the suite's expected facts:
- 41637 (196,175,000,000);
- 41640 (2,584,000,000);
- 47401 (44,000,000).

Each best candidate's dimensions match the hints one-to-one. The keys must be axis-free:
keeping `product` on the left part pins "Family of Apps" to the product axis, and Meta reports
it on the segment axis, so that variant stays unresolved.

## Requirement

1. **`split_glued_hints(dimensions)`** returns the split variants to try, in order. It returns
   `[]` when `dimensions` is not a non-empty dict.
   - The hints are visited in the dict's order. In each hint's text (`str(value)`), every
     match of the separator `\s+and\s+` (case-insensitive) is visited left to right.
   - Each match gives one variant:
     - a new dict equal to `dimensions` without that hint's key;
     - plus `"<key>_a"` = the text before the match, stripped;
     - plus `"<key>_b"` = the text after it, stripped.
   - A match is skipped when either part is empty, or when `<key>_a` or `<key>_b` is already a
     key of `dimensions`.
   - At most `4` variants are returned: the first four in this order.
   - `dimensions` itself is never modified.
2. **The executor.** When a `lookup` step is still NOT_FOUND after the existing fallbacks
   (derived families, `revenue-subfamily-fallback`), it tries each variant in order. Each try
   is one lookup with the step's fields and `dimensions` replaced by the variant.
   - The first try that is OK, **and** whose best candidate (`candidates[0]`) has
     `dimension_labels` matching the variant's hints one-to-one
     (`dimensions_match_ignoring_neutral`), becomes the step's result.
   - Its id, value, unit, citation and confidence are kept, and the value is bound under the
     step id.
   - When no try is accepted, the step keeps its original NOT_FOUND result.
3. **Scope.** An OK or AMBIGUOUS lookup, or a NOT_FOUND one with no variants, makes no extra
   lookup. A NOT_FOUND lookup makes at most one extra lookup per variant, and stops at the
   first accepted one.

## Acceptance criteria

- **C1**: `split_glued_hints` point cases:
  - `{"product": "Advertising and Family of Apps"}` →
    `[{"product_a": "Advertising", "product_b": "Family of Apps"}]`;
  - `{"product": "Family of Apps and Service, Other"}` →
    `[{"product_a": "Family of Apps", "product_b": "Service, Other"}]`;
  - `{"instrument": "certificates of deposit and debt securities", "fair_value_level":
    "level 2"}` → one variant with `fair_value_level` kept and `instrument_a`/`instrument_b`;
  - `{"x": "Research and Development Expense and 2022 Restructuring"}` → two variants, split
    at the first and at the second "and";
  - `{"x": "Cloud AND Gaming"}` → split (case-insensitive);
  - → `[]`:
    - `{"x": "Brand Name"}` and `{"x": "Operand"}` ("and" inside a word);
    - `{"x": "and services"}` and `{"x": "services and"}` (an empty part);
    - `{"x": "a and b", "x_a": "c"}` (key collision);
    - `"NONE"`, `{}` and `None`.

  Properties:
  - the number of variants is `min(4, number of usable separators)`;
  - every variant has exactly one more key than the input;
  - a variant's `_a` and `_b` values, joined with `" and "`, reproduce the original hint up to
    whitespace and case around the separator;
  - the input is never mutated.

  Oracle: Requirement 1.
- **C2**: The executor on Meta FY2025's step `m25` (Revenue,
  `{"product": "Family of Apps and Service, Other"}`), followed by `x2 = m25 * 2`. The fact store
  is faked at the DB boundary: the glued hint is `UNRESOLVED_DIMENSION`. The split hint is OK,
  2,584,000,000 USD, fact 41640, confidence 0.8, with a best candidate labelled
  `{segment axis: "Family of Apps [Member]", product axis: "Service, Other [Member]"}`.
  - `m25` is OK with that value, unit, citation (41640) and confidence; `bindings["m25"]` is
    2,584,000,000; `x2` is 5,168,000,000.
  - The split answer is rejected (`m25` stays NOT_FOUND, value None) when its status is
    AMBIGUOUS or NOT_FOUND, when it has no candidates, or when the candidate's labels do not
    match one-to-one (only one of the two members, or an extra non-neutral dimension).
  - With two variants, a rejected first and an accepted second give the second's fact.

  Oracle: Requirement 2; the value is corpus fact 41640.
- **C3**: Calls made to the fact store (dimensions per call, in order):
  - glued NOT_FOUND, first variant accepted → `[glued, variant 1]`;
  - glued NOT_FOUND, two variants both rejected → `[glued, variant 1, variant 2]`;
  - an OK or AMBIGUOUS step with a glued hint → `[glued]`;
  - a NOT_FOUND step whose hints contain no separator → `[its dims]`;
  - a hint with 6 separators, all rejected → exactly 1 + 4 calls.

  Metamorphic: appending a step with no separator to a plan adds exactly one call.

  Oracle: Requirement 3.

## Interface

- `src.retriever.xbrl.plan_executor.split_glued_hints(dimensions: object) -> list[dict]`

## Notes

- The facts are from the local corpus (public SEC filings), replayed 2026-10-17 through
  `XbrlFactstoreLookup.lookup`.
- **Why accepting is safe.** A variant is accepted only when the fact's dimensions are exactly
  the named members, one-to-one. A member whose own name contains "and" ("Technology and
  Computer Equipment", "Subscriptions and Services") splits into two parts contained in the
  **same** label. The one-to-one match needs distinct labels, so it is rejected and the step
  stays as it was.
- **Out of scope:**
  - splitting on "," or "&";
  - splitting more than one glued hint in one variant;
  - combining with the sub-family retry (a glued `ServiceRevenue` hint is split under its own
    family only);
  - the decomposer splits and the Tesla Debt cases (the latter held AMBIGUOUS on purpose by
    the `within-concept-confidence` Debt component guard).
- **Validation:** replay the stored plans of the three cases (SQL only). The final full run
  then covers it end to end.
