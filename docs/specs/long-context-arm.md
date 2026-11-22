# long-context-arm — "paste the 10-K into a frontier model"

Part of external test v1, second of two changes (baseline-arms built the
closed-book and naive-RAG arms). It adds the comparison readers most often propose: give a
frontier model the whole filing and ask the question. The arm is handed the **right
filing** for each case (an oracle document), which favours it. No XbrlRag pipeline
behaviour changes.

The frontier models for this comparison are **gpt-5.6-sol** (the default) and **Claude
Opus 5.5**. The arm takes the model by exact ID, so a run names the model it tested.
Running it on Claude needs the multi-provider gateway work (Claude pricing and request
rules), which is a separate, later spec. Until then the arm refuses a model it can't
price.

## Requirement

1. **The document.** For a case with a `ticker` and an `fy`, the arm reads that filer's
   canonical annual filing for that fiscal year:
   - the 10-K or 20-F that the fact store uses, so the original unless a full restatement
     superseded it;
   - resolved through a `FilingResolver` (an I/O boundary, defined in
     `src/eval/systems/base.py`);
   - read through the existing `FilingStore` façade, so it works in local and AWS mode.

   A year-over-year case uses only its own year's filing, which carries the prior year as
   comparatives.
2. **Text, not HTML.** The filing is reduced to plain text by `filing_text`:
   - the hidden inline-XBRL header (`ix:header`), `script` and `style` are dropped;
   - each block (paragraph, heading, list item, division, table row, line break) starts a
     new line;
   - a table row's cells are joined by `" | "`;
   - whitespace inside a line collapses to single spaces, and empty lines are dropped.
3. **One call, document first.** The arm makes one LLM call with the filing text and the
   question, and asks for the arm-neutral JSON answer object. Its reply is read with
   `parse_structured_answer`. The arm never produces `fact_ids` or `chunk_ids`.
   - The filing text is untrusted: it goes into the user message through `json.dumps`,
     never into the system prompt or a format template.
   - **The filing comes before the question.** The system message doesn't depend on the
     case, and the user message is the filing text followed by the question. So every
     call on the same filing shares one prefix (system message plus filing), which the
     provider's prompt cache serves at a fraction of the input price.
4. **An explicit frontier model.** The call goes through `LLMGateway` with stage
   `long_context`, on the model the arm was built with: `model`, default
   `"gpt-5.6-sol"`.
   - It never resolves the model from a tier, so a change to the tier mappings can't
     silently change what a run tested.
   - A model with no entry in `MODEL_PRICING` is rejected when the arm is built
     (`ValueError`), because the run's cost cap can't work without prices.
   - The prompt is a versioned file (`v1`) resolved by the XBRL prompt registry for the
     model's provider and series.
5. **One call per filing at a time.** Calls for the same filing are serialized. While one
   call on a filing is in flight, another answer on that filing waits for it, so the
   later questions read the cache the first one wrote instead of all paying full price.
   Calls on different filings run concurrently.
6. **Exclusions, not truncation.** The arm makes no LLM call and returns
   `SystemAnswer(error=...)` whose message starts with `"excluded: "` when any of these
   holds:
   - the case has no `ticker` or no `fy` (a cross-filer screen has no single document);
   - no filing resolves;
   - the resolved key is missing from the store;
   - the text is longer than `4 * max_input_tokens` characters (default
     `max_input_tokens = 250_000`). The implementer checks this against each supported
     model's documented input limit and lowers the default if any is smaller.

   A filing is never truncated. The analysis counts `excluded:` errors separately from
   real errors.
7. **Accounting and failures** work as for the baseline arms:
   - each answer opens its own trace, carrying `query_id`, `cost_usd` and `latency_ms`;
   - a provider, resolver or store error becomes `SystemAnswer(error=...)`;
   - `RunHalt` propagates.
8. **Selection.** `system_names()` gains `"long_context"`, appended last, and
   `build_system("long_context")` builds the arm without network or database access.
   - This supersedes baseline-arms C7's exact four-name tuple, whose retired test is
     updated in this spec PR.
   - `build_system` gains `model: str | None = None`, passed to the long-context arm (its
     default when `None`), and `run_suite.py` gains `--model`.
   - Giving a model for any other system raises `ValueError`.

