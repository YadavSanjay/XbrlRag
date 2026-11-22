# xbrlrag-run-halt — a run halt leaves the XBRL pipeline, so an `xbrlrag_full` eval run stops and resumes

Open question 1 of [`flex-retry-halt`](flex-retry-halt.md) (2026-11-04 handover). A
`RunHalt` (`RunBudgetExceeded`, `CircuitBreakerOpen`, and flex-retry-halt's
`FlexWaitExceeded` and `RunStopped`) is the run deciding to stop, not a case failing. Every
other eval arm lets it through to the runner, which ends the run `ABORTED` and keeps it
resumable ([`eval-run-checkpoint`](eval-run-checkpoint.md) requirement 11). The
`xbrlrag_full` arm does not, because the XBRL pipeline turns the halt into an answer
before the arm sees it.

Route: locked spec. A halted case graded as an answer looks like any other "not answered"
case. The run reports one accuracy and nothing downstream notices that it stopped.

## The defect (read from code on `main` 4ee4429, and confirmed offline)

The halt is caught in four places. In each, the eval run carries on and stores the halted
case as answered.

1. **`XbrlRetrievalPipeline.generate`** (`src/pipelines/xbrl/retrieval_pipeline.py`) wraps
   the whole query in `except RunHalt as halt: return self._halted_response(query, halt)`.
   This covers the single-query path and the compound path, so a halt in sub-query 2 throws
   away sub-query 1's answer and returns a "run halted" answer for the whole query.
   `XbrlRagFullSystem.answer` (`src/eval/systems/xbrlrag_full.py`) already has
   `except RunHalt: raise`, but the halt never reaches it. The halted response
   (`metadata["halted"]`, `fully_answered=False`, no values) is graded as a refusal, and
   `run_checkpointed` writes it as a finished case row, so a resume never redoes it.
2. **`XbrlRetrievalPipeline.generate_from_decision`** does the same.
3. **`QueryDecomposer.decompose`** (`src/retriever/xbrl/query_decomposer.py`) runs its LLM
   call under `except Exception`, which falls back to passing the query through. A
   `RunHalt` is an `Exception`. A sticky halt (budget, breaker, the Flex cap) is raised
   again by the router's call, so today it only reaches site 1. A `RunStopped` is not
   sticky: it is raised only around a Flex wait, so after the decomposer swallows it the
   query runs to a full answer.
4. **`XbrlFaithfulnessVerifier._call_llm`** (`src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py`)
   catches `except Exception` and marks the batch `LLM_ERROR`. The verifier is the query's
   last LLM call, so a halt there is never raised again. The query returns a normal answer
   whose statements are marked "unverified", with no `halted` mark at all.

Confirmed offline with the real pipeline (real stages, real gateways on a fake adapter,
fake fact store). With `RunBudget(max_llm_calls=k)` on a fast-path lookup that makes four
calls (decomposer, router, synthesizer, verifier), `generate` returns a "run halted" answer
for k = 0 and 1. For k = 3 it returns the full answer with
`faithfulness.status == "LLM_ERROR"` and the `RunBudgetExceeded` text in
`faithfulness.error`.

No other `except` in the query path catches a halt. The entity extractor, fact-store
lookup, screen, breakdown, narrative neighbour fetch, query cache and audit sink catch broad
errors, but they make no LLM calls. The intent router, plan generator and synthesizer catch
only JSON and `ValueError` errors, and `RunHalt` is neither. `trace_query` records the
exception and re-raises it.

## Requirement

1. **The pipeline lets every halt through.** `XbrlRetrievalPipeline.generate` and
   `generate_from_decision` raise any `RunHalt` (any subclass) that a stage raises during
   the query. The caller gets the halt itself, re-raised, not wrapped: the same class and
   message. Neither method returns an answer for a halted query. This holds for:
   - every stage that makes an LLM call (decomposer, intent router, plan generator,
     synthesizer, faithfulness verifier);
   - both paths: one query, and a compound query at any of its sub-queries. A halt in a
     later sub-query discards the earlier sub-queries' answers; no partial merge is
     returned.
2. **Stages do not swallow a halt.** `QueryDecomposer.decompose` and the faithfulness
   verifier let a `RunHalt` from their LLM calls propagate. Their fallbacks are unchanged
   for every other exception: the decomposer still passes the query through, and the
   verifier still marks the batch `LLM_ERROR`. More generally (`.claude/rules/rag.md`), no
   `except` in `src/retriever/xbrl/` or `src/pipelines/xbrl/` may turn a `RunHalt` into a
   value.
