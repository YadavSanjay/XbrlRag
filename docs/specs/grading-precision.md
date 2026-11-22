# grading-precision — compare numbers at their stated precision, on what each answer states

Area D of the test strategy ("Number comparison", approved 2026-11-07). Today a v2 case
is matched by one relative tolerance (`rel_tol`, 1e-4), which is wrong in both
directions: "$93.4 million" fails a key tagged to the million (93,000,000 at
`decimals` -6), while "$20.83 billion" passes a key of 20,832,000,000 tagged to the
million. And the arms are not graded on the same thing: XbrlRag passes when *any*
execution-step value matches (the SQL value, at full precision), while a text arm is
graded on the numbers its reply states. This change grades every v2 case at the
precision its key carries, and every arm on the numbers its answer states.

## Requirement

1. **Precision rules (R1).** A v2 case (one with a `shape:` tag) compares a produced
   number with its key under the rule of its metric kind:

   | Kind | Shapes (and unit) | Compared at |
   |---|---|---|
   | `amount` | `lookup`, `point_in_time`, `trend`, `screen.top`, `screen.bottom`, `peer_relative`, unit not per-share or shares | the key fact's `decimals` (quantum `10**-decimals`) |
   | `shares` | same shapes, unit `shares` | the key fact's `decimals` |
   | `per_share` | any amount or aggregate shape whose unit ends in `/shares` | cents (quantum 0.01) |
   | `aggregate` | `compute.difference`, `screen.sum`, `screen.median` (unit not per-share) | the coarsest `decimals` among the key's inputs |
   | `ratio` | `compute.ratio` | 3 significant figures of the key |
   | `percent` | `compute.yoy_growth`, `compute.cagr` | 0.1 percentage point (quantum 0.001 on the fraction) |

   - **Where the decimals come from.** The case already carries them: the v2 build
     writes every key input, with its `decimals`, under `case.key["inputs"]`; frozen
     suites need no rebuild, which is what lets stored runs be re-graded at no cost. A
     scalar case uses the input whose `value` equals `expected_value` (first such; if none,
     the coarsest among the inputs). A row (screen or trend) uses its own `decimals` key
     when it has one, otherwise the input with the same `value` and `period_end`. The
     unit is `case.unit`, else the first input's `unit`.
   - **`INF` decimals** (an exact fact) compare exactly. For an aggregate, only integer
     decimals count towards "the coarsest"; with none, all-`INF` inputs compare exactly.
   - **Missing decimals** (`None`: companyfacts carries no precision, and a key the fact
     store did not verify, typically from a 10-Q, has none) fall back to the case's own
     `rel_tol`. Rationale: there is no stated precision to apply; "exact" would fail
     "$182.64 billion" for a key of 182,637,000,000 that was almost certainly tagged to the
     million, and inferring decimals from trailing zeros would make 3,000,000,000 a
     `decimals` -9 key that "$3.4 billion" passes. The case's `rel_tol` (1e-4 in v2) still
     fails every statement coarser than about four significant figures. A per-share,
     ratio or percent case never needs decimals.
   - **Cases without a `shape:` tag** (the legacy SQL-derived suite, H-X, hand-built
     cases) are graded exactly as today, by `rel_tol`. Alternate readings
     (`alt_readings`, docs/specs/shares-outstanding-reading.md) and "not meaningful"
     cases (docs/specs/pct-change-nm.md) keep their `rel_tol` matching. A precision-graded
     case ignores `alt_values`: the ratio and percent rules read a bare percentage
     themselves (below).

2. **The comparison.** For a produced number `A`, a key `T` and a quantum `q` (a power
   of ten; for a ratio `q = 10**(floor(log10|T|) - 2)`):
   - both are first normalised to 15 significant figures (binary float noise:
     0.2799999999999998 reads 0.28);
   - `round(x, q)` rounds half away from zero to a multiple of `q`;
   - the answer's own precision `s(A)` is the place value of `A`'s last stated digit
     (for a float, of its shortest repr; an integer-valued float has `s = 1`);
   - `A` passes when `round(A, q) == round(T, q)`, or when `s(A) < q` and
     `A == round(T, s(A))` (a more precise answer that is the key correctly rounded at its
     own precision: 0.07055 for a ratio of 0.0705460 passes, though it rounds to 0.0706).

   Consequences, all intended: an answer stated **less** precisely than its rule fails
   unless the digits it drops are zeros in the key ("$1.2 billion" fails a `decimals` -6
   key of 1,234,000,000; "$16.7" passes an EPS of 16.70; "0.14" fails a ratio of 0.140746
   while 0.1407 passes; "14%" fails 14.07% while 14.1% passes). A **ratio** or **percent**
   key also accepts its percentage written without the scale: `A` is compared both with
   `T` at `q` and with `100·T` at `100·q`. `exact` passes when `A == T`; the `rel_tol`
   fallback passes when `|A − T| <= rel_tol·|T|` (`|A| < 1` when `T == 0`), as
   `close_enough` does.

