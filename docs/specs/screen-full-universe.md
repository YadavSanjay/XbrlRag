# screen-full-universe — a screen ranks every filer of its universe, not the first 50

`XbrlFactstoreScreen` runs one SQL per view (`mv_financial_facts`, `mv_derived_metrics`,
`mv_geographic_facts`, `mv_segment_matrix`). Each query ends with

```sql
SELECT DISTINCT ON (ticker) ... ORDER BY ticker, fiscal_year DESC, ... LIMIT %(limit)s
```

with `limit = DEFAULT_SCREEN_LIMIT = 50`. `DISTINCT ON (ticker)` makes the query return one
row per filer, ordered by **ticker**, so the `LIMIT` keeps the 50 alphabetically first filers
and drops the rest before anything is ranked. The corpus has 49 filers today; the S&P 100
waves take it past 50. From then on, with no error and no warning:

- "top N" and "bottom N" are taken from the first 50 tickers, not from the universe;
- a sum, median or count over the screen's rows covers only the first 50 tickers;
- the disclosure ([`screen-universe`](screen-universe.md)) reports each dropped filer as
  `metric_not_tagged` ("metric not reported"), a false statement about that filer.

The evaluation keys (`src/eval/suites/v2_suite.py` `_cross_filer`, the `sql_derived` screen
templates) rank the full universe, so the defect would show up only as unexplained screen
failures. This change removes the silent cap.

## Requirement

1. **No row cap before ranking.** No per-view screen query limits the number of filers it
   returns. Every filer of the universe that has a row is considered by every later filter
   (fiscal year, concept preference, currency, units) and, if it passes them, is ranked.
2. **Aggregates cover the full universe.** With `top_n` null and no threshold, `rows` holds
   one row per ranked filer, every ranked filer, so a sum, median or count taken over `rows`
   is over the full ranked set. `disclosure["ranked"]` lists every ranked filer in rank order.
3. **Top and bottom N are taken after ranking the full set.** `order: "desc"` with `top_n=k`
   returns the `k` largest values of the full ranked set; `order: "asc"` the `k` smallest.
   `disclosure["ranked"]` stays the full ranking (as in `screen-universe` Requirement 5).
4. **Exclusions stay named and true.** A member that is not ranked carries the reason of the
   filter that removed it (`period_not_filed` with its latest year, `component_only`,
   `currency`, `unit_mismatch`, `not_in_corpus`); `metric_not_tagged` only for a roster member
   with no row at all. Position in the alphabet never removes a filer.
5. **The result does not depend on ticker names.** Which tickers hold which values does not
   change the ranked value sequence: renaming or permuting the filers permutes the ranked
   tickers and nothing else.
6. **A result-size cap is explicit and reported.** `XbrlFactstoreScreen(row_limit=None)` is
   the default: no cap. A caller may pass `row_limit=k` (a positive int). The cap is applied to
   the **returned rows after ranking** (after `top_n` and `min_threshold`), never to a query.
   When it removes rows, `ScreenResult.to_dict()["row_cap"]` is
   `{"limit": k, "rows_before_cap": m, "returned": k}`, where `m` is the number of rows before the cap
   (after `top_n`), and `disclosure["ranked"]` still lists all ranked filers. When the cap
   removes nothing (or there is none), `to_dict()` has no `row_cap` key.

## Acceptance criteria

All criteria are checked through `XbrlFactstoreScreen.screen(step)` over a fake database (the
I/O boundary; Notes) holding more than 50 filers, for each of the four views: flat
(`dimension: "NONE"`), derived (`metric_kind: "derived:NetMarginPct"`), geographic
(`dimension: {"geography": "China"}`) and segment (`dimension: {"segment": "cloud"}`).

- **C1**: Every filer with a fiscal-year-`fy` row is ranked: with `top_n` null, the tickers of
  `rows` and `disclosure["ranked"]` are, in order, the filers sorted by value descending, all
  of them; filers whose latest year is older are `period_not_filed` exclusions with that year,
  and roster filers with no row are `metric_not_tagged`, whatever their position in the
  alphabet. Point case: 120 filers, the largest values on tickers past the 50th. Property: any
  51 to 150 filers with distinct values. Oracle: Requirements 1 and 4; sorting computed in
  the test.
