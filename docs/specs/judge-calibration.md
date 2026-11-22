# judge-calibration — human labels, per-dimension kappa and the calibration record

> **Amended by [judge-calibration-one-class](judge-calibration-one-class.md)** (2026-11-13):
> a dimension whose human labels are at least 99% one class is gated on observed agreement
> instead of kappa; `calibrate` gains `min_items`; the record is `judge-calibration/2`.
> Requirements 9, 10, 13 and 14 below describe the rule before that change.

Part of the v2 eval rebuild ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.5, phase 1 exit
"judge kappa ≥ 0.7"; [eval v2 architecture](../design/XbrlRag_Eval_v2_Architecture.md) §7;
[pre-registration](../eval/heldout_v2/preregistration.md) Amendment 1 item 5, whose
calibration result Amendment 2 records). Status item E3.

## What exists and what is missing

[eval-judge](eval-judge.md) built the judge and a *scalar* calibration gate in
`src/eval/core/graders/judge.py`: `cohen_kappa(a, b)`, `calibration_report(judge_labels,
human_labels, *, judge_version, threshold=0.7, min_items=100)` and `scores_reportable`.
[judge-runner-wiring](judge-runner-wiring.md) put the judge in a run and made
`judge_summary` withhold means unless `scores_reportable` holds; it left "building or
storing calibration labels" out of scope, and `run_suite.py --judge` passes no calibration
reports, so every judge version is reported as `uncalibrated` today.

Missing, and added here, in a new module `src/eval/core/graders/judge_calibration.py`:

1. **What a label is.** The judge emits three kinds of boolean verdict: per claim `correct`
   and `supported`, per key point `covered` (eval-judge requirement 3). `JudgeResult` keeps
   only the claim verdicts; the per-key-point `covered` values are folded into
   `completeness` and lost, so completeness cannot be calibrated from a `JudgeResult`.
2. **Where calibration answers come from**, with a guard that no held-out material enters.
3. **A label sheet** the maintainer fills in by hand, and its strict import.
4. **Agreement per dimension** (one kappa per verdict kind, with an interval), the pass rule
   over all of them, and the bridge to the existing `CalibrationReport` gate.
5. **Choosing the cheapest passing judge** from several candidates calibrated on one set.
6. **A versioned, hashed calibration record** for Amendment 2, which a run can load.

No judge LLM call is made by the tests; the only fake is a provider adapter behind a real
`LLMGateway`, as in eval-judge.

## Requirement

### Calibration answers (dev only)

1. **`CalibrationAnswer(*, answer_id, case_id, split, generator_model, question,
   reference_answer, key_points, claims)`** holds one answer to be labelled: the case's
   question, reference answer (`EvalCase.expected_text`) and key points, the answer's judge
   `Claim`s, and the model that generated it. Its constructor raises `ValueError` when
   `split != "dev"`, `answer_id` is blank, `key_points` is empty or holds a blank string,
   `claims` is empty, two claims share a `claim_id`, or `generator_model` has no row in
   `configs/llm/llm.yaml` (`model_family` raises). Attributes as named; `key_points` and
   `claims` are stored as tuples.
   - *Why dev only:* calibration labels are read by a person and committed (decision D3);
     held-out questions (H-F, H-X, set B) are never seen before their look (pre-registration
     §5). The split is the structural guard: H-X builds are tagged `split:external`, H-F
     `split:heldout_filers`, an untagged case is `legacy`; all are refused.
   - *Why at least one claim:* an answer with no claims is never sent to the judge
     (eval-judge requirement 1), so it has nothing to calibrate.
2. **`calibration_answer(case, answer, *, answer_id, generator_model)`** builds one from a
   suite case and a system's answer: `split` is `slice_fields_from_tags(case.tags)["split"]`
   or `"legacy"` when untagged; `question = case.question`; `reference_answer =
   case.expected_text`; `key_points = case_key_points(case)`; `claims =
   answer_claims(answer)` (judge-runner-wiring requirement 3, so the judge sees in
   calibration exactly what it sees in a run). Raises `ValueError` when `is_text_graded(case)`
   is False, `answer.error` is set, or the constructor's checks fail.
   - **The text question shape** is that of a judge-graded case: `expected_text` and
     `key_points`, as judge-runner-wiring requirement 2 defines and as hx-authoring's `text`
     kind builds (`kind: "narrative"`). Dev text questions written in that shape are
     calibration-ready; their `passages` are not used by the judge (it reads each claim's
     own cited passages).

