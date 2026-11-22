# sign-and-nil — a number keeps the sign its element carries, and a filed nil is never a value

The sign and nil part of nuance N7 in
[`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md) ("It mixes TWD and USD, or
reads a nil as 0"). Units and currency are covered by
[`units-currency-integrity`](units-currency-integrity.md).

**Sign.** An XBRL value carries its element's own sign convention: a loss is a negative
`NetIncomeLoss`, a `PaymentsFor…` is a positive cash outflow, an `IncreaseDecreaseIn…`
asset is positive when the asset grew (cash went down). A statement often shows the
opposite: its line has a negated label (FSDS `pre.negating` = 1), so Apple's FY2024
"Repurchases of common stock" prints `(94,949)` for a tagged +94,949,000,000. Answers,
computes, screens and eval keys use the tagged value with its own sign, never the printed
one, and a loss word reads negative (decided in
[`grading-precision`](grading-precision.md), requirement 9).

**Nil.** A fact filed with `xsi:nil="true"` says "nothing is reported here". It is not 0
and not a value from another column. Our store keeps it as `fact.effective_value` NULL,
`fact_value` `''` and `decimals_value` NULL (there is no nil flag; `value IS NULL` is the
nil test: no non-nil numeric fact has a NULL value). `.claude/rules/rag.md`: "nil facts and
INF decimals never become 0".

## What the corpus shows (xbrl_db and SEC copies, read-only, 2026-11-07)

Held-out filers, draws and suites were not read; every filer below is a corpus filer.

- **Negated labels are everywhere, and the store is faithful.** 148 corpus filings in the
  local FSDS copies (2022q1–2026q3) have 14,301 IS/BS/CF statement lines; 2,415 of them
  (CF 2,040, IS 290, BS 85) are negating, in every one of the 148 filings. For the 2,389
  negating lines our store has a current-period value for, the store equals the FSDS `num`
  value 2,385 times, its negation 0 times (4 have no single FSDS value to compare). 382 of those values are
  negative and 132 zero: a negating line does not mean "a positive number". Top tags:
  `PaymentsForRepurchaseOfCommonStock` (127), `PaymentsToAcquirePropertyPlantAndEquipment`
  (119), `IncreaseDecreaseInInventories` (95), `IncreaseDecreaseInAccountsReceivable` (93),
  `InterestExpense…` (83), `IncomeTaxExpenseBenefit` (36).
- **Nil facts.** 316 numeric facts are nil (28 filers; VST 42, CEG 23). 247 are
  `CommitmentsAndContingencies` (the balance-sheet caption line, no family). 11 belong to a
  concept family, all VST, all on `srt:ConsolidationItemsAxis` = `OperatingSegmentsMember`:
  `InterestExpenseNonoperating` (family InterestExpense, rank 2) and
  `IncomeTaxExpenseBenefit` (TaxExpense, rank 1) in the FY2025 10-K and FY2026 10-Qs.
- **INF decimals.** 8,876 facts have `decimals="INF"` (826 undimensioned, in a family:
  dividends per share, share counts, statutory rates). `mv_financial_facts.decimals_value`
  is NULL for both an INF fact and a nil fact.
- **Sign flips between filings.** Across the 33 corpus filers' companyfacts, 1,619 periods
  of one concept and unit carry a value and its exact negation from different filings
  (304 ending in 2021 or later, 7 of those on a mapped concept). Two FY2021–FY2025 USD
  fiscal-year keys of mapped concepts sit on such a period, both AMD FY2021 and both from
  its FY2023 10-K `0000002488-24-000012`: `NetCashProvidedByUsedInFinancingActivities`
  (+1,895,000,000; the FY2021 and FY2022 10-Ks file −1,895,000,000) and
  `NetCashProvidedByUsedInInvestingActivities` (+686,000,000 against −686,000,000).
  `latest_filed_key` takes the latest filing, so today both keys carry the filer's error.
  This is fact 18008 of `data/eval/calc_check/report.md` ("(1,895)" on the page, no minus
  on the tag; Arelle computes −1,898..−1,892 for that summation): a filer error the store
  keeps faithfully, which a key must not adopt silently.

## Requirement

### R1. A lookup never answers with a nil

`XbrlFactstoreLookup._build_result` (rows as its SQL returns them; a row whose `value` is
None is nil):

1. **The answer period** is the `period_end` of the first row in `candidate_order`, nil
   rows included: a nil row is the filer's own column for that period, so it is never
   skipped in favour of an earlier column.
2. **Only a valued row answers.** Among the rows `candidate_order` keeps at the answer
   period, the answer is chosen exactly as today but over the valued rows only (the first
   valued row in `candidate_order`, then `most_precise_consistent` within its concept). A
   nil row is never a distinct value, never a rival and never blocks a confidence credit
   (as today).
3. **Nothing valued at the answer period → NOT_FOUND.** The status is
   `LookupStatus.NOT_FOUND` (with or without dimensions), `value` None, and one resolution
   note contains the word `nil` and the `fact_id` of every nil row `candidate_order` keeps
   at the answer period. Today such a lookup is AMBIGUOUS ("confidence 0.40 < threshold"),
   with the nil fact as its citation: a filed nil reads as a low-confidence guess.

### R2. A screen never ranks a nil

`XbrlFactstoreScreen.screen`, every view (flat, segment, geographic, derived):

4. A row whose `value` is None is not ranked. It is disclosed as the exclusion
   `{"ticker": <ticker>, "reason": "nil", "fact_id": <its fact_id as str>}` (a given
   exclusion of `screen-universe`'s disclosure), and a resolution note names its ticker and
   the word `nil`. The other rows are ranked as today, 1..n without a gap, in the requested
   order. When no row is left the status is `ScreenStatus.NOT_FOUND`, still with the
   disclosure. Today the segment and geographic views rank the nil (`_rank_and_filter`
   sorts None as −inf), so a FY2025 InterestExpense "operating segments" screen returns VST
   as rank 1 with value None, in either order.
5. **SQL.** Each view's per-filer `DISTINCT ON` prefers a valued row of the filer to a nil
   one (today `ORDER BY ticker, fiscal_year DESC, value DESC` puts NULL first, so the nil
   wins). A filer whose only row is nil still reaches requirement 4 as a nil exclusion, not
   as `metric_not_tagged`. (Not checked by the spec tests, which fake the database; see
   Notes.)

### R3. An eval key never adopts a sign flip silently

`src.eval.keys.core.latest_filed_key`, and through it `fiscal_year_key` /
`labelled_fiscal_year_key`:

6. When the period's candidate entries (`_candidates`: report forms, non-nil, that exact
   period) hold a value `v != 0` and its exact negation `-v`, the key raises
   `KeyAmbiguousError` whose message contains `sign`. The source cannot say which filing
   has the sign right, so the intent is dropped (as for any `KeyAmbiguousError`) rather than
   keyed with a guess. Any other disagreement (a restatement to a different magnitude) is
   S1's latest-filed rule, unchanged.
7. Otherwise a key's value is the entry's `val` with its own sign: on a negated-label line
   the key is the tagged value, never the printed one (`fsds.presented_value`). This holds
   today; the tests pin it beside requirement 6.

`as_reported_key` is unchanged: a key "as reported in accession X" is what X filed.

## Acceptance criteria

- **C1**: A lookup never answers with a nil (R1). Point cases, rows as stored in xbrl_db,
  VST FY2025 10-K `0001692819-26-000006`, dimension `ConsolidationItemsAxis` =
  `OperatingSegmentsMember`, single-dimension rows: TaxExpense (nil 146227 at FY2025, nil
  146353 at FY2023) → NOT_FOUND, value None, a note with `nil` and `146227`;
  InterestExpense (nil 146221 at FY2025, nil 146287 at FY2024) → NOT_FOUND naming
  `146221`; the InterestExpense rows with the FY2024 column given a value (synthetic: the
  undimensioned 900,000,000 of fact 143321) → still NOT_FOUND, never 900,000,000. Control:
  the undimensioned FY2025 tax expense 179,000,000 (fact 143329) with a nil copy of itself
  added → OK, 179,000,000, fact 143329. Properties: for any rows whose latest period holds
  only nil rows (any ranks, any count ≥ 1) and whose earlier periods hold values, the
  result is NOT_FOUND, value None, its note names every nil row at that period; adding nil
  rows at the answer period of a resolvable lookup changes neither status, value nor fact
  id. Oracle: the iXBRL source (`xsi:nil="true"`, no content) and R1 applied by hand.
- **C2**: A screen never ranks a nil (R2.4), over a database faked at `psycopg2.connect`.
  Point cases, FY2025 segment screens for "operating segments" (rows as the current SQL
  returns them): InterestExpense → VST (fact 146221, nil) only → NOT_FOUND, no rows,
  exclusion `{"ticker": "VST", "reason": "nil", "fact_id": "146221"}`, in `asc` and `desc`;
  TaxExpense → the same with 146227. Properties: for random distinct valued rows and at
  least one nil row, the ranking is the valued rows sorted by value in the requested order,
  ranked 1..n, each nil ticker disclosed with reason `nil` and its fact id; negating every
  value and flipping the order gives the same ticker order (sign symmetry); the input
  order of the rows does not matter. Oracle: R2 by hand; the stored rows.
- **C3**: An eval key keeps its element's sign and refuses a sign flip (R3), over committed
  companyfacts excerpts. AMD FY2021 `us-gaap:NetCashProvidedByUsedInFinancingActivities`
  USD → `KeyAmbiguousError` mentioning `sign` (entries −1,895,000,000 from
  `0000002488-22-000016` and `0000002488-23-000047`, +1,895,000,000 from
  `0000002488-24-000012`); AMD FY2022 and FY2023 → −3,264,000,000 and −1,146,000,000.
  Negated-label lines keep the tagged value (the printed value in brackets): AAPL FY2024
  `PaymentsForRepurchaseOfCommonStock` +94,949,000,000 (printed (94,949)),
  `IncreaseDecreaseInAccountsReceivable` +3,788,000,000 (printed (3,788)),
  `PaymentsToAcquirePropertyPlantAndEquipment` +9,447,000,000 (printed (9,447)); AAPL
  FY2022 `IncreaseDecreaseInInventories` −1,484,000,000 (printed 1,484); VST FY2025
  `InterestExpenseNonoperating` +1,179,000,000 (printed (1,179)). A loss: INTC FY2024
  `NetIncomeLoss` −18,756,000,000 (not negating, printed (18,756)). Exact equality: keys
  are integers from the source. Properties: for any entries of one period holding `v` and
  `-v` (v ≠ 0) the key raises with `sign`; without the flipped entries the key is the
  latest entry's value; negating every entry negates that key. Oracle: SEC companyfacts
  and FSDS `num` / `pre.negating` for the same accession (Notes), hand-checked against
  the filing's statement.

## Interface

No new function. Changed behaviour, no new signature:

- `src.retriever.xbrl.xbrl_factstore_lookup.XbrlFactstoreLookup._build_result` (R1).
- `src.retriever.xbrl.xbrl_factstore_screen.XbrlFactstoreScreen.screen` (R2): the
  exclusion reason `"nil"` is new.
- `src.eval.keys.core.latest_filed_key` (R3), so `fiscal_year_key` and
  `labelled_fiscal_year_key` too.

## Notes

- **Sources (fixtures under `test/spec/sign-and-nil/fixtures/`, each with its SHA-256).**
  - `companyfacts_excerpts.json`: the USD entries of each concept used, copied unchanged
    from `data/sec_companyfacts/CIK<cik>.json` (SEC's
    `https://data.sec.gov/api/xbrl/companyfacts/CIK<cik>.json`), with the file's SHA-256 and
    date.
  - `fsds_lines.json`: the `pre` lines and USD `num` rows of each case's filing and tag from
    the SEC Financial Statement Data Sets (local filtered copy
    `data/cache/sec_fsds/filtered/<quarter>/`), with the SHA-256 of `pre.tsv` and `num.tsv`.
  - VST rows (C1, C2): `mv_financial_facts` read 2026-11-07, committed in the conftest. The
    nil facts are in `vistra-20251231.htm` (SHA-256
    `958c23a62fc145c9ac04f26d2b6c8d654f642cfe5e3307452135b08b31b9851d`), e.g.
    `<ix:nonFraction … contextRef="c-1091" xsi:nil="true"
    name="us-gaap:InterestExpenseNonoperating" id="f-3000"></ix:nonFraction>` (fact
    146221), `f-3006` (146227), `f-3066` (146287), `f-3132` (146353).
- **The VST rows in C1 are the single-dimension ones.** The flat lookup also returns VST's
  multi-dimensional cells under `OperatingSegmentsMember` (a segment × operating segments,
  e.g. 146222–146226 for TaxExpense, values 0 and 1,000,000), because
  `mv_financial_facts` keeps only a fact's first dimension. Today those cells answer the
  "operating segments" question, AMBIGUOUS with value 0.0: the filed nil is replaced by
  another cell's 0. Keeping multi-dimensional cells out of a single-dimension lookup and of
  the segment screen (the breakdown already does, `has_multiple_dimensions`) is nuance N6,
  a separate change; C1 checks the nil rule on the rows that change would leave.
- **Found, outside this spec's tests (SQL and views; a corpus check or SQL-in-CI test is the
  right check):**
  - `scripts/ddl/mv/31_mv_segment_matrix.sql`: `RANK() … ORDER BY f.value DESC` ranks a nil
    first (NULLS FIRST): VST facts 146221, 146227, 148234, 150551, 150655 have
    `segment_rank` 1. `pct_of_axis_total` is NULL for them (correct).
  - `scripts/ddl/mv/34_mv_derived_metrics.sql`: the `prim` / `inst` `DISTINCT ON` picks by
    rank and fact id without preferring a valued row, so a nil could displace a valued
    duplicate and the metric would vanish (the final `WHERE value IS NOT NULL`). No
    undimensioned family fact is nil today, so nothing is lost yet.
  - Screen SQL (`_query_segment`, `_query_geographic`; `_query_flat` and the breakdown's
    `_query_total` order by rank and fact id): requirement 5.
  - `src/eval/suites/v2_sources.py` `FILING_FACTS_SQL` reads `decimals_value`, which is NULL
    for an INF fact, so `PostgresFactStoreSource.value` reports an exact fact's decimals as
    None (missing) and ranks it least precise (−10**6) instead of most precise; the
    cross-checked key then adopts None. 826 undimensioned family facts are INF. The lookup's
    own `_DECIMALS_SQL` reads `is_decimals_infinity` correctly.
  - `src/retriever/xbrl/xbrl_factstore_breakdown.py` `choose_partition` refuses a total
    ≤ 0, so an operating-loss total can never be broken down by segment (the members of a
    negative total are a partition just as well). No corpus filer-year has such a partition
    today (INTC FY2025 and VST FY2022 negative totals do not sum from their members), so it
    is noted, not specified.
- **Already right, pinned or left alone.** The calculator refuses a None input
  (`CalculatorError`), so a nil never computes as 0; a compute over a non-OK lookup is
  refused (`units-currency-integrity` R3). Eval keys drop nil companyfacts entries
  (`_candidates`, `calendar_year_key`), and the v2 fact-store source drops nil rows. The
  lookup and screen never use `pre.negating`; nothing in `src/` negates a value by its
  label. Growth uses `abs(prior)` as the base and a non-positive base is n/m
  (`pct-change-nm`). The grader's loss-word rule reads `-|v|` for income-type metrics.
- **Policy points for the maintainer.** R3's refusal is new policy: a key over a period
  whose filings disagree only in sign is dropped, not keyed (two keys today, both AMD
  FY2021). The alternative, keying the majority or the earliest filing, would need a
  decision in `XbrlRag_Eval_Semantics.md` S1. R2's new exclusion reason `nil` joins
  `screen-universe`'s reasons.
- **Graduated tests.** No graduated spec test feeds a nil row to `_build_result` or a
  screen, or a companyfacts period holding `v` and `-v`; the implementer runs `pytest
  test/spec` to confirm.