## Acceptance criteria

The fakes are the I/O boundaries only: a provider adapter behind a real `LLMGateway`, a
`FilingResolver` subclass, and an in-memory `FilingStore` subclass. The arm, `filing_text`,
the gateway, the prompt registry and the answer parser are real.

- **C1**: `filing_text(html)` on hand-built HTML gives exactly the expected lines:
  - text in an `ix:header` (with the inline-XBRL namespace), `script` or `style` never
    appears;
  - `<p>Net sales</p><p>increased</p>` gives two lines;
  - a row `<tr><td>Revenue</td><td>391,035</td></tr>` gives `Revenue | 391,035`;
  - runs of spaces, tabs and newlines inside a block become one space, and there is no
    empty line.

  Properties:
  - **Order:** random paragraphs come out as one line each, in order.
  - **Hidden header:** adding an `ix:header` holding random text never changes the
    output.

  Oracle: requirement 2, hand-built HTML.
- **C2**: for a case with `ticker` and `fy`:
  - `resolver.canonical_key` is called once with `(case.ticker, case.fy)`;
  - the store is read at the returned key;
  - exactly one provider call is made, whose user message contains `case.question` and
    the filing's text (the `filing_text` lines) and whose system message contains none of
    that text;
  - in the user message, the whole filing text comes before the question;
  - the answer's numbers, rows, `refused` and `hedged` are the reply object's own fields,
    with `fact_ids == []` and `chunk_ids == []`.

  Property, **shared prefix:** for two cases on the same filing with different questions,
  the two calls' system messages are identical, and their user messages are identical up
  to the end of the filing text.

  Oracle: requirements 1 and 3, a hand-built filing and replies.
- **C3**: exclusions. In each case below, `error` starts with `"excluded: "`, no provider
  call is made, `numeric_values == []` and `cost_usd == 0`:
  - `case.ticker is None` (the resolver is not called);
  - `case.fy is None` (the resolver is not called);
  - the resolver returns `None`;
  - the store has no object at the returned key;
  - the text is longer than `4 * max_input_tokens` characters.

  Property, exact at the boundary: with `max_input_tokens = n`, a document whose
  `filing_text` is exactly `4n` characters is sent, and one of `4n + 1` is excluded.

  Oracle: requirement 6.
- **C4**: serialization.
  - Several answers on the **same** filing, started together from different threads,
    never have two provider calls in flight at once.
  - Answers on **different** filings can: two calls on two filings are in flight
    together.
  - The test makes this deterministic, for example with a fake adapter that waits on a
    barrier only two concurrent calls can pass.
  - Each answer still makes exactly one call.

  Oracle: requirement 5.
- **C5**: accounting and failures, for an answer that made a provider call:
  - `query_id` is non-empty and differs between two answers;
  - `cost_usd > 0`;
  - doubling the fake's prompt and completion tokens doubles `cost_usd` (rel 1e-9);
  - `latency_ms >= 0`;
  - an exception from the resolver, the store or the provider (non-transient) gives a
    non-empty `error` that does **not** start with `"excluded: "`, and does not raise;
  - a gateway whose `RunBudget` has `max_llm_calls=0` makes `answer()` raise `RunHalt`.

  Oracle: requirement 7.
- **C6**: the model and selection:
  - the provider call's `model` is `"gpt-5.6-sol"` by default, and the given model when
    one is passed (any model with a `MODEL_PRICING` entry);
  - its stage is `long_context`;
  - a model with no `MODEL_PRICING` entry raises `ValueError` at construction, before any
    call (for example `"claude-opus-5-5"` until the multi-provider spec adds its price);
  - the model doesn't follow the tier mappings: with `XBRLRAG_USE_REPLACEMENT_MODELS` on
    or off, the default call's model is `"gpt-5.6-sol"`;
  - `system_names()` is `("xbrlrag_full", "closed_book", "naive_rag_plain",
    "naive_rag_filtered", "long_context")`;
  - `build_system("long_context")` and `build_system("long_context", model=m)` return a
    `SystemUnderTest` named `"long_context"`, without network or database access;
  - `build_system(name, model=m)` raises `ValueError` for every other name;
  - the registry resolves stage `long_context` at `v1` for the default model's provider
    and series.

  Oracle: requirements 4 and 8.

