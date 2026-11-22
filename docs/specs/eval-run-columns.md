# eval-run-columns — record what v2 slices by, on every run and case

v2 reads every result by split, phrasing family, answer-key source and intent
(docs/design/XbrlRag_v2_Plan.md §3.1, §3.2, §3.3, §3.5, §3.8). Today the `eval` schema
records none of these: a run does not say whether it ran dev, validation or a held-out
set, and a case row does not say which intent it phrases, in which family, or where its
key came from. This change adds those columns, defines where their values come from,
and adds the report slices built on them.

The values come from `EvalCase.tags`, so the suite generator (eval-intents), the key
builders (sec-keys) and the judge (eval-judge) can produce them without importing this
module, and this module does not import theirs.

## Requirement

1. **Tag mapping.** `slice_fields_from_tags(tags)` in the new module
   `src/eval/core/run_columns.py` reads these tag prefixes (case-sensitive, the value is
   everything after the **first** colon, kept verbatim):

   | Tag | Field |
   |---|---|
   | `split:<s>` | `split` |
   | `intent:<id>` | `intent_id` |
   | `family:<f>` | `phrasing_family` |
   | `template:<id>` | `template_id` |
   | `key_source:<src>` | `key_source` |
   | `key_status:<st>` | `key_status` |

   It returns a dict with exactly those six keys; a field with no tag is `None`. Other
   tags are ignored (`splitter:x` is not a `split:` tag). It raises `ValueError` when:
   - a prefix appears with an empty value (`split:`);
   - one prefix appears with two different values (the same value twice is fine);
   - `split` is not one of `dev`, `validation`, `heldout_filers`, `external`,
     `temporal`, `legacy`;
   - `key_status` is not one of `verified`, `single-source`.

   `key_source` is free text (§3.8 names `sec_companyfacts`, `sec_frames`, `html_hand`,
   `fact_store`; more may come) and is not validated.
2. **One split per run.** `run_split(cases)` returns the run's split. A case's
   *effective split* is its `split:` tag, or `legacy` when it has none. If every case has
   the same effective split, that is the result; an empty input gives `legacy`. Two or
   more effective splits raise `MixedSplitError` (a `ValueError` subclass) whose message
   names them. Every case's tags are parsed, so a malformed tag (requirement 1) also
   raises `ValueError` here.
3. **The runner checks the split first.** `EvalRunner.run` calls `run_split` on its
   `cases` before any case is answered, and stores the result in
   `report.config["split"]`. A mixed run therefore raises before any system call (no money
   spent) and leaves nothing to persist.
4. **Judge sub-scores.** `judge_columns(scores)` maps the judge's scores (§3.5) to four
   columns:

   | Score key | Column | Allowed values |
   |---|---|---|
   | `correctness` | `judge_correctness` | float in [0, 1] |
   | `completeness` | `judge_completeness` | float in [0, 1] |
   | `citation_support` | `judge_citation_support` | float in [0, 1] |
   | `unsupported_claims` | `judge_unsupported_claims` | int >= 0 (a count) |

   `None` (no judge ran) or a missing key gives `None` for that column. A value outside
   its range, a NaN, a non-integral claim count, or an unknown key raises `ValueError`.
   An integral float claim count (`2.0`) is returned as the int `2`.
5. **Case columns.** `case_columns(result)` returns the six tag fields of
   `result.case.tags` (requirement 1) merged with `judge_columns` of
   `result.grade.judge_scores` (requirement 4): exactly ten keys. A grade without a
   `judge_scores` attribute counts as `None`. `judge_scores` is the attribute the judge
   (eval-judge) sets on a `CaseGrade`: a mapping with the four score keys, or `None`.
6. **Run columns.** `run_columns(report)` returns `{"split": ..., "judge_version": ...}`:
   - `split`: `report.config["split"]` when present. It must be one of the six splits
     (else `ValueError`) and, when `report.results` is non-empty, must equal
     `run_split` over the results' cases (else `MixedSplitError`). Without the config key,
     it is `run_split` over the results' cases.
   - `judge_version`: `report.config["judge_version"]` when present and non-empty, else
     `None`. The judge's runner integration records it there (the judge's prompt and
     model version, §3.5).
