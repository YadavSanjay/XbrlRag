# eval-run-checkpoint — an eval run survives a restart

Source: [measurement plan](../eval/heldout_v2/measurement_plan.md) §10 and decision 12. Today
`scripts/eval/run_suite.py` persists a run once, at the end, in one transaction. A power cut, a
restart or a `RunHalt` loses every graded result, and with it the arm's LLM spend ($15–35 for a
long-context or frontier arm on Flex). `EvalRunner.run` accepts a `prior` report and an
`on_result` hook, but `run_suite.py` wires neither, `run_many` refuses both for more than one
system, and `on_result` fires only when a whole cache group has finished.

Route: locked spec. A wrong resume double-counts or skips cases, or mixes two code versions into
one score, and nothing downstream notices: the run still prints one accuracy.

This change covers items 1–4 and 6 of §10. **Item 5 (Flex retries) is split into its own spec,
`llm-flex-retry`**; see Notes.

## Requirement

The new code is `src/eval/core/checkpoint/` (`base.py`: the `RunStore` interface; `run.py`: the
logic), `CaseResult.from_dict`, the `RunStore` methods on `PostgresEvalStore`, one column in
`scripts/ddl/eval/eval_runs.sql`, and the `run_suite.py` flags.

### Checkpointing (item 1)

1. **The case payload.** `CaseResult` gains two fields, `judge_error: str | None = None` (the
   judge's `judge_error` text for this case) and `judge_cost_usd: float = 0.0` (what judging
   this case cost). The graded runner sets both on each case it judges, in the case's own task,
   from the same values it records today. `to_dict()` is unchanged.
   `checkpoint_payload(result)` is `result.to_dict()` with these keys set from the result
   itself, so nothing is rounded or dropped: `latency_ms` and `cost_usd` (the exact floats),
   `served_models` (a copy), `judge_scores` (`result.grade.judge_scores`), `incomplete`
   (`result.grade.incomplete`), `judge_error` and `judge_cost_usd` (exact). It is
   JSON-serialisable.
2. **Restoring a case.** `CaseResult.from_dict(data, case)` rebuilds a result from a payload and
   the suite's `EvalCase` for it. For every `r`,
   `CaseResult.from_dict(json.loads(json.dumps(checkpoint_payload(r))), r.case)` equals `r`:
   the same `case`, a `grade` equal field by field (including `incomplete`, `judge_scores` and
   `checks`), and the same `query_id`, `latency_ms`, `cost_usd`, `served_models`,
   `judge_error` and `judge_cost_usd`. Its `to_dict()` equals `r.to_dict()`. `ValueError` when
   `data["case_id"] != case.id`.
3. **The run row at start.** `run_checkpointed` calls `store.start_run(report)` once per system
   before any system is called, with `report.status == "RUNNING"`, the run's `run_id`, and a
   config holding `resume_identity`, `schedule_run_ids` (system name → run id for every system
   in the schedule) and `segments` (requirement 9). If `start_run` raises, that exception
   propagates before any system is called: a run that was asked to be stored is never run
   unstored.
4. **One case, one write.** Each graded result is passed to `store.write_case(run_id, result)`
   as soon as it is graded, once, before the next case of the same task is answered (a cache
   group no longer holds its results until the group ends). With `workers=1`, when a system is
   called for its k-th case of the run, exactly k−1 case rows are stored. A failed `write_case`
   (it returns `False`) is counted in `config["checkpoint_write_failures"]` and the run goes on:
   a case with no row is re-run on resume, so a missing row never double-counts.
5. **The row at the end.** `store.finish_run(report)` is called once per system with the final
   report: `status` `OK`, or `ABORTED` after a budget stop or a halt (requirement 11).
6. **Postgres.** `PostgresEvalStore` is a `RunStore`. `start_run` writes the `eval_run` row with
   status `RUNNING` in one transaction. `write_case` writes one `eval_case_result` row in its own
   transaction (one connection, one commit, one INSERT), with the existing columns plus
   `result_payload`, a JSONB column holding `checkpoint_payload(result)`. `finish_run` is today's
   `persist_run`, with `result_payload` on every row. `load_run_row(run_id)` returns the row as
   a dict (at least `run_id`, `system`, `status`, `started_at`, `config`) or `None`;
   `load_case_payloads(run_id)` returns the stored `result_payload`s. `eval_runs.sql` adds
   `ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS result_payload JSONB;`.

