# repeat-spread — mean ± spread over repeated eval runs

Phase 6 of the accuracy plan (`docs/design/XbrlRag_Accuracy_Plan_Oct2026.md` §4 item 8). One
full run of the 472-case suite moves about ±3 points from run to run, and the `aq-8b`
multi-dimension cases flip a lot. Between `eval-9a74fa9183cf` and `eval-d2884b8dd7c1`, 6 of the
8 lost cases were `aq-8b` refusals that were not caused by the code change. Without a noise
band, a +2 point change cannot be told apart from noise. Also, nothing records which cases flip,
so every comparison re-investigates the same flaky cases.

This change adds a pure summary over N runs of the same suite. It does not add a new runner,
and it does not persist anything. The runs are taken as usual (`run_suite.py`), and a script
summarises them.

## Requirement

1. **Summary.** `summarize_repeats(runs, kinds=None)` takes one mapping per run, from
   `case_id` to `passed`, and returns a summary `dict` with the keys below.
   - It needs at least 2 runs; with fewer it raises `ValueError`.
   - Only the **common cases**, the ids present in every run, are counted. A partial run
     therefore cannot bias the summary. If no case is common, it raises `ValueError`.
   - Keys:
     - `n_runs`, `n_cases` (the common cases);
     - `accuracies`: each run's fraction passed over the common cases, as a tuple in input
       order;
     - `mean`; `std`, the **sample** standard deviation (n − 1 denominator); `min`; `max`;
     - `floor`: the fraction of common cases passed in **every** run;
     - `ceiling`: the fraction passed in **at least one** run;
     - `flaky_case_ids`: a sorted tuple of the common cases passed in some runs but not all;
     - `flip_rate`: `len(flaky_case_ids) / n_cases`;
     - `by_kind`: when `kinds` (`case_id` → kind) is given, a dict from kind to the
       summary of that kind's common cases, each with an empty `by_kind`. Cases missing
       from `kinds` are left out of `by_kind` but still count in the totals. When `kinds` is
       `None`, it is `{}`.
2. **Script (not locked).** `scripts/eval/summarize_repeats.py --runs <id> <id> ... [--out
   <json>]` loads each stored run's case results from the `eval` schema and prints the summary:
   overall, then by kind, then the flaky cases. It must read **every** case of a run;
   `store.load_case_results` defaults to `limit=1000`, and a paraphrase run is larger than
   that.
3. **Paraphrase tier (config, not locked).** Add a `paraphrase` tier to
   `configs/eval/sql_derived_templates.yaml`: the `standard` tier's settings with
   `paraphrases: 3`. Build it to `test/eval/suites/sql_derived_paraphrase_v1.json`.
   - Stratified selection keys on the parameter combination, not on the phrasing. So the tier's
     first-phrasing case ids must equal `sql_derived_v1.json`'s ids exactly. Check this on the
     built suite before committing it.
   - A run of it fills `paraphrase_stability` and `unstable_groups()`; that code already exists.

## Acceptance criteria

- **C1**: `summarize_repeats` returns the fields of Requirement 1 for two or more runs. Floats
  are compared within `abs=1e-12`.

  Point case (hand calculation), runs over cases `a`–`e`, with run 3 also having an extra case
  `x` that is not common:
  - run 1: `a` ✓ `b` ✓ `c` ✓ `d` ✗ `e` ✗ → 3/5 = 0.6;
  - run 2: `a` ✓ `b` ✓ `c` ✗ `d` ✓ `e` ✗ → 0.6;
  - run 3: `a` ✓ `b` ✓ `c` ✓ `d` ✓ `e` ✗, `x` ✓ → 0.8.

  This gives `n_runs` 3, `n_cases` 5, `accuracies` (0.6, 0.6, 0.8), `mean` 2/3, `std`
  √(((−1/15)² + (−1/15)² + (2/15)²)/2) = √(1/75) ≈ 0.11547, `min` 0.6, `max` 0.8, `floor` 2/5
  (`a`, `b`), `ceiling` 4/5, `flaky_case_ids` (`c`, `d`), `flip_rate` 0.4.

  Oracle: the definitions, worked by hand.
- **C2**: Invariants for any valid input. Point checks: the C1 case (0.4 ≤ 0.6 ≤ 2/3 ≤ 0.8 ≤
  0.8, and 0.4 = 0.8 − 0.4), and two identical runs (std 0, no flaky case). The invariants:
  - `floor ≤ min ≤ mean ≤ max ≤ ceiling`;
  - `flip_rate == ceiling − floor`;
  - `std == 0` and `flaky_case_ids == ()` when every run has the same outcomes;
  - reordering the runs changes only the order of `accuracies`;
  - adding cases that are not common to every run changes nothing.

  Oracle: the definitions (metamorphic).
- **C3**: `by_kind[k]` equals `summarize_repeats` applied to the runs restricted to the cases
  of kind `k`. Cases missing from `kinds` do not appear in any kind, but count in the totals.
  With `kinds=None`, `by_kind == {}`.

  Point case: the C1 runs, with kinds `a`, `c` → `lookup` and `b`, `d` → `dimensional`
  (`e` unmapped).
  - `lookup` accuracies are (1.0, 0.5, 1.0) and its flaky case is `c`.
  - `dimensional` accuracies are (0.5, 1.0, 1.0) and its flaky case is `d`.

  Oracle: the definition, worked by hand.
- **C4**: Fewer than 2 runs, or no common case, raises `ValueError`.

  Oracle: Requirement 1.

## Interface

- `src.eval.core.repeats.summarize_repeats(runs: Sequence[Mapping[str, bool]], kinds:
  Mapping[str, str] | None = None) -> dict[str, Any]`
  - It returns a plain dict, so it serialises to JSON for the script's `--out`; a spec PR
    cannot add a data class.
  - Keys and value types: `n_runs: int`, `n_cases: int`, `accuracies: tuple[float, ...]`,
    `mean`, `std`, `min`, `max`, `floor`, `ceiling`: `float`,
    `flaky_case_ids: tuple[str, ...]`, `flip_rate: float`, `by_kind: dict[str, dict]`.

## Notes

- **Out of scope:**
  - persisting the summary;
  - a `--repeat` flag on `run_suite.py`;
  - making `compare_runs.py` aware of flaky cases.

  Each is a possible follow-up once the band is measured.
- **Proposed measurement (maintainer to confirm the cost):** 3 full runs of `sql_derived_v1`
  on one commit (~$2.50 each; `eval-d2884b8dd7c1` can count as the first). Then 1 run of the
  paraphrase suite (~2.4 phrasings per case, ~1,100 cases, ~$6).
- **Synthetic data only:** the tests use hand-made case ids, not real eval runs.
