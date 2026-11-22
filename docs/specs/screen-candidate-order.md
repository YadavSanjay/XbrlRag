# screen-candidate-order — a screen picks each filer's fact as the lookup does, and the families gain the presented lines they miss

Decisions of the maintainer (2026-11-12) from the key-concept audit
(`data/handover/key_concept_audit/REPORT.md`, not committed): the flat cross-filer screen and the
lookup pick different facts for one filer, family and year, and the concept mapping misses
presented elements that are the family's own measure. Both cost silent-wrong answers in the
dev and validation slices (runs `eval-91a015b22028`, `eval-97b2ebee6736`).

Four defects, each checked on `xbrl_db` (read-only, 2026-11-12) and against SEC's own data:

1. **The screen ignores D13.** The lookup orders a family's candidates with
   `family_resolution.candidate_order` (concept-family-coverage R2: primary before substitute,
   presented line before a notes-only fact, then rank, value, precision, fact id). The flat
   screen (`XbrlFactstoreScreen._query_flat`) orders by rank and `fact_id` only. Intel's FY2025
   income statement presents "Interest and other, net" 3,257M (`NonoperatingIncomeExpense`);
   the family's rank 1, `OtherNonoperatingIncomeExpense`, is a notes-only −416M, and the screen
   ranks Intel on it. Qualcomm likewise (972M presented, 21M in the notes). The semiconductor
   FY2025 median (case `int-b4de130cc08b`) is 230M against the key's 577M. Over the corpus's
   4,507 FY, USD filer-year-family cells the two orders pick a different value in 106 (TSM,
   never ranked on its USD translation, left out); 33 of those are a rounded duplicate of the
   same concept (Walmart FY2025 cash 9,000M at `decimals` −8 against the exact 9,037M).
2. **The mapping misses presented elements of the measure.** Net PP&E including finance-lease
   right-of-use assets (Intel, Home Depot, Micron, UPS, Salesforce, Tesla, Meta, Alphabet,
   Union Pacific, Amazon), current available-for-sale debt securities (Intel, Micron, Oracle,
   Salesforce), senior-note proceeds (Oracle) and short-term debt proceeds (AMD) belong to no
   family, so the system finds no fact. FY2022 short-term investments of the covered companies
   sum to 275.7B against a key of 294.5B (Intel 17,194M, Micron 1,069M, Oracle 519M missing).
3. **Cash including restricted cash is outside `CashAndEquivalents`.** Procter & Gamble
   (FY2023–FY2026) and Intel (FY2022–FY2024) present their balance-sheet "Cash and cash
   equivalents" line with `us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`
   and no plain cash fact. The FY2025 covered-companies cash sum is 423.1B against a key of
   432.7B: P&G's 9,556M is missing, and Walmart's rounded duplicate costs 37M (defect 1).
4. **The reload would put a fallback line first.** The facts-count pass (`filer-rank-coverage`)
   makes the QName a filer reports in the most fiscal years its rank 1. Cash including
   restricted cash and short-term debt proceeds sit on the cash-flow statement in every year,
   so with them in the families the pass would rank Intel's FY2025 cash at 14,712M (cash-flow
   total) instead of the balance sheet's 14,265M, and answer Costco's and Vistra's debt issued
   with their short-term lines (Costco FY2024 928M instead of 498M; Vistra FY2024 1,802M
   instead of 3,817M), where the key keeps the long-term line.

## Requirement

### R1. The screen's choice is the lookup's (`select_preferred_concept`)

`select_preferred_concept(rows)` keeps its signature and return shape
`(kept, component_only)`. It takes the flat screen query's candidate rows (several per filer,
any years) and, per `ticker`:

1. takes the filer's rows of its **latest `fiscal_year`**;
2. orders them with `family_resolution.candidate_order`, the one rule a lookup uses; the
   screen never re-implements it;
3. when the first row is a `primary` (`family_resolution.row_role`), **keeps** it (the input
   dict, unchanged);
4. otherwise lists the filer apart in `component_only` as
   `{"ticker", "fiscal_year", "concept"}`: the concept of the first row when it is a stated
   substitute (a screen never ranks a substitute, as the v2 key excludes it with reason
   `substitute:<code>`), or, when no row may answer, the concept of the lowest-ranked row
   (larger `fact_id` on a tie).

