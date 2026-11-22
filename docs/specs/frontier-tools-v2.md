# frontier-tools-v2 — fixes to the `frontier_sec_tools` tools and context window before the freeze

`frontier_sec_tools` is the competitor that XbrlRag must beat on cross-filer, cross-period
and compound questions ([decision record](../design/XbrlRag_HtmlRag_Decision.md);
pre-registration Amendment 1, D4 and §1.6). Before its tools and prompt are frozen, a
read-only review on 2026-11-06 found defects in how the arm works. Each one would make
the agent lose answers for reasons that have nothing to do with the question:

- `analyze_rows` with `latest_by` merges different filers. `get_concept` rows carry no
  filer, so `latest_by: ["end"]` over two filers' rows keeps one row per period end for
  the two filers together, and silently drops the other filer's value.
- `get_concept` reads one filer per call, so a fiscal-year screen over 30 filers costs
  30 calls per concept.
- `get_concept` pages oldest first. A filer with 15 years of history fills the first page
  with values from before the question's period.
- The window estimate counts characters / 4. In the 2026-11-02 pilot on `deepseek-flash`,
  the provider counted a median of 2.1 characters per token (range 1.75 to 3.0). A
  prompt "estimated" at 250K tokens is therefore about 475K real tokens, well past the
  272K tokens at which gpt-6.1-sol's price rises.
- Every turn asks for at most 8,000 output tokens. That is below the configured ceiling
  of `deepseek-v4-pro`, `claude-sonnet-5-5` and `claude-opus-5-5` (16,000), and the
  reasoning shares the budget, so a long tool turn loses its call.

The aim is a fair competitor, not a weak one. This change only fixes these defects. It
adds no data source, and it does not write the prompt: the v2 prompt's text arrives in its
own `reviewed` change, and this change only makes v2 the arm's default (requirement 11).
No XbrlRag pipeline behaviour changes.

## Requirement

### Tools (`src/eval/sec_tools/toolbox.py`)

1. **Rows name their fact.** Every `get_concept` row carries three more fields next to
   `unit`:
   - `cik`, as the filer's ten-digit zero-padded CIK string, as `find_company` returns it;
   - `taxonomy`, as given;
   - `tag`, as given.

   The SEC's fields stay unchanged, and no field is renamed or dropped. If an SEC entry
   ever carries a field with one of these names, the added value wins.
2. **Several filers in one call.** `get_concept` accepts `ciks`, a list of 1 to 50 CIKs,
   instead of `cik`. Exactly one of `cik` and `ciks` must be given; both, or neither, is
   a `bad_arguments` outcome.
   - The schema's `required` list becomes `["taxonomy", "tag"]`.
   - Each filer is read and filtered exactly as a one-filer call reads it, with the same
     as-of filter and the same `unit`, `form`, `fiscal_year` and `end_from` / `end_to`
     filters.
   - A withheld filer in `ciks` gives the `withheld` outcome for the whole call, as
     `get_frame` does with `ciks` today. A call that lifts withheld filers reads it.
   - Duplicate CIKs are read once. Two CIKs that are equal as integers are duplicates.
   - The payload adds `not_found`: the ten-digit CIKs, in the order given, of filers that
     do not report the concept. The call is `not_found` only when no filer in it reports
     the concept.
   - `label` and `description` come from the first filer, in the order given, that
     reports the concept. `units` is the union of every reporting filer's units, in
     first-seen order.
   - A one-filer call (`cik`) returns the same payload as before, plus the row fields of
     requirement 1. It also carries `not_found: []`.
3. **Newest first.** `get_concept` rows are ordered by:
   - `end`, descending;
   - then `cik`, ascending;
   - then `filed`, descending;
   - then `accn`, descending.

   A missing value sorts as the empty string. Paging (`offset`, `limit`, `next_offset`)
   is unchanged and applies to this order. The tool's description says the order.
4. **`latest_by` keeps one row per filer.** In `analyze_rows`, the key used by
   `latest_by` is the named columns plus the row's filer:
   - the filer is the row's `cik`, compared as an integer, so `"0000320193"`, `"320193"`
     and `320193` are one filer;
   - a row with no `cik`, or one that is not a CIK, is keyed by the named columns alone,
     as today.

   Among the rows of one key, the row with the greatest `(filed, accn)` is kept, as today.
   `cik` may still be named in `latest_by`, and the result is the same. The tool's
   description says that rows of different filers are never merged.
5. **`source_id` is a column.** In `analyze_rows`, every row read has the column
   `source_id`: the id of the source it was read from (`S1`, `S2`, ...). It can be used in
   `where`, `latest_by` and `by`, and it is never an `unknown_column`. A row that already
   has a `source_id` field keeps its own value; the rows of an earlier `sort` or `rank`
   result are such rows. `column` (the numeric column) is unaffected. The tool's
   description names the column.

