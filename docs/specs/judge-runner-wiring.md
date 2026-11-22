# judge-runner-wiring — the claim judge and the key-free checks inside an eval run

Part of the v2 eval rebuild ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.5). Three merged
changes left pieces that nothing yet runs together:

- [eval-judge](eval-judge.md) added `ClaimJudge` (`src/eval/core/graders/judge.py`), its
  family rule and the calibration gate `scores_reportable`, but nothing calls it in a run;
- [eval-run-columns](eval-run-columns.md) added `CaseGrade.judge_scores` and the
  `judge_version` run column, but nothing fills them;
- [keyfree-checks](keyfree-checks.md) added the metamorphic and perturbation checks as pure
  functions, and left "a runner that produces the inputs" to a later change.

This change is that wiring. It adds two modules, `src/eval/core/judge_wiring.py` (the judge
in a run) and `src/eval/keyfree/runner.py` (the key-free checks over a run), and teaches
`EvalCase` to carry a text case's key points. It doesn't change the judge, the checks, the
graders' verdicts, the `eval` schema or `scripts/eval/run_suite.py` (the CLI flag that
calls `run_graded` is a later change).

## Requirement

### Part A: the judge in a run

1. **Key points on a case.** A suite case may carry `"key_points"`: a list of strings,
   the reference answer's key points. `EvalCase.from_dict` reads it (absent means none) and
   `EvalCase.to_dict` writes it back when non-empty, so a suite round-trips.
   `case_key_points(case)` returns them as a new list, `[]` when there are none.
2. **Text-graded cases.** `is_text_graded(case)` is True exactly when `case.expected_text`
   is a string with non-whitespace content **and** `case_key_points(case)` is non-empty.
   Only text-graded cases are judged; every other case keeps `judge_scores = None`.
3. **Claims from an answer.** `answer_claims(answer)` turns a `SystemAnswer` into judge
   `Claim`s. It reads `answer.raw`, an `XbrlRagResponse` or any object with the same
   attributes, or a dict with the same keys (`answer_details`, `execution_result`,
   `narrative_chunks`):
   - **No structured statements** (`raw` is `None`, or its `answer_details` is missing or
     not a list): one claim `Claim("s0", answer.text, [])` when `answer.text` has
     non-whitespace content, else no claims. A baseline arm's prose is one uncited claim.
   - **Structured statements**: one claim per entry `i` of `answer_details` (0-based) that is
     a dict whose `statement` is a string with non-whitespace content; other entries are
     skipped without renumbering, so the claim id is `f"s{i}"` and ids stay unique. The
     claim's text is the statement, unchanged. An empty `answer_details` gives no claims.
   - **Cited passages**, in this order, each kept once (the first occurrence of an
     identical string wins):
     1. for each id in the entry's `fact_ids` (in order), compared as `str(id).strip()`:
        the first step in `execution_result["steps"]` whose `status` is `"OK"` and whose
        `step_id` or `citation["fact_id"]` equals it gives
        `json.dumps(step, sort_keys=True, default=str)`; failing that, the first row (in
        step order, then row order) of an OK step's `rows` whose `fact_id` equals it gives
        `json.dumps(row, sort_keys=True, default=str)`;
     2. for each id in the entry's `chunk_ids` (in order): the `text` of the first
        `narrative_chunks` entry whose `chunk_id` (as `str`) equals it.
   - An id that resolves to nothing is dropped, so a statement citing only unknown ids
     has no passages (the judge then counts it unsupported, eval-judge requirement 4).
4. **Judging one case.** `judge_case(judge, case, answer)` returns `None`, making no call,
   when the case is not text-graded or `answer.error` is set. Otherwise it calls
   `judge.judge(case.question, case.expected_text, case_key_points(case),
   answer_claims(answer))` exactly once and returns its `JudgeResult`. `RunHalt` from the
   judge propagates.
5. **Scores on the grade.** `judge_scores(result)` maps a `JudgeResult` to the dict stored
   on `CaseGrade.judge_scores`: exactly the four keys of `run_columns.JUDGE_COLUMNS`
   (`correctness`, `completeness`, `citation_support`, `unsupported_claims`) with the
   result's values. A `judge_error` result has all four `None` (its scores are never
   imputed); `judge_columns` accepts that dict and gives four `None` columns. So on a graded
   case `judge_scores is None` means "not judged", and a dict of four `None`s means "judged,
   judge_error".
