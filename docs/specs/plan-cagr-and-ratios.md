# plan-cagr-and-ratios — a CAGR is computed in code, and a ratio that was asked is computed or declined

The D3 failure analysis of the dev baseline (`data/eval/d3/failure_classes_2026-11-10.md`,
classes C5 and C8, "Top 5 recommended fixes" items 3 and 4) found two plan defects that
fail on both providers:

- **C5, CAGR not annualized (12 A / 13 D).** Every lookup is OK, but the calculator
  excludes `Pow` by design (`xbrl_calculator_tool.py`, `_BINOPS`), so the planner stops at
  `end / start` (`int-8f03f4b84ad5-0080f8ed`: GOOGL retained earnings, 1.657), cubes the
  ratio (`int-86910ad075ce-04561786`), writes a six-step Newton iteration
  (`int-61a3f72ad807-006cf964`), or computes `((ia25 / ia23) - 1) / 2` and calls it a CAGR
  (`int-01252267680b-014b9820`, a DeepSeek silent-wrong).
- **C8, ratio asked, components answered (6 A / 5 D).** "X Assets, Current / Liabilities,
  Current, FY" or "as a percentage of": the plan holds the two lookups and no compute, the
  synthesizer reports both components with `fully_answered: true`, and the verifier passes
  each. Four of the six A silent-wrongs and one of the three D silent-wrongs are this class.
  Once the router said NUMERIC_LOOKUP (`int-1401f69cbbe3-029fc0d3`); once the decomposer
  split the ratio into two sub-queries, so no step could divide (`int-3503ee9f3fad-006d848e`).

