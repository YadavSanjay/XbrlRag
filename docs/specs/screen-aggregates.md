# screen-aggregates — a total or median over a named group is a screen plus a computed aggregate

The D3 failure analysis of the dev baseline (`data/eval/d3/failure_classes_2026-11-10.md`,
classes C1 and C2) found that every question asking for a total or a median over a named
group fails: 45 of 45 dev cases on each provider (F3a/F7a). Examples: "combined dividends of
the big tech companies" (`int-014d29a97aa6-041912ee`), "median investing cash flow, big tech"
(`int-6fe14a201c46-02a7fe7d`), "combined operating income of the covered companies"
(`int-c1e691533098-0fc7ba04`), "median other income across the retailers"
(`int-a8e0f84b90f9-0bc7bca1`). Two defects:

- **C1, routing (23 A / 29 D).** The router has no notion of a group. It classifies the
  question NUMERIC_COMPUTE; either it lifts the group phrase as a company mention, which
  resolves to no filer and is refused before any SQL, or it lifts nothing and
  `BranchDispatcher._unresolved_reasons` degrades the question to a narrative plan.
- **C2, no aggregate (22 A / 16 D).** When a screen does run, the plan holds one `screen`
  step and nothing else; no op sums or takes the median of its rows, and the synthesizer
  rightly refuses to add the figures itself (`int-014d29a97aa6-058ab168` listed MSFT, AAPL and
  NVDA; the key, 35.223B, is their sum).

