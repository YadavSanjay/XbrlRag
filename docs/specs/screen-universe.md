# screen-universe — a screen ranks the universe the question names, and says what it ranked

The dev baseline (`eval-9a99afc97d4b`, `eval-0051272815db`, 2026-11-04) scored 0 of 135 screen
cases. Two system gaps against the evaluation semantics
([`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md) S2, S3) account for 114 of
them:

- **The named universe is ignored (75 cases).** "Among the semiconductor companies" ranks every
  covered filer: the plan's `screen` step has no field for a universe, and the groups file
  ([`sector-tags`](sector-tags.md), `configs/eval/v2/groups.yaml`) is read only by the suite
  builder. Example: top 3 by FY2025 net income among semiconductors returned GOOGL, AAPL, MSFT.
- **The answer never discloses (39 cases with exactly the right rows).** S3 requires the answer
  to state the universe it ranked and to name each filer in it that it could not rank, and why.
  S2 requires each ranked filer's period end when the year-ends are more than three months
  apart (132 of the 135 cases). The screen's rows reach the synthesizer without `period_end`,
  only filers with an older year are listed as excluded, and nothing is stated.

This change adds a universe to the screen, a structured disclosure on its result, and a
deterministic disclosure sentence appended to the answer. The disclosure is built from the
screen's data, not written by the LLM, so it cannot be dropped or misquoted.

## Requirement

1. **A screen step may name its universe.** The `screen` step gets an optional `universe`:
   `"covered"` (every corpus filer; also the meaning of an absent or null `universe`) or a group
   id of `configs/eval/v2/groups.yaml` (`load_universe_groups()["groups"]`). `PlanValidator`
   rejects any other value, including a non-string, with an error that names the step's index
   and id, the word `universe` and the value, so the planner retries.
2. **The universe's members.** The corpus roster is the filers in `entity_master` (CIK,
   ticker). For `covered` the members are the roster's tickers; for a group, the group's
   tickers, whether or not the corpus holds them. `screen()` ranks only rows whose ticker is a
   member. Ranks run 1..n inside the universe; `top_n` and `min_threshold` apply after the
   restriction, exactly as before.
3. **Every member is ranked or excluded, with a reason.** A member that is not ranked has
   exactly one exclusion `{"ticker", "reason", ...}`:
   - `not_in_corpus`: a group member the roster does not hold;
   - `period_not_filed`, with `latest_fiscal_year` (int or None): the screen's existing
     fiscal-year exclusion ([`screen-exact-fiscal-year`](screen-exact-fiscal-year.md));
   - `component_only`: the flat screen's component-only filer
     ([`screen-concept-rank`](screen-concept-rank.md));
   - `metric_not_tagged`: a roster member with no row and no other exclusion.

   A reason found by an earlier filter wins over `metric_not_tagged`. A filer cut by `top_n` or
   `min_threshold` is ranked, not excluded. Exclusions of filers outside the universe are
   dropped. Other changes may add reasons (`currency`,
   [`screen-reporting-currency`](screen-reporting-currency.md)); they arrive as given
   exclusions with their own keys, which are kept.
4. **The stated period end.** `mv_financial_facts.period_end` holds Arelle's next-midnight date,
   one day after the period's last day (MSFT fiscal 2022 is stored as 2022-07-01; SEC states
   2022-06-30). The disclosure and the rows state the last day: the stored date minus one day.
5. **The screen result carries a disclosure.** `ScreenResult.to_dict()["disclosure"]` is:

   ```python
   {
       "universe": "semiconductors",      # "covered" or the group id
       "fiscal_year": 2023,
       "members": 8,                      # number of universe members, all of them
       "ranked": ["AVGO", "QCOM", ...],   # every ranked member in rank order, before top_n
       "exclusions": [...],               # Requirement 3, sorted by ticker
       "period_ends": {"AVGO": "2023-10-29", ...},  # stated ends of ranked filers that have one
       "period_ends_differ": True,        # latest minus earliest stated end > 92 days
   }
   ```

   It is present for an OK and a NOT_FOUND result. Each flat-screen row carries `period_end`,
   the stated end as an ISO string (None when the view gives none). The existing `excluded`
   key keeps its shape (graduated tests pin it); for a group universe it lists members only,
   and for `covered` it is unchanged.
6. **An unknown universe is an error, not a silent full ranking.** `screen()` with a `universe`
   that is neither `covered` nor a group id returns status ERROR, no rows, and an `error` naming
   the value.
7. **The disclosure sentence.** `screen_disclosure_text(disclosure)` returns, exactly:

   ```
   Universe: {label} ({m} {companies}; {r} ranked for FY{fy}).[ Not ranked: {e1}; {e2}; ....][ Fiscal years end on different dates: {t1} {end1}, {t2} {end2}, ....]
   ```

   - `{label}` is `the covered companies` for `covered`, otherwise `the {id} group` with each
     `_` in the id written as a space (`the big tech group`);
   - `{m}` is `members` and `{r}` the number of ranked tickers, as numerals; `{companies}` is
     `company` when `m` is 1, otherwise `companies`;
   - the "Not ranked" sentence is present only when there are exclusions. It lists each one as
     `{ticker} ({phrase})`, sorted by ticker, joined by `; `. Phrases:
     - `period_not_filed`: `no FY{fy} figure; latest FY{latest}`, or `no FY{fy} figure` when
       the latest year is None;
     - `metric_not_tagged`: `metric not reported`;
     - `component_only`: `reports only components of the metric`;
     - `not_in_corpus`: `not in the corpus`;
     - `currency`: `reports in {currency}, not USD`, or `does not report in USD` without a
       `currency` key;
     - any other reason: the reason with each `_` written as a space;
   - the period-end sentence is present only when `period_ends_differ` is true. It lists every
     ranked ticker that has a stated end, in `ranked` order, as `{ticker} {YYYY-MM-DD}`, joined
     by `, `;
   - sentences are separated by one space; there is no trailing space.
8. **The answer states the disclosure.** `append_screen_disclosures(summary, steps)` takes the
   answer summary and the plan's step results as `StepResult.to_dict()` dicts. For each step with
   `op == "screen"` and a dict `disclosure`, in step order, it appends that step's sentence,
   separated from the text before it by one space. A sentence already contained in the summary
   (or already appended) is not added again. An empty summary gets the sentences alone, with no
   leading space; with no screen disclosure the summary is returned unchanged.

## Acceptance criteria

- **C1**: `stated_period_end(stored)` returns the ISO date one day before `stored`, for a
  `datetime.date` or an ISO `YYYY-MM-DD` string; None for None. Point cases: 2022-07-01 →
  2022-06-30 (MSFT FY2022), 2023-10-01 → 2023-09-30 (AAPL FY2023), 2023-01-30 → 2023-01-29
  (NVDA FY2023), 2023-10-30 → 2023-10-29 (AVGO FY2023), 2023-01-01 → 2022-12-31 (AMZN FY2022),
  2024-03-01 → 2024-02-29 (leap year). Property: for any date, the result plus one day is the
  input, and a date and its ISO string give the same result. Oracle: SEC companyfacts `end`
  dates for those filings (Notes); the calendar.
- **C2**: `resolve_universe_members(universe, roster, groups)` returns the sorted, de-duplicated
  roster tickers for `covered` and the sorted group tickers for a group id (members outside the
  roster included). It raises `ValueError` naming the value for any other id. Property: for
  `covered` the result equals `sorted(set(roster))`; for a group it does not depend on the
  roster. Oracle: Requirement 2.
- **C3**: `build_screen_disclosure(universe, fiscal_year, members, roster, ranked_rows,
  exclusions)` returns the Requirement 5 dict, exactly: `ranked` is the tickers of
  `ranked_rows` in the given order; `exclusions` follows Requirement 3 (given exclusions of
  members kept with all their keys, `not_in_corpus` for members outside `roster`,
  `metric_not_tagged` for the other unranked members, sorted by ticker); `period_ends` maps each
  ranked row with a non-None `period_end` to `stated_period_end` of it; `period_ends_differ` is
  whether the latest minus the earliest stated end exceeds 92 days (false with fewer than two).
  Point case: the semiconductors FY2023 example in Notes. Properties: ranked and excluded
  tickers partition the members; the result does not depend on the order of `exclusions`;
  `period_ends_differ` agrees with the 92-day rule computed in the test. Oracle: Requirements 3–5.
- **C4**: `screen_disclosure_text(disclosure)` returns exactly the Requirement 7 sentence. Point
  cases: the semiconductors example; a `covered` disclosure with no exclusions and ends within
  92 days (one sentence); a one-member group (`company`); each reason phrase, including
  `currency` with and without a code and an unknown reason. Properties: the text contains every
  excluded ticker as a whole word, `m` and `r` as numerals, and each listed end when
  `period_ends_differ`; it contains no "Fiscal years end" sentence otherwise; it does not depend
  on the order of `exclusions`. Oracle: Requirement 7; S2, S3.
- **C5**: `append_screen_disclosures(summary, steps)` follows Requirement 8. Point cases: one
  screen step; two screen steps in order; a lookup step only (unchanged); an empty summary; a
  screen step whose `disclosure` is missing or None (unchanged). Properties: applying it twice
  equals applying it once; the result starts with the summary (stripped of trailing whitespace
  when something is appended). Oracle: Requirement 8.
- **C6**: `XbrlFactstoreScreen.screen(step)`, over a fake database (the I/O boundary), ranks
  only the universe's members and returns the Requirement 5 disclosure:
  - a `semiconductors` net-income screen ranks only the members, in value order, with ranks
    1..n, and lists the roster member with no row as `metric_not_tagged` and the member with
    only an older year as `period_not_filed`;
  - each flat row's `period_end` is the stated end;
  - an absent, a null and a `"covered"` universe give the same result;
  - an unknown universe gives ERROR, no rows, and an error naming it;
  - a NOT_FOUND result still carries the disclosure.

  Properties, for any store and any universe among `covered` and the committed group ids: rows
  are members only; ranks are 1..len(rows); ranked and excluded tickers partition the members;
  `top_n` keeps the first `top_n` of `ranked`; the existing `excluded` key holds the same
  entries as without a universe, restricted to members (so for `covered` it is unchanged).
  Oracle: Requirements 2–6 applied by hand to the fake store.
- **C7**: `PlanValidator().validate(plan)` gives no `universe` error for a screen step whose
  `universe` is absent, null, `"covered"` or a committed group id, and exactly one error that
  contains `universe` and the value for an unknown string or a non-string. Property: for any
  string not in the valid set, that error appears. Oracle: Requirement 1.

## Interface

- `src.retriever.xbrl.screen_disclosure.stated_period_end(stored: dt.date | str | None) -> str | None`
- `src.retriever.xbrl.screen_disclosure.resolve_universe_members(universe: str, roster: Sequence[str], groups: Mapping[str, Sequence[str]]) -> list[str]`
- `src.retriever.xbrl.screen_disclosure.build_screen_disclosure(universe: str, fiscal_year: int, members: Sequence[str], roster: Sequence[str], ranked_rows: Sequence[Mapping[str, Any]], exclusions: Sequence[Mapping[str, Any]]) -> dict[str, Any]`
- `src.retriever.xbrl.screen_disclosure.screen_disclosure_text(disclosure: Mapping[str, Any]) -> str`
- `src.retriever.xbrl.screen_disclosure.append_screen_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str`
- Changed behaviour, no new signature: `XbrlFactstoreScreen.screen` (the step's `universe`, the
  `disclosure` key of `to_dict()`, `period_end` on flat rows) and `PlanValidator.validate`
  (the `universe` field).

## Notes

- **Oracle for C1:** SEC companyfacts `end` dates of the annual filings: MSFT FY2022
  2022-06-30, AAPL FY2023 2023-09-30, NVDA FY2023 2023-01-29, AVGO FY2023 2023-10-29, AMZN
  FY2022 2022-12-31. The same dates are the answer keys' `period_end` in the frozen v2 suite.
  The eval side already converts the same way (`src/eval/suites/v2_sources.py` `_xbrl_end`).
- **Semiconductors FY2023 example (C3, C4; synthetic values shaped like the corpus).**
  Members AMD, AVGO, INTC, MU, NVDA, QCOM, TSM, TXN; roster the 33 corpus filers; ranked rows
  AVGO (stored end 2023-10-30), QCOM (2023-09-25), TXN (2024-01-01), AMD (2023-12-31), MU
  (2023-09-01); given exclusions INTC `period_not_filed` latest 2022, NVDA `metric_not_tagged`,
  TSM `currency` TWD, and ORCL `period_not_filed` (not a member, dropped). The stated ends span
  2023-08-31 to 2023-12-31, more than 92 days. Expected text:

  ```
  Universe: the semiconductors group (8 companies; 5 ranked for FY2023). Not ranked: INTC (no FY2023 figure; latest FY2022); NVDA (metric not reported); TSM (reports in TWD, not USD). Fiscal years end on different dates: AVGO 2023-10-29, QCOM 2023-09-24, TXN 2023-12-31, AMD 2023-12-30, MU 2023-08-31.
  ```

- **Why deterministic text:** the grader checks the answer text for the universe, each excluded
  ticker and each period end ([`v2-grading`](v2-grading.md), `disclosure_checks`). With 30+
  ranked filers, 30 dates would rely on the LLM copying them all; built from the screen data,
  the sentence is exact. The verifier never judges it (it is appended after verification).
- **Implementer work not locked here:**
  - `StepResult.disclosure` (default None), emitted by `to_dict()` when set, and
    `XbrlRetrievalPipeline._screen_step` copying `ScreenResult`'s disclosure into it;
  - the pipeline appending `append_screen_disclosures` to each (sub-)answer's `answer_summary`
    after the faithfulness verifier and its caveats;
  - the plan generator prompt (a new version under `prompts/templates/xbrl/`) listing the valid
    `universe` values and saying when to set one; reviewed on a screen slice, not locked;
  - the roster query. The graduated screen fakes (`test/spec/screen-exact-fiscal-year`) answer
    every SQL with per-filer rows carrying `ticker` and `cik`, filtered by `params["fy"]`. To
    keep them passing, select the roster as `SELECT cik, trading_symbol AS ticker FROM
    entity_master ...` and pass `fy` among its parameters (an unused named parameter is
    harmless in psycopg2). The new spec tests' fake answers any SQL that mentions
    `entity_master` with the roster.
- **Out of scope:** period ends for the geographic, segment and derived views (their views have
  no `period_end`; they report None and no period-end sentence); a peer-relative subject outside
  its universe; calendar-year wording (S2); the currency exclusion
  ([`screen-reporting-currency`](screen-reporting-currency.md)).
- **Expected effect** (measured on a screen slice before any full run): the 75 named-universe
  cases need the planner to set `universe`; the 39 PARTIAL cases need only the disclosure.