7. **Storage.** In `scripts/ddl/eval/eval_runs.sql`, additive and idempotent, after the
   `CREATE TABLE` of each table:
   - `eval.eval_run`: `split TEXT NOT NULL DEFAULT 'legacy'` and `judge_version TEXT`.
     The default is the legacy rule: every row written before this change becomes
     `legacy` when the statement runs, and so does any writer that omits the column.
   - `eval.eval_case_result`: `intent_id`, `phrasing_family`, `template_id`, `key_source`,
     `key_status` (all `TEXT`), `judge_correctness`, `judge_completeness`,
     `judge_citation_support` (`DOUBLE PRECISION`) and `judge_unsupported_claims`
     (`INT`), all nullable.
   - each as `ALTER TABLE eval.<table> ADD COLUMN IF NOT EXISTS <col> <type>;`. Every
     `ADD COLUMN` in the file keeps `IF NOT EXISTS`, so the file re-applies cleanly.
8. **The store writes them.** In `src/eval/core/store.py`:
   - `_INSERT_RUN` lists `split` and `judge_version`; `PostgresEvalStore._run_params`
     fills them from `run_columns(report)`. The `ON CONFLICT` update sets
     `judge_version` too.
   - `_INSERT_CASE` lists the nine new case columns (requirement 7: `case_columns`
     without `split`, which lives on the run row); `_case_params` fills them from
     `case_columns(result)`. The `ON CONFLICT` update sets the four judge columns too.
   - `paraphrase_stability` on the run row is `intent_stability(results)` when that is
     not `None`, else today's `paraphrase_stability(results)` (requirement 10).
   - `persist_report_dict(data)` (re-import of a report JSON): `split` is
     `data["split"]`, or `legacy` when absent or empty, and an unknown split raises
     `ValueError` before connecting; `judge_version` is `data.get("judge_version")`; each
     case row's five tag columns come from `slice_fields_from_tags(c["tags"])` and its four
     judge columns from `c.get("judge_correctness")` etc. (`None` when absent). An old
     report JSON therefore imports as `legacy` with its tag fields filled from its tags.
9. **The report JSON carries them.** `RunReport.to_dict` adds top-level `split` and
   `judge_version` (from `run_columns(self)`) and `slices` (from
   `slice_report(self.results)`); its `paraphrase_stability` follows the rule in
   requirement 8. `CaseResult.to_dict` adds the ten keys of `case_columns(self)`. So
   `persist_report_dict(report.to_dict())` writes the same new columns as
   `persist_run(report)`.