## Interface

New (stubbed in the spec PR):

- `src.eval.systems.base.FilingResolver`, an abstract base class:
  - `canonical_key(self, ticker: str, fiscal_year: int) -> str | None`, which returns the
    `FilingStore` key of the canonical annual filing's `canonical.htm` (for example
    `"AAPL/2025/10-K/canonical.htm"`), or `None`.
- `src.eval.systems.long_context.filing_text(html: str) -> str`
- `src.eval.systems.long_context.LongContextSystem`
  - `__init__(self, *, model: str = "gpt-5.6-sol", gateway: LLMGateway | None = None,
    resolver: FilingResolver | None = None, store: FilingStore | None = None,
    max_input_tokens: int = 250000) -> None`
  - `answer(self, case: EvalCase) -> SystemAnswer`
  - its `name` is `"long_context"`.

Behaviour on existing code, which the implementer adds:

- A Postgres `FilingResolver`, the default when none is injected. It queries
  `filing_registry` joined to `entity_master` on `cik` (`trading_symbol = ticker`), with
  `fiscal_year = fy`, `fiscal_period = 'FY'`, `superseded_by IS NULL` and an annual form.
  It takes the key from the accession's `entry_url` mirrored under the normalized root,
  so a restatement resolves to its `10-K_A/` directory. It is built lazily, never at
  construction.
- The registry entry and the `v1` templates for stage `long_context`. There is no
  `XBRL_STAGE_TIERS` entry, because the model is explicit.
- `system_names()` gains `long_context` and `build_system()` gains the `model` keyword.
  `run_suite.py` gains `--model` and records the model in the run's config.
- The shared arm plumbing (`src/eval/systems/_stage_llm.py`) accepts an explicit model as
  well as a stage's tier model, without changing the baseline arms' behaviour.
- Module and package docstrings, and the WS9-E5 manifest entry, which flips to `done` with
  this change.

## Notes

- **Size check, 2026-10-21.** The 66 loaded 10-K/20-F canonical filings reduce to about
  18K–203K tokens of text (median ~102K, estimated as characters ÷ 4). None exceeds the
  limit today.
- **Cost.** gpt-5.6-sol and Claude Opus 5.5 both cost $4 / $20 per 1M input / output
  tokens. Cached input costs $0.40 and $0.20 respectively.
  - A median filing (~102K tokens) costs about $0.41 uncached and about $0.04 from cache.
  - With three questions per filing, a 60-case run costs roughly $10–15 per model.
  - A 10-case pilot measures the real tokens, cache hits and cost before the run's
    `--max-cost-usd` is set.
- **Run scope** is not in this change. The run uses **60 cases**:
  - about 40 lookups and 20 compute;
  - on about 20 filings, 3 questions each. More filings with fewer questions each is
    better, because questions on one filing are correlated;
  - stratified by filer, built with the existing slice tooling, and run in filing order;
  - one run per model, on the same 60 cases.

  Screens are excluded by construction and reported as excluded.
- **Claude Opus 5.5** can't run until the multi-provider spec lands. It needs:
  - a `MODEL_PRICING` entry;
  - no `temperature` (Opus 5.5 rejects sampling parameters);
  - an explicit effort setting;
  - a cache breakpoint after the filing (Anthropic caches only where marked; OpenAI
    caches identical prefixes automatically);
  - handling of `refusal`.

  That spec is deferred until the sister project's multi-LLM change lands, so its design
  can be reused. It must merge before the benchmark freeze.
- **Not in spec tests:**
  - the Postgres resolver against the real database;
  - prompt wording;
  - the live pilot, which the implementer runs and reports in the PR.
- **XML declaration.** Canonical files may begin with an `<?xml ...?>` declaration, which
  `lxml.html` rejects in a `str`. `filing_text` must accept it; the spec tests' HTML has
  none.
- **Retired test update.** `test/spec/baseline-arms/test_ba_c7_selection.py`'s `NAMES`
  gains `"long_context"`, for requirement 7.
- **Oracles:** hand-built HTML, cases, filings and replies. No suite files, database or
  network.
