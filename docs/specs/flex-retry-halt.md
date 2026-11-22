# flex-retry-halt — a Flex wait stops for a run halt, and a trace's Flex waiting is capped

`llm-flex-retry` (merged, retired) lets `LLMGateway._call` wait out a Flex "resource
unavailable" 429 for up to 15 attempts and 2,715 s (about 45 minutes) per call, outside the
circuit breaker. A deep review of that change proved two defects that follow from its own
"outside the breaker" rule:

1. **A halted run lingers.** `_enforce_run_budget` runs once, before the retry loop. During
   the Flex waits the gateway never looks at the run budget, the circuit breaker or the eval
   runner's stop signal (`EvalRunner.execute`'s `stop` event, set when another task raised a
   `RunHalt` or crashed). A run that has been halted keeps a worker asleep for up to ~45
   minutes per in-flight call.
2. **Exhausted storms multiply.** An exhausted Flex call neither advances the breaker nor
   counts anywhere. Stages that catch an LLM error and degrade (the query decomposer falls
   back to a passthrough, `src/retriever/xbrl/query_decomposer.py`; the faithfulness
   verifier marks each batch `LLM_ERROR`, `src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py`)
   each start a fresh 45-minute wait, so one query can wait for hours with no bound.

Maintainer decision (2026-11-04): (a) re-check the budget, the breaker and the stop signal
around every Flex wait and halt promptly with a `RunHalt` subclass; (b) cap the cumulative
Flex waiting per trace, so a second storm in one trace halts the run.

This change keeps every graduated `llm-flex-retry` test passing; it changes no file under
`test/spec/` other than its own folder.

## Requirement

1. **Two new halts.** `src.llm.run_budget.FlexWaitExceeded` and
   `src.llm.run_budget.RunStopped` are subclasses of `RunHalt` (so every place that lets a
   `RunHalt` through, from the arms to `run_checkpointed`, which ends the run `ABORTED` and
   keeps it resumable, treats them as it treats `CircuitBreakerOpen`). Neither is a subclass
   of the other, of `RunBudgetExceeded` or of `CircuitBreakerOpen`.
2. **The stop signal.** `src.llm.run_budget.RunStopScope(stop)` is a context manager over a
   `threading.Event`. `src.llm.run_budget.run_stop_requested() -> bool` is True exactly when
   a scope is active in the current context (a `contextvars` variable: per thread, and per
   asyncio task) and the event of the innermost active scope is set. Leaving a scope
   restores the scope that was active before it (none: False). `__enter__` returns the
   scope.
3. **The runner installs it.** `EvalRunner._run_one` calls `system.answer(case)` (and the
   `_after_grade` hook) inside `RunStopScope(stop)`, `stop` being the run's own stop event
   in `EvalRunner.execute`. So when one task halts the run, a case that is waiting out a
   Flex 429 in another task notices it (requirement 6) instead of sleeping on.
4. **The trace cap.** `src.llm.request_options.flex_trace_wait_cap_s(environ=None) -> float`
   reads `XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S` from *environ* (`os.environ` when None):
   unset, empty or whitespace gives the default **3600.0**; surrounding whitespace is
   stripped; a value that does not parse as a number, or is not finite, or is not above 0,
   raises `LlmConfigError`.
5. **Checks before every Flex wait.** In `LLMGateway._call`, when a Flex 429 is to be
   retried (its Flex count is below the policy's `max_attempts`, llm-flex-retry
   requirement 5) and its wait `w` has been worked out, the gateway first checks, in this
   order:
   1. the stop signal: `run_stop_requested()` → raise `RunStopped`;
   2. the run budget and breaker, exactly as `_enforce_run_budget` does at the start of a
      call (an active `RunBudget` and an active trace) → raise its `CircuitBreakerOpen` or
      `RunBudgetExceeded`;
   3. the trace cap: with an active trace, when the trace's **Flex wait total** plus `w` is
      above `flex_trace_wait_cap_s()` → raise `FlexWaitExceeded`.

   A halt raised here takes the place of the wait: no `retry` event is logged for that 429,
   nothing is slept and the request is not sent again.
