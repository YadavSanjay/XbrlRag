# subquery-period-phrases — a lone "from <year>" is a base year, and a relative phrase followed by a year states that year

This change amends [dependent-subquery-period](dependent-subquery-period.md) (implemented, lock
retired), which adds a deterministic step after `QueryDecomposer.decompose`: a dependent
sub-query that states no period inherits the compound question's one annual fiscal year
(`src/retriever/xbrl/subquery_period.py`). A $0 trace of the dev compound run
`eval-18b83b48fb4d` found two defects in how that step reads period text.

**A. A lone "from <year>" is read as the target year.** For a compound question whose screen
part says FY25 and whose growth part says "from FY24", the decomposer wrote the dependent
sub-query "For <<TOP:sq1:1>>, what was the accumulated earnings growth from FY24?". The
sub-query writes a year, so `has_own_period` is true and it is left alone; the router resolves
`periods=[2024]` and the plan computes FY23-to-FY24 growth, while the question means FY24 to
FY25. The whole question states two years, so `stated_fiscal_year` is None by design
(dependent-subquery-period Requirement 2) and inheritance cannot help. The answer is a silent
wrong with real citations.

**B. A relative phrase directly followed by a year is read as relative.** In "… top 3 by
Assets, Current fiscal year 2024: …" (the metric "Assets, Current" next to "fiscal year 2024")
the normalised text contains `current fiscal year`, one of the relative phrases of
dependent-subquery-period Requirement 1.2, so `stated_fiscal_year` returns None and the
dependent sub-query does not inherit 2024 (the conservative miss named in that spec's Notes,
case `int-a16b37bcfb9a-0796df51`).

The maintainer decided both rules below. Everything else dependent-subquery-period specifies
stays as it is, except where Requirement 3 says otherwise.

## Requirement

All new functions are pure (no I/O, clock, logging or LLM), never raise on `str` input and
never modify their arguments. Throughout, *normalised* means lowercased with each run of
whitespace replaced by one space; a match *stands alone* when no letter or digit (`a-z`,
`0-9`) is immediately before or after it. A **year form** is one of, standing alone in
normalised text: `20dd` (a four-digit year 2000-2099), `fy20dd`, `fy 20dd`, `fydd`, `fy'dd`,
`fy’dd` (`dd` two digits, read as `20dd`).

### 1. A year-relative phrase followed by a year states that year (`states_period_phrase`)

`states_period_phrase(text)` returns `True` when at least one period phrase of
dependent-subquery-period Requirement 1.2 occurs in normalised `text` (standing alone; the
longest phrase wins where phrases overlap, so `current fiscal year` is one occurrence, not
`current year` inside it) **and is not excused**; else `False`.

An occurrence is **excused** when the phrase is one of the **year-relative phrases**
`most recent`, `latest`, `this year`, `current year`, `current fiscal year`, and it is
immediately followed by one space, then optionally `fiscal year ` or `fiscal ` or `year `,
then a year form. So "Assets, Current fiscal year 2024", "the latest fiscal year 2023", "the
most recent fiscal 2024", "this year FY25" and "current year 2024" state no period phrase;
they state the year (which `extract_years_in_range` reads as before).

Not excused, so still `True`: a year-relative phrase followed by anything but a year form
("the latest fiscal year, 2024", "the current fiscal year ended June 30, 2024", "the latest
fiscal year"); every other phrase even when a year follows (`current quarter`,
`current fiscal quarter`, `current period`, the prior, quarter and cumulative phrases:
"current quarter 2024", "prior year 2023", "Q3 2024"); and a text with one excused occurrence
and another phrase ("current fiscal year 2024 versus the prior year"). "this fiscal year" is
not a phrase of Requirement 1.2 and stays none.

### 2. A lone "from <year>" is a base year

#### 2.1 `from_base_year(text)`

Let *body* be `text` with every `<<TOP:<id>:<n>>>` token (`query_decomposer.TOP_TOKEN_RE`)
removed. A **from-phrase** is, in normalised *body*, the word `from` standing alone (no letter
or digit before it), one space, optionally `fiscal year ` or `fiscal `, then a year form
standing alone. Forms: "from FY24", "from FY2024", "from FY 2024", "from FY'24", "from fiscal
2024", "from fiscal year 2024", "from 2024".

`from_base_year(text)` returns the `int` year `B` when all hold, else `None`:

1. `extract_years_in_range(body)` (default bounds) is exactly `[B]`;
2. `2018 <= B <= 2035` (the router's period bounds, as dependent-subquery-period R2.2);
3. a from-phrase occurs in normalised *body* whose year is `B`, and the text after the first
   such from-phrase, with leading spaces removed, does not start with the whole word `to`,
   `through`, `thru`, `until` or `till`, nor with `-`, `–`, `->` or `→` (so "from FY24 to
   date" states an end and gives None);
4. `states_period_phrase(body)` is `False` (no quarter, relative, prior or cumulative period:
   "from Q3 FY24" gives None);
5. `fiscal_date.find_written_dates(body)` is empty;
6. **a growth or change word occurs in *body*** (standing alone in normalised *body*, so any
   case): `grow`, `grows`, `grew`, `grown`, `growing`, `growth`, `change`, `changes`,
   `changed`, `increase`, `increases`, `increased`, `decrease`, `decreases`, `decreased`,
   `rise`, `rises`, `rose`, `risen`, `fall`, `falls`, `fell`, `fallen`, `decline`, `declines`,
   `declined`, `cagr`. Without one, "from FY24" is a point in time, not a base ("For
   <<TOP:sq1:1>>, what was revenue from FY24?" is a lookup and must not become a comparison),
   so the sub-query is left as today. The list is the maintainer's growth/change wording with
   its regular inflections; a word outside it ("evolve", "trend", "move") errs towards leaving
   the sub-query unchanged.

So "from FY24 to FY25", "from FY24 through FY25", "between FY24 and FY25", "FY24-25" (two
years), "in FY24" (no "from"), "from the prior year" (no year), "from FY2040" (out of bounds),
"from the fiscal year ended June 30, 2024" (a date), "from 24" (not a year form) and "what
was revenue from FY24?" (no growth or change word) give None.

#### 2.2 `from_end_year(query, base_year)`

Let *later* be the distinct years of `extract_years_in_range(query)` (the whole compound
question, default bounds) greater than `base_year`. `from_end_year` returns the one element of
*later* when *later* has exactly one element, else `base_year + 1`. Years equal to or below
the base never count ("FY23 … from FY21" with base 2021 gives 2023; a question stating FY25
and FY26 with base 2024 gives 2025; a question stating only the base gives base + 1).

#### 2.3 `with_from_end_year(query, text)`

When `from_base_year(text)` is `None`, returns `text` unchanged. Otherwise let `B` be that
year and `E = from_end_year(query, B)`. Find the first from-phrase in `text` itself (matched
case-insensitively, each single space of the pattern matching one or more whitespace
characters). The result is `text` with the part of that match after `from` and its following
whitespace (the optional `fiscal year`/`fiscal` and the year form) replaced by
`fiscal year B to fiscal year E`; the word `from` as written, the whitespace after it and every
other character are kept. Example: "For <<TOP:sq1:1>>, what was the accumulated earnings growth
from FY24?" in a question stating FY25 becomes "For <<TOP:sq1:1>>, what was the accumulated
earnings growth from fiscal year 2024 to fiscal year 2025?".

The result states both years in the range form `extract_years_in_range` and the router already
read (fiscal-year-range): its years run from `B` to `E`, so `from_base_year` of the result is
`None` and rewriting it again changes nothing.

### 3. Wiring (locked through C2, C6 and C7)

1. **`stated_fiscal_year(query)`** uses `states_period_phrase` for its condition 3 (no period
   phrase) instead of the plain phrase check; its other conditions are unchanged. So the
   defect-B question gives 2024.
2. **`inherit_dependent_periods(query, subqueries)`**:
   - condition 4's "states exactly `Y`" check of a dependency uses `states_period_phrase` for
     "no period phrase occurs in it" (so a screen "… by Assets, Current fiscal year 2024"
     states exactly 2024 and does not block);
   - **new:** a dependent sub-query `s` (dependent as dependent-subquery-period R3 defines it)
     with `from_base_year(s.query)` not `None` is returned with text
     `with_from_end_year(query, s.query)`, whatever `stated_fiscal_year(query)` is and
     whatever its dependencies state. This amends dependent-subquery-period R3 ("a sub-query
     that states a period of its own is never changed") for this one case only. Its returned
     text is what later sub-queries of the walk see as their dependency.
   - Everything else is unchanged: a non-dependent sub-query is never changed (even with a
     lone "from FY24"), "from FY24 to FY25", "between", ranges, quarter forms and every other
     sub-query with a period of its own are returned as given, ids, order and `depends_on` are
     kept, and applying the function to its own output changes nothing.
3. **`QueryDecomposer.decompose`**: for each sub-query whose text changed by the new rule of
   3.2 it logs exactly one event `log_event("decision", point="query_decomposer",
   decision="from_end_year", subquery=<id>, base_year=<B>, end_year=<E>)` and no
   `inherit_period` event; a sub-query that inherited `Y` logs `inherit_period` exactly as
   before.
4. `has_own_period` is unchanged (any written year already counts as the sub-query's own
   period). The phrase list `PERIOD_PHRASES` is unchanged.

### 4. Unlocked, implementer

- Update the period bullet of `src/retriever/xbrl/CLAUDE.md` and the decomposer section of
  `docs/guides/xbrl-flow/06-retrieval-pipeline.md` with the two rules and the `from_end_year`
  decision.
- The validation plan in Notes.

## Acceptance criteria

All values are exact `bool`, `int`, `None` or strings; there are no floats.

- **C1** (`states_period_phrase`): "by Assets, Current fiscal year 2024", "the latest fiscal
  year 2023", "the most recent fiscal 2024", "this year FY25", "current year 2024",
  "current fiscal year FY'24", "Current Ratio" and "" give False; "the current fiscal year",
  "the latest fiscal year", "the latest fiscal year, 2024", "the current fiscal year ended
  June 30, 2024", "current quarter 2024", "prior year 2023", "Q3 2024" and "current fiscal
  year 2024 versus the prior year" give True. Property: each Requirement 1.2 phrase in a
  carrier sentence gives True; metamorphic: following a year-relative phrase by one space, an
  optional connector and any year form gives False, following any other phrase by a year still
  gives True. Oracle: Requirement 1, by hand.
- **C2** (`stated_fiscal_year`, wired): the defect-B question gives 2024; "… for the latest
  fiscal year 2023, …" gives 2023; "… in the latest fiscal year, …", "… current fiscal year
  2024 versus the prior year …", "… current quarter 2024 …" and "… Current fiscal year 2024 …
  from FY23" give None. Property: a template stating one year Y (2018-2035) as a year-relative
  phrase followed by any year form gives Y; metamorphic: appending a non-excused phrase or a
  second distinct year gives None. Oracle: Requirement 1 and 3.1, by hand.
- **C3** (`from_base_year`): "For <<TOP:sq1:1>>, what was the accumulated earnings growth from
  FY24?" gives 2024, as do "from fiscal 2024", "from fiscal year 2024", "from 2024", "From
  FY2024", "from FY 2024"; "from FY'23" gives 2023; "from FY24 to FY25", "from FY24 through
  FY25", "between FY24 and FY25", "from FY24 to date", "from Q3 FY24", "from FY24 to the
  latest fiscal year", "in FY24", "from the prior year", "from FY2040", "from the fiscal year
  ended June 30, 2024", "from 24", "from <<TOP:fy2024:1>>" and the lookups "For
  <<TOP:sq1:1>>, what was revenue from FY24?" / "… the total debt from fiscal 2024?" (no
  growth or change word) give None. Property: a template ending in a from-phrase of any form
  for B in 2018-2035 gives B; metamorphic: appending an end ("to FY…", "through fiscal …",
  "-…", "to date") or a quarter, or replacing "from" by "in", gives None; with any listed
  growth or change word (any case) it gives B, and replacing that word by a neutral one
  ("level", "amount", "value") gives None. Oracle: Requirement 2.1, by hand.
- **C4** (`from_end_year`): the defect-A question with base 2024 gives 2025; a question stating
  only FY24 with base 2024 gives 2025; "FY23 … from FY21" with base 2021 gives 2023; a question
  stating FY25 and FY26 with base 2024 gives 2025; a question stating only FY23 with base 2024
  gives 2025. Property: for B < E, a question stating B and E (and any years below B) gives E;
  with two distinct later years, or none, it gives B + 1. Oracle: Requirement 2.2, by hand.
- **C5** (`with_from_end_year`): the defect-A sub-query becomes exactly "For <<TOP:sq1:1>>,
  what was the accumulated earnings growth from fiscal year 2024 to fiscal year 2025?"; "From
  fiscal 2023, how did revenue change for <<TOP:sq1:1>>?" in a question stating FY24 becomes
  "From fiscal year 2023 to fiscal year 2024, how did revenue change for <<TOP:sq1:1>>?";
  a text whose `from_base_year` is None is returned unchanged. Property: for any form, B and E,
  the result is the text with exactly the year part replaced, `extract_years_in_range` of it
  runs from B to E, its `from_base_year` is None and rewriting it again changes nothing.
  Oracle: Requirement 2.3, by hand.
- **C6** (`inherit_dependent_periods`, wired): the defect-A decomposition returns `sq1`
  unchanged and `sq2` exactly as in C5; the defect-B decomposition returns `sq2` with
  " for fiscal year 2024" appended; a non-dependent "from FY24" sub-query, a dependent lookup
  "For <<TOP:sq1:1>>, what was revenue from FY24?" (no growth word), a dependent "from
  FY24 to FY25", "between FY24 and FY25" or "from Q3 FY24" sub-query are unchanged; a later
  sub-query depending on a rewritten one is not given a year. Property: for generated
  decompositions mixing lone-from dependents, own-period and yearless sub-queries, (a) every
  dependent lone-from sub-query gets exactly `with_from_end_year`, (b) every other sub-query
  behaves as dependent-subquery-period R3, (c) ids, order and `depends_on` are kept and the
  input is not modified, (d) applying the function to its output changes nothing. Oracle:
  Requirement 3.2, by hand.
- **C7** (wiring through `QueryDecomposer.decompose`, LLM faked at the provider adapter behind
  the real `LLMGateway`): the defect-A reply returns `sq2` rewritten and the trace holds
  exactly one `decision` event `from_end_year` with `subquery="sq2"`, `base_year=2024`,
  `end_year=2025` and no `inherit_period` event; the defect-B reply returns `sq2` inherited
  with one `inherit_period` event, `fiscal_year=2024`, and no `from_end_year` event. Property:
  for any B in 2018-2034 and any later screen year E, a two-year question and a reply whose
  dependent sub-query says "from FY<B>" give that sub-query the range B-E and one
  `from_end_year` event. Oracle: Requirements 2-3, by hand.

## Interface

- `src.retriever.xbrl.subquery_period.states_period_phrase(text: str) -> bool`
- `src.retriever.xbrl.subquery_period.from_base_year(text: str) -> int | None`
- `src.retriever.xbrl.subquery_period.from_end_year(query: str, base_year: int) -> int`
- `src.retriever.xbrl.subquery_period.with_from_end_year(query: str, text: str) -> str`

Wired by the implementer into the existing `stated_fiscal_year`,
`inherit_dependent_periods` (and its dependency check) and `QueryDecomposer.decompose`
(Requirement 3), whose signatures do not change.

## Notes

- **Oracle.** The question and sub-query texts themselves: Requirements 1-3 applied by hand to
  synthetic phrasings, expected years written in each test. No database, data file, eval
  suite or real LLM is read by the tests; the decomposer replies are hand-built JSON. The two
  defect phrasings are paraphrased synthetically from the trace description, not copied from
  any suite.
- **Retired tests.** `test/spec/dependent-subquery-period/` stays green under this change: none
  of its sub-queries has a lone "from <year>", and none of its texts puts a year-relative
  phrase directly before a year (its "Assets, Current {p}" template has no "fiscal year"
  between, and "current" alone is not a phrase).
- **A wide base-to-end gap expands.** "from fiscal year 2021 to fiscal year 2024" is a range,
  which `extract_years_in_range` expands to 2021-2024, exactly as if the user had written that
  range; the router and planner then treat it as they treat the user's own range.
- **Known limit, accepted by the maintainer.** When the question states only the base year
  (the decomposer copied the screen's year as "from FY24"), the end is base + 1, so a question
  meaning FY23-to-FY24 growth would plan FY24-to-FY25. No stored decomposition shows this;
  it is the rule as decided.
- **Validation plan (implementer).** `pytest test/spec/subquery-period-phrases`, then
  `pytest test/spec -n auto` (the retired dependent-subquery-period tests must stay green); a
  $0 dry pass of `inherit_dependent_periods` over the stored decomposer replies, expecting
  only the defect-A sub-query to change by the new rule; then one slice of the dev compound
  cases (about $0.10) checking the trace shows `from_end_year` and a two-period plan. No full
  run for this change alone.