3. **The trace records the halt.** Before a halt leaves `generate` or
   `generate_from_decision`, the query's trace is annotated `halted=True` and
   `halt_reason=<the halt's class name>` (today's annotation keys). The trace's `error` is
   `repr(halt)`, which `trace_query` already sets for an exception. The trace is persisted
   through the audit sink once, as for any query. The trace is the Trace Explorer's view of
   the halted query.
4. **Refusals are unchanged.** An `LLMRefusalError` is still turned into a declined answer
   inside the pipeline (`metadata["refused"]`), as llm-anthropic-and-caching specifies. A
   refusal is the model's final word on the case, not a run halt.
5. **The eval run stops and stays resumable.** With requirement 1, `XbrlRagFullSystem.answer`'s
   existing `except RunHalt: raise` takes effect, and the run goes as eval-run-checkpoint
   requirement 11 says for any arm. `run_checkpointed` ends the run `ABORTED` with
   `config["halt"] == {"type": <class name>, "detail": str(halt)}`. Every case graded before
   the halt is stored and kept. The halting case gets no row and no `ERROR` grade, so
   `--resume RUN_ID` answers it again. A resumed run answers exactly the cases with no row
   and then finishes `OK`. No change to the arm is required. If an implementation stores a
   halt as the arm's error instead, it is graded wrong; flex-retry-halt requirement 10 only
   classifies such a text as `"transport"`.
6. **Serving keeps a user-facing answer, at the API boundary.** The serving surfaces (the
   BFF `/api/query`, its SSE stream, `/api/v1/answer`, and the MCP tools that wrap the v1
   router) all reach the pipeline through `src.api.pipelines.run_xbrl`. They keep today's
   behaviour: a halted query is answered with a "not answered: run halted" body, not an
   error. The conversion moves out of the pipeline into the new function
   `src.api.pipelines.xbrl_answer(pipeline, query, user_id=None)`, which `run_xbrl` calls in
   place of `pipeline.generate`, and which returns the answer dict:
   - for an answer, `pipeline.generate(query, user_id=user_id).model_dump()`;
   - for a `RunHalt` `h`, the halted answer, a dict with exactly these keys:
     `query` = *query*, `answer_summary` = `""`, `answer_details` = `[]`,
     `unanswered_parts` = `[{"question_fragment": query, "reason": R}]` where
     `R = f"run halted ({type(h).__name__}): {h}"`, `metadata` containing at least
     `fully_answered=False`, `halted=True` and `halt_reason=type(h).__name__`, `plan` = `{}`,
     `execution_result` with `status == "ERROR"` and `error == R`, and
     `narrative_chunks` = `[]` (today's `_halted_response` shape);
   - any other exception propagates unchanged; the routes' own handlers deal with it, as
     today.

   `run_xbrl` keeps its signature and still returns `(answer, query_id)`. The query id is
   that of the halted query's trace, captured by the same audit sink as for an answer.
7. **What is unchanged.** Queries that do not halt get the same answer, the same LLM calls
   and the same trace as before. The query cache still never stores a halted query; a
   raised query never reaches `_cache_put`. The other eval arms, the runner and
   `run_checkpointed` are unchanged.

## Acceptance criteria

- **C1**: a halt at any stage of one query propagates out of the pipeline (requirements 1
  and 2). The tests use the real `XbrlRetrievalPipeline`, real stages and real `LLMGateway`s,
  with a fake provider adapter per stage and a fake entity resolver and fact store; the
  question is a fast-path lookup that makes four LLM calls (decomposer, router,
  synthesizer, verifier).

  Point cases:
  - `RunBudget(max_llm_calls=k)`: `generate` raises `RunBudgetExceeded` for k = 0, 1, 2
    and 3, after exactly k adapter calls in stage order. For k = 3 the verifier's own call
    halts.
  - A `RunStopScope` whose event is set, with a Flex 429 at the decomposer: `RunStopped`,
    and no later stage is called.
  - A Flex trace cap of 1 s with a Flex 429 at the verifier: `FlexWaitExceeded`.
  - `max_consecutive_failures=1` with a permanent error at the decomposer: the decomposer
    still passes the query through, and the router's call raises `CircuitBreakerOpen`.
  - A non-halt error at the verifier still gives an answer with `LLM_ERROR`, while a halt at
    the same call propagates.
  - `generate_from_decision` with `max_llm_calls=0` raises `RunBudgetExceeded`.
  - The exception raised has the gateway's class and `RunBudget.check`'s message.

  Property: for any stage (the four) and any halt (budget, stop, Flex cap), `generate`
  raises that halt's class. Each earlier stage's adapter was called once. A budget halt
  makes no call at its stage, and a stop or cap halt makes one. No later stage is called.

  Oracle: requirements 1 and 2. The call counts are counted by hand from the stage order.
- **C2**: a halt in a compound query's later sub-query propagates (requirement 1). The
  decomposer splits the question into two independent sub-queries, so the query makes
  seven calls: the decomposer, then router, synthesizer and verifier for each sub-query.

  Point cases:
  - `max_llm_calls=4`: the halt is at sub-query 2's router, `RunBudgetExceeded` is raised,
    and no merged answer is returned.
  - A set stop event with a Flex 429 at sub-query 2's synthesizer: `RunStopped`.
  - With no halt, the merged answer holds both sub-answers.

  Property: for any k in 0..6, `RunBudgetExceeded` after exactly k calls. For k ≥ 7 the
  merged answer is the unbudgeted one.

  Oracle: requirement 1, with the calls counted by hand.
- **C3**: the trace records the halt (requirement 3). Point case: a recording audit sink
  receives exactly one trace for a halted query, annotated `halted=True` and
  `halt_reason="RunBudgetExceeded"`, with `error == repr(<the raised halt>)`.

  Property: for any stage and halt as in C1, the one persisted trace carries
  `halt_reason == type(raised).__name__` and `error == repr(raised)`.

  Oracle: requirement 3.
- **C4**: an `xbrlrag_full` eval run stops at a halt and resumes (requirement 5). The tests
  use the real `XbrlRagFullSystem` over the real pipeline, the real runner and
  `run_checkpointed`, and an in-memory `RunStore`, with one worker. Every case is on one
  filing and the run groups by filing (`cache_groups="filing"`), so the cases form one task
  answered in order. With one task per case, a second task can start before the runner has
  seen the first one's halt, so which later cases ran would depend on timing.

  Point cases:
  - Cases `a`, `b`, `c`, with a Flex-cap halt at case `b`'s decomposer or at its verifier:
    the report is `ABORTED`, `config["halt"]["type"] == "FlexWaitExceeded"`, only `a` is
    stored, and `b` has no row and no `ERROR` grade.
  - A resume with a pipeline that does not halt answers exactly `b` and `c` and finishes
    `OK` with all three cases once.

  Property: for any n in 2..5 and halting case index h in 1..n−1, the cases before h are
  stored, and the resume answers exactly the cases from h on.

  Oracle: eval-run-checkpoint requirements 10–11, with the cases counted by hand.
- **C5**: the API boundary keeps a user-facing halted answer (requirements 4 and 6).

  Point cases:
  - With the real pipeline and `max_llm_calls=0`, `xbrl_answer` returns the halted dict of
    requirement 6, with `halt_reason "RunBudgetExceeded"` and the reason text built from
    the raised halt.
  - An unhalted query returns `generate(...).model_dump()`.
  - A refusal at the synthesizer returns the pipeline's declined answer (`refused`).
  - A pipeline raising `RuntimeError` propagates it.
  - `user_id` reaches `generate`.

  Property: for each of the four `RunHalt` subclasses and any message text, a pipeline
  raising it gives the halted dict with that class name and that message.

  Oracle: requirement 6, which states today's `_halted_response` shape.

## Interface

- `src.api.pipelines.xbrl_answer(pipeline: Any, query: str, user_id: str | None = None) -> dict[str, Any]`
  is a new function (requirement 6).
- Behaviour changes with no signature change: `XbrlRetrievalPipeline.generate`,
  `XbrlRetrievalPipeline.generate_from_decision`, `QueryDecomposer.decompose`,
  `XbrlFaithfulnessVerifier` (its `_call_llm`), and `src.api.pipelines.run_xbrl`, which
  calls `xbrl_answer`. Where the halted-answer builder lives is the implementer's choice.
  It may move out of the pipeline into `src/api/`, as long as the pipeline no longer
  returns it.

## Notes

**Why the serving path keeps an answer.** An API or MCP caller asked a question; a run
budget or breaker is an operator's guard on spend. The caller is better served by a "not
answered: run halted (CircuitBreakerOpen)" body than by a 500, and that is what they get
today. Changing that would change the v1 and MCP contracts, which is not this change's
purpose. The pipeline is the wrong place for the conversion because it cannot tell a
serving caller from an eval run. So the conversion moves to the one function every serving
surface already calls. The routes' broad `except Exception` handlers would also catch a
halt, but they turn it into an error body (BFF) or an HTTP 500 (v1). `xbrl_answer` catches
it first, so their behaviour for a halt stays what it is today.

**Why the stages must not swallow a halt.** The flex-retry-halt cap is sticky, so a stage
that swallows the first `FlexWaitExceeded` cannot start another wait. Stickiness bounds
the waiting; it does not make the halt reach the runner. The verifier is the last LLM call
of a query and `RunStopped` is not sticky, so a swallowed halt there is lost.
`.claude/rules/rag.md` already says a `RunHalt` must never be swallowed in a broad
`except`.

**Oracles.** The expected outcomes come from the requirement text, the stage order (counted
by hand), and the shape `_halted_response` builds today, copied into requirement 6. No
expected value comes from running this change's code.

**Fakes.** Only at I/O boundaries:
- a fake provider adapter behind a real `LLMGateway` for each LLM stage, installed as the
  stage's `_llm_client` (the gateway is the stage's only LLM seam);
- a look-alike of openai 2.14.0's Flex `RateLimitError` (copied from the flex-retry-halt
  spec conftest) and a recording `sleep_fn` (no test sleeps);