### The label sheet (export and import)

3. **Label keys.** Each label is keyed `(answer_id, dimension, item_id)`:
   - `("<a>", "correct", "<claim_id>")` for every claim;
   - `("<a>", "supported", "<claim_id>")` for every claim **with at least one cited
     passage**. An uncited claim has no `supported` label: eval-judge requirement 4 forces
     it unsupported whatever the judge says, so labelling it would only add free agreement;
   - `("<a>", "covered", "kp<i>")` for key point `i` (0-based).
4. **`build_label_sheet(answers) -> str`** returns CSV text written with Python's `csv`
   writer (default dialect, `lineterminator="\n"`): the header `answer_id, dimension,
   item_id, text, label`, then one row per label key, in this order: answers in input order;
   within an answer, for each claim in order its `correct` row then (if cited) its
   `supported` row; then its `covered` rows in key-point order. `text` is the claim text or
   the key point text; `label` is empty. Raises `ValueError` on two answers with the same
   `answer_id`.
5. **`render_label_context(answers) -> str`** returns the Markdown the maintainer reads
   while labelling: per answer, in input order, a heading line `## <answer_id>` followed by
   the question, the reference answer, each key point as `kp<i>: <text>`, and each claim as
   `<claim_id>: <text>` followed by each of its cited passages, or the line `(no cited
   passages)` when it has none. Every one of those strings appears verbatim.
6. **`parse_label_sheet(text, answers) -> dict[tuple[str, str, str], bool]`** reads a
   filled sheet back:
   - a leading UTF-8 BOM is ignored and `\r\n` line ends are accepted (spreadsheet saves);
   - the header must be exactly the five column names;
   - a label cell, stripped and lower-cased, is `y` or `yes` (True) or `n` or `no` (False);
   - the `text` column is ignored (an edited text changes nothing), and row order is free;
   - the rows' keys must be exactly the label keys of requirement 3 for `answers`, each
     once.

   A blank or other label, a wrong header, a missing, duplicate or unknown key, or a row
   with the wrong number of cells raises `ValueError` naming the 1-based line number (or
   the missing key). Nothing is defaulted: an unlabelled cell is an error, not a "no".

### Judge labels

7. **`judge_answer_labels(judge, answer) -> JudgeLabels`** makes exactly one judge call for
   the answer, through the judge's gateway, with the same inputs and prompt as
   `judge.judge(answer.question, answer.reference_answer, answer.key_points,
   answer.claims)`, and returns `JudgeLabels(answer_id, judge_version, labels, error,
   cost_usd)`:
   - `labels` has exactly the label keys of requirement 3 for this answer: `correct` and
     `supported` from the claim entries of the reply, `covered` from its key-point entries.
     So the labels aggregate to the scores `judge.judge` gives for the same reply.
   - A reply that eval-judge requirement 5 calls malformed, a provider error or a refusal:
     `error` starts with `"judge_error: "` and `labels` is empty. `RunHalt` propagates.
   - `cost_usd` is the cost the gateway recorded for the call (configs/llm/pricing.yaml),
     `0.0` when no call completed.
   - `judge_version` is `judge.judge_version`.
   - Before any call, `ValueError` when `model_family(judge.model) ==
     model_family(answer.generator_model)`: a judge is never calibrated on its own family's
     answers, for the reason it never grades them.

### Agreement

