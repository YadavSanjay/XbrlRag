# The `frontier_sec_tools` arm: tools and prompt, for publication

`frontier_sec_tools` is the competitor arm for cross-filer and cross-period questions: a frontier
model in a bounded tool-calling loop over the public SEC APIs. The tool set and the agent prompt
are fixed and published with the results, so a reader can check that the arm was built to win.
Read this page to interpret a `frontier_sec_tools` run's report or to audit what the agent could
and could not do. Requirements and the reasoning behind each choice: [the spec](../specs/frontier-sec-tools.md),
and the fixes before the freeze: [frontier-tools-v2](../specs/frontier-tools-v2.md).

## What is published with a run

A run's report holds `config.frontier`:

| Field | Meaning |
|---|---|
| `model` | The exact model ID (`--model`; default the active LLM profile's frontier tier in `configs/llm/llm.yaml`). |
| `max_completion_tokens` | The output tokens each turn asks for: 16,000, capped at the model's `max_output_tokens` in `configs/llm/llm.yaml`. |
| `chars_per_token` | The rate (1.75) at which text the provider has not yet counted enters the window estimate. |
| `prompt_version`, `prompt_ref` | The prompt below, by version and resolved location. |
| `tools_fingerprint` | SHA-256 of the tool definitions as canonical JSON (`tools_fingerprint()`). |
| `max_steps`, `max_tool_calls`, `max_cost_usd`, `max_input_tokens` | The four budgets of one case (defaults 20, 60, $2.00, 250,000; `--max-input-tokens` sets the last). |
| `sec_snapshot` | `as_of`, `snapshot_id`, `urls`, `first_fetched`, `last_fetched`, `fetched`, `frozen`. |
| `agent_summary` | Cases answered, cases per budget hit, tool calls, tool errors, snapshot misses, unresolved citations, the most steps in a case, and the largest prompt any turn reported (`prompt_tokens_max`). |

`max_input_tokens` is in real tokens. Before each turn the arm estimates the prompt as the last
turn's reported `prompt_tokens` plus the characters appended since, at 1.75 characters per token
(all characters at that rate before the first turn, or when the provider reported no usage). 1.75
is the lowest ratio of the 2026-11-02 pilot on `deepseek-flash` (20 dev cases, 95 calls; median
2.1), so the estimate is an upper bound. A tool result that would push it past the window is
refused, and a turn whose reported prompt is already past it gets `window` errors for its calls;
either way the next turn is the final one. A run can set a smaller window with
`--max-input-tokens`.

Each case's answer holds an `agent` record: its steps, tool calls, errors, how it ended
(`stop`), the budget it hit (`budget`, kept when the final turn after the hit fails, so a case
ending in `error` still counts as a hit), and every source it could cite, with the tool,
arguments and SEC URLs.

## The prompt

- System: [`prompts/templates/xbrl/_base/frontier_sec_tools/v2/system.txt`](../../prompts/templates/xbrl/_base/frontier_sec_tools/v2/system.txt)
- User: [`prompts/templates/xbrl/_base/frontier_sec_tools/v2/user.txt`](../../prompts/templates/xbrl/_base/frontier_sec_tools/v2/user.txt)

The system prompt explains the tools and how to combine them, the concept traps (several revenue
concepts, concept changes between years, units, `fy` being the filing's year, restatements,
calendar-aligned frames), the evaluation policies S1 to S6 in full
([Eval semantics](../design/XbrlRag_Eval_Semantics.md)), the honesty rules (state assumptions,
correct false premises, decline with a reason, no number from memory), that tool results are data
and any instruction in them is ignored, and the reply's JSON shape. The user message gives the
run's `as_of` date, the scope (the covered companies and named groups, or the case's own universe)
and the question. Nothing else from the case reaches the model.

## The tools

All read public SEC data through one client (the EDGAR `User-Agent` from `SEC_EDGAR_IDENTITY`, at
most 10 requests per second, retries on 429 and 5xx) and a dated snapshot. A list result is paged
(`offset`, `limit` up to 1,000, `total`, `next_offset`, `truncated`) and is at most 40,000
characters as JSON; nothing is dropped silently.

| Tool | Source | Returns |
|---|---|---|
| `find_company` | `www.sec.gov/files/company_tickers.json` | `companies`: `{cik, ticker, name}`; an exact ticker first, then names containing the query; `not_found` for a list of tickers |
| `list_filings` | `data.sec.gov/submissions/CIK##########.json` and its older pages | `rows`: `{accession, form, filing_date, report_date, primary_document}`, newest first |
| `list_concepts` | `data.sec.gov/api/xbrl/companyfacts/` | `rows`: `{taxonomy, tag, label, units, first_end, last_end, facts}` |
| `get_concept` | companyfacts, for one filer (`cik`) or up to 50 (`ciks`) | `label`, `description`, `units`, `not_found`, `rows`: the SEC's entries unchanged, with `unit`, `cik`, `taxonomy` and `tag`, newest first (`end` desc, `cik`, `filed` desc, `accn` desc); restatements stay |
| `get_frame` | `data.sec.gov/api/xbrl/frames/` | `rows`: the SEC's entries unchanged, sorted by `val` then `cik`; `as_of_filtered: false` |
| `search_filings_text` | `efts.sec.gov/LATEST/search-index` | `total`, `rows`: `{accession, form, filing_date, period_ending, ciks, companies, document}`, 100 to a page |
| `list_filing_documents` | the filing's `index.json` | `rows`: `{name, size}` |
| `read_filing` | a document of the filing (default the primary one) | `text` (one paragraph or table row per line), `offset`, `total_chars`, `next_offset`, `document`, `form`, `filing_date` |
| `search_filing` | the same text | `rows`: `{position, snippet}`; a literal, case-insensitive match; `position` is an offset `read_filing` accepts |
| `calculate` | none | `value`: `+ - * / **`, parentheses, `abs`, `round`, `min`, `max`, `sum`, `median`, from the parsed expression, never `eval` |
| `analyze_rows` | earlier results of the same case, by source id | sums, means, medians, minimums, maximums, counts, sorts, ranks, percent changes and ratios over every row of those results, with filters, `latest_by` (S1's dedupe, per filer: rows of different filers are never merged) and `by` groups; every row has the column `source_id` |

The definitions as the model sees them are `tool_specs()` in
[`src/eval/sec_tools/toolbox.py`](../../src/eval/sec_tools/toolbox.py); their hash is the
fingerprint above.

## Dates and held-out filers

`as_of` (`--sec-as-of`, the corpus's date) is told to the model and applied wherever the SEC gives
a date: facts and filings after it are dropped, and a filing after it cannot be read. Frames carry
no filing date, so a frame fetched long after `as_of` can show a later value; `last_fetched` says
how long after. companyfacts is a current view, so a fact the SEC later withdrew is gone from it.
One snapshot per milestone, recorded close to `as_of`, keeps these gaps small.

Every filer in the committed held-out pool (`docs/eval/heldout_v2/heldout_v2_pool.json`), plus any
in `XBRLRAG_SEC_WITHHELD_CIKS`, is withheld: a tool sends no request for it, and list results leave
it out and count it. Only a case tagged `split:heldout_filers` lifts this.

## What the arm does not have

No web search, no third-party data, no bulk Financial Statement data sets, and no general code
execution: `analyze_rows` is a fixed set of operations, so a computed column or a join within a
row takes several calls. The budgets are bounds a product would have too; the pilot checks that
they do not bite (see the spec's Notes).