### The arm (`src/eval/systems/frontier_sec_tools.py`)

6. **The window is measured, not guessed.** Before each model turn, the arm estimates
   the size of the prompt it is about to send:
   - Before the case's first model turn, the estimate is all message characters divided
     by `CHARS_PER_TOKEN`, which becomes **1.75**, the lowest ratio seen in the pilot.
     This rate makes the estimate an upper bound for all the text measured.
   - After a turn, the provider's `prompt_tokens` for that turn is the measured size of
     everything the arm had sent so far. The estimate for a later point is that number
     plus the characters appended since that turn, divided by `CHARS_PER_TOKEN`.
   - If a turn reports `prompt_tokens` of 0 or less, the provider gave no usage. The
     estimate for that point is then all message characters divided by
     `CHARS_PER_TOKEN`, as before the first turn.

   `_fits`, and every other window decision, uses this estimate in place of characters / 4
   against `max_input_tokens`. Requirement 9 adds what happens when the window is hit.
   `max_input_tokens` keeps its default and its 250,000 maximum. These limits are now
   real tokens, below the 272K price tier.
7. **The case records its largest prompt.** The `agent` record carries
   `prompt_tokens_max`: the largest `prompt_tokens` that any model turn of the case
   reported, or 0 when no turn reported a positive number. `agent_summary` adds
   `prompt_tokens_max`, the largest value over the run's records, read as 0 when a
   record lacks the field. This lets the D6 budgets read how close each case came to the
   window.
8. **Output at the model's ceiling.** Each model turn asks for the model's own per-call
   ceiling. The arm's default `MAX_COMPLETION_TOKENS` becomes **16,000**, and it still
   passes through `call_params`, so a model whose `max_output_tokens` is lower, or which
   has its own per-call config, keeps its own value. With the committed `llm.yaml`:
   - `deepseek-v4-pro`, `claude-sonnet-5-5` and `claude-opus-5-5` ask for 16,000;
   - `deepseek-flash` asks for 8,000.
9. **A turn that crossed the window closes the tools.** A turn can report `prompt_tokens`
   above `max_input_tokens`, for example when one tool result was larger than estimated.
   When it does, and the turn asks for tools:
   - each of its calls is answered with the `window` error and is not run;
   - the case's next turn is the final turn, and its stop reason is `"window"`.

   This is how a window hit found by `_fits` already ends a case.
10. **`run_config`** adds `chars_per_token` (1.75) and `max_completion_tokens` (the
    per-call value actually sent).
11. **Prompt v2.** The arm resolves its prompt at version `v2`
    (`prompts/templates/xbrl/_base/frontier_sec_tools/v2/{system,user}.txt`, added by a
    separate `reviewed` prompt PR that merges before the implementation), by making v2 the
    registry default for stage `frontier_sec_tools` in
    `prompts/templates/xbrl/registry.yaml`. `run_config()["prompt_version"]` is `"v2"`. v1
    stays on disk unchanged.

## Acceptance criteria

- **C1**: Every `get_concept` row carries `cik` (ten-digit string), `taxonomy` and `tag`
  as given, and every SEC field is unchanged (requirement 1). Oracle: the committed SEC
  companyfacts fixture in the test.
- **C2**: With `ciks`, `get_concept` returns the rows of every reporting filer, each
  filtered as a one-filer call. It lists non-reporting filers in `not_found`, fails as
  `not_found` only when none reports, fails as `withheld` on a withheld filer, reads
  duplicates once, and rejects both or neither of `cik`/`ciks` as `bad_arguments`
  (requirement 2). Oracle: the fixture's facts, counted by hand in the test. Metamorphic:
  the rows for `ciks=[a, b]` are exactly the union of the rows for `cik=a` and `cik=b`.
- **C3**: Rows are ordered as requirement 3 says, on every page. Oracle: the order
  written out by hand from the fixture. Metamorphic: concatenating the pages read with
  `next_offset` gives the unpaged list, and it is sorted under the stated key.
- **C4**: `analyze_rows` `latest_by` never merges rows of different filers, and keeps
  the greatest `(filed, accn)` per key (requirement 4). Oracle: two filers whose
  restated facts share period ends; the expected sum is computed by hand in the test.
  Property: for any rows with random filers, ends and filing dates, the count kept
  equals the number of distinct (filer, key) pairs.
- **C5**: `source_id` works in `where`, `latest_by` and `by`, is never
  `unknown_column`, and a row's own `source_id` field wins (requirement 5). Oracle:
  hand-built sources in the test.
