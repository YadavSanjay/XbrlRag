# eval-regrade — re-grade a stored run against a rebuilt suite, and store the result

Upcoming key fixes rebuild the frozen v2 dev and validation suites and the 100-case dev sample
with corrected answer keys, keeping every case's `id` and question text. The stored runs (the
DeepSeek dev baseline `eval-9a99afc97d4b`, validation `eval-0051272815db`, the Anthropic smoke
reports in `data/eval/anthropic_smoke/2026-11-05/`, and every credit run after them) must then be
re-graded against the new keys from their stored answers, with no system called.

**What exists today.** `scripts/eval/compare_runs.py` has `regrade(rows, cases)`, which rebuilds
each stored answer from its `produced` payload and grades it with today's graders against a
suite; `summarize_repeats.py` uses it too. It is an in-memory comparison only:

- it never stores the result, so a corrected accuracy exists only in a terminal;
- a case missing from the suite is silently skipped, and it never checks that the stored
  question is the suite's question, so an answer can be graded against another question's key;
- it never checks the run's split against the suite's;
- it drops `selection` and `agent` from the rebuilt answer, and every judge score;
- it records no provenance (which run, which suite, which grader).

Route: locked spec. Re-grading decides the reported accuracy of every baseline and credit run,
and a wrong re-grade (a dropped case, a key matched to the wrong question, a judge score carried
onto a changed key) still prints one plausible accuracy. The right answer is independently
stated: re-grading against the unchanged suite must reproduce the stored grades, and a corrected
key changes exactly the cases it touches, by hand count.

## Requirement

New module `src/eval/core/regrade.py`, a CLI `scripts/eval/regrade_run.py`, and the run-row
fields `load_source` needs from `PostgresEvalStore.load_run_row`.

### The source run

1. **Shape.** A source is a dict in the form of `RunReport.to_dict()` (a `run_suite.py --out`
   JSON report) or of `load_source` (requirement 9): run-level keys `run_id`, `suite`,
   `suite_version`, `system`, `tier`, `corpus_fingerprint`, `git_sha`, `started_at`,
   `finished_at`, `status`, `n_planned`, `split`, `grader_version`, `config`, and `results`, a
   list of per-case dicts as `CaseResult.to_dict()` or `checkpoint_payload()` writes them. A
   result is read for `case_id`, `question`, `produced`, `grade`, `expected`, `query_id`,
   `latency_ms`, `cost_usd`, `served_models`, `judge_scores`, `judge_error` and
   `judge_cost_usd`; a missing optional key reads as `None`, `0.0` or `{}`. A result's judge
   scores are its `judge_scores` when that key is present; otherwise, as `CaseResult.to_dict()`
   writes them, the four columns `judge_correctness`, `judge_completeness`,
   `judge_citation_support`, `judge_unsupported_claims` read back as `{"correctness", ...,
   "unsupported_claims"}` when any of them is not `None`, else `None`. The source's split is
   `source["split"]`, else `source["config"]["split"]`, else `"legacy"`.

### Rebuilding an answer

2. `answer_from_payload(produced)` returns the `SystemAnswer` with `text`, `numeric_values`,
   `rows`, `fact_ids`, `chunk_ids`, `refused`, `hedged`, `error`, `selection` and `agent` read
   from the payload (missing: `""`, `[]`, `False`, `None`, `{}`), and `query_id`, `cost_usd`,
   `latency_ms`, `raw` and `served_models` at their defaults. For any answer `a` and case `c`,
   `grade_case(c, answer_from_payload(json.loads(json.dumps(a.to_dict()))))` equals
   `grade_case(c, a)` field by field, and its `to_dict()` equals `a.to_dict()`.

### Re-grading

3. **Refusals.** `regrade_report(source, cases, ...)` raises `RegradeRefused` (a `ValueError`)
   and returns nothing when one of these holds. The message names the offending case ids for
   (b)–(e), contains `split` for (f) and names the run id for (g).
   - (a) `source["results"]` is empty or missing;
   - (b) a result's `case_id` is not in `cases`;
   - (c) a result's `question` is not exactly (string equality) the case's `question`;
   - (d) two results have the same `case_id`;
   - (e) a result has no `produced` dict;
   - (f) the source's split (requirement 1) differs from `run_split` of the matched cases, or the
     matched cases mix splits;
   - (g) `run_id` is given and equals `source["run_id"]`.
   Cases in `cases` with no result are ignored: they are neither graded nor planned.
4. **Grades.** Each result's grade is `grade_case(case, answer_from_payload(result["produced"]))`
   for the suite's case with that id, through the current graders. The report's results follow
   the source's result order, one per source result.
