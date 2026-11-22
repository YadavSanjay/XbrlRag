# fiscal-year-range-split — a range the router splits into its endpoints keeps its middle years

[`fiscal-year-range`](fiscal-year-range.md) made one period mention that names a range resolve to
every year in it. Its decision (b) left one case out: the router LLM returning a range as **two
endpoint mentions** (`["FY23", "FY25"]` for "FY23 through FY25"). Each mention then resolves to
its own year, the resolved years are `[2023, 2025]`, the planner's `FISCAL_YEARS_IN_SCOPE` is
`[2023, 2025]`, and a trend plan may look up only the endpoints. The answer is a two-year trend,
cited and confident, for a three-year question: a quietly wrong result.

The router prompt invites it. `prompts/templates/xbrl/_base/intent_router/v3/system.txt` defines
`period_mentions` with `"FY21 to FY23"` as one entry, while its Example 3 ("from FY2022 to
FY2023") lists `["FY2022", "FY2023"]`.

**Evidence** (read-only, `eval` and `audit` schemas of `xbrl_db`, dev and validation runs of
`xbrlrag_full`; no held-out set read):

- `int-e7396d5254a1-0073bc7a` "Union Pacific Corporation shares outstanding trend, FY23 through
  FY25": run `eval-06ccd735ebad` (DeepSeek) got `period_mentions ["FY23", "FY25"]`, a plan over
  2023 and 2025 only, INCORRECT "matched 2/3 periods; missing FY2024". Run `eval-d33940dc59b5`
  got `["FY23 through FY25"]` and was CORRECT.
- `int-03c77d1d4aa0-01d41bfe` "Tesla NI trend, the fiscal year ended December 31, 2023 through
  the fiscal year ended December 31, 2025": both compared runs got the two dated endpoints as two
  mentions. In `eval-043192f2a5e2` the plan looked up 2023 and 2025 and a change between them
  (INCORRECT, missing FY2024); in `eval-33dbd82fe9e0` the planner happened to add 2024 (CORRECT).
- `int-548774abac0e-03c2c576` "Meta Stockholders' Equity Attributable to Parent by year, the
  fiscal year ended December 31, 2023 to the fiscal year ended December 31, 2025": in 12 runs the
  router split the range into its two endpoints 6 times, and **every one of those 6 runs planned
  only 2023 and 2025 and was INCORRECT** (missing FY2024); the 6 runs with one range mention, or
  three listed years, were CORRECT.
- Survey over every stored dev and validation `xbrlrag_full` result whose question states a range
  of 3 or more years (2026-11-11): 58 distinct cases had the range split into endpoints in at
  least one run. Of the `breakdown` (trend) results with a split range, 73 of 130 were CORRECT and
  **all 40 INCORRECT ones read "missing FY…"**; with the range kept as one mention, 147 of 184
  were CORRECT. (Different questions sit in each group, so the gap is indicative, not a measured
  effect.) `compute` results (change, growth, CAGR) need only the endpoints and are not hurt.

The repair is deterministic: after period resolution, a range that the question itself states
and whose two endpoints were both resolved brings its middle years back. The question text and
the filer's fiscal calendar decide it, never the LLM.

## Requirement

1. **A stated fiscal-year range.** A range in the question is two endpoints joined as in
   [`fiscal-year-range`](fiscal-year-range.md) requirement 1, case-insensitively:
   - `P1 through P2`, `P1 thru P2`, `P1 to P2`, `P1-P2`, `P1 – P2`, `P1 -> P2`, `P1 → P2`
     (hyphen or en dash, spaces optional), with or without a leading `from`;
   - `between P1 and P2`, where `between` immediately precedes `P1`.

   Each endpoint is one of `FY2023`, `FY 2023`, `FY23`, `FY'23`, `FY’23`, `fiscal 2023`,
   `fiscal year 2023`, `fiscal years 2023`, a bare `2023`, or a written date with the month first
   (`December 31, 2023`, `Dec 31 2023`, `Dec. 31, 2023`), optionally led by `the`, `fiscal year
   ended` or both. The two endpoints may differ in form. Not ranges: two-digit split-year forms
   (`FY21-23`, `2021-22`, `FY2023-24`: the second endpoint is no endpoint form), ISO dates
   (`2023-12-31`), and years joined by anything else.
2. **A list is not a range.** Years joined by `and` (without the `between` of requirement 1), a
   comma, `vs`, `versus`, `compared with` or `or` are a list: "FY23 and FY25", "FY23, FY25",
   "FY23 vs FY25", "the change between revenue in FY23 and FY25" state no range.
3. **Quarter ranges are out.** A range is not a fiscal-year range when its own text, or the three
   whitespace-separated words immediately before its first endpoint, contain a quarter cue
   (`mention_period_kind(...)` returns `"quarter"` for that text, e.g. `Q1`, `first quarter`,
   `three months`, `nine months`, `quarterly`).
4. **Each endpoint is a fiscal-year label.** An undated endpoint is the year it writes (`20yy`
   for a two-digit one). A dated endpoint:
   - when the calendar has usable rows, is the `fiscal_year` of
     `match_period_end(date, calendar, "FY")` (fiscal-year-ended-date requirement 2, 7 days'
     tolerance) when that status is `matched`; any other status drops the whole range (never the
     date's digits);
   - when the calendar has no usable rows, is the date's calendar year (today's reading of a
     dated mention without a calendar).
5. **Bounds and order.** A range whose two labels are not both within `year_range` (inclusive) is
   dropped. A reversed range names the same years: it is reported as `(lower, higher)`.
   `stated_fiscal_year_ranges` returns the distinct ranges, ascending, including ranges longer
   than `MAX_RANGE_YEARS`.
6. **The repair.** `missing_range_years(question, resolved_years, calendar)` returns, ascending,
   every year `y` not in `resolved_years` with `lo < y < hi` for some stated range `(lo, hi)`
   such that `lo` and `hi` are both in `resolved_years` and `hi - lo + 1 <= MAX_RANGE_YEARS`
   (10, `src.retriever.extract_year_util`). It never returns a year outside such a range, never a
   year already resolved, and nothing when the question states no range (a list, requirement 2),
   when only one endpoint was resolved, or when the range is longer than 10 years (the resolver
   keeps such a range ambiguous, fiscal-year-range requirement 3).
7. **Wiring (implementer; not locked here).** `IntentRouter.route`, after
   `EntityExtractor.resolve_periods`, completes the resolved periods:
   - `resolved_years` = the `fiscal_year` of every non-ambiguous `ResolvedPeriod` with
     `fiscal_period == "FY"`; `question` = the `query` the router was given (the sub-query after
     decomposition);
   - `calendar` = the primary company's `fiscal_calendar` rows (`fiscal_year, fiscal_period,
     period_end`, the read `_resolve_dated` already does), read only when the question writes a
     full date (`find_written_dates(query)` is non-empty) and a company with a cik resolved;
     otherwise empty;
   - each year `missing_range_years` returns is appended as one `ResolvedPeriod` from
     `EntityExtractor._resolve_year` (period `FY`, not `ytd`), its `mention` the range as written
     in the question, so every consumer (planner scope, narrative filter, `html_rag` scopes,
     long-context targets) sees it unchanged;
   - when it adds years it logs `log_event("decision", point="period_resolution",
     decision="stated_range_fill", added=[...], resolved=[...])`;
   - a unit test in `test/unit/` covers the wiring with a fake LLM adapter and a fake connection
     (the pattern of `test/spec/fiscal-year-range/conftest.py`): split mentions `["FY23",
     "FY25"]` for "… FY23 through FY25" give `FISCAL_YEARS_IN_SCOPE` `[2023, 2024, 2025]`; the
     mentions `["FY23", "FY25"]` for "… FY23 and FY25" keep `[2023, 2025]`;
   - `src/retriever/xbrl/CLAUDE.md` ("A range the router LLM splits into two mentions loses its
     middle years (not repaired)") is updated to describe the repair.
8. **Router prompt (implementer; not locked here).** A new `intent_router` version `v4` under
   `prompts/templates/xbrl/_base/intent_router/`, the registry default moved to it: Example 3
   lists the range as one mention, `"period_mentions": ["from FY2022 to FY2023"]`, and the
   `period_mentions` definition says a range ("FY23 through FY25", "from … to …", "between … and
   …", "the fiscal year ended … through the fiscal year ended …") is **one** entry as written,
   never split into its endpoints, while a list ("FY23 and FY25") is one entry per year.
   Requirement 6 stays the guarantee when the model splits anyway.

**In scope:** requirements 1 to 8 (1 to 6 locked). **Out of scope:** quarter ranges
(requirement 3), ranges between two-digit split-year forms, day-first and ISO-date endpoints, a
range whose endpoints were resolved through two different companies' calendars (the primary
company's calendar is used, as `_resolve_dated` does), and the legacy PDF retriever.

## Acceptance criteria

All comparisons are exact list equality. `year_range` is `(2018, 2035)` unless stated.

- **C1**: `stated_fiscal_year_ranges(question, calendar=())` returns the stated ranges of
  requirements 1, 2, 3 and 5 as fiscal-year labels: "Union Pacific Corporation shares outstanding
  trend, FY23 through FY25" gives `[(2023, 2025)]`; each separator (`through`, `thru`, `to`,
  `from … to`, `from … through`, `-`, `–`, `->`, `→`, `between … and`) and each endpoint form
  gives its range; "from FY2025 to FY2023" gives `[(2023, 2025)]`; two ranges give both; "FY23
  and FY25", "FY23, FY25", "FY23 vs FY25", "FY23 versus FY25", "FY23 compared with FY25", "the
  change between revenue in FY23 and FY25", "FY21-23", "2021-22", "FY2023-24", "2023-12-31" and
  "Q1 FY23 through FY25" give `[]`; "fiscal 2010 through fiscal 2025" (an endpoint below 2018)
  gives `[]`; "fiscal 2018 through fiscal 2030" gives `[(2018, 2030)]`. Property: any endpoint
  forms, any separator and any span within the bounds give exactly the one range; the same two
  endpoints joined by a list connector give `[]`. Oracle: the question text, read by hand.
- **C2**: dated endpoints are read through the calendar (requirement 4). With a synthetic
  December-31 filer calendar (FY 2021 to 2025 ending December 31), the Tesla and Meta questions
  give `[(2023, 2025)]`. With a synthetic 52/53-week filer whose labels lag the end date's year
  (FY 2022 ends 2023-01-29, FY 2023 ends 2024-01-28, FY 2024 ends 2025-02-02, FY 2025 ends
  2026-02-01), "the fiscal year ended January 29, 2023 through the fiscal year ended February 2,
  2025" gives `[(2022, 2024)]`; without a calendar the same text gives `[(2023, 2025)]`; a date
  that ends no fiscal year in the calendar (more than 7 days from every annual end, or the end of
  a quarter only) drops the range. Property: for generated calendars with a label offset, a range
  between two annual end dates gives the two labels; a range whose date is 8 or more days from
  every end gives `[]`. Oracle: the fixture calendar rows and the question text, by hand.
- **C3**: `missing_range_years(question, resolved_years, calendar=())` fills only the middle of a
  stated range whose endpoints were both resolved (requirement 6): Union Pacific with `[2023,
  2025]` gives `[2024]`; Tesla and Meta with the December-31 calendar and `[2023, 2025]` give
  `[2024]`; the 52/53-week text with `[2022, 2024]` gives `[2023]`; "FY21 through FY25" with
  `[2021, 2023, 2025]` gives `[2022, 2024]`; "FY2019 through FY2021 and FY2023 through FY2025"
  with `[2019, 2021, 2023, 2025]` gives `[2020, 2024]` (not 2022); a range already complete gives
  `[]`. Property: the result is disjoint from `resolved_years`, lies inside a stated range whose
  endpoints are both resolved, and with `resolved_years` makes every such range complete. Oracle:
  the question text and the resolved years, by hand.
- **C4**: the repair adds nothing where it must not: a list ("FY23 and FY25", "FY23 vs FY25",
  "between revenue in FY23 and FY25") with `[2023, 2025]` gives `[]`; only one endpoint resolved
  ("FY23 through FY25" with `[2025]`, or with `[2023, 2024]`) gives `[]`; a range of 11 years
  ("fiscal 2018 through fiscal 2028" with `[2018, 2028]`) gives `[]` while one of 10 ("fiscal
  2018 through fiscal 2027" with `[2018, 2027]`) gives 2019 to 2026; a quarter range ("Q1 FY23
  through FY25") gives `[]`; a dated range whose date matches no calendar row gives `[]`.
  Property: replacing the range connector of any question with a list connector gives `[]`, and
  resolved years that leave out either endpoint give `[]`. Oracle: requirements 2, 3 and 6, by
  hand.

## Interface

New, pure (no I/O), in `src/retriever/xbrl/fiscal_date.py`:

- `stated_fiscal_year_ranges(question: str, calendar: Sequence[Mapping[str, Any]] = (), year_range: tuple[int, int] = (2018, 2035)) -> list[tuple[int, int]]`
- `missing_range_years(question: str, resolved_years: Sequence[int], calendar: Sequence[Mapping[str, Any]] = (), year_range: tuple[int, int] = (2018, 2035)) -> list[int]`

Read through, unchanged in signature: `match_period_end`, `mention_period_kind`,
`src.retriever.extract_year_util.MAX_RANGE_YEARS`. The wiring of requirement 7 changes
`IntentRouter.route` (or a new `EntityExtractor` method it calls) without changing a signature.

## Notes

- **Oracle.** Every expected value is the question text read by hand, with the fixture calendar
  rows for dated endpoints. The calendars are synthetic (a December-31 filer and a 52/53-week
  filer whose label is the year the fiscal year starts, the shape of the Home Depot rows in
  `test/spec/fiscal-year-ended-date/conftest.py`). The three evidence questions are quoted from
  the dev suite; no test reads `data/`, the database or the clock.
- **Why the question and not the mentions.** The two endpoint mentions alone cannot tell "FY23
  through FY25" from "FY23 and FY25"; only the question can. Decision (b) of
  `fiscal-year-range` kept the resolver to the router's mentions. This change keeps that rule for
  resolving and adds a separate, narrow completion step that reads the question for one thing:
  a stated range whose two endpoints the mentions already resolved. It never adds an endpoint,
  never a year outside a stated range, and never acts on a list.
- **Why both endpoints must already be resolved.** It ties the repair to what the router
  extracted: a range in an unrelated clause ("revenue for FY2025, a period that ran from 2023 to
  2025 under the plan") adds nothing unless both its endpoints are also resolved periods.
- **`between … and`** is a range, as in `fiscal-year-range` (52 of its 248 surveyed ranges), so
  "the change between FY23 and FY25" gains FY2024 in scope. A change or CAGR plan needs only the
  endpoints; one more year in scope is harmless there, and costs `html_rag` and
  `long_context_multi` one more filing. "the change between revenue in FY23 and FY25" (between
  not immediately before the endpoint) stays a list.
- **Quarter cue window.** Three words before the first endpoint catch "Q1 FY23 through FY25",
  "first quarter of fiscal 2023 to fiscal 2025" and "three months ended March 31, 2023 through
  …"; a quarter word further back is not read. A quarter question that slips past is still safe
  in practice: its endpoints resolve to quarter periods, which `resolved_years` (FY only, by
  requirement 7) leaves out.
- **Expected movement.** The `breakdown` results that lost a middle year because the router
  split the range (40 INCORRECT "missing FY…" results over the stored dev and validation runs,
  among them the three evidence cases) should become CORRECT when the planner looks up every
  year in scope; `compute` results should not move. The fill does not touch the 20 `breakdown`
  INCORRECT results whose range was one mention (the planner or the data dropped a year there).
- **Validation plan.** (1) A **$0 replay**: for every dev case whose question states a range,
  take the stored `intent_router` responses (`audit.query_event`, `llm_call` bodies) of the runs
  above, re-run `resolve_periods` plus the fill offline against read-only `xbrl_db`, and check
  that the resolved FY set equals the stated range's years for every split and every unsplit
  response, and that list questions are unchanged. (2) A **DeepSeek slice** of the range cases
  plus a seeded sample (`scripts/eval/build_slice_suite.py` → `run_suite.py --suite-path
  --llm-profile deepseek --max-cost-usd <maintainer's cap>` → `compare_runs.py` against the
  same-day control), with the new router prompt `v4`; one full run only at the end.
- **Decisions for the reviewer.** (a) Completion reads the question, not only the mentions (the
  change to decision (b) of `fiscal-year-range`). (b) Both endpoints must be resolved. (c)
  `between … and` is a range. (d) The three-word quarter window. (e) Split-year forms
  (`FY21-23`) are not repaired, although `extract_years_in_range` reads them as ranges inside one
  mention.