6. **The family rule, before anything runs.** `check_judge_family(judge_model,
   generator_profile)` raises `ValueError` when the judge model's family
   (`model_family`, its provider in `configs/llm/llm.yaml`) is the family of any model of
   the generator profile (its `small`, `medium` and `frontier` models), and when the
   profile or the model is unknown; otherwise it returns `None`.
7. **The graded run.** `run_graded(cases, system, *, judge=None, generator_profile=None,
   calibration=(), metamorphic=True, config=None, **run_kwargs)` runs the cases like
   `EvalRunner(config).run(cases, system, **run_kwargs)` (same split check, per-kind
   concurrency, cost budget, resume and report) and in addition:
   - **refuses before any call**: with a `judge` and no `generator_profile`, or when
     `check_judge_family(judge.model, generator_profile)` raises, it raises `ValueError`
     with the system never called and no judge call made;
   - **judges each text-graded case once**, in the same task that answered it and before
     its result is returned, through `judge_case`; the grade's `judge_scores` is
     `judge_scores(result)` (a judge_error gives the four-`None` dict and adds
     `{case_id: result.error}` to `report.config["judge_errors"]`). Cases that are not
     text-graded, or whose answer has an error, are not judged and keep `judge_scores =
     None`. Cases carried over from `prior` are not judged again;
   - **charges the judge to the run's cost budget**: a judge call's cost is the cost the
     gateway recorded for it (configs/llm/pricing.yaml). It is added to the budget that
     `RunnerConfig.max_cost_usd` caps, at once, so with `max_workers=1` the case after the
     one whose answer-plus-judge cost reaches the cap is not attempted and the run is
     `ABORTED`. It is not added to the case's `cost_usd` (that stays the system's cost, so
     arms stay comparable); the run's total is `report.config["judge_cost_usd"]`;
   - **lets `RunHalt` from the judge propagate** out of `run_graded` (never a judge_error,
     never an `ERROR` grade);
   - **records the judge in the run config**: `report.config["judge_version"] =
     judge.judge_version` (so `run_columns` reports it), `"judge_errors"` (a dict, `{}`
     when none) and `"judge_cost_usd"` (`0.0` when nothing was judged), and
     `report.config["judge_summary"] = judge_summary(report, calibration)`. Without a judge
     none of the judge keys is set except `"judge_summary"`, whose status is `"none"`;
   - **runs the metamorphic pass** when `metamorphic` is True: `report.config["keyfree"] =
     metamorphic_pass(pairs)` over the `(case, answer)` pairs answered in this run (carried
     cases have no answer and are left out). With `metamorphic=False` the key is absent.
8. **The judge summary and the calibration gate.** `judge_summary(report, calibration)`
   returns a dict:
   - `judge_version`: `report.config.get("judge_version") or None`;
   - `reportable`: `judge_version is not None and scores_reportable(judge_version,
     calibration)`;
   - `status`: `"none"` without a judge version, else `"calibrated"` when reportable, else
     `"uncalibrated"`;
   - `n_judged`: results whose `judge_scores` is not `None`;
   - `n_judge_error`, `judge_error_case_ids` (sorted): judged results whose four scores are
     all `None`;
   - `means`: when reportable, the mean of each of the four scores over the judged results
     without error (each `None` when there are none); when not reportable, `None`. An
     uncalibrated judge's scores are never shown as numbers.

### Part B: the key-free checks over a run

9. **Common report shape.** Both passes return a dict with:
   - `checks`: per check name, `{"consistent": [...], "inconsistent": [...],
     "not_applicable": [...]}`, each a sorted list of subject case ids;
   - `counts`: per check name, the length of each of those lists;
   - `verdicts`: one dict per verdict, the check function's verdict dict plus `case_id`
     (the subject case) and `related_case_ids` (the partner cases, as below), in subject
     case order.

   Every check of the pass appears in `checks` and `counts`, with zero counts when it never
   applied. A case to which a check does not apply gets no verdict for it.
