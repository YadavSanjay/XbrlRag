# long-context-cache-reuse — write the Anthropic prompt cache only for a filing asked about twice

Narrows requirement 9 of [llm-anthropic-and-caching](llm-anthropic-and-caching.md) for the
`long_context_oracle` arm (`src/eval/systems/long_context.py`, class `LongContextSystem`) on
Anthropic models, and adds an optional run-plan hook to the eval runner.

## Why

The arm sends each case as three messages: the system message; a user message holding the
whole filing text, marked `cache_point: True`; a user message holding the question. Calls on
one filing are serialized, so later calls read the cache the first one wrote. On the 11-10
Anthropic dev run the arm made 81 calls over about 60 filings: cache writes 7.88M tokens,
cache reads 2.91M. Most filings are asked about once, so their cache write (billed at 1.25x
input on Anthropic) is never read: about 13% of the arm's cost is wasted. Answers do not
depend on caching. This is a cost change only.

## Requirement

1. **Run-plan hook.** A `SystemUnderTest` may define an **optional** method
   `plan_cases(cases: Sequence[EvalCase]) -> None`. It is not a member of the
   `SystemUnderTest` protocol; a system without it is unaffected.
   - When a run schedules **exactly one system**, every path through
     `src/eval/core/runner.py` and `src/eval/core/checkpoint/run.py` that answers cases
     (`EvalRunner.run`, `EvalRunner.run_many` with one system, `run_checkpointed` with one
     system, all of which reach `EvalRunner.execute`) calls that system's `plan_cases`
     **once per invocation**, **before any case of that invocation is answered**.
   - The argument is exactly the cases the system will be asked to answer in this
     invocation: its outstanding set (the cases passed in, which are already narrowed by
     kind, sample or rerun selection, minus the cases a resume restores), **in suite order**
     (the order of the `cases` argument).
   - When a run schedules **two or more systems together** (`run_many` or
     `run_checkpointed` with several systems), `plan_cases` is **not called on any of
     them**, so each behaves as with no plan (requirement 3).
   - A system without `plan_cases` is never called for it.
   - An exception other than `RunHalt` raised by `plan_cases` does not abort the run: it is
     logged and the run proceeds as if that system had no plan. A `RunHalt` propagates, as it
     does from `answer`.
   - Whether `plan_cases` is called when a system has no outstanding case is not specified.
