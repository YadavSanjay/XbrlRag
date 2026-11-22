# blog-stats — the statistics pre-registered for external test v1

Pre-registration v1 §5 ([`preregistration_v1.md`](../eval/external_test_v1/sealed/preregistration_v1.md))
fixes how external test v1's results are reported: every proportion with a Wilson 95%
interval; a system run three times as the mean and range of its runs; and XbrlRag against
each baseline with a two-sided exact McNemar test, where a case counts as passed by a
three-run system when it passes in at least 2 of the 3 runs. The repo has none of these.
`summarize_repeats` (docs/specs/repeat-spread.md) already gives the mean and range.

This change adds the three pure functions, and an unlocked script that applies them to
stored runs. It runs no system and persists nothing.

## Requirement

1. **Wilson interval.** `wilson_interval(k, n)` returns the Wilson score 95% interval
   `(lower, upper)` for `k` successes in `n` trials, without continuity correction, with
   `z = 1.959963984540054` (the 0.975 normal quantile):
   - `p = k/n`, `d = 1 + z²/n`, centre `(p + z²/(2n)) / d`,
     half-width `z·√(p(1−p)/n + z²/(4n²)) / d`;
   - `lower = centre − half-width` and `upper = centre + half-width`, clamped to `[0, 1]`;
     at `k = 0` the lower bound is 0, and at `k = n` the upper bound is 1, within float
     rounding; otherwise both are strictly inside `(0, 1)`;
   - `n < 1`, `k < 0` or `k > n` raises `ValueError`.
2. **Repeat outcome.** `majority_outcomes(runs)` takes one mapping per run, from `case_id`
   to whether the case passed, and returns one mapping: a case passes when it passed in
   **more than half** of the runs (2 of 3; 1 of 1).
   - The number of runs must be odd (1, 3, 5, …); otherwise it raises `ValueError`.
   - Every run must have the same case ids; otherwise it raises `ValueError`. Repeats of
     one suite always do, so a difference means a wrong run id, and it is not papered over.
3. **Paired test.** `mcnemar_exact(a, b)` compares two systems' per-case outcomes over the
   **shared cases**: the case ids present in both mappings. Cases in only one mapping are
   ignored. A decline is a failed case like any other; it is never dropped (v1 §5, "the
   cases both answered", read as the cases both systems were given; decided 2026-10-28 and
   stated in v2). It returns a dict:
   - `n`: shared cases; `both`: passed by both; `a_only`; `b_only`; `neither`;
   - `p_value`: two-sided exact McNemar, with `m = a_only + b_only` and `j = min(a_only, b_only)`:
     `min(1, 2 · Σ_{i=0..j} C(m, i) / 2^m)`; `1.0` when `m = 0`.
   - No shared case raises `ValueError`.
4. **Script (not locked).** `scripts/eval/eval_stats.py` takes a JSON file naming, for one
   question set, the systems and their run ids (one or three each), the reference system
   (`xbrlrag_full`) and, for A′, a `ticker → stratum` map. For each system it loads every
   case row of each run from the `eval` schema (`value_correct`, `kind`, `ticker`; no
   `limit` cap) and prints, for the headline slice (lookup + compute), each kind, and each
   A′ stratum:
   - per run: `k/n` value-correct with its Wilson interval;
   - per system: the mean and range of its runs;
   - against the reference: `mcnemar_exact` on the `majority_outcomes` of each.

   **It prints and writes counts and rates only:** no case id, question, answer or ticker
   beyond the stratum names. Set B's author runs it on B and shares the output
   (amendment 2 §2).

## Acceptance criteria

- **C1**: `wilson_interval` matches published Wilson intervals within `abs=5e-5`.

  Point cases, Newcombe (1998) Table II, method 3 (score, no continuity correction):
  81/263 → (0.2553, 0.3662); 15/148 → (0.0624, 0.1605); 0/20 → (0.0000, 0.1611);
  1/29 → (0.0061, 0.1718).

  Oracle: Newcombe RG, "Two-sided confidence intervals for the single proportion:
  comparison of seven methods", *Statistics in Medicine* 17 (1998) 857–872. The tolerance
  is half the table's last printed digit.