### Resume (item 2)

7. **Identity.** `resume_identity(cases, *, system, model, llm_profile, git_sha, settings)` is
   `{"suite_hash": suite_hash(cases), "system": system, "model": model, "llm_profile":
   llm_profile, "git_sha": git_sha, "settings": dict(settings)}`. `settings` holds the
   answer-affecting options the caller chose (for `run_suite.py`: `cache_groups`, the judge's
   model and version, `sec_as_of`, the `--sec-universes` file's content hash,
   `max_input_tokens`, the `--with-system` names). It does not hold `max_cost_usd` or
   `max_workers`: raising the cap to continue a budget-stopped run is the main use of resume.
   `suite_hash(cases)` is `"sha256:"` plus the SHA-256 hex digest of the canonical JSON
   (`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False,
   allow_nan=False)`, UTF-8) of `[c.to_dict() for c in cases]` taken in case-id order, so the
   order of the selection does not matter and any change to a case's content does.
8. **Refusal.** `check_resume(stored_config, identity)` raises `ResumeRefused` (a `ValueError`)
   when `stored_config` has no `resume_identity`, when any of the six fields differs, or when
   `suite_hash` or `git_sha` is `None` on either side (a resume cannot then prove it is the same
   code and cases). The message names every differing field. `run_checkpointed(...,
   resume=RUN_ID)` raises `ResumeRefused` with no system called and nothing written when the run
   is unknown, when any system's identity is refused, or when the systems passed are not exactly
   the stored `schedule_run_ids`.