`kept` and `component_only` are sorted by ticker; every input ticker appears in exactly one;
the result never depends on the rows' order.

### R2. Concept-mapping additions

Each element below is a **primary** of its family for every filer, appended after the
family's existing primaries in the order listed (no existing primary changes rank), and
listed under the coverage file's `same_measure` (an answer with it earns the
preferred-concept credit):

| Family | Element | Generic rank | Evidence (FSDS statement line) |
|---|---|---|---|
| PropertyPlantAndEquipment | `us-gaap:PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization` | 3 | INTC FY2025 "Property, plant and equipment, net" 105,414M; HD "Net property and equipment" 28,021M; MU "Property, plant, and equipment" 46,590M |
| ShortTermInvestments | `us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent` | 6 | INTC FY2022 "Short-term investments" 17,194M; MU 1,069M; ORCL "Marketable securities" 519M |
| DebtIssued | `us-gaap:ProceedsFromIssuanceOfSeniorLongTermDebt` | 8 | ORCL FY2025 "Proceeds from issuances of senior notes ..." 19,548M |
| DebtIssued | `us-gaap:ProceedsFromShortTermDebt` (fallback-only, R3) | 9 | AMD FY2025 "Proceeds from debt and commercial paper ..." 2,441M, its only issuance line |
| CashAndEquivalents | `us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` (fallback-only, R3) | 4 | PG FY2025 balance sheet "Cash and cash equivalents" 9,556M, no plain cash fact |

Placement (the presented-line rule decides first; rank decides only between lines a filing
presents alike):

- **PP&E** after `PropertyPlantAndEquipmentNet` (1) and `PropertyPlantAndEquipmentGross` (2).
  No corpus filing presents the element beside a presented gross line, and every filer that
  presents it presents no `…Net` line in that year, so the presented-line rule picks it. Rank 2
  would be closer in meaning (both are net balances) but would move `Gross` and change a base
  rank; not needed.
- **AFS debt, current** after the five existing primaries: it is the post-ASU 2016-01
  successor of `AvailableForSaleSecuritiesCurrent` (2), and the filers that present it present
  no other short-term-investment line.
- **Senior notes** after the long-term lines: a part of long-term debt issued; Oracle's only
  issuance line from FY2023.
- **Short-term debt proceeds** last: gross debt of any term is the measure (D3), but where a
  filer presents a long-term line too (Amazon, Costco, Vistra) the long-term line keeps
  answering, as the v2 statement-line rule (short-term lines excluded) keys it.
- **Cash including restricted cash** after `CashAndCashEquivalentsAtCarryingValue` and the two
  cash-and-short-term-investment totals: below the plain line so that wherever both are
  presented (Intel FY2025: balance sheet 14,265M, cash-flow statement 14,712M) the plain line
  wins; no corpus filing presents it beside a cash-and-short-term-investments total without a
  plain cash line, so its place relative to those (ranks 2, 3) decides nothing today. It
  answers only as a filer's presented line with no plain cash fact (R3 keeps the reload from
  undoing that).

### R3. Fallback-only primaries (`facts_count_ranking`)

The coverage file gains a top-level key `fallback: {family: [QName, ...]}` listing primaries
that answer only where the filer presents no higher-ranked primary:

```yaml
fallback:
  DebtIssued: [us-gaap:ProceedsFromShortTermDebt]
  CashAndEquivalents: [us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents]
```

`load_family_coverage()` returns it as `"fallback": {family: frozenset[QName]}` (every listed
QName must be a primary of its family; anything else raises `ValueError`).

`facts_count_ranking(global_ranking, years_reported, fallback)` is the facts-count rule with
the fallback QNames left out of the choice: among the eligible QNames (rank outside 50–99) that
are not fallback, those the filer reports in the most fiscal years tie; the first of them in
global rank order wins. It returns None when nothing is reported or the winner is already the
global first eligible QName; otherwise the full ranking: the winner at 1, every other eligible
QName (fallback ones included) in global order from 2, then the component-band rows at their
global ranks (as `filer_ranking`). With an empty `fallback` it is today's rule exactly.
`Layer0Populator.populate_concept_mapping_overrides` uses it with the family's `fallback`.

### R4. Wiring (not locked)