- **C2**: Aggregates over `rows` equal those over the full universe: the count is the number
  of fiscal-year-`fy` filers, the sum is their sum and the median their median, within a
  relative 1e-12. Point case: 120 filers with values 1..120 (×1e6): count 120, sum 7,260e6,
  median 60.5e6. Property: any 51 to 150 filers. Oracle: Requirement 2; `math.fsum` and
  `statistics.median` in the test.
- **C3**: `top_n=k` with `order: "desc"` returns the `k` largest of the full set, and with
  `order: "asc"` the `k` smallest, ranked 1..k, with `disclosure["ranked"]` the full ranking.
  Point case: 120 filers, top 5 and bottom 5 sit on tickers past the 50th. Property: any 51 to
  150 filers, any `k` from 1 to 10. Oracle: Requirement 3; sorting in the test.
- **C4**: Permuting which filer holds which value leaves the ranked value sequence unchanged
  and equal to all the values sorted descending. Point case: 120 values assigned in ticker
  order and in reverse ticker order. Metamorphic property: any 51 to 150 values and any
  permutation of them. Oracle: Requirement 5.
- **C5**: `row_limit=k` caps the returned rows after ranking and reports it: `rows` are the `k`
  top-ranked of the full set, ranks 1..k, `to_dict()["row_cap"] == {"limit": k,
  "rows_before_cap": m, "returned": k}` (`m` the row count after `top_n`) and
  `disclosure["ranked"]` is the full ranking of every ranked filer; with `row_limit=None` (the
  default) or `k >= m`, `to_dict()` has no `row_cap` key. Point cases: 120 filers with
  `row_limit=10`; `top_n=20` with `row_limit=10` (`rows_before_cap` 20); `row_limit=None` and
  `row_limit=200` (no key). Property: any 51 to 150 filers, any `k` from 1 to `m - 1`. Oracle:
  Requirement 6.

## Interface

No new functions. Changed behaviour of existing interfaces:

- `src.retriever.xbrl.xbrl_factstore_screen.XbrlFactstoreScreen(db_conn_params: dict | None = None, row_limit: int | None = None)`:
  the default becomes no cap, and the cap moves from the SQL to the ranked rows.
- `XbrlFactstoreScreen.screen(step: dict) -> ScreenResult`: Requirements 1–6.
- `ScreenResult.to_dict() -> dict`: a `row_cap` key when, and only when, Requirement 6's cap
  removed rows.

## Notes

- **The fake database** (test/spec/screen-full-universe/conftest.py) answers the publish
  probe (`to_regclass`) with "absent", `entity_master` with the roster, and every other query
  like Postgres answers `SELECT DISTINCT ON (ticker) ... ORDER BY ticker, ... LIMIT n`: one
  row per filer, the row of its latest fiscal year <= `fy`, in ticker order, **truncated to
  the query's `LIMIT`** when the SQL has one (`LIMIT %(name)s` or a literal). Honouring the
  `LIMIT` is what makes the fake faithful to the database at this boundary; it ignores every
  other predicate. Rows carry the columns of all four views; units are `USD`, derived rows
  carry no component fact ids (so no unit query is issued and their unit stays None, which is
  ranked).
- **Data**: synthetic tickers `F001`..`F150` and synthetic values; nothing from `data/`.
- Out of scope, recorded for the record: `configs/eval/sql_derived_templates.yaml` template
  `aq-8b-multidim-lookup` keeps `ORDER BY 1 LIMIT 40` in its `combo` enumeration: it samples which
  single-filer lookup cases to generate, deterministically, and feeds no screen or aggregate.
  The `frontier_sec_tools` toolbox (`src/eval/sec_tools/`) caps pages explicitly and returns
  `next_offset`, which this spec does not change.
