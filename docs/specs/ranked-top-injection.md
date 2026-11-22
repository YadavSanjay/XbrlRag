# ranked-top-injection — a `<<TOP:<id>:<n>>>` token injects each company with its rank in the prior screen

`QueryDecomposer.decompose` (an LLM stage) splits a compound question into ordered sub-queries; a
dependent sub-query refers to an earlier screen's result with the token `<<TOP:<id>:<n>>>`
(`query_decomposer.TOP_TOKEN_RE`). `XbrlRetrievalPipeline._run_decomposed` runs the sub-queries in
order and `_inject_dependencies` replaces each token with the first *n* tickers of the dependency's
screen, comma-separated (`_extract_top_companies` sorts the screen rows by their `rank` key).

A $0 trace of dev compound run `eval-18b83b48fb4d` showed the defect. The decomposer wrote the
dependent sub-query "For the company ranked first among <<TOP:sq1:3>>, state the growth in its
accounts receivable…", and the token became "AMZN, MSFT, GOOGL": the prior screen's top 3 without
their ranks. The planner could not tell which company was first, ran its own screen and answered for
MSFT; AMZN (rank 1 of the prior screen) was computed but not stated.

This change makes the injected text carry each company's rank, so a sub-query that refers to a
position ("ranked first", "the leader", "#2", "second-largest") resolves without re-ranking. It
changes no prompt and makes no LLM call.

## Requirement

A new pure module `src/retriever/xbrl/ranked_top.py`: no I/O, no clock, no logging, no LLM. Its
functions never raise on the inputs below and never modify their arguments.

### 1. The injection text (`ranked_top_text`)

`ranked_top_text(rows, n)` takes the prior screen step's ranked rows **in the order the step
reports them** (rank 1 first; `XbrlFactstoreScreen._rank_and_filter` orders the rows, descending
for a top/largest screen and ascending for a bottom/smallest one, and numbers them `rank` 1, 2, …
in that list order) and the token's `n`.

- A row **names a ticker** when `row.get("ticker")` is a `str` whose `.strip()` is non-empty; its
  ticker is that stripped string.
- A row's **rank label** is its 1-based position in `rows` (the whole list, so a row that names no
  ticker still holds its position). The row's `rank`, `value` and every other key are not read:
  the function never re-sorts or re-ranks.
- The **top rows** are `rows[:n]` (the first *n* positions), keeping only those that name a
  ticker, in list order. Fewer than *n* rows means every row is a top row.

The result:

- `None` when `n < 1` or no top row names a ticker (the caller keeps today's placeholder, below);
- for `n == 1`: the single top row's ticker, bare (no label), exactly as injected today: a
  `<<TOP:x:1>>` sub-query already names its one company ("For <<TOP:sq1:1>>, what is …"), so
  there is no position to resolve, and the bare form keeps the most common dependent sub-query
  byte-identical;
- for `n >= 2`: each top row as `"<ticker> (#<label>)"`, joined by `", "` (comma, one space). For
  rows AMZN, MSFT, GOOGL and `n = 3`: `"AMZN (#1), MSFT (#2), GOOGL (#3)"`. The label is written
  even when fewer than *n* rows exist ("AMZN (#1)" for one row and `n = 3`): the sub-query asked
  for a ranked set, and the label says where the one company stood.

**Why this format.** `#k` reads as a rank to a person and to every model the router, planner and
synthesizer run on; the parentheses keep the ticker itself a separate token, so the router can lift
it as written; the format has no digit run of four, so `extract_years_in_range` and the period
phrases never read a year or a period in it.

### 2. A labelled mention resolves (`strip_rank_label`)

The router's LLM lifts company mentions "AS WRITTEN" (`intent_router` prompt), so a sub-query
"For the company ranked first among AMZN (#1), MSFT (#2), GOOGL (#3), …" can come back with the
mention `"AMZN (#1)"`. `EntityExtractor.resolve_companies` resolves a mention by exact ticker,
alias, normalized name and fuzzy name, and `"AMZN (#1)"` matches none of them: the mention is
unresolved and the non-screen query is refused. So the label is removed before resolution.

`strip_rank_label(mention)`: let `t = mention.strip()`. When `t` matches
`^(?P<name>.*?)\s*\(#[0-9]+\)$` (one trailing label: `(`, `#`, one or more ASCII digits, `)`, at
the very end, optionally preceded by whitespace) **and** `name.strip()` is non-empty, the result is
`name.strip()`. Otherwise the result is `mention`, unchanged (not stripped). Only one trailing label
is removed. Examples: `"AMZN (#1)"` → `"AMZN"`; `"  MSFT(#12) "` → `"MSFT"`;
`"Amazon.com, Inc. (#2)"` → `"Amazon.com, Inc."`; unchanged: `"AMZN"`, `" Alpha Holdings "`,
`"Berkshire Hathaway (Class B)"`, `"AMZN #1"`, `"AMZN (#)"`, `"AMZN (#1a)"`, `"AMZN (#1) Inc"`,
`"(#1)"`.

### 3. Wiring (locked through C3 and C4)

- **Router.** `IntentRouter.route` applies `strip_rank_label` to each company mention the LLM
  lifted (after its `str(...)`), before `split_company_mentions` and
  `EntityExtractor.resolve_companies`; so the resolver receives `[strip_rank_label(m) for m in
  mentions]`, in order. Nothing else in routing changes.
- **Pipeline.** `XbrlRetrievalPipeline._run_decomposed` / `_inject_dependencies` replace each
  `<<TOP:<dep>:<n>>>` token, when the dependency's response has a `screen` step with rows, by
  `ranked_top_text(<that step's rows as the step reports them>, n)`; the first `screen` step of
  the dependency's `execution_result["steps"]` is the one used, as today. The rows are passed in
  the step's own order: the existing sort by the `rank` key is removed (the screen already numbers
  its rows in list order). When `ranked_top_text` returns `None`, or the dependency has no screen
  step, the behaviour is today's: the fallback tickers of the plan's lookup steps, comma-separated
  without labels (they are not a ranking), else the placeholder "the top companies identified
  above" with its warning. Every character of the sub-query outside its tokens is unchanged, and
  several tokens in one sub-query are each replaced independently. The non-decomposed path is
  untouched.

### 4. Unlocked, implementer

- Update `test/unit/test_xbrl_screen_and_decompose.py::TestPipelineDecomposition`
  (`test_inject_top_companies`, `test_extract_top_companies_from_screen_rank`) to the new
  behaviour; the second asserts the sort by `rank` this change removes.
- Add the rank-label format to the decomposer bullet of `src/retriever/xbrl/CLAUDE.md` and the
  decomposer section of `docs/guides/xbrl-flow/06-retrieval-pipeline.md`.
- The validation plan in Notes.

## Acceptance criteria

All values are exact strings, lists or `None`; there are no floats.

- **C1** (`ranked_top_text`): screen rows CCC, AAA, BBB (in that order) give
  `"CCC (#1), AAA (#2), BBB (#3)"` for `n = 3`, `"CCC (#1), AAA (#2)"` for `n = 2`, all three
  labelled for `n = 5`, and `"CCC"` for `n = 1`; rows of an ascending (smallest-first) screen are
  labelled in their list order, never re-sorted by `value` or `rank`; a row with no, an empty, a
  blank or a non-`str` ticker is skipped while keeping its position (`"AAA (#1), CCC (#3)"`); a
  padded ticker is stripped; empty rows, `n = 0` or only tickerless top rows give `None`; the
  input is not modified. Property: for any list of rows with distinct tickers (some missing) and
  any `n` in 0–8, the result is Requirement 1's text computed by hand; metamorphic: changing the
  rows' `value` and `rank` keys never changes the result, and appending rows past position *n*
  never changes it. Oracle: Requirement 1, by hand.
