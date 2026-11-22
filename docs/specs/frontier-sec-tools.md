# frontier-sec-tools — a frontier model with tools over the public SEC APIs

`frontier_sec_tools` is the real competitor for cross-filer and cross-period questions, and the
only arm besides XbrlRag that can attempt them ([v2 plan](../design/XbrlRag_v2_Plan.md) section
3.6). The success bar (decision 10.7) asks XbrlRag to beat it at p < 0.05 on those kinds, at no
more than a tenth of its cost per question. So beating a weak version proves nothing, and an
inflated cost proves nothing either. This change builds the arm earnestly: a fixed, documented
tool set, a real agent prompt, and nothing held back for convenience. The prompt and the tools
are published with the results.

The change has three parts: tool calling in `LLMGateway`, for every provider that has it; the
SEC tools with a dated snapshot; the arm and its runner wiring. No XbrlRag pipeline behaviour
changes.

## Requirement

### Tool calling in the gateway

1. **Neutral types** (`src/llm/gateway/tools.py`). `ToolSpec(name, description, parameters)`,
   where `parameters` is a JSON schema object. `ToolCall(call_id, name, arguments,
   arguments_error=None)`. `tool_result_message(call_id, content)`. Each is a plain value:
   attributes of the same names.
2. **The neutral conversation.** Messages stay as they are, plus two forms:
   - an assistant turn, `{"role": "assistant", "content": <text>, "tool_calls": [{"id", "name",
     "arguments"}], "provider_state": <opaque, JSON-safe>}`. The adapter builds it in
     `parse_tool_turn`, and only the same adapter reads `provider_state`. The caller appends it
     unchanged;
   - a tool result, `tool_result_message(...)`.

   `"cache_point": True` keeps its meaning on a `user` message.
3. **`LLMGateway.complete_tools`** is `complete` for a turn that may call tools. It keeps the
   same order and behaviour: cache-point count, `build_tool_params`, run budget, rate limiter,
   retries on transient errors only, final refusals, cost, trace, `llm_call` event. It is one loop:
   the implementer shares `complete`'s code and does not copy it. The result is an
   `LLMCallResult` with two new fields, `tool_calls: list[ToolCall]` (empty by default) and
   `assistant_message: dict | None`, which `ParsedResponse` carries too. `content` may be `""`
   when the turn holds tool calls. The `llm_call` event adds `tool_calls` (the count). A reply
   with no text and no tool call raises `ValueError`, as an empty reply does in `complete`.
4. **A provider without tools.** The base `ProviderAdapter.build_tool_params` and
   `parse_tool_turn` raise `ToolsUnsupportedError`, a `ValueError`, before any provider call.
5. **Unusable arguments are not a failed call.** Arguments that are not a JSON object give a
   `ToolCall` with `arguments=None` and `arguments_error` set, so the model can be told and can
   try again. A reply cut off by the output limit is the usual cause.
6. **OpenAI: the Responses API.** `OpenAIAdapter.build_tool_params` returns the kwargs of one
   `responses.create` call, with or without a cache point:
   - `model`, `input`, `tools`, `tool_choice`, `store: False`,
     `include: ["reasoning.encrypted_content"]`;
   - `tools` entries are `{"type": "function", "name", "description", "parameters", "strict":
     False}`. Responses function tools default to strict, which needs every property required and
     `additionalProperties: false`; the tools' optional arguments are neither (re-lock,
     2026-11-02, from the PR review);
   - `max_output_tokens`, `temperature` and `reasoning: {"effort": ...}` when given;
   - `prompt_cache_options: {"mode": "explicit"}` when a message is a cache point;
   - `input` holds system and user messages as `_responses_input` does; an assistant turn as its
     `provider_state["output"]` items, verbatim; a tool result as `{"type":
     "function_call_output", "call_id", "output"}`.

   `store: False` means reasoning has to travel in the request, which is why `include` asks for it
   and the items go back verbatim. Dropping them would make the model reason from scratch each
   turn, which costs it accuracy and money. `parse_tool_turn` reads `function_call` items
   (`call_id`, `name`, JSON `arguments`) and `output_text`, keeps every output item as a plain
   dict in `provider_state["output"]`, and reads usage as `_parse_responses` does.
7. **Anthropic.** `AnthropicAdapter.build_tool_params` extends `build_params` with `tools` entries
   `{"name", "description", "input_schema"}` and `tool_choice: {"type": "auto" | "none"}`. An
   assistant turn goes back as its `provider_state["content"]` blocks, verbatim, thinking blocks
   and their signatures included: the API requires it during tool use. Consecutive tool results,
   and a user message that follows them, form one `user` message: `tool_result` blocks in order,
   then the text block, which carries `cache_control` when it is a cache point. `parse_tool_turn`
   reads `tool_use` blocks and keeps every block as a plain dict. `stop_reason == "refusal"` raises
   as `parse` does.
8. **OpenAI-compatible and DeepSeek.** `OpenAICompatAdapter` builds a Chat Completions request:
   `tools` entries `{"type": "function", "function": {...}}`, `tool_choice`, an assistant turn as
   `{"role": "assistant", "content", "tool_calls": [{"id", "type": "function", "function":
   {"name", "arguments": <JSON string>}}]}` plus any `provider_state` fields, and a tool result as
   `{"role": "tool", "tool_call_id", "content"}`. `DeepSeekAdapter` adds its own rules: `max_tokens`,
   the configured effort, `cache_point` removed. `parse_tool_turn` reads `message.tool_calls`, and
   keeps `reasoning_content` in `provider_state` when the reply has one, so it goes back on the
   next turn.

### The SEC client and the snapshot

9. **The client.** `SecApiClient.get(url)` allows only `https` URLs on `data.sec.gov`,
   `efts.sec.gov`, and `www.sec.gov` under `/Archives/edgar/data/` or at
   `/files/company_tickers.json`. Anything else raises `ValueError`. Requests carry the EDGAR
   identity as `User-Agent`, from `identity` or `SEC_EDGAR_IDENTITY`; a missing or blank identity
   raises `ValueError`. Starts are paced to `max_rps` (at most 10, default 8) **across threads**,
   with the pacing lock held through the wait. A URL the snapshot holds is not paced.
   429 and 5xx are retried up to `max_retries` with a doubling backoff; 404 is an answer, not an
   error; any other status, or retries used up, raises `SecRequestError`.
