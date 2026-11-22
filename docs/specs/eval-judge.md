# eval-judge — a calibrated, claim-by-claim judge for text answers

Part of the v2 eval rebuild ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.5 and §3.8). The
numeric graders cannot score a text answer. This change adds an LLM judge that scores an
answer claim by claim against a reference answer and the passages each claim cites, plus
the calibration that decides whether its scores may be reported at all. It adds a module,
`src/eval/core/graders/judge.py`; no existing grader, arm or pipeline stage changes, and
nothing here wires the judge into `run_suite.py` or the `eval` schema (that is the
eval-run-columns change).

## Requirement

1. **Inputs.** `ClaimJudge.judge(question, reference_answer, key_points, claims)` takes
   the question, the reference answer, the reference's key points (a non-empty list of
   strings) and the answer's claims. Each `Claim` has a `claim_id` (unique within the
   answer), its `text` and `cited_passages`, the text of each passage the claim cites.
   - `key_points` empty, or two claims with the same id: `ValueError`, before any call.
   - No claims: no LLM call; the result has every ratio `0.0`, `unsupported_claims = 0`,
     no verdicts and no error. An empty answer asserts nothing, so it must not score as
     correct or complete.
2. **One call through the gateway.** Otherwise the judge makes exactly one call through
   `LLMGateway`, stage `eval_judge`, on the judge model given at construction (never a
   tier lookup). The prompt is a versioned file pair (default version `v1`) under
   `prompts/templates/eval/`, resolved by a prompt registry for that stage.
   - **Untrusted text is data.** The question, reference answer, key points, claim texts
     and cited passages are untrusted. They reach the provider only inside the user
     message, serialized with `json.dumps` (default arguments, so ASCII-escaped). None of
     them appears in the system message, and none is ever used as a format template (a
     `{` or `}` in them is harmless).
   - In the payload each claim carries its `claim_id`; key points are identified by their
     0-based index in `key_points`.
3. **The reply.** The judge asks for, and reads (with `extract_json`), exactly this
   object:

   ```json
   {"claims":     [{"id": "<claim_id>", "correct": true, "supported": false}, ...],
    "key_points": [{"index": 0, "covered": true}, ...]}
   ```

   - `correct`: the claim agrees with the reference answer;
   - `supported`: the claim is supported by its own cited passages;
   - `covered`: the answer covers that key point.

   The order of the entries doesn't matter.
4. **Aggregation is code, not the model's arithmetic.** With *n* claims and *k* key
   points, after the override below:
   - `correctness = (#claims with correct) / n`;
   - `completeness = (#key points covered) / k`;
   - `citation_support = (#claims with supported) / n`;
   - `unsupported_claims = n − #claims with supported` (an int).

   **Override:** a claim with no cited passages is unsupported, whatever the judge says.
   `verdicts` holds one `ClaimVerdict(claim_id, correct, supported)` per claim, in the
   input order, with the final `supported`.