- `_query_flat` returns every candidate row of each filer's latest `fiscal_year <= fy` (same
  filters as today: canonical, own column, undimensioned, the literal `unit = 'USD'`, the
  period and duration predicates, the publish gate), selecting also `concept_role`,
  `presented` and `decimals` (`fact.is_decimals_infinity` → None, as the lookup's
  `_DECIMALS_SQL`). The pick moves out of the SQL's `DISTINCT ON` into R1. Keep the literal
  `unit = 'USD'`: the restatement-s1 fake recognises the query by it.
- Everything after the pick is unchanged: the reporting-currency check, S1 restatement
  (`_restate`), the S4 defined measures (Debt, DeferredRevenue: the pick only names the
  filing and date), nil rows, `select_fiscal_year`, ranking, aggregates and medians over the
  rows, compound dependent asks.
- The v1 suite's flat screen templates `aq-1b-revenue-screen` and `aq-5b-total-assets-screen`
  (`configs/eval/sql_derived_templates.yaml`) order by `@LOOKUP_ORDER(f)` with
  `@ANSWERABLE(f)` instead of rank only, so the suite keys the fact the screen cites.
- The mapping additions go in `configs/concept_mapping/concept_family_overrides.yaml`
  (`families.<F>.primaries`, with reasons; `CashAndEquivalents` and `PropertyPlantAndEquipment`
  are new entries) and `concept_family_coverage.yaml` (`same_measure`, `fallback`).

## Acceptance criteria

- **C1**: The screen keeps, per filer, the row the lookup's candidate order puts first in its
  latest year. Points: INTC and QCOM FY2025 OtherNonOperatingIncome are
  `NonoperatingIncomeExpense` 3,257,000,000 and 972,000,000; the median of the seven
  semiconductor filers' FY2025 picks is 577,000,000 (exact); Walmart FY2025 cash keeps the
  exact 9,037M fact over its −8-decimals duplicate; a filer whose first answerable row is a
  stated substitute, or with only components, is listed apart with the concept R1 names.
  Property: for any candidate rows, `select_preferred_concept` equals R1's definition built on
  `candidate_order`, and permuting the rows changes nothing. Oracle: FSDS statement lines and
  companyfacts notes facts of the filers' 10-Ks (`fixtures/`); the hand median; R1.
- **C2**: Each addition answers its filer's presented line, through both `candidate_order` and
  `select_preferred_concept`: PP&E FY2025 INTC 105,414M, HD 28,021M, MU 46,590M, UPS 37,731M,
  CRM 3,236M, TSLA 40,643M, META 176,400M, GOOGL 246,597M; short-term investments FY2022 INTC
  17,194M, MU 1,069M, ORCL 519M; debt issued AMD FY2025 2,441M, ORCL FY2025 19,548M, and COST
  FY2024 keeps its long-term line 498M beside short-term 928M; cash PG FY2025 9,556M
  (inclusive, the only line) and INTC FY2025 14,265M (plain, with the inclusive 14,712M also
  presented). Exact (whole dollars from SEC). Properties: the additions are primaries at the
  ranks of R2's table, credited as the measure, existing primaries' ranks unchanged; for any
  presentation, precision and fact ids, plain cash wins unless only the inclusive line is
  presented. Oracle: FSDS num/pre and companyfacts byte excerpts; R2.
- **C3**: Fallback-only primaries never become a filer's facts-count rank 1: the coverage file
  declares exactly R3's two; with companyfacts' years, `facts_count_ranking` returns None for
  INTC and PG cash and for COST, VST and AMD debt issued, and puts ORCL's senior notes first
  (its other QNames in global order). Property: for any years and either family, the result
  equals R3's definition written out in the test, and never starts with a fallback QName.
  Oracle: companyfacts entries of the fixture 10-Ks, counted by hand; R3.

## Interface

- New: `src.ingest.sec_edgar.ingest.layer0_populator.facts_count_ranking(global_ranking:
  Sequence[tuple[str, int]], years_reported: Mapping[str, Collection[int]], fallback:
  Collection[str]) -> list[tuple[str, int]] | None` (stubbed in the spec PR).
