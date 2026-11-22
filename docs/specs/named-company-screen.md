# named-company-screen — a ranking among named companies ranks only those companies

A $0 trace of the dev compound run `eval-18b83b48fb4d` found a confident, cited, wrong answer
shape. A (sub-)question that names specific companies and asks which of them is highest or
lowest on a metric ("Among AAPL, MSFT and GOOGL, which has the highest R&D intensity?")
resolves all of them in the intent, but the planner sometimes emits a `screen` step with
`universe: "covered"` (or no universe) and `top_n: 1`. Nothing compares the screen's universe
with the companies the intent resolved, so the plan passes `PlanValidator`, the screen ranks
the whole corpus, and the answer names the corpus-wide leader as the leader among the named
companies. Three dev cases hit it while their siblings passed: intermittent planner behaviour
that a deterministic check must catch, the way `group_plan_errors` catches a screen of the
wrong group (docs/specs/screen-aggregates.md, Requirement 6).

**Interface choice.** The screen op cannot state a member list today: `universe` is
`"covered"` or a group id of `configs/eval/v2/groups.yaml` (docs/specs/screen-universe.md),
`XbrlFactstoreScreen.screen` returns ERROR for anything else, and the SQL is per view, not per
ticker. So this change adds the smallest interface that can: **`universe` may also be a list
of tickers**. It reuses the one field that already means "the set this screen ranks", so a
step can never name a group and a list at once, and every consumer of the screen's members
(ranking, exclusions, disclosure, `aggregate` over the screen) follows from it unchanged. A
separate `members` key was rejected: it would need a precedence rule against `universe` and a
second check in every place that reads the universe. The plan check then rejects a screen
whose members are not exactly the resolved companies, as a plan error in the planner's
validate-retry loop, which ends in `plan_validation_failed` and escalates one model tier.

## Requirement

1. **A member list.** `member_list(universe)` is the one reading of a list universe, shared
   by the validator, the plan check and the screen. A list whose items are all strings that
   are non-empty after `strip()`, and that holds at least two distinct tickers after
   `strip().upper()`, gives those distinct tickers, upper-cased, sorted. Anything else (a
   string, None, an empty list, a one-ticker list such as `["AAPL", "aapl"]`, a list with a
   non-string or blank item, any other type) gives None.
2. **The validator accepts a member list.** `PlanValidator.validate` gives no `universe` error
   for a screen step whose `universe` is a list that `member_list` reads, and exactly one
   error containing `universe`, the step's index and id, and the value's `repr` for any other
   list. Every non-list value is checked exactly as before (docs/specs/screen-universe.md R1):
   `["semiconductors"]` stays one error.
3. **The named companies.** `named_companies(companies)` takes the intent's
   `IntentRouterResult.companies` (`ResolvedCompany` objects) and returns the tickers of the
   resolved ones (truthy `cik`, truthy `ticker`, not `is_ambiguous`: the router's own
   `resolved` test plus a ticker), upper-cased, de-duplicated, in order of first occurrence.