2. **The long-context arm uses the plan, on Anthropic only.** `LongContextSystem.plan_cases`
   computes, for each planned case, its document key exactly as `answer` does
   (`_document_key`: the cited filing when one holds every cited fact, else the canonical
   annual filing for the case's ticker and year). A case with no ticker or fiscal year, or
   whose key is None, is ignored. A key planned for **two or more** cases is *reused*.
   - When the arm's model provider is `anthropic` (the provider of the `StageLLM`'s model
     ref, i.e. the model's `provider` in `configs/llm/llm.yaml`), `answer` marks the filing
     message `cache_point: True` **only** for a planned case whose key is reused, and leaves
     the flag off (absent or false) for a planned case whose key is not reused.
   - For every other provider (`openai`, `deepseek`, ...) the filing message is always
     marked, exactly as today. An OpenAI request without a cache point switches from the
     Responses API to Chat Completions (llm-anthropic-and-caching requirement 7), which must
     not happen before the G1 look.
   - Reuse is counted over keys, not over `(ticker, fiscal year)`: two cases with different
     years whose keys are the same filing (a restated period cited from a later filing)
     reuse it.
3. **No plan means today's behaviour.** The filing message is marked `cache_point: True`
   (every provider) for a case answered when:
   - `plan_cases` was never called;
   - the case was not in the last plan;
   - the last `plan_cases` call failed (raised). An exception from the resolver while
     planning propagates from `plan_cases` (the arm does not swallow it; the runner logs it,
     requirement 1). A failed call leaves no partial plan: the arm behaves as if it had never
     been planned.
   A later `plan_cases` call replaces the earlier plan.
   This keeps the locked or retired tests of llm-anthropic-and-caching C7 and
   long-context-arms C3/C6 green; they are not changed.
4. **Answers unchanged.** With and without the mark, the request's message list differs
   **only** in the filing message's `cache_point` flag: the system message, the filing
   message's content and the question message are byte-identical. The number of provider
   calls per case (one, or none for an excluded case) and the per-filing serialization are
   unchanged, and the parsed answer is the same.

## Acceptance criteria

- **C1**: Runner hook. In a single-system run `plan_cases` is called once, before the first
  answer, with the outstanding cases in suite order: a plain run, a narrowed run (a subset
  passed in), and a resumed run (`EvalRunner.run(prior=...)` and `run_checkpointed(resume=...)`)
  that skips restored cases. When two systems are scheduled together (`run_many`,
  `run_checkpointed`), neither is called, and an Anthropic oracle arm scheduled with another
  system marks every filing message. A system without the method runs normally. A raising
  hook (not a `RunHalt`) leaves every case answered and graded; a `RunHalt` from the hook
  propagates before any case is answered. Oracle: the case lists the test constructs.
- **C2**: Anthropic arm. With a plan over filings of known multiplicities, every call on a
  reused filing carries `cache_point: True` on the filing message and the single call on a
  single-use filing carries none; also end to end through `EvalRunner.run`, and with two
  years resolving to one key. Oracle: hand-counted multiplicities; a property test over
  random multiplicities.
- **C3**: Non-Anthropic providers (`openai`, `deepseek`). Every filing message is marked
  whatever the plan, while an Anthropic arm given the same plan leaves the single-use filing
  unmarked. Oracle: requirement 2, the provider of the committed model config.
- **C4**: No plan, a case outside the plan, a failed plan → marked (Anthropic), while a planned
  single-use case in the same run is not. Oracle: requirement 3.
- **C5**: Byte identity. The same case with and without the mark sends the same system
  message, filing-message content and question message, the same number of calls, and gets
  the same parsed answer. Oracle: metamorphic, the unmarked run is compared to the marked one.

## Interface

- `src.eval.systems.long_context.LongContextSystem.plan_cases(self, cases: Sequence[EvalCase]) -> None`
  (new; stub raises `NotImplementedError("tdd:long-context-cache-reuse")`).
- The runner wiring is inside the existing `EvalRunner` methods; no new public runner
  signature.

## Notes

- This spec **narrows requirement 9 of llm-anthropic-and-caching** for Anthropic models: the
  filing message is a cache point only for a filing the run plans to ask about at least
  twice. That spec file is not edited; its C7 tests call `answer` without a plan, which keeps
  today's behaviour (requirement 3 here).
- **Extending the rule to OpenAI is deferred until after G1**: on OpenAI an unmarked request
  changes API (Responses → Chat Completions), and that is not a cost-only change.
- **Why a joint schedule gets no plan.** `run_suite.py --with-system` schedules
  `long_context_oracle` together with `long_context_resolved` so that both send the same filing
  prefix and share one cache entry per filing: the oracle's write is read by the resolved arm.
  If the oracle left a single-use filing unmarked there, the resolved arm (which marks every
  filing) would write the entry instead and nobody would read it, which costs more than today
  (about 1x + 1.25x input instead of 1.25x + 0.1x). So the hook runs only in a single-system
  run, and a joint schedule keeps today's marks (requirement 3).
- **Planning queries the resolver.** `plan_cases` resolves every planned case's document key
  before any answer, so a run starts with one extra fact-store query (or two, for a case with
  cited facts) per case, bounded by the resolver's connect and statement timeouts. `answer`
  resolves the key again; the plan is not a cache of keys.
- The resolving arms (`long_context_resolved`, `long_context_multi`,
  `src/eval/systems/long_context_arms.py`) choose their filing from the question, so their key
  is not known before the call; they keep marking every filing and are out of scope.
- Fixtures are synthetic: a fake provider adapter behind a real `LLMGateway`, a fake filing
  resolver and an in-memory filing store (the I/O boundaries), as in the
  llm-anthropic-and-caching and long-context-arms spec tests; for `run_checkpointed`, an
  in-memory `RunStore`, as in eval-run-checkpoint.