3. **Stated precision is read from the answer (R2).** A parser returns, for each amount a
   text states, its value, its stated quantum, whether it was a percentage and its
   direction word. Candidates, skips (years, fiscal labels, dates, form names), scale
   words and letters (`thousand`, `million`/`mn`/`mm`/`m`, `billion`/`bn`/`b`,
   `trillion`/`tn`/`t`), currency prefixes (`$`, `US$`, `NT$`, `USD`, `TWD`), signs and
   parentheses are those of `extract_numbers` (docs/specs/arm-neutral-grading.md C4). The
   quantum is the place value of the last stated digit times the scale ("$1.2 billion"
   1e8, "$1,234 million" 1e6, "1,234,000,000" 1, "14.07%" 0.01).

4. **Signs and readings (R4).** A direction word governs a number when it immediately
   precedes it (only spaces and the fillers `by`, `of`, `a`, `an`, `about`, `around`,
   `approximately`, `roughly`, `nearly`, `some` between) or immediately follows it
   (only spaces between). Down words are `v2.DOWN_WORDS` plus `down`; up words are
   `v2.UP_WORDS` plus `up`. A number governed by a down word is negative (`-|v|`): "declined
   5.2%" and "a 5.2% decline" state -5.2, "fell by $514 million" states -514,000,000.
   "fell to $95 billion" states +95 billion (`to` is not a filler). A sign-flipped value
   fails like any other wrong number. The free-text fallback of `parse_structured_answer`
   reads its numbers this way.

5. **Symmetric grading (R3).** Every arm is graded on the values its answer states.
   - XbrlRag's final answer is its `answer_summary` followed by the `statement` of each
     `answer_details` entry (the statements the faithfulness verifier kept), one per
     line; an absent `answer_details` is empty. `XbrlRagFullSystem.answer` sets `text` to
     it and `numeric_values` to the values it states (requirements 3 and 4).
   - XbrlRag's `rows` are the rows its statements state: walking `answer_details` in
     order, a statement that cites (`fact_ids`, int or digit string) an OK screen row's
     `fact_id`, `citation.fact_id` or one of its `component_fact_ids` states that row; its
     value is the number the statement states nearest the row's execution value; a
     statement stating no number states no row; each ticker once (first statement wins).
     Each row is `{"ticker": <row ticker>, "value": <stated value>}`.
   - Execution values remain the basis of grounding only: `fact_ids` and the refusal
     signals are unchanged.
   - So the same text, answered by XbrlRag or by a text arm without a JSON object, gets
     the same value verdict; and changing XbrlRag's stated number while keeping its
     `execution_result` changes its grade.

6. **Screens and breakdowns (R5).** A screen's value agreement compares each shared
   entity's produced value with its expected row under that row's rule (requirement 1),
   and a trend matches each period under its row's rule; the other screen signals and
   all thresholds are unchanged. The 1e-3 / 1e-4 defaults stay only for cases without a
   `shape:` tag. `choose_partition` (src/retriever/xbrl/xbrl_factstore_breakdown.py) is
   not a grader: it keeps its own 1e-4 tolerance, so partition membership, and with it
   the breakdown suite's ground truth, does not change. Breakdown-op cases live only in
   the legacy suite and keep `rel_tol`.

7. **Grader version.** These rules change the scorecard, so `grader_version()` is `"4"`.