6. **Checks during and after the wait.** When the checks pass, the gateway adds `w` to the
   trace's Flex wait total (with an active trace), logs the `retry` event (its `wait_ms` is
   the whole `w`, as before) and waits:
   - with **no** active stop scope, as one `sleep_fn(w)` call (exactly as llm-flex-retry);
   - with an active stop scope, in consecutive `sleep_fn` slices of
     `min(10.0, what is left of w)` seconds, so the slices sum to `w` and none exceeds 10 s;
     before each slice after the first it checks the stop signal and raises `RunStopped` if
     it is set. A stop is therefore noticed within 10 s of being set.

   After the wait, before sending the request again, it checks the stop signal and the
   budget and breaker again (1 and 2 of requirement 5), raising as there.
7. **A cap halt is sticky.** Once a call in a trace has raised `FlexWaitExceeded`, every
   later `_call` in that trace raises `FlexWaitExceeded` before its first attempt (no
   adapter call), whatever its own errors would have been. A stage that swallows the first
   halt (the decomposer, the verifier) therefore cannot start another wait: the trace's
   Flex waiting never exceeds the cap.
8. **What a halt records.** Each halt of requirements 5–7 logs one `budget_halt` event
   (`reason` = the halt's class name, as `_enforce_run_budget` does) and leaves
   `trace.consecutive_failures`, `trace.llm_calls` and the cost unchanged (the 429s were not
   billed and the provider did not fail). No `llm_call` error event is logged for the halted
   call.
9. **What is unchanged.** Ordinary transient retries (≤ 8 s each) are not sliced and not
   re-checked. An exhausted Flex call (its count reaches `max_attempts`) is raised as
   llm-flex-retry requirement 5 says; its waits count toward the trace total. Without an
   active trace there is no Flex wait total and no cap (and, as today, no budget or
   breaker). Without an active stop scope the stop checks are False. With none of the
   halts triggered, every result, event and sleep is what llm-flex-retry specifies.
10. **A halt stays resumable.** `src.eval.core.checkpoint.run.error_origin` returns
    `"transport"` for an error text of the form `FlexWaitExceeded(...)` or
    `RunStopped(...)`, as it already does for `CircuitBreakerOpen` and `RunBudgetExceeded`
    (by class name; a message that carries an HTTP status is still decided by the status,
    as for every error text), so a case whose arm stored such a halt as its error is redone
    by `--resume --retry-errors transport`.

## Acceptance criteria

- **C1**: the cap setting (requirement 4).

  Point cases: `flex_trace_wait_cap_s({})` → 3600.0, blank / whitespace → 3600.0, `" 90 "`
  → 90.0, `"0.5"` → 0.5, `os.environ` read when *environ* is None; `LlmConfigError` for
  `"0"`, `"-5"`, `"nan"`, `"inf"`, `"-inf"`, `"x"`, `"1e999"`.

  Property: any positive finite number written as text (with surrounding spaces) reads back
  as itself.

  Oracle: requirement 4.
- **C2**: the stop scope (requirement 2).

  Point cases: no scope → False; inside a scope whose event is clear → False; set inside the
  scope → True; after leaving → False; nested scopes: the innermost decides and leaving it
  restores the outer; `__enter__` returns the scope; another thread started inside a scope
  with a set event sees False.

  Property: for any stack of scopes with any clear/set events, the result is the innermost
  event's state, and after unwinding it is False.

  Oracle: requirement 2.
- **C3**: the budget and breaker are re-checked around Flex waits (requirement 5.2, 6
  "after the wait", 8), through a real `LLMGateway`, real trace and `RunBudget`, and a fake
  adapter.

  Point cases (default Flex policy, waits 5, 10, 20, ...): the trace's
  `consecutive_failures` is raised to the breaker limit while the gateway sleeps after the
  first Flex 429 → `CircuitBreakerOpen`, sleeps `[5]`, one adapter call, a `budget_halt`
  event with reason `CircuitBreakerOpen`, the count untouched by the gateway; the trace's
  cost passes `max_cost_usd` while the first attempt is in flight → `RunBudgetExceeded`
  before any sleep (sleeps `[]`, one adapter call, no `retry` event); the `max_llm_calls`
  cap reached during the third wait → `RunBudgetExceeded` after three adapter calls and
  sleeps `[5, 10, 20]`.

  Property: for any Flex attempt `j` (1–10) at which the breaker trips, during the attempt or
  during the wait after it, the gateway raises `CircuitBreakerOpen` after exactly `j`
  adapter calls and `j - 1` or `j` sleeps of the closed-form waits.

  Oracle: requirements 5, 6, 8; the waits are llm-flex-retry requirement 4's closed form.
- **C4**: the stop signal around Flex waits (requirements 5.1, 6).

  Point cases, inside a `RunStopScope`: the event set before the call → the first Flex 429
  raises `RunStopped` with no sleep and one adapter call; waits of 5 and 10 s are single
  slices, and the event set during the second → `RunStopped` after that wait, sleeps
  `[5, 10]`, two adapter calls; with base 25 s a wait is sliced `[10, 10, 5]`, and the event
  set during the second slice → `RunStopped`, sleeps `[10, 10]`; the event never set: two
  25 s waits then a reply → sleeps `[10, 10, 5, 10, 10, 5]`, two `retry` events with
  `wait_ms` 25000, the reply; with no scope the same storm sleeps `[25, 25]`; the halt
  leaves the breaker count unchanged and logs `budget_halt` reason `RunStopped`.

  Property: for any wait (base and attempt) and any slice index at which the event is set,
  the sleeps are the closed-form slices up to and including that one, each ≤ 10 s, and
  the request is not sent again. Metamorphic: with the event never set, the slices of each
  wait sum to the unsliced wait.

  Oracle: requirements 5 and 6, slices by hand.
- **C5**: the trace cap (requirements 5.3, 6, 7, 8, 9).

  Point cases, default policy and cap: in one trace, a first call exhausts its 15 Flex
  attempts (raised as the 429 itself, 14 sleeps summing to 2,715 s); a second call's Flex
  429s wait 5, 10, 20, 40, 80, 160, 300 (total 3,330 s) and its eighth 429 raises
  `FlexWaitExceeded` (3,330 + 300 > 3,600) after 8 adapter calls; a third call raises
  `FlexWaitExceeded` with no adapter call; `budget_halt` events with reason
  `FlexWaitExceeded`; the breaker count unchanged throughout. With the cap 30 and the policy
  (5, 10, 10): three waits of 10 reach exactly 30 and are taken; the next Flex 429 halts with
  no sleep. A new trace starts from 0. Without a trace, three exhausted storms are each
  waited in full (no cap). A Retry-After counts as the wait actually taken.

  Property: for any policy, cap and sequence of calls each meeting some Flex 429s, the
  outcomes and sleeps equal a simulation of requirements 5–7 written in the test, and the
  sleeps never sum above the cap.

  Oracle: requirements 5–7; the hand sums 2,715 = 315 + 8 × 300 and 3,330 = 2,715 + 615.
- **C6**: the runner wiring (requirement 3), the real `EvalRunner` with two workers.

  Point case: case A's system raises `CircuitBreakerOpen` once case B is inside a Flex wait;
  case B's gateway notices the run's stop after its current slice, raises `RunStopped`
  without sending again (one adapter call), and the run raises A's `CircuitBreakerOpen`.

  Metamorphic: a case whose one or three Flex 429s are waited out under the runner (no halt)
  gets the same answer, adapter calls and total wait as the same system answering it
  directly; under the runner the waits are sliced (≤ 10 s), directly they are not.

  Oracle: requirement 3, `EvalRunner.execute`'s documented stop.
- **C7**: the halts stay resumable (requirements 1, 10).

  Point cases: `FlexWaitExceeded` and `RunStopped` are `RunHalt` subclasses, neither a
  subclass of the other, of `CircuitBreakerOpen` or of `RunBudgetExceeded`; `error_origin`
  of the texts their `repr` gives (`FlexWaitExceeded('...')`, `RunStopped('...')`) →
  `"transport"`, as for `CircuitBreakerOpen('...')`.

  Property: for any message text without digits (so without an HTTP status), both forms are
  `"transport"`.

  Oracle: requirements 1 and 10; eval-run-checkpoint's transport rule for run halts.

## Interface

- `src.llm.run_budget.FlexWaitExceeded(RunHalt)` — new exception class.
- `src.llm.run_budget.RunStopped(RunHalt)` — new exception class.
- `src.llm.run_budget.RunStopScope(stop: threading.Event)`, with
  `__enter__(self) -> RunStopScope` and `__exit__(self, *exc: object) -> None`.
- `src.llm.run_budget.run_stop_requested() -> bool`
- `src.llm.request_options.flex_trace_wait_cap_s(environ: Mapping[str, str] | None = None) -> float`
- Behaviour change, no signature change: `LLMGateway._call` (so `complete` and
  `complete_tools`), `EvalRunner._run_one`, `error_origin`. `LLMGateway.__init__`,
  `RunBudget`, `TraceContext`'s existing fields and `EvalRunner.execute` keep their
  signatures; where the trace keeps its Flex wait total and its cap mark is the
  implementer's choice.

## Notes

**The cap rule: seconds per trace, default one hour.** A count of exhausted Flex calls per
trace was the maintainer's example ("a second exhausted Flex call halts the run"), but it
conflicts with the graduated llm-flex-retry C6 tests, which make two to four exhausted Flex
calls in one trace (short policies, a few seconds each) and require the next call to go
ahead. A cap on the **seconds** of Flex waiting per trace bounds what matters (wall time),
leaves those tests alone, and is still met by one full default storm (2,715 s) with room for
Retry-After. With the defaults, a trace that has already sat through one exhausted storm
halts during the next one, 615 s in (`FlexWaitExceeded` at its eighth 429), and every later
call in that trace halts at once (requirement 7). So no query waits more than one hour on
Flex. An operator who raises `XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS` to wait longer must raise
`XBRLRAG_FLEX_RETRY_MAX_TRACE_WAIT_S` with it.

**Why the stop travels in a context variable.** The runner's stop event is local to
`EvalRunner.execute`, and the trace is opened inside each arm (`answer_in_trace`, the XBRL
pipeline), so the runner cannot reach the trace or the gateways. A `contextvars` scope set
around `system.answer` in the worker thread reaches every gateway call that thread makes, is
invisible to the other workers (each sets its own, all over the same event), and is absent
outside the runner (the API, the CLI), where nothing changes.

**Why slices, and only inside a scope.** A budget or breaker change on this trace cannot
happen while this thread sleeps (the trace is per context), so re-checking those before and
after each wait is enough. The stop comes from another thread, so a 300 s wait is sliced
into 10 s naps to notice it within 10 s. Outside a scope nothing can stop the wait, and
the wait stays one `sleep_fn` call, as the graduated llm-flex-retry tests require.

**Why a halt is not an `llm_call` error.** The provider did not fail and nothing was billed;
the run decided to stop. It is logged as `budget_halt`, the event every other halt already
uses, and the breaker is left alone (requirement 8).

**Oracles.** Requirement text, closed forms and hand sums in the tests (llm-flex-retry
requirement 4's waits). No value comes from running this change's code.

**Fakes.** Only at the provider boundary: a fake adapter behind a real `LLMGateway`, raising
look-alikes of openai 2.14.0's `RateLimitError` (copied from the llm-flex-retry spec tests'
conftest), and the gateway's `sleep_fn` recording sleeps (no test sleeps). A sleep or an
adapter call may run a test hook that changes the real trace (as another stage or worker
would) or sets the real stop event. C6 uses the real `EvalRunner` and two threads; its test
systems wait on `threading.Event`s with a bounded timeout to order the two cases.

**Wrong variants the tests must catch.** Checking only at the start of the call; checking
only before the wait (a breaker trip during the wait resends) or only after it; ignoring the
stop; sleeping a long wait in one piece inside a scope; slicing outside a scope (breaks the
graduated tests); slices above 10 s; a cap that counts calls, not seconds; `>=` instead of
`>` at the cap; a cap kept per gateway or per process instead of per trace; forgetting
earlier calls' waits; a non-sticky cap; advancing the breaker on a halt; logging an
`llm_call` error or a `retry` event for the halted 429; the runner not installing the scope.

**Open questions for the operator.**

1. **The XBRL pipeline turns every `RunHalt` into an answer.**
   `XbrlRetrievalPipeline.generate` catches `RunHalt` and returns a "run halted" response
   (`metadata["halted"]`), which `XbrlRagFullSystem.answer` grades as an ordinary
   unanswered case. So for the `xbrlrag_full` arm a halt (this change's, and the existing
   breaker and budget halts) does not stop the eval run and the case is stored as answered,
   not resumable. This predates the change and is left out of it; a follow-up could make
   the eval arm re-raise when the response is marked halted.
2. The default cap (3600 s) and slice (10 s) are judgements. The slice is a constant, not a
   setting: a stop noticed within 10 s is prompt next to a 45-minute storm.
3. The stop is checked only around Flex waits, not before every LLM call: a case not stuck
   in a Flex wait finishes its current answer after the run is stopped, as today.