- **C6**: The window estimate follows requirement 6 in all three cases: before the
  first turn, after a turn with usage, and after a turn without usage. A tool result
  that would push the estimate past `max_input_tokens` is refused with the `window`
  error. Oracle: the arithmetic `prompt_tokens + appended_chars / 1.75`, computed in the
  test from the fake gateway's reported usage. Metamorphic: raising the reported
  `prompt_tokens` never turns a refused result into an accepted one. No window decision
  is taken before the first turn (no tool result exists yet), so the before-the-first-turn
  estimate is observed through the no-usage case, which uses the same formula. Since the
  tests cannot know the exact character measure of a message, they place
  `max_input_tokens` with margins of a few thousand characters on each side of the
  decision.
- **C7**: `prompt_tokens_max` is the largest reported value in a case (0 when none was
  reported), and `agent_summary` takes the largest over the run's records, reading a
  missing field as 0 (requirement 7). Oracle: the fake gateway's scripted usage.
- **C8**: The per-call `max_completion_tokens` sent to `complete_tools` is 16,000 for
  `deepseek-v4-pro`, `claude-sonnet-5-5` and `claude-opus-5-5`, and 8,000 for
  `deepseek-flash` (requirement 8). `run_config` records it, together with
  `chars_per_token` (requirement 10). Oracle: `configs/llm/llm.yaml`
  `max_output_tokens`.
- **C9**: A turn reporting `prompt_tokens` above `max_input_tokens` while asking for
  tools gets `window` errors for its calls, none of which runs. The next turn is final,
  with `tool_choice="none"`, and the stop reason is `"window"` (requirement 9). Oracle:
  the scripted fake gateway and a toolbox that counts executions.
- **C10**: The arm's resolved prompt version and `run_config()["prompt_version"]` are
  `"v2"`, the resolved prompt's ref is `_base/frontier_sec_tools/v2`, and v1 still
  resolves when asked for explicitly (requirement 11). Oracle: requirement 11.
  Metamorphic: the default resolution's content hash differs from v1's, for every
  model's provider and series.

## Interface

There are no new public functions. These signatures change:

- `SecToolbox.execute("get_concept", {...})` accepts `ciks: list[str]` in place of
  `cik`, through the tool's schema in `tool_specs()`.
- `analyze_rows(arguments, results, *, max_result_chars=40000) -> ToolOutcome` keeps
  its signature. Requirements 4 and 5 change its behaviour.
- `frontier_sec_tools.CHARS_PER_TOKEN: float = 1.75` and
  `frontier_sec_tools.MAX_COMPLETION_TOKENS: int = 16000`, both module constants.
- `FrontierSecToolsSystem.answer(case).agent["prompt_tokens_max"]: int`;
  `agent_summary(records)["prompt_tokens_max"]: int`.
- `FrontierSecToolsSystem.run_config()` adds `chars_per_token` and
  `max_completion_tokens`, and reports `prompt_version` `"v2"`.

## Notes

- **Graduated tests.** The `frontier-sec-tools` lock is retired. Its graduated tests in
  `test/spec/frontier-sec-tools/` may assert behaviours this change replaces:
  - oldest-first `get_concept` order;
  - the `get_concept` required list;
  - characters / 4 in the window;
  - an 8,000-token output budget;
  - `latest_by` keyed by the named columns alone;
  - `get_concept` rows equal to the SEC's entries plus `unit` alone (requirement 1 adds
    `cik`, `taxonomy` and `tag`);
  - `agent_summary`'s exact key set (requirement 7 adds `prompt_tokens_max`);
  - a 300,000-token turn under the 250,000-token window ending as `cost` (requirement 9
    makes it `window`);
  - prompt v1 as the default (requirement 11).

  The spec PR updates exactly those assertions, and nothing else in that directory, so
  `pytest test/spec` stays green after the implementation merges.
- **Context limits.** The provider limits do not bind. As documented on 2026-11-06,
  `gpt-6.1-sol` has about 1.05M tokens, the Anthropic 5.5 models 1M and
  `deepseek-v4-pro` 1M, all far above the 250,000-token cap. That cap therefore stays the
  only window limit, and `llm.yaml` gains no context field. If a model is added whose
  context is below 250,000 + 16,000 tokens, this needs revisiting.
- **The 1.75 ratio.** It is the lowest characters-per-token ratio among the pilot's 95
  calls (`deepseek-flash`, 2026-11-02). After the first turn, the provider's own count
  replaces most of the estimate, so the conservative rate only governs the text appended
  since the last turn.
- **Fixtures.** The tests use synthetic companyfacts payloads (see
  `test/spec/frontier-sec-tools/sec_payloads.py` for the shape), with a fake HTTP
  transport and a fake gateway at the I/O boundary only. They read no `data/` and use no
  network.
- **Out of scope:**
  - the prompt's text: v2 is its own `reviewed` change, covering the fiscal-year rule, the
    shares-outstanding rule and "latest filing" meaning report forms;
  - a dimensional (segment) route;
  - the `--persist-audit` wiring, a separate `tested` fix.
