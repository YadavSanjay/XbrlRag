# judge-calibration-one-class — observed agreement where the human labels are one class

Changes the pass rule of [judge-calibration](judge-calibration.md) (requirements 9, 10, 13 and
14 there) for a dimension whose human labels are nearly all one class. Status item E3;
[pre-registration](../eval/heldout_v2/preregistration.md) Amendment 1 item 5, whose
calibration result Amendment 2 records.

## Why

The maintainer's labels of the E3 subset (`docs/eval/judge_calibration/labels.csv`, branch
`data/judge-calibration-labels`, commit 95bc1a3; 68 answers, 466 labels) are:

| dimension | labels | y | n | human majority share |
|---|---|---|---|---|
| `correct` | 150 | 143 | 7 | 143/150 = 0.9533 |
| `supported` | 103 | 103 | 0 | 103/103 = 1 |
| `covered` | 213 | 109 | 104 | 109/213 = 0.5117 |

`supported` is one class by construction: only `xbrlrag_full` claims carry citations (baseline
claims carry none, [judge-baseline-claim-text](judge-baseline-claim-text.md) C3), and its
faithfulness verifier has already dropped the claims their citations do not support. With no
human `n`, `p_e` equals the judge's own "supported" rate, so Cohen's kappa is 0 when the judge
marks any claim unsupported and undefined (`p_e == 1`) when it marks none. Today's rule
(`kappa is not None and kappa >= 0.7` for every dimension) can therefore never pass, whatever
the judge does. This is the first of Feinstein and Cicchetti's (1990) two kappa paradoxes: at
extreme prevalence, high agreement gives a low or undefined kappa.

## Requirement

All changes are in `src/eval/core/graders/judge_calibration.py`. `p_o`, `kappa` and `n` are
those of `dimension_agreement` (unchanged, judge-calibration requirement 8).

1. **`human_majority_share(human: Sequence[bool]) -> float | None`**: the share of the more
   frequent label, `max(#True, #False) / len(human)`; `None` when `human` is empty.
2. **`dimension_gate(human: Sequence[bool], *, one_class_share: float = 0.99) -> str`**:
   `"agreement"` when `human_majority_share(human)` is not None and `>= one_class_share`
   (inclusive), else `"kappa"`. It reads **only the human labels**.