8. **Imprecise answers (decided 2026-11-07 by the maintainer).** An answer that fails
   only because it is stated less precisely than its rule gets its own outcome,
   `Grade.IMPRECISE` (value `"IMPRECISE"`), a new member of the existing `Grade` enum
   next to `HEDGED_MISS`. It is neither correct nor wrong.
   - **The stated quantum of a produced value** `v` is the `quantum` of the first
     `StatedNumber` of `extract_stated_numbers(answer.text)` whose `value` equals `v`
     (rel 1e-12, or both zero); with none (an empty text, a JSON number the text does
     not state as prose) `v` has no stated quantum. XbrlRag's `text` is its final answer
     (requirement 5), so both arms are read the same way.
   - **`precision_outcome(value, stated_quantum, expected, rule)`** returns `"match"`
     when `matches_at_precision` passes; otherwise `"imprecise"` when the rule's kind is
     not `legacy`, `stated_quantum` is not `None`, `round(expected, s)` is not zero and
     `value == round(expected, s)` with `s = stated_quantum` (both normalised to 15
     significant figures, rounding as in requirement 2; for a `ratio` or `percent` rule
     also `value == round(100·expected, s)`, the percentage reading); otherwise
     `"wrong"`. Because a value equal to the key rounded at a place no coarser than the
     rule always matches, "imprecise" implies the stated precision is coarser than the
     rule's. A number that disagrees even at its own stated precision ("$94 million" for
     93,000,000, "$5.34" for -5.34) stays wrong; so does a zero statement of a non-zero
     key. The `rel_tol` fallback and `exact` rules can be imprecise too ("$182.6
     billion" for an undecimalled 182,637,000,000; "1.2" for an exact 1.19).
   - **Scope: scalar v2 cases** (every case `grade_scalar` grades: lookups,
     point-in-time lookups, compute shapes, screen sums and medians). When no produced
     value matches, the answer is not hedged and some produced value's outcome is
     `"imprecise"`, the grade is `IMPRECISE` (`passed` False, `matched_value` None,
     reason starting `imprecise:`), whatever its citation. A hedged answer stays
     `HEDGED_MISS`; a case without a `shape:` tag is graded as today (never
     `IMPRECISE`). Screens, trends and peer-relative rows keep their per-row booleans:
     a row value carries no stated quantum, so an imprecise row is a disagreeing row
     (open, below).
   - **How it counts.** `value_correct`, `confident_wrong` and `silent_wrong` are all
     False for `IMPRECISE`, so it is excluded from `confident_wrong_rate` and
     `silent_wrong_rate` and does not count towards `value_accuracy` or `accuracy`.
     `Metrics` gains `n_imprecise` (the count) and `imprecise_rate` (`n_imprecise / n`),
     both in `to_dict()`; the stored `eval_run` row gets them as additive nullable
     columns, as grader version 3's columns were added.

9. **Loss words (decided 2026-11-07 by the maintainer).** For an income-type metric, a
   number a loss word governs is negative: "a loss of $5.34", "net loss of $1.2
   billion", "loss per share of $0.40" state -5.34, -1.2e9 and -0.40.
   - **Loss words** are `loss` and `losses` (any case), taken together with a directly
     following `per share`, `per diluted share`, `per basic share` or `per basic and
     diluted share`. Not loss words: one in parentheses ("net income (loss) of $5.34"
     states +5.34), one directly after `or`, `and` or `/` ("gain or loss", "profit and
     loss", "income/loss"), and one directly after `impairment` ("impairment loss").
   - **Governs** as in requirement 4: the loss word immediately precedes the number with
     only spaces, a colon, requirement 4's fillers and `was`, `were`, `is`, `totaled`,
     `totalled` between ("a net loss of $1.2 billion", "net loss was $1.2 billion",
     "diluted loss per share was $(5.34)"), or immediately follows it with only spaces
     and at most one of `net`, `operating`, `pretax`, `pre-tax`, `comprehensive` between
     ("$1.2 billion net loss", "$5.34 loss per share").
   - **The parser marks, the grader signs.** `StatedNumber` gains a fifth field, `loss:
     bool`, True when a loss word governs the number; its `value` is unchanged (sign,
     parentheses and direction words as before), because the parser does not know the
     metric.
   - **Scope.** `is_income_metric(case)` is True when the local name (after the prefix)
     of the case's concept (`case.concept`, else the first key input's `concept`)
     contains `IncomeLoss`, `ProfitLoss` or `EarningsLossPerShare`, or starts with
     `EarningsPerShare` or `ComprehensiveIncome` (net, operating, pretax and
     comprehensive income, EPS, IFRS profit and EPS). The loss reading applies only to
     a v2 scalar case of rule kind `amount` or `per_share` (lookups and point-in-time
     lookups) whose metric is income-type: each produced value equal (rel 1e-12) to the
     value of a loss-marked stated number of `answer.text` is graded as `-|v|`. Every
     other case reads "loss" unsigned: an impairment metric's "impairment loss of $2
     billion" is +2e9; growth, CAGR, ratio and difference cases keep requirement 4's
     direction words only (the sign of a change is a direction, not a loss); legacy
     cases are unchanged.
   - **No double flip.** `-|v|` is idempotent: "a loss of -$5.34", "a loss of $(5.34)",
     "net loss per share was (5.34)" and "a loss of $5.34" all state -5.34 for an
     income-type metric.

## Acceptance criteria

- **C1**: `precision_rule(case, row=None)` returns the rule of requirement 1 as
  `PrecisionRule(kind, compare, quantum, sig_figs, rel_tol)`, `compare` being `quantum`,
  `sig_figs`, `exact` or `rel_tol`, and the unused fields `None`. On real dev cases:
  INTC operating income lookup `("amount", "quantum", 1e6)`; TSM cash `("amount",
  "quantum", 1e5)`; NVDA EPS and MSFT DPS (decimals missing) `("per_share", "quantum",
  0.01)`; CRM and AAPL share counts `("shares", "quantum", 1e6 | 1e3)`; NFLX and TSM R&D
  ratios `("ratio", "sig_figs", sig_figs=3)`; MSFT tax growth, QCOM CAGR `("percent",
  "quantum", 0.001)`; the dividends sum and equity median `("aggregate", "quantum", 1e6)`;
  GOOGL's difference over decimals -8 and missing `("aggregate", "quantum", 1e8)`; MSFT's
  DPS difference `("per_share", "quantum", 0.01)`; per row, the goodwill screen's MSFT
  row (missing) `("amount", "rel_tol", rel_tol=1e-4)` and NVDA row `("amount", "quantum",
  1e6)`, TSM's gross-profit trend rows `1e5`, UNP's share trend rows `("shares",
  "rel_tol", 1e-4)`. An `INF` lookup is `("amount", "exact")`; a case without a `shape:`
  tag is `("legacy", "rel_tol", rel_tol=case.rel_tol)`. Metamorphic: the aggregate rule
  does not depend on the inputs' order, adding a missing-decimals input never changes it,
  and adding an integer input coarser than every other makes it that input's quantum.
  Quanta within rel 1e-12. Oracle: requirement 1's table read against each fixture's
  `key.inputs`.
- **C2**: `matches_at_precision(value, expected, rule)` follows requirement 2. Point
  cases (hand-rounded in the tests): the maintainer's examples (1,234,000,000 at -6:
  "1.2e9" fails, 1.234e9 passes; ratio 0.140746: 0.1407 passes, 0.14 fails; percent
  0.1407: 14.1 passes, 14 fails); EPS 16.70: 16.7 passes, 16.71 fails; TSM R&D ratio:
  7.05, 0.0705 and 0.07055 pass, 7.1 and 0.0706 fail; `exact`: 1.19 passes, 1.2 fails;
  `rel_tol` 1e-4: 182.64e9 passes 182,637,000,000, 182.6e9 fails. Properties: the key
  rounded at its quantum always passes and one quantum beyond always fails; any amount
  within less than half a quantum of a reported key passes; the key rounded to any place
  finer than the rule passes; the result is unchanged by adding binary noise below 15
  significant figures to either side. Oracle: closed-form decimal rounding in the test.
