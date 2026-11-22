# screen-reporting-currency — a screen never ranks a filer on a convenience translation

The dev baseline (`eval-9a99afc97d4b`, `eval-0051272815db`, 2026-11-04) lost 19 screen cases to
one filer. The flat screen keeps only `unit = 'USD'` rows, and TSMC reports in TWD but also tags
a USD convenience translation for the latest period. So the screen ranks TSMC on that
translation (FY2022 capex: USD 35,231,800,000, fact 80152, beside TWD 1,082,672,100,000). The
answer key excludes TSMC under S3/S5 ("a filer that does not report in USD is excluded and
named"; [`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md)). Every filer below
TSMC is then ranked one place too low, which breaks all 18 failing covered-universe
peer-relative cases ("META is not ranked 3": the answer said 4th).

The lookup already knows a filer's reporting currency
([`reporting-currency-coverage`](reporting-currency-coverage.md), `choose_lookup_unit`): among
the units at the latest period, the one tagged at the most distinct period ends. The screen
applies the same rule.

This change builds on [`screen-universe`](screen-universe.md): the exclusion is reported in that
change's disclosure. Implement it after `screen-universe` is implemented.

## Requirement

1. **The reporting currency of a screened figure.** For each filer the flat screen would rank,
   its observations are the `(unit, period_end)` pairs of every row the flat screen's query
   would consider for that filer and fiscal year, in any unit and at any period of the filing
   (comparatives included). Its reporting currency is the unit `choose_lookup_unit(observations)`
   returns with no requested unit.
2. **A filer whose reporting currency is not USD is not ranked.** When that unit is a currency
   other than USD, the filer is left out of the ranking, even though it has a USD row. Its
   exclusion is `{"ticker": <ticker>, "reason": "currency", "currency": <unit>}`. When the rule
   finds no single unit (None), or the filer has no observations, the filer is ranked on its USD
   row as today. A filer whose reporting currency is USD is unaffected.
3. **The exclusion is disclosed.** It is one of the given exclusions of `screen-universe`'s
   disclosure, so it appears in `ScreenResult.to_dict()["disclosure"]["exclusions"]` and is
   stated as `TSM (reports in TWD, not USD)`. Ranks of the remaining filers run 1..n without a
   gap. The existing `excluded` key (filers with only an older year) is unchanged.
4. **Scope.** The flat view (`mv_financial_facts`, `dimension` `"NONE"`, a value metric). The
   geographic, segment and derived screens are unchanged.

## Acceptance criteria

- **C1**: `select_reporting_currency(rows, observations)` returns `(kept, excluded)`: `kept` is
  the rows (input order) whose ticker has a USD reporting currency, no single reporting
  currency, or no entry in `observations`; `excluded` is one
  `{"ticker", "reason": "currency", "currency"}` per other ticker, sorted by ticker. Point
  cases:
  - TSMC FY2022 capex (TWD at 2021-01-01, 2022-01-01 and 2023-01-01; USD at 2023-01-01) is
    excluded with `TWD`;
  - a USD-only filer is kept;
  - a filer with two currencies at its latest period with equal coverage is kept;
  - a filer absent from `observations` is kept.

  Oracle: the stored facts for fact ids 80149–80152 (Notes); the reporting-currency rule.
- **C2**: For any rows and observations, the result agrees with the rule written out in the
  test (latest period, units at it, most distinct period ends, a tie is no unit). Kept and
  excluded tickers partition the rows' tickers. Properties: the order of the observations does
  not matter; repeating an observation changes nothing; adding an earlier period end for a
  filer's reporting currency never changes the result. Oracle: Requirement 2;
  `reporting-currency-coverage` C2–C3.
- **C3**: `XbrlFactstoreScreen.screen(step)` for a flat FY2022 capex screen, over a fake database
  (the I/O boundary), does not rank TSM. It ranks AMZN, GOOGL, META, INTC, MSFT with ranks 1–5
  (Notes), and its disclosure lists `{"ticker": "TSM", "reason": "currency", "currency": "TWD"}`.
  A peer-relative reading follows: META is rank 3. Metamorphic: removing TSM's TWD observations
  makes TSM ranked again at its USD value; a store where every filer reports only in USD gives
  the same rows as before this change. Oracle: Requirements 1–3 applied by hand to the fake
  store; the answer key's exclusion of TSM for currency in the frozen v2 dev suite.

## Interface

- `src.retriever.xbrl.xbrl_factstore_screen.select_reporting_currency(rows: Sequence[dict], observations: Mapping[str, Sequence[tuple[str | None, str | None]]]) -> tuple[list[dict], list[dict]]`
  (`observations`: ticker → `(unit, period_end)` pairs, period ends as ISO dates)
- Changed behaviour, no new signature: `XbrlFactstoreScreen.screen` (flat view).

## Notes

- **Stored facts (xbrl_db, read-only, 2026-11-04).** TSM (CIK 0001046179) FY2022 `Capex`,
  `ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`, canonical
  filing `0001193125-23-107214`: fact 80149 TWD 507,238,700,000 at 2021-01-01; 80150 TWD
  839,195,700,000 at 2022-01-01; 80151 TWD 1,082,672,100,000 at 2023-01-01 (current); 80152 USD
  35,231,800,000 at 2023-01-01 (current). Period ends are the store's next-midnight dates;
  the rule only compares them.
- **C3 fake store (FY2022 capex, USD, from the baseline traces):** AMZN 63,645,000,000; TSM
  35,231,800,000 (plus the three TWD observations above); GOOGL 31,485,000,000; META
  31,431,000,000; INTC 24,844,000,000; MSFT 23,886,000,000.
- **Fake database contract** (so the implementation's queries are answered sensibly):
  - a query mentioning `entity_master` gets the roster (`cik`, `ticker`);
  - a query on `mv_financial_facts` whose text has a USD predicate (regex
    `unit\s*=\s*'USD'`) gets one current-period USD row per filer at the latest fiscal year
    <= `params["fy"]` (the flat screen's query);
  - any other query on `mv_financial_facts` gets every observation row of each filer at that
    year, in every unit and period (`ticker`, `cik`, `fiscal_year`, `unit`, `period_end`,
    `value`, `concept_pref_rank`, `fact_id`).

  So the observation query must not carry the USD predicate. The graduated screen fakes answer
  every SQL with USD rows whose `period_end` is None, for which `choose_lookup_unit` returns
  None, so they still rank every filer.
- **Out of scope:** the geographic and segment views, which also filter on USD; a filer there
  without a USD row is a `metric_not_tagged` exclusion under `screen-universe`. Derived ratios
  are currency-neutral.