9. **Same run, new segment.** A resumed run keeps its `run_id` (each sibling's too) and its
   `started_at`. Its `config["segments"]` is the stored list plus one entry for this invocation,
   `{"started_at", "n_restored", "retried", "max_cost_usd", "max_workers"}`; the first
   invocation writes the first entry, with `n_restored` 0 and `retried` `[]`. `retried` is the
   sorted case ids re-run under requirement 14.
10. **What is restored.** Every stored payload becomes a restored result (`CaseResult.from_dict`
    with the suite's case), and those cases are not answered again by that system. A case with no
    row (it was in flight, or its write failed) is answered. The final report holds each case at
    most once, so `n_planned`, `n_cases` and every metric are those of one uninterrupted run with
    the same answers. `config["served_models"]` is the union over restored and new results.
    With a judge, `config["judge_errors"]` is `{case_id: judge_error}` over every result with a
    `judge_error`, restored ones included (sorted by case id), and `config["judge_cost_usd"]` is
    the sum of every result's `judge_cost_usd`; both, and `judge_summary`, equal those of one
    uninterrupted run with the same judge replies. `--max-cost-usd` caps the whole run: the
    budget starts charged with the restored results' `cost_usd` plus `judge_cost_usd`.

### A halt keeps the partial run (item 3)

11. A `RunHalt` (budget or circuit breaker) raised by a system's `answer` stops the run; it is
    not graded as an `ERROR` case. `run_checkpointed` returns the reports, each with status
    `ABORTED` and `config["halt"] = {"type": <exception class name>, "detail": str(halt)}`,
    holding every result graded before the halt, and finalises each through `finish_run`. The
    case that raised has no row. Any other exception propagates, as a crash would, and the run's
    row is not finalised as `OK`.

### Siblings (item 4)

12. With several systems (`--with-system`), `resume` names the first system's run id, and the
    siblings' run ids come from its stored `schedule_run_ids`. Each system answers exactly its
    own missing cases: a case one sibling finished and the other did not is answered only by the
    other. Every sibling's run is restored, checked and finalised as in requirements 8–11.

### Error origin (item 6)

13. **Tag.** A failed case stores `error_text(exc)`: `repr(exc)`, or, when that drops the
    message (`exc.args` is empty but `str(exc)` is not, as with Qdrant's `UnexpectedResponse`,
    whose repr is `UnexpectedResponse()`), `type(exc).__name__ + "(" + repr(str(exc)) + ")"`.
    The runner's catch-all and the arms' own catch-alls (`_stage_llm`, `xbrlrag_full`,
    `CallableSystem` and any arm that turns an exception into `SystemAnswer(error=...)`) store
    `error_text(exc)` in place of `repr(exc)`.
    `error_origin(error)` classifies such a text: `None` for `None` or an empty string.
    Otherwise it is `"transport"` when the text reads `Name(<message>)` and one of these holds,
    and `"arm"` in every other case:
    - (a) **a status.** The message (after its opening quote) starts with one of
      `Error code: NNN` (the provider SDKs), `Server error 'NNN` or `Client error 'NNN` (httpx's
      `HTTPStatusError`, RagInference's client), `NNN Server Error` or `NNN Client Error`
      (requests' `HTTPError`) or `Unexpected Response: NNN` (Qdrant's `UnexpectedResponse`), and
      NNN is in `TRANSIENT_STATUS` or ≥ 500. A status, when present, decides, as in
      `classify_transient`: any other status is `"arm"`.
    - (b) **a transport class**, with no status: `Name` is in `TRANSIENT_EXC_NAMES` or is
      `TimeoutError`, `ConnectionError`, `ConnectionResetError`, `ConnectionAbortedError`,
      `ConnectionRefusedError`, `BrokenPipeError`, `CircuitBreakerOpen`, `RunBudgetExceeded`;
      httpx's `ConnectError`, `ReadError`, `WriteError`, `RemoteProtocolError`, `PoolTimeout`,
      `WriteTimeout` (RagInference and Qdrant unreachable); or Qdrant's
      `ResponseHandlingException` (its wrapper for a failed connection).
    - (c) **a lost Postgres connection**: `Name` is `OperationalError` or `InterfaceError` and
      the message contains, ignoring case, one of `server closed the connection unexpectedly`,
      `connection already closed`, `could not connect to server`, `connection to server at`,
      `terminating connection`, `ssl connection has been closed unexpectedly`,
      `connection refused` or `connection timed out`.

    So 5xx and connection errors from our own services (RagInference at `RAGINFER_URL`, Qdrant,
    Postgres) count as transport, like a provider's 429 or 5xx. A doubtful text (no status, no
    listed class, any other database error) is the arm's own, since retrying an arm's error
    would be choosing which answers count.
14. **Retry.** `run_checkpointed(..., resume=..., retry_errors="transport")` drops every restored
    `ERROR` result whose `error_origin(grade.error)` is `"transport"` and answers that case
    again; the new result replaces the stored row. Arm errors are kept. Without `retry_errors`
    no restored result is re-run. `retry_errors` takes only `None` or `"transport"`.
15. **Reported.** The final report's `config["error_origins"]` is `{"transport": n, "arm": m}`
    over its `ERROR` results.

### Refused combinations (fail before any call)

16. `run_checkpointed` raises `ValueError`, with no system called and nothing written, when:
    `identities` has a different length from `systems`; `retry_errors` is not `None` or
    `"transport"`; `retry_errors` is given without `resume`; `run_ids` is given with `resume`; or
    `resume` is given with `metamorphic=True` (the key-free pass needs every case's answer in
    memory, and a restored case has none).
17. `run_suite.py` adds `--resume RUN_ID` and `--retry-errors {transport}`, and exits with
    status 2 and a message naming the flags when: `--retry-errors` is given without `--resume`;
    `--resume` is combined with `--no-store`, `--rerun-failed`, `--keyfree` or
    `--perturb-twins`. Its runs go through `run_checkpointed` with `PostgresEvalStore` unless
    `--no-store`. On either path a run stopped by a `RunHalt` is printed, written to `--out`
    and exits 0 with status `ABORTED`, as a budget stop does today (requirement 11 turns a
    system's halt from an `ERROR` grade into a stop, and locked tests already run `closed_book`
    with `XBRLRAG_RUN_MAX_LLM_CALLS=0` and expect exit 0 and a report).

## Acceptance criteria

- **C1**: payload round trip — requirement 2 holds exactly for results graded by the real
  graders (lookup, wrong, refused, error, judged, judge error, incomplete, with
  `served_models`), the payload carries the judge's error and cost exactly and the defaults for
  an unjudged case, and `Metrics.compute` over restored results equals it over the originals.
  `ValueError` on a case-id mismatch. Oracle: identity with the original object.
- **C2**: checkpointing — before the first answer the store holds one `RUNNING` row per system
  and no case rows; with `workers=1` the k-th answer sees k−1 rows, also inside a cache group;
  the run finishes `OK` with one `finish_run`; a failed write is counted. Oracle: requirements
  3–5, counted by hand.
- **C3**: Postgres — one transaction with one `RUNNING` run INSERT for `start_run`, one
  transaction with one case INSERT whose `result_payload` decodes to the payload for each
  `write_case`; `PostgresEvalStore` is a `RunStore`; the DDL adds the column. Oracle:
  requirement 6.
- **C4**: suite hash and identity — the hash equals SHA-256 computed in the test, is order-
  insensitive and changes with any case field; each of the six identity fields and a `None`
  sha or hash refuses, naming the field. Oracle: requirements 7–8.
- **C5**: interrupted then resumed equals uninterrupted — for any crash point and any set of
  wrong answers, the resumed report has the same run id, each case once, the same per-case
  grades and metrics as one uninterrupted run, and the second segment answers exactly the cases
  with no row; `segments` has two entries; refusals call nothing; the budget counts restored
  cost; `served_models` is the union. Oracle: the uninterrupted run as the reference;
  requirements 8–10.
- **C6**: halt — a `RunHalt` from a system ends the run `ABORTED` with `config["halt"]`, the
  earlier results stored and finalised, the halting case with no row and no `ERROR` grade; a
  crash propagates. Oracle: requirement 11.
- **C7**: siblings — two systems in one filing-grouped schedule, interrupted with different
  progress, resume from the first's run id; each answers exactly its missing cases and both
  finish complete under their own run ids. Oracle: requirement 12.
- **C8**: error origin — the table of texts in requirement 13; our services' real client
  exceptions (RagInference via httpx and requests, Qdrant's `UnexpectedResponse` and
  `ResponseHandlingException`, psycopg2's connection loss) through `error_text`, with doubtful
  ones staying the arm's; `error_text` keeps a message `repr` drops; a Qdrant 503 raised
  through the runner is stored classifiable; agreement with `classify_transient` for any
  synthetic exception, and with its status rule for any service status. Oracle: requirement 13
  and the gateway's own rule.
- **C9**: retry — `retry_errors="transport"` re-runs exactly the transport-tagged errors and
  keeps arm errors; without it nothing is re-run; `error_origins` counts. Oracle: requirements
  14–15.
- **C10**: refused combinations in `run_checkpointed` and in `run_suite.py`'s parser. Oracle:
  requirements 16–17.
- **C11**: the judge across a resume — with the real claim judge on a fake provider, a judged
  run interrupted and resumed has `judge_errors` (restored messages included), `judge_cost_usd`
  and `judge_summary` equal to an uninterrupted run's, each case's `judge_cost_usd` is one
  call at the `pricing.yaml` rate, the resumed segment judges only its own cases, and the
  budget counts the restored judge cost. Oracle: hand counts with `pricing.yaml`, and the
  uninterrupted run as the reference; requirements 1 and 10.

## Interface

```python
# src/eval/core/checkpoint/base.py
class RunStore(ABC):
    def start_run(self, report: RunReport) -> None: ...          # raises when it cannot write
    def write_case(self, run_id: str, result: CaseResult) -> bool: ...
    def finish_run(self, report: RunReport) -> bool: ...
    def load_run_row(self, run_id: str) -> dict[str, Any] | None: ...
    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]: ...

# src/eval/core/checkpoint/run.py   (the tags are the literals "transport" and "arm")
class ResumeRefused(ValueError): ...
def suite_hash(cases: Sequence[EvalCase]) -> str
def checkpoint_payload(result: CaseResult) -> dict[str, Any]
def error_origin(error: str | None) -> str | None
def error_text(exc: BaseException) -> str
def resume_identity(cases: Sequence[EvalCase], *, system: str, model: str | None,
                    llm_profile: str | None, git_sha: str | None,
                    settings: Mapping[str, Any]) -> dict[str, Any]
def check_resume(stored_config: Mapping[str, Any], identity: Mapping[str, Any]) -> None
def run_checkpointed(cases: Sequence[EvalCase], systems: Sequence[SystemUnderTest], *,
                     store: RunStore, identities: Sequence[Mapping[str, Any]],
                     resume: str | None = None, retry_errors: str | None = None,
                     run_ids: Sequence[str] | None = None,
                     config: RunnerConfig | None = None, judge: ClaimJudge | None = None,
                     generator_profile: str | None = None, metamorphic: bool = False,
                     suite: str = "", suite_version: str = "",
                     corpus_fingerprint: str | None = None) -> list[RunReport]

# src/eval/core/report.py
class CaseResult:
    judge_error: str | None = None       # new fields (requirement 1); the implementer adds
    judge_cost_usd: float = 0.0          # them, as a stub may not change an existing class
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], case: EvalCase) -> CaseResult

# src/eval/core/store.py
class PostgresEvalStore(RunStore):   # start_run, write_case, finish_run,
    ...                              # load_run_row, load_case_payloads
```

`store.py` imports the report module as `eval_report` for `write_case`'s annotation
(`result: eval_report.CaseResult`), since a stub may not re-import names the module already
imports.

`run_checkpointed` takes `git_sha` for the reports from `identities[i]["git_sha"]`.

## Notes

**Item 5 is split out.** Flex retries touch a different subsystem (the gateway's retry and
breaker policy, the adapters' 429 classification, and the frontier agent loop) and carry no
eval-store logic, so they go in their own spec, `llm-flex-retry`, written next. Its draft
requirements: an opt-in retry policy for Flex (many attempts, backoff growing into minutes,
capped total wait); a 429 whose body says the resource is unavailable is retried and neither
advances nor resets the circuit breaker; and since the retry happens inside the gateway call,
the frontier agent continues at the same step with its messages intact. Until it lands, a Flex
storm that exhausts the retries is a transport `ERROR` (requirement 13), which
`--retry-errors transport` redoes. Item 6 needs no part of item 5.

**Why the runner changes.** Two current behaviours defeat a checkpoint: `on_result` fires only
when a whole task (a filing's cache group) finishes, so a crash mid-group loses its finished
cases; and `_run_one` turns a `RunHalt` raised by a system into an `ERROR` grade, so a breaker
storm would write hundreds of `ERROR` rows that a resume then treats as done. Requirements 4 and
11 fix both. The judge's `RunHalt` already propagates (judge-runner-wiring), and `run_many` still
raises it; the partial reports are `run_checkpointed`'s.

**Oracles.** Expected values come from requirement text, hand counts over synthetic cases, the
original object (round trip), an uninterrupted run of the same deterministic fake system
(resume), `hashlib` (suite hash) and the gateway's `classify_transient` (error origin). No value
comes from running this change's code.

**Fakes.** Only at I/O boundaries: an in-memory `RunStore` (the store is the database boundary),
fake systems (the arms are the LLM boundary), a fake `psycopg2` module for C3, and for C11 a
fake provider adapter behind a real gateway and claim judge, with the audit sink off. The
runner, graders, judge, `RunReport`, `Metrics`, the checkpoint logic and `PostgresEvalStore` are
real. A crash
is simulated by a system raising a `BaseException` subclass, which the runner does not catch.

**Proved before locking.** A throwaway trial implementation (not committed) passed all 109
tests, and 26 wrong variants of it each failed some: the budget ignoring restored cost or
restored judge cost, a system's `RunHalt` graded `ERROR`, the retry also redoing arm errors,
`error_origin` ignoring the status or reading only the provider status form, `incomplete`,
`served_models`, `judge_error` or `judge_cost_usd` dropped on restore, the judge config taken
from the last segment only, siblings sharing one done set, pending cases computed with "all"
for "any", `None` identities accepted, segments not carried over, write failures not counted,
no `result_payload` on a write, `--keyfree` allowed with `--resume`, the halt not kept,
`started_at` reset, `error_text` as plain `repr`, the runner storing `repr`, Postgres
connection loss as the arm's, every Postgres error as transport, and Qdrant's wrapper or httpx's
`ConnectError` left out of the transport classes. The `run_suite.py` tests pass a suite path
that does not exist, so a parser that wrongly lets a combination through fails on loading the
suite, before any system is built or any call is made. Every test run had the provider keys
blanked and `XBRLRAG_RUN_MAX_LLM_CALLS=0`.

**Not locked here, by decision.** `load_run_row` and `load_case_payloads` against a live
Postgres stay out of the locked tests (spec tests have no database). The implementer adds a test
under `test/integration/` that writes a run through `start_run`, `write_case` and `finish_run`
and reads it back through both methods, and runs the live DeepSeek resume test of §6 (a few
cases killed and resumed), which also covers the `run_suite.py` wiring beyond its parser checks.

**Why `--keyfree` and `--perturb-twins` stay refused with `--resume`.** Neither is worth
resuming: the key-free pass makes no calls and takes seconds, and the perturbation pass (about
2N answers after the run, minutes to about an hour) is optional and already keeps its result
when a halt stops it, so either is rerun cheaply rather than restored.

**The judge across a resume.** The judge's error text and cost live on each case
(`CaseResult.judge_error`, `judge_cost_usd`) and in its payload, so a resumed run's
`judge_errors`, `judge_cost_usd` and budget are exact, not only its `judge_summary`.

**Our services' errors.** RagInference's embedder and reranker clients use httpx
(`raise_for_status`, so `HTTPStatusError`); requests' `HTTPError` is covered too. Qdrant's REST
client raises `UnexpectedResponse`, whose `repr` is `UnexpectedResponse()` with no status, hence
`error_text`. Spec tests may not import the HTTP clients, so C8 uses stand-in classes with the
clients' names and message formats, as these reprs read when the real classes were built in
the py312 env on 2026-11-04 (offline, nothing sent):

```text
HTTPStatusError("Server error '503 Service Unavailable' for url 'http://127.0.0.1:8799/rerank'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503")
HTTPStatusError("Client error '422 Unprocessable Entity' for url 'http://127.0.0.1:8799/rerank'\n...")
ConnectError('[WinError 10061] No connection could be made')
HTTPError('503 Server Error: Service Unavailable for url: http://127.0.0.1:8799/embed')
UnexpectedResponse()          # str(): "Unexpected Response: 503 (Service Unavailable)\nRaw response content:\nb'busy'"
ResponseHandlingException(ConnectError('All connection attempts failed'))
OperationalError('server closed the connection unexpectedly\n\tThis probably means the server terminated')
InterfaceError('connection already closed')
```

**Interaction with existing tests.** `run_many`'s existing parameters keep their meaning, and
the judge's `RunHalt` still propagates out of `run_graded` (judge-runner-wiring C5). With the
trial implementation above (which did not wire `run_suite.py` beyond its parser), three
existing tests failed, all for one reason: they run `closed_book` offline with
`XBRLRAG_RUN_MAX_LLM_CALLS=0` and expect exit 0 and a written report, which today holds only
because the arm's `RunHalt` is graded `ERROR`. They are
`test/spec/llm-provider-profiles/test_c9_compat.py::test_run_suite_records_the_llm_profile`,
`::test_run_suite_records_whichever_profile_is_given` (locked) and
`test/unit/test_baseline_arms_wiring.py::test_run_suite_runs_the_selected_system[closed_book]`.
The last sentence of requirement 17 is what keeps them green (in the trial, keeping the partial
report on halt in `run_suite.py`'s call made all three pass); the implementer must not change
them. With that in the trial, on main `48c2357`: `test/unit` 2680 passed (27 skipped), and
every spec test passed except the 45 of `openai-6-1-profile`, an active lock not yet
implemented, which fail the same way without the trial. That change also edits `run_suite.py`
(a service-tier flag); whichever lands second merges with the other, and a recorded service
tier belongs in the resume `settings` if it can differ between segments.