10. **The snapshot.** `SecSnapshot` keeps each URL's first response, 200 or 404, on disk: a
    `manifest.json` (URL to SHA-256, status, `fetched_at`) and the bodies by SHA-256, written
    atomically. It is safe across threads, and a reopened snapshot holds what it held.
    - A URL the snapshot holds sends no request, ever. A later run reads the same bytes whatever
      the SEC now says.
    - The manifest is append-only: `store` on a URL already held changes nothing.
    - A snapshot has one `as_of`. Opening it with another raises `ValueError`.
    - `frozen=True` sends no request at all: a URL not held raises `SecSnapshotMissError`, from `get`
      and `store`.
    - `provenance()` gives `as_of`, `frozen`, `urls` (the count), `first_fetched`, `last_fetched`
      and `snapshot_id`: the SHA-256 of the lines `f"{url} {status} {sha256}"`, sorted, joined by
      `"\n"`.
    - The snapshot is the arm's own. It never shares a directory with `src/eval/keys`, so the
      arm cannot read a payload an answer key was built from.
11. **As of a date.** A question's facts must be the ones that existed at the run's `as_of`, which
    the operator sets to the corpus's date. Otherwise the arm is marked wrong for knowing about a
    filing that came later, or right by luck. Every tool applies it where the SEC gives a date:
    - `get_concept`, `list_concepts`: facts with `filed` after `as_of` are dropped;
    - `list_filings`, `search_filings_text`: filings after `as_of` are dropped, and the search's
      end date is at most `as_of`;
    - `read_filing`, `search_filing`, `list_filing_documents`: the filing's date comes from its
      submissions row. A filing after `as_of` gives the error `not_yet_filed`, with no request for
      its folder, and an accession the filer's submissions do not list gives `not_found`;
    - `get_frame`: the frames API gives no filing date, so its result says `"as_of_filtered":
      false`. Notes says what this means for a run.

### The tools