8. **`dimension_agreement(judge, human) -> DimensionAgreement`** over two equal-length
   boolean sequences (`ValueError` when the lengths differ), with attributes `n`, `p_o`,
   `p_e`, `kappa`, `ci_low`, `ci_high`:
   - `n = len(judge)`; when `n == 0` all others are `None`;
   - `p_o` the observed agreement, `p_e = Σ_k p_judge(k)·p_human(k)`;
   - `kappa = (p_o − p_e) / (1 − p_e)`, Cohen's kappa (equal to `cohen_kappa`); `None`
     when `p_e == 1` (both always gave one same label), with `ci_low = ci_high = None`;
   - the interval is Cohen's (1960) large-sample approximation: `se = sqrt(p_o·(1 − p_o) /
     (n·(1 − p_e)²))`, `ci_low = max(−1, kappa − 1.96·se)`, `ci_high = min(1, kappa +
     1.96·se)`. It is published, never gated on (decision D4).
   - Labels are binary because the judge's verdicts are; kappa is unweighted (decision D2).
9. **`calibrate(answers, human_labels, judge_labels, *, threshold=0.7, min_answers=100) ->
   CalibrationResult`** measures one judge version against the human labels:
   - `ValueError` when `human_labels`' keys are not exactly the label keys of `answers`;
     when `judge_labels` is not exactly one `JudgeLabels` per answer id; when the
     `judge_labels` carry more than one `judge_version`; or when a `JudgeLabels` without
     error has keys other than its answer's label keys.
   - An answer whose `JudgeLabels` has an error is left out of every dimension and counted
     in `n_judge_errors`; `n_answers` counts the others (it is never imputed, decision D5).
   - For each dimension `correct`, `supported`, `covered`: `dimension_agreement` over the
     label keys of that dimension of the counted answers, judge first.
   - `passed = n_answers >= min_answers` and, for every dimension, `n >= min_answers`,
     `kappa is not None` and `kappa >= threshold` (inclusive). Every dimension must pass,
     not a pooled kappa (decision D1).
   - `CalibrationResult` attributes: `judge_version`, `model` (the part of `judge_version`
     after its first `:`), `family` (`model_family(model)`), `n_answers`, `n_judge_errors`,
     `dimensions` (dict dimension → `DimensionAgreement`), `threshold`, `min_answers`,
     `passed`, `cost_usd` (sum over all `judge_labels`, errors included),
     `answer_set_sha256` and `label_set_sha256` (requirement 12, of `answers` and
     `human_labels`).
10. **`as_report(result) -> CalibrationReport`** bridges to the existing gate:
    `CalibrationReport(judge_version, n=n_answers, kappa=min of the three kappas (NaN when
    any is None), threshold, min_items=min_answers, passed=result.passed)`, so
    `scores_reportable(v, [as_report(r)])` is `r.passed`.

### Choosing the judge

11. **`choose_judge(results, *, generator_profile) -> CalibrationResult | None`**:
    - `ValueError` when the results do not all share one `answer_set_sha256` and one
      `label_set_sha256` (one labelled set calibrates every candidate; comparing candidates
      on different sets is meaningless), or when two share a `judge_version`; an unknown
      profile raises `ValueError` (as `check_judge_family`).
    - Eligible: `passed`, and `check_judge_family(result.model, generator_profile)` does not
      raise (the family rule against every model of the generator profile).
    - Returns the eligible result with the lowest `cost_usd` — the measured cost of judging
      the same labelled answers, priced by configs/llm/pricing.yaml (decision D6) — ties
      broken by the smaller `judge_version`; `None` when none is eligible. The input order
      does not matter.

### The calibration record

12. **Hashes** (SHA-256, lower-case hex, of UTF-8 bytes of compact JSON:
    `separators=(",", ":")`, default `ensure_ascii`):
    - `label_set_sha256(labels)`: of `json.dumps(sorted([a, d, i, v] for (a, d, i), v in
      labels.items()))` (lists sort by answer id, dimension, item id);
    - `answer_set_sha256(answers)`: of `json.dumps([...], sort_keys=True)` of one object per
      answer, sorted by `answer_id`, with keys `answer_id`, `case_id`, `split`,
      `generator_model`, `question`, `reference_answer`, `key_points` (list), `claims` (list
      of `{"claim_id", "text", "cited_passages"}`, in claim order);
    - `record_sha256(record)`: of `json.dumps(record, sort_keys=True)`.
13. **`calibration_record(results, *, answers, human_labels) -> dict`**:
    `{"schema": "judge-calibration/1", "answer_set_sha256", "label_set_sha256",
    "n_answers": len(answers), "n_labels": len(human_labels), "candidates": [...]}`, the
    candidates sorted by `judge_version`, each `{"judge_version", "model", "family",
    "n_answers", "n_judge_errors", "threshold", "min_answers", "passed", "cost_usd",
    "dimensions": {dim: {"n", "p_o", "p_e", "kappa", "ci_low", "ci_high"}}}` (floats as
    floats, `None` as JSON `null`). `ValueError` when a result's answer or label hash differs
    from those of `answers` / `human_labels`, or two results share a `judge_version`. The
    record has no timestamp, so the same inputs give the same `record_sha256`.
14. **The committed file.** `calibration_file_text(record) -> str` is
    `json.dumps({"record": record, "record_sha256": record_sha256(record)}, indent=2,
    sort_keys=True) + "\n"`. `load_calibration_reports(text) -> list[CalibrationReport]`
    parses it, raises `ValueError` when `record_sha256` does not match the record (an edited
    record is refused) or `schema` is not `"judge-calibration/1"`, and returns one
    `CalibrationReport` per candidate in record order, as requirement 10 builds it from the
    candidate's fields.

The implementer also adds (not spec-tested; wiring whose failure is visible):

- `scripts/eval/judge_calibration.py` with subcommands `export` (a dev run's text cases and
  answers → `labels.csv` + `context.md`), `judge` (candidate judges over the answers, under
  a hard `--max-cost-usd`), `calibrate` (→ the record file) and `show`;
- `run_suite.py --judge` loading the committed record with `load_calibration_reports` and
  passing it as `calibration` to `run_graded`, so a calibrated version's means are shown.

## Acceptance criteria

The only fake is a provider adapter behind a real `LLMGateway`. Everything in `src` is real.
All data is synthetic.

- **C1**: calibration answers. `calibration_answer` of a dev-tagged text-graded case and a
  structured answer gives the case's question, reference and key points, the claims of
  `answer_claims`, `split == "dev"`. A case tagged `split:external`, `split:heldout_filers`,
  `split:validation` or untagged, a case without key points, an errored answer, an answer
  with no claims, an unknown generator model, and a direct constructor call with a blank
  key point or duplicate claim ids raise `ValueError`. Property: for every split in
  `run_columns.SPLITS` other than `dev`, the case is refused. Oracle: requirements 1–2.
- **C2**: export. For two hand-built answers (one with an uncited claim), the sheet parsed
  with `csv.reader` equals the hand-listed header and rows (order, item ids `kp<i>`, no
  `supported` row for the uncited claim, empty labels), and text holding commas, quotes and
  a newline survives; duplicate answer ids raise. The context holds every question,
  reference, `kp<i>: text`, `<claim_id>: text`, passage and `(no cited passages)`, answers'
  headings in input order. Property: for random answers the sheet has `Σ (claims + cited
  claims + key points)` rows and its keys are requirement 3's. Oracle: requirements 3–5.
- **C3**: import. A filled sheet with `y`, `Yes `, `N`, `no` gives the hand-listed labels;
  a BOM and `\r\n` are accepted; a blank label, `maybe`, a bad header, a missing row, a
  duplicate row, an unknown key and a short row raise `ValueError`. Metamorphic: shuffling
  the data rows or rewriting the `text` column leaves the labels unchanged; export then
  fill then import is the identity on random labels. Oracle: requirement 6.
- **C4**: judge labels. With a fake reply, `judge_answer_labels` makes one provider call at
  stage `eval_judge`, returns the reply's verdicts as labels (no `supported` key for the
  uncited claim), `judge_version == "v1:claude-haiku-5-5"`, `cost_usd` equal to 1,000
  prompt and 200 completion tokens at the pricing row; a malformed reply gives a
  `judge_error` with no labels; a same-family generator raises with no call; `RunHalt`
  propagates. Property: for random verdicts, the labels aggregate to the ratios
  `ClaimJudge.judge` returns for the same reply. Oracle: requirement 7, eval-judge
  requirements 3–5, configs/llm/pricing.yaml.
- **C5**: per-dimension agreement. The Wikipedia worked example (20/5/10/15) gives `p_o =
  0.7`, `p_e = 0.5`, `kappa = 0.4`, `se = sqrt(0.21/12.5)`, `ci = 0.4 ± 1.96·se`; the
  hand-computed (4, 1, 1, 4) table gives `kappa = 0.6` and its interval; identical labels
  with both values give `kappa = 1`, `ci = (1, 1)`; one shared constant label gives
  `kappa is None`; empty gives all `None`; a length mismatch raises. Properties: equals
  `cohen_kappa`; invariant under permuting items, under swapping raters, and under flipping
  every label of both raters; `ci_low <= kappa <= ci_high` within `[−1, 1]`. Oracle:
  Wikipedia "Cohen's kappa" worked example and its standard-error formula; hand
  calculations in the tests.
- **C6**: calibrate and the gate. A hand-built set with a known 2×2 table per dimension
  gives those kappas; with `min_answers` at the answer count and every kappa ≥ threshold it
  passes, one dimension below threshold fails it, `n_answers < min_answers` fails it, a
  dimension with fewer than `min_answers` items fails it; a judge_error answer is left out
  of every dimension and counted; `cost_usd` sums all calls; mismatched keys, a missing
  answer and two judge versions raise. `scores_reportable(v, [as_report(r)]) == r.passed`
  and `as_report(r).kappa` is the smallest kappa. Property: `passed` equals the closed form
  of requirement 9. Metamorphic: permuting answers changes nothing. Oracle: requirements
  8–10.
- **C7**: choosing. Over hand-built results (cheap failing, mid passing Anthropic, dear
  passing OpenAI): the mid one is chosen for profile `deepseek` and `openai-6.1`, the dear
  one for `anthropic`; with none eligible `None`; equal costs pick the smaller version;
  differing answer or label hashes, duplicate versions and an unknown profile raise.
  Property: the choice is the minimum-cost eligible result, independent of input order;
  adding a failing cheaper candidate does not change it. Oracle: requirement 11 and the
  providers in configs/llm/llm.yaml.
- **C8**: record and hashes. The three hashes equal SHA-256 of the JSON built in the test
  from requirement 12; the record has the hand-listed keys and candidate fields, sorted by
  `judge_version`; `load_calibration_reports(calibration_file_text(record))` gives the
  candidates' reports; an edited record, a wrong schema and a result from another label set
  raise. Metamorphic: changing one label changes `label_set_sha256` and `record_sha256`;
  reordering the results or the label dict does not. Oracle: requirements 12–14, `hashlib`.

## Interface

New, in `src/eval/core/graders/judge_calibration.py` (stubbed in the spec PR):

- `CalibrationAnswer(*, answer_id: str, case_id: str, split: str, generator_model: str,
  question: str, reference_answer: str, key_points: Sequence[str], claims: Sequence[Claim])`
- `calibration_answer(case: EvalCase, answer: SystemAnswer, *, answer_id: str,
  generator_model: str) -> CalibrationAnswer`
- `build_label_sheet(answers: Sequence[CalibrationAnswer]) -> str`
- `render_label_context(answers: Sequence[CalibrationAnswer]) -> str`
- `parse_label_sheet(text: str, answers: Sequence[CalibrationAnswer]) -> dict[tuple[str, str, str], bool]`
- `JudgeLabels(*, answer_id: str, judge_version: str, labels: Mapping[tuple[str, str, str], bool], error: str | None, cost_usd: float)`
- `judge_answer_labels(judge: ClaimJudge, answer: CalibrationAnswer) -> JudgeLabels`
- `DimensionAgreement(*, n: int, p_o: float | None, p_e: float | None, kappa: float | None, ci_low: float | None, ci_high: float | None)`
- `dimension_agreement(judge: Sequence[bool], human: Sequence[bool]) -> DimensionAgreement`
- `CalibrationResult(*, judge_version, model, family, n_answers, n_judge_errors, dimensions, threshold, min_answers, passed, cost_usd, answer_set_sha256, label_set_sha256)`
- `calibrate(answers, human_labels, judge_labels, *, threshold: float = 0.7, min_answers: int = 100) -> CalibrationResult`
- `as_report(result: CalibrationResult) -> CalibrationReport`
- `choose_judge(results: Sequence[CalibrationResult], *, generator_profile: str) -> CalibrationResult | None`
- `label_set_sha256(labels: Mapping[tuple[str, str, str], bool]) -> str`
- `answer_set_sha256(answers: Sequence[CalibrationAnswer]) -> str`
- `record_sha256(record: Mapping[str, Any]) -> str`
- `calibration_record(results: Sequence[CalibrationResult], *, answers: Sequence[CalibrationAnswer], human_labels: Mapping[tuple[str, str, str], bool]) -> dict`
- `calibration_file_text(record: Mapping[str, Any]) -> str`
- `load_calibration_reports(text: str) -> list[CalibrationReport]`

Existing code is not changed by the spec PR. The implementer may refactor `ClaimJudge`'s
private reply handling so `judge_answer_labels` reuses it (the public behaviour of
eval-judge stays).

## Decisions for the maintainer

- **D1 — what is gated.** *Recommendation:* three kappas (`correct`, `supported`,
  `covered`), each ≥ 0.7 and each over ≥ 100 items, plus ≥ 100 answers. The plan's single
  "kappa ≥ 0.7" maps onto all three; a pooled kappa would let a judge that tracks support
  well but correctness badly pass. `unsupported_claims` and `citation_support` are both
  derived from `supported`, so they need no kappa of their own. The pre-registration says
  "at least 100 human labels" and the plan "about 100 answers"; the rule meets both.
- **D2 — binary, unweighted kappa.** The judge emits booleans, so labels are y/n and kappa
  is unweighted. *Recommendation:* keep it. An ordinal scale (e.g. partly correct) needs a
  new prompt version and a weighted kappa; not worth it before the first calibration.
- **D3 — where calibration answers come from.** **No dev text questions exist yet**: the
  v2 dev and validation suites are numeric only (pre-registration Amendment 1), the
  generator has no narrative shape, and `sql_derived_v1` has no `expected_text` case.
  *Decided (maintainer, 2026-11-10):* Claude drafts about 35 dev text questions in
  hx-authoring's `text` shape (reference answer, key points, passages) from dev filers'
  filings only, never held-out ones, and the maintainer reviews and edits them; they live in
  a committed dev file built with `split:dev` tags. Calibration questions are never scored,
  so drafting them is not tuning. Each is answered by three DeepSeek-profile arms of
  different quality (`xbrlrag_full`, `naive_rag_filtered`, `closed_book`) for about 105
  answers, and the maintainer labels them. DeepSeek-generated answers can calibrate both the
  Anthropic and the OpenAI candidate (requirement 7), so every candidate sees the same set.
  Commit the labels sheet, the context and the record under
  `docs/eval/judge_calibration/` (dev material is public, plan §10.8), so the result is
  reproducible. Label about 110 answers so a few judge_errors cannot drop `n_answers`
  below 100.
- **D4 — the interval.** The pre-registration asks only for kappa ≥ 0.7, so the gate is
  on the point estimate. *Recommendation:* publish Cohen's approximate 95% interval beside
  it and do not gate on it. It ignores that claims of one answer are correlated, so it is
  somewhat narrow; a cluster bootstrap over answers could replace it later.
- **D5 — judge_errors in calibration.** *Recommendation:* left out and counted, never
  imputed (as in runs); they shrink `n_answers` and so can fail the calibration. The count
  goes in the record.
- **D6 — "cheapest".** *Recommendation:* the measured cost of judging the same labelled
  answers (each call priced by configs/llm/pricing.yaml), not list price per token: a
  cheaper-per-token model that writes longer replies can cost more, and the same answers
  and prompt make the measured figures comparable.

## Notes

- **Oracles.** Wikipedia "Cohen's kappa": worked example (50 items, κ = 0.4) and the
  approximate standard error `sqrt(p_o(1−p_o)/(N(1−p_e)²))` (Cohen 1960); the other
  confusion tables are hand calculations in the tests. Hashes are recomputed in the tests
  with `hashlib`/`json`. Prices come from configs/llm/pricing.yaml, families from
  configs/llm/llm.yaml, both read by the tests.
- **Not covered here:** writing the dev text questions (D3), the human labelling itself,
  the paid candidate runs, and Amendment 2's text. The calibration record file is what
  Amendment 2 cites (its `record_sha256`).
- **Untrusted text.** Answer and passage text reach the judge only as eval-judge does
  (`json.dumps` in the user message). The sheet and context are files for a person; nothing
  read back from them except the label cell and the key columns is used.