5. **Carried per case.** `query_id`, `latency_ms` and `cost_usd` (exact), `served_models`,
   `judge_error` and `judge_cost_usd` are the source result's. `judge_scores` is the source
   result's, except when it is stale: the source result has `judge_scores` that are not `None`,
   and the new case is not text-graded (`is_text_graded`) or its `expected_text` differs from the
   source result's `expected["text"]` (missing: `None`). A stale case gets `judge_scores` `None`.
   The judge is never called.
6. **The new run.** `regrade_report` returns a `RunReport` with:
   - `run_id`: the one given, else `"eval-"` plus 12 lowercase hex digits, never the source's;
   - `suite`, `suite_version`, `corpus_fingerprint`: the arguments (the rebuilt suite's);
   - `system`, `tier`, `git_sha`, `started_at`, `finished_at`, `status`: the source's (the run
     that answered); `n_planned`: the source's, else the number of results;
   - `notes`: `"regrade of <source run_id>"`;
   - `config`: a copy of the source's config with `"resume_identity"` removed (a re-graded run is
     never resumed: `check_resume` refuses it), `"split"` set to the source's split, and
     `"regrade"` set to:

     | key | value |
     |---|---|
     | `source_run_id` | `source["run_id"]` |
     | `root_run_id` | `source["config"]["regrade"]["root_run_id"]` when the source is itself a re-grade, else `source["run_id"]` |
     | `source_suite`, `source_suite_version` | the source's `suite`, `suite_version` |
     | `source_suite_hash` | `source["config"]["resume_identity"]["suite_hash"]`, else `source["config"]["regrade"]["suite_hash"]`, else `None` |
     | `source_grader_version` | `source["grader_version"]`, else `None` |
     | `suite_hash` | `suite_hash(matched cases)` (the eval-run-checkpoint hash) |
     | `grader_version` | `grader_version()` |
     | `regraded_at` | the `regraded_at` argument, else the current UTC time in ISO 8601 |
     | `n_regraded` | the number of results |
     | `n_grade_changed` | results whose new grade value differs from the source result's `grade` (a missing `grade` counts as changed) |
     | `judge_stale` | the stale case ids of requirement 5, sorted |
     | `judge_rerun` | `False` |

   Everything else (metrics, `by_kind`, `run_columns`, the scorecard) is computed by `RunReport`
   from the re-graded results as for any run.
7. **Identity.** Re-grading a source against the very cases it was graded with, under the same
   grader version, gives every case its stored grade, `n_grade_changed == 0`, and
   `metrics.to_dict()` equal to the source run's. Re-grading a re-graded report's `to_dict()`
   against the same cases changes no grade and keeps `root_run_id`.
8. **A corrected key.** When only some cases' keys change, only those cases' grades may change;
   every other case keeps its grade, its `checks` and its grounding values.

### Loading a stored run

9. `load_source(store, run_id)` builds a source from a `RunStore`: the run-level keys from
   `store.load_run_row(run_id)` (with `split` from the row, else from its `config`, else
   `"legacy"`; `n_planned` from the row, else the number of payloads) and `results` from
   `store.load_case_payloads(run_id)`. It raises `RegradeRefused` when the run is unknown (the
   message names the run id), when its status is `RUNNING` (in progress or crashed: a partial
   run must be resumed, not re-graded; the message says `RUNNING`), or when it has no payloads
   (a run stored before `result_payload`: re-grade its `--out` JSON instead; the message says
   `result_payload`). `PostgresEvalStore.load_run_row` also returns `n_planned`, `split`,
   `grader_version` and `corpus_fingerprint`.

### Reporting