12. **The set is fixed.** `tool_specs()` gives these eleven, in this order. `tools_fingerprint()` is
    the SHA-256 of `json.dumps([{"name", "description", "parameters"}, ...], sort_keys=True)`. Every
    description says what the tool returns and how to page it.

    | Tool | Arguments | Source | Returns |
    |---|---|---|---|
    | `find_company` | `query` or `tickers` (a list, up to 200) | `www.sec.gov/files/company_tickers.json` | `companies`: `{cik, ticker, name}`, an exact ticker first, then names containing the query, case-insensitive; `not_found` for `tickers` |
    | `list_filings` | `cik`; `forms`, `filed_from`, `filed_to`, paging | `data.sec.gov/submissions/CIK<10>.json`, and the files in its `filings.files` | `rows`: `{accession, form, filing_date, report_date, primary_document}`, newest first |
    | `list_concepts` | `cik`; `query`, paging | `data.sec.gov/api/xbrl/companyfacts/CIK<10>.json` | `rows`: `{taxonomy, tag, label, units, first_end, last_end, facts}`; `query` matches tag or label, case-insensitive |
    | `get_concept` | `cik`, `taxonomy`, `tag`; `unit`, `form`, `fiscal_year`, `end_from`, `end_to`, paging | companyfacts | `label`, `description`, `units`, and `rows`: the SEC's entries, unchanged, each with `unit`, sorted by `(end, filed, accn)`. No deduplication: restatements stay visible |
    | `get_frame` | `taxonomy`, `tag`, `unit`, `period`; `ciks`, `order` (`desc` default or `asc`), paging | `data.sec.gov/api/xbrl/frames/<taxonomy>/<tag>/<unit>/<period>.json`, a `/` in the unit sent as `-per-` (`USD/shares` is `USD-per-shares`, the frames API's form; `USD%2Fshares` is a 404) | `label`, `description`, and `rows`: the SEC's entries, unchanged, sorted by `val`, then `cik` |
    | `search_filings_text` | `query`; `forms`, `ciks`, `filed_from`, `filed_to`, `page` | `efts.sec.gov/LATEST/search-index` (EDGAR full-text search) | `total`, and `rows`: `{accession, form, filing_date, period_ending, ciks, companies, document}`, 100 to a page |
    | `list_filing_documents` | `cik`, `accession` | `www.sec.gov/Archives/edgar/data/<cik>/<accession, no dashes>/index.json` | `rows`: `{name, size}` |
    | `read_filing` | `cik`, `accession`; `document`, `offset`, `max_chars` (default 20000) | that folder's document, default the filing's `primary_document` | `text`, `offset`, `total_chars`, `next_offset`, `document`, `form`, `filing_date` |
    | `search_filing` | `cik`, `accession`, `query`; `document`, `context_chars` (default 400), paging | the same text | `rows`: `{position, snippet}`. `query` is a literal, case-insensitive; `position` is an offset that `read_filing` accepts |
    | `calculate` | `expression` | none | `value` |
    | `analyze_rows` | `sources`, `op`; `column`, `where`, `latest_by`, `by`, `order`, the sides of a comparison, paging | the rows of earlier results in this case, no request | requirements 30-35 |

    `query` in `find_company` is not a search of filings, and `calculate` reads nothing. A
    `get_concept` tag the filer does not report is `not_found`; a filter that matches nothing is an
    empty list. `ciks` in `search_filings_text` go to the SEC as ten digits, comma-separated, and
    `page` n is `from=(n-1)*100`.
13. **Filing text** is `filing_text` applied to a document, for `.htm` and `.html`, and the body
    for any other text document. `filing_text` is a pure function, so the implementer moves it to
    `src/eval/sec_tools/` and `long_context` keeps importing the name. A CIK is accepted padded or
    not.
14. **Paging, never silent truncation.** A list tool takes `offset` (default 0) and `limit`
    (default 100, at most 1000; a larger value is clamped). Every list result carries `total`,
    `offset`, `next_offset` (`None` at the end) and `truncated` (`next_offset` is not `None`). One
    result, as `json.dumps(..., ensure_ascii=False)`, is at
    most `max_result_chars` (default 40,000): a result that would be longer holds fewer whole rows,
    or fewer characters of text, and says where to continue. Walking `next_offset` gives every row
    or character exactly once.
15. **`calculate`** evaluates `+ - * / **`, parentheses, numbers and `abs`, `round`, `min`, `max`,
    `sum`, `median`, from the parsed expression tree, never with `eval`. An expression over 2,000
    characters, an exponent above 64 in size, a name, an attribute, or a result that is not finite
    is an error. XbrlRag does its arithmetic in a sandbox, so a model doing it in its head would
    be a handicap this arm does not get.
16. **Failures are results.** `execute` never raises for a failed tool, with one exception:
    `RunHalt` propagates. It returns `ToolOutcome(payload={"error": <code>, "message": <text>},
    urls, error_code=<code>)`, with the codes `unknown_tool`, `bad_arguments`, `not_found` (a 404),
    `withheld`, `not_yet_filed`, `not_in_snapshot` and `upstream`, plus those of requirement 34. The
    model reads it and carries on.
17. **Tool arguments are untrusted**, since filing text can steer the model. `cik` must be 1 to 10
    digits; `accession` `##########-##-######` or its 18 digits without dashes; `document`
    a single file name matching `[A-Za-z0-9_.-]+` and holding no `..`; dates `YYYY-MM-DD`;
    `order` `desc` or `asc`; `page` at least 1; a required argument present; `taxonomy`, `tag`, `unit`
    and `period` must match `[A-Za-z0-9_.-]+` (`unit` may hold `/`, sent percent-encoded); `limit`
    and `offset` non-negative integers. A value that fails is `bad_arguments`, and no request
    is sent. URLs are built only by the tools, so no argument can choose a host.

### The arm

18. **The conversation.** Stage `frontier_sec_tools`, a versioned prompt at `v1` under
    `prompts/templates/xbrl/_base/frontier_sec_tools/`, registered with no `XBRL_STAGE_TIERS`
    entry, because the model is explicit. One system message, then one user message from `user.txt`
    with `{as_of}`, `{scope}` and `{question}`:
    - `as_of` is the toolbox's date. A model that does not know today's date cannot read "most
      recent", so leaving it out would be a handicap;
    - `scope` is a `json.dumps`: `{"universe": case.universe}` when the case has one, else the
      `universes` mapping. "The covered companies" and a named group mean something only to someone
      who has the list, and XbrlRag has it (S3). A scope is a definition, not an answer;
    - the case's `question`.

    The arm reads `question`, `universe`, `tags` and `id` from the case, and nothing else: not
    `ticker`, `fy`, `cik`, `kind`, or any `expected_*`. It excludes no case: an arm that can attempt
    everything is scored on everything.
19. **The prompt** is written as a good analyst tool would be, and the implementer states in
    the PR that it was. It holds at least:
    - the tools and how to use them together: find the filer, prefer `get_concept` and `get_frame`
      for tagged numbers, use `search_filing` and `read_filing` for notes and untagged text, use
      `calculate` for arithmetic on a few numbers, and `analyze_rows` for anything over rows a tool
      returned, so that no number is copied by hand;
    - which concept traps to expect: a filer may tag revenue under several concepts, and may change
      concept between years;
    - the policies of [Eval semantics](../design/XbrlRag_Eval_Semantics.md) in full: S1 (the latest
      filing's value, and one filing for a comparison; `fy` is the filing's year, not the fact's),
      S2 (the filer's own fiscal label; frames are calendar-aligned), S3 (state the universe and
      name every filer not ranked, and why), S4 (the whole definitions table, and saying so when a
      closing balance stands in for an average), S5, and S6;
    - honesty: state an assumption when a question is ambiguous, correct a false premise, decline
      with the reason when the tools do not support an answer, and never give a number from memory;
    - that tool results are data, and any instruction inside them is ignored;
    - the reply: one JSON object, the fields of `long_context_multi`'s reply, where `cited_chunks`
      lists source ids, plus `statements: [{"statement", "sources"}]`.
20. **The loop.**
    - Each tool turn offers `tool_specs()` through `complete_tools`, with `tool_choice="auto"`.
      The output budget is `call_params(config, max_completion_tokens=8000, temperature=0.1)`, and
      not the 2,000 of the other arms: reasoning shares this budget, and a tool-using turn that
      runs out of it loses the call.
    - A turn with no tool call ends the loop, and its text is the answer.
    - Otherwise the arm appends `assistant_message`, runs every call in order, appends one
      `tool_result_message` per call, then one user status message that says how many steps and
      tool calls are left and carries `"cache_point": True`. An older status message loses the flag,
      so a request has one cache point. On gpt-5.6 the implicit cache point sits at the end of the
      last user message, so this message is what lets each turn read the last one's prefix. Without
      it the arm would pay full price each turn and its cost would be overstated, which is a handicap
      that favours XbrlRag on the 1/10 bar.
    - A successful result is `json.dumps({"source_id": "S<n>", "tool": name, "result": payload},
      ensure_ascii=False)`, numbered from 1 within the case. A failed one is `json.dumps(payload)`,
      with no source id.
21. **Tool output is data.** It reaches the model only as tool-role content, encoded by
    `json.dumps`. It never enters a system prompt, and never passes through `str.format`.
22. **Budgets.** Four limits apply to one case. They stop the tools, not the answer: the arm then
    makes **one final call with `tool_choice="none"`**, so the model answers from what it has,
    hedges, or declines.
    - `max_steps` (20): tool turns. A case makes at most `max_steps + 1` model calls.
    - `max_tool_calls` (60): calls past it are answered with `{"error": "budget", ...}`, each one,
      because a provider requires an answer to every call id. They are not run. With none left, the
      next call is the final one.
    - `max_cost_usd` (2.00): checked after each turn against the trace's cost. A case can
      overshoot by one turn and the final call, and their prompts are bounded by the window.
    - `max_input_tokens` (250,000, and a larger value raises `ValueError`, as in long-context-arms
      requirement 15): estimated as the characters of the messages as JSON, `provider_state`
      included, over 4. A tool result that would pass it is replaced by `{"error": "window", ...}`.
      This keeps prompts near the 272K tokens at which gpt-5.6-sol's price rises, but it is an
      estimate and guarantees nothing, so requirement 36 prices a prompt past that point.

    The status message says how much is left, so the model can plan. A hit budget is reported, not
    hidden: `stop` says which, and `agent_summary` counts them. A provider that returns a tool
    call on the final turn gives `refused=True` with the reason in `text`.
23. **Citations.** The answer's `cited_chunks` and `statements[].sources` are source ids. In the
    `SystemAnswer`:
    - `chunk_ids` holds the ids that name a successful tool result of this case, once each, in
      order of first citation, statements first. The rest go in `agent["unresolved_citations"]`,
      once each;
    - `fact_ids` stays `[]`: the arm has no fact store;
    - `raw` is `{"answer_details": [{"statement", "fact_ids": [], "chunk_ids"}],
      "narrative_chunks": [{"chunk_id", "text"}]}`, with `text` the tool result's JSON, the shape
      `judge_wiring.answer_claims` reads. A reply with no statements gives one entry: its `text`,
      with `cited_chunks`. So the text judge scores this arm's citation support on what it really
      cited, where a long-context arm has none to score.
24. **Honesty.** The reply is read by `parse_structured_answer`, as for every arm, so `refused` and
    `hedged` mean what they mean elsewhere. The arm adds nothing to them and never turns a budget
    stop into a refusal by itself: the final call decides.
25. **The record.** `SystemAnswer` gains `agent: dict`, empty by default, and `to_dict()` includes
    it under `"agent"` only when not empty, so no other arm's payload moves. It holds:
    - `steps`: model calls, the final one included;
    - `tool_calls` (calls run), `tool_errors`, `snapshot_misses`, `by_tool` (name to count),
      `tool_ms`, and `withheld`: `withheld` outcomes plus rows left out;
    - `stop`: `"answered"`, `"steps"`, `"tool_calls"`, `"cost"` or `"window"`;
    - `budget_hit`: `stop != "answered"`;
    - `sources`: `[{"id", "tool", "arguments", "urls", "chars"}]`, one per successful call;
    - `unresolved_citations`.

    It survives a provider failure, and each tool call also logs a `tool_call` event on the trace
    (`tool`, `arguments`, `status`, `chars`, `latency_ms`, `source_id`).
26. **Accounting and failures** work as for the other arms, through `answer_in_trace`: `query_id`,
    `latency_ms`, `cost_usd` covering every call. An exception from the provider becomes `error`, a
    provider refusal becomes `refused`, `RunHalt` propagates. The arm is thread-safe, and its
    gateway and toolbox are built on first use, under a lock.
27. **Isolation.** The arm reads none of our fact store, Qdrant, the filing store, or any `xbrl_*`
    data. `src/eval/sec_tools/` and `src/eval/systems/frontier_sec_tools.py`, and every `src` module
    they import outside `src.llm`, `src.observability`, `src.logging_config` and `prompts`, import
    none of `psycopg2`, `qdrant_client`, `src.db`, `src.retriever`, `src.vectorstores`,
    `src.embeddings`, `src.pipelines`, `src.ingest`, `src.eval.systems.long_context` or
    `src.eval.keys.sec_live`. A package's own `__init__` does not count, because
    `src/eval/systems/__init__.py` imports every arm. And in a run, no database or Qdrant
    connection is opened.
28. **Held-out filers.** Nothing in development touches them, and the arm must work on them later.
    - `SecToolbox` takes `withheld_ciks`. For such a filer a tool sends no request and gives
      `withheld`. A list result leaves its rows out and says how many, `"withheld": n`, without
      naming them. That holds for `find_company` (a ticker lands in `not_found`), `get_frame` and
      `search_filings_text`.
    - The default is safe: `SecToolbox.from_env` withholds `load_withheld_ciks(
      default_withheld_path())`, every `candidates` entry of the committed
      `docs/eval/heldout_v2/heldout_v2_pool.json`. Those are the 38 filers `sectors.yaml` says are
      never development filers, and they include the draw and its alternates. A missing or empty
      pool raises `ValueError`. `XBRLRAG_SEC_WITHHELD_CIKS`, comma-separated, adds more, so an
      operator can withhold a filer without committing its name. The snapshot lives at
      `<XBRLRAG_SEC_SNAPSHOT_DIR>/<as_of>/`, default `<PROJECT_ROOT>/data/frontier_sec_snapshots`.
    - The arm passes `lift_withheld=True` only for a case tagged `split:heldout_filers`, so an H-F
      run needs no flag and a development run cannot lift it. `load_universes` reads `sectors.yaml`,
      which never holds a held-out filer.
29. **Names and wiring.**
    - `system_names()` is the seven names of long-context-arms, then `"frontier_sec_tools"` and
      `"html_rag"` in the order the two changes land ([html-rag-arm](html-rag-arm.md) requirement 25).
    - `build_system("frontier_sec_tools")` builds the arm offline. `build_system(name, model=m)`
      passes the model on.
    - `run_suite.py`: `--system` accepts it, `--model` applies to it, `--sec-as-of` sets
      `XBRLRAG_SEC_AS_OF` and is required for this system (a parser error, before any cost), and
      `--sec-universes` names a YAML file for `universes`. The report gets
      `config["frontier"] = {**arm.run_config(), "agent_summary": agent_summary(...)}`, printed at the
      end. `docs/eval/frontier_sec_tools.md` documents the tools and the prompt's location for
      publication, and `.env.example` gains `XBRLRAG_SEC_SNAPSHOT_DIR`, `XBRLRAG_SEC_AS_OF`,
      `XBRLRAG_SEC_FROZEN` and `XBRLRAG_SEC_WITHHELD_CIKS`.
    - `run_config()` gives `model`, `prompt_version`, `tools_fingerprint`, `max_steps`,
      `max_tool_calls`, `max_cost_usd`, `max_input_tokens` and `sec_snapshot`.

### Computing over rows

30. **`analyze_rows`, the eleventh tool.** It computes over the rows of earlier successful results in
    the same case, named by source id, so the model never copies a number across. It is a pure function
    of those rows: no request, no snapshot read, no `eval`, no code from the model. The arm stands in
    no more than a spreadsheet's filter and summary functions would.
31. **What it reads.** A list tool's `ToolOutcome.rows` holds **every** row of the result before
    paging and before the character cap, after the `as_of` and withheld rules. So `get_frame` with
    `limit: 1` costs one row of context, and `analyze_rows` still reads the whole frame. Rows are:
    `companies` for `find_company`, `rows` for the list tools. `search_filings_text` holds the one page
    the SEC returned. `read_filing` and `calculate` hold none. `analyze_rows` holds what it produced
    (requirement 33), so a result can feed another. The arm keeps the case's successful outcomes
    by source id and passes them as `results`, and `execute("analyze_rows", ...)` calls
    `analyze_rows(arguments, results, max_result_chars=...)`.
    - `sources` is a list of 1 to 50 ids, read in the order given, a repeated id once.
    - The rows read in one call are at most 100,000; more is `bad_arguments`. A source's rows stay in
      memory for its case only, and a case makes at most `max_tool_calls` calls, so this is bounded.
32. **Selecting rows.** Applied in this order:
    - `where`: a list of `{"column", "op", "value"}`, all of which must hold. `op` is `eq`, `ne`, `lt`,
      `le`, `gt`, `ge`, `in` (`value` is a list), `contains` (case-insensitive, on text) or `is_number`
      (no `value`). `lt` to `ge` compare numbers with numbers and text with text, so ISO dates work; a
      row of another type does not match. Column `cik` compares as an integer, because frames hold
      integers and other tools padded text. This is how a question selects by year (`fy`, or `end` with
      `ge`/`le`), by form (`form`) or by filer (`cik`, `ticker`, `entityName`).
    - `latest_by`: a list of columns. For each distinct key, only the row with the greatest `filed`, then
      the greatest `accn`, stays. This is S1's dedupe, and only when the model asks for it.
    - `by`: one column, for a result per group.
33. **Operations.** `op` is one of the following. `column` names a numeric column and is required
    except for `count`. Every result holds `op`, `column`, `where`, `count` (rows selected) and `inputs`:
    `[{"source_id", "rows_total", "rows_selected", "complete"}]`, where `complete` is false when the
    source holds fewer rows than its `total`, as a full-text search page can.
    - `sum` (`math.fsum`), `mean`, `median` (the mean of the middle two for an even count), `min`,
      `max`, `count`. `value` is a number.
    - `sort` and `rank`: `rows` are the selected rows, each with `source_id`, `index` and its own
      fields, ordered by `column`, `order` `desc` by default, ties in source order. `rank` adds `rank`:
      1, 1, 3 for a tie. The rows are paged as in requirement 14 (`offset`, `limit`, `total`,
      `next_offset`, `truncated`, the cap), and `ToolOutcome.rows` holds all of them.
    - `percent_change`: `current` and `base` are each `{"where": [...]}`, applied on top of the top-level
      selection. `value` is `(current - base) / |base| * 100`, the number of percent, as S4 defines
      growth. `base <= 0` is `not_meaningful`.
    - `ratio`: `numerator` and `denominator`, the same shape. `value` is their quotient, and a zero
      denominator is `not_meaningful`.
    - With `by`, every numeric operation gives `rows` per group: `{"key", "value", "count"}`, or with
      `current` and `base` (or `numerator` and `denominator`) in place of `count`, sorted by `value`,
      `order` `desc` by default, then `key`, and paged the same way. A group without one row on each
      side, or that is not meaningful, gives no row: it goes in `unmatched` as `{"key", "reason"}`, at
      most 100 with `unmatched_total`. So "which filer grew fastest" is one call, and the filers it
      cannot rank are named for S3.
    - Without `by`, `percent_change` and `ratio` need exactly one row on each side.
34. **Failures are results** (requirement 16), with no exception and no number:
    `bad_reference` (an id this case did not return, or one that holds no rows), `unknown_column`
    (no row read has the column), `non_numeric` (a selected row's value is missing, text, a boolean or
    not finite; the payload adds `rows`, the count, with `first_source_id` and `first_index`. The model
    can add `is_number`, which holds for exactly the other values, and a row is never skipped
    silently), `empty_selection`, `ambiguous_selection` (a side of a comparison without `by` selected
    more than one row; the payload adds `rows`), `not_meaningful`, and `bad_arguments` for any other
    malformed input, including more than 50 entries in `sources` before duplicates are removed.
35. **Citation.** A successful `analyze_rows` result is an ordinary source: it gets the next id, and its
    `ToolOutcome.derived_from` lists the sources it read. Its payload holds `row_refs`:
    `[{"source_id", "index", "value"}]` for the rows that fix the value (the row of `min` or `max`, the
    middle rows of `median`, the two rows of a comparison), and the first 100 selected for `sum`, `mean`
    and `count`, with `row_refs_total`. In the answer (requirement 23):
    - citing an `analyze_rows` id resolves it and then, transitively, what it was derived from, each
      id once, in `chunk_ids` and in the statement's `answer_details` entry. So a claim cites the
      computation, its parameters, and the results it read;
    - its entry in `agent["sources"]` adds `derived_from`, and its `urls` are the union of those
      sources' `urls`.

### The long-context price

36. **The tier.** `gpt-5.6-sol` in `configs/llm/pricing.yaml` gains one entry in `tiers`:
    `above_prompt_tokens` 272000, `input` 0.008, `output` 0.03, `cached_input` 0.0008, `cache_write`
    0.01. Its base rates do not change. The comment above the row that says there is deliberately no
    tier is replaced by one that gives the source and date. No code changes: `cost_usd` already reads
    `tiers`, so a prompt over the threshold pays these rates on the whole call. `max_cost_usd` and the
    run budget see that cost as they see any other.

**In scope:** requirements 1-36. **Out of scope:** any paid run, including the pilot in Notes; any
change to graders, keys, `RunBudget` or the XbrlRag pipeline; streaming to the UI; a
`provider_state` that crosses providers.

## Acceptance criteria

The fakes are the I/O boundaries only: a provider adapter or an SDK client behind the real
`LLMGateway` and the real adapters, and an `HttpTransport` holding hand-built SEC payloads for
synthetic filers AAA, BBB, CCC and HOLD. The arm, the toolbox, the client, the snapshot, the prompt
registry and the answer parser are real. No test reads a real held-out ticker.

- **C1**: `complete_tools` returns `tool_calls` and `assistant_message` from a fake adapter behind the
  real gateway, records cost at `MODEL_PRICING` rates on the trace, retries a transient error, stops a
  refusal, honours the run budget, logs the event with the count, accepts a turn with no text, and
  rejects a turn with neither. The base adapter raises `ToolsUnsupportedError` with no provider call.
  Property: doubling tokens doubles the cost. Oracle: requirements 1-5.
- **C2**: the OpenAI request is exactly requirement 6's, with and without a cache point; the reply
  gives the calls, the text and the usage; unreadable arguments give `arguments_error`. Property: any
  list of output items goes back verbatim on the next request, in place. Oracle: requirement 6.
- **C3**: the Anthropic request is requirement 7's; thinking blocks go back verbatim; a refusal
  raises. Property: n tool results and a status message form one user message of n+1 blocks, in
  order. Oracle: requirement 7.
- **C4**: the DeepSeek request is requirement 8's, with `reasoning_content` going back; the compat
  adapter gives the same shape. Property: `cache_point` never reaches the wire. Oracle: requirement 8.
- **C5**: the client sends the identity, refuses a blank one and any other host, retries as stated,
  gives 404 as an answer, and raises `SecRequestError` when retries run out. Property: starts are at
  least `1/max_rps` apart, across threads, on a fake clock. Oracle: requirement 9.
- **C6**: a held URL sends no request, even after the fake SEC changes its bytes; a reopened snapshot
  holds them; a 404 is held; another `as_of` raises; a frozen snapshot sends nothing; the id equals the
  hash computed in the test. Property: the manifest only grows, and any order of stores gives the same
  id. Oracle: requirement 10.
- **C7**: each date rule of requirement 11 holds on hand-built filings dated around `as_of`, and
  `get_frame` says it was not filtered. Property: moving `as_of` later never removes a row. Oracle:
  requirement 11.
- **C8**: `tool_specs()` lists the eleven names in order, and each tool gives its hand-listed result,
  older submissions pages included. Property: `tools_fingerprint` equals the hash computed in the
  test. Oracle: requirements 12, 13.
- **C9**: paging gives the envelope on every list tool. Property: walking `next_offset` gives each row
  or character once, and no result passes `max_result_chars`. A `position` from `search_filing` starts
  the match in `read_filing`. Oracle: requirement 14.
- **C10**: `calculate` gives hand-computed values, refuses the stated expressions, and a failed tool is
  an outcome with the stated code. Every bad argument sends no request. Property: any path-like
  `document` is refused, and a random arithmetic tree equals the same tree computed in the test.
  Oracle: requirements 15-17.
- **C11**: the conversation is requirement 18's and 20's: stage, prompt `v1`, as-of, scope, offered
  tools, output budget, one cache point, source ids; the prompt holds every tool name and the honesty
  and semantics markers. Filing text with braces and instructions stays in tool-role content, and the
  system message is the same on every turn. Property: changing `ticker`, `fy`, `cik`, `kind` or an
  `expected_*` field changes nothing sent. Oracle: requirements 18-21.
- **C12**: each budget stops the tools, makes one `tool_choice="none"` call and records its `stop`;
  calls past `max_tool_calls` are answered and not run; a window breach never reaches the model; a tool
  call on the final turn is a refusal. Property: model calls never pass `max_steps + 1`, whatever the
  script. Oracle: requirement 22.
- **C13**: citations resolve as stated, unresolved ones are listed, `raw` gives `answer_claims` its
  passages, a refusal stays a refusal, and a failed result cannot be cited. Property: `chunk_ids` is a
  subset of the sources. Oracle: requirements 23, 24; `judge_wiring.answer_claims`.
- **C14**: `cost_usd` equals the sum over turns at `MODEL_PRICING` rates; `query_id` differs between
  answers and `latency_ms >= 0`; the record is as stated, survives `json.dumps` and a provider failure,
  and is absent for an answer with none; a provider error is `error`, a refusal `refused`; `RunHalt`
  propagates from the first and a later turn and from a tool; `agent_summary` gives the hand-built
  totals. Property: doubling tokens doubles the cost. Oracle: requirements 25, 26.
- **C15**: a run opens no database or Qdrant connection, and the import walk finds none of the listed
  modules. Property: the arm's answer does not change when those endpoints differ. Oracle:
  requirement 27.
- **C16**: a withheld filer gives `withheld` with no request, its rows vanish and are counted, its name
  appears nowhere; a case tagged `split:heldout_filers` reaches it through the same arm, and no other
  split does; `load_withheld_ciks` reads a synthetic pool; `from_env` withholds the committed pool and
  the operator's CIKs; a missing pool raises; `load_universes` reads the committed configs, and its
  filers are not withheld.
  Property: any mix of withheld and other rows shows exactly the others. Oracle: requirement 28.
- **C18**: `analyze_rows` gives each operation's hand-computed value on hand-built rows, including a
  median over 5,001 rows of a frame of which the model saw one; filters, `latest_by`, `by`, and the
  paged `sort` and `rank` behave as stated; each failure of requirement 34 is an outcome with its code;
  no request is sent; `as_of` and withheld rows never enter; through the arm, an id resolves to its
  sources under C13's rules. Property: `sum`, `mean`, `median`, `min` and `max` equal `math` and
  `statistics` on any integers, in any row order; walking a ranking gives each row once, within the
  cap. Oracle: requirements 30-35.
- **C17**: `system_names()` holds the name after the seven existing ones; the arm builds offline under its name; `model` passes
  through; the constructor's checks raise; `run_config` holds the stated keys. Oracle: requirement 29.
- **C19**: the `gpt-5.6-sol` row holds the stated tier and its base rates; a call of exactly 272,000
  prompt tokens pays the base rates and one of 272,001 pays the tier on the whole call, cached and
  written tokens included; the arm's `cost_usd` is that cost, turn by turn. Property: above the
  threshold the cost is the tier's closed form, for any token counts. Oracle: requirement 36;
  OpenAI's pricing page, 2026-10-31.

## Interface

New (stubbed in the spec PR). Stubs have no dataclasses, so these are plain classes with an explicit
`__init__`, and their signatures are locked.

`src/llm/gateway/tools.py`:

- `ToolsUnsupportedError(ValueError)`
- `ToolSpec(name: str, description: str, parameters: dict[str, Any])`
- `ToolCall(call_id: str, name: str, arguments: dict[str, Any] | None, arguments_error: str | None = None)`
- `tool_result_message(call_id: str, content: str) -> dict[str, Any]`

Methods:

- `LLMGateway.complete_tools(*, messages, tools, model_ref, stage=None, tool_choice="auto",
  max_completion_tokens=None, temperature=None, reasoning_effort=None, prompt_templates=None,
  prompt_version=None) -> LLMCallResult`
- `build_tool_params(*, messages, model, tools, tool_choice="auto", max_completion_tokens=None,
  temperature=None, reasoning_effort=None, extra=None) -> dict` on `ProviderAdapter`, `OpenAIAdapter`,
  `OpenAICompatAdapter`, `AnthropicAdapter`, `DeepSeekAdapter`
- `parse_tool_turn(raw: Any, *, stage: str) -> ParsedResponse` on `ProviderAdapter`, `OpenAIAdapter`,
  `OpenAICompatAdapter`, `AnthropicAdapter`

`src/eval/sec_tools/client.py`:

- `SecSnapshotMissError(LookupError)`, `SecRequestError(RuntimeError)`
- `SecSnapshot(root: Path, *, as_of: str, frozen: bool = False, wall_clock=None)`, with
  `lookup(url) -> tuple[int, bytes] | None`, `store(url, status, body) -> None`,
  `provenance() -> dict[str, Any]`
- `SecApiClient(snapshot, *, transport=None, identity=None, max_rps=8.0, max_retries=3, backoff_s=1.0,
  clock=None, sleep=None)`, with `get(url) -> tuple[int, bytes]`

`src/eval/sec_tools/toolbox.py`:

- `ToolOutcome(payload: dict[str, Any], urls: list[str], error_code: str | None = None, rows:
  list[dict[str, Any]] | None = None, derived_from: list[str] | None = None)`
- `tool_specs() -> list[ToolSpec]`, `tools_fingerprint() -> str`
- `calculate(expression: str) -> float`
- `analyze_rows(arguments, results: Mapping[str, ToolOutcome], *, max_result_chars: int = 40000) ->
  ToolOutcome`
- `default_withheld_path() -> Path`, `load_withheld_ciks(path: Path) -> frozenset[str]`
- `load_universes(groups_path: Path, sectors_path: Path) -> dict[str, Any]`
- `SecToolbox(client, *, as_of: str, withheld_ciks, max_result_chars=40000)`, with `from_env(*,
  transport=None)`, `execute(name, arguments, *, lift_withheld=False, results=None) -> ToolOutcome`,
  `provenance() -> dict[str, Any]`

`src/eval/systems/frontier_sec_tools.py`:

- `FrontierSecToolsSystem(*, model="gpt-5.6-sol", gateway=None, toolbox=None, universes=None,
  max_steps=20, max_tool_calls=60, max_cost_usd=2.0, max_input_tokens=250000, reasoning_effort=None)`,
  with `answer(case) -> SystemAnswer`, `run_config() -> dict[str, Any]`, and `name =
  "frontier_sec_tools"`, `model`
- `agent_summary(records) -> dict[str, Any]`

Behaviour on existing code, which the implementer adds:

- `ParsedResponse` and `LLMCallResult` gain `tool_calls` and `assistant_message`.
- `SystemAnswer` gains `agent`.
- `src/eval/systems/__init__.py`: the name, the class and `build_system`, with `model` taken for this
  arm. `LONG_CONTEXT_NAMES` keeps its meaning; `run_suite.py` uses its own list for `--model`.
- `filing_text` moves (requirement 13). `prompts/templates/xbrl/registry.yaml` gains the stage.
- `docs/design/XbrlRag_Evaluation.md` (the `WS9-E5` row), `XbrlRag_Eval_v2_Architecture.md`, the
  evaluation guide and `docs/design/XbrlRag_LLM_Harness.md` (tool calling).

## Notes

**The pilot, for the operator; it is not run here.** Before any comparison, and through the
pre-spend checks in `CLAUDE.md` (keys and `SEC_EDGAR_IDENTITY` matter here; the arm needs no Postgres,
Qdrant or RagInference), run about 20 dev or validation cases, none on a held-out filer, across the
cross-filer, cross-period and compound kinds, with `--sec-as-of` set to the corpus's date and
`--max-cost-usd 40`. Read off:

1. `cached_prompt_tokens` is above zero from the second turn on. If it is zero, the arm's cost is
   overstated: fix that first. The Responses tool path's cache point is unverified until then, since
   it is built from the docs and no live call.
2. The first live reply of each provider matches the shapes in requirements 6-8, which are also built
   from the docs. In particular, whether gpt-5.6 returns `encrypted_content` in reasoning items, and
   whether the EFTS response holds the fields requirement 12 lists.
3. `agent_summary`: how many cases hit a budget, and which. Above 5% of cases on steps or cost means the
   budget is too tight for a fair result: raise it and rerun.
4. Median and maximum cost per case, for the 1/10 bar and the budget of each milestone.

**How a run stays comparable when SEC data changes.** Three pieces work together:

- `as_of` is fixed by the operator to the corpus's date, and is told to the model, and is applied to
  everything that has a date, so a filing made after it does not exist for the arm.
- The snapshot fixes bytes: the first response to a URL is the response for that URL for good. Two runs
  on one snapshot see the same data wherever they ask the same thing.
- The report records `sec_snapshot`: `as_of`, `snapshot_id`, `urls`, `first_fetched`, `last_fetched`,
  `fetched`, `frozen`. A later run on one snapshot has a larger id, since the model may ask new things,
  and the earlier data is unchanged. A strict rerun sets `XBRLRAG_SEC_FROZEN`: a URL not held is then
  `not_in_snapshot` to the model and is counted, which makes it a small handicap, so a frozen run is a
  reproduction and not a milestone result.

Two gaps stay, and they should be stated with results. Frames carry no filing date, so a frame fetched
long after `as_of` can show a value filed later, and `last_fetched` shows how long after. And
companyfacts is a current view: a fact the SEC later withdrew is gone from it, `as_of` or not. The
sound practice is one snapshot per milestone, recorded close to `as_of`.

**Each choice that could handicap the arm, and what was done.**

| Choice | Risk | Decision |
|---|---|---|
| Tools limited to the four named APIs | No way to go from ticker to CIK, or to find an accession | Adds `find_company`, `list_filings`, `list_filing_documents`, all public SEC data |
| Whole companyfacts as one result | Several MB, unusable | `list_concepts` and `get_concept` give all of it, paged. Nothing is dropped |
| Frames as returned | Thousands of unsorted rows | Sorted, filterable by `ciks`, paged. A top-N takes one call |
| Reading a 10-K in pages | A note at 300K characters is out of reach in 20 steps | `search_filing` finds it, and `read_filing` starts there |
| Arithmetic in the model's head | XbrlRag has a calculator | `calculate` |
| No code execution | A median or ranking over rows means copying numbers, and 5,000 rows cannot be copied | `analyze_rows`, over every row of a result and not only the page the model saw. The maintainer asked for this on 2026-10-30 |
| Output budget of 2,000, as the other arms | Reasoning eats it, and a call is lost | The model's 8,000 |
| No date in the prompt | "Most recent" is unanswerable | `{as_of}` |
| No universe | "The covered companies" is unanswerable | `{scope}`, with CIKs. Note that the arm then gets the universe free, while XbrlRag derives it |
| Dropping reasoning between turns | Worse answers and more cost | Items go back verbatim on all three providers |
| No caching across turns | Cost overstated, on a bar that is a cost ratio | A cache point each turn; pilot check 1 |
| Budget hit means an error | A near-finished case scores nothing | One final call, tools off |
| Fact rows deduplicated by us | Hides restatements, and is our reading of S1 | Rows come unchanged, and the prompt carries S1 |
| Grader needs `fact_ids` | The arm's right numbers grade `PARTIAL`, as every baseline's do | Not changed here. Compare on `value_accuracy`, not `accuracy`, as for all baselines (arm-neutral-grading). The arm's scorecard must say so |
| Text judge needs passages | No citation support, as a long-context arm | `raw` gives it the cited tool results (requirement 23) |
| Withheld filers in dev | Dev results slightly weaker on rows that are out of scope anyway | Lifted on H-F, the run that counts |

**Limits I chose that a reader could call a handicap, and why they stay.** Twenty steps, 60 calls and
$2 are bounds, and the plan requires a capped step budget. A real product has them too. The pilot's
check 3 is what shows they do not bite. There is no web search and no third-party data: the plan names
the SEC APIs, and the point is what public SEC data supports. There is no Financial Statement data set
bulk file, because the APIs cover the same facts per filer and per frame.

There is still no general code execution. `analyze_rows` is a fixed set of operations, so what is left
out is what such a set leaves out: no join of two columns inside a row, no window functions, no
regression, no computed column, so `by` compares a filer's two periods but a margin per filer over
several years takes one `by` ratio per year. A model with Python would do these in a line. The arm can
still reach them through several calls and `calculate`, at more steps and more cost, so the handicap
is steps and not reach. It is small on the kinds in the bar, and the pilot can show it: look for cases
that hit a budget while chaining `analyze_rows`. Text is also outside it: a table found by
`read_filing` is not rows, so numbers in an untagged table are still copied into `calculate`.

**`analyze_rows` and the judge.** A claim citing a median over 5,000 rows cites its computation, whose
parameters and `row_refs` the judge can read, plus the source's page. The judge cannot see all 5,000,
but the value is a deterministic function of rows that `sources` and `where` identify, so it can be
recomputed from the snapshot. Its rows are the arm's own, read as the SEC gave them, with no use of
our fact store.

**Provider coverage.** Requirements 6-8 give all three the tools. The model is a human decision, since
3.6 says matched models unless the comparison is the model itself. `gpt-5.6-sol` is the default, as for
the long-context arms. Its price is promotional through at least 2026-11-21 (`pricing.yaml`).

**Why the tier is priced here.** `llm-provider-profiles` left it out because the long-context arm
excludes filings over 250K estimated tokens and real counts ran 0.86-0.98 of the estimate, and it said
a later change could add the tier with the test that assumed linear cost. That measurement was on filing
text. This arm's prompts are mostly JSON tool results full of digits, plus opaque `provider_state`, and
nobody has measured those. An undercount above 8.8% passes 272K (272 / 250). Cost is half of the success
bar, so an unpriced turn would misstate the measure. The rates are from OpenAI's pricing page, Standard,
long context, read 2026-10-31 at developers.openai.com/api/docs/pricing: $8.00 input, $0.80 cached, $10.00
30-minute write, $30.00 output per 1M, "Long context: >272K input tokens". The row uses the 30-minute
write, as the base rate does. The page does not say whether cached tokens count toward the threshold, or
whether the long rate covers the whole request. `cost_usd` counts all prompt tokens and prices the whole
call; the pilot's first check on a long turn is to compare that with the bill. The retired
`long-context-arm` doubling test now stops at 136,000 prompt tokens, since its oracle holds only below
the tier.

**Not in spec tests:** the prompt's wording beyond the markers of C11; `run_suite`'s flags and the
`config["frontier"]` wiring, `build_system`'s doc, the publication doc and `.env.example` (unit tests or
review, by the implementer); `from_env`'s real httpx transport; any live call. Run all of `test/spec`
after implementing.

**Retired tests.** `test/spec/long-context-arms/test_c1_names.py` holds for the first seven names, so
it passes whichever of this arm and `html_rag` lands first. C17 pins no exact tuple, for the same
reason.

**Oracles.** Hand-built SEC payloads in the SEC's published JSON shapes, for synthetic filers; the SEC's
API documentation for URLs; hand-computed arithmetic; `MODEL_PRICING` rates; hashes computed in the test.
The provider shapes come from each provider's API reference and from the adapters' existing specs. No
held-out filer appears anywhere; the committed pool is read only by count and by CIK, in a guard.

**Which filers are withheld, and two things an operator must do.** The pool file is the source
because it is committed and is the list `sectors.yaml` keeps out of development. A filer outside it
is not guarded unless its CIK is in `XBRLRAG_SEC_WITHHELD_CIKS`, so set that for any filer held out
for another reason, such as external test v1's. And `load_universes` reads `sectors.yaml`, which is
the development universe, so "the covered companies" in an H-F run needs its own list:
`--sec-universes`, unless each case carries a `universe`.