- **C2** (`strip_rank_label`): the examples of Requirement 2 give exactly the results stated.
  Property: for any name (letters, digits, spaces, `.,&'-`) whose strip is non-empty and any
  label `(#k)`, `k` ≥ 0, joined with any whitespace (or none), the result is `name.strip()`;
  metamorphic: a mention that does not end in a label is returned unchanged, character for
  character. Oracle: Requirement 2, by hand.
- **C3** (router wiring, `IntentRouter.route` with the LLM faked at the provider adapter behind
  the real `LLMGateway` and the SQL resolver faked): a lifted mention `"AAA (#1)"` reaches the
  resolver as `"AAA"` and resolves to AAA; `["BBB (#2)", "CCC"]` reaches it as `["BBB", "CCC"]`.
  Property: any list of synthetic tickers, each with or without a label, reaches the resolver as
  the list with each label removed, in order, and every company resolves. Oracle: Requirements
  2–3, by hand.
- **C4** (pipeline wiring, `XbrlRetrievalPipeline.generate` on a compound question, every LLM stage
  faked at its provider adapter behind a real gateway, the screen faked at the fact store): with
  `sq1` a screen returning CCC, AAA, BBB in that order, `sq2` "For the company ranked first among
  <<TOP:sq1:3>>, state the growth in its accounts receivable for fiscal year 2024." reaches the
  router as "For the company ranked first among CCC (#1), AAA (#2), BBB (#3), state the growth in
  its accounts receivable for fiscal year 2024."; with `<<TOP:sq1:1>>` it reaches it as "For CCC,
  …"; `sq1` reaches the router unchanged. Property: for any order of the screen's rows, any `n`
  in 1–6 and any text around one or two tokens, `sq2` reaches the router as its text with each
  token replaced by Requirement 1's text for those rows; metamorphic: reversing the screen's row
  order (an ascending screen of the same filers) reverses which ticker carries `(#1)`. Oracle:
  Requirements 1 and 3, by hand.

## Interface

- `src.retriever.xbrl.ranked_top.ranked_top_text(rows: Sequence[Mapping[str, Any]], n: int) -> str | None`
- `src.retriever.xbrl.ranked_top.strip_rank_label(mention: str) -> str`

## Notes

- **Oracle.** Requirements 1–3 applied by hand to synthetic rows, mentions and questions (tickers
  AAA–EEE, fact ids 901+). The LLM replies in the tests are hand-built JSON; no database, data file,
  eval suite or real LLM is read.
- **Evidence.** The defect is the dev compound trace named above (read by the maintainer, $0). A
  pre-change probe of the C4 kit confirmed today's behaviour: `sq2` reached the router as "For the
  company ranked first among CCC, AAA, BBB, …", and a lifted `"AAA (#1)"` reached the resolver
  verbatim and did not resolve.
- **Expected movement.** Only dependent sub-queries with a `<<TOP:x:n>>` token, `n >= 2`, over a
  dependency with a screen step: their text gains the labels. `n = 1` tokens, fallback
  injections, non-dependent sub-queries and every non-compound question are byte-identical. Whether
  the planner then answers for the right company is a model behaviour this spec cannot lock; the
  slice below checks it.
- **Validation plan (implementer).** `pytest test/spec/ranked-top-injection`, then
  `pytest test/spec -n auto`; one slice of the dev compound leader cases (`build_slice_suite.py`,
  deepseek, about $0.10) checking that the traced `subquery` event shows the labelled text and
  that the answer names the rank-1 company. No full run for this change alone.
- **Follow-up, out of scope (reviewed route: prompts).** These prompts could name the format, each
  a new version under `prompts/templates/xbrl/_base/`: `query_decomposer` (that a `<<TOP:x:n>>`
  token, `n >= 2`, is replaced by "TICKER (#k), …", so a dependent sub-query may refer to a
  position); `intent_router` (lift a labelled company as its ticker, without `(#k)`; this change's
  `strip_rank_label` is the guarantee either way); `plan_generator` (a "(#k)" label is the prior
  screen's rank: plan for the named position, do not re-screen). `synthesizer` needs nothing.
