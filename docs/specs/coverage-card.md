# coverage-card — which canonical metrics each filer-year tags, and how

v2 plan §4.5 asks for a per-filer-year **coverage card**, and the phase 2 exit
([§8](../design/XbrlRag_v2_Plan.md)) needs "a coverage card exists for every filer-year".
Eval semantics S5 ([`XbrlRag_Eval_Semantics.md`](../design/XbrlRag_Eval_Semantics.md)) needs
it to decide whether a filer may be ranked in a screen. When a filer is missing from a
screen because it tags the metric differently, that is a graded disclosure, not a silent
drop.

Today the raw material sits in `mv_concept_coverage` and `mv_filing_completeness`, and the v2
suite's corpus inventory (`src/eval/suites/v2_sources.py`, `INVENTORY_SQL`) stands in for the
card. The inventory keeps only standard-taxonomy facts and says nothing about *why* a metric
is missing.

## Requirement

1. **The card's metrics** are a committed list of ten. Eight are concept metrics, each read
   from one `concept_mapping` family. Two are axis metrics, both read from the `Revenue`
   family:

   | metric | kind | concept_family |
   |---|---|---|
   | `Revenue` | concept | `Revenue` |
   | `CostOfRevenue` | concept | `CostOfRevenue` |
   | `OperatingIncome` | concept | `OperatingIncome` |
   | `NetIncome` | concept | `NetIncome` |
   | `TotalAssets` | concept | `TotalAssets` |
   | `StockholdersEquity` | concept | `StockholdersEquity` |
   | `CashFromOperations` | concept | `CashFromOperations` |
   | `Capex` | concept | `Capex` |
   | `SegmentAxis` | axis | `Revenue` |
   | `GeographyAxis` | axis | `Revenue` |

2. **The card is a materialized view, `mv_coverage_card`**, in
   `scripts/ddl/mv/42_mv_coverage_card.sql`. It is added to `MANAGED` in
   `scripts/rebuild_mvs.py`, so it is rebuilt with the others after every load.

   Why a view and not a table:
   - It is a pure function of `mv_financial_facts` and `concept_mapping`. As a view it can
     never go stale against a `concept_mapping` reload: the reload runbooks already end with
     the MV rebuild.
   - It needs no new write path or ingest step. The view is "at ingest" because the rebuild
     runs after every load.
   - It is queryable by SQL, which the later metadata screens (plan §5) need.
   - It sits beside `mv_filing_completeness`, the other per-filer-year Layer-4 gate.

   It has one row per **(cik, fiscal_year, metric)**, with a unique index on
   `(cik, fiscal_year, metric)`. The columns are:
   - `cik` (10 digits), `ticker`, `fiscal_year`, `metric`;
   - `status`, which is one of `standard`, `extension`, `component_only`, `absent`;
   - `standard_qnames`, `extension_qnames`, `component_qnames`: `text[]`, never NULL (empty
     when there is no such concept).

3. **The filer-years.** There is a card for every `(cik, fiscal_year)` with a canonical FY
   filing in `mv_financial_facts` (`is_canonical_filing AND fiscal_period = 'FY'`).
   `fiscal_year` is that filing's DEI focus year, as everywhere else. Every card has all ten
   metrics, including the `absent` ones.

4. **Reported, for a concept metric.** A concept is *reported* in a filer-year when the
   canonical FY filing has at least one fact of it that meets all of these:
   - current period (`is_current_period`);
   - undimensioned (`segment_axis IS NULL`);
   - not nil (`value IS NOT NULL`);
   - `duration_kind IN ('annual', 'instant')`.

   Its **rank** for the filer is that of its `concept_mapping` row in the metric's family,
   leaving out deprecated rows. When the concept has a row with `cik_override` equal to the
   filer, that row is used. Otherwise the generic row (`cik_override IS NULL`) is used. A
   concept with no row in the family does not count.

   The concept goes into exactly one tier:
   - `component_qnames` when its rank is in the component band, 50–99;
   - `extension_qnames` when it is outside that band and `is_extended`, which means a
     filer-extension namespace;
   - `standard_qnames` otherwise: a us-gaap, srt or ifrs-full element at a primary rank
     (< 50 or ≥ 100).

   Each array is distinct and ordered by (rank, concept). Concepts are stored as
   `mv_financial_facts.concept` stores them (`<namespace URI>:<local name>`).
