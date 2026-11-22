# screen-concept-rank — a flat screen ranks each filer on the concept its family means

A cross-filer screen over a raw line item (`_query_flat`) keeps one row per filer with
`DISTINCT ON (ticker) … ORDER BY ticker, fiscal_year DESC, period_end DESC, fact_id DESC`.
`concept_pref_rank` plays no part. Where a filer tags several concepts of one family, the
screen takes whichever fact has the highest id:

- **Oracle FY2026 revenue.** It cites `Revenues` (rank 2, fact 109105, from the geographic
  note), not `RevenueFromContractWithCustomerExcludingAssessedTax` (rank 1, facts
  107265/109053, from the income statement). The value is the same, so the ranking is right,
  but the citation misses the ground truth, and the `aq-1b` FY2026 screen grades PARTIAL in
  every run.
- **Where values differ, the screen reports the wrong number.** Across the corpus
  (2026-10-16), the screen's pick has a different value from the rank-1 pick in **124 of
  1,822** filer × year × family cells.
  - Most are components: `AmortizationOfIntangibleAssets` or `Depreciation` for D&A,
    `OtherComprehensiveIncomeLossNetOfTax` for comprehensive income, `ShortTermBorrowings`
    for debt, `ContractWithCustomerLiabilityCurrent` for deferred revenue.
  - For example, a D&A screen would rank Alphabet FY2024 on depreciation alone (15,311M),
    and Microsoft FY2025 on amortization alone (6,000M).

The lookup already handles both. It prefers the lowest rank, and it never answers with a
component-band concept (ranks 50–99, `docs/specs/concept-rank-integrity.md`). This change
gives the screen the same rules.

## Requirement

1. **Choice per filer.** `select_preferred_concept(rows)` takes the candidate rows the flat
   screen query returns, with several per filer. Each has `ticker`, `fiscal_year`,
   `period_end`, `concept`, `concept_pref_rank`, `fact_id` and `value`. For each ticker it
   picks one row by:
   1. the latest `fiscal_year`;
   2. then the latest `period_end`;
   3. then the lowest `concept_pref_rank`, with `None` last;
   4. then the highest `fact_id`.
2. **Components never rank.** When the picked row's rank is in the component band
   (50 ≤ rank < 100), the filer reports only parts of the measure. It is not ranked; it
   is listed instead.
3. **Result.** The function returns `(kept, component_only)`.
   - `kept`: one picked row per ticker, sorted by ticker; each row is the input dict,
     unchanged.
   - `component_only`: one `{"ticker", "fiscal_year", "concept"}` per excluded filer,
     sorted by ticker.
4. **Screen (not locked).** `_query_flat` keeps `DISTINCT ON (ticker)` but orders it by
   Requirement 1's key: `concept_pref_rank ASC NULLS LAST` goes before `fact_id DESC`. It
   also selects `concept` and `concept_pref_rank`. Dropping `DISTINCT ON` would let the row
   cap cut whole filers. It then applies `select_preferred_concept` to the SQL rows (which
   agrees with the SQL's choice and sets component-only filers apart) before building the
   screen rows, ahead of `select_fiscal_year`.
   - Component-only filers are named in the resolution notes, so the answer can say why
     they are absent.
   - They are not added to `excluded`, which means "has not reported this year".

## Acceptance criteria

- **C1**: point cases (corpus rows, 2026-10-16).
  - Oracle FY2026 revenue rows 107265 (rank 1), 109053 (rank 1) and 109105 (rank 2), all
    67,357M on period end 2026-06-01, give the row of fact 109053.
  - Alphabet FY2024 D&A, whose only row is `Depreciation` (rank 50), is `component_only`
    with concept `…Depreciation` and `kept` is empty.
  - Microsoft FY2025 D&A (`Depreciation` 50, `AmortizationOfIntangibleAssets` 51) is
    `component_only`.
  - A filer with a FY2025 rank-2 row and a FY2024 rank-1 row gives the FY2025 row.
  - A rank-`None` row loses to any ranked row in the same year and period.

  Oracle: Requirement 1–3 by hand.
- **C2**: Invariants for any input:
  - permuting the rows changes nothing;
  - the outputs together cover exactly the input tickers, once each, in ticker order;
  - every kept row is the input's maximum under the ordering key of Requirement 1, and
    its rank is outside 50–99;
  - every `component_only` filer's best row has a rank in 50–99.

  Oracle: the definition (metamorphic and property).

## Interface

- `src.retriever.xbrl.xbrl_factstore_screen.select_preferred_concept(rows:
  Sequence[dict]) -> tuple[list[dict], list[dict]]`

## Notes

- The facts are from the local corpus (public SEC filings), checked 2026-10-16.
- The suite's screen ground truth (`analyst_queries.sql`) already uses rank-1 concepts, so
  the `aq-1b` FY2026 screen should grade CORRECT afterwards.
- **Out of scope:** the geographic, segment and derived screens (other views).
- **Validation after the merge:**
  - the screen cases of the suite (22), with the regression sample;
  - a live D&A screen showing Alphabet and Microsoft listed as component-only, not ranked.