4. **The plan check.** `named_company_plan_errors(plan, companies, universe)`, with
   `companies` the named tickers and `universe` the router's `IntentRouterResult.universe`:
   - returns `[]` when `universe` is not None (the question names a group or "covered
     companies"; `group_plan_errors` owns that case), when `companies` holds fewer than two
     distinct tickers after `strip().upper()` (one company is a lookup), or when `plan` is
     not a dict whose `steps` is a list;
   - otherwise returns, in step order, one error per step with `op == "screen"` whose
     `member_list(step.get("universe"))` is not exactly the set of the named tickers
     (upper-cased). A missing, null, `"covered"`, group-id, partial or larger universe is
     such a step. Each error contains the step's index as `step[<i>]`, its id, the word
     `universe`, and every named ticker, so the planner can fix it on the retry.
   - Steps of other ops are never errors: a plan of lookups and a compute answers the
     question without a screen and is unchanged. An `aggregate` over a screen aggregates the
     screen's rows, so restricting the screen restricts it; it needs no check of its own.
5. **The planner runs the check.** `PlanGenerator.generate_plan` adds
   `named_company_plan_errors(plan, named_companies(intent.companies), intent.universe)` to
   the errors of each attempt, beside `PlanValidator.validate`, `group_plan_errors` and
   `operation_plan_errors`. A plan with such an error is retried with the errors as
   `VALIDATION_FEEDBACK`; when every attempt fails, the result is
   `{"error": "plan_validation_failed", ...}` (whose `reason` holds the errors), which
   escalates one model tier as today.
6. **The screen ranks a member list.** `XbrlFactstoreScreen.screen(step)` with a list
   `universe` that `member_list` reads ranks exactly as for a group whose members are
   `member_list(universe)` (docs/specs/screen-universe.md R2, R3, R5): only rows of those
   tickers, ranks 1..n among them, `top_n` after the restriction, every member ranked or
   excluded with one reason (`not_in_corpus` for a ticker the roster lacks,
   `metric_not_tagged` for a roster ticker with no row, the existing reasons otherwise). The
   disclosure's `universe` is the string `"named"`, so `screen_disclosure_text` writes
   `Universe: the named group (...)`, unchanged code. A list that `member_list` does not read
   gives status ERROR and no rows, never a ranking of every filer.

## Acceptance criteria

- **C1**: `member_list(universe)` follows Requirement 1 exactly. Point cases:
  `["msft", " AAPL ", "GOOGL"]` → `["AAPL", "GOOGL", "MSFT"]`; `["AAPL", "MSFT", "aapl"]` →
  `["AAPL", "MSFT"]`; None for `"covered"`, `"big_tech"`, None, `[]`, `["AAPL"]`,
  `["AAPL", "aapl"]`, `["AAPL", 7]`, `["AAPL", ""]`, `["AAPL", "  "]`, `("AAPL", "MSFT")`,
  `{"AAPL": 1, "MSFT": 2}`. Property: for any list of ticker-like strings, the result is None
  exactly when the distinct upper-cased stripped tickers number fewer than two, and otherwise
  equals their sorted list; it does not depend on the list's order or the items' case.
  Oracle: Requirement 1, computed in the test.
- **C2**: `named_companies(companies)` follows Requirement 3. Point case: Apple (AAPL),
  Microsoft (MSFT), an ambiguous mention, an unresolved mention, a second AAPL mention
  ("apple inc", ticker `aapl`), a resolved company with no ticker, and Alphabet (GOOGL) →
  `["AAPL", "MSFT", "GOOGL"]`. Property: for any list of resolved and unresolved companies,
  the result is the first-occurrence order of the resolved tickers, upper-cased, without
  repeats. Oracle: Requirement 3, by hand.
- **C3**: `named_company_plan_errors(plan, companies, universe)` follows Requirement 4. Point
  cases, named AAPL, MSFT, GOOGL and `universe=None`: a `covered` `top_n: 1` screen, a screen
  with no `universe`, a `big_tech` screen, a two-ticker list and a four-ticker list each give
  exactly one error with `step[0]`, the id, `universe` and the three tickers; the exact list
  in any order or case gives `[]`; a plan of lookups and a compute gives `[]`; a two-screen
  plan with only the second wrong gives one error naming `step[1]`; the same wrong plan
  gives `[]` with `universe="big_tech"` or `"covered"`, with one named company, with none,
  and for a non-dict plan or non-list steps. Properties: for any named set of two or more
  tickers and any plan of screens with drawn universes, the errors are one per screen whose
  member set differs from the named set, in step order; permuting or re-casing the named
  tickers never changes the error count. Oracle: Requirement 4 applied in the test.
- **C4**: `PlanValidator().validate(plan)` follows Requirement 2. Point cases: no `universe`
  error for `["AAPL", "MSFT"]`, `["aapl", "msft", "googl"]`; exactly one, naming `s1`, index
  `0` and the value, for `[]`, `["AAPL"]`, `["AAPL", "aapl"]`, `["AAPL", 7]`, `["AAPL", ""]`;
  the plan with a valid list gives no error at all. Property: for any list of ticker-like
  strings, a `universe` error appears exactly when Requirement 1 reads no member list.
  Oracle: Requirements 1 and 2, computed in the test.
- **C5**: `XbrlFactstoreScreen.screen(step)` over a fake database (the I/O boundary) follows
  Requirement 6. Point case, FY2024 net income screen, `order: desc`,
  `top_n: 1`, store values AMZN 85.6 (the corpus leader), GOOGL 49.3, META 43.9, AAPL 31.4,
  MSFT 29.5, NVDA 12.9 (billions; synthetic): with `universe ["AAPL","MSFT","GOOGL"]` the
  one row is GOOGL with rank 1 (AMZN leads the corpus); the disclosure has universe
  `"named"`, 3 members, ranked GOOGL, AAPL, MSFT; its text starts `Universe: the named group
  (3 companies; 3 ranked for FY2024).`. A list naming a roster ticker with no row and a ticker
  outside the roster excludes them as `metric_not_tagged` and `not_in_corpus`. `["AAPL"]`
  gives ERROR and no rows. Properties, for any store and any drawn member list of two or more
  tickers: status OK when a member has a row; rows are members only, in value order, ranks
  1..len(rows); with `top_n: 1` the one row is the member with the largest value (computed in
  the test); ranked and excluded tickers partition the members; re-ordering or re-casing the
  list gives the same rows. Oracle: Requirement 6 applied by hand to the fake store.
- **C6**: `PlanGenerator.generate_plan(query, intent)`, with the provider faked behind a real
  `LLMGateway` (the I/O boundary), follows Requirement 5. Point cases, intent with AAPL, MSFT
  and GOOGL resolved and no universe: a reply sequence covered-screen then member-list screen
  returns the member-list plan, after two calls, and the second call's user message holds
  `VALIDATION_FEEDBACK` naming the three tickers; a provider that always replies with the
  covered screen ends in `{"error": "plan_validation_failed"}` whose `reason` contains
  `universe`. Metamorphic: the same covered-screen reply is accepted on the first call when
  the intent resolves one company, and when it names the `covered` universe; for any drawn
  named set, a reply sequence wrong-then-right returns the right plan after exactly two calls.
  Oracle: Requirements 4 and 5.

## Interface

- `src.retriever.xbrl.named_company_screen.member_list(universe: Any) -> list[str] | None`
- `src.retriever.xbrl.named_company_screen.named_companies(companies: Sequence[Any]) -> list[str]`
- `src.retriever.xbrl.named_company_screen.named_company_plan_errors(plan: Any, companies: Sequence[str], universe: str | None) -> list[str]`
- Changed behaviour, no new signature: `PlanValidator.validate` (a list `universe`),
  `XbrlFactstoreScreen.screen` (a list `universe`), `PlanGenerator.generate_plan` (runs the
  check). The disclosure's universe for a member list is the literal `"named"`; the
  implementer may name it as a module constant.

## Notes

- **Oracles.** Every expected value is the requirement applied by hand in the test to
  synthetic inputs. Tickers are real names used as labels; the store values are synthetic,
  chosen so the corpus leader (AMZN) is not among the named companies and the named leader
  (GOOGL) is not the first named. The phrasings are synthetic, not eval questions.
- **Unchanged.** A screen over a named group or "covered companies" (`universe` set by the
  router: `group_plan_errors` owns it), a question with no resolved company, and a question
  with one resolved company. A plan that answers with lookups and a compute is not checked.
- **Implementer work not locked here:**
  - a new plan-generator prompt version under `prompts/templates/xbrl/` that lists the member
    list form of `universe` and says to use it when the question ranks companies it names;
    reviewed on a slice, not locked;
  - `resolve_universe_members` / `build_screen_disclosure` may take the member list through a
    helper; the locked surface is `screen()`'s result;
  - a `decision` event when the check rejects a plan is welcome but not required.
- **Known limits (decided by the maintainer as out of scope, recorded for review):**
  - `IntentRouterResult.universe` is None when a question names two universes, so such a
    question with two named companies is checked as a named-company question;
  - a question that names companies but ranks against a corpus phrase that is not in the
    committed `universe_aliases()` table ("among all filers") is checked as a named-company
    question; adding the phrase to the alias table is the fix, not loosening this rule;
  - unresolved mentions are not counted: a question naming AAPL, MSFT and an unknown name
    restricts the screen to AAPL and MSFT. (A non-screen query with an unresolved company is
    already refused before planning.)
  - a groups.yaml group may never be named `named` (the disclosure label would be shared).