5. **Reported, for an axis metric.** The standard axes are:
   - segment: `us-gaap:StatementBusinessSegmentsAxis` and `ifrs-full:SegmentsAxis`;
   - geography: `srt:StatementGeographicalAxis` and `ifrs-full:GeographicalAreasAxis`.

   An axis is *reported* when the canonical FY filing has at least one fact that meets all
   of these:
   - its `segment_axis` is a standard axis of that metric;
   - it is current period, a duration and not nil;
   - its concept has a primary rank in the `Revenue` family, resolved for the filer as in
     item 4.

   `standard_qnames` holds the distinct standard axes found, ordered by name. The other two
   arrays are empty, because an axis cell is only ever `standard` or `absent`.

6. **Status** is decided by the first tier that is not empty:
   - `standard` when `standard_qnames` is not empty;
   - else `extension` when `extension_qnames` is not empty;
   - else `component_only` when `component_qnames` is not empty;
   - else `absent`.

   The view computes it. `classify_cell` states the same rule in Python, and the reader
   fails loud when a row disagrees with it.
7. **The reader** reads through a seam, `CoverageCardSource`. The production source reads
   `mv_coverage_card` read-only, parameterised and time-limited, like `v2_sources`; it is
   added by the implementer. `coverage_card` and `coverage_cards` turn rows into cells and
   validate each card. Each cell gives the status and the qnames of the winning tier, which
   are empty for `absent`.
8. **The empty cells.** A cell is *empty* when its status is `component_only` or `absent`.
   The empty cells are the development cases for the concept families. A report lists them,
   and a CLI prints that report.
9. **The S5 hook** for the v2 suite builder. `screen_eligibility` says, per metric and
   filer-year, whether a filer may be ranked:
   - `standard` and `extension` are rankable, because the card maps the concept to the
     canonical metric;
   - `component_only` is excluded with reason `component_only`;
   - `absent` is excluded with reason `metric_not_tagged`;
   - a filer-year with no card is excluded with reason `period_not_filed`.

   The reasons reuse the v2 suite's exclusion vocabulary. The returned cell is the card entry
   a key cites (S5 "Keys").

## Acceptance criteria

- **C1**: `card_metrics()` returns the ten `CardMetric(metric, kind, concept_family)` of
  requirement 1, in the table's order. Metric ids are unique, and both axis metrics read
  `Revenue`. Oracle: the table in requirement 1.
- **C2**: `classify_cell(standard_qnames, extension_qnames, component_qnames)` returns
  `(status, qnames)`, where `qnames` is the winning tier as a tuple in the given order (`()`
  for `absent`). The status follows requirement 6.

  Point cases:
  - `(["us-gaap:Revenues"], ["nvda:X"], [])` gives `("standard", ("us-gaap:Revenues",))`;
  - `([], ["abc:TotalNetSales"], ["us-gaap:Depreciation"])` gives
    `("extension", ("abc:TotalNetSales",))`;
  - `([], [], ["us-gaap:Depreciation", "us-gaap:AmortizationOfIntangibleAssets"])` gives
    `component_only` with both qnames in order;
  - `([], [], [])` gives `("absent", ())`.

  Properties:
  - it agrees with the rule written out for any input;
  - adding a standard qname always gives `standard`;
  - the extension and component tiers never change a `standard` result.

  Oracle: requirement 6.
- **C3**: `cell_from_row(row)` turns a `CoverageRow` into a `CoverageCell(cik, ticker,
  fiscal_year, metric, status, qnames)`, with `(status, qnames)` equal to `classify_cell`'s.
  It raises `ValueError` when any of these holds:
  - the row's `status` differs from `classify_cell`'s;
  - the metric is not one of `card_metrics()`;
  - an axis metric has non-empty extension or component qnames.

  Oracle: requirements 5–7.
- **C4**: `coverage_card(source, cik, fiscal_year)` returns the filer-year's cells as a dict
  keyed by metric, in `card_metrics()` order. `coverage_cards(source)` returns every card,
  keyed by `(cik, fiscal_year)` in ascending order.
  - A CIK is zero-padded to 10 digits before the source call: `"1045810"` becomes
    `"0001045810"`.
  - `coverage_card` calls `source.rows(cik, fiscal_year)` once. `coverage_cards` calls
    `source.rows(None, None)` once.
  - Rows of other filer-years are ignored.
  - `coverage_card` raises `LookupError` when there is no row for the filer-year.
  - Both raise `ValueError` when a card lacks a metric or repeats one, or when any row fails
    `cell_from_row`.

  Properties:
  - `coverage_cards(source)[k] == coverage_card(source, *k)` for every key `k`;
  - the order of the source's rows does not matter.

  Oracle: requirements 3 and 7, on hand-built rows.