- Changed behaviour, same signature: `xbrl_factstore_screen.select_preferred_concept` (R1),
  `generate_concept_mapping.load_family_coverage` (R3's `fallback` key), the committed mapping
  (R2), `Layer0Populator.populate_concept_mapping_overrides` (R3), `_query_flat` (R4).

## Notes

- **Oracles and fixtures** (`test/spec/screen-candidate-order/fixtures/`, public SEC data):
  - `fsds/<quarter>/{sub,num,pre}.tsv`: byte copies of the lines of SEC's Financial Statement
    Data Sets for 37 annual reports of 18 filers and 24 tags (`num`: undimensioned rows only);
    `provenance.json` lists each filing, the source zip's URL and SHA-256 and each excerpt's
    SHA-256 (checked when the tests load). FSDS holds statement facts only, so `pre` says
    which lines a filing presents.
  - `companyfacts_excerpt.json`: SEC companyfacts entries of the same tags filed in those
    reports (notes included), with each payload's URL, fetch time and SHA-256.
  - Candidate rows are built from them (`sco_support.store_rows`): value from companyfacts,
    `presented` from FSDS `pre` (statements BS, IS, CF, CI, EQ; not parenthetical), rank and
    role from `family_member` (the committed mapping, so the tests read R2 as committed).
- **Dry check of the effect** ($0, read-only, the change's mapping emulated over
  `mv_financial_facts` with the facts-count pass of R3 replayed on `xbrl_db`):
  - FY2025 cash, covered companies: 423.132B → 432.725B (P&G added, Walmart's exact fact);
    equals the key.
  - FY2022 short-term investments: 275.732B → 294.514B (INTC, MU, ORCL added); equals the key.
  - FY2025 semiconductor other non-operating income median: 230M → 577M; equals the key.
  - FY2024 debt issued: 193.347B → 193.347B (AMD and ORCL are added at 0). The key is 179.534B:
    the gap is Alphabet's 13,589M, which `key_resolutions.yaml` entry 18 drops as "not
    long-term debt" (written 2026-11-09, before D3 made the family debt of any term), and
    Abbott's 223M (`presented_line_not_keyed`). Both are key-side and outside this change.
  - Whole corpus (FY, USD, the screen's filters, TSM left out): 164 filer-year-family cells
    change. 58 appear (PP&E 26, short-term investments 17, debt issued 9: AMD 4, ORCL 4,
    COST FY2023 917M; cash 6: PG 4, INTC 2). 106 change value (deferred revenue 38, S4-measured
    in screens so only the pick's filing and date matter; other non-operating income 24;
    accounts receivable 12; goodwill 9; cash 4; D&A 4; pretax income 4; share-based
    compensation 4; dividends 3; debt, debt issued, net income to common, stock repurchases
    1 each); 33 of them are a rounded duplicate replaced by the exact fact.
- **Consumers of the screen's pick** (all follow R1 with no change of their own): ranked
  screens, `aggregate` and `median` over screen rows, compound dependent asks (`<<TOP:id:n>>`
  injects the screen's ranked rows), the reporting-currency check, S1 restatement and the S4
  defined measures (which take only the pick's filing and date). The breakdown does not use
  the screen's pick: its fallbacks and its total (`_query_total`) have their own order.
- **Consumers of the mapping** (all read the reloaded `concept_mapping` / MVs or the committed
  rows; none needs a code change): the lookup (`family_member`, the view join), the screen,
  `mv_financial_facts` and the 18 MVs built on it (`mv_derived_metrics` gains PG's cash ratio
  inputs; its picks stay rank-only, see Out of scope), the v2 key build (`v2_sources`
  `INVENTORY_SQL` / `CANDIDATES_SQL` read `mv_financial_facts`; `v2_witness` and the statement
  witness read the candidates), the v1 templates through the MVs, and the coverage card.
- **Frozen suites** ($0 dry build of the K6 arguments with this change's mapping emulated
  over `mv_financial_facts`, the facts-count pass replayed, and the R4 template change; the
  unchanged build reproduces `data/eval/v2_frozen` byte for byte). No `expected_value`
  changes. dev: 2 intents swap (META FY2025 total current assets lookups out, META FY2025 PP&E
  lookups in: the seeded draw saw a larger inventory), 6 cases change key inputs or
  disclosures only (PG and INTC join the FY2023 cash ranking, UNP the FY2024/FY2025 PP&E
  rankings, HD FY2024 and AMZN FY2025 PP&E cite the inclusive element at the same value);
  validation: 1 swap (META), 3 such cases; dev compound 1 and validation compound 2 (HD FY2023
  PP&E cites the inclusive element, same value); dev sample: 2 swaps, 1 such case. v1 suite
  (rebuilt both ways; the committed `sql_derived_v1.json` already differs from a fresh build
  by earlier reloads): 513 cases either way, one change: `aq-1b-revenue-screen:fy=2024` cites
  Costco's presented `RevenueFromContractWithCustomerExcludingAssessedTax` fact (208783)
  instead of the notes-only `Revenues` fact (209601), same value.
  **The rebuilt build blocks** until three keys are resolved (the statement witness rejects
  the new candidate's line): UNP FY2024 and FY2025 PP&E ("Properties, net", outside the PP&E
  include) and INTC FY2023 cash (read from the FY2025 10-K's cash-flow opening balance; the
  cash rule takes balance-sheet lines). Reviewed `key_resolutions.yaml` entries (reading
  `key`) or a rule change are the maintainer's; the dry build used simulated entries.
- **Deployment** (operator, after the implementation merges; no re-ingest: the facts are in the
  store, only `concept_mapping` and the MVs change). Stop the API, MCP and UI; Postgres,
  Qdrant and RagInference stay up. On `xbrl_db`, the concept reload of guide 04 §4.6.1 (as
  `data/handover/ifrs_debt_rollout/ifrs_debt_mapping_rollout.ps1` runs it): the
  `concept_mapping.sql` DDL, regenerate the CSV (`generate_concept_mapping --max-depth 0
  --top-k 30`), load it (`--concept-mapping-mode replace_source`), the facts-count pass
  (`--concept-mapping-overrides`, now with R3), `concept_mapping_ns_expand.sql`,
  `--sync-ns-expanded`, the extension classifier (dry run, then write; the coverage file
  changed), then `scripts/rebuild_mvs.py` once. Checks: `check_family_coverage_load.py`, the
  ns_expanded drift dry run, per-family fact counts move only for PropertyPlantAndEquipment,
  ShortTermInvestments, DebtIssued and CashAndEquivalents, `entity_master` unchanged, and the
  dry-check sums above. Then rebuild the v1 suite and dry-build v2 (both $0).
- **Held-out** (`xbrl_heldout`, the maintainer only; H-F stays sealed and is not rebuilt):
  after the `xbrl_db` rollout passes, the same reload and MV rebuild under the held-out
  profile, then the counts-only `data/handover/key_concept_audit/run_hf_key_concepts.ps1`
  (expected: the CashAndEquivalents no-system-fact cases, 9 before, fall; no family's harmful
  count rises). Its emulation (`key_concept_core.pick`) still models the screen as rank-only:
  switch the screen path to the lookup path's order first, or the presented-versus-rank
  counts cannot move.
- **Out of scope** (listed, not decided here):
  - excluding, with a reason, filers whose only presented line is a broader total (D13
    follow-up);
  - LMT `CostOfRevenue` keyed as operating expenses (key error, key side, after G1);
  - the witness ordering (INTC restructuring substitute; NVDA FY2023 `prefer` pattern);
  - Alphabet's FY2024 debt-issued drop (entry 18) under D3, and Abbott's unkeyed line;
  - the other rank-only orderings: the breakdown's total (`_query_total`, no value differs in
    the corpus), `mv_derived_metrics` inputs (CRM D&A 903M notes-only against 3,786M
    presented; MU receivables; AMD pretax income), the `/api/v1` comparison grid
    (`src/api/v1/service.py`), and the reference SQL in `scripts/xbrl/analyst_queries.sql`.
- **Retired tests rewritten in this PR** (they pinned the rank-only pick):
  - `test/spec/screen-concept-rank/test_screen_concept_rank.py::test_each_filer_gets_its_best_row_or_is_component_only`
    now asserts only what the rank-only key and the candidate order agree on (C1 here pins
    the choice), so it passes before and after the implementation.
  - `test/spec/restatement-s1/conftest.py`: the fake answers the flat query (literal
    `unit = 'USD'`) with every own-column row of each filer's latest year, not one row picked
    by a `DISTINCT ON` it no longer needs; `docs/specs/restatement-s1.md` Notes say so.
