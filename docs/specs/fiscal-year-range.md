# fiscal-year-range — a period mention that names a range resolves to every year in it

Found on 2026-11-05 in the Anthropic smoke, on case `int-dfc1ab954793-00ccbb95` ("Please present
Procter & Gamble's shares outstanding for each year from fiscal year 2023 through fiscal year
2025."). The router LLM returned one period mention, `"fiscal year 2023 through fiscal year 2025"`.
Two defects then dropped FY2023 and FY2024 without a word:

1. **The resolver keeps one year of a range, and the fast path takes it.**
   `EntityExtractor._resolve_one_period` keeps `fiscal_year = years[-1]` ("ranges are left to
   plan_generator"), but `IntentRouter._try_fast_path_plan` checks only `len(result.periods) == 1`,
   so it built a one-step FY2025 `lookup` and the planner never ran. On the planner path,
   `PlanGenerator._fiscal_years_in_scope` would have passed only `[2025]`, and the XBRL narrative
   step filters its search on the same single year.
2. **`extract_years_in_range` misses phrasings.** "fiscal year 2023 through fiscal year 2025" and
   "fiscal 2023 to fiscal 2025" give `[2023, 2025]`, while "FY2023 through FY2025" and "2023
   through 2025" give all three. `html_rag`'s search scope (`src/retriever/html10k/scopes.py`)
   and the long-context arms' filing selection (`src/eval/systems/long_context_arms.py::
   filing_targets`) both re-read the mention with this function, so both searched or sent
   `[2023, 2025]`.

**How often.** Of the frozen v2 suites (2026-11-05, dev and validation only; no held-out set
read), a question contains a two-endpoint year range in **64 of 635 dev cases and 25 of 265
validation cases** (89 of 900), and in 116 of 1,151 dev and 43 of 476 validation paraphrases.
On the range text alone, `extract_years_in_range` returns the wrong years for 28 dev and 14
validation cases (33 and 11 paraphrases). The kinds are `compute` (growth, change, CAGR: 47 dev,
12 validation) and `breakdown` (the year-by-year trend: 17 dev, 13 validation); the trend cases
are the ones where a dropped middle year makes the answer wrong. Spans seen: 2 to 5 years.

This is a narrow fix ahead of the deterministic period parser of the
[v2 plan](../design/XbrlRag_v2_Plan.md) section 6.1; it adds no fiscal-calendar logic.

## Requirement

1. **A range is every fiscal year from its lower endpoint through its higher one, inclusive.** A
   *range* is two period expressions joined by one of these, case-insensitively:
   - `P1 through P2`, `P1 thru P2`, `P1 to P2`, `P1-P2`, `P1 – P2` (hyphen or en dash, with or
     without spaces);
   - `from P1 to P2`, `from P1 through P2`, `from P1 thru P2`;
   - `between P1 and P2`, where `between` immediately precedes `P1`.

   Each endpoint is one of the forms the suites use, and the two may differ:
   `FY2023`, `FY 2023`, `FY23`, `FY'23`, `FY’23`, `fiscal 2023`, `fiscal year 2023`, `fiscal years
   2023` (as in "fiscal years 2023 through 2025"), a bare `2023`, and a year-end date: `the fiscal
   year ended June 30, 2023`, `fiscal year ended dec 31 2023`, `December 31, 2022` (full or
   three-letter month, comma optional). An endpoint's year is the four-digit year it writes, or
   `20yy` for a two-digit one; a date endpoint's year is the date's year, which is the year the
   extractor already gives that form on its own.
2. **A reversed range names the same years.** "from fiscal 2025 to fiscal 2023" is 2023, 2024,
   2025, returned ascending.
3. **A range longer than 10 fiscal years is not expanded.** `MAX_RANGE_YEARS = 10` (inclusive
   count, so 2016 through 2025 still expands). `extract_years_in_range` returns only the two
   endpoints of such a range, as if they were listed. The resolver marks the mention ambiguous
   instead of guessing a subset, so the dispatcher degrades with the mention in its reason and
   the long-context arms refuse, as for any unresolved period.
4. **Lists keep their meaning.** Years joined by `and`, a comma, `vs`, `versus` or `compared with`,
   without the `between` of requirement 1, are those years and nothing between them:
   "fiscal 2023 and fiscal 2025" is 2023 and 2025. A text that holds a list and a range is the
   union: "fiscal 2019 and fiscal year 2023 through fiscal year 2025" is 2019, 2023, 2024, 2025.
5. **The resolver resolves a mention to every year it names** (range: requirement 1; list:
   requirement 4), not to the latest one. How `ResolvedPeriod` carries several years is the
   implementer's choice; what is fixed is what each consumer below receives.
6. **A mention that resolves to more than one year never takes the single-step lookup fast
   path.** `IntentRouter.route` returns `plan=None` for such a `NUMERIC_LOOKUP`, and
   `BranchDispatcher.decide_from_result` sends it to `PLAN_GENERATOR`, not degraded. A
   single-year mention keeps its one-step lookup.
7. **Every consumer of the router's periods sees every year:** the planner's
   `FISCAL_YEARS_IN_SCOPE`, the XBRL narrative step's `fiscal_years` filter, `html_rag`'s
   `search_scopes` and the long-context arms' `filing_targets`.

**In scope:** requirements 1 to 7. **Out of scope:**

- Mapping a year-end date to the filer's fiscal-year label (a 52/53-week year ending in early
  January): section 6.1's parser, with `fiscal_calendar`.
- A router LLM that splits one range into two mentions (`["fiscal 2023", "fiscal 2025"]`): the
  middle year is then not in any mention. See Notes.
- Quarter ranges ("Q1 FY23 through Q3 FY24"), two-digit `fiscal 23` forms (no frozen question
  uses one), `FY22–23` / `2021/22` split-year forms (their current reading is kept), and a range
  with an endpoint outside the caller's `year_range`.
- The legacy PDF retriever (`src/retriever/base_retriever.py`) also calls `extract_years_in_range`;
  it gains the fix but is not tested here (maintenance-only).

## Acceptance criteria

All comparisons are exact list or set equality. The resolver's `year_range` is `[2018, 2035]`.

- **C1**: `extract_years_in_range(text, year_range=[2018, 2035])` returns every year of a range,
  inclusive and ascending, for each separator and endpoint form of requirement 1, alone or inside a
  sentence: e.g. "fiscal year 2023 through fiscal year 2025" and "fiscal 2023 to fiscal 2025" give
  `[2023, 2024, 2025]`, "between the fiscal year ended January 30, 2022 and the fiscal year ended
  January 25, 2026" gives 2022 through 2026. Property: for any two endpoint forms, separator and
  span of 2 to 10 years, the result is `list(range(lo, hi + 1))`. Oracle: the phrase itself, read
  by hand.
- **C2**: a reversed range gives the same years as the forward one; a range of 11 or more years
  gives only its two endpoints, and one of exactly 10 years expands. E.g. "from fiscal year 2025
  to fiscal year 2023" gives `[2023, 2024, 2025]`; "fiscal 2018 through fiscal 2028" gives
  `[2018, 2028]`; "fiscal 2018 through fiscal 2027" gives 2018 through 2027; "2000 to 2030" with
  the default `year_range` gives `[2000, 2030]`. Property: reversing the endpoints does not change
  the result; above 10 years, the result is the endpoints. Oracle: requirements 2 and 3, by hand.
- **C3**: a list is its years; a list with a range is the union. "fiscal 2023 and fiscal 2025"
  gives `[2023, 2025]`; "the difference between revenue in fiscal 2025 and fiscal 2023" gives
  `[2023, 2025]`; "fiscal 2019 and fiscal year 2023 through fiscal year 2025" gives `[2019, 2023,
  2024, 2025]`. Property: listed years joined to a range clause give exactly the listed years plus
  the range's. Oracle: requirement 4, by hand.
- **C4**: `IntentRouter.route` (real router and extractor, fake LLM adapter and fake database) on a
  `NUMERIC_LOOKUP` with one resolved company, the concept "revenue" and one period mention that is
  a range or a list of several years returns `plan=None`, and `decide_from_result` gives
  `PLAN_GENERATOR` with `degraded=False`; the same question with "fiscal 2024" still gets a
  one-step `lookup` with `fy=2024`. Property over generated ranges: `plan` is `None`. Oracle:
  requirement 6; the years from the phrase by hand.
- **C5**: `PlanGenerator`'s user message carries `FISCAL_YEARS_IN_SCOPE` equal to every year the
  mentions name, ascending: `[2023, 2024, 2025]` for "fiscal year 2023 through fiscal year 2025"
  and for "fiscal year 2025 through fiscal year 2023"; `[2019, 2023, 2024, 2025]` for the mentions
  `["fiscal 2019", "fiscal 2023 to fiscal 2025"]`; `[2023, 2025]` for "fiscal 2023 and fiscal
  2025". Property over generated ranges. Oracle: the phrases, by hand.
- **C6**: for the router result of a range mention, `search_scopes` gives the company's cik with
  `fiscal_years` of every year, `filing_targets` gives `(ticker, year)` for every year and no
  reason, and the XBRL narrative step (`XbrlNarrativeRetriever.execute_step`, fake embedder and
  vector store) filters its search on every year. Property over generated ranges for
  `search_scopes` and `filing_targets`. Oracle: the phrase, by hand.
- **C7**: a mention whose range is longer than 10 years ("fiscal 2018 through fiscal 2030") gets no
  plan, `decide_from_result` degrades to `NARRATIVE_BRANCH` with the mention in its reason, and
  `filing_targets` refuses with no filings; a 10-year range ("fiscal 2018 through fiscal 2027")
  is not degraded and puts all 10 years in scope. Property over spans of 11 to 18 years: degraded.
  Oracle: requirement 3.

## Interface

No new functions (bug-fix mode). Behaviour changes, signatures do not:

- `src.retriever.extract_year_util.extract_years_in_range(text: str, year_range: list[int] = [2000, 2100]) -> list[int]`
- `src.retriever.xbrl.entity_extractor.EntityExtractor.resolve_periods(mentions, companies)`
- `src.retriever.xbrl.intent_router.IntentRouter.route(query, **kwargs) -> IntentRouterResult`
- read through, unchanged in signature: `PlanGenerator.generate_plan`,
  `BranchDispatcher.decide_from_result`, `XbrlNarrativeRetriever.execute_step`,
  `src.retriever.html10k.scopes.search_scopes`, `src.eval.systems.long_context_arms.filing_targets`.

## Notes

- **Oracle.** Every expected list is the phrase read by hand, written in the test. Fixtures are
  synthetic: filer `AAA` (cik `0000000001`, FYE 12-31). No `data/` file is read by the tests; the
  survey counts above were taken once, by hand, from `data/eval/v2_frozen/xbrl_v2_dev.json`,
  `xbrl_v2_validation.json` and the dev and validation `paraphrases_*_frozen.json`.
- **Separators seen in the frozen suites** (dev and validation questions and paraphrases, 248
  ranges): `from … to` 82, `between … and` 52, `-` 43, bare `to` 30, `from … through` 29, bare
  `through` 9, `thru` 2. Endpoint forms: `FY23` 100, year-end date 62, `fiscal 2023` 49, `fiscal
  year 2023` 37. No reversed range occurs (one reversed *list*: "the difference between Eli Lilly's
  repayments … in fiscal 2025 and fiscal 2024").
- **Suggested representation (not required):** one `ResolvedPeriod` per year, each with the
  mention, so `_try_fast_path_plan`'s `len(result.periods) != 1`, `_fiscal_years_in_scope`, the
  narrative retriever, `search_scopes` and `filing_targets` already union `fiscal_year` without
  change. The `intent_extraction` event then shows one chip per year. A `fiscal_years` field on
  one `ResolvedPeriod` works too, but every consumer above must then read it.
- **`between … and` for a change or CAGR** ("change between FY23 and FY25") expands to the middle
  year too. The planner only needs the endpoints; the extra year in scope is harmless there, and
  costs `html_rag` and `long_context_multi` one more filing. `extract_years_in_range` already
  expanded "between FY23 and FY25" before this change, so this keeps one reading for all forms.
- **Decisions for the reviewer.** (a) `MAX_RANGE_YEARS = 10`, with endpoints-only in the utility
  and ambiguous in the resolver. (b) A split range from the router LLM (two mentions) is not
  repaired here; re-reading the question text in the resolver would fix it, at the cost of the
  rule that only the router's mentions are resolved. (c) The NARRATIVE fast path stays one step:
  its search filters on every year (C6).
- **Measure after the fix** on a slice: the 89 dev/validation range cases plus a seeded sample
  (`scripts/eval/build_slice_suite.py`), for `xbrlrag_full`, `html_rag` and the long-context arms.