- **C5**: `empty_cells(cards)` returns the cells whose status is `component_only` or
  `absent`, ordered by (cik, fiscal_year, metric in `card_metrics()` order).
  `render_report(cards)` returns one line per empty cell, then one summary line, joined by
  `\n` with a trailing `\n`:
  - each cell line is
    `f"{ticker or '-'}\t{cik}\t{fiscal_year}\t{metric}\t{status}\t{','.join(qnames) or '-'}"`;
  - the summary line is `f"{n} empty cells across {k} of {m} filer-years"`, where `n` is the
    number of empty cells, `k` the number of filer-years with at least one, and `m` the
    number of cards.

  Properties: the report has `len(empty_cells) + 1` lines, and it does not depend on the
  order of the cards. Oracle: requirement 8.
- **C6**: `screen_eligibility(cards, metric, cik, fiscal_year)` returns
  `Eligibility(rankable, reason, cell)` as in requirement 9. The CIK is zero-padded, and
  `cell` is the card's cell, or `None` when there is no card. An unknown metric raises
  `ValueError`. Oracle: requirement 9 and S5.
- **C7**: `cli.main(argv, source, out)` writes `render_report` of the cards to `out` and
  returns 0.
  - `--cik CIK` (zero-padded) and `--fiscal-year YEAR` restrict the report to the matching
    cards; a card must match both when both are given. The summary then counts only those
    cards.
  - With `--strict` it returns 1 when the report has an empty cell.
  - It returns 2, and writes a line starting `no coverage cards` to `out`, when the source has
    no rows or the filters match no card.

  Oracle: requirement 8.
- **C8**: The committed DDL and the rebuild runner match requirement 2. In
  `scripts/ddl/mv/42_mv_coverage_card.sql`, read as text with comments dropped and case and
  whitespace ignored outside string literals:
  - `DROP MATERIALIZED VIEW IF EXISTS mv_coverage_card` comes before
    `CREATE MATERIALIZED VIEW mv_coverage_card`;
  - there is a unique index on `mv_coverage_card (cik, fiscal_year, metric)`;
  - it reads `mv_financial_facts` and `concept_mapping`;
  - it names every column of requirement 2;
  - it contains the four status literals and the four standard-axis literals;
  - it contains the ten `('<metric>', '<kind>', '<concept_family>'[, <non-literal extras>])`
    tuples of requirement 1, for example as a `VALUES` list (a trailing order column is
    allowed), and no other tuple of that shape.

  `scripts/rebuild_mvs.py` lists `"mv_coverage_card"` in `MANAGED`. Property: the checks do
  not depend on re-spacing or re-casing the SQL. Oracle: requirement 2.

## Interface

New, stubbed in the spec PR. A spec PR may only add empty classes, so the four NamedTuples
are declared without fields and their docstrings list the fields. The implementer declares
the fields exactly as listed here, in this order.

- `src.ingest.sec_edgar.coverage.base.CoverageRow` (NamedTuple: `cik: str`,
  `ticker: str | None`, `fiscal_year: int`, `metric: str`, `status: str`,
  `standard_qnames: tuple[str, ...]`, `extension_qnames: tuple[str, ...]`,
  `component_qnames: tuple[str, ...]`). Wherever a row is taken, any 8-sequence in this
  order is accepted; the tests pass plain tuples.
- `src.ingest.sec_edgar.coverage.base.CoverageCardSource` (ABC):
  `rows(self, cik: str | None, fiscal_year: int | None) -> Sequence[Sequence]`, returning
  rows as above. `None` means no filter.
- `src.ingest.sec_edgar.coverage.card.CardMetric` (NamedTuple: `metric: str`, `kind: str`,
  `concept_family: str`)
- `src.ingest.sec_edgar.coverage.card.CoverageCell` (NamedTuple: `cik: str`,
  `ticker: str | None`, `fiscal_year: int`, `metric: str`, `status: str`,
  `qnames: tuple[str, ...]`)