- a fake entity resolver (`resolve_companies` / `resolve_periods`, the SQL boundary of
  `EntityExtractor`), returning the real `ResolvedCompany` / `ResolvedPeriod` dataclasses;
- a fake fact-store lookup returning a real `FactLookupResult`. The screen, breakdown and
  narrative backends are stand-ins that fail the test if touched;
- a disabled query cache (its epoch reader would read Postgres);
- an in-memory `RunStore` and a recording audit sink.

The pipeline, its stages, the dispatcher, the plan executor, `XbrlRagFullSystem`, the
runner, the graders and `run_checkpointed` are real. C5's property uses a stand-in pipeline
object (an argument, not a patch) to raise arbitrary halts; its point cases use the real
pipeline.

**Wrong variants the tests must catch.** The pipeline still catching at `generate` or at
`generate_from_decision`; the decomposer or verifier still swallowing (caught by
`RunStopped` at the decomposer and any halt at the verifier); a wrapper or another halt
class in place of the original; a partial merge returned when sub-query 2 halts; the trace
not annotated or its `error` not set; `xbrl_answer` letting the halt through, swallowing
other exceptions, or losing `user_id`; the eval arm storing the halted case as answered or
as an `ERROR`.

**Proved before locking.** A throwaway trial implementation (not committed) passed all 26
tests and kept `test/unit` green (2727 passed, 27 skipped). It made these changes:
- re-raise in both pipeline methods, after annotating the trace;
- `except RunHalt: raise` ahead of the broad `except` in the decomposer and in the
  verifier's `_call_llm`;