This change adds a deterministic `cagr` plan op whose number of years is derived from its
two input steps (never written by the LLM), reads CAGR and ratio cues from the question by
rule, makes the plan validator's retry demand the operation the question asks for, keeps the
decomposer from separating the operands of one operation, and marks an answer that lacks the
asked value as not fully answered, so the silent-wrong is removed even when the plan still
misses. Semantics follow [`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md)
S4 ("CAGR (n years): (Current / value n years earlier)^(1/n) − 1; only when both are
positive"; "Current ratio: Current assets / current liabilities") and S2 (fiscal labels).
It changes no locked behaviour of [`pct-change-nm`](pct-change-nm.md),
[`units-currency-integrity`](units-currency-integrity.md) or
[`screen-aggregates`](screen-aggregates.md).

## Requirement

### Part A: the CAGR operation (C5)

1. **The CAGR value is computed in code.** `cagr_value(end, start, years)` returns
   `{"status", "value", "error"}`, checked in this order:
   - `end` or `start` is not a finite real number (None, a bool, NaN, ±inf, anything not an
     `int` or `float`): `"ERROR"`, the error naming the argument (`end` or `start`);
   - `years` is not an `int` (a bool is not one) or is below 1: `"ERROR"`, the error
     containing `years`;
   - `start <= 0` or `end <= 0`: `"NOT_MEANINGFUL"`, the error non-empty and naming the
     non-positive argument (S4: "only when both are positive"; a CAGR from or to zero or a
     loss has no meaning, as a percentage change from a non-positive base, pct-change-nm);
   - otherwise `"OK"` with `value` = `(end / start) ** (1 / years) - 1` and `error` None.

   `value` is None unless the status is OK. The calculator keeps excluding `Pow`; no
   expression grammar changes.

2. **The years come from the input steps, never from the LLM.** `cagr_years(start_step,
   end_step)` takes the two plan steps (dicts) and returns `end_step["fy"] -
   start_step["fy"]` when both `fy` are ints (not bools), the end's is the larger, and both
   steps have the same period kind: `fiscal_period` (absent or None meaning `"FY"`, compared
   upper-cased) and `ytd` (absent or None meaning False, compared as a bool). Otherwise
   None. Fiscal labels are S2's: a 52/53-week year is one year.

3. **The `cagr` plan op.** A step `{"id", "op": "cagr", "start": <step id>, "end": <step
   id>}`. `PlanValidator.validate` accepts it when:
   - `start` and `end` each name an *earlier* step whose op is `lookup`; otherwise exactly
     one error per field naming the step's index (`step[<i>]`), its id, the field in quotes
     (`'start'` / `'end'`) and the value's repr;
   - when both name earlier lookups: `cagr_years` of the two is not None, else exactly one
     error naming the index, the id and both input step ids; the two have the same `cik`
     (compared as strings when both are present), else exactly one error containing `cik`;
     the same `concept_family`, else exactly one error containing `concept_family`;
   - the step has no `years` key: a `years` key is exactly one error naming `'years'` (the
     years are derived, never planned).

   A later `compute` may reference a `cagr` step's id as any earlier step's.

4. **The executor runs it.** `PlanExecutor.execute` runs a `cagr` step over the earlier
   results of `start` and `end` and returns a `StepResult` with `op` `"cagr"`:
   - `start` or `end` naming no earlier step result, or one whose plan step is not a
     `lookup`: ERROR, the error naming the field and value;
   - an input step not OK: ERROR naming that step's id and status (an AMBIGUOUS value is a
     guess, never an operand, as for compute);
   - the inputs' units differ or either is None: ERROR naming the units (no currency
     conversion, S6);
   - the inputs' plan steps differ in `cik` or `concept_family`: ERROR naming the field;
   - `cagr_years` of the two plan steps is None: ERROR containing `years`;
   - otherwise the status, value and error of `cagr_value(end value, start value, years)`.

   An OK step has unit `"pure"` and the value is bound, so a later compute (`g * 100`) can
   use it; any other status leaves value and unit None. Its `to_dict()` carries, whenever
   `start` and `end` name earlier lookup steps, `"cagr": {"start", "end", "start_fy",
   "end_fy", "years", "start_value", "end_value", "fact_ids"}`: the two step ids, their
   planned `fy`, the derived years (None when not derivable), the two input values, and
   the two inputs' citation `fact_id`s in the order start, end.

### Part B: cues read by rule (C5, C8)

5. **Operation cues.** Words are read as: lower case; each `%` becomes ` percent `; every
   other character outside `a-z` and `0-9` becomes a space; split on spaces.
   `operation_kinds(text)` returns the subset of `{"cagr", "ratio", "difference"}` whose
   cue occurs:
   - **cagr**: the word `cagr` or `cagrs`; or `compound` or `compounded` immediately
     followed by `annual`, `annually`, `growth` or `rate`; or `annualized` or `annualised`
     immediately followed by `growth`;
   - **ratio**: in the raw text, a `/` with whitespace on both sides between a letter or
     `)` and a letter or `(` (the regex `[A-Za-z)]\s+/\s+[A-Za-z(]`; "Assets, Current /
     Liabilities, Current", never "10-K/A", "and/or" or "FY2024 / FY2025"); or in the
     words, `ratio of`, `divided by`, or `as a` followed by one of `percentage`, `percent`,
     `share`, `proportion`, `fraction` and then `of`; or `to`, not the first word, followed
     by one or two words none of which is `the`, `a`, `an`, `its`, `their`, `this` or
     `that`, and then `ratio` or `ratios` ("debt to equity ratio", "debt-to-equity ratio",
     "net debt to EBITDA ratio");
   - **difference**: the word pairs `difference between` or `difference in`.

   `operation_cue(query)` is `"cagr"` when `cagr` is among the kinds, else `"ratio"` when
   `ratio` is, else None; `difference` is never a cue (it is used by Requirement 8 only).
   Not cues, left to the router and planner LLMs: `per year`, `annual growth`, `average
   annual growth` (an arithmetic mean of yearly growth is a different metric), a bare
   named ratio (`current ratio`), `margin`, `percentage of` without `as a`, and a slash
   without spaces ("debt/equity").

6. **A cued operation is never answered by the lookup fast path.**
   `operation_intent(intent, operation)` returns `"NUMERIC_COMPUTE"` when the intent is
   `"NUMERIC_LOOKUP"` and `operation` is not None; otherwise the intent unchanged. The
   router applies it after `universe_intent` and before the fast-path plan, so a cued
   question always reaches the plan generator.

7. **The plan must do the cued operation.** `is_ratio_expr(expr)` is True when `expr`
   parses as a Python expression whose top node, after removing one outer multiplication
   by the literal `100` on either side, is a division whose numerator and denominator each
   contain at least one name; False otherwise, also for a non-string, empty or unparsable
   expr. `operation_plan_errors(plan, operation)`, run by the plan generator beside
   `PlanValidator.validate` and `group_plan_errors` (its errors feed the same retry and
   escalation), returns:
   - `[]` when `operation` is None or not `cagr`/`ratio`, when the plan is not a dict with
     a `steps` list, or when any step has op `screen`, `aggregate` or `breakdown` (a
     cross-filer or partition question ranks or splits a metric; screen-aggregates owns it);
   - for `cagr`: `[]` when a step has op `cagr`, else exactly one error containing `cagr`;
   - for `ratio`: `[]` when a `compute` step's `expr` satisfies `is_ratio_expr`, else
     exactly one error containing `ratio` and `compute`.

8. **The decomposer never separates one operation's operands.**
   `split_loses_operation(query, subqueries)` (sub-query texts) is True when there are at
   least two sub-queries and some kind in `operation_kinds(query)` is in the kinds of none
   of them; otherwise False. When it is True the decomposer returns the single passthrough
   sub-query (`sq1`, the original query, no dependencies), its existing fail-safe. A
   compound question whose operation survives in one sub-query ("which of the three largest
   … had the highest revenue CAGR" → `… revenue CAGR of <<TOP:sq1:3>>`) is kept.

### Part C: the asked value is in the answer or declared missing (C8)

9. **A missing asked value is an unanswered part.** `mark_missing_operation(response,
   query, operation)` takes a (sub-)query's verified response as a dict
   (`XbrlRagResponse.model_dump()` shape: `plan`, `execution_result`, `answer_details`,
   `unanswered_parts`, `metadata`, ...) and returns a new dict; the input is not changed.
   The asked value is **present** when `operation` is None or not `cagr`/`ratio`; when any
   step of `execution_result["steps"]` has op `screen`, `aggregate` or `breakdown`; for
   `cagr`, when a step with op `cagr` and status `OK` is cited (its `step_id`, compared as a
   string, is in some `answer_details` entry's `fact_ids`); for `ratio`, when a step with op
   `compute` and status `OK` whose plan step (same id) has an `expr` satisfying
   `is_ratio_expr` is cited likewise. When present, the result equals the input. Otherwise:
   - `{"question_fragment": query, "reason": <reason>}` is appended to `unanswered_parts`
     (created if missing), unless an equal entry is already there;
   - `metadata["fully_answered"]` is set to False (`metadata` created if missing);
   - `reason` contains `CAGR` (cagr) or `ratio` (ratio), and the non-empty `error` of every
     step of that kind that is not OK (cagr steps; compute steps with a ratio expr);
   - nothing else changes: the stated components stay (as with pct-change-nm, the inputs
     may be shown), so the answer is a hedge, never a confident wrong.

   The pipeline applies it to each (sub-)query's response after the faithfulness verifier
   and the disclosure sentences, with `operation_cue` of that (sub-)query's own text; the
   merged compound answer's `fully_answered` stays the AND of its parts.

10. **A statement citing a CAGR may state its inputs.** In
    `citation_integrity.unmatched_numbers`, a statement citing a `cagr` step id may state
    the step's value at a computed value's precision (`ratio_step`, and as a percent) and
    the values of its `start` and `end` steps at their own precision (as a compute's
    inputs). "Revenue grew at a 10.0% CAGR from $100.0 billion in FY2023 to $121.0 billion
    in FY2025" citing the cagr step is supported; a third amount matching neither input is
    still unmatched.

## Acceptance criteria

- **C1**: `cagr_value` follows Requirement 1. Point: the Notes' revenue fixture (FY2022 to
  FY2025, 3 years: 10%), the FY2023 to FY2025 citation fixture (2 years: 10%), a decline
  (negative CAGR above −1), `years` 1 equal to simple growth; `start` 0 or negative and
  `end` 0 or negative NOT_MEANINGFUL; NaN, ±inf, None and a bool input ERROR naming the
  argument; `years` 0, −1, 2.0, True and None ERROR containing `years`; value None unless
  OK. Values within `rel=1e-9, abs=1e-12` of `math.exp(math.log(end / start) / years) - 1`.
  Properties: `start * (1 + value) ** years` returns `end`; scaling both by one positive
  factor leaves the value unchanged; two consecutive spans compound to the whole span
  (`(1 + g_ac)^(m+n) = (1 + g_ab)^m (1 + g_bc)^n`); the value's sign is that of
  `end - start` and it is above −1; any non-positive input is NOT_MEANINGFUL. The round-trip
  and consecutive-span properties draw each `end / start` from [1e-3, 1e3]: below about 1e-7,
  `1 + g` is so close to 0 that no float `g` reproduces `end` to `rel=1e-9` (re-lock
  2026-11-11 after the implementer's dispute). Oracle: S4's definition, closed form computed
  in the test.
- **C2**: `cagr_years` follows Requirement 2. Point: FY2022 → FY2025 is 3; Q2 → Q2 two
  years apart is 2; absent `fiscal_period` equals `"FY"` and `"fy"`; equal or reversed
  years, a string or bool `fy`, different fiscal periods and different `ytd` are None.
  Property: for any two int years `a < b` and one period kind, the result is `b - a`;
  swapping the steps gives None. Oracle: Requirement 2; S2 fiscal labels.
- **C3**: `PlanValidator().validate` accepts a two-lookup-then-cagr plan and a later
  compute over the cagr id; it gives exactly one error, with the named parts, for a
  missing, forward, unknown or non-lookup `start` (and `end`), for underivable years, for a
  different cik, for a different concept_family, and for a `years` key. Property: for any
  integer `years` value on an otherwise valid cagr step, exactly that one error. Oracle:
  Requirement 3.
- **C4**: `PlanExecutor.execute`, with the fact store faked at its boundary (results per
  step id), returns an OK `cagr` step with the C1 value, unit `pure` and the `cagr` record
  (ids, fys, years, values, fact ids), binds it so `g * 100` computes; gives ERROR for a
  non-OK input, different units, a different cik, underivable years and an unknown
  `start`; NOT_MEANINGFUL for a non-positive start. Property: for any positive inputs and
  span, the step's value equals the closed form of C1 computed in the test. Oracle:
  Requirements 1, 2 and 4.
- **C5**: `operation_kinds` and `operation_cue` follow Requirement 5. Point cases for every
  listed cue, for each listed non-cue, and for the precedence (cagr over ratio).
  Properties: letter case does not change the kinds; inserting a word that is not part of a
  cue at the start or end of the text does not; `operation_cue` is always one of the kinds
  or None and never `difference`. Oracle: Requirement 5.
- **C6**: `operation_intent` follows Requirement 6 for every intent value and each
  operation. Property: the result is the input intent or `NUMERIC_COMPUTE`, and it changes
  only a `NUMERIC_LOOKUP`. Oracle: Requirement 6.
- **C7**: `is_ratio_expr` and `operation_plan_errors` follow Requirement 7. Point: `a / b`,
  `(a) / (b)`, `a / b * 100`, `100 * a / b`, `(a - b) / b` are ratios; `a - b`, `a * b`,
  `a / 2`, `2 / 4`, `a / b - 1`, `""`, `"a /"` and None are not; two lookups with no
  compute and a ratio cue give one error, adding the division gives `[]`; a cagr cue with a
  plan of lookups and a compute gives one error, adding a cagr step gives `[]`; a screen
  plan, operation None and an unknown operation give `[]`. Property: for any plan, the
  result has at most one error, and for `operation` None it is `[]`. Oracle: Requirement 7.
- **C8**: `split_loses_operation` follows Requirement 8. Point: the ratio split into its two
  components is True; a cagr kept in one sub-query of a compound question is False; one
  sub-query is False; a query with no operation is False; a difference split into two
  lookups is True. Property: appending the original query as one more sub-query always
  makes it False. Oracle: Requirement 8.
- **C9**: `mark_missing_operation` follows Requirement 9. Point: the C8 components answer
  (two lookups cited, no compute) gets one unanswered part and `fully_answered` False; with
  a cited OK ratio compute it is unchanged; an uncited ratio compute is still missing; a
  NOT_MEANINGFUL cagr step's error appears in the reason; a screen step exempts; operation
  None leaves it unchanged; missing `metadata` and `unanswered_parts` are created; the input
  is not changed. Properties: applying it twice equals applying it once; for operation None
  it is the identity; `answer_details` and `answer_summary` are never changed. Oracle:
  Requirement 9.
- **C10**: `unmatched_numbers` follows Requirement 10: the Notes' CAGR statement citing the
  cagr step is `[]`; stating a third amount gives exactly that amount. Property: for any
  positive inputs and span, stating the rounded CAGR percent and both inputs in billions to
  one decimal is supported, and stating three times the start is not. Oracle: Requirement
  10; hand arithmetic.

## Interface

- `src.retriever.xbrl.cagr.cagr_value(end: float | None, start: float | None, years: int | None) -> dict[str, Any]`
- `src.retriever.xbrl.cagr.cagr_years(start_step: Mapping[str, Any], end_step: Mapping[str, Any]) -> int | None`
- `src.retriever.xbrl.operation_cues.operation_kinds(text: str) -> frozenset[str]`
- `src.retriever.xbrl.operation_cues.operation_cue(query: str) -> str | None`
- `src.retriever.xbrl.operation_cues.operation_intent(intent: str, operation: str | None) -> str`
- `src.retriever.xbrl.operation_cues.is_ratio_expr(expr: Any) -> bool`
- `src.retriever.xbrl.operation_cues.operation_plan_errors(plan: Any, operation: str | None) -> list[str]`
- `src.retriever.xbrl.operation_cues.split_loses_operation(query: str, subqueries: Sequence[str]) -> bool`
- `src.retriever.xbrl.operation_cues.mark_missing_operation(response: Mapping[str, Any], query: str, operation: str | None) -> dict[str, Any]`
- Changed behaviour, no new signature: `PlanValidator.validate` (the `cagr` op),
  `PlanExecutor.execute` and `StepResult.to_dict` (the `cagr` step and key),
  `citation_integrity.unmatched_numbers` (a cagr step's inputs).

## Notes

- **Fixtures (synthetic; no filer's figures).** Example Co (cik `0000000001`) revenue:
  FY2022 80,000,000,000; FY2023 88,000,000,000; FY2024 96,800,000,000; FY2025
  106,480,000,000 (each 10% above the last). CAGR FY2022 → FY2025: (106.48 / 80)^(1/3) − 1
  = 1.331^(1/3) − 1 = 0.1. Citation fixture: FY2023 100,000,000,000 (fact 201) and FY2025
  121,000,000,000 (fact 202), 2 years: 1.21^(1/2) − 1 = 0.1. Ratio fixture: current
  assets 50,000,000,000 (fact 301), current liabilities 40,000,000,000 (fact 302), FY2024;
  current ratio 1.25. Questions are written for the test ("Example Co Assets, Current /
  Liabilities, Current, FY2024"), from the shapes D3 describes, not copied from the
  phrasebook or lexicon (neither was read for this spec).
- **What is spec-tested and what is reviewed.** Locked here: Requirements 1–10 through the
  functions above, the validator, the executor and citation integrity. **Reviewed by the
  maintainer, not locked** (prompt content has no independent oracle):
  - plan generator prompt (next free `_base/plan_generator` version): the `cagr` op with an
    example (`{"id": "g1", "op": "cagr", "start": "r22", "end": "r25"}`, never a `years`
    field or a Newton/average substitute); a ratio question always ends in a compute
    dividing the two lookups; the router's `OPERATION` hint to follow;
  - synthesizer prompt (next free `_base/synthesizer` version): state a cagr step's value
    as the CAGR citing the step id; when the question asks for a ratio or CAGR and
    FACT_RESULTS has none, put it in `unanswered_parts` and set `fully_answered` false
    (Requirement 9 enforces this after the fact; the prompt makes it the normal path);
  - decomposer prompt (next free `_base/query_decomposer` version): a single ratio,
    difference or CAGR question is one sub-query; never split an operation's operands.
- **Implementer work not locked here:**
  - `IntentRouter.route`: compute `operation_cue(query)`, apply `operation_intent` after
    `universe_intent` and before `_try_fast_path_plan`; carry `operation` on
    `IntentRouterResult` (and `to_dict`, the `intent_extraction` event), and log a
    `decision` event when the intent changes.
  - `PlanGenerator`: pass the operation to the user prompt (`operation_json`); append
    `operation_plan_errors` to the validator's errors inside the existing validate-retry
    loop (escalation unchanged).
  - `PlanValidator`: `cagr` in `VALID_OPS`; the validator needs the earlier steps' dicts
    (not only their ops) for Requirement 3.
  - `PlanExecutor`: the `cagr` dispatch (it needs the plan steps by id for the years); a
    `numeric_lineage` event (start, end, years, values, fact ids); confidence the lower of
    the two inputs'; `StepResult` gains `cagr: dict | None`.
  - `QueryDecomposer.decompose`: after `_parse`/`_order_by_dependency`, return the
    passthrough when `split_loses_operation(query, [sq.query for sq in subqueries])`, with
    a `decision` event at point `query_decomposer`.
  - `XbrlRetrievalPipeline`: `mark_missing_operation` per (sub-)query after the verifier
    and `_append_disclosures`, rebuilding the response from the returned dict.
  - The faithfulness verifier: a cagr step's evidence shows its value, years, inputs and
    fact ids, so the LLM verifier does not mark the stated CAGR unsupported.
- **Files.** New modules `cagr.py`, `operation_cues.py`; changed `intent_router.py`,
  `plan_generator.py`, `plan_validator.py`, `plan_executor.py`, `query_decomposer.py`,
  `citation_integrity.py`, `llm_xbrl_faithfulness_verifier.py`, `retrieval_pipeline.py`,
  three prompt files. None of `xbrl_factstore_lookup.py`, the screens or the breakdown, so
  no overlap with `dimensional-s1` beyond a possible textual one in `plan_executor.py`.
- **Affected dev cases (D3, dev split only).** C5: 12 A / 13 D (`compute.cagr`), examples
  `int-8f03f4b84ad5-0080f8ed`, `int-86910ad075ce-04561786`, `int-61a3f72ad807-006cf964`;
  silent-wrong D `int-01252267680b-014b9820`. C8: 6 A / 5 D (`compute.ratio`), examples
  `int-943b695472f1-00408afc`, `int-d06162478454-055f9fc6`, `int-3503ee9f3fad-006d848e` (D,
  decomposer split); silent-wrongs A `int-1401f69cbbe3-029fc0d3` (CEG, router
  NUMERIC_LOOKUP), `int-582b36b9ddda-03542d38` (QCOM), `int-943b695472f1-00408afc` (MSFT),
  `int-a9017a54836a-03934b57` (ADBE); D `int-582b36b9ddda-03542d38` (QCOM). Requirement 9
  turns each C8 silent-wrong into a hedge even where the plan still misses; Requirement 1–4
  and 7 should turn most C5 cases correct. C9-related CAGR cases (fiscal year from an end
  date) are not fixed here.
- **Validation plan (after the impl merges, maintainer cap).** A DeepSeek dev slice of the
  C5 and C8 cases above plus a seeded regression sample of `compute.yoy_growth`,
  `compute.difference`, lookup, trend and screen cases (`scripts/eval/build_slice_suite.py`
  → `run_suite.py --suite-path … --llm-profile deepseek --persist-audit --max-cost-usd
  0.50`), run before (main) and after, compared case by case with `compare_runs.py`; about
  $0.30–0.50 per run, the maintainer sets the cap and gives the go for each paid run. Dev
  split only; held-out sets (H-F, H-X, set B) are never read or run. Expected: most C5
  cases correct; C8 cases correct or hedged (no silent-wrong left in the class); no new
  silent-wrong among the regression sample (a forced ratio on a question that only used a
  spaced slash as "or" would show here).
- **Out of scope.** CAGR of a computed metric (free cash flow over years): `start`/`end`
  must be lookups; a later spec may allow compute inputs with both inputs' years. A CAGR
  from or to a period in another filer. A difference requirement in the plan (only the
  decomposer guard reads the difference cue). Cross-filer ratio screens (a `derived:`
  screen already covers them). The fiscal-year-from-end-date defect (D3 C9).
- **Decisions for the maintainer (recommendations given).**
  1. *End value 0.* The brief allowed `end < 0` only as not meaningful; S4 says "only when
     both are positive", so `end == 0` (a −100% CAGR) is NOT_MEANINGFUL here too.
     Recommended: follow S4 as specified.
  2. *"per year" and "average annual growth".* Not cues: "grew per year" can mean an
     absolute amount, and an average of yearly growth rates is a different metric from a
     CAGR. Recommended: leave both to the LLMs; widen only on evidence from a fresh,
     disjoint sample.
  3. *The spaced slash.* "A / B" is a ratio cue; the risk is a slash used as "or"
     ("net income / earnings"), which would force a meaningless division. Unspaced slashes
     are not cues. Recommended: keep the spaced form, and check the regression sample for
     a forced ratio.
  4. *Bare named ratios* ("current ratio", "quick ratio", margins) are not cues, though S4
     defines them as divisions. Recommended: keep them out for now (the planner usually
     computes them; D3 shows no failure there); revisit with evidence.
  5. *Requirement 9 keeps the components.* The answer still shows the two looked-up values
     with the unanswered part, rather than being reduced to a refusal. Recommended: keep;
     the grader treats it as hedged, never as a silent-wrong.
  6. *Difference.* Only the decomposer guard reads it. Recommended: no plan requirement
     until a failure class shows a missing subtraction.
