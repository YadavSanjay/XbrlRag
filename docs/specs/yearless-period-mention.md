# yearless-period-mention — a period mention with no year in it never degrades a query whose other periods resolved

The IntentRouter LLM sometimes lifts a word that names no period as a `period_mentions` entry.
Dev case `int-b96e86dae3b5-02a0e6fc` (deepseek, run `eval-06ccd735ebad`): "What was the
year-over-year percentage change in MRK's Assets for fiscal 2024?" was extracted as
`period_mentions: ["fiscal 2024", "year-over-year"]`. "fiscal 2024" resolved to FY2024;
"year-over-year" carries no year, so `EntityExtractor._resolve_one_period` returned it ambiguous
with reason `could_not_resolve_year`. `BranchDispatcher._unresolved_reasons` treats any ambiguous
period as a resolution failure, so the whole query degraded to `NARRATIVE_BRANCH`
("ambiguous entity/period resolution - degrading to narrative: period mention
'year-over-year': could_not_resolve_year"). The narrative answer had no fact rows and refused
the percentage (INCORRECT). The paired run, whose router lifted only "fiscal 2024", went to
`PLAN_GENERATOR` and was answered.

The degradation contract stays as it is for real failures: an unresolved company, a mention
that names a year and does not resolve (an overlong range, a date matching no calendar row,
"last fiscal year" with no anchor, "Q3" with no year), or a query in which no period resolved.
This change only stops a mention that names no period at all from overriding periods that did
resolve.

## Requirement

Two pure functions in a new module `src/retriever/xbrl/period_evidence.py` (no I/O, no clock,
no logging; never raise on the inputs below).

### 1. Year evidence (`has_year_evidence`)

`has_year_evidence(mention)` returns the `bool` `True` when the mention carries **year
evidence**, else `False`. Let *tokens* be the maximal runs of the characters `a-z` and `0-9` in
`mention.lower()`. The mention carries year evidence when at least one of these holds:

1. **A digit:** some character `ch` of the mention has `ch.isdigit()` (covers "2024", "FY24",
   "'23", "Q3", "H1", "1st quarter"; the same test `_resolve_one_period` uses today).
2. **A relative word:** a token is one of `last`, `latest`, `recent`, `current`, `this`,
   `prior`, `previous`, `preceding`, `ago`, `past`, `trailing`, `next`, `earlier`, `ytd`,
   `today`, `now`.
3. **A sub-annual period word:** a token is one of `quarter`, `quarters`, `quarterly`, `half`,
   `halves`, `month`, `months`, `date` (as in "year to date").
4. **A number word:** a token is one of `one`, `two`, `three`, `four`, `five`, `six`, `seven`,
   `eight`, `nine`, `ten`, `eleven`, `twelve`, `first`, `second`, `third`, `fourth`.
5. **A month name:** a token is one of `january` ... `december`, or `jan`, `feb`, `mar`, `apr`,
   `may`, `jun`, `jul`, `aug`, `sep`, `sept`, `oct`, `nov`, `dec`.

Matching is on whole tokens: "nowhere", "maybe", "lasting", "decade" and "halfway" carry no
evidence. Everything else carries none — in particular "FY", "fiscal", "fiscal year", "the year
ended", "annual", "annual filing", "calendar year", "each year", "year-over-year", "YoY" and the
empty string.

The lists are deliberately generous: a word that might anchor a period counts as evidence, so
the rule can only fail towards today's behaviour (degrade), never towards masking a real
resolution failure.

### 2. Ignoring yearless failures (`partition_yearless_periods`)

`partition_yearless_periods(periods)` takes the `list[ResolvedPeriod]` that
`EntityExtractor.resolve_periods` returned and returns `(kept, ignored)`, two lists:

- A period is **ignorable** when `period.ambiguous` is true and
  `has_year_evidence(period.mention)` is false.
- When at least one period in the input has `ambiguous` false, `ignored` is the ignorable
  periods and `kept` is all the others.
- Otherwise (no period resolved, or an empty input), `kept` is every period and `ignored` is
  `[]`.
- Both lists keep the input order and hold the input's own objects (no copies, no new
  periods). The input list is not modified.

A resolved period is always kept, whatever its mention. A failed period with year evidence is
always kept, so it still degrades the query in the dispatcher.

### 3. Wiring (implementer; unlocked)