10. `regrade_summary(source, regraded)` returns `{"split", "n", "transitions", "changed"}`:
    `split` the re-graded run's split; `n` its number of results; `transitions` a dict
    `"<before> -> <after>"` → count over the results whose grade changed (before is the source
    result's `grade`, `"(none)"` when missing); `changed` the list of
    `{"case_id", "kind", "before", "after"}` for those results in source order. For a held-out
    split (`heldout_filers`, `external`, `temporal`) `changed` is `None`: only aggregates of a
    held-out run are shown.
11. **CLI** (not locked; the implementer's unit tests). `scripts/eval/regrade_run.py --source
    <run id | report.json> --suite <suite.json> [--run-id ID] [--out PATH] [--no-store]` loads
    the source (a `.json` path that exists is read as a report, anything else through
    `load_source(PostgresEvalStore.from_env(), ...)`), re-grades it, prints the source's stored
    scorecard, the re-graded scorecard and `regrade_summary`, writes `--out`, and persists
    through `PostgresEvalStore.finish_run` unless `--no-store`. It exits 2 with the message on
    `RegradeRefused`, and 1 when persisting was asked for and failed. It makes no LLM, network or
    system call.

## Acceptance criteria

- **C1**: answer rebuild — requirement 2 for answers with values, rows, citations, decline flags,
  errors, `selection` and `agent`; missing keys default. Oracle: the original answer and its
  grade (identity).
- **C2**: refusals — each condition of requirement 3 refuses with `RegradeRefused` naming the case
  id (or the split / run id), and any single changed character in a question refuses. Oracle:
  requirement 3.
- **C3**: identity — a source produced by the real runner on a fake system re-grades to the same
  grade per case, the same `metrics.to_dict()`, and `n_grade_changed == 0`; re-grading the
  re-grade is idempotent. Oracle: the source run (identity), requirement 7.
- **C4**: a corrected key — hand-built runs where a key moves onto (or off) the value an answer
  stated flip exactly those cases, with accuracy, value accuracy and confident-wrong counts by
  hand; untouched cases keep grade, checks and grounding. Oracle: hand counts, requirement 8.
- **C5**: provenance — `run_id` is new (or the given one), the run fields and every
  `config["regrade"]` value of requirement 6, `suite_hash` computed in the test with `hashlib`,
  `resume_identity` gone and `check_resume` refusing, cost, latency and query ids exact, and the
  chain's `root_run_id`. Oracle: requirement 6, `hashlib`.
- **C6**: judge — scores carried when the key text is unchanged, dropped and listed in
  `judge_stale` when the expected text changes or the case stops being text-graded; no judge is
  called. Oracle: requirement 5.
- **C7**: loading — `load_source` over an in-memory `RunStore` gives the source a JSON report
  would, and refuses an unknown run, a `RUNNING` run and a run without payloads. Oracle:
  requirement 9.
- **C8**: summary — hand-counted transitions and changed lists; `changed` is `None` for each
  held-out split and a list for `dev`, `validation` and `legacy`. Oracle: requirement 10.

## Interface

```python
# src/eval/core/regrade.py
class RegradeRefused(ValueError): ...

def answer_from_payload(produced: Mapping[str, Any]) -> SystemAnswer
def regrade_report(source: Mapping[str, Any], cases: Sequence[EvalCase], *, suite: str,
                   suite_version: str, corpus_fingerprint: str | None,
                   run_id: str | None = None, regraded_at: str | None = None) -> RunReport
def load_source(store: RunStore, run_id: str) -> dict[str, Any]
def regrade_summary(source: Mapping[str, Any], regraded: RunReport) -> dict[str, Any]
```

## Notes

**Oracles.** Expected values come from the stored run itself (re-grading against unchanged keys
must reproduce it), hand counts over synthetic lookup and screen cases, the requirement text, and
`hashlib` for the suite hash. Sources are produced in the tests by the real `EvalRunner` on fake
systems (the system is the LLM boundary) and serialised through `RunReport.to_dict()` or
`checkpoint_payload()`, as `run_suite.py` and the store write them.

**A JSON report is rounded.** `CaseResult.to_dict()` rounds `latency_ms` to 0.1 ms and
`cost_usd` to 1e-6 USD, and a re-grade from a `--out` report carries those rounded values (its
latency percentiles can then differ from the stored run's by that rounding). A store source
(`result_payload`) is exact. The tests use values exact at that resolution.

**Fakes.** Only at I/O boundaries: fake systems, and an in-memory `RunStore` for `load_source`.
The graders, `RunReport`, `Metrics`, `suite_hash` and `check_resume` are real.

**Why the judge is not re-run.** The judge reads the answer's statements and the passages they
cite from `answer.raw`, which is never stored, and it costs LLM calls. Its scores do not enter
accuracy (`Metrics`); they are carried for the text cases whose reference text is unchanged and
dropped, visibly, where it changed. Re-judging is a separate, paid step.

**The key-free pass** (`config["keyfree"]`) needs no keys and is carried unchanged in the copied
config.

**Held-out runs.** Re-grading a held-out run is allowed to the operator (its aggregates are
shared); the summary hides per-case transitions for held-out splits. Agents never run it on one.

**The dev sample.** Its case ids are a subset of the dev suite's, so a sample run re-grades
against the rebuilt sample or the rebuilt full dev suite alike; with the full suite the other
cases are ignored (requirement 3).

**compare_runs.py and summarize_repeats.py** keep their in-memory `regrade`. Moving them onto
`answer_from_payload` and the question check of requirement 3 is a follow-up for the human to
decide (it changes how they treat a case missing from the suite: today a skip, here a refusal).

**Stored runs checked (2026-11-05, SELECT only).** `eval-9a99afc97d4b` (dev, 635 cases) and
`eval-0051272815db` (validation, 265 cases) carry `result_payload` on every row and a
`resume_identity.suite_hash`, so `load_source` serves both. The Anthropic smoke reports are
`--out` JSON reports of split `dev` on suite `xbrl_v2_dev_sample`.