10. **Report slices.**
    - `slice_metrics(results, field)` buckets results by one of the six tag fields
      (requirement 1; another name raises `ValueError`) and returns
      `{value: Metrics.compute(bucket)}`, keys sorted, with results whose field is `None`
      under the key `"(none)"`. Accuracy is `Metrics.accuracy` (grade `CORRECT` over
      the bucket's cases), as everywhere else.
    - `intent_stability(results)`: group results by `intent_id`; ignore results without
      one; keep the groups with two or more results; the value is the number of kept
      groups whose results all have the same `passed`, divided by the number of kept
      groups. `None` when no group is kept (never a vacuous 1.0).
    - `slice_report(results)` returns
      `{"by_split": ..., "by_phrasing_family": ..., "by_key_source": ...,
      "intent_stability": ...}`, where each `by_*` maps the bucket key to
      `Metrics.to_dict()` of `slice_metrics(results, field)`.

## Acceptance criteria

Fakes sit only at I/O boundaries: a fake `SystemUnderTest` behind the real `EvalRunner`,
and a fake `psycopg2.connect` (a recording connection and cursor that, like psycopg2,
raises `KeyError` for a `%(name)s` placeholder missing from its parameters). The committed
DDL file is read as text. Graders, `Metrics`, `RunReport`, the runner and the store are
real. Cases and answers are synthetic.

- **C1**: tag mapping. `["split:dev", "family:terse", "template:t7:v2", "intent:i-42",
  "key_source:sec_companyfacts", "key_status:verified", "kind:lookup"]` maps to
  `{split: dev, phrasing_family: terse, template_id: "t7:v2", intent_id: i-42,
  key_source: sec_companyfacts, key_status: verified}`; `[]` maps to six `None`s;
  `splitter:x` is ignored; each `ValueError` case of requirement 1 raises; a repeated
  identical tag is fine. Property: for random field values rendered as tags, shuffled and
  mixed with unrelated tags, parsing gives back exactly those values. Oracle:
  requirement 1.
- **C2**: one split per run. All-`dev` cases give `dev`; untagged cases and `[]` give
  `legacy`; `split:legacy` plus untagged gives `legacy`; `dev` plus `validation`, and
  `dev` plus untagged, raise `MixedSplitError` (also a `ValueError`) naming both splits;
  a malformed tag raises `ValueError`. Property: random cases with one effective split
  give it; adding one case with another split raises. Oracle: requirement 2.
- **C3**: the runner. Over mixed cases `EvalRunner.run` raises `MixedSplitError` and the
  fake system answered nothing; over `heldout_filers` cases `report.config["split"] ==
  "heldout_filers"`, and over untagged cases `legacy`. Metamorphic: the outcome doesn't
  depend on case order. Oracle: requirement 3.
- **C4**: judge columns. `{correctness: 0.8, completeness: 0.5, citation_support: 1.0,
  unsupported_claims: 2}` maps to the four columns with those values; `None` and `{}`
  give four `None`s; a partial mapping gives `None` for the rest; `1.2`, `-0.1`, NaN,
  claims `-1` or `1.5`, and an unknown key raise `ValueError`; claims `2.0` gives int `2`.
  Property: random in-range scores map one-to-one. Oracle: requirement 4.
- **C5**: case and run columns. A graded result with tags and `judge_scores` gives the
  ten expected values; without `judge_scores` the judge columns are `None`.
  `run_columns`: config split wins; without it, the results' split; empty results and no
  config give `legacy`; an unknown config split raises `ValueError`; a config split
  disagreeing with the results raises `MixedSplitError`; `judge_version` from config or
  `None` (also for `""`). Property: random tag sets and scores give the expected ten
  values. Oracle: requirements 5 and 6.
- **C6**: the DDL. Reading `scripts/ddl/eval/eval_runs.sql` as text (whitespace and
  letter case normalized): each of the eleven columns of requirement 7 has an
  `ALTER TABLE eval.<table> ADD COLUMN IF NOT EXISTS` with its type (`INT` or `INTEGER`;
  `split` with `NOT NULL DEFAULT 'legacy'` in either order), placed after that table's
  `CREATE TABLE`; every `ADD COLUMN` in the file has `IF NOT EXISTS`. Property: re-spacing
  and re-casing the SQL doesn't change the checks. Oracle: requirement 7.
- **C7**: the store writes them. With the fake driver, `persist_run` returns `True`; the
  run row's SQL lists `split` and `judge_version` and its parameters carry the run's
  values; each case row lists the nine new columns with the case's values; `paraphrase_stability`
  is the intent stability (hand-computed) when intents are tagged, else the
  paraphrase-group stability. `persist_report_dict` on a v1-shaped dict (no `split`, no
  judge keys) writes `legacy`, the tag fields from its tags and `None` judge columns; an
  unknown split raises `ValueError`. Metamorphic: `persist_report_dict(report.to_dict())`
  writes the same new run and case columns as `persist_run(report)`. Oracle:
  requirements 8 and 9.
- **C8**: report slices. On a hand-built run (four intents, two families, two key
  sources, mixed outcomes), `slice_metrics` gives the hand-counted `n` and accuracy per
  bucket, with `"(none)"` for untagged; an unknown field raises `ValueError`;
  `intent_stability` equals the hand count (single-result intents and untagged cases
  ignored), `None` with no multi-result intent; `RunReport.to_dict()` carries `split`,
  `judge_version` and `slices` with those values, and each result dict carries the ten
  case columns. Property: for random intents and outcomes, `intent_stability` equals the
  closed form computed in the test and is invariant under shuffling; the bucket sizes of
  `slice_metrics` sum to the number of results. Oracle: requirements 9 and 10.

## Interface

New (stubbed in the spec PR), in `src/eval/core/run_columns.py`:

- `class MixedSplitError(ValueError)`
- `slice_fields_from_tags(tags: Iterable[str]) -> dict[str, str | None]`
- `run_split(cases: Iterable[EvalCase]) -> str`
- `judge_columns(scores: Mapping[str, Any] | None) -> dict[str, float | int | None]`
- `case_columns(result: CaseResult) -> dict[str, Any]`
- `run_columns(report: RunReport) -> dict[str, Any]`
- `slice_metrics(results: Sequence[CaseResult], field: str) -> dict[str, Metrics]`
- `intent_stability(results: Sequence[CaseResult]) -> float | None`
- `slice_report(results: Sequence[CaseResult]) -> dict[str, Any]`

The implementer may add module constants (the split list, the key statuses, the tag
prefixes); a spec PR can only add stubs.

Wiring on existing code, which the implementer adds (requirements 3, 7, 8, 9):

- `EvalRunner.run`: `run_split(cases)` first, into `report.config["split"]`.
- `scripts/ddl/eval/eval_runs.sql`: the `ALTER TABLE` block.
- `store.py`: `_INSERT_RUN`, `_INSERT_CASE`, `_run_params`, `_case_params`,
  `persist_report_dict`.
- `RunReport.to_dict`, `CaseResult.to_dict`.
- `CaseGrade` may gain a `judge_scores` field defaulting to `None` (the judge may add it
  first); `case_columns` must also work when the attribute is absent.

`run_columns.py` imports `CaseResult`, `Metrics` and `RunReport` from
`src.eval.core.report`, so `report.py` must import `run_columns` lazily (inside
`to_dict`) to avoid an import cycle.

## Notes

- **Why a mixed run is rejected, not recorded as `mixed`.** The split column exists so
  every number can be read within one split; a `mixed` run has no meaning in any of them.
  Worse, it is how a held-out case would leak into a dev run: §3.7 says held-out sets are
  not looked into, and §3.1 caps held-out looks at three, each logged. A dev slice that
  silently included held-out cases would spend a look nobody logged. Raising in
  `EvalRunner.run`, before the first case, costs nothing; the operator narrows the run
  with `--tag split:<s>`. Untagged cases are `legacy`, so a legacy suite still runs
  unchanged, and a half-tagged suite is caught as mixed rather than half-labelled.
- **Why `legacy` is a column default, not a nullable column.** Old rows then need no
  backfill and can't read as an unknown split; `ADD COLUMN ... NOT NULL DEFAULT` fills
  them when it runs. A writer that predates this change (an old checkout) also lands in
  `legacy`, which is what its cases are.
- **No CHECK constraint** on `split` or `key_status`: Postgres has no
  `ADD CONSTRAINT IF NOT EXISTS`, and the file must stay re-appliable as plain SQL.
  Validation is in Python (requirements 1 and 6), which every writer goes through.
- **Coordination.** eval-intents emits `split:`, `family:`, `template:` and `intent:`
  tags; sec-keys emits `key_source:` and `key_status:`; eval-judge sets
  `CaseGrade.judge_scores` and `config["judge_version"]`. Each side depends only on the
  tag spelling and attribute names above. If a sibling spec names the attribute
  differently, the later implementer adds an alias rather than changing locked tests.
- **Paraphrase stability.** §3.2 defines it per intent and says the `eval_run` column
  exists but v1 never filled it. The column now holds intent stability whenever intents
  are tagged; for a legacy suite it keeps today's paraphrase-group value, so old and new
  runs remain comparable within their own split.
- All fixtures are synthetic. No network, no database, no `data/`.
