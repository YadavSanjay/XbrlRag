# r1-report — the pre-registered comparison table (Amendment 1, R1)

Status item B1. The v2 pre-registration
([preregistration.md](../eval/heldout_v2/preregistration.md)) fixes how the final look is read:
§3 (the success bar and "how the bar is read") and Amendment 1 R1 (one table with XbrlRag and
both tools agents, on the same cases). Today nothing builds that table. `eval_stats.py` was
written for external test v1 (2-of-3 rule, `lookup`/`compute` headline) and knows neither the
v2 kinds nor the bar's lines. This change adds a pure, read-only report over stored runs: it
takes one stored run per arm on the same suite and split, pairs them on case id, and produces
the R1 table and every bar line it can compute, as JSON and Markdown. It makes no LLM call and
writes nothing to any database.

## Requirement

1. **Kinds from tags.** `case_groups(tags)` returns the report groups a case belongs to, from
   its `shape:<s>` tag (v2 suites, docs/specs/eval-intents.md, compound-shape.md) or, when
   it has no `shape:` tag, from its H-X kind tag (`src/eval/set_b/hx.py`: the bare tags
   `cross_filer`, `cross_period`, `compound`). The groups are the §3 "Kinds, by suite shape":

   | Shape (or H-X tag) | Groups |
   |---|---|
   | `screen.<anything>`, `peer_relative`; H-X `cross_filer` | `cross_filer`, `line1`, `all` |
   | `trend`, `point_in_time`, `compute.cagr`; H-X `cross_period` | `cross_period`, `line1`, `all` (and `compute.cagr` also `lookup_compute`, see 1a) |
   | `compound.<anything>`; H-X `compound` | `compound`, `line1`, `all` |
   | `lookup` | `single_filer_numeric`, `lookup_compute`, `all` |
   | `compute.<anything>` other than `compute.cagr` | `single_filer_numeric`, `lookup_compute`, `all` |
   | `dimensional`, `breakdown` | `single_filer_numeric`, `all` |
   | any other shape, or no kind tag at all | `all` |

   - The first `shape:` tag decides; other tags are then ignored. Without one, the H-X tags
     are read (at most one of the three appears in a valid H-X case; if more than one does,
     the case is in each named kind, `line1` and `all`).
   - **1a.** `lookup_compute` is §3's "single-filer lookup + compute": `lookup` and the
     single-filer `compute.*` shapes. §3 excludes `compute.cagr` from the single-filer
     *numeric* kinds (it is cross-period) but not from lookup + compute, and a CAGR is
     computed on one filer, so `compute.cagr` is in `lookup_compute` as well
     (Decision for the maintainer 1).
   - Every case is in `all`. The return is a `frozenset[str]`.
2. **Inputs.** An *arm* is a mapping with `label` (unique in the report), `run_id`, `system`,
   `suite`, `split`, `config` (a mapping) and `cases`: a sequence of case rows, each a
   mapping with `case_id`, `tags`, `value_correct`, `confident_wrong`, `grounding_precision`,
   `cost_usd`, `latency_ms` and `error` (the `eval.eval_case_result` columns of those names).
   `None` reads as `False` for the two booleans, `0.0` for the cost and "not recorded" for
   latency and grounding.
3. **Refusals.** `build_r1(arms, reference, bar_agent, oracle=None)` raises `R1Refused` (a
   `ValueError`) before computing anything when:
   - a label repeats, `reference` or `bar_agent` is not a label, `bar_agent == reference`,
     or `oracle` is given and is not a label or equals `reference`;
   - the arms' suites differ (`suite`, compared exactly);
   - an arm's split is not one value: its `split`, its `config["split"]` when present, and
     the `split:` tag of each of its cases when present must agree;
   - the arms' splits differ;
   - an arm holds the same `case_id` twice;
   - two arms classify the same `case_id` into different groups (they were not run on the
     same suite).

   The refusal message names no case id: it may be shown for a held-out run.
4. **Excluded and missing cases.**
   - A row is **excluded** when its `error` starts with `excluded:` (the long-context arm's
     structural exclusion, `src/eval/systems/long_context.py`). An excluded row is counted
     and reported, and leaves the arm's denominator; it is not a failure
     (Decision for the maintainer 2). Every other row is **evaluable**, an errored or
     declined one included (§3: a decline and an error count as failed cases).
   - The case universe `U` is the union of every arm's case ids. A case of `U` with no row in
     an arm is **missing** for that arm. It is counted per arm and per group, and it is left
     out of every pair; it is not a failure (Decision for the maintainer 3). `complete` is
     true exactly when no arm misses a case.
   - A case's groups come from any arm's row of it (requirement 3 guarantees they agree).