- **C3**: `extract_stated_numbers(text)` returns `StatedNumber(value, quantum, percent,
  direction, loss)` per amount (`loss` is C10's), in order: "$1.2 billion" `(1.2e9, 1e8, False, 0)`, "$1,234
  million" `(1.234e9, 1e6)`, "NT$1,342,814.1 million" `(1,342,814,100,000, 1e5)`, "93m"
  `(9.3e7, 1e6)`, "14.07%" `(14.07, 0.01, True)`, "$(5.34)" `(-5.34, 0.01)`, "declined
  5.2%" `(-5.2, 0.1, True, -1)`, "a 5.2% decline" `-5.2`, "fell by $514 million"
  `(-5.14e8, 1e6, False, -1)`, "grew 10.9%" `(10.9, 0.1, True, 1)`, "fell to $95
  billion" `(9.5e10, 1e9, False, 0)`; FY2024, 2024-12-31, 10-K are skipped. Values within
  rel 1e-12, quanta within rel 1e-9. Properties: for a text with no direction word the
  values equal `extract_numbers(text)`; scale-word restatements of one amount ("X
  million", "X/1000 billion", the full number with commas) state the same value with
  quanta 1e6, 1e6 and 1. Oracle: requirements 3 and 4, hand-read texts.
- **C4**: `grade_case` grades a v2 scalar case at its rule (a less precise statement
  that agrees at its own precision is C9's IMPRECISE). On real dev cases: INTC
  93,000,000 at -6: "$93.4 million" CORRECT, "$94 million" INCORRECT; TSM R&D ratio:
  "7.05%" CORRECT, "7.1%" IMPRECISE; MSFT tax growth: "10.9%" CORRECT, "11%" IMPRECISE;
  QCOM CAGR "30.4%" CORRECT, "30%" IMPRECISE; NFLX R&D ratio "0.0793" CORRECT, "0.079"
  IMPRECISE; the dividends sum "$35.223 billion" value-correct, "$35.2 billion" IMPRECISE
  and "$35.225 billion" (within 1e-4, wrong at the million) INCORRECT; GOOGL difference
  "$2.64 billion" CORRECT (rounds to 2.6e9 at -8), "$2.7 billion" INCORRECT; TSM cash
  "NT$1,342,814.1 million" CORRECT, "NT$1,342,814 million" IMPRECISE; AAPL shares (-3)
  "16.216 billion" IMPRECISE, "16,215,963 thousand" CORRECT; CRM shares "962 million"
  CORRECT; ADBE EPS "$16.7" CORRECT, NVDA EPS "$1.2" IMPRECISE and "$1.194" CORRECT
  (rounds to the cent); the equity median (decimals -6) "$182.637 billion" value-correct,
  "$182.64 billion" IMPRECISE; MSFT DPS (missing decimals, per-share) "$3" CORRECT. A
  case without a `shape:` tag keeps `rel_tol` ("$391.0 billion" stays CORRECT for
  391,035,000,000; under a decimals -6 v2 key it is IMPRECISE).
  Properties: for an amount key `m·10**k` at decimals `-k` (m < 4000) an answer `0.4` of a
  quantum away is CORRECT and one quantum away INCORRECT; restating the answer by scale
  word or rewording its sentence never changes the grade. Oracle: requirement 2,
  hand-rounded keys.
- **C5**: signs. Through `parse_structured_answer`'s free-text fallback: META interest
  income growth (-15.65%) "declined 15.7%" and "a 15.7% decline" CORRECT, "grew 15.7%"
  and "15.7%" INCORRECT; TSLA pretax CAGR (-27.27%) "a decline of 27.3% a year" CORRECT;
  P&G difference (-514,000,000) "decreased by $514 million" and "$(514) million" CORRECT,
  "$514 million" INCORRECT; MU EPS as originally reported (-5.34) "$(5.34)" CORRECT,
  "$5.34" INCORRECT. Property: for any growth key, the right magnitude with the opposite
  direction word is INCORRECT. Oracle: requirement 4.
- **C6**: screens and trends per row. Goodwill top 3 (MSFT 119,651,000,000 missing
  decimals, NVDA 20,832,000,000 at -6): rows MSFT 119.65e9 and NVDA 20.83e9 give value
  agreement 0.5 (NVDA fails at the million though within 1e-4), NVDA 20.832e9 gives 1.0;
  semis net income top 3 rows stated to the million pass; TSM gross-profit trend: every
  year "NT$…,…,….x million" matches, "NT$1,348.35 billion" for FY2022 does not; META
  equity trend (FY2023 at -6, FY2024 and FY2025 missing): "$153.17 billion" fails FY2023
  (to the million it is 153,168), "$182.64 billion" and "$217.24 billion" pass FY2024 and
  FY2025 within 1e-4. Property: perturbing one
  screen row by one quantum lowers value agreement by exactly 1/n. Oracle: requirement 6,
  hand-rounded rows.
- **C7**: XbrlRag is graded on its final answer. `final_answer_text(resp)` is the summary
  and the kept statements, one per line; `answer_rows(resp)` maps each citing statement
  to its row as requirement 5 says; `XbrlRagFullSystem.answer` over an injected pipeline
  returns that text, its stated values and its stated rows, with `fact_ids` from
  `execution_result`. Metamorphic: the same text graded as XbrlRag's answer and as a text
  arm's reply gets the same `value_correct`; replacing the stated number while keeping
  `execution_result` changes XbrlRag's grade (a full-precision execution value no
  longer rescues "$1.2 billion"; "$95 million" for INTC's 93 million is INCORRECT and
  "$0.1 billion" IMPRECISE). Oracle: requirement 5, hand-built responses.
- **C8**: `grader_version()` is `"4"` and every scorecard carries it. Oracle: requirement 7.
- **C9**: imprecise answers (requirement 8). `precision_outcome` on real dev rules: TSM
  cash (1e5) 1,342,814e6 stated at 1e6 `"imprecise"`, 1,342,814.1e6 at 1e5 `"match"`,
  1,342,815e6 at 1e6 `"wrong"`, 1,342,814e6 with no stated quantum `"wrong"`; TSM R&D
  ratio 7.1 at 0.1 and 0.1 at 0.1 `"imprecise"`, 7.2 at 0.1 `"wrong"`; MU EPS -5.3 at 0.1
  `"imprecise"`, 5.3 at 0.1 `"wrong"`; INTC 0 at 1e9 `"wrong"` (zero for a non-zero
  key); the goodwill screen's MSFT row (`rel_tol`) 119.7e9 at 1e8 `"imprecise"`; a
  legacy rule never `"imprecise"`. `grade_case`: INTC "$0.1 billion" IMPRECISE, "$0
  billion" and "$94 million" INCORRECT; GOOGL difference (2.6e9 at -8) "$3 billion"
  IMPRECISE; MU EPS "$(5.3)" IMPRECISE, "$5.3" INCORRECT; a hedged imprecise answer
  HEDGED_MISS; an uncited imprecise answer IMPRECISE; an untagged case's "$390 billion"
  for 391,035,000,000 INCORRECT. An IMPRECISE grade has `value_correct`,
  `confident_wrong` and `silent_wrong` False; `Metrics.compute` over one CORRECT, one
  IMPRECISE and one INCORRECT case gives `n_imprecise` 1, `imprecise_rate` 1/3,
  `confident_wrong_rate` 1/3, `value_accuracy` 1/3, `accuracy` 1/3, also in
  `to_dict()`. Properties: a key of M million at -6 (M not a multiple of 100) stated in
  tenths of a billion, correctly rounded, is IMPRECISE and never confident-wrong,
  value-correct or silent-wrong, and 0.1 billion further INCORRECT; a stated quantum no
  coarser than the rule's quantum never gives `"imprecise"`. Oracle: requirement 8,
  decimal rounding by hand in the tests.
- **C10**: loss words (requirement 9). `extract_stated_numbers` sets `loss`: "a net loss
  of $1.2 billion" `(1.2e9, 1e8, loss=True)`, "a loss per share of $0.40" `(0.40, 0.01,
  True)`, "Diluted loss per share was $(5.34)" `(-5.34, True)`, "$1.2 billion net loss"
  True, "$5.34 loss per share" True; False for "net income (loss) per share of $5.34",
  "an impairment loss of $2 billion", "gain or loss of $3 million", "revenue of $5
  billion" and "the loss widened while revenue was $5 billion". `is_income_metric` is
  True for MU EPS, INTC operating income, TSLA net income and the IFRS `ProfitLoss`,
  `BasicEarningsLossPerShare` and US-GAAP `ComprehensiveIncomeNetOfTax` concepts; False
  for NFLX G&A, TSM cash, P&G other non-operating income and `GoodwillImpairmentLoss`.
  `grade_case` on MU EPS (-5.34): "a loss per share of $5.34", "a diluted loss per share
  of $5.34", "a net loss of $(5.34) per share", "a loss of -$5.34 per share" and "$5.34
  loss per share" CORRECT; "net income (loss) per share of $5.34" INCORRECT; "a loss per
  share of $5.3" IMPRECISE. INTC operating income (+93,000,000): "an operating loss of
  $93 million" INCORRECT, "operating income of $93 million" CORRECT. NFLX G&A (not
  income-type): "a loss of $1,720.285 million" CORRECT (unsigned). Properties: for an
  income-type case with key ±c cents, every loss-worded form (plain, parenthesised,
  minus-signed, trailing) grades alike, CORRECT exactly when the key is negative, and the
  loss-free form CORRECT exactly when it is positive; for a non-income case a
  loss-worded statement grades as the bare number does. Oracle: requirement 9, hand-read
  texts.

## Interface

The two `NamedTuple` classes are committed as empty stubs (the stub rules allow no class
body); the implementation declares their fields, in the order below. Tests only read them.

- `src.eval.core.answer_parsing.StatedNumber(typing.NamedTuple)`: `value: float`,
  `quantum: float`, `percent: bool`, `direction: int` (-1, 0 or 1), `loss: bool`.
- `src.eval.core.answer_parsing.extract_stated_numbers(text: str) -> list[StatedNumber]`
- `src.eval.core.graders.precision.PrecisionRule(NamedTuple)`: `kind: str`,
  `compare: str`, `quantum: float | None`, `sig_figs: int | None`, `rel_tol: float | None`.
- `src.eval.core.graders.precision.precision_rule(case: EvalCase, row: dict | None = None) -> PrecisionRule`
- `src.eval.core.graders.precision.matches_at_precision(value: float, expected: float, rule: PrecisionRule) -> bool`
- `src.eval.core.graders.precision.precision_outcome(value: float, stated_quantum: float | None, expected: float, rule: PrecisionRule) -> str`
  (`"match"`, `"imprecise"` or `"wrong"`)
- `src.eval.core.graders.precision.is_income_metric(case: EvalCase) -> bool`
- `src.eval.core.graders.base.Grade.IMPRECISE = "IMPRECISE"` (a new enum member; tests
  compare `grade.grade.value`, so the stub rules need no stub for it)
- `src.eval.core.report.Metrics`: new fields `n_imprecise: int = 0`,
  `imprecise_rate: float = 0.0`, both in `to_dict()`
- `src.eval.systems.xbrlrag_full.final_answer_text(resp: Any) -> str`
- `src.eval.systems.xbrlrag_full.answer_rows(resp: Any) -> list[dict]`
- Behaviour changes, no new signature: `grade_case` (scalar, screen and trend matching for
  `shape:` cases), `parse_structured_answer` (free-text fallback, requirement 4),
  `XbrlRagFullSystem.answer` (requirement 5), `GRADER_VERSION` "4", `grade_scalar`
  (requirements 8 and 9), `Metrics.compute` (requirement 8).

## Notes

- Fixtures: `test/spec/grading-precision/fixtures/dev_cases.json`, 27 cases copied
  verbatim from the frozen dev split `data/eval/v2_frozen/xbrl_v2_dev.json` (allowed: dev,
  never held-out). Values are SEC companyfacts (public domain). No restated key exists in
  the dev split (no case records a disagreement), so MU's diluted EPS "as presented in the
  FY2025 annual report" (a point-in-time key) stands in for the as-reported reading.
- Oracles are decimal hand calculations written in each test (e.g. 2,144 / 19,651 =
  0.10910 → 10.9%), never the suite's stored `expected_value` run through our code.
- Graduated tests updated on this branch: arm-neutral-grading C6 (`"3"` → `"4"`) and
  eval-regrade C5 (`source_grader_version` is the stored run's `"3"`, no longer the
  current version).
- Open for the maintainer, outside this spec: (a) a stored XbrlRag run keeps its
  execution-step `numeric_values` on re-grade (its statements are not in `produced`), so
  an R3-comparable XbrlRag number needs a new run; (b) a peer-relative answer now ranks by
  the rows its statements state, so an XbrlRag answer that names only the subject reads
  as rank 1; (c) an imprecise screen, trend or peer-relative row still counts as a
  disagreeing row (requirement 8 is scalar-only, since a row value carries no stated
  quantum); (d) loss words sign only income-type lookups, not trend or screen rows of
  an income metric.
- Decided 2026-11-07 by the maintainer (formerly open items (c) and (d)): an answer that
  fails only by being less precise is IMPRECISE, not confident-wrong and not correct
  (requirement 8, C9); "a loss of $5.34" reads as -5.34 for an income-type metric
  (requirement 9, C10).
- The only real negative-income key in the dev split is MU's FY2023 diluted EPS (-5.34);
  C10 uses it and INTC's positive operating income. NFLX G&A stands in for a metric where
  "loss" is unsigned; the `GoodwillImpairmentLoss`, `ProfitLoss`,
  `BasicEarningsLossPerShare` and `ComprehensiveIncomeNetOfTax` concepts in C10 are names
  only (no case is built on them).