- `IntentRouter.route` applies `partition_yearless_periods` to the periods right after
  `resolve_periods`, and builds `IntentRouterResult.periods` (and so the fast path, the
  `intent_extraction` event and the dispatcher's decision) from `kept` only.
  `raw_extraction` keeps the LLM's original `period_mentions` untouched.
- For each ignored period it logs one trace event
  `log_event("decision", point="period_resolution", decision="ignore_yearless_mention",
  mention=<period.mention>, reason=<period.reason>)`.
- `BranchDispatcher` is unchanged: with the yearless mention gone it decides on the remaining
  periods as usual.
- Covered by implementer unit tests in `test/unit/` (a router fed a fake LLM extraction of
  `["fiscal 2024", "year-over-year"]` keeps one period, logs the event and dispatches to
  `PLAN_GENERATOR`; `["year-over-year"]` alone still degrades; `["fiscal 2024", "Q3"]` with Q3
  unresolved still degrades), and the `src/retriever/xbrl/CLAUDE.md` period bullet updated.

## Acceptance criteria

- **C1**: `has_year_evidence` point cases, each `True`/`False` exactly:
  - `True`: "2024", "fiscal 2024", "FY2023", "FY24", "'23", "Q3", "third quarter",
    "first half", "nine months", "year to date", "YTD", "last fiscal year", "most recent",
    "prior year", "the year ended December 31", "Sept", "May", "quarterly", "trailing twelve
    months", "LAST YEAR";
  - `False`: "", "FY", "fiscal", "fiscal year", "the year ended", "annual", "annual filing",
    "calendar year", "each year", "year-over-year", "YoY", "y/y", "full year", "nowhere",
    "maybe", "lasting", "decade", "halfway", "per annum".

  Properties: any string made only of non-evidence words and separators is `False`; inserting
  one evidence token (any of the five lists, any letter case) or one digit as its own token
  anywhere makes it `True`; an evidence word glued inside a longer letter run is not evidence;
  the result does not depend on letter case. Oracle: Requirement 1 applied by hand.
- **C2**: `partition_yearless_periods` point cases (synthetic `ResolvedPeriod`s):
  - the defect: `[fiscal 2024 → FY2024, "year-over-year" ambiguous]` → kept the first, ignored
    the second; likewise `[2023, 2025, "each year"]` and `[2023, "annual filing"]` (the other
    stored cases, see Notes);
  - nothing resolved: `["year-over-year" ambiguous]` → kept it, ignored `[]`;
    `["YoY" ambiguous, "2019 through 2032" ambiguous]` → both kept;
  - real failures stay: `[2024 resolved, "Q3" ambiguous]` and `[FY2023 resolved, "last fiscal
    year" ambiguous]` → both kept, ignored `[]`;
  - a resolved period with a yearless mention ("FY", fiscal_year 2024, not ambiguous) is kept;
  - several yearless failures next to one resolved period are all ignored, in order;
  - `[]` → `([], [])`.

  Properties over generated lists of periods: `kept` and `ignored` interleave back to the input
  exactly (order and identity); every ignored period is ambiguous with an evidence-free
  mention; `ignored` is non-empty only when some input period is not ambiguous; every
  non-ambiguous period is in `kept`; applying the function to `kept` ignores nothing more; the
  input is not mutated. Oracle: Requirement 2 applied by hand to a hand-labelled mention pool.

## Interface

- `src.retriever.xbrl.period_evidence.has_year_evidence(mention: str) -> bool`
- `src.retriever.xbrl.period_evidence.partition_yearless_periods(periods: list[ResolvedPeriod]) -> tuple[list[ResolvedPeriod], list[ResolvedPeriod]]`

## Notes

- **Oracle.** The rule of Requirements 1 and 2 applied by hand to synthetic mention text; the
  stored cases below only motivate it. No data file, database or LLM is read by the tests.
- **Evidence (read-only, `xbrl_db` audit + eval schemas, 2026-11-11).** Over every stored
  `xbrlrag_full` eval run, distinct queries with a `branch_dispatch` decision: deepseek 2,215,
  anthropic 900, openai 1,446, unlabelled (older runs) 6,698. Degraded to narrative: deepseek
  95, anthropic 43, unlabelled 56, openai 0. Of those, degraded for `could_not_resolve_year`:
  **4** (deepseek 3, anthropic 1) — and in all four another period of the same query had
  resolved and the failing mention carries no year evidence by Requirement 1:
  - `int-b96e86dae3b5-02a0e6fc` (deepseek `eval-06ccd735ebad`): "year-over-year" next to 2024,
    INCORRECT;
  - `int-cc0df0ddb03f-020a8bb9` (deepseek `eval-0051272815db`): "year-over-year" next to 2024,
    REFUSED;
  - `int-333d26b6c6ba-00f2edf5` (anthropic `eval-41b4ab7e7015`): "each year" next to 2023 and
    2025, INCORRECT;
  - `int-8e9430b9beb7-01e7e706` (deepseek `eval-b836ff5b0052`): "annual filing" next to 2023,
    PARTIAL.
  None of the other degradations involved `could_not_resolve_year`, so the rule touches nothing
  else in the stored runs.
- **Expected movement.** Small and one-directional: about 1 case in 700 queries on the dev
  profiles (0–1 case per dev run), each moving from a narrative refusal/partial to the plan
  path. The rule cannot move a query whose periods all failed, nor one whose failed mention
  names a year, so no currently-planned query changes branch. The LLM's extraction is
  stochastic, so a single dev run may show no change at all.
- **Validation plan.** Implementer: `pytest test/spec/yearless-period-mention`, the unit tests
  of Requirement 3, `pytest test/spec -n auto`. Then a $0 replay is not possible (the router
  call is an LLM call), so build a slice of the four cases above
  (`build_slice_suite.py`) and run it once per dev profile (deepseek, anthropic; about
  $0.05–0.10) and check each trace shows the `ignore_yearless_mention` decision and
  `PLAN_GENERATOR`; no full run for this change alone.
- **Out of scope.** Changing the router prompt so it stops lifting such words (a prompt change,
  reviewed route); resolving "last fiscal year" (today it matches no relative phrase and fails —
  it keeps degrading here, see C2); the company side of the degradation contract.