5. **Per-arm cells.** For every group in `GROUPS` =
   (`cross_filer`, `cross_period`, `compound`, `line1`, `single_filer_numeric`,
   `lookup_compute`, `all`) and every arm, `r1["groups"][group][label]` holds:
   - `n_cases` (the arm's rows in the group), `n_missing`, `n_excluded`, and
     `n = n_cases − n_excluded` (evaluable rows);
   - `n_value_correct` and `value_accuracy = n_value_correct / n`;
   - `n_confident_wrong` and `confident_wrong_rate = n_confident_wrong / n`
     (docs/specs/arm-neutral-grading.md, C1);
   - `grounding_precision`: for the reference arm, the mean of the non-null
     `grounding_precision` of its evaluable rows (as `Metrics` averages it), `None` when
     there is none; for every other arm the string `"not applicable"` (R1: they cite no
     `fact_id`);
   - `mean_cost_usd = Σ cost_usd / n` over the evaluable rows;
   - `latency_p50_ms`, `latency_p95_ms`: over the evaluable rows with a non-null
     `latency_ms`, the linear-interpolated percentile (`rank = q·(m−1)` over the sorted
     values, as `src.eval.core.graders.base.percentile`); `None` when there is none.

   Every ratio and mean is `None` when its denominator is 0.
6. **Arm facts.** `r1["arms"][label]` holds `run_id`, `system`, `service_tier`
   (`config["service_tier"]`, `None` when absent), `max_workers` and `kind_concurrency`
   (from `config`, `None` when absent), and `n_cases`, `n_missing`, `n_excluded` over `all`.
   R1 states each arm's service tier and concurrency beside its latency.
7. **Paired comparisons.** For every arm other than the reference and every group of
   (`cross_filer`, `cross_period`, `compound`, `line1`), `r1["comparisons"][other][group]`
   holds the paired analysis of the reference against that arm:
   - the **pairs** are the cases of the group that have a row in both arms and are excluded
     in neither (§3: "a pair is left out of a test only when the case was excluded for that
     system"); a case's outcome is its `value_correct`;
   - `n` (pairs), `both`, `reference_only`, `other_only`, `neither`;
   - `n_excluded`: cases of the group with a row in both arms, excluded in at least one;
     `n_missing`: cases of the group missing from at least one of the two arms;
   - `p_value`: the two-sided exact McNemar p on the discordant pairs:
     with `b = reference_only`, `c = other_only`, `m = b + c`,
     `p = min(1, 2 · Σ_{i=0..min(b,c)} C(m, i) / 2^m)`, and `p = 1.0` when `m = 0`;
   - `beats`: `p_value < 0.05` and `reference_only > other_only` (§3 "Beats");
   - for `line1` only: `reference_mean_cost_usd` and `other_mean_cost_usd`, each arm's mean
     `cost_usd` over the same pairs (§3: "the mean cost per question over the same pooled
     cases"), and `cost_ratio = reference_mean / other_mean` (`None` when there is no pair or
     the other mean is 0).

   `line1` is one pooled test over the three kinds, not a combination of their p-values.
8. **Bar lines.** `r1["bar"]` holds one entry per line of §3 as amended, each with a boolean
   `holds`:
   - `line1`: against `bar_agent`: `p_value`, `reference_only`, `other_only`, `cost_ratio`,
     `beats` (requirement 7), `cost_ok` (there are pairs, `other_mean_cost_usd > 0` and
     `10 · reference_mean_cost_usd ≤ other_mean_cost_usd`), and
     `holds = beats and cost_ok`;
   - `line2`: `{"status": "withdrawn", "holds": True}` (Amendment 1 D4 withdrew the
     `html_rag` line; a withdrawn line does not block the bar);
   - `line3` ("within 5 points of `long_context_oracle` on single-filer lookup + compute"):
     on the `lookup_compute` cases with a row in both the reference and the oracle and
     excluded in neither: `n`, `reference_accuracy`, `oracle_accuracy` (value accuracy of
     each over those cases), `difference_points = 100 · (k_ref − k_oracle) / n`, and
     `holds` exactly when `n > 0` and `20 · (k_ref − k_oracle) ≥ −n` (the difference is at
     least −5.0 points, decided on the counts so the boundary is exact). Without an oracle:
     `{"status": "not computed", "holds": False}`;
   - `confident_wrong`: the reference arm over `all`: `n`, `n_confident_wrong`, `rate`, and
     `holds` exactly when `n > 0` and `100 · n_confident_wrong ≤ n` (≤ 1%);
   - `grounding`: `value` = the reference's `grounding_precision` over `all`, and `holds`
     exactly when `value` is not `None` and `value ≥ 0.95`;
   - `stability`: per intent over the reference's evaluable rows of `all`: rows grouped by
     their `intent:` tag (rows without one ignored), intents with at least two rows kept;
     `n_intents` kept, `n_stable` of them whose rows all have the same `value_correct`,
     `value = n_stable / n_intents` (`None` when none is kept), and `holds` exactly when
     `n_intents > 0` and `10 · n_stable ≥ 9 · n_intents` (≥ 90%). The outcome is
     `value_correct`, §3's comparison metric (Decision for the maintainer 4);
   - `met`: `holds` of every entry above, together.
9. **Held-out runs: aggregates only.** The report is *held-out* when any arm's `split` or
   `config["split"]` is in `HELDOUT_SPLITS` (`heldout_filers`, `external`, `temporal`; the
   constant of `src.eval.core.regrade`), any arm's suite starts with `heldout`
   (case-insensitive), or any case row carries a held-out `split:` tag or the `set-b` tag
   (the checks of `scripts/eval/run_report.py`). Then `r1["heldout"]` is `True` and
   `r1["case_level"]` is `None`: nothing in the result names a case. Otherwise
   `r1["case_level"]` is
   `{"missing": {label: [ids]}, "excluded": {label: [ids]}, "discordant": {other: {"reference_only": [ids], "other_only": [ids]}}}`,
   each list sorted, the discordant pairs taken over `all`. `r1` also carries `suite`,
   `split`, `heldout`, `complete`, `reference`, `bar_agent` and `oracle`, and is JSON
   serialisable with `allow_nan=False`.
10. **Markdown.** `render_markdown(r1)` returns the table as Markdown and never prints a
    case id, whatever the split. It holds one table whose header row is `| | <label> | ... |`
    with the arms in input order, and whose body rows begin with these labels, each followed
    by one cell per arm in the same order:
    - `value accuracy, cross-filer`, `value accuracy, cross-period`,
      `value accuracy, compound`, `value accuracy, pooled`, `value accuracy, lookup + compute`:
      each cell `<pct> (<k>/<n>)`, the percentage to one decimal (`85.0%`), or `-` when
      `n = 0`;
    - `confident-wrong`: the `all` cell, same format;
    - `grounding precision`: the reference's percentage to one decimal (`-` when `None`);
      `not applicable` for every other arm;
    - `mean cost per question`: the `all` mean;
    - `latency p50 / p95 (ms)`;
    - `service tier`: the arm's service tier, or `not recorded`;
    - `concurrency`: contains the arm's `max_workers` (or `not recorded`).

    After the table, one line per bar entry: line 1 shows its `p_value` formatted as
    `f"{p:.3g}"`, line 2 the word `withdrawn`, and every line `met` or `missed`.
11. **Loading.** `load_arm(label, run_id, reader)` reads one stored run through `reader`, an
    object with `run_row(run_id) -> Mapping | None` and
    `case_rows(run_id) -> Sequence[Mapping]` (the SELECT-only reader of `run_report.py`,
    the database boundary), and returns the arm mapping of requirement 2:
    - `split` is the row's `split` when non-empty, else `config["split"]`, else `legacy`; `config` is the
      row's `config`, decoded when it is a JSON string, `{}` when `None`;
    - each case keeps the eight fields of requirement 2, `tags` `[]` when `None`;
    - an unknown run id, or a run with no case rows, raises `R1Refused`.
12. **CLI** (wiring, implementer): `scripts/eval/r1_report.py --arm <label>=<run_id> ...
    --reference <label> --bar-agent <label> [--oracle <label>] --out-json <path>
    --out-md <path>`, reading through a read-only session as `run_report.py` does. It
    prints the Markdown. No paid call, no write to any database.

## Acceptance criteria

All inputs are synthetic case rows and run rows. The only fake is the reader of requirement
11 (the database boundary). Counts are rational, so expected rates are compared at
`abs=1e-12` (float division only).

- **C1**: kinds. Each row of the table of requirement 1 maps to its groups exactly
  (`screen.top`, `screen.sum`, `peer_relative`, `trend`, `point_in_time`, `compute.cagr`,
  `compute.yoy_growth`, `compute.ratio`, `lookup`, `dimensional`, `breakdown`,
  `compound.rank_by_ratio`, the H-X tags `cross_filer`/`cross_period`/`compound`, an
  unknown shape, no tags); a `shape:` tag overrides an H-X tag. Properties: `line1` is in the
  groups exactly when one of the three line-1 kinds is; `cross_filer`, `cross_period`,
  `compound` and `single_filer_numeric` are pairwise disjoint over every shape; `all` is
  always present; shuffling the tags and adding unrelated tags does not change the result.
  Oracle: requirement 1 and the pre-registration §3 shape list.
- **C2**: refusals. Each condition of requirement 3 raises `R1Refused` (also a
  `ValueError`); the message of a refusal over held-out runs contains no case id.
  Property: two arms with different random splits always refuse; equal splits and suites
  with consistent rows never refuse. Oracle: requirement 3.
- **C3**: per-arm cells. On a hand-built three-arm run with exclusions, errors, declines
  and missing cases, every count, rate, mean and `grounding_precision` cell of the
  requirement-5 groups equals the hand count; `n_cases = n + n_excluded`;
  `n_cases + n_missing = |U ∩ group|`; arm facts (requirement 6) match the configs.
  Metamorphic: shuffling each arm's rows and the order of arms (with the same roles) does
  not change any cell. Oracle: requirements 4 to 6, hand counts.
- **C4**: paired McNemar. On hand-built pairs the counts and `p_value` match the closed
  form (for example 8 against 1 discordant: `p = 2 · (1 + 9) / 512 = 0.0390625`, `beats`;
  1 against 8: same `p`, not `beats`; no discordant pair: `p = 1`); excluded and missing
  cases are left out and counted; `line1` pools the three kinds (its cells are the sums of
  theirs when each case is in one kind). Properties: for random outcomes, `p_value` equals
  the closed form computed in the test with exact integers, is symmetric under swapping
  the two arms' outcomes, and `beats` follows its definition. Oracle: requirement 7 and §3.
- **C5**: cost and latency. Mean cost per question and latency p50/p95 match hand
  computation (for example latencies 100, 200, 300, 400, 500 ms give p50 300 and p95 480);
  excluded rows and null latencies are left out; the line-1 cost ratio uses the pairs only.
  Properties: scaling every cost of an arm by `s > 0` scales its means by `s`;
  `p50 ≤ p95`; the percentiles equal the linear-interpolation closed form computed in the
  test. Oracle: requirements 5 and 7, hand arithmetic.
- **C6**: bar lines. Line 1 holds only with `beats` and `cost_ok` (cost ratio exactly 0.1
  holds, just above it does not); line 2 is `withdrawn` and holds; line 3 at exactly −5.0
  points holds and one more oracle-only win misses, and without an oracle is `not computed`
  and misses; confident-wrong at 1 of 100 holds and 2 of 100 misses; grounding at 0.95
  holds and below misses; stability at 9 of 10 intents holds and 8 of 10 misses; `met` is
  the conjunction. Properties: for random counts each line's `holds` equals its count
  inequality computed in the test. Oracle: requirement 8 and §3.
- **C7**: held-out. For each held-out trigger of requirement 9 `heldout` is `True`,
  `case_level` is `None`, and neither `json.dumps(r1)` nor `render_markdown(r1)` contains
  any case id; for a dev run `case_level` lists the hand-expected missing, excluded and
  discordant ids. Property: random distinctive case ids never appear in the JSON of a
  held-out report nor in the Markdown of any report; the JSON dumps with
  `allow_nan=False`. Oracle: requirement 9 and §5 of the pre-registration.
- **C8**: Markdown. The header lists the labels in input order; the
  `grounding precision` row has the reference's percentage and `not applicable` for each
  other arm; the `value accuracy, pooled` row has each arm's `k/n`; `service tier` and
  `concurrency` rows carry each arm's values; the line-1 `p_value` (`.3g`) and `withdrawn`
  appear, and `met`/`missed` match the bar. Metamorphic: rendering does not mutate `r1`
  and is deterministic. Oracle: requirement 10.
- **C9**: loading. With a fake reader, `load_arm` maps a run row and its case rows to the
  arm mapping (split from the row, else the config, else `legacy`; a JSON-string config
  decoded; `None` tags as `[]`); an unknown run and a run without case rows raise
  `R1Refused`. Metamorphic: `build_r1` over `load_arm` results equals `build_r1` over the
  same arms built by hand. Oracle: requirement 11.

## Interface

New, in `src/eval/core/r1_report.py` (stubbed in the spec PR):

- `class R1Refused(ValueError)`
- `case_groups(tags: Iterable[str]) -> frozenset[str]`
- `build_r1(arms: Sequence[Mapping[str, Any]], reference: str, bar_agent: str, oracle: str | None = None) -> dict[str, Any]`
- `render_markdown(r1: Mapping[str, Any]) -> str`
- `load_arm(label: str, run_id: str, reader: Any) -> dict[str, Any]`

The implementer may add module constants (`GROUPS`, the line-1 kinds) and reuse
`src.eval.core.stats.mcnemar_exact`, `src.eval.core.graders.base.percentile` and
`src.eval.core.regrade.HELDOUT_SPLITS`. The CLI `scripts/eval/r1_report.py` and a
read-only reader (as `PostgresRunReader` in `run_report.py`, without the audit reads) are
wiring the implementer adds.

## Notes

- **What this does not decide.** The report computes the bar's lines on whatever split it is
  given. Only H-F (`heldout_filers`) at the final milestone decides the bar (§1, §3); a dev
  or validation report is a rehearsal. H-X confirms direction by pass counts with Wilson
  intervals (§4); that reading is out of scope here (`eval_stats.py` already gives Wilson
  intervals). Set B and H-T are reported on their own lines and never pooled (§1): one
  report per set, and the split refusal enforces it.
- **The `long_context_oracle` arm on H-F** runs single-filer cases only (Amendment 1 D2). If
  it is run on the whole suite, its cross-filer rows are structural exclusions (as on the dev
  sample, 1.4) and the report stays `complete`. If it is run on a tag-filtered suite, its
  cross-filer cases are missing and `complete` is false; its pairs are unaffected.
- **Repeats.** Amendment 1 item 4: no repeats, so one run per arm. The 2-of-3 rule has no
  case to apply to and is not implemented here.
- **Decisions for the maintainer** (each flagged where the pre-registration is silent or
  reads two ways; the spec locks the recommendation):
  1. **`compute.cagr` in "single-filer lookup + compute".** §3 excludes it from the
     single-filer numeric kinds, but the lookup + compute line says only "`lookup` and the
     single-filer `compute.*` shapes". *Recommendation:* include it (the literal text; a
     CAGR is one filer's computation and the oracle answers it). The alternative excludes
     it, making lookup + compute a subset of single-filer numeric.
  2. **Structural exclusions and an arm's accuracy.** §3 says an exclusion "is counted and
     reported" and leaves a pair out of a test. *Recommendation:* it also leaves the arm's
     own denominator (not a failure), and every cell shows `n_excluded`. Otherwise
     `long_context_oracle` would score 0 on the cross-filer cases it is not meant to answer.
  3. **Missing rows.** §3 does not foresee a case with no stored row (an interrupted run is
     resumed, Amendment 1 item 4). *Recommendation:* pair over the intersection, count the
     missing cases per arm and group, set `complete: false`, and do not count them as failed;
     the bar's `met` is reported beside `complete`, not gated by it. A report with
     `complete: false` should not be published as the bar verdict without saying so.
  4. **Paraphrase stability's outcome.** The run column (`eval-run-columns`, requirement 10)
     uses `passed`. *Recommendation:* the bar line uses `value_correct`, §3's comparison
     metric for every system, over evaluable rows only.
  5. **Cost ratio's cases.** *Recommendation:* the line-1 pairs (both arms have an evaluable
     row), errored cases included at their recorded cost.
- **Oracles:** hand counts and closed forms in the tests; no suite file, no database, no
  network, no `data/` path.