- `xbrl_answer` catching only `RunHalt` and building today's `_halted_response`.

Seven wrong variants of it each failed some tests:
- verifier still swallows: 9 failed;
- decomposer still swallows: 3;
- trace not annotated: 2;
- `generate_from_decision` still catches: 1;
- `xbrl_answer` catching every `Exception`: 1;
- `xbrl_answer` dropping `user_id`: 2;
- a partial merge when a later sub-query halts: 4.

The tests do not tell a halt re-raised from one built afresh with the same class and
message. Requirement 1 still asks for a re-raise.

**Not locked here, for the implementer.**
- The query-id capture through `run_xbrl` (the BFF's `_CapturingSink`) is unchanged and is
  not exercised. `trace_query` persists the trace in its `finally`, so the halted query's
  id is still captured.
- `XbrlRetrievalPipeline._is_cacheable`'s `halted` check becomes unreachable and may stay.
- Update `src/retriever/xbrl/CLAUDE.md` and `src/api/CLAUDE.md` to say where a halt is
  turned into an answer.

**Open questions for the operator.**
1. Several dev-time callers of `generate` will now see a halt as an exception instead of an
   answer: `src/eval/xbrl/golden.py` (13-case golden set, `except Exception`: the case
   fails and the set goes on), `src/eval/regression/prompt_matrix.py` (`except Exception`:
   the arm is recorded as failed), `scripts/eval/run_golden_eval.py`, `run_drift_check.py`,
   `build_baseline.py` and `src/eval/xbrl/regression_runner.py` (no handler: the script
   stops). For the scripts this is fail-loud. For `golden.py` and `prompt_matrix.py` a halt
   is recorded as a failed case, which is roughly what happens today. Bringing those two to
   "a halt stops the set" is a follow-up, not part of this change.
2. `/api/v1/answer` and the MCP `answer` tool keep a 200 response with the halted body. A
   v1 client that would rather receive a typed 503 is a contract change for a later
   decision.
3. Only the halting case is redone on resume. Other workers' cases still in flight when the
   run stops finish their current answer (flex-retry-halt open question 3), and are stored
   if graded.
