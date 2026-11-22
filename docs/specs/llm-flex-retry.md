# llm-flex-retry — retry Flex "resource unavailable" 429s for minutes, outside the breaker

The held-out looks run on gpt-6.1 at OpenAI's **Flex** tier
([measurement plan](../eval/heldout_v2/measurement_plan.md) §5 "Flex", §10 item 5). Flex
returns occasional **"resource unavailable" 429s that are not charged**. Every look is offline,
so a 429 is a retry, not a failure. Today the gateway (`LLMGateway._call`) retries a 429 three
times with at most 8 s of backoff and then counts a failure on the circuit breaker, so one bad
half-hour at OpenAI ends a two-day run: every in-flight case errors, and once
`XBRLRAG_RUN_MAX_CONSECUTIVE_FAILURES` failures pile up the breaker halts the run.

This change was split out of `eval-run-checkpoint` (its Notes, "Item 5 is split out"). It
builds on two merged changes and changes neither one's locked tests:

- `openai-6-1-profile`: `XBRLRAG_OPENAI_SERVICE_TIER`, `openai_service_tier()`, the OpenAI
  adapter sending `service_tier`, and the 900 s request timeout of the long-context stages.
- `eval-run-checkpoint`: resume, `error_origin` and `--retry-errors transport`. A Flex storm
  that outlasts this change's retries still ends the case as a **transport** `ERROR`, which a
  resume with `--retry-errors transport` redoes.

