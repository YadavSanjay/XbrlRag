# keyfree-checks — metamorphic consistency and perturbation checks with no answer key

v2 plan §3.5 "Key-free checks". Two families of checks find defects without an answer key,
so they scale to every filer at near-zero cost:

- **metamorphic consistency:** the same number reached by different routes must agree;
- **perturbation:** change the ticker or the year in a question, and the plan must differ
  in exactly that slot, and the answer must change accordingly.

This change adds the checks as **pure functions over answers and plans that already
exist**, plus a deterministic question perturbation generator. Nothing here calls the
pipeline, an LLM or a database; a later change wires a runner that produces the inputs
(step results and plans from traces) and persists the verdicts.

## Requirement

### Common shapes

1. **An amount** is a dict `{"value": float | None, "unit": str | None}`: the shape of a
   `StepResult`'s `value`/`unit` and of a breakdown or screen row. Other keys are ignored.
2. **A verdict** is a dict with at least:
   - `check`: the check's name (given per check below);
   - `verdict`: one of `"consistent"`, `"inconsistent"`, `"not_applicable"`;
   - `reason`: a short code from the list given per check.

   Each check adds the numbers it compared and the tolerance it used, under the keys named
   below.
3. **The unit policy (all metamorphic checks).** Values are never compared across units
   (S6).
   - Any compared amount whose `value` is `None` → `not_applicable`, reason
     `"missing_value"`; else any compared amount whose `unit` is `None` →
     `not_applicable`, reason `"missing_unit"`. Nothing can be checked.
   - Two compared amounts with **different** units (string inequality, both non-`None`)
     → `inconsistent`, reason `"unit_mismatch"`, with no numeric comparison.
     Justification: each check compares one filer-period's figures (or, for a screen, a
     ranking that S6 requires to be in one unit), so two routes reporting different units
     for the same quantity is itself a defect — one route picked a wrong fact or a
     convenience translation. Calling it `not_applicable` would let a wrong-currency route
     pass silently.
   - The missing checks come first: a `None` anywhere wins over a unit mismatch.

### Metamorphic checks (`src/eval/keyfree/metamorphic.py`)

4. **Breakdown sums to the total.** `check_breakdown_sum(rows, total, rel_tol=1e-3)`;
   `check = "breakdown_sum"`.
   - `rows` are the breakdown's amounts, `total` the same filer-period's lookup total.
   - Fewer than 2 rows → `not_applicable`, `"too_few_rows"` (before the unit policy).
   - Unit policy over every row and the total.
   - `observed = sum(row values)`, `expected = total value`,
     `tolerance = rel_tol * abs(expected)`.
   - `consistent` (`"match"`) iff `abs(observed - expected) <= tolerance`, else
     `inconsistent` (`"mismatch"`).
   - The verdict carries `observed`, `expected`, `tolerance` (all three `None` when no
     numeric comparison was made).
5. **YoY growth equals the two lookups.** `check_growth(growth, current, prior,
   abs_tol=5e-4)`; `check = "growth"`.
   - `growth` is the compute step's result as a **fraction** (0.12 for 12%); its `unit`
     is ignored (a ratio). `current` and `prior` are the two lookups.
   - Unit policy over `current` and `prior` only.
   - S4: growth is not meaningful when `prior <= 0`. Then: `growth` value `None` →
     `consistent`, `"not_meaningful"` (the system declined, as it should); a number →
     `inconsistent`, `"not_meaningful"` (it stated a figure S4 rules out).
   - Otherwise a `None` growth value → `not_applicable`, `"missing_value"`.
   - `expected = (current - prior) / abs(prior)`, `observed = growth value`,
     `tolerance = abs_tol`; `consistent` (`"match"`) iff `abs(observed - expected) <=
     abs_tol`, else `inconsistent` (`"mismatch"`).
   - The verdict carries `observed`, `expected`, `tolerance` (`None` where not computed;
     for `"not_meaningful"`, `observed` is the growth value and `expected` is `None`).
