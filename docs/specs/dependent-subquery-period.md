# dependent-subquery-period — a dependent sub-query with no period of its own inherits the compound question's one fiscal year

`QueryDecomposer.decompose` (an LLM stage) splits a compound question into ordered sub-queries;
a later sub-query that uses an earlier one's result names it in `depends_on` and/or writes the
token `<<TOP:<id>:<n>>>`. The prompt asks each sub-query to carry its period over (prompt v3,
rule 6), but the model sometimes drops it.

Dev case `int-21e0eddbbb5b-094b3f3b` (template `lg-formal-2` of
`configs/eval/v2/phrasebook_compound.yaml`; deepseek, run `eval-67dfcc7839c2`, query
`531dbaf7c8e04b5b`) asked: "Identify the 3 members of the covered companies with the largest
Total Debt for FY24, and give the percentage growth in Payments to Acquire Property, Plant, and
Equipment of the first-ranked one." The question states its period once, in the screen part.
The decomposer returned

- `sq1`: "Among the covered companies, identify the 3 companies with the largest Total Debt for
  FY24." (no dependency)
- `sq2`: "For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire Property,
  Plant, and Equipment?" (`depends_on: ["sq1"]`, no year)

`sq2` became "For AAPL, what is the percentage growth in …?", the router resolved no period
(`periods=[]`), and the plan generator chose FY2025 vs FY2024 itself. The key wants FY2023 to
FY2024 (−13.8%); the answer was +34.6% for a year the user never asked about (PARTIAL, with
real citations).

This change adds a deterministic step after the LLM decomposition. It never asks the model
again and changes no prompt.

## Requirement

A new pure module `src/retriever/xbrl/subquery_period.py`: no I/O, no clock, no logging, no
LLM. Its functions never raise on the inputs below and never modify their arguments.

### 1. What "has a period of its own" means (`has_own_period`)

`has_own_period(text)` returns the `bool` `True` when the sub-query text states a period,
else `False`. Let *body* be `text` with every `<<TOP:<id>:<n>>>` token
(`query_decomposer.TOP_TOKEN_RE`) removed, so a sub-query id never counts as a year. *body*
states a period when at least one of these holds:

1. **A written year.** `extract_years_in_range(body)` (`src/retriever/extract_year_util.py`,
   its default bounds `[2000, 2100]`) is non-empty. This is the same reader that resolves a
   period mention's years (with wider bounds, so a year the router would refuse, such as
   "fiscal 2015", still counts as the sub-query's own and is never overwritten). A full date
   always writes a year, so it is covered.
2. **A period phrase.** Lowercase *body* and replace each run of whitespace by one space. A
   phrase *occurs* when it appears with no letter or digit (`a-z`, `0-9`) immediately before
   or after it. The phrases are those `EntityExtractor` reads in a period mention
   (`RELATIVE_PERIOD_PHRASES`, `PRIOR_PERIOD_PHRASES`, `_QUARTER_WORDS`, `_YTD_PHRASES`,
   `_H1_PHRASES`, `_9M_PHRASES` in `src/retriever/xbrl/entity_extractor.py`), listed here so
   the rule is fixed by this spec:
   - relative: `most recent`, `latest`, `this year`, `current year`, `current fiscal year`,
     `current quarter`, `current fiscal quarter`, `current period`;
   - prior: `prior year`, `last year`, `previous year`, `prior fiscal year`,
     `last fiscal year`, `previous fiscal year`, `preceding fiscal year`, `year ago`,
     `year-ago`, `preceding year`;
   - quarter: `first quarter`, `1st quarter`, `q1`, `second quarter`, `2nd quarter`, `q2`,
     `third quarter`, `3rd quarter`, `q3`, `fourth quarter`, `4th quarter`, `q4`;
   - cumulative: `year to date`, `year-to-date`, `ytd`, `first half`, `1st half`,
     `six months`, `six-month`, `h1`, `first nine months`, `nine months`, `nine-month`,
     `9 months`, `cumulative`.

   **The bare word `current` is not a period phrase here.** `EntityExtractor` reads it inside a
   mention the router already lifted as a period; in a whole question it is almost always a
   balance-sheet class ("current assets", "Current Ratio", "Liabilities, Current"), which 26
   of the 136 dev compound questions name. Only the `current …` phrases above count.

Everything else states no period, in particular "FY", "fiscal year" with no year, "annual",
"year-over-year", "growth", "the first-ranked one", "top 3" and "10-K". The lists can only err
towards "has its own period", which leaves the sub-query as today.