- **C2**: Wilson invariants, for any `0 ≤ k ≤ n`, within `abs=1e-12`:
  - `0 ≤ lower ≤ k/n ≤ upper ≤ 1`;
  - `wilson_interval(n − k, n) == (1 − upper, 1 − lower)` (symmetry);
  - `lower` is 0 (within `1e-12`) when `k == 0` and above `1e-12` otherwise; `upper` is
    1 when `k == n` and below `1 − 1e-12` otherwise;
  - for `0 < k < n`, scaling to `(c·k, c·n)` with `c ≥ 2` gives a strictly narrower interval.
  - Invalid inputs (`n < 1`, `k < 0`, `k > n`) raise `ValueError`.

  Oracle: the definition (metamorphic).
- **C3**: `majority_outcomes` gives the more-than-half outcome per case.

  Point case: three runs over `a`–`d`, with `a` passed 3 times, `b` 2, `c` 1, `d` 0 →
  `{a: True, b: True, c: False, d: False}`. One run is returned unchanged.

  Oracle: v1 §5, worked by hand.
- **C4**: `majority_outcomes` invariants: reordering the runs changes nothing; negating
  every outcome negates the result (odd counts have no tie); an even number of runs, no run,
  or runs with different case ids raise `ValueError`.

  Oracle: the definition (metamorphic).
- **C5**: `mcnemar_exact` counts and p-values, floats within `abs=1e-12`.

  Point cases (binomial sums worked by hand):
  - `a_only = 1, b_only = 6` (plus 3 `both`, 2 `neither`): `m = 7`,
    `P(X ≤ 1) = (1 + 7)/128`, `p = 16/128 = 0.125`; `n = 12`.
  - `a_only = 0, b_only = 5`: `p = 2 · 1/32 = 0.0625`.
  - `a_only = 3, b_only = 3`: `P(X ≤ 3) = 42/64`, so `p = 1.0`.
  - `a_only = b_only = 0`: `p = 1.0`.
  - Cases in only one mapping are not counted: with `a` over 482 cases and `b` over 60 of
    them, `n == 60`.

  Oracle: the exact binomial definition of the McNemar test, worked by hand.
- **C6**: `mcnemar_exact` invariants: `both + a_only + b_only + neither == n`; swapping
  `a` and `b` swaps `a_only` and `b_only` and keeps `p_value`; adding cases passed by both
  or by neither keeps `p_value`; `0 < p_value ≤ 1`; `p_value == 1` when `a_only == b_only`;
  no shared case raises `ValueError`.

  Oracle: the definition (metamorphic).

## Interface

- `src.eval.core.stats.wilson_interval(k: int, n: int) -> tuple[float, float]`
- `src.eval.core.stats.majority_outcomes(runs: Sequence[Mapping[str, bool]]) -> dict[str, bool]`
- `src.eval.core.stats.mcnemar_exact(a: Mapping[str, bool], b: Mapping[str, bool]) -> dict[str, Any]`
  — keys `n`, `both`, `a_only`, `b_only`, `neither` (`int`) and `p_value` (`float`); a
  plain dict so the script can write it as JSON.

## Notes

- **Pass means `value_correct`** (v1 §3: value accuracy is how systems are compared), read
  from `eval.eval_case_result.value_correct` as graded in the run itself at the freeze tag.
  The script does not regrade, so it needs no suite file, and B's suite stays in the vault.
- **Never pooled:** the script takes one question set per call (v1 §5).
- **Out of scope:** multiple-comparison corrections (v1 reports p-values as they are),
  mid-p variants, intervals for differences.
- **Synthetic data only:** the tests use hand-made case ids, not real eval runs.