5. **A malformed reply is a `judge_error`, never a pass and never an abort.** The result
   then has `error` starting with `"judge_error: "`, all four scores `None` and no
   verdicts. Malformed means any of:
   - no JSON object can be read from the reply;
   - `claims` or `key_points` missing or not a list;
   - the claim ids in the reply are not exactly the input ids, each once;
   - the key-point indices are not exactly `0..k−1`, each once;
   - a `correct`, `supported` or `covered` value missing or not a JSON boolean.

   A provider error (after the gateway's retries) or a provider refusal is also a
   `judge_error`. `RunHalt` (budget, circuit breaker) propagates.

   *Why not fail loud:* a judge pass runs over hundreds of answers in one paid run, and
   one garbled reply must not discard the rest; it is the same rule the arms follow for a
   provider error (`SystemAnswer.error`). *Why not a default score:* a malformed reply
   carries no verdict, so any number would be invented. A `judge_error` is counted and
   shown, never folded into a score as 0 or 1.
6. **Judge rules.**
   - `model_family(model)` is the `provider` that `configs/llm/llm.yaml` gives the model
     (`openai`, `anthropic`, `deepseek`); a model with no row raises `ValueError` (its
     family can't be checked, so it can't be used).
   - `ClaimJudge(model=..., generator_model=...)` raises `ValueError` when the two
     families are equal, or either is unknown. The judge is never from the generator's
     family (v2 plan §3.5; §7.3: never DeepSeek while DeepSeek is generating).
   - `judge_version(prompt_version, model) == f"{prompt_version}:{model}"`, for example
     `"v1:claude-opus-5-5"`. `ClaimJudge.judge_version` is that for the judge's own
     prompt version and model, and every `JudgeResult` (including an empty answer and a
     `judge_error`) carries it as `judge_version`.
7. **Calibration.**
   - `cohen_kappa(labels_a, labels_b)` is Cohen's kappa, κ = (p_o − p_e) / (1 − p_e),
     with p_o the observed agreement and p_e = Σ_k p_a(k)·p_b(k) over the labels.
     Labels are any hashable values. Different lengths, empty input, or p_e = 1 (both
     raters always gave the same single label; κ undefined) raise `ValueError`.
   - `calibration_report(judge_labels, human_labels, *, judge_version, threshold=0.7,
     min_items=100)` returns a `CalibrationReport` with `judge_version`, `n`, `kappa`,
     `threshold`, `min_items` and `passed = n >= min_items and kappa >= threshold`. The
     plan calls for about 100 human-labelled answers, hence `min_items = 100`.
   - **The gate:** `scores_reportable(judge_version, reports)` is True only when the
     **last** report in `reports` for exactly that `judge_version` passed. No report, or
     a later failing one, means the version's scores are not reported. A new prompt
     version or judge model is a new `judge_version` and needs its own calibration.

## Acceptance criteria

The only fake is a provider adapter behind a real `LLMGateway`. The judge, the
aggregation, the gateway and the calibration functions are real.

- **C1**: aggregation. For claims c1 (correct, supported), c2 (correct, unsupported), c3
  (incorrect, supported), all cited, and key points covered `[T, F, T, T]`, with the reply
  listing the claims out of order: `correctness = 2/3`, `completeness = 3/4`,
  `citation_support = 2/3` (each abs 1e-12), `unsupported_claims == 1`, `error is None`,
  verdicts in input order with the reply's values, `judge_version == "v1:claude-opus-5-5"`.
  Properties: for random verdicts and coverage the scores equal requirement 4's formulas;
  **metamorphic:** permuting the input claims permutes the verdicts and leaves the scores
  unchanged. Oracle: requirement 4, hand counts.
- **C2**: the uncited override. A claim with no cited passages that the judge calls
  supported has `supported == False` and counts in `unsupported_claims`; its `correct`
  is the judge's. **Metamorphic:** adding one uncited claim (any judge verdict) to an
  answer raises `unsupported_claims` by exactly 1 and gives
  `citation_support = supported_before / (n + 1)`. Oracle: requirement 4.
- **C3**: inputs. No claims: no provider call, ratios `0.0`, `unsupported_claims == 0`, no
  verdicts, `error is None`, `judge_version` set. Empty `key_points` or duplicate claim
  ids: `ValueError` and no provider call. Property: for any non-empty key points, an empty
  answer makes no call and scores zero. Oracle: requirement 1.
- **C4**: `judge_error`. Each malformed reply of requirement 5, a non-transient provider
  error and a provider refusal give `error` starting `"judge_error: "`, all four scores
  `None`, no verdicts, `judge_version` set, and no exception. With an open trace and
  `RunBudget(max_llm_calls=0)`, `judge()` raises `RunHalt` and makes no provider call.
  **Metamorphic:** dropping any one claim entry from a valid reply turns a scored result
  into a `judge_error`. Oracle: requirement 5.
- **C5**: one call, untrusted text. Exactly one provider call, at stage `eval_judge`, with
  `model == "claude-opus-5-5"`; every untrusted input `v` satisfies `json.dumps(v) in
  user_text`, and its marker never appears in the system message; text holding
  `{question}`, `{0}` or a lone `}` is judged without error. Property: the same for random
  text. Oracle: requirement 2.
- **C6**: judge rules. `model_family` gives `openai` for `gpt-5.6-sol` and `gpt-5-nano`,
  `anthropic` for `claude-opus-5-5`, `deepseek` for `deepseek-flash`, and raises
  `ValueError` for a model not in `llm.yaml`. `ClaimJudge` raises `ValueError` for
  same-family pairs (`deepseek-flash`/`deepseek-v4-pro`, `gpt-5`/`gpt-5.6-sol`,
  `claude-haiku-5-5`/`claude-opus-5-5`) and for an unknown model on either side, and
  builds for cross-family pairs. `judge_version("v1", "claude-opus-5-5") ==
  "v1:claude-opus-5-5"`, which is also the default judge's `judge_version`. Properties:
  for every model in `llm.yaml`, `model_family` is its `provider`; for every pair,
  construction raises exactly when the providers match; `judge_version(p, m) ==
  f"{p}:{m}"`. Oracle: requirement 6 and `configs/llm/llm.yaml`.
- **C7**: `cohen_kappa`. The Wikipedia worked example (50 items: 20 yes/yes, 5 yes/no, 10
  no/yes, 15 no/no) gives 0.4; a hand-computed 2×2 (4, 1, 1, 4) gives 0.6; a hand-computed
  3-label example gives 0.5; disjoint constant raters give 0.0 (all abs 1e-12). Length
  mismatch, empty input and p_e = 1 raise `ValueError`. Properties: symmetric; invariant
  under a relabelling applied to both raters and under a permutation of the items;
  κ(a, a) = 1 when `a` has two or more labels; −1 ≤ κ ≤ 1. Oracle: Wikipedia "Cohen's
  kappa" worked example; hand calculations shown in the tests.
- **C8**: calibration and the gate. The worked example twice over (100 items) reports
  `kappa ≈ 0.4`, `n == 100`, `passed == False`; perfect agreement on 100 items passes;
  on 50 items it fails with the default `min_items` and passes with `min_items=50`;
  κ = 0 exactly passes at `threshold=0.0` and fails at `0.01` (the threshold is
  inclusive). `scores_reportable` is False with no report or only another version's
  report, True after a passing report, and follows the last report for the version.
  Properties: `passed == (n >= min_items and kappa >= threshold)` and `kappa ==
  cohen_kappa(...)`; swapping judge and human labels changes neither. Oracle:
  requirement 7.

## Interface

New (stubbed in the spec PR), all in `src.eval.core.graders.judge`:

- `model_family(model: str) -> str`
- `judge_version(prompt_version: str, model: str) -> str`
- `Claim(claim_id: str, text: str, cited_passages: Sequence[str])`, with those attributes.
- `ClaimVerdict(claim_id: str, correct: bool, supported: bool)`, with those attributes.
- `JudgeResult(*, judge_version: str, verdicts: Sequence[ClaimVerdict], correctness:
  float | None, completeness: float | None, citation_support: float | None,
  unsupported_claims: int | None, error: str | None = None)`, with those attributes.
- `ClaimJudge(*, model: str, generator_model: str, gateway: LLMGateway | None = None,
  prompt_version: str = "v1")`
  - `judge_version` (property) `-> str`
  - `judge(question: str, reference_answer: str, key_points: Sequence[str], claims:
    Sequence[Claim]) -> JudgeResult`
- `cohen_kappa(labels_a: Sequence[Hashable], labels_b: Sequence[Hashable]) -> float`
- `CalibrationReport(*, judge_version: str, n: int, kappa: float, threshold: float,
  min_items: int, passed: bool)`, with those attributes.
- `calibration_report(judge_labels, human_labels, *, judge_version: str, threshold: float
  = 0.7, min_items: int = 100) -> CalibrationReport`
- `scores_reportable(judge_version: str, reports: Sequence[CalibrationReport]) -> bool`

The implementer adds:

- the `v1` prompt pair for stage `eval_judge` under `prompts/templates/eval/` and the
  registry that resolves it (the XBRL registry is rooted at `prompts/templates/xbrl/`);
- the default gateway, built lazily on first use for stage `eval_judge`;
- the module docstring, and the package export if wanted.

## Notes

- **Cohen's kappa oracle:** the worked example in Wikipedia's "Cohen's kappa" article
  (p_o = 0.7, p_e = 0.5·0.6 + 0.5·0.4 = 0.5, κ = 0.4). The other values are hand
  calculations written out in the tests.
- **Model families** come from the committed `configs/llm/llm.yaml`; adding a model there
  gives it a family. A model id in no row is refused rather than guessed from its name.
- **What the judge does not do:** decide a case's pass/fail, persist results, or pick the
  judge model. Those belong to the run (eval-run-columns) and to the operator, who
  records the judge_version with the run.
- All test data is synthetic.