### 2. The compound question's one fiscal year (`stated_fiscal_year`)

`stated_fiscal_year(query)` returns the `int` year `Y` when the whole (compound) question
states exactly one annual fiscal year, else `None`. It returns `Y` only when all hold:

1. `extract_years_in_range(query)` (default bounds) is exactly `[Y]`;
2. `2018 <= Y <= 2035` (the bounds the router's period resolution uses, `_MENTION_YEAR_RANGE`);
3. no period phrase of Requirement 1.2 occurs in it;
4. `fiscal_date.find_written_dates(query)` is empty (a date's year need not be its fiscal year:
   Home Depot's "fiscal year ended February 2, 2025" is fiscal 2024);
5. the whole word `calendar` does not occur in it (lowercased, no letter or digit around it):
   a calendar year is not a fiscal year for an off-calendar filer.

So a question that states no year, two years ("in FY24 … from FY23"), a range, a quarter, a
relative period, a date or a calendar year gives `None`.

### 3. Inheritance (`inherit_dependent_periods`)

`inherit_dependent_periods(query, subqueries)` takes the compound question and the
decomposer's ordered `list[SubQuery]` and returns a new `list[SubQuery]` of the same length,
order, ids and `depends_on` lists. A sub-query `s` is **dependent** when `s.depends_on` is
non-empty or `s.top_refs` is non-empty. Its **dependencies** are the sub-queries of the list
whose id is in `s.depends_on` or is a `top_refs` id, other than `s` itself (an id naming no
sub-query of the list is ignored).

Let `Y = stated_fiscal_year(query)`. Walk the list in order. `s` **inherits** `Y` when all
hold:

1. `s` is dependent;
2. `has_own_period(s.query)` is `False`;
3. `Y` is not `None`;
4. every dependency `d` (with its text as already returned by this walk, so a chain inherits
   consistently) either has no period of its own or states exactly `Y`: `extract_years_in_range`
   of `d.query` with its `<<TOP…>>` tokens removed is `[Y]`, no period phrase of
   Requirement 1.2 occurs in it, and it writes no full date. A dependency that states another
   year, two years, a quarter or a relative period blocks the inheritance.

An inheriting sub-query's text gets the year appended, in this exact form: let `t` be
`s.query.rstrip()`; when `t` ends with `?` or `.`, the result is `t[:-1].rstrip()` +
`" for fiscal year "` + `str(Y)` + that last character; otherwise `t + " for fiscal year " +
str(Y)`. For example "For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire
Property, Plant, and Equipment?" becomes "For <<TOP:sq1:1>>, what is the percentage growth in
Payments to Acquire Property, Plant, and Equipment for fiscal year 2024?". The result then has
a period of its own, so applying the function again changes nothing.

Every other sub-query is returned with the same `id`, `query` and `depends_on` as given. In
particular:

- a sub-query that states a period of its own is never changed, whatever the parent says;
- a non-dependent sub-query is never changed, even when it states no period (the prompt's own
  example: "What are Amazon's main logistics-related risk factors?" next to an NVIDIA FY2026
  ask is an independent need, not scoped to FY2026);
- a list of one sub-query (every non-compound question, and every passthrough) is never
  changed: its only sub-query depends on nothing.

**Why the year rides in the text, not in a structured field.** The question text is the one
input every later stage reads: the router's LLM lifts the period mention, `resolve_periods`
resolves "fiscal year 2024" by the same code as any written year (so the planner's
`fiscal_years_in_scope` holds 2024), and the plan generator, synthesizer and verifier read the
sub-query text; the merged answer shows it as `[sq2] <query>`. The pipeline already rewrites a
dependent sub-query's text after decomposition (`_inject_dependencies`, logged as
`rewritten`). A structured hint would need new parameters on `_run_single`,
`BranchDispatcher.decide` and `IntentRouter.route` and would still leave the plan generator's
question without the year. The four-digit "fiscal year Y" form is the one
`extract_years_in_range` and the router both read without context.

**Why a multi-year parent inherits nothing.** "Top 3 by debt in FY24: how did the leader's
capex change from FY21?" states both the screen's year and the growth base. Inheriting the
latest year would plan FY2023-to-FY2024 growth and lose FY2021, a silent wrong with real
citations; inheriting both years would hand a ratio ask two years. Only a question with one
annual fiscal year has one unambiguous answer, so any other question leaves the sub-query as
today. In the stored runs, every dependent sub-query of a two-year question kept its years.

### 4. Wiring (locked through C4)

`QueryDecomposer.decompose(query)` applies `inherit_dependent_periods(query, subqueries)` to the
ordered sub-queries **after** the `split_loses_operation` check, and returns its result. Each
passthrough return (LLM or parse failure, a non-list reply, `keep_operation_whole`) is unchanged.
For each sub-query whose text changed it logs one trace event
`log_event("decision", point="query_decomposer", decision="inherit_period",
subquery=<id>, fiscal_year=<Y>)`. Nothing else in the pipeline changes: `_run_decomposed`
injects the companies into the rewritten text as before. (`subquery_period` imports `SubQuery`
and `TOP_TOKEN_RE` from `query_decomposer`, so `query_decomposer` imports it inside `decompose`
or below those definitions, never at its top.)

### 5. Interaction with other period rules

- **no-default-year** (`docs/specs/no-default-year.md`): no year is invented. The inherited year
  is one the user wrote; a question that writes none inherits nothing. A dependent sub-query
  with a yearless digit mention such as "Q3" has its own period (a quarter phrase), is not
  changed, and still degrades as that spec requires.
- **yearless-period-mention**: unchanged. When the router also lifts "year-over-year" from an
  inheriting sub-query, the inherited year resolves, so that mention is dropped by
  `partition_yearless_periods` as designed rather than leaving no period at all.
- **fiscal-year-range / -split, fiscal-year-ended-date, last-fiscal-year-phrase**: a range, a
  date or a relative phrase in the parent gives `None`, and in a sub-query counts as its own
  period, so none of their inputs is rewritten.

### 6. Unlocked, implementer

- Add the `inherit_period` decision to the period bullet of `src/retriever/xbrl/CLAUDE.md`
  (stage flow: decomposer, then this step) and to the decomposer section of
  `docs/guides/xbrl-flow/06-retrieval-pipeline.md`.
- The validation plan in Notes.

## Acceptance criteria

All values are exact `bool`, `int`, `None` or strings; there are no floats.

- **C1** (`has_own_period`): "For <<TOP:sq1:1>>, what is the percentage growth in Payments to
  Acquire Property, Plant, and Equipment?", "Among <<TOP:sq1:3>>, which has the highest Current
  Ratio?", "For <<TOP:sq1:1>>, what was the change in total current assets?", "What are
  Amazon's main logistics-related risk factors?", "For <<TOP:fy2024:1>>, what is the
  year-over-year growth in revenue?" and "Which is first-ranked by 10-K revenue?" state no
  period; the same texts with " in FY24", " for fiscal 2015", " in Q3", " for the latest
  fiscal year", " versus the prior year", " for the current fiscal year", " year to date" or
  " for the fiscal year ended June 30, 2024" appended each state one. Property: any text built
  from period-free words, balance-sheet words containing `current` and `<<TOP…>>` tokens with
  any id states none; metamorphic: appending any period phrase of Requirement 1.2 or any year
  form (`Y`, `FYyy`, `fiscal Y`, `FY'yy`) of a year in 2000-2099 makes it state one. Oracle:
  Requirement 1, by hand.
- **C2** (`stated_fiscal_year`): the `lg-formal-2` question of case 094b gives 2024; "… for
  fiscal year 2023, …" gives 2023; the leader-growth question with "in FY24 … from FY23" gives
  None; "FY2022 through FY2024", "Q3 FY24", "the latest fiscal year", "calendar 2024", "the
  fiscal year ended February 2, 2025", "FY2040", "fiscal 2015" and a question with no year give
  None. Property: a template with one year Y in 2018-2035 written in any of the four forms gives
  Y; metamorphic: adding a second distinct year, a period phrase, or the word `calendar` gives
  None. Oracle: Requirement 2, by hand.
- **C3** (`inherit_dependent_periods`): the stored 094b decomposition gives `sq1` unchanged and
  `sq2` exactly "For <<TOP:sq1:1>>, what is the percentage growth in Payments to Acquire
  Property, Plant, and Equipment for fiscal year 2024?"; a dependent sub-query marked only by
  `depends_on` or only by a `<<TOP…>>` token inherits; a three-step chain inherits on both
  dependents; a text ending in no punctuation or in "." gets the suffix by Requirement 3's
  rule; a two-year parent, a parent with no year, a dependency stating another year or a
  quarter, a dependent sub-query stating its own year, quarter or relative period, and a
  non-dependent yearless sub-query are all unchanged; the input list and its sub-queries are
  not modified. Property/metamorphic: for any parent with one year Y and any generated
  decomposition, (a) every sub-query that has a period of its own is returned unchanged,
  (b) every non-dependent sub-query is returned unchanged, (c) every dependent sub-query with
  no period of its own whose dependencies state no other period gets exactly the suffix
  " for fiscal year Y", (d) applying the function to its own output changes nothing, and
  (e) ids, order and `depends_on` are kept. Oracle: Requirement 3, by hand.
- **C4** (wiring through `QueryDecomposer.decompose`, LLM faked at the provider adapter behind
  the real `LLMGateway`): the hand-built 094b reply for the 094b question returns `sq2` with the
  inherited year and `sq1` unchanged, and the trace holds exactly one `decision` event with
  `point="query_decomposer"`, `decision="inherit_period"`, `subquery="sq2"`,
  `fiscal_year=2024`. A reply for a two-year question returns its texts as given and logs no
  `inherit_period` event. Property: for any year Y in 2018-2035, a single-year compound
  question and a reply whose dependent sub-query drops the year return that sub-query with
  " for fiscal year Y" appended, every other sub-query as the reply wrote it, and one event per
  rewritten sub-query. Oracle: Requirements 3-4, by hand.

## Interface

- `src.retriever.xbrl.subquery_period.has_own_period(text: str) -> bool`
- `src.retriever.xbrl.subquery_period.stated_fiscal_year(query: str) -> int | None`
- `src.retriever.xbrl.subquery_period.inherit_dependent_periods(query: str, subqueries: list[SubQuery]) -> list[SubQuery]`

## Notes

- **Oracle.** Requirements 1-3 applied by hand to synthetic and stored question text; the
  decomposer replies in the tests are hand-built JSON (the 094b one copied from the stored
  trace). No database, data file or real LLM is read by the tests.
- **Evidence (read-only, `xbrl_db` audit + eval schemas, 2026-11-14).** Stored compound eval
  traces exist only for the 11-13 s4sdm slices (`eval-67dfcc7839c2` dev, 5 compound cases;
  `eval-8ae493b49f30` validation, 1). Across every `decomposer` `llm_call` event in
  `audit.query_event`, 241 replies split a query (77 distinct questions); 6 distinct
  decompositions have a dependent sub-query, all from those compound cases. One dependent
  sub-query lacks a period (094b, above); the other five carry theirs.
- **Dry pass ($0, deterministic step only, 2026-11-14).** A throwaway implementation of
  Requirements 1-3 over the 173 distinct stored split decompositions: 6 dependent sub-queries,
  1 without a period of its own (094b `sq2`), which inherits 2024; 1 sub-query rewritten in
  all, 0 non-dependent ones. Over the frozen compound questions
  (`data/eval/v2_frozen/xbrl_v2_{dev,validation}_compound.json`), `stated_fiscal_year` gives
  a year for 76 of 136 dev (49 rank-by-ratio, 27 leader-growth; always the case's own `fy`)
  and None for 60 (59 two-year leader-growth questions and
  `int-a16b37bcfb9a-0796df51`, "by Assets, Current fiscal year 2024", where the metric name
  next to the year reads as the phrase "current fiscal year": a conservative miss), and a year
  for 41 of 44 validation (the 3 others are two-year).
- **Expected movement.** Only dependent sub-queries the model wrote without a period, in
  compound questions stating one fiscal year: 1 of 6 stored dependent decompositions. Every
  other sub-query, and every non-compound question, is returned unchanged, so no other case can
  move. The model's omission is stochastic, so a run may show no change.
- **Validation plan (implementer).** `pytest test/spec/dependent-subquery-period`, then
  `pytest test/spec -n auto`; a $0 dry pass of `inherit_dependent_periods` over the stored
  decomposer replies (expect only 094b's `sq2` to change, to FY2024); then one slice of the
  dev compound cases (`build_slice_suite.py`, deepseek, about $0.10) checking 094b's trace
  shows the `inherit_period` decision, the router's period 2024 and a FY2023-to-FY2024 plan.
  No full run for this change alone.
- **Follow-up, out of scope (reviewed route).** The decomposer prompt could restate rule 6 with
  a dependent example that keeps the year (a new version under
  `prompts/templates/xbrl/_base/query_decomposer/`); this step is the guarantee either way.
  A dependent sub-query of a two-year question that drops its years still reaches the plan
  generator with no period, which then picks years itself; making the planner refuse or ask
  instead is a separate change.