3. **`dimension_passed(agreement: DimensionAgreement, gate: str, *, threshold: float,
   agreement_bound: float, min_items: int) -> bool`**:
   - gate `"kappa"`: `n >= min_items` and `kappa is not None` and `kappa >= threshold`
     (today's per-dimension rule);
   - gate `"agreement"`: `n >= min_items` and `p_o is not None` and `p_o >= agreement_bound`
     (inclusive);
   - any other gate: `ValueError`.
4. **`calibrate(answers, human_labels, judge_labels, *, threshold=0.7, min_answers=100,
   min_items=None, one_class_share=0.99, agreement_bound=0.95)`**:
   - `min_items=None` means `min_answers` (today's behaviour, where one number floors both).
     The CLI passes `--min-items 100` by default (requirement 9).
   - `ValueError` when `one_class_share` is not in `(0.5, 1]` (at 0.5 or below every
     dimension would take the agreement gate), when `agreement_bound` is not in `[0, 1]`, or
     when `min_items` (after the default) is negative. The existing checks stay.
   - **The gate of each dimension is `dimension_gate` of the human labels of that dimension
     over every answer in `answers`**, judge_error answers included, in label-key order. It
     never depends on the judge's labels or on which answers the judge failed, so every
     candidate calibrated on one label set gets the same gates.
   - The agreement per dimension is computed as today (counted answers only).
   - `passed = n_answers >= min_answers` and `dimension_passed(...)` for every dimension, with
     the call's `threshold`, `agreement_bound` and `min_items`.
5. **`CalibrationResult`** gains the attributes `min_items` (int, after the default),
   `one_class_share`, `agreement_bound`, `gates` (dict dimension → `"kappa"` /
   `"agreement"`) and `human_majority_share` (dict dimension → float or None, requirement 1
   over the same labels as the gate). Its constructor takes them as keywords with defaults
   `min_items=None` (→ `min_answers`), `one_class_share=0.99`, `agreement_bound=0.95`,
   `gates=None` (→ `"kappa"` for every dimension of `dimensions`) and
   `human_majority_share=None` (→ None for every dimension), so a result built without them
   is a result of today's all-kappa rule and judge-calibration's tests still build one.
6. **`as_report(result)`**: `kappa` is the smallest kappa **over the dimensions whose gate is
   `"kappa"`**, NaN when one of those is None or there is none; an agreement-gated dimension's
   kappa (0 or None by construction) is left out. `passed`, `n`, `threshold` and
   `min_items=min_answers` are as today, so `scores_reportable(v, [as_report(r)]) ==
   r.passed` still holds. `CalibrationReport` and `scores_reportable` do not change.
7. **The record, schema `"judge-calibration/2"`.** `calibration_record` writes
   `"schema": "judge-calibration/2"`; each candidate gains `"min_items"`,
   `"one_class_share"` and `"agreement_bound"`, and each of its dimensions gains `"gate"`,
   `"human_majority_share"` (float or null) and `"passed"` (`dimension_passed` of that
   dimension with the candidate's parameters). Every other field, the hashes and the file
   format (judge-calibration requirements 12–14) are unchanged, and the record still has no
   timestamp, so the same inputs give the same `record_sha256`.
8. **Loading.** `load_calibration_reports` accepts only `"judge-calibration/2"` and builds each
   report as requirement 6 does, from the dimensions' `"gate"` and `"kappa"` fields; a `/1`
   record, correctly hashed, raises `ValueError` (decision D5).
9. **Not spec-tested; wiring the implementer adds** (`scripts/eval/judge_calibration.py` and
   its unit test, `test/unit/test_judge_calibration_cli.py`):
   - `calibrate` gains `--min-items` (default 100) and passes it, with `--min-answers` and
     `--threshold`, to `calibrate`; `one_class_share` and `agreement_bound` keep their
     defaults (no CLI flags: they are pre-registered values, decision D2). It prints, per
     candidate, `min_answers` and `min_items`, and per dimension the gate, the human majority
     share, `n`, kappa with its interval (kappa gate) or `p_o` (agreement gate) and the
     dimension's pass.
   - `show` prints the same per dimension from the record, and the candidate's `min_answers`
     and `min_items`. "min kappa" becomes "min kappa (kappa-gated)".
   - The unit test's record fixture becomes `/2`, and its `calibrate` call passes
     `--min-items` for its 6-label dimensions.
   - Docs: `docs/guides/xbrl-flow/11-evaluation.md` (the calibration section) and
     `docs/design/XbrlRag_Eval_v2_Architecture.md` §7 describe the two gates.

## Acceptance criteria

Everything in `src` is real; there is no LLM call and no fake. Labels are synthetic.

- **C1**: the gate and the share. `human_majority_share` of hand-listed label lists gives the
  hand-counted fraction (`[T]*3 + [F]` → 3/4; `[F]*5` → 1; `[T, F]` → 1/2; `[]` → None).
  `dimension_gate` gives `"agreement"` at 99/100 and at 100/100 and `"kappa"` at 98/100 with
  the default cut-off; at a passed `one_class_share=0.95`, `"agreement"` at 19/20 and
  `"kappa"` at 18/20; `"kappa"` for an empty list. Property: the gate equals the closed form
  of requirement 2 computed in the test, and is invariant under permuting the labels and
  under flipping every label. Oracle: requirements 1–2, hand counts.
- **C2**: a dimension's pass. `dimension_passed` on hand-built `DimensionAgreement`s:
  agreement gate with `p_o = 19/20` passes at bound 0.95 (inclusive) and `p_o = 18/20` fails;
  `n` one below `min_items` fails at `p_o = 1`; kappa gate with kappa 0.7 passes at threshold
  0.7, kappa 0.69 fails, kappa None fails; an agreement gate ignores kappa (None or 0 still
  passes on `p_o`) and a kappa gate ignores `p_o`; an unknown gate raises. Property: the
  result equals requirement 3's closed form for random fields. Oracle: requirement 3.
- **C3**: calibrate with a one-class dimension. Twenty answers, each one cited claim and one
  key point; human `supported` all y; `correct` and `covered` mixed tables with hand-computed
  kappa ≥ 0.7. A judge marking 19 of 20 claims supported passes (`p_o = 19/20 >= 0.95`), with
  `gates == {"correct": "kappa", "supported": "agreement", "covered": "kappa"}`,
  `human_majority_share["supported"] == 1` and `p_o`, kappa as hand-computed; one marking
  18 of 20 fails on `supported` alone; one marking all 20 supported (kappa None) passes; one
  marking all 20 unsupported fails (`p_o = 0`). A dimension below the cut-off keeps kappa
  even when `p_o >= 0.95`: human `correct` 19 y / 1 n (share 0.95 < 0.99) and a judge that
  calls every claim correct give `p_o = 0.95`, kappa 0, gate `"kappa"`, and fail (D1's case).
  `as_report(r).kappa` is the smaller of the two kappa-gated kappas, and
  `scores_reportable(v, [as_report(r)]) == r.passed`. `min_items` floors each dimension
  independently of `min_answers` (20 answers pass `min_answers=20` but fail `min_items=21`),
  and `min_items` defaults to `min_answers`. Bad `one_class_share` (0.5, 1.01) and
  `agreement_bound` (−0.01, 1.01) raise. Oracle: requirements 3–6, hand-computed tables.
- **C4**: the gate is computed from the human labels only. Property: for random human labels
  and random judge labels, any permutation of the judge's labels across answers, any
  replacement of them, and turning any answers into judge_errors leave `gates` and
  `human_majority_share` unchanged, and they equal the closed form of requirement 2 over all
  answers' human labels, computed in the test. Oracle: requirement 4.
- **C5**: the record. For results built by `calibrate`, the record has `schema
  "judge-calibration/2"`, each candidate the hand-listed `min_answers`, `min_items`,
  `one_class_share`, `agreement_bound`, and each dimension the hand-listed `gate`,
  `human_majority_share` and `passed` besides today's fields; `record_sha256` equals
  SHA-256 of the record's key-sorted compact JSON (built in the test) and is the same for
  results given in another order or label dicts in another order, and differs when
  `min_answers` or `min_items` differs. `load_calibration_reports(calibration_file_text(rec))`
  gives the candidates' `passed` and the requirement-6 kappa (the agreement-gated kappa left
  out); a correctly hashed `/1` record raises. Oracle: requirements 6–8, `hashlib`, `json`.

## Interface

New, in `src/eval/core/graders/judge_calibration.py` (stubbed in the spec PR):

- `human_majority_share(human: Sequence[bool]) -> float | None`
- `dimension_gate(human: Sequence[bool], *, one_class_share: float = 0.99) -> str`
- `dimension_passed(agreement: DimensionAgreement, gate: str, *, threshold: float,
  agreement_bound: float, min_items: int) -> bool`

Changed by the implementer (existing signatures gain keywords only):

- `calibrate(..., *, threshold: float = 0.7, min_answers: int = 100, min_items: int | None
  = None, one_class_share: float = 0.99, agreement_bound: float = 0.95) -> CalibrationResult`
- `CalibrationResult(..., min_items: int | None = None, one_class_share: float = 0.99,
  agreement_bound: float = 0.95, gates: Mapping[str, str] | None = None,
  human_majority_share: Mapping[str, float | None] | None = None)`
- `as_report`, `calibration_record`, `load_calibration_reports`, `RECORD_SCHEMA`
  (requirements 6–8).

Tests reach the new keywords and attributes through `calibrate` and `getattr`, so against
today's code they fail on an assertion, not a `TypeError` (the spec's `conftest.py` turns the
missing keyword into a test failure).

## Decisions for the maintainer

- **D1 — the cut-off: 0.99, not 0.95.** *Recommendation:* `one_class_share = 0.99`. At 0.95,
  `correct` (143/150 = 0.9533) would take the agreement gate too, and a judge that calls every
  claim correct would pass it (`p_o = 0.9533 >= 0.95`) without showing it catches a single
  wrong claim; the 7 human `n` labels are exactly what kappa should measure there. At 0.99
  the minority class has at most 1 label in 100: kappa then rests on one item, which is the
  paradox, while `correct`'s 7 still give kappa something to measure. Any cut-off in
  (0.9533, 1] gives these labels the same gates; 0.99 is a round number that does not sit on
  a measured share. Exactly one class (cut-off 1) would leave a 1-in-200 minority on kappa,
  where it is just as degenerate.
- **D2 — the bound: 0.95, a different number from the cut-off.** *Recommendation:* keep the
  maintainer's 0.95. The cut-off decides whether kappa can measure anything (a property of
  the label set); the bound is the judge's tolerated disagreement on a dimension that is
  effectively one class. With human `supported` all y, `p_o` is the share of claims the judge
  also calls supported, so 0.95 means at most 5 false "unsupported" per 100 verified claims
  (98/103 passes, 97/103 fails). `p_o >= 0.95` is the same as PABAK `>= 0.90` (PABAK = 2·p_o
  − 1, Byrt, Bishop and Carlin 1993). Both values are function defaults, recorded in the
  record, and not exposed on the CLI: they are pre-registered by Amendment 2, so they should
  not be tunable by a flag after the judges' labels are seen.
- **D3 — why raw observed agreement, not AC1, PABAK or specific agreement.**
  *Recommendation:* `p_o`, because it is the simplest to state and audit. Cicchetti and
  Feinstein (1990) advise reporting `p_pos` and `p_neg` beside kappa; with no human `n`,
  `p_neg` is 0 or undefined, and `p_pos` = 2a/(2a+b) tracks `p_o` (here `b` = judge "n" on a
  human "y"). PABAK is a linear map of `p_o`, so it gates identically while adding a formula.
  Gwet's AC1 (2008) stays near 1 at extreme prevalence, but its chance term uses the judge's
  own marginal, and at `AC1 >= 0.7` it passes a judge that calls about 20% of verified claims
  unsupported (`p_o = 0.8`, AC1 ≈ 0.76): looser than intended, and harder to explain. The
  limit of every one of them is the same and must be said: on an agreement-gated dimension
  the calibration shows that the judge rarely rejects what a person accepts (specificity on
  the majority class); with no human negatives it **cannot** show the judge catches an
  unsupported claim.
- **D4 — the gate is measured on all the human labels of the dimension.**
  *Recommendation:* every answer's labels, judge_error answers included. Measuring it on the
  judge's labels would let the judge choose its own gate; measuring it on the counted answers
  would let a judge's errors move it. The human labels alone fix it before any judge is run.
- **D5 — `/1` records are refused.** *Recommendation:* refuse them. No `calibration.json` has
  ever been committed on any branch (checked 2026-11-13), so nothing needs reading, and a
  `/1` `passed` was decided by another rule with a single floor; reading it under `/2` would
  be a silent change of meaning.
- **D6 — `min_items` separate from `min_answers`.** *Recommendation:* split them. The
  maintainer runs `calibrate --min-answers 68` (the subset). Today that one number also floors
  every dimension, so the gate would require only 68 labels per dimension, below the
  pre-registered "at least 100 human labels". With `--min-items 100` the pre-registered floor
  is enforced and both numbers are in the record. `min_items=None` keeps today's meaning for
  existing callers and the retired tests.
- **D7 — risks the record should not hide.** With `--min-answers 68` a single judge_error
  fails the calibration (`n_answers` 67 < 68). `supported` has 103 labels against a floor of
  100, so a judge_error on an answer with 4 or more cited claims fails it. `correct`'s kappa
  rests on 7 human `n` labels; its interval will be wide (published, not gated, judge-calibration
  D4). Re-judging only the errored answers is not supported by the CLI's `judge` resume, which
  treats an errored answer as done; left out of scope here.

## Notes

- **Oracles.** Hand counts and hand-computed 2×2 tables in the tests (as fractions). Literature
  for the rule: A. R. Feinstein and D. V. Cicchetti, "High agreement but low kappa: I. The
  problems of two paradoxes", J Clin Epidemiol 43(6):543–549 (1990); D. V. Cicchetti and A. R.
  Feinstein, "II. Resolving the paradoxes", ibid. 551–558; T. Byrt, J. Bishop and J. B.
  Carlin, "Bias, prevalence and kappa", J Clin Epidemiol 46(5):423–429 (1993); K. L. Gwet,
  "Computing inter-rater reliability and its variance in the presence of high agreement",
  Br J Math Stat Psychol 61:29–48 (2008); J. Sim and C. C. Wright, "The kappa statistic in
  reliability studies", Phys Ther 85(3):257–268 (2005).
- **Retired tests updated in this PR.** judge-calibration C6
  `test_pass_rule_closed_form_and_order` draws random human labels; a one-class draw now takes
  the agreement gate, so its strategy now gives each non-empty dimension both human labels
  (it still pins today's kappa rule for mixed dimensions). judge-calibration C8
  `test_record_and_file` pinned the exact `/1` field set; it now checks its listed fields as a
  subset and leaves the schema value to this spec's C5. Both pass against today's code and the
  new code. eval-judge (`calibration_report`, `scores_reportable`) and judge-runner-wiring C6
  (`CalibrationReport`) are unchanged and unaffected. `run_suite.py` reads `calibration.json`
  through `load_calibration_reports` only.
- **Dry check ($0, 2026-11-13).** `calibrate` of the spec author's throwaway reference
  implementation on the real subset (answers.json and labels.csv of commit 95bc1a3), with
  synthetic judges made from the human labels plus chosen disagreements, `threshold 0.7`,
  `min_answers 68`, `min_items 100`, bound 0.95. Gates at cut-off 0.99: `correct` kappa,
  `supported` agreement, `covered` kappa.

  | synthetic judge | correct (kappa) | supported (p_o) | covered (kappa) | cut-off 0.99 | cut-off 0.95 |
  |---|---|---|---|---|---|
  | 5 of 103 unsupported, correct 3 flips, covered 15 flips | 0.790 | 98/103 = 0.9515 | 0.859 | PASS | PASS |
  | 6 of 103 unsupported | 0.790 | 97/103 = 0.9417 | 0.859 | fail | fail |
  | 30 of 103 unsupported | 0.790 | 0.7087 | 0.859 | fail | fail |
  | none unsupported (supported kappa undefined) | 0.790 | 1.0 | 0.859 | PASS | PASS |
  | calls every claim correct | 0.000 (p_o 0.9533) | 1.0 | 0.859 | fail | **PASS** |
  | misses 4 of the 7 wrong claims | 0.588 (p_o 0.9733) | 0.9806 | 0.859 | fail | **PASS** |
  | covered 40 flips | 0.850 | 0.9806 | 0.625 | fail | fail |

  The two bold cells are D1's evidence: at cut-off 0.95 `correct` leaves kappa, and a judge
  that never flags a wrong claim passes.
- **Draft text for Amendment 2, item 4.** "The judge is calibrated on the seeded E3 subset:
  68 dev answers with 466 human labels (correct 150, supported 103, covered 213), run with
  `--min-answers 68` and `--min-items 100`, so every dimension meets the pre-registered 100
  labels. The human `supported` labels are all one class (103 y, 0 n), because only
  `xbrlrag_full` claims carry citations and its verifier has already removed unsupported ones;
  kappa is 0 or undefined there whatever the judge does (Feinstein and Cicchetti 1990), so a
  dimension whose human labels are at least 99% one class is gated on observed agreement
  ≥ 0.95 instead of kappa ≥ 0.7 (`docs/specs/judge-calibration-one-class.md`), a rule fixed
  from the human labels before any candidate judge labelled the set. On `supported` the
  calibration therefore shows that the judge rarely rejects a claim a person accepts, not that
  it catches unsupported claims; `correct` and `covered` keep kappa ≥ 0.7."
