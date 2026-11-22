# fiscal-year-ended-date — "the fiscal year ended <date>" is the fiscal year that ends on that date

The D3 failure analysis of the dev baseline (`data/eval/d3/failure_classes_2026-11-10.md`, class
C9, fix 5) found 6 of 6 dev cases wrong on each provider (F3a anthropic, F7a deepseek) when a
question names a fiscal year by its end date and the filer's label is not the end date's
calendar year. `EntityExtractor._resolve_one_period` reads a period mention with
`extract_years_in_range`, which takes the date's year digits, and never asks `fiscal_calendar`
which fiscal year ends on that date:

- `int-b1ea3c4b1d1e-00613ca4` "Interest Expense reported by HD for the fiscal year ended
  February 2, 2025": fy 2025, 2.412B, cited and confident; the key is fiscal 2024, 2.321B
  (silent-wrong on F7a).
- `int-b8fb63762e0d-00062de8` (op income share, "ended February 1, 2026") and
  `int-01252267680b-0326f40c` (intangibles CAGR "between the fiscal year ended January 28, 2024
  and the fiscal year ended February 1, 2026"): fy 2026 does not exist for HD, NOT_FOUND.
- `int-e71ca6667408-027caf2c` (point-in-time: "In the annual report for the fiscal year ended
  February 1, 2026, what did HD show as its EPS for the fiscal year ended January 28, 2024?"):
  FY2024 EPS 14.91 returned for the FY2023 15.11.

Home Depot is a 52/53-week filer that names a fiscal year by the year it starts (S2 of
[`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md): "fiscal 2024" is the year
the filer itself labels 2024). Since
[`fiscal-label-alignment`](fiscal-label-alignment.md) and
[`tenq-fiscal-labels`](tenq-fiscal-labels.md), `fiscal_calendar` carries those labels, so
`(HD, 2024, FY)` has `period_end` 2025-02-02. The mechanism is general: any filer whose label
differs from the calendar year of its period end. [`fiscal-year-range`](fiscal-year-range.md)
put exactly this out of scope ("mapping a year-end date to the filer's fiscal-year label").

This change adds a pure, deterministic resolver: a full written date in a period mention, for a
resolved filer, maps to the fiscal period whose `period_end` is that date (within a small
tolerance), using the filer's `fiscal_calendar` rows passed in. The database read stays in
`EntityExtractor`, at the I/O boundary. A mention without a full date ("fiscal 2025", "FY25",
"fiscal year 2023 through fiscal year 2025") is resolved exactly as today.

## Requirement

All functions are in a new module `src/retriever/xbrl/fiscal_date.py`, are pure (no I/O, no
clock, no logging needed) and never raise on any `str` mention.

### 1. Written dates (`find_written_dates`)

`find_written_dates(text)` returns every full date written in `text`, in order of appearance,
as `(value, start, end)` with `value` a `datetime.date` and `text[start:end]` the exact written
date. A *full date* is one of these forms, case-insensitively:

- **F1, month first:** `<Month> <D>, <YYYY>` or `<Month> <D> <YYYY>` (comma optional).
- **F2, day first:** `<D> <Month> <YYYY>` or `<D> <Month>, <YYYY>`.
- **F3, ISO:** `<YYYY>-<MM>-<DD>`, exactly four, two and two digits joined by hyphens.

where `<Month>` is a full English month name or one of the abbreviations `Jan Feb Mar Apr Jun
Jul Aug Sep Sept Oct Nov Dec` (`May` has none), an abbreviation optionally followed by `.`;
`<D>` is one or two digits, optionally followed by `st`, `nd`, `rd` or `th`; `<YYYY>` is four
digits from 1900 to 2099; the parts of F1 and F2 are separated by one or more whitespace
characters (the comma follows the part before it directly). The written date is the shortest
span that covers these parts (no surrounding whitespace or punctuation, the trailing `.` of a
sentence excluded). A date is neither preceded nor followed by a letter or digit.

These are **not** full dates, and leave the mention to today's reading: a day and month that
do not form a real calendar date (`February 30, 2025`, `2025-13-01`), numeric slash forms
(`02/02/2025`, `2/2/25`: month-first and day-first readings conflict), a two-digit year (`Feb
2, '25`), a month and year without a day (`January 2025`), and words (`the end of January
2025`).

### 2. Matching one date to the calendar (`match_period_end`)

`match_period_end(end, calendar, period, quarter=None, tolerance_days=7)` matches one date
against one filer's calendar rows. `calendar` is a sequence of mappings with at least
`fiscal_year` (int), `fiscal_period` (str) and `period_end` (`datetime.date`), as a
`RealDictCursor` returns `fiscal_calendar` rows; other keys are ignored, and a row whose
`fiscal_period` is not `FY`, `Q1`, `Q2`, `Q3` or `Q4` is ignored. Rows with the same
`(fiscal_year, fiscal_period, period_end)` count once; order does not matter.

The distance of a row is `abs((row.period_end - end).days)`; a row is a *candidate* when its
distance is at most `tolerance_days`. Within one kind of row (annual `FY`, or quarters
`Q1`-`Q4`), the candidates **pick** a row when there is exactly one, or when exactly one of
several has distance 0. Otherwise (two or more and not exactly one exact) they are a **tie**.

`period` is `"FY"`, `"quarter"` or `"any"`; `quarter` is `None` or a named quarter `"Q1"`..`"Q4"`
(used only with `"quarter"`). The result is a dict `{"status", "fiscal_year",
"fiscal_period", "matched_end", "reason"}`:

- `calendar` has no usable row: `status "no_calendar"`.
- `"FY"`: annual candidates pick a row → `matched`, its `fiscal_year`, `fiscal_period "FY"`.
  A tie → `ambiguous`. No annual candidate but a quarter candidate → `ambiguous` (the date ends
  a quarter, not a fiscal year). No candidate at all → `not_found`.
- `"quarter"`: quarter candidates pick a row → `matched` with that row's year and quarter,
  unless a named `quarter` differs from it → `ambiguous`. A quarter tie → `ambiguous`. No
  quarter candidate but annual candidates pick a row → `matched` with that fiscal year and
  `fiscal_period` the named `quarter`, or `"Q4"` when none is named (the quarter that ends on a
  fiscal year end is its fourth; "the first quarter of the fiscal year ended February 1, 2026"
  names that year). An annual tie → `ambiguous`. No candidate → `not_found`.
- `"any"`: as `"FY"` when there is any annual candidate; else as `"quarter"` with no named
  quarter, except that no candidate at all is `not_found`.

`matched_end` is the `period_end` of the row that matched. For every status other than
`matched`, `fiscal_year`, `fiscal_period` and `matched_end` are `None` and `reason` is a
non-empty string containing `end.isoformat()`; for `matched`, `reason` is `None`.

**The tolerance is 7 days, inclusive.** A 52/53-week year ends on a fixed weekday: the last
one of a month (up to 6 days before the month end) or the one nearest to it (up to 3 days
either side). A reader who writes the nominal month end ("January 31, 2025") for such a filer is
therefore at most 6 days off; 7 covers that. Period ends of one kind are at least 364 days
(annual) or about 84 days (a 12-week quarter) apart, so two rows of one kind are both within 7
days of a date only for a transition period or a data error, which is a tie, not a guess. On
`xbrl_db` (2026-11-10, read once by hand), all 150 full dates in dev questions with a
ticker match an annual row at distance 0, and the smallest gap between two period ends of one
filer is 90 days.

### 3. The kind of period a mention asks for (`mention_period_kind`)

`mention_period_kind(mention)` returns `(period, quarter)`. Lower-case the mention; a *cue*
below matches only as whole words (not preceded or followed by a letter or digit; `fy` also
matches before digits, as in `fy2025`):

- **Quarter cues:** `quarter`, `quarterly`, `three months`, `three-month`, `3 months`,
  `3-month`, `13 weeks`, `13-week`, `thirteen weeks`, `six months`, `six-month`, `6 months`,
  `nine months`, `nine-month`, `9 months`, `first half`, `year to date`, `year-to-date`, `ytd`,
  `q1`, `q2`, `q3`, `q4`. Any of them gives `"quarter"`.
- **Named quarter** (only with a quarter cue): `first quarter`, `1st quarter`, `q1` → `Q1`;
  `second quarter`, `2nd quarter`, `q2`, `first half`, `six months`, `six-month`, `6 months` →
  `Q2`; `third quarter`, `3rd quarter`, `q3`, `nine months`, `nine-month`, `9 months` → `Q3`;
  `fourth quarter`, `4th quarter`, `q4` → `Q4`. When cues name two different quarters, none is
  named (`None`).
- **Annual cues** (only without a quarter cue): `fiscal`, `fy`, `year ended`, `years ended`,
  `year ending`, `year end`, `year-end`, `twelve months`, `12 months`, `52 weeks`, `53 weeks`,
  `52-week`, `53-week`, `annual`, `10-k`. Any of them gives `("FY", None)`.
- Neither: `("any", None)` (for instance "as of February 2, 2025").

### 4. Resolving a dated mention (`resolve_dated_mention`)

`resolve_dated_mention(mention, calendar, tolerance_days=7, year_range=(2018, 2035))` returns
`None` when `find_written_dates(mention)` is empty: the caller then resolves the mention as
today. Otherwise it returns `{"status", "periods", "reason"}`, `periods` a list of
`(fiscal_year, fiscal_period)` tuples, ascending:

1. `calendar` has no usable row → `{"status": "no_calendar", "periods": [], "reason": <str>}`.
2. `(period, quarter) = mention_period_kind(mention)`. Each written date is matched with
   `match_period_end(date, calendar, period, quarter, tolerance_days)`.
3. **Any date not `matched` → `ambiguous`.** No fallback to the date's digits when the filer
   has a calendar.
4. **A quarter mention names one period.** With `period == "quarter"` and more than one written
   date → `ambiguous` (quarter ranges and lists are not resolved). With one date: `resolved`,
   `periods == [(fiscal_year, fiscal_period)]` of the match.
5. **An `"any"` mention whose one date matched a quarter** → `resolved`, that one quarter. With
   more than one date, any date matching a quarter → `ambiguous`.
6. **Otherwise every date matched a fiscal year, and the mention's years are read as today
   with each date's year replaced by its label.** Let `m'` be `mention` with every written date
   replaced by `"<Month> <D>, <L>"` (full English month name, day without leading zero or
   ordinal, `L` the matched `fiscal_year`). Then:
   - `overlong_year_ranges(m', year_range=list(year_range))` non-empty → `ambiguous` (as
     [`fiscal-year-range`](fiscal-year-range.md) requirement 3);
   - a matched `fiscal_year` outside `year_range` → `ambiguous` (`extract_years_in_range` would
     drop it and might fall back to its default year);
   - else `resolved` with `periods == [(y, "FY") for y in extract_years_in_range(m',
     year_range=list(year_range))]`.

   So ranges, lists and mixed mentions keep the [`fiscal-year-range`](fiscal-year-range.md)
   reading: "between the fiscal year ended January 28, 2024 and the fiscal year ended February
   1, 2026" for Home Depot is fiscal 2023, 2024 and 2025; "fiscal 2022 and the fiscal year ended
   February 1, 2026" is 2022 and 2025. Any written form of a date (F1, F2, F3) gives the same
   result as F1.
7. `reason` is `None` for `resolved`; for `ambiguous` it is a non-empty string that contains the
   ISO form of the first date that did not match (rule 3), or names the cause (rules 4-6).

For a filer whose labels equal the calendar year of their period ends (most filers), rule 6
gives exactly what `extract_years_in_range(mention)` gives today, for F1 dates that match.

### 5. Wiring (implementer; not locked)

`EntityExtractor._resolve_one_period`, in the branch that handles a mention with digits (before
`overlong_year_ranges` / `extract_years_in_range`):

1. If `find_written_dates(mention)` is non-empty and the company resolved with a `cik`, read
   that filer's rows once: `SELECT fiscal_year, fiscal_period, period_end FROM fiscal_calendar
   WHERE cik = %s` (parameterized, `RealDictCursor`), and call `resolve_dated_mention`.
2. `resolved` → for each `(fiscal_year, fiscal_period)`, `self._resolve_year(conn, mention,
   cleaned, company, fiscal_year, fiscal_period, ytd)` with `ytd` from `_detect_period`, so the
   date bounds are attached as today. The `"calendar"`-wording check in `_resolve_year` is
   unchanged.
3. `ambiguous` → one `ResolvedPeriod(mention=mention, ambiguous=True, reason=...)`, so
   `BranchDispatcher` degrades with the reason in view, as for any unresolved period.
4. `None`, `no_calendar`, or no resolved company / cik → today's path, unchanged. A cross-filer
   screen ("the fiscal year ended December 31, 2024" with no company) is not affected.
5. Log a `decision` trace event when a date's label differs from its digits
   (`{"mention", "date", "fiscal_year"}`), so the C9 cases are visible in the Trace Explorer.

The module carries comments for now; the implementer adds the module docstring.

**In scope:** requirements 1-4 (locked), 5 (wiring, checked on the slice). **Out of scope:**

- Re-reading the mention in `src/eval/systems/long_context_arms.py::filing_targets` and
  `src/retriever/html10k/scopes.py::_period_years` (both union `extract_years_in_range` of the
  mention with the resolved years, so a dated HD mention adds the digit year's filing too). See
  decision (d).
- The planner LLM writing the digit year into a step despite `FISCAL_YEARS_IN_SCOPE` (the
  prompt says "prefer these"). Measured on the slice; see decision (e).
- Quarter ranges and lists, numeric slash dates, "calendar year ended" wording, a date before
  or after every calendar row (decision (c)), cross-filer screens.

## Acceptance criteria

The calendars are synthetic unless named; the Home Depot rows are the three public period ends
and labels of [`fiscal-label-alignment`](fiscal-label-alignment.md) (fiscal 2023 ended
2024-01-28, fiscal 2024 ended 2025-02-02, fiscal 2025 ended 2026-02-01) and its first quarter of
fiscal 2025 ended 2025-05-04 ([`tenq-fiscal-labels`](tenq-fiscal-labels.md) C2).

- **C1**: `find_written_dates` returns exactly the dates and spans of requirement 1 for each
  form (F1 full and abbreviated month, with and without comma, `Sept.`, ordinals; F2; F3),
  alone or inside a sentence, several in order; and nothing for the non-forms listed. Property:
  any real date 1900-2099 written in any of the forms, between arbitrary words, is found once
  with its exact span and value. Oracle: the written text, by hand; `datetime.date` for the
  calendar validity of generated dates.
- **C2**: `match_period_end` with `period "FY"` maps each Home Depot end date to its label
  (2025-02-02 → 2024, 2024-01-28 → 2023, 2026-02-01 → 2025), a date up to 7 days either side
  to the same label, and 8 days off to `not_found`; a date-labelled filer's ends to their own
  year. Property: on generated 52/53-week and month-end calendars with label offset -1, 0 or +1,
  every annual end shifted by 0-7 days matches its own label and by 8-40 days is `not_found`.
  Oracle: Home Depot's 10-K cover dates and labels; the generated calendar's own labels.
- **C3**: the defined outcomes: an empty calendar is `no_calendar`; two annual rows within 7
  days tie unless exactly one is exact; a quarter end asked as `"FY"` is `ambiguous`; `"quarter"`
  matches the quarter row, a fiscal year end as `Q4` or as the named quarter, and a named
  quarter that differs from the matched row is `ambiguous`; `"any"` prefers the annual row;
  every non-match has `None` fields and a reason with the date. Property: shuffling and
  duplicating rows, and adding rows of other periods more than 7 days away, change nothing.
  Oracle: requirement 2, by hand.
- **C4**: `mention_period_kind` gives the kind and named quarter of requirement 3 for each cue
  family, quarter cues winning over annual ones, `("any", None)` without a cue. Property: adding
  an annual cue to a quarter mention leaves it a quarter; adding words without cues changes
  nothing. Oracle: requirement 3, by hand.
- **C5**: `resolve_dated_mention` resolves the four C9 dev mentions for the Home Depot calendar
  to fiscal 2024; 2025; 2023-2025; and 2023 and 2025; mixed mentions (a date with `fiscal 2022`)
  and quarter mentions; returns `None` for a mention without a full date. Properties: for a
  calendar whose labels equal the end year, the result equals `extract_years_in_range` of the
  mention (unchanged behaviour); with label offset k, a date-only range or list gives the digit
  years plus k; writing the dates in F2 or F3 instead of F1 gives the same result. Oracle: Home
  Depot's labels; the phrase, by hand; the metamorphic relations of requirement 4.6.
- **C6**: `resolve_dated_mention` returns `no_calendar` for an empty calendar, and `ambiguous`
  with a reason (the unmatched date's ISO form, or the cause) for: a date matching no row, a
  quarter mention with two dates, a range rewritten to more than 10 years, and a label outside
  `year_range`; never periods from the digits. Property: adding an unmatched date to any
  resolvable mention makes it `ambiguous`. Oracle: requirement 4.1-4.7, by hand.

## Interface

New module `src/retriever/xbrl/fiscal_date.py`:

- `find_written_dates(text: str) -> list[tuple[datetime.date, int, int]]`
- `match_period_end(end: datetime.date, calendar: Sequence[Mapping[str, Any]], period: str, quarter: str | None = None, tolerance_days: int = 7) -> dict[str, Any]`
- `mention_period_kind(mention: str) -> tuple[str, str | None]`
- `resolve_dated_mention(mention: str, calendar: Sequence[Mapping[str, Any]], tolerance_days: int = 7, year_range: tuple[int, int] = (2018, 2035)) -> dict[str, Any] | None`

Read through, unchanged: `src.retriever.extract_year_util.extract_years_in_range`,
`overlong_year_ranges`. Changed in behaviour, not signature (wiring, unlocked):
`EntityExtractor.resolve_periods`.

## Notes

- **Oracles.** Home Depot's period ends and labels are public (its 10-K covers; restated in
  fiscal-label-alignment). Every other calendar is synthetic, built in the tests' conftest:
  month-end years, "last Saturday of the month" and "Sunday nearest the month end" 52/53-week
  years, 13-week quarters, labels = calendar year of the end + offset. Expected values are the
  generated calendar's own labels or hand readings of the phrase. No test reads `data/`, the
  database or the clock.
- **Affected dev cases** (frozen dev suite, read 2026-11-10; dev only): 6 Home Depot cases with a
  full date, each failing on both providers: `int-b1ea3c4b1d1e-00613ca4` (lookup, key fy 2024),
  `int-b8fb63762e0d-00062de8` (ratio, 2025), `int-01252267680b-0326f40c` and
  `int-01252267680b-03700394` (CAGR, 2023→2025), `int-842f2c9e0a24-038aad18` (trend
  2023-2025; also C11, the `Debt` definition, so it may stay wrong), `int-e71ca6667408-027caf2c`
  (point-in-time, fy 2023 in the fiscal 2025 report). Of the 150 full dates in dev questions with
  a ticker, 140 are on filers whose label is the end year and resolve as today, and 10 (Home
  Depot) change. No dev question uses F2, F3 or a quarter date; those forms are specified so
  the resolver does not depend on the suite's wording.
- **Validation plan (dev only, after the impl PR's CI is green).** `$0` first: `pytest
  test/spec/fiscal-year-ended-date` and the existing `test/spec/fiscal-year-range` and
  `tenq-fiscal-labels` (retired tests stay green), plus a dry router replay of the 150 dated dev
  questions through `EntityExtractor.resolve_periods` against `xbrl_db` (no LLM): 140 unchanged,
  10 HD relabelled, none ambiguous. Then one DeepSeek dev slice (`build_slice_suite.py` with the
  6 C9 ids plus a seeded sample of about 30 dated non-HD cases as the no-regression control;
  `run_suite.py --llm-profile deepseek --suite-path ... --max-cost-usd 1.00` maintainer cap;
  `compare_runs.py` against F7a). Expected: the 5 C9 cases outside C11 CORRECT, no control case
  changes. Read the traces of the planner-path cases (ratio, CAGR, trend) for the step `fy`.
  Never on validation or held-out data.
- **Decisions for the maintainer** (recommendation first):
  - (a) Tolerance **7 days** (requirement 2). Exact-only would also pass every dev date, but it
    refuses a reader who writes the nominal month end for a 52/53-week filer; 7 is below half
    of any quarter gap, so it never chooses between two real periods.
  - (b) **No calendar rows for the cik → today's digits reading** (`no_calendar`). Such a filer
    has no ingested filing (`fiscal_calendar` is written for every registered filing), so any
    year returns NOT_FOUND anyway; a refusal would only change the error text. The alternative is
    `ambiguous`.
  - (c) **A date matching no row → `ambiguous`**, even before the first or after the last row
    (for instance a year not ingested). Alternative: extrapolate the label by whole years from
    the nearest annual row when the date is within 7 days of a multiple of 364/371 days; more
    reach, but a guess about filings we do not hold.
  - (d) Leave the long-context and html_rag mention re-reads alone (both are baselines;
    html_rag was dropped 2026-11-06). They gain the right year from the resolved periods and keep
    the digit year as an extra filing. A one-line change there (skip the re-read for a dated
    mention) is a follow-up if their numbers matter.
  - (e) No planner prompt change in this spec. If the slice shows the planner writing the digit
    year, the follow-up is a plan_validator rule (a step `fy` outside `FISCAL_YEARS_IN_SCOPE`
    and its adjacent years, for a dated mention, fails validation) — deterministic, its own spec.
  - (f) Slash dates stay unparsed (requirement 1); if wanted, `MM/DD/YYYY` read US-style only.