This change resolves group mentions deterministically, routes a question over a group to
the screen path, adds a deterministic `aggregate` plan op over a screen's rows, and states
what was aggregated. Semantics follow
[`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md): S2 (fiscal labels; each
period end disclosed when the ends are more than three months apart), S3 (the universe
stated, every member not ranked named with its reason; a total may be stated with
exclusions, as long as they are disclosed), S6 (screens in USD, no conversion). It builds on
[`screen-universe`](screen-universe.md), [`screen-full-universe`](screen-full-universe.md),
[`screen-reporting-currency`](screen-reporting-currency.md) and
[`screen-exact-fiscal-year`](screen-exact-fiscal-year.md), and changes none of them.

## Requirement

### Part A: the group is resolved and routed (C1)

1. **Group mentions are resolved by rule, never by the LLM.** A *universe* is `covered` (every
   corpus filer) or a group id of `configs/eval/v2/groups.yaml`
   (`load_universe_groups()["groups"]`). Each universe has phrases:
   - a group id's own words: the id with each `_` written as a space (`big_tech` → "big
     tech"), so the words of the disclosure sentence ("the big tech group") resolve;
   - the aliases of a committed alias table, `universe_aliases()` (id → phrases), whose keys
     are `covered` and group ids only. `covered` has phrases only from the table (the bare
     word "covered" is ordinary English: "the period covered by the report"), and the table
     gives it at least "covered companies", the product's own wording.

   `find_universe_mentions(text, groups, aliases, ignore)` normalizes the text and every
   phrase the same way: lower case, every character other than `a-z`, `0-9` and `&` becomes
   a space, then split into words. Each `ignore` string (normalized the same way) is masked
   wherever it occurs as a word sequence, so its words match no phrase. The scan runs left
   to right: at each position the longest phrase whose words occur there wins and the scan
   continues after it; with no match the scan moves one word on. The result is the matched
   universe ids in order of first occurrence, without duplicates. Matching is exact on
   words: no stemming, so "Taiwan Semiconductor" never matches "semiconductors". It raises
   `ValueError` naming the key or phrase when an alias key is neither `covered` nor a group
   id, a phrase has no words, or one phrase belongs to two universes.

2. **A group lifted as a company is not a company.** `split_company_mentions(mentions,
   groups, aliases)` returns `(kept, universes)`: each mention in which
   `find_universe_mentions` finds a universe is removed and its universes collected (order of
   first occurrence, no duplicates); every other mention is kept unchanged, in order. The
   router resolves only `kept` as companies, so "the big tech companies" is never refused as
   an unknown filer.

3. **Aggregate cues are read from the question by rule.** `aggregate_cue(query)` (words
   normalized as in 1) returns:
   - `"median"` when the words contain `median` or `medians`;
   - `"sum"` when they contain `combined`, `aggregate`, `aggregated`, `collectively`,
     `altogether` or `summed`, or the word pairs `sum of`, `in total` or `taken together`;
   - `None` when neither, or when both kinds occur.

   The bare word `total` is not a cue ("total assets" is a metric) and neither are
   `average`/`mean` ("average shares outstanding") or `how many`: those are left to the
   router and planner LLMs. A cue only ever adds a requirement (Requirement 6); it never
   chooses the metric.

4. **Routing.** `universe_intent(intent, universes, resolved_companies, aggregate)` returns
   the intent value the dispatcher uses:
   - unchanged when `universes` does not hold exactly one id, or when `resolved_companies`
     (the number of company mentions resolved to a filer) is above 0 (a peer question such
     as "where does Walmart rank among the retailers" keeps the router's intent);
   - `"NUMERIC_SCREEN"` when the intent is `NUMERIC_LOOKUP`, `NUMERIC_COMPUTE` or
     `NUMERIC_SCREEN`;
   - `"NUMERIC_SCREEN"` when the intent is `NARRATIVE` or `HYBRID` and `aggregate` is not
     None; otherwise unchanged.

   The existing dispatcher step 2b then sends the question to `PLAN_GENERATOR`, never to
   narrative. The router passes the one universe and the cue to the planner.

### Part B: the aggregate (C2)

5. **The `aggregate` plan op.** A step `{"id", "op": "aggregate", "of": <step id>, "fn":
   <fn>}` with `fn` one of `sum`, `median`, `mean`, `count`. `PlanValidator.validate` accepts
   it when `of` is the id of an *earlier* step whose op is `screen`; otherwise exactly one
   error for the step that names its index, its id, `'of'` and the value. An `fn` outside the
   four is exactly one error naming the index, id, `'fn'` and the value. A later `compute`
   may reference an aggregate step's id, as any earlier step's.

6. **The plan must use the resolved group.** `group_plan_errors(plan, universe, aggregate)`,
   run by the plan generator beside `PlanValidator.validate` (its errors feed the same
   retry): with a `universe`, each `screen` step whose universe (absent or null meaning
   `covered`) differs is one error naming the step's index, id, the word `universe`, the
   step's universe and the expected one; with an `aggregate` cue, a plan with no `aggregate`
   step of that `fn` whose `of` names a `screen` step is one error containing `aggregate` and
   the fn. Errors come in step order, the aggregate error last; `[]` when both are None, and
   for a plan that is not a dict with a `steps` list (the validator reports those).

7. **The aggregate is computed in code.** `aggregate_screen_rows(fn, screen)` takes the
   screen step's `StepResult.to_dict()` and returns:

   ```python
   {
       "fn": "sum",                    # as given
       "status": "OK",                 # "OK" | "NOT_FOUND" | "ERROR"
       "value": 7155000000.0,          # None unless OK
       "unit": "USD",                  # the rows' unit; None for count and unless OK
       "n": 3,                         # rows aggregated
       "members": ["WMT", "AMZN", "HD"],        # row tickers, row order
       "fact_ids": ["101", "102", "103"],       # row fact ids, row order
       "middle": [],                   # median only: the middle row(s)' tickers
       "universe": "retailers",        # from the screen's disclosure; None without one
       "universe_members": 4,          # disclosure "members"; None without one
       "exclusions": [...],            # disclosure "exclusions", as given; [] without one
       "period_ends": {"WMT": "2025-01-31", ...},  # aggregated rows' stated ends, when set
       "period_ends_differ": False,    # latest minus earliest of period_ends > 92 days
       "error": None,                  # a reason unless OK (or NOT_FOUND)
   }
   ```

   Rules, checked in this order:
   - the step's `op` is not `screen`, or `fn` is not one of the four: ERROR, the error naming
     the op or the fn;
   - screen status `NOT_FOUND`: NOT_FOUND; any other status but `OK`: ERROR naming the
     screen's `step_id` and status;
   - a `row_cap` (rows trimmed by a size cap): ERROR, the error containing `row_cap`, since a
     total over trimmed rows would be silently partial;
   - no rows: NOT_FOUND; a row whose `value` is not a finite number: ERROR naming its ticker;
   - except for `count`, the rows must share one non-None unit, else ERROR naming the units;
     a `sum` of a unit `pure` or `Rate` or of money per share (`is_per_share_money`) is ERROR naming the unit
     (a sum of ratios or per-share amounts means nothing); `median` and `mean` of them are OK;
   - the value: `sum` the sum of the values; `mean` the sum over n; `count` n; `median` the
     middle value of the values sorted ascending, or for an even n the mean of the two middle
     ones. `middle` is the tickers of the middle row(s) in that ascending order (ties broken
     by ticker).

   The aggregate covers exactly the screen's rows, after `top_n` and `min_threshold`. Members
   the screen could not rank are never in it; they stay named in `exclusions`.

8. **The executor runs it.** `PlanExecutor.execute` runs an `aggregate` step over the
   `StepResult` of the earlier step `of` names and returns a `StepResult` with `op`
   `"aggregate"`, the status, `value`, `unit` and `error` of `aggregate_screen_rows`; its
   `to_dict()` carries the whole dict under `"aggregate"`. Its value is bound like any
   step's, so a later `compute` can use it. An `of` that names no earlier step is an ERROR
   step whose error names the value.

9. **The answer says what was aggregated.** `aggregate_disclosure_text(aggregate)` returns,
   for an OK aggregate, exactly

   ```
   {Label} of {n} {companies}: {T1}, {T2}, ..., {Tn}.
   ```

   with `{Label}` `Total` (sum), `Average` (mean), `Median` (median) or `Count` (count),
   `{companies}` `company` when n is 1, and the members in row order joined by `, `. A median
   inserts before the final period ` (middle: {M})` for one middle row, or
   ` (middle: {M1} and {M2}, averaged)` for two. Any other status gives `""`.
   `append_aggregate_disclosures(summary, steps)` behaves as `append_screen_disclosures`
   (screen-universe Requirement 8) for steps with `op == "aggregate"` and a dict `aggregate`:
   non-empty sentences appended in step order, one space apart, none already contained in
   the summary or already appended; an empty summary gets the sentences alone. The pipeline
   appends the screen disclosures first and then these, after the faithfulness verifier, so
   the universe, the named exclusions and the period ends (S2, S3) come from the screen's
   sentence and the aggregated members from this one.

10. **A stated count of the aggregate is supported evidence.** In
    `citation_integrity.unmatched_numbers`, a statement citing an aggregate step may state
    the step's value (precision of a computed value, `ratio_step`), its `aggregate.n` and
    its `aggregate.universe_members` (exact). "The 3 ranked retailers' combined interest
    expense was $7.155 billion; the group has 4 companies" is supported; a count matching
    neither is still unmatched.

## Acceptance criteria

- **C1**: `find_universe_mentions` follows Requirement 1. Point cases (groups and aliases of
  the test fixture): a group by its id words, by an alias, with an apostrophe or hyphen; two
  groups in order of appearance; the longest phrase winning over a shorter one of another
  group; "Taiwan Semiconductor" and "the period covered by" matching nothing; an `ignore`
  string masking a phrase; `ValueError` for an unknown alias key, an empty phrase and a
  phrase of two universes. Properties: the result does not depend on letter case or on
  punctuation between words; adding words that are in no phrase before or after the text never
  changes it; every id
  returned is a universe of the inputs; each appears once. Oracle: Requirement 1 applied by
  hand.
- **C2**: `universe_aliases()` (the committed table) has only `covered` and committed group
  ids as keys, gives `covered` "covered companies", and no phrase in it belongs to two
  universes or is a word sequence inside a corpus filer's name or ticker in the test's list.
  Property: through `find_universe_mentions` with the committed groups, every group id's own
  words and every alias resolve to their own universe. Oracle: Requirement 1;
  `configs/eval/v2/groups.yaml`.
- **C3**: `split_company_mentions` follows Requirement 2. Point: `["the big tech companies",
  "Apple"]` gives `(["Apple"], ["big_tech"])`; company names are kept. Property: `kept`
  plus the removed mentions is a partition of the input, order kept; `kept` holds no mention
  in which a universe is found. Oracle: Requirement 2.
- **C4**: `aggregate_cue` follows Requirement 3. Point cases for every listed word and pair,
  for `total`, `average` and `how many` (None), and for a median and a sum cue together
  (None). Property: case and punctuation do not change the result; inserting a non-cue word
  does not. Oracle: Requirement 3.
- **C5**: `universe_intent` follows Requirement 4, for every intent value, 0/1/2 universes,
  0/1 resolved companies and each cue. Property: the result is either the input intent or
  `NUMERIC_SCREEN`, and never `NARRATIVE` when the input was numeric. Oracle: Requirement 4.
- **C6**: `PlanValidator().validate` accepts a screen-then-aggregate plan for each fn, and a
  later compute over aggregate ids; it gives exactly one error naming `'of'` (or `'fn'`), the
  index, the id and the value for a missing, forward, unknown or non-screen `of`, and for an
  unknown `fn`. Property: for any string `fn` outside the four, that one error. Oracle:
  Requirement 5.
- **C7**: `group_plan_errors` follows Requirement 6. Point: a matching plan gives `[]`; a
  screen without `universe` when the group is `retailers` gives one error; a plan without the
  cued aggregate (or with a `median` when `sum` is cued, or an aggregate over a lookup) gives
  one; both at once give two, aggregate last. Property: with `universe` and `aggregate` None
  the result is `[]` for any plan. Oracle: Requirement 6.
- **C8**: `aggregate_screen_rows` follows Requirement 7, values within `rel=1e-12` of hand
  arithmetic on the fixture rows (retailers FY2024, Notes): sum, mean, count, median of 3
  (the middle value) and of 4 (the mean of the two middle), the disclosure fields copied,
  `period_ends_differ` both ways, and every ERROR / NOT_FOUND rule. Properties: sum, mean and
  median do not depend on row order; adding an exclusion to the disclosure never changes the
  value and the exclusion appears in `exclusions`; a sum over integer-valued rows equals the
  integer sum; the median of an even count is the mean of the two middle of the sorted
  values, and of an odd count is one of the values; `members` and `fact_ids` follow row
  order. Oracle: Requirement 7; hand arithmetic.
- **C9**: `PlanExecutor.execute`, with the screen supplied through its `screen_fn` seam as a
  fixture `StepResult` (no database), returns an `aggregate` step with the C8 value and
  dict, binds it so `a1 / a2` computes, and gives ERROR naming `of` for an unknown `of`.
  Property: for any fixture rows and fn, the step's value equals `aggregate_screen_rows` of
  the screen step's dict computed in the test from the same rows by hand. Oracle:
  Requirements 7–8.
- **C10**: `aggregate_disclosure_text` and `append_aggregate_disclosures` follow
  Requirement 9 exactly. Point: each label, one company, median of odd and even n, non-OK
  `""`; append after a screen sentence, empty summary, no aggregate step (unchanged).
  Properties: appending twice equals appending once; the text contains every member as a
  whole word and n as a numeral. Oracle: Requirement 9.
- **C11**: `unmatched_numbers` follows Requirement 10: the Notes' statement citing the
  aggregate step id is `[]`; stating 5 retailers instead is `[5]`. Property: for any n and
  universe size, stating them is supported and stating another small integer is not.
  Oracle: Requirement 10.

## Interface

- `src.retriever.xbrl.universe_routing.universe_aliases() -> dict[str, list[str]]`
- `src.retriever.xbrl.universe_routing.find_universe_mentions(text: str, groups: Mapping[str, Sequence[str]], aliases: Mapping[str, Sequence[str]], ignore: Sequence[str] = ()) -> list[str]`
- `src.retriever.xbrl.universe_routing.split_company_mentions(mentions: Sequence[str], groups: Mapping[str, Sequence[str]], aliases: Mapping[str, Sequence[str]]) -> tuple[list[str], list[str]]`
- `src.retriever.xbrl.universe_routing.aggregate_cue(query: str) -> str | None`
- `src.retriever.xbrl.universe_routing.universe_intent(intent: str, universes: Sequence[str], resolved_companies: int, aggregate: str | None) -> str`
- `src.retriever.xbrl.universe_routing.group_plan_errors(plan: Any, universe: str | None, aggregate: str | None) -> list[str]`
- `src.retriever.xbrl.screen_aggregate.aggregate_screen_rows(fn: str, screen: Mapping[str, Any]) -> dict[str, Any]`
- `src.retriever.xbrl.screen_aggregate.aggregate_disclosure_text(aggregate: Mapping[str, Any]) -> str`
- `src.retriever.xbrl.screen_aggregate.append_aggregate_disclosures(summary: str, steps: Sequence[Mapping[str, Any]]) -> str`
- Changed behaviour, no new signature: `PlanValidator.validate` (the `aggregate` op),
  `PlanExecutor.execute` and `StepResult.to_dict` (the `aggregate` step and key),
  `citation_integrity.unmatched_numbers` (aggregate counts).

## Notes

- **Fixture (C8–C11; synthetic values shaped like the corpus).** Retailers FY2024 interest
  expense; group members AMZN, COST, HD, WMT. Rows in rank order: WMT 2,683,000,000 (stated
  end 2025-01-31, fact 101), AMZN 2,406,000,000 (2024-12-31, 102), HD 2,066,000,000
  (2025-02-02, 103), all USD; COST excluded `period_not_filed`, latest FY2023. Sum
  7,155,000,000; mean 2,385,000,000; median AMZN 2,406,000,000; ends span 33 days, so no
  disclosure. With COST added as a fourth row of 150,000,000 (end 2024-09-01): median
  (2,066,000,000 + 2,406,000,000) / 2 = 2,236,000,000, middle HD and AMZN; ends span 154
  days, so `period_ends_differ` is true. Expected sentences: `Total of 3 companies: WMT,
  AMZN, HD.` and `Median of 4 companies: WMT, AMZN, HD, COST (middle: HD and AMZN,
  averaged).`
- **What is spec-tested and what is reviewed.** Locked here: Requirements 1–10 through the
  functions above, the validator, the executor and citation integrity. **Reviewed by the
  maintainer, not locked** (prompt content cannot have an independent oracle): new versions
  of the intent router prompt (`_base/intent_router/v3`: NUMERIC_SCREEN also covers a total,
  median, average or count over a named group or the covered companies; a group phrase is
  never a company mention), the plan generator prompt (next free `_base/plan_generator`
  version: the `aggregate` op with an example, and the router's `UNIVERSE` / `AGGREGATE`
  hints to copy), and the synthesizer prompt (next free `_base/synthesizer` version: state an
  aggregate step's value citing the step id, never add figures itself). The alias table's
  content (`universe_aliases()`) is reviewed data; C2 locks only its shape.
- **Implementer work not locked here:**
  - `IntentRouter.route`: split the LLM's company mentions (`split_company_mentions`) before
    resolution; find universes in the query (`find_universe_mentions`, `ignore` = the
    mentions that resolved to a filer) plus those from the split; set the intent with
    `universe_intent` and `aggregate_cue`; carry `universe` (the one id, else None) and
    `aggregate` on `IntentRouterResult` (and in `to_dict` and the `intent_extraction` event).
    Groups come from `load_universe_groups()`; never from the LLM.
  - `PlanGenerator`: pass the universe and cue to the prompt; append `group_plan_errors` to
    the validator's errors inside the existing validate-retry loop (escalation unchanged).
  - `StepResult` gains `aggregate: dict | None`; the pipeline's `_screen_step` copies the
    screen's `row_cap` into the step (`to_dict()["row_cap"]`) so Requirement 7 sees it; the
    pipeline appends `append_aggregate_disclosures` after `append_screen_disclosures`.
  - The executor's `numeric_lineage` event for an aggregate step (fn, of, the row fact ids).
  - `citation_integrity`: an aggregate record's candidates (Requirement 10).
  - The verifier: an aggregate step's evidence shows its value, fn, members and fact ids, so
    the LLM verifier does not mark the stated total unsupported.
  - Suggested starting alias table (common English for each group's definition, not copied
    from the eval lexicon or phrasebook, which were not read for this spec): `covered`:
    "covered companies", "companies in coverage", "all companies covered"; `semiconductors`:
    "semiconductor companies", "chip companies", "chipmakers", "chip makers";
    `retailers`: "retail companies", "retail sector"; `big_tech`: "big tech companies",
    "largest technology companies". The maintainer decides the final list.
- **Files.** New modules only (`universe_routing.py`, `screen_aggregate.py`); changed:
  `intent_router.py`, `plan_generator.py`, `plan_validator.py`, `plan_executor.py`,
  `citation_integrity.py`, `retrieval_pipeline.py`. None of `xbrl_factstore_lookup.py`, the
  segment/geographic screens or `xbrl_factstore_breakdown.py` (where `impl/dimensional-s1`
  is working). Possible textual overlap with `dimensional-s1` only in `plan_executor.py` if
  that change touches the lookup dispatch there; rebase if so.
- **Parts.** Two implementation parts are suggested: `impl/screen-aggregates--part1`
  (Part B: C6, C8–C11, which pay off for the 22/16 C2 cases already routed to a screen) and
  `impl/screen-aggregates` (Part A: C1–C5, C7, and the prompts).
- **Out of scope.** Peer questions naming a company and a group (routing unchanged; C3/C4 of
  the D3 analysis); a count of members above a threshold that returns 0 (the screen returns
  NOT_FOUND with no rows); an aggregate over a breakdown; the key-side universe differences
  (COST/MU `no_fsds`, WMT/GOOGL `resolved_drop`), which this change discloses but cannot
  fix; questions naming two groups (routing unchanged, left to the decomposer).
- **Validation plan (after the impl merges, maintainer cap).** A DeepSeek dev slice of the
  screen.sum and screen.median cases from the D3 run plus a seeded regression sample of
  screen.top/bottom, peer_relative and lookup cases (`scripts/eval/build_slice_suite.py` →
  `run_suite.py --suite-path … --llm-profile deepseek --persist-audit --max-cost-usd 0.50`),
  run before (main) and after, compared with `compare_runs.py`; about $0.30–0.50 per run.
  Dev split only; held-out sets are not touched. Expected: most of the 10 A / 9 D cases where
  the system's rows already sum to the key become correct; the rest differ by key-side
  universe membership.
- **Decisions for the maintainer (recommendations given).**
  1. *State a total with exclusions or differing period ends?* S3 answers it: yes, with the
     disclosure (Requirement 9 plus the screen sentence). Recommended as specified.
  2. *Cue words.* Only unambiguous ones force an aggregate step (Requirement 3); `total`,
     `average`, `how many` are left to the LLMs. Recommended: keep the list narrow; widen
     only on evidence from a fresh, disjoint sample.
  3. *Alias table content.* Reviewed data in `universe_aliases()`; recommended: common
     English per group definition (above), never phrases copied from the eval lexicon, so
     paraphrase cases still measure generalization.
  4. *`mean` and `count`.* Included because they are cheap and deterministic; no eval shape
     uses them yet. Recommended: keep.