Both SDK clients are built with `max_retries=0`, so the gateway is the only retry loop. The
pinned SDKs (openai 2.14.0, anthropic 0.109.1) raise a 429 as `RateLimitError`
(`APIStatusError`, `status_code == 429`); the OpenAI client passes the error object inside the
reply (`{"message", "type", "code", ...}`, the `"error"` member of the JSON body) as the
exception's `body`, the Anthropic client passes the whole JSON body (`{"type": "error",
"error": {...}}`); the response headers are on `exc.response.headers` (`httpx.Headers`). Each
SDK's own retry reads `retry-after-ms` first and then `retry-after`
(`openai/_base_client.py::_parse_retry_after_header`).

## Requirement

1. **Classify a Flex 429.** `src.llm.gateway.adapters.base.is_flex_unavailable(exc, *,
   service_tier=None) -> bool` is True exactly when all of these hold:
   - the error's HTTP status (its `status_code`, else its `http_status`, as
     `classify_transient` reads it) is 429;
   - its error object's `code` and `type` are both other than `insufficient_quota` (a
     billing stop, never a capacity one);
   - either its error object names a resource being unavailable, or `service_tier ==
     "flex"`.

   The error object is `body["error"]` when the exception's `body` is a dict whose `"error"`
   member is a dict (Anthropic's shape), else `body` itself when it is a dict (OpenAI's). It
   *names a resource being unavailable* when any of its `code`, `type` or `message`
   (strings), lower-cased with `_` and `-` read as spaces, contains
   `resource unavailable`. An exception with no status, any other status, or no body is
   judged by these rules as they stand (no body: the tier decides; no 429: False).
2. **Retry-After.** `src.llm.gateway.adapters.base.retry_after_s(exc) -> float | None` reads
   the headers of `exc.response` (none: None), through `headers.get("retry-after-ms")` and
   `headers.get("retry-after")` (`httpx.Headers` is case-insensitive). `retry-after-ms` is
   used when it parses as a
   positive finite number (its value / 1000); else `retry-after` when it parses as a positive
   finite number of seconds; else None. An HTTP-date `retry-after` gives None: it needs a
   clock, and the policy's own backoff applies instead.
3. **The Flex policy is configurable.** `src.llm.request_options.flex_retry_policy(environ=
   None) -> (max_attempts, base_backoff_s, max_backoff_s)` reads
   `XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS` (an integer, default **15**),
   `XBRLRAG_FLEX_RETRY_BASE_S` (default **5.0**) and `XBRLRAG_FLEX_RETRY_MAX_BACKOFF_S`
   (default **300.0**) from *environ* (`os.environ` when None); unset, empty or whitespace
   takes the default, surrounding whitespace is stripped. It raises `LlmConfigError` when a
   value does not parse (`max_attempts` must be an integer literal), `max_attempts < 1`, the
   base is not a positive finite number, or the maximum is not finite or is below the base.
   With the defaults the waits are 5, 10, 20, 40, 80, 160 s and then 300 s, so a call
   gives up after 15 Flex 429s and 2,715 s (about 45 minutes) of waiting: one bad half-hour
   is survived, a dead tier is not waited on for hours.
4. **One wait formula.** `src.llm.gateway.gateway.retry_wait_s(attempt, base_backoff,
   max_backoff, retry_after=None) -> float` is `b = min(max_backoff, base_backoff * 2 **
   (attempt - 1))` when *retry_after* is None, and `min(max_backoff, max(b, retry_after))`
   otherwise. The gateway uses it for every retry wait.
5. **The gateway retries a Flex 429 under the Flex policy.** In `LLMGateway._call` (so both
   `complete` and `complete_tools`), when an attempt raises an exception `exc` with
   `is_flex_unavailable(exc, service_tier=tier)`, where `tier` is `openai_service_tier()` for
   a call whose model's provider is `openai` and None for every other provider:
   - the call keeps two failure counts, Flex and other-transient, each from 0;
   - the Flex count goes up by one; when it is below the policy's `max_attempts`, the gateway
     logs a `retry` event (`error_class="flex_unavailable"`, `attempt` = the call's attempts
     so far, `max_attempts` = the policy's, `wait_ms` = the wait × 1000 rounded to 0.1),
     sleeps `retry_wait_s(flex_count, base_backoff_s, max_backoff_s, retry_after_s(exc))`
     through the gateway's `sleep_fn`, and sends the **same params** again;
   - when the Flex count reaches `max_attempts`, the call fails: the exception is re-raised
     and its `llm_call` event has `outcome="error"` and `error_class="flex_unavailable"`.
   - The policy is `flex_retry_policy()`, read when the call meets its first Flex 429, so a
     run that never meets one never reads it.
   - The gateway's own `max_attempts` / `base_backoff` / `max_backoff` govern the other
     transient errors, counted by the other-transient count only: a Flex 429 never uses up
     their attempts, and they never use up the Flex ones. `LLMCallResult.attempts` and the
     events' `attempts` stay the call's total attempts, of every kind.
6. **The breaker.** A call that fails on an exhausted Flex 429 leaves
   `trace.consecutive_failures` unchanged: it neither advances nor resets the breaker, as a
   refusal does (llm-refusals-and-store). A call that succeeds after Flex 429s resets it to 0
   as any success does; a call that fails on any other error advances it as today. Flex
   retries add nothing to `trace.llm_calls`, the cost or the tokens (they are not charged).
7. **The frontier agent continues at the same step.** Because the retry happens inside the
   gateway call, `frontier_sec_tools` needs no change of its own: a Flex 429 on any turn of
   its loop is retried with that turn's conversation unchanged, does not count as a step,
   and the case goes on from there. A storm that exhausts the policy ends the case as today:
   `SystemAnswer.error` holds the 429, which `error_origin` tags `"transport"`.
8. **Other errors: Retry-After, otherwise unchanged.** For every other transient error the
   wait is `retry_wait_s(n, self.base_backoff, self.max_backoff, retry_after_s(exc))`, `n`
   being the other-transient count. Without a `Retry-After` header that is exactly today's
   wait; with one, the gateway waits at least what the provider asked, never above
   `max_backoff` (8 s by default). Attempts, the breaker and permanent errors (no retry) are
   unchanged. A run without the Flex tier never meets a Flex 429 (the body rule only matches
   what Flex sends), so its retry behaviour is today's except for honouring `Retry-After`.

## Acceptance criteria

- **C1**: `is_flex_unavailable` follows requirement 1, exactly (a bool).

  Point cases, on look-alikes of the SDKs' `RateLimitError` (same class name, `status_code`,
  `body` where each client puts it, `response.headers`): a 429 with code `resource_unavailable` → True at any tier
  and without one; a 429 whose message is "Resource unavailable, please retry" → True; a 429
  `rate_limit_exceeded` → True with `service_tier="flex"`, False with None or `"default"`;
  a 429 `insufficient_quota` → False even at Flex; an Anthropic 429 (`rate_limit_error`, body
  wrapped in `"error"`) → False without the tier; a 500 or a `TimeoutError` → False at Flex.
  Each False case is in a test that also has a True case.

  Property: over synthetic status errors (status 400–599, code/type/message drawn from a set
  including the resource-unavailable spellings and `insufficient_quota`, tier from the four
  tiers and None), the result equals the requirement's rule evaluated in the test.

  Oracle: requirement 1; OpenAI's flex-processing guide ("429 Resource Unavailable", not
  charged), the plan §5; the SDK source for where the body lands.
- **C2**: `retry_after_s` follows requirement 2.

  Point cases: `retry-after: 7` → 7.0; `retry-after-ms: 1500` → 1.5; both → the ms value;
  `retry-after-ms: abc` with `retry-after: 4` → 4.0; `retry-after: 2.5` → 2.5; an HTTP-date,
  `0`, `-3`, `abc` and no header → None; an exception with no `response` → None (each None
  case with a positive case in the same test).

  Property: for any positive integer seconds `n` the result is `n`; for any positive integer
  milliseconds `m` it is `m / 1000`; for any non-numeric text it is None.

  Oracle: requirement 2; openai 2.14.0 `_parse_retry_after_header` (header order).
- **C3**: `flex_retry_policy` follows requirement 3.

  Point cases: `{}` → `(15, 5.0, 300.0)` and `max_attempts` is an `int`; blanks → defaults;
  `{"…MAX_ATTEMPTS": " 20 ", "…BASE_S": "2.5", "…MAX_BACKOFF_S": "600"}` → `(20, 2.5,
  600.0)`; `os.environ` is read when *environ* is None; `LlmConfigError` for attempts `0`,
  `-1`, `1.5`, `x`; base `0`, `-1`, `nan`, `inf`, `x`; maximum `inf`, `nan`; maximum below
  base. The default waits sum to 2,715 s.

  Property: for any valid triple written as text, the result is that triple.

  Oracle: requirement 3 (the plan's "many attempts, backoff into minutes"), hand sums.
- **C4**: `retry_wait_s` follows requirement 4, `abs=1e-9`.

  Point cases: `(1, 0.5, 8)` 0.5, `(2, 0.5, 8)` 1.0, `(5, 0.5, 8)` 8.0; `(1, 5, 300)` 5,
  `(6, 5, 300)` 160, `(7, 5, 300)` 300; with *retry_after*: `(1, 5, 300, 42)` 42,
  `(1, 5, 300, 900)` 300, `(3, 5, 300, 2)` 20, `(1, 0.5, 8, 3)` 3.

  Property: for any attempt 1–60, base, maximum ≥ base and optional retry_after, the closed
  form computed in the test. Metamorphic: the wait never decreases as the attempt grows, and
  never exceeds the maximum.

  Oracle: requirement 4 (today's formula, `gateway.py` line "min(self.max_backoff, ...)").
- **C5**: the gateway's Flex retries (requirement 5), through a real `LLMGateway`, real
  `CostTracker` and trace, and a fake adapter playing scripted exceptions.

  Point cases, model `gpt-6.1-sol`, tier `flex`, the gateway's `max_attempts=3`: five Flex
  429s then a reply → the reply, `attempts == 6`, `retries == 5`, sleeps `[5, 10, 20, 40,
  80]`, six identical params, five `retry` events with `error_class "flex_unavailable"` and
  `wait_ms` 5000 … 80000; with the defaults, 14 Flex 429s then a reply → sleeps `[5, 10, 20,
  40, 80, 160]` + 8 × `[300]`, and 15 → the 15th exception re-raised after 15 attempts and 14
  sleeps summing to 2,715; with the environment policy `(4, 1, 3)`, 4 → re-raised, sleeps
  `[1, 2, 3]`; `[flex, flex, 500, flex, reply]` at `max_attempts=2` → the reply, sleeps `[5,
  10, 0.5, 20]`; Retry-After 42 then 900 → sleeps `[42, 300]`; with no tier, a
  `resource_unavailable` body is still retried 5 times; with the tier set, a Claude call's
  Anthropic 429 and an OpenAI `insufficient_quota` 429 keep the 3-attempt policy (sleeps
  `[0.5, 1.0]`, raised); `complete_tools` is retried the same way.

  Property: for any policy and any `k` below its attempts, `k` Flex 429s then a reply give
  the reply with `attempts == k + 1` and the closed-form sleeps.

  Oracle: requirement 5, the closed form of requirement 4 by hand.
- **C6**: the breaker (requirement 6), in a trace with an active `RunBudget`
  (`max_consecutive_failures=3`) starting from 2 failures.

  Point cases: an exhausted Flex 429 leaves 2, twice running, and the next call is made (no
  `CircuitBreakerOpen`) and resets it to 0; the exhausted call's `llm_call` event has
  `outcome "error"`, `error_class "flex_unavailable"`; five Flex 429s then a reply reset it
  to 0 with `trace.llm_calls` up by exactly 1; an exhausted 500 still advances it to 3 and
  the next call raises `CircuitBreakerOpen` before reaching the adapter.

  Property: for any start below the limit and any number of exhausted Flex calls, the count
  is the start.

  Oracle: requirement 6; the plan §10 item 5 ("a 429 'resource unavailable' is not a
  breaker failure").
- **C7**: the frontier agent (requirement 7), the real `FrontierSecToolsSystem` with a real
  gateway (`max_attempts=3`), a real `SecToolbox` whose HTTP transport refuses every request,
  and a scripted adapter.

  Point cases, tier `flex`: turn 1 calls an unknown tool; turn 2's first five attempts are
  Flex 429s; then the answer → the case is answered (value 120), `agent.steps == 2`, seven
  adapter calls, the six turn-2 attempts carry identical messages, which start with turn 1's
  and add its assistant and tool messages; sleeps `[5, 10, 20, 40, 80]`. With every turn-2
  attempt a Flex 429 and the default policy, the case ends with `error` after 1 + 15 adapter
  calls and `error_origin(answer.error) == "transport"`.

  Metamorphic: for `k` of 3, 4, 6 and 9 Flex 429s at turn 2 (beyond the gateway's 3 attempts), the answer, `steps`, `tool_calls`
  and the final request's messages are the same as with none.

  Oracle: requirement 7; the plan §10 item 5 ("resumes the agent at the same step").
- **C8**: other errors (requirement 8).

  Point cases, no tier: an OpenAI 429 `rate_limit_exceeded` with `retry-after: 3` then a
  reply → sleeps `[3.0]`; with `retry-after: 30` → `[8.0]`; without the header, three of
  them → raised after 3 attempts, sleeps `[0.5, 1.0]`, and the breaker advanced by 1; a 500
  and an `APITimeoutError` without headers → sleeps `[0.5, 1.0]`; a 400 → raised after one
  attempt, no sleep.

  Property: for any sequence of fewer than 3 ordinary transient errors, each with or
  without a Retry-After, then a reply, the sleeps are `retry_wait_s` by hand at the
  other-transient count.

  Oracle: requirement 8; today's waits (`min(8.0, 0.5 * 2 ** (n - 1))`) for the
  header-free case.

## Interface

- `src.llm.gateway.adapters.base.is_flex_unavailable(exc: BaseException, *, service_tier: str | None = None) -> bool`
- `src.llm.gateway.adapters.base.retry_after_s(exc: BaseException) -> float | None`
- `src.llm.request_options.flex_retry_policy(environ: Mapping[str, str] | None = None) -> tuple[int, float, float]`
- `src.llm.gateway.gateway.retry_wait_s(attempt: int, base_backoff: float, max_backoff: float, retry_after: float | None = None) -> float`
- Behaviour change, no signature change: `LLMGateway._call` (so `complete` and
  `complete_tools`). `LLMGateway.__init__`, the adapters, `RunBudget`, `classify_transient`
  and `FrontierSecToolsSystem` keep their signatures.

## Notes

**Which 429s get the long policy, and why not all of them.** The draft (eval-run-checkpoint
Notes) asked for an opt-in policy. The opt-in is the Flex tier itself: a 429 counts as Flex
when its body says the resource is unavailable (only Flex sends that), or when the call went
to OpenAI at `service_tier="flex"`. The second arm is there because the exact wording of the
Flex 429 body could not be checked offline: OpenAI's flex guide names the error "429 Resource
Unavailable", but the pinned SDK carries no constant for it. On a Flex run every OpenAI 429
except `insufficient_quota` is waited out, which suits an offline look (rate-limit 429s are
not charged either). Applying the long policy to *every* 429 was rejected: on a Standard run,
or for the Anthropic judge inside a Flex run, a rate-limit storm should still reach the
breaker within seconds, as today.

**Why a Flex 429 leaves the breaker alone.** The breaker protects the budget from a failing
provider that keeps billing; Flex 429s are not billed. Counting them would halt a run during
an outage the policy is built to outlast. An exhausted call still errors the case as a
transport error, so nothing is silently dropped, and `--resume --retry-errors transport`
redoes it.

**What is not changed.** `pricing.yaml` (Flex is disclosed, not modelled, plan §5), the
request timeout (900 s on the long-context stages), `RunBudget`, `classify_transient` and
the adapters' `is_transient`. The SDK clients stay at `max_retries=0`.

**Oracles.** Requirement text, closed forms computed in the tests, hand sums, and the pinned
SDK source (exception classes, where the body lands, header order). No value comes from
running this change's code.

**Fakes.** Only at the provider boundary: a fake adapter behind a real `LLMGateway` raising
look-alikes of the SDKs' exception classes (the spec lint keeps network packages such as
`openai`, `anthropic` and `httpx` out of spec tests, so their shape is copied from the pinned
source: class name, `status_code`, `body`, `code`/`type`, a case-insensitive
`response.headers`), and for C7 a fake HTTP
transport under a real `SecToolbox` (it refuses every request; the scripted tool call is to an
unknown tool, which needs no request). Sleeps go to the gateway's `sleep_fn`; no test sleeps.

**Wrong variants the tests must catch.** Counting Flex attempts against the gateway's
`max_attempts`; one shared counter (a Flex storm using up a later timeout's retries); the
Flex wait indexed by the total attempts instead of the Flex count; ignoring or uncapping
Retry-After; reading `retry-after` before `retry-after-ms`; advancing or resetting the
breaker on an exhausted Flex 429; the long policy for an Anthropic 429 or `insufficient_quota`
at Flex; restarting the frontier case or counting a retried turn as a step.

**Open questions for the operator.**

1. The Flex 429's exact body (code/message) is unverified. The 10-case frontier smoke (plan
   §5 go/no-go) should log one; if it names something other than "resource unavailable", the
   tier arm of requirement 1 still covers it, and requirement 1 can be widened by re-lock.
2. `insufficient_quota` stays an ordinary transient error (3 quick attempts), as today; making
   it permanent is a separate change.
3. The defaults (15 attempts, 5 s, 300 s; about 45 minutes) are a judgement. A longer outage
   errors the in-flight cases as transport errors, which a resume redoes; raise
   `XBRLRAG_FLEX_RETRY_MAX_ATTEMPTS` for a run that should wait longer.