10. **Amounts from an answer** (read from `answer.raw`'s `execution_result["steps"]` and
    `plan["steps"]`, object or dict as in requirement 3). An answer with `error` set, or
    without steps, gives the missing amount (`{"value": None, "unit": None}`) / no rows.
    - a **lookup amount**: the first step with `op == "lookup"` and `status == "OK"`, as
      `{"value", "unit"}`;
    - **breakdown rows**: the rows of the first OK `breakdown` step, each `{"value",
      "unit"}` with the row's own `unit`, or the step's when the row has none;
    - a **growth amount**: the first OK `compute` step's value; when the plan step with the
      same id has an `expr` that, with all whitespace removed, contains `*100`, the value is
      divided by 100 (the check takes a fraction, keyfree-checks Notes);
    - **screen rows**: the rows of the first OK `screen` step; the ranking order is the
      first plan `screen` step's `order` lower-cased when it is `"asc"` or `"desc"`, else
      `"desc"`.
11. **`metamorphic_pass(pairs)`**, over `(EvalCase, SystemAnswer)` pairs of one run; checks
    `breakdown_sum`, `growth`, `screen_order`. A **partner lookup** of a subject case is a
    pair whose case has `kind == "lookup"`, the same `ticker` (case-insensitive, both set),
    the same `concept` (both set), the same `fiscal_period` and the `fy` the check asks
    for; among several, the first in pair order whose lookup amount has a value, else the
    first. A subject case whose answer has `error` set gets the verdict `{"check": <name>,
    "verdict": "not_applicable", "reason": "answer_error"}` (no check call), with the same
    `case_id` and `related_case_ids`, when the check otherwise applies.
    - **breakdown_sum** applies to a `breakdown` case with a partner lookup at its own
      `fy`: `check_breakdown_sum(rows, total)`; related ids `[lookup id]`.
    - **growth** applies to a `compute` case tagged `growth` with partner lookups at `fy`
      (current) and `fy - 1` (prior): `check_growth(growth, current, prior)`; related ids
      `[current id, prior id]`.
    - **screen_order** applies to a `screen` case whose first plan `screen` step has no
      dimensions (key absent, `None`, `{}` or `"NONE"`), no `axis`, and a `metric_kind` of
      `None` or `"value"`, when at least one lookup case matches its `concept`, `fy` and
      `fiscal_period` (any ticker): `check_screen_order(rows, lookups, order)` with
      `lookups` the partner lookup amount per upper-cased ticker; related ids the sorted ids
      of the lookups used.
    The pass makes no call and reads no store.
12. **`perturbation_twins(cases, *, seed, names, max_twins)`** produces perturbed twins,
    deterministically for a seed:
    - the ticker universe is the sorted set of the cases' upper-cased tickers;
    - for each case in order: first a **ticker twin** when the case has a ticker and the
      universe has another ticker: `to = random.Random(f"{seed}:{case.id}").choice(others)`
      with `others` the universe minus the case's ticker (sorted), and
      `perturb_ticker(case.question, case.ticker, to, names)`; then a **year twin** when
      the case has an `fy`: `perturb_year(case.question, case.fy, case.fy - 1)`. A perturbation
      that returns `None` gives no twin (no retry with another ticker);
    - each twin is `{"original": case, "twin": twin_case, "perturbation": p["perturbation"]}`
      where `twin_case` has id `f"{case.id}~ticker:{to}"` or `f"{case.id}~year:{case.fy - 1}"`,
      the perturbed question, the original's kind, suite, `fiscal_period`, `concept`,
      `unit`, `origin` and a copy of its tags (so the split is kept), the new `ticker`
      (and `cik=None`) or the new `fy`, and **no answer key** (`expected_value`,
      `expected_rows`, `expected_text` `None`; fact, chunk and alt lists empty);
    - the list is cut to its first `max_twins` items; `max_twins < 0` raises `ValueError`.
13. **`run_perturbation_checks(twins, system, *, lookups_equal, max_workers=4,
    max_cost_usd=None)`** runs each pair through the system and checks it:
    - each distinct original (by id) is answered once and reused; each twin once. A pair
      is its original's answer (when not yet answered) then its twin's;
    - at most `max_workers` pairs run at once (`max_workers < 1` raises `ValueError`);
    - `max_cost_usd` caps the summed `SystemAnswer.cost_usd`: a pair does not start once
      the spent total has reached the cap; its twin id goes to `skipped`. With
      `max_workers=1` pairs run in order and the cap is checked before each pair;
    - an exception from `system.answer` becomes `SystemAnswer(error=repr(exc))`, except
      `RunHalt`, which propagates;
    - checks `plan_perturbation` (`check_plan_perturbation(original plan, twin plan,
      perturbation)`, a missing plan passed as `{}`) and `answer_perturbation`
      (`check_answer_perturbation(original.numeric_values, twin.numeric_values,
      lookups_equal(original_case, twin_case))`), subject the twin id, related ids
      `[original id]`;
    - returns the common shape plus `skipped` (sorted twin ids), `n_pairs` (pairs run) and
      `cost_usd` (the summed answer cost).