- `src.ingest.sec_edgar.coverage.card.Eligibility` (NamedTuple: `rankable: bool`,
  `reason: str | None`, `cell: CoverageCell | None`)
- `src.ingest.sec_edgar.coverage.card.card_metrics() -> tuple[CardMetric, ...]`
- `src.ingest.sec_edgar.coverage.card.classify_cell(standard_qnames: Sequence[str], extension_qnames: Sequence[str], component_qnames: Sequence[str]) -> tuple[str, tuple[str, ...]]`
- `src.ingest.sec_edgar.coverage.card.cell_from_row(row: Sequence) -> CoverageCell`
- `src.ingest.sec_edgar.coverage.card.coverage_card(source: CoverageCardSource, cik: str, fiscal_year: int) -> dict[str, CoverageCell]`
- `src.ingest.sec_edgar.coverage.card.coverage_cards(source: CoverageCardSource) -> dict[tuple[str, int], dict[str, CoverageCell]]`
- `src.ingest.sec_edgar.coverage.card.empty_cells(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]]) -> list[CoverageCell]`
- `src.ingest.sec_edgar.coverage.card.render_report(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]]) -> str`
- `src.ingest.sec_edgar.coverage.card.screen_eligibility(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]], metric: str, cik: str, fiscal_year: int) -> Eligibility`
- `src.ingest.sec_edgar.coverage.cli.main(argv: Sequence[str] | None = None, source: CoverageCardSource | None = None, out: TextIO | None = None) -> int`

Added by the implementer (not locked):

- `scripts/ddl/mv/42_mv_coverage_card.sql` (requirements 2–6), and `"mv_coverage_card"` in
  `MANAGED` in `scripts/rebuild_mvs.py`.
- A production `CoverageCardSource` that reads `mv_coverage_card` (read-only session,
  statement timeout, parameterised `WHERE (%s IS NULL OR cik = %s) AND ...`). `cli.main`
  uses it when `source` is `None`, and `src/ingest/sec_edgar/coverage/__main__.py` makes
  `python -m src.ingest.sec_edgar.coverage` run `main`.
- An integration test in `test/integration/` (live backend, not a spec test). It recomputes
  each `mv_coverage_card` row's tiers from `mv_financial_facts` and `concept_mapping` by the
  rule of requirements 4–5, and checks that `cell_from_row` accepts every row. This is the
  only check of the view's tiering, because spec tests have no Postgres.
- Docs: `docs/design/XbrlRag_Materialized_Views.md` (the new Layer-4 view), the ingest
  guide's rebuild step, and "18 managed MVs" → 19 wherever it is stated.

## Notes

- **Later step, not in this change: wiring the v2 suite.** `v2_suite._cross_filer` today
  excludes a filer as `metric_not_tagged` when the inventory lacks the metric. The wiring
  change calls `screen_eligibility` for each universe member before keying. That change
  carries its own spec, because it moves the suite and its keys (S5 "a key cites the card
  entry").
- **Snapshot measured on 2026-10-29**, read-only, with the tiering of requirements 4–5. An
  ingest was running, so these numbers are indicative only. The tests do not use them.
  - There are 63 FY filer-years.
  - Every non-standard cell among the eight concept metrics is `absent`:
    - CostOfRevenue: CEG FY2022–25, ORCL FY2022–26, VST FY2022–25;
    - Capex: NVDA FY2022–23;
    - Revenue: TSM FY2025;
    - INTC FY2024: every metric, an ingest in progress.
  - No cell is `extension` or `component_only` today. None of these eight families has a
    component band yet, and `concept_mapping` holds no extension QName. Both tiers become
    reachable as the sibling concept-family work adds components and reviewed extension
    rows (plan §6.1).
  - Revenue on a standard axis: `us-gaap:StatementBusinessSegmentsAxis` in 43 filer-years,
    `srt:StatementGeographicalAxis` in 50.
- **Out of scope:**
  - An **extension axis** (for example Microsoft's own
    `ProductsOrServicesSecondaryCategorizationAxis`). Telling which extension axis is a
    segment or geography axis needs a reviewed mapping that does not exist, so axis cells are
    `standard` or `absent` only.
  - Quarterly (10-Q) cards.
  - Currency, which is S6's own exclusion.
- **Fixtures** are synthetic rows with made-up and public CIKs. They need no DB.