6. **A screen's ranking agrees with the lookups.** `check_screen_order(screen_rows,
   lookups, order="desc", rel_tol=1e-9)`; `check = "screen_order"`.
   - `screen_rows` is the screen's ranked list (rank = list position), each a dict with
     `ticker` and `unit`; `lookups` maps ticker → the individual lookup amount for the
     same metric and period.
   - `order` not in `{"asc", "desc"}` → `ValueError`.
   - A row is **compared** when its ticker is in `lookups`, the lookup's value and unit
     are not `None`, and the row's unit is not `None`; every other row is **skipped**.
     (Missing data never fails the check.)
   - Unit policy over the compared rows: any row whose unit differs from its lookup's, or
     compared lookups in more than one unit → `inconsistent`, `"unit_mismatch"`.
   - Fewer than 2 compared rows → `not_applicable`, `"too_few_rows"` (checked after the
     unit policy).
   - **Ties:** lookup values `a` and `b` tie when `abs(a - b) <= rel_tol * max(abs(a),
     abs(b))`. Tied rows may appear in either order.
   - An **inversion** is a pair of compared rows `i < j` (screen positions) that do not
     tie and where, for `desc`, `lookup_i < lookup_j`; for `asc`, `lookup_i > lookup_j`.
   - `consistent` (`"match"`) iff there is no inversion, else `inconsistent`
     (`"misordered"`).
   - The verdict carries `order`, `compared` (tickers in screen order), `values` (their
     lookup values, same order), `skipped` (tickers in screen order), `inversions` (list
     of `[ticker_i, ticker_j]`, ordered by `i` then `j`) and `tolerance` (= `rel_tol`).
7. **Q4 is the full year minus nine months.** `check_q4(q4, fy, nine_months,
   rel_tol=1e-3)`; `check = "q4"`.
   - Unit policy over all three.
   - `observed = q4`, `expected = fy - nine_months`, `tolerance = rel_tol * abs(fy)`;
     `consistent` (`"match"`) iff `abs(observed - expected) <= tolerance`, else
     `inconsistent` (`"mismatch"`). Carries `observed`, `expected`, `tolerance`.
   - It applies once 10-Qs land (plan phase 3b); it is tested now on synthetic inputs.

### Perturbation (`src/eval/keyfree/perturbation.py`)

8. **A perturbation** is a dict:
   - `{"kind": "ticker", "from": "AAPL", "to": "MSFT"}`, optionally with `"from_cik"` and
     `"to_cik"` (both or neither);
   - `{"kind": "year", "from": 2024, "to": 2023}`. A year perturbation is a **shift** by
     `delta = to - from`, applied to every year in the question and every `fy` in the
     plan, so a two-year question keeps its shape ("2024 vs 2023" → "2023 vs 2022").
9. **Plan slots.** `plan_slots(plan) -> list[dict]` reads the plan JSON
   `{"steps": [...]}` (the `plan_generator` / `PlanValidator` shape). A plan that is not
   a dict, or has no `steps` list → `ValueError`. It returns one dict per step, in plan
   order, with **exactly** these keys:
   `op, cik, ticker, fy, fiscal_period, ytd, as_originally_reported, as_of,
   concept_family, dimensions, axis, metric_kind, order, top_n, min_threshold, unit,
   expr`. Normalisation (a missing key is `None` before normalising):
   - `ticker`: a string is stripped and upper-cased;
   - `cik`: an int, or a string of digits after stripping, becomes the 10-digit
     zero-padded string; anything else is kept;
   - `fiscal_period`: `None` → `"FY"`; a string is upper-cased;
   - `ytd`, `as_originally_reported`: `None` → `False`;
   - `dimensions`: the step's `dimensions`, or its `dimension` (the screen spelling) when
     `dimensions` is absent. `None`, `{}` or the string `"NONE"` (any case) → `"NONE"`; a
     dict → a dict with keys and values stripped and lower-cased (`str()` of a value);
   - `order`: a string is lower-cased;
   - `expr`: a string has every identifier that is the `id` of a step in the plan
     replaced by `$<index of that step>`, and all whitespace removed; other identifiers
     and numbers are kept. A non-string is `None`;
   - all other slots are kept as given.

   `id`, `section_hint` and any other key are not slots: step ids and free-text hints
   differ between two plans for no semantic reason.
10. **Plan perturbation check.** `check_plan_perturbation(original_plan, perturbed_plan,
    perturbation)`; `check = "plan_perturbation"`.
    - An unknown `kind`, or `from == to` (tickers compared upper-cased) → `ValueError`.
    - A plan without a non-empty `steps` list (e.g. an `{"error": ...}` plan) →
      `not_applicable`, `"no_plan"`.
    - Different step counts → `inconsistent`, `"step_count"`; a step whose `op` differs →
      `inconsistent`, `"op_mismatch"`.
    - **Perturbed slots:** `ticker` and `cik` for a ticker perturbation, `fy` for a year
      perturbation. Every other slot must be equal in both plans.
    - **Expected value** of a perturbed slot, per step, from the original slot `o`:
      - `ticker`: `to` (upper) when `o == from` (upper), else `o`;
      - `cik` with `from_cik`/`to_cik` given: normalised `to_cik` when `o` equals
        normalised `from_cik`, else `o`;
      - `cik` without them: any value is accepted on a step whose original `ticker`
        slot equals `from`; elsewhere `o`;
      - `fy`: `o + delta` when `o` is an int, else `o`.
    - A **difference** is `{"step": i, "slot": s, "original": o, "perturbed": p}`.
      Collect, in step then slot-key order (the order listed in requirement 9):
      unexpected differences (a non-perturbed slot with `o != p`) and wrong changes (a
      perturbed slot whose `p` is not its expected value).
    - A **target** is a perturbed slot whose expected value differs from `o` (for the
      free `cik` case, the step's `ticker` slot counts as the target).
    - Verdict, first that applies: `"step_count"`, `"op_mismatch"`, then
      `"unexpected_change"` if any unexpected difference, `"wrong_change"` if any wrong
      change (both `inconsistent`); then no target → `not_applicable`, `"slot_absent"`
      (the original plan never names the perturbed value, e.g. a screen under a ticker
      swap); else `consistent`, `"match"`.
    - The verdict carries `differences` (the list above; empty when the plans could not be
      aligned) and `changed` (every perturbed slot with `o != p`, as differences, same
      order).
11. **Answer perturbation check.** `check_answer_perturbation(original_values,
    perturbed_values, lookups_equal, rel_tol=1e-9)`; `check = "answer_perturbation"`.
    - Either list empty → `not_applicable`, `"missing_value"`.
    - The answers are **equal** when the lists have the same length and every pair
      satisfies `abs(a - b) <= rel_tol * max(abs(a), abs(b))`.
    - `lookups_equal` is the caller's statement that the underlying lookups (the same
      metric for the two filers or years) are equal. With `lookups_equal=False`:
      `consistent` (`"match"`) iff the answers are not equal, else `inconsistent`
      (`"unchanged"`: the system ignored the perturbation). With `True`: `consistent`
      iff equal, else `inconsistent` (`"changed"`).
    - Carries `original`, `perturbed`, `lookups_equal`, `tolerance` (= `rel_tol`).
12. **Ticker perturbation of a question.** `perturb_ticker(question, from_ticker,
    to_ticker, names)` returns `{"question": str, "perturbation": {"kind": "ticker",
    "from": FROM, "to": TO}}` (tickers upper-cased) or `None`.
    - `from_ticker == to_ticker` (upper-cased) → `ValueError`.
    - `names` maps a ticker to its company surface forms, e.g. `{"AAPL": ["Apple Inc.",
      "Apple"]}` (from the committed alias file; the caller loads it). The **forms** of a
      ticker `T` are `T` itself (matched **case-sensitively**, upper case) and each of
      `names.get(T, [])` (matched **case-insensitively**).
    - A form matches where it is not preceded or followed by an ASCII letter or digit.
      The question is scanned left to right; at each position the **longest** matching
      form wins, and matches do not overlap.
    - Each match of a `from` form is replaced: a ticker match by `TO`; a name match by
      `names[TO][0]` if `names` has a non-empty list for `TO`, else by `TO`.
    - `None` when no `from` form matches, or when any `to` form already matches in the
      original question (the swap would be ambiguous).
13. **Year perturbation of a question.** `perturb_year(question, from_year, to_year)`
    returns `{"question": str, "perturbation": {"kind": "year", "from": from_year, "to":
    to_year}}` or `None`.
    - `from_year == to_year` → `ValueError`.
    - Year mentions: a 4-digit `20dd` not preceded or followed by a digit (so `FY2024`
      counts), and `FY` (any case) followed by exactly two digits `dd` and no further
      digit, not preceded by a letter or digit, meaning year `2000 + dd`.
    - Every mention is shifted by `delta`: a 4-digit year becomes the shifted 4-digit
      year; a `FYdd` keeps its `FY` text as written and gets the shifted year's last two
      digits.
    - `None` when `from_year` is not among the mentioned years, or when any shifted year
      falls outside 2000–2099.

## Acceptance criteria

All inputs are synthetic and built by hand in the tests. Expected values are computed in
the test from the closed forms in the requirement.

- **C1**: `check_breakdown_sum`.
  - Rows 60 + 30 + 10 = 100 vs a total of 100 → `consistent`, `observed` 100,
    `expected` 100, `tolerance` 0.1 (rel_tol 1e-3).
  - Total 100.2: |100 − 100.2| = 0.2 > 0.1002 → `inconsistent`, `"mismatch"`; total
    100.05: 0.05 ≤ 0.10005 → `consistent`.
  - One row → `not_applicable`, `"too_few_rows"`.

  Metamorphic: permuting the rows or scaling every value by the same `k > 0` leaves the
  verdict unchanged; rows drawn at random with the total set to their sum are
  `consistent`. Oracle: requirement 4.
- **C2**: `check_growth`.
  - current 112, prior 100, growth 0.12 → `consistent`; growth 0.13 → `inconsistent`,
    `"mismatch"`, `expected` 0.12; growth 0.1204 → `consistent` (|0.0004| ≤ 5e-4).
  - Prior −5,745 and current 1,304 (Micron FY2023→FY2024 operating income, in
    millions, docs/specs/pct-change-nm.md): growth `None` → `consistent`,
    `"not_meaningful"`; growth −1.227 or 1.227 → `inconsistent`, `"not_meaningful"`.
    Prior 0 behaves the same.
  - Prior positive, growth `None` → `not_applicable`, `"missing_value"`.

  Property: for random `current` and `prior > 0`, a growth computed in the test as
  `(current − prior) / prior` is `consistent`; any growth value with `prior <= 0` is
  `inconsistent`. Oracle: requirement 5 and S4.
- **C3**: `check_screen_order`.
  - desc rows A, B, C with lookups 30, 20, 10 → `consistent`; rows A, C, B →
    `inconsistent`, `"misordered"`, `inversions == [["C", "B"]]`.
  - A tie: rows B, A with lookups 20 and 20 → `consistent`, both orders.
  - asc: rows C, B, A with 10, 20, 30 → `consistent`.
  - A row missing from `lookups` and a lookup with value `None` are `skipped` and don't
    fail the check; one compared row → `not_applicable`, `"too_few_rows"`.
  - `order="up"` → `ValueError`.

  Metamorphic: rows sorted by random distinct lookup values descending are `consistent`
  for `desc` and, reversed, `consistent` for `asc` and `inconsistent` for `desc` (when at
  least 2 rows), with `len(inversions) == n(n−1)/2` for the full reversal. Oracle:
  requirement 6.
- **C4**: `check_q4`.
  - FY 400, 9M 290, Q4 110 → `consistent`, `expected` 110, `tolerance` 0.4; Q4 111 →
    `inconsistent`, `"mismatch"`; Q4 110.3 → `consistent`.

  Property: for random FY and 9M, Q4 = FY − 9M is `consistent`; adding more than the
  tolerance to Q4 makes it `inconsistent`. Oracle: requirement 7.
- **C5**: the unit policy, across the four checks.
  - USD rows vs a TWD total → `inconsistent`, `"unit_mismatch"`, `observed` `None`; the
    same for growth (USD current, TWD prior), Q4 (one of three in TWD) and a screen (a
    row's unit differs from its lookup's, or lookups in two units).
  - A `None` unit → `not_applicable`, `"missing_unit"`; a `None` value →
    `"missing_value"`, and it wins over a unit mismatch.
  - The growth step's own unit (`"pure"`) is ignored.

  Property: for random values, giving one compared amount a different unit never yields
  `consistent`. Oracle: requirement 3 and S6.
- **C6**: `plan_slots`.
  - The exact slot list for a hand-built YoY plan (two lookups with string ciks, one
    compute `(r24 - r23) / r23` → `($0-$1)/$1`) and for a screen step (`dimension`
    `"NONE"`, `order` `"DESC"`) and a breakdown step.
  - Normalisation: ticker `" aapl "` → `"AAPL"`, cik `320193` → `"0000320193"`,
    dimensions `{" Geography ": "China "}` → `{"geography": "china"}`, a missing
    `fiscal_period` → `"FY"`.
  - A plan without `steps` → `ValueError`.

  Metamorphic: renaming step ids consistently (in `id` and every `expr`), changing
  `section_hint`, or reordering keys inside a step leaves `plan_slots` unchanged. Oracle:
  requirement 9.
- **C7**: `check_plan_perturbation`.
  - AAPL→MSFT on a YoY plan, with ciks given, ticker and cik changed in both lookups →
    `consistent`, `changed` listing 4 slots. Without ciks, any cik change on those steps
    is accepted.
  - The same with one lookup still on AAPL → `inconsistent`, `"wrong_change"`; with
    `concept_family` also changed → `"unexpected_change"`.
  - Year 2024→2023 on a YoY plan (fy 2024 and 2023 → 2023 and 2022) → `consistent`;
    only the first fy shifted → `"wrong_change"`.
  - An extra step → `"step_count"`; a lookup turned into a narrative → `"op_mismatch"`;
    a screen plan under a ticker swap, unchanged → `not_applicable`, `"slot_absent"`; an
    `{"error": ...}` plan → `"no_plan"`; unknown kind and `from == to` → `ValueError`.

  Metamorphic: for random lookup plans, applying the perturbation to the plan in the test
  (and renaming step ids) is `consistent`; additionally changing any one non-perturbed
  slot is `inconsistent`. Oracle: requirements 8–10.
- **C8**: `check_answer_perturbation`.
  - [100.0] vs [250.0], lookups not equal → `consistent`; [100.0] vs [100.0] →
    `inconsistent`, `"unchanged"`; with `lookups_equal=True`: equal → `consistent`,
    different → `"changed"`; an empty list → `not_applicable`, `"missing_value"`;
    different lengths count as different answers.

  Property: for random values, `lookups_equal=False` and identical lists is
  `inconsistent`; swapping the two lists never changes the verdict. Oracle: requirement 11.
- **C9**: `perturb_ticker`.
  - "What was Apple's revenue in fiscal 2024?" with AAPL→MSFT and names
    `{"AAPL": ["Apple Inc.", "Apple"], "MSFT": ["Microsoft Corporation", "Microsoft"]}`
    → "What was Microsoft Corporation's revenue in fiscal 2024?".
  - "AAPL vs Apple Inc. margins" → "MSFT vs Microsoft Corporation margins" (longest form
    wins: "Apple Inc." is one match).
  - "Pineapple revenue" and lower-case "aapl" don't match → `None`; a question naming
    both Apple and Microsoft → `None`; `from == to` → `ValueError`.
  - A `to` ticker without names: a name match is replaced by the ticker.

  Property: with ticker-only forms (empty `names`), perturbing A→B then B→A returns the
  original question; the count of `to` forms in the output equals the count of `from`
  forms in the input. Oracle: requirement 12.
- **C10**: `perturb_year`.
  - "Revenue growth in 2024 vs 2023?" 2024→2023 → "Revenue growth in 2023 vs 2022?".
  - "FY24 and FY2024" 2024→2025 → "FY25 and FY2025"; "fy24" → "fy25".
  - No mention of the from year → `None`; "20245" and "FY245" are not mentions;
    a shift past 2099 → `None`; `from == to` → `ValueError`.

  Property: for random years and deltas within range, shifting by `delta` then by
  `−delta` returns the original question. Oracle: requirement 13.

## Interface

New, stubbed in the spec PR. New files carry no module docstring (the stub lint allows
none) and no constants. Everything is a plain `dict` (no `TypedDict`, no dataclass).

`src/eval/keyfree/__init__.py`: comments only.

`src/eval/keyfree/metamorphic.py`:
- `check_breakdown_sum(rows: list[dict], total: dict, rel_tol: float = 1e-3) -> dict`
- `check_growth(growth: dict, current: dict, prior: dict, abs_tol: float = 5e-4) -> dict`
- `check_screen_order(screen_rows: list[dict], lookups: dict[str, dict], order: str = "desc", rel_tol: float = 1e-9) -> dict`
- `check_q4(q4: dict, fy: dict, nine_months: dict, rel_tol: float = 1e-3) -> dict`

`src/eval/keyfree/perturbation.py`:
- `plan_slots(plan: dict) -> list[dict]`
- `check_plan_perturbation(original_plan: dict, perturbed_plan: dict, perturbation: dict) -> dict`
- `check_answer_perturbation(original_values: list[float], perturbed_values: list[float], lookups_equal: bool, rel_tol: float = 1e-9) -> dict`
- `perturb_ticker(question: str, from_ticker: str, to_ticker: str, names: dict[str, list[str]]) -> dict | None`
- `perturb_year(question: str, from_year: int, to_year: int) -> dict | None`

## Notes

- **Oracles** are the closed forms above, the S4 growth definition and S6 unit rule of
  [`docs/design/XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md), and the
  plan JSON shape of `src/retriever/xbrl/plan_validator.py` and the plan-generator prompt.
  The one public figure (Micron operating income FY2023 −$5,745M → FY2024 $1,304M) is the
  one already used by docs/specs/pct-change-nm.md.
- **Fractions, not percent.** `check_growth` takes the growth as a fraction, the
  calculator's `(a - b) / b` form. A runner reading a `* 100` expression divides by 100
  first; that normalisation belongs to the runner, not the check.
- **Why `lookups_equal` is an input.** Whether two filers' or years' underlying lookups
  are equal is itself a lookup (two SQL reads); the check stays pure, and the runner
  supplies it.
- **Out of scope (the later runner change):** running the pipeline on perturbed
  questions, extracting amounts and plans from traces (`StepResult`,
  `audit.query_event`), persisting verdicts, and the calculation-linkbase engine of plan
  §6.6.
- **Year shift, not year swap.** A year perturbation shifts every year in the question
  and every `fy` in the plan by the same delta, so a YoY plan stays a YoY plan. A fixed
  date slot (`as_of`) is not shifted and must stay equal; a question with an `as_of`
  date is a poor perturbation candidate.