## Acceptance criteria

- **C1**: key points. `EvalCase.from_dict` of a case with `key_points: ["a", "b"]` gives
  `case_key_points == ["a", "b"]`, and `to_dict` then `from_dict` keeps them; a case
  without them gives `[]` and `to_dict` has no `key_points` key. `is_text_graded` is True
  for non-blank `expected_text` plus key points, False when either is missing, empty or
  whitespace. Property: for random key-point lists and texts, the round trip keeps the
  list and `is_text_graded` equals the closed form. Oracle: requirements 1 and 2.
- **C2**: claims. A hand-built response with three statements (one citing a lookup step by
  `citation.fact_id` and a chunk, one citing a compute step by `step_id` and an unknown
  chunk, one with a blank statement) gives claims `s0`, `s1` with the passages built in the
  test from requirement 3 (`json.dumps(step, sort_keys=True, default=str)`, chunk text);
  a screen row cited by `fact_id` gives the row's dump; a duplicate citation is kept once;
  the same response as a dict and as an `XbrlRagResponse` gives the same claims; no `raw`
  gives one uncited claim of the text, or none for blank text. Metamorphic: appending an
  unknown id to any statement's citations changes no claim. Oracle: requirement 3.
- **C3**: one case. With a fake provider, `judge_case` on a text-graded case makes one call
  whose payload carries the question, the reference, the key points and the claims of
  `answer_claims`, and returns the judge's result; on a case without key points or an
  answer with `error` it returns `None` with no call. `judge_scores` of a scored result is
  the four values; of a judge_error the four `None`s, and `judge_columns` accepts both.
  Property: for random verdicts, `judge_scores` equals the judge's closed-form ratios.
  Oracle: requirements 4 and 5, eval-judge requirement 4.
- **C4**: the family rule. `check_judge_family("claude-haiku-5-5", "anthropic")` raises,
  with `"deepseek"` or `"openai-5.6"` it returns `None`; `gpt-5.6-luna` against `openai`
  raises; an unknown profile or model raises. `run_graded` with a same-family profile, or
  a judge without a profile, raises `ValueError` with the system never called and no
  provider call. Property: over every model and profile in `configs/llm/llm.yaml`, it
  raises exactly when the model's provider is one of the profile's models' providers.
  Oracle: requirement 6 and `configs/llm/llm.yaml` read in the test.
- **C5**: judging in the run. Over two text-graded cases and one lookup case, the fake
  judge is called twice; the text cases' `judge_scores` are the hand-counted ratios, the
  lookup's is `None`; `config["judge_version"] == "v1:claude-opus-5-5"`, `run_columns`
  reports it; a malformed reply gives the four-`None` dict and its case in
  `config["judge_errors"]`; an errored answer is not judged. `config["judge_cost_usd"]`
  equals the calls × the pricing-row cost of the fake usage, and the cases' `cost_usd` are
  the system's. With `max_cost_usd` below two judge calls and `max_workers=1`, the third
  case is not attempted and the run is `ABORTED`. `RunHalt` from the provider propagates
  out of `run_graded`. Metamorphic: the judge scores per case do not depend on case order.
  Oracle: requirement 7, configs/llm/pricing.yaml.
- **C6**: the summary. A run with three scored cases and one judge_error, with a passing
  calibration report for its judge version: status `"calibrated"`, the hand-computed
  means, `n_judged == 4`, the error id listed; with no report, a failing report, or a
  passing report for another version: status `"uncalibrated"` and `means is None`, the
  counts unchanged; without a judge version status `"none"`. Property: for random scores,
  the means are the closed-form means when reportable and `None` otherwise. Oracle:
  requirement 8, `scores_reportable`.
- **C7**: the metamorphic pass. A hand-built run: a breakdown whose rows sum to its partner
  lookup (consistent), one off by 5% (inconsistent), one with no partner (no verdict); a
  growth case whose `*100` compute equals the two lookups' growth (consistent); a screen
  ranked against its lookups with one inversion (inconsistent, the inversion named); a
  dimensioned screen (no verdict); an errored subject (`not_applicable`,
  `answer_error`). Counts, sorted ids and related ids as hand-counted. `run_graded` puts the
  same pass in `config["keyfree"]`, and omits it with `metamorphic=False`. Metamorphic:
  shuffling the pairs changes no count or id list. Oracle: requirements 9 to 11 and the
  keyfree-checks closed forms.
- **C8**: twins. For a hand-built suite and seed 7, the twins are those computed in the test
  from requirement 12 (ids, questions, ticker/fy, no key, tags kept); a case whose
  perturbation does not apply gives no twin; `max_twins` cuts the list and a negative one
  raises. Property: for random seeds the result is a function of the seed (two calls agree)
  and every twin's ticker differs from its original's. Oracle: requirement 12 and
  `perturb_ticker` / `perturb_year`.
- **C9**: the perturbation runner. With a fake system: a twin whose plan differs only in
  the ticker and whose value changes is consistent on both checks; a twin whose plan also
  changes the concept is `inconsistent` (`unexpected_change`); an unchanged value with
  `lookups_equal` False is `inconsistent` (`unchanged`); two twins of one original answer it
  once; a raising system gives an errored answer (`not_applicable`), a `RunHalt` propagates;
  `max_cost_usd` with `max_workers=1` skips the later pairs and lists them; `max_workers=0`
  raises. Metamorphic: the counts do not depend on `max_workers`. Oracle: requirement 13 and
  the keyfree-checks closed forms.

## Interface

`src/eval/core/judge_wiring.py`:
- `case_key_points(case: EvalCase) -> list[str]`
- `is_text_graded(case: EvalCase) -> bool`
- `answer_claims(answer: SystemAnswer) -> list[Claim]`
- `judge_case(judge: ClaimJudge, case: EvalCase, answer: SystemAnswer) -> JudgeResult | None`
- `judge_scores(result: JudgeResult) -> dict[str, float | int | None]`
- `check_judge_family(judge_model: str, generator_profile: str) -> None`
- `judge_summary(report: RunReport, calibration: Sequence[CalibrationReport]) -> dict`
- `run_graded(cases: Sequence[EvalCase], system: SystemUnderTest, *, judge: ClaimJudge | None = None, generator_profile: str | None = None, calibration: Sequence[CalibrationReport] = (), metamorphic: bool = True, config: RunnerConfig | None = None, **run_kwargs: Any) -> RunReport`

`src/eval/keyfree/runner.py`:
- `metamorphic_pass(pairs: Sequence[tuple[EvalCase, SystemAnswer]]) -> dict`
- `perturbation_twins(cases: Sequence[EvalCase], *, seed: int, names: Mapping[str, list[str]], max_twins: int) -> list[dict]`
- `run_perturbation_checks(twins: Sequence[dict], system: SystemUnderTest, *, lookups_equal: Callable[[EvalCase, EvalCase], bool], max_workers: int = 4, max_cost_usd: float | None = None) -> dict`

`EvalCase` (behaviour only): `from_dict` reads and `to_dict` writes `key_points`.

## Notes

- **Oracles.** The closed forms above; eval-judge requirement 4 (ratios), keyfree-checks
  requirements (verdicts), `configs/llm/llm.yaml` (families) and `configs/llm/pricing.yaml`
  (USD per 1,000 tokens; the fake usage is 1,000 prompt and 200 completion tokens), read by
  the tests themselves. All data is synthetic (tickers `AAA`, `BBB`, `CCC`; round numbers).
- **Fakes only at the provider boundary.** The judge runs on a real `LLMGateway` with a
  fake provider adapter that answers from the payload it receives; the system under test is
  a fake `SystemUnderTest` (the pipeline is the I/O boundary of a run). Nothing in `src` is
  mocked.
- **Why the judge cost is not in `cost_usd`.** v2 compares cost per case per arm (§3.5);
  the judge is the evaluator's cost, the same for every arm, so it is kept apart and only
  the budget sees both.
- **Why an uncalibrated judge shows no means.** §3.5: no judge score is reported before
  calibration. The per-case columns are still persisted (they are the calibration's input);
  only the run summary withholds the numbers.
- **Out of scope.** The `run_suite.py` flags, persisting `keyfree` verdicts to their own
  tables, perturbation lookups-equal from SQL (the caller injects `lookups_equal`), the
  `q4` check (its partners need quarterly cases the suites don't have yet), and building or
  storing calibration labels.
