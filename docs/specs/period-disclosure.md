# period-disclosure — the period a number covers is the period asked for, and its length is said

Area G, nuance N4 ("fiscal periods") of
[`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md). The read-only
investigation of 2026-11-07 (`data/eval/fiscal_periods/report.md`, findings G5–G9, proposed
criteria 8–10) found five ways a figure covers a different period than the question, or a
period whose length is never said:

| # | Finding (corpus, `xbrl_db`, 2026-11-07) | Silent wrong number? |
|---|---|---|
| G5 | `_query_geographic` / `_query_segment` filter `fiscal_period` only and pick `value DESC`, so a 10-Q's year-to-date beats its quarter: geographic Q2 17 of 17 and Q3 8 of 8 return the YTD, segment Q2 99 and Q3 63. FY screens return 26 quarter-length values as the year (MCD FY2023 restructuring members, UPS, META, NVDA, VST). `service.variance_bridge` has no duration filter either. | Yes |
| G6 | 53-week years (AAPL FY2023, AMD FY2022, AVGO FY2024, COST FY2023, HD FY2024, INTC FY2022, MU FY2026, QCOM FY2024) are compared with 52-week years without a word: AAPL FY2024 revenue growth is +2.02% reported, +3.98% per day. 2,027 annual YoY rows have sides 6 or more days apart. | No (reported basis), undisclosed |
| G7 | `mv_derived_metrics` takes the opening balance from `fiscal_year = fy - 1`; a filer's first corpus year has none, so 148 of 526 FY average-based rows divide by the closing balance while `formula` says `avg(...)`. The opening balance is in the same 10-K every time. AAPL FY2022 asset turnover is stored as 1.1179 = 394,328 / 352,755; the stated definition gives 1.1206. | Yes (the definition changes) |
| G8 | `duration_kind` is by length only (`<= 100` quarter, `<= 190` half, `<= 285` nine-month, else annual), so 'annual' holds CEG's 334-day stub, MCD's 731- and 1,096-day program-to-date totals and rows of up to 3,048 days; 'quarter' holds VST's 31-day December dividend and 1-day facts. | Not today (rank resolves them) |
| G9 | No row has `fiscal_period = 'Q4'`, so a Q4 lookup is always NOT_FOUND, although FY − nine-month YTD is derivable (MSFT FY2026 revenue 331,839 − 241,832 = $90,007M). | No (a refusal) |

Store convention, unchanged: `mv_financial_facts.period_end` is SEC's end + 1 day (Arelle's
next-midnight date) for durations and instants; `period_start` is SEC's start. So
`period_end - period_start` is the number of days in the period, inclusive, and an opening
balance (the prior year's balance-sheet date) is stored with `period_end` equal to the year's
`period_start`.

**Not here.** Which filing's value answers when a later filing restates it, and the basis of
YoY pairs, is [`restatement-s1`](restatement-s1.md). Fiscal-year labels of 10-Qs and
`fiscal_calendar` bounds are [`tenq-fiscal-labels`](tenq-fiscal-labels.md). This spec uses the
labels as stored and never decides between filings.

## Requirement

### R1. A duration is classed by its length, with fixed bands (G8)

`duration_class(period_start, period_end)` takes stored dates (`period_end` as stored, one
day after the period's last day). With `days = period_end - period_start`:

| class | days |
|---|---|
| `"quarter"` | 84–119 (12 to 17 weeks: a calendar quarter, a 13- or 14-week quarter, a 12-week retail quarter and its 16- or 17-week fourth quarter) |
| `"half"` | 168–189 (24 to 27 weeks) |
| `"nine_month"` | 252–280 (36 to 40 weeks) |
| `"annual"` | 350–380 (52 or 53 weeks, 365 or 366 days; the eval keys' year band) |
| `"other"` | anything else: stubs, monthly, multi-year and program-to-date totals |
| `"instant"` | `period_start` is None |

The bands are inclusive and do not overlap. Inside them the class equals FSDS `qtrs` (SEC's
"count of the number of quarters represented by the data value, rounded"), i.e.
`round(days × 4 / 365.25)` = 1, 2, 3 or 4. Outside them the class is `"other"` even where
FSDS rounds to a whole number: CEG's 334-day stub (2022-02-01 to 2022-12-31) would round to
4, but it is not CEG's 2022 (that is the 365-day entry of the same filing).

### R2. Segment and geographic screens rank only the requested duration (G5)

`XbrlFactstoreScreen.screen` with a `dimension` (geographic or segment view) ranks only rows
whose `duration_kind` is the kind of the requested period, the one
`XbrlFactstoreLookup._derive_duration_kind(fiscal_period, ytd)` gives: FY → `"annual"`;
Q1 → `"quarter"`; Q2 → `"quarter"`, or `"half"` with `ytd`; Q3 → `"quarter"`, or
`"nine_month"` with `ytd`. `ytd` is read from the step for these views as for the flat one.
The per-filer pick (latest fiscal year, then the largest value) happens among the rows of
that kind only. A filer with no row of that kind is not ranked (it is disclosed as today,
`metric_not_tagged`); a screen with none is NOT_FOUND. Never a year-to-date figure for a
quarter, never a quarter for a year.

The pin belongs in the views' SQL (`duration_kind = %(duration_kind)s`, as `_query_flat`
does), before `DISTINCT ON`; a Python filter after the SQL's per-filer pick is too late, and
the locked fakes of [`sign-and-nil`](sign-and-nil.md) return rows without `duration_kind`.

### R3. Year lengths are disclosed when years are compared (G6)

`year_length_disclosure(current, prior)` takes two stored rows (`period_start`,
`period_end`) and returns:

- `days_current`, `days_prior`: each `period_end - period_start` in days;
- `current_period`, `prior_period`: `(start, last day)` as ISO strings, the last day being the
  stored end − 1 day;
- `differs`: `abs(days_current - days_prior) >= 6`;
- `note`: None when not `differs`; otherwise a sentence naming both fiscal periods' day
  counts (it contains `str(days_current)` and `str(days_prior)`), with the words `53-week`
  when either side has 370–372 days, saying that the change is on the reported basis.

The reported basis stays the default (no per-day restatement of the number).

### R4. Average-based ratios use (opening + closing) / 2, and say so when they cannot (G7)

- `opening_balance(instants, period_start)`: among instant rows (`fact_id`, `value`,
  `period_end`; the caller passes one filing's rows of one concept and unit), the valued row
  whose stored `period_end` equals `period_start` (the prior balance-sheet date + 1 day: the
  current filing's comparative column). Lowest `fact_id` among several; None when there is
  none. Rows at other dates, and nil rows, are never the opening balance.
- `average_based_ratio(numerator, closing, opening)` returns `value`, `basis`,
  `denominator`, `note`:
  - opening given: `denominator = (opening + closing) / 2`, `basis = "average"`, `note` None;
  - opening None: `denominator = closing`, `basis = "closing"`, and `note` says the opening
    balance was not filed and the closing balance was used (it contains `opening` and
    `closing`);
  - `value = numerator / denominator`, or None when the denominator is 0.

### R5. Q4 is FY minus the nine-month year-to-date, on one basis, or refused (G9)

`derive_q4(annual, nine_month)` takes two stored rows (`fact_id`, `concept`, `unit`,
`value`, `decimals`, `period_start`, `period_end`, `accession_no`). It derives when all hold:

1. both values are not None;
2. same `concept` and same `unit`;
3. same `period_start` (one fiscal year, one basis);
4. `duration_class(annual) == "annual"` and `duration_class(nine_month) == "nine_month"`;
5. the remainder `annual.period_end - nine_month.period_end` is a `"quarter"` length
   (84–119 days).

Derived: `status "derived"`, `value = annual.value - nine_month.value`, `unit`,
`period_start` = the nine-month row's stored end (the day after its last day: Q4's first
day), `period_end` = the annual row's stored end − 1 day (ISO strings), `days` = the
remainder, `decimals` = the coarser of the two (the smaller; None only when both are None),
`fact_ids = (annual.fact_id, nine_month.fact_id)`, `accessions` likewise, and a `note` that
says it is derived (contains `derived`). Otherwise `status "refused"`, `value` None and a
non-empty `reason` naming the failed condition.

### R6. Every date these functions emit is a stated date (G9, +1 day)

`year_length_disclosure` and `derive_q4` never emit a stored end: an emitted end is the
stored end − 1 day, an emitted start is the stored start, and `days` is (emitted end −
emitted start) + 1.

### R7. Wiring (not locked; implementer, each with a live test in `test/integration/`)

1. `scripts/ddl/mv/10_mv_financial_facts.sql`: the `duration_kind` CASE mirrors
   `duration_class` (adds `'other'`); rebuild the dependent views. A live test checks that
   every `mv_financial_facts` duration row's `duration_kind` equals
   `duration_class(period_start, period_end)`.
2. Segment and geographic screen SQL as in R2.
3. `src/api/v1/service.py::variance_bridge`: both queries filter `duration_kind` to the kind
   of `fiscal_period` (FY → annual, Qn → quarter).
4. `scripts/ddl/mv/33_mv_yoy_comparison.sql` carries `days_current` and `days_prior`;
   answers that compare fiscal years (a YoY or growth compute, `RevenueGrowthPct` /
   `NetIncomeGrowthPct` screens, the bridge) carry `year_length_disclosure`'s note when
   `differs`.
5. `scripts/ddl/mv/34_mv_derived_metrics.sql`: the opening balance is the current filing's
   instant at the period's start (R4, `opening_balance`), and a closing-only row's `formula`
   says so (for example `Revenue / TotalAssets (closing; opening balance not filed)`),
   mirroring `average_based_ratio`.
6. A lookup with `fiscal_period = "Q4"`: `derive_q4` over the filer's FY `annual` row and its
   Q3 `nine_month` row of the same fiscal-year label and concept; a refusal is NOT_FOUND with
   the reason in the resolution notes.
7. `src/retriever/xbrl/CLAUDE.md`: the duration bands, the screen pin and Q4.

## Acceptance criteria

- **C1**: Duration bands (R1). Stored rows give: AVGO 10-Q `0001730168-26-000054` Americas
  revenue fact 94704 (2026-02-02 to 2026-05-04, 91 days) `quarter` and 94728 (2025-11-03 to
  2026-05-04, 182 days) `half`; MSFT 10-Q `0001193125-26-191507` revenue 52104 (274 days)
  `nine_month` and 50640 (90 days) `quarter`; MSFT 10-K `0001193125-26-323660` 105742 (365
  days) `annual`; AAPL FY2023 12096 (371 days) `annual`; MCD 10-K `0000063908-24-000072`
  restructuring member 215829 (92 days) `quarter`; CEG `0001868275-23-000014` 116516 (334
  days) `other`; MCD 217103 (731) and 218467 (1,096) `other`; VST 135373 (31) `other`; an
  instant `instant`. Exact. Oracle: FSDS `num` `qtrs` of the same accession and value (1, 2,
  3, 1, 4; AAPL 4), the iXBRL context of MCD f-906 (2023-10-01 to 2023-12-31), SEC
  companyfacts start/end for CEG, MCD and VST, and the band table. Properties: for every day
  count in a band the class equals `round(days × 4 / 365.25)`'s kind; shifting both dates by
  the same number of days never changes the class; every day count outside the bands is
  `other`.
- **C2**: Screens pin the duration (R2), over a database faked at `psycopg2.connect` that
  holds the views' rows and applies the SQL's `duration_kind` predicate and `DISTINCT ON`.
  FY2026 Q2 geographic "united states": GOOGL 60,846,000,000 (fact 101632) then TSLA
  13,208,000,000 (111045), never 114,821,000,000 or 23,885,000,000; with `ytd` GOOGL
  114,821,000,000 (101636) and TSLA 23,885,000,000 (111047). FY2026 Q3: MSFT 42,336,000,000
  (52120); with `ytd` 123,823,000,000 (52122). FY2023 segment "other restructuring": MCD's
  only row is quarter-length (35,000,000, fact 215829), so NOT_FOUND with no rows; "u.s.
  market": MCD 62,400,000 (215844, annual). Exact (values are integers from the source).
  Oracle: the iXBRL contexts (GOOGL f-599/f-603, TSLA f-1188/f-1190, MCD f-906), FSDS `num`
  `qtrs` 1 and 3 for MSFT. Properties: adding rows of any other duration kind, with any
  values, never changes the ranked tickers, values or fact ids; the input order of rows does
  not matter.
- **C3**: Year lengths (R3). AAPL FY2024 (14262, 2023-10-01 to stored 2024-09-29) against
  FY2023 (12096, 2022-09-25 to stored 2023-10-01): 364 and 371 days, `differs`, note with
  `364`, `371` and `53-week`, periods ("2023-10-01", "2024-09-28") and ("2022-09-25",
  "2023-09-30"); AAPL FY2023 against FY2022 (10919, 364 days): differs; MSFT FY2026 against
  FY2025 (105742, 104052; 365 and 365): not `differs`, note None. Exact. Oracle: SEC
  companyfacts start/end for the same accessions (AAPL `0000320193-24-000123`,
  `-23-000106`, `-22-000108`; MSFT `0001193125-26-323660`). Properties: swapping the sides
  swaps the day counts and keeps `differs`; `differs` holds exactly when the counts are 6 or
  more apart; shifting all four dates by the same days changes nothing but the periods.
- **C4**: Averages (R4). AAPL 10-K `0000320193-22-000108`: total assets 11022 (352,755M,
  stored 2022-09-25) and 11023 (351,002M, stored 2021-09-26) with the revenue period's start
  2021-09-26 → the opening balance is 11023; asset turnover 394,328M / ((351,002M +
  352,755M) / 2) = 1.12064, basis `average`; with no opening, 394,328 / 352,755 = 1.11785,
  basis `closing`, a note with `opening` and `closing`. Tolerance rel 1e-9: the inputs are
  exact integers (three significant figures, 1.12, cannot tell the two definitions apart).
  Oracle: SEC companyfacts and FSDS `num` (Assets at 2022-09-24 and 2021-09-25, revenue
  2021-09-26 to 2022-09-24) by hand. Properties: opening = closing gives the closing-basis
  value; scaling the numerator and both balances by the same factor leaves the ratio; the
  average-basis ratio lies between numerator / max(balances) and numerator / min(balances);
  rows at any other date are never the opening balance.
- **C5**: Q4 (R5). MSFT FY2026 revenue: 105742 (2025-07-01 to stored 2026-07-01,
  331,839,000,000) minus 52104 (2025-07-01 to stored 2026-04-01, 241,832,000,000) →
  derived 90,007,000,000 USD, period ("2026-04-01", "2026-06-30"), 91 days, decimals −6,
  fact ids (105742, 52104), accessions (`0001193125-26-323660`, `0001193125-26-191507`).
  Exact, to $1M by the facts' decimals. Refused: the discrete Q3 quarter 50640 in place of
  the nine-month row; a nine-month row with another start; another unit; another concept; a
  nil value. Oracle: FSDS `num` (`qtrs` 4 at 2026-06-30 = 331,839,000,000; `qtrs` 3 at
  2026-03-31 = 241,832,000,000) and companyfacts start/end, by hand. Properties: a derived Q4
  plus the nine-month value is the annual value; scaling both values scales Q4; every refusal
  has value None and a reason.
- **C6**: Stated dates (R6). For any stored rows, `derive_q4`'s and
  `year_length_disclosure`'s emitted ends are the stored ends − 1 day, starts are the stored
  starts, and days = emitted end − emitted start + 1. Oracle: the store convention (SEC's end
  + 1 day; AAPL FY2024 stored 2024-09-29, SEC 2024-09-28). Point: the C3 and C5 periods.

## Interface

New module `src/retriever/xbrl/period_disclosure.py` (pure; no I/O):

- `duration_class(period_start: dt.date | str | None, period_end: dt.date | str) -> str`
- `year_length_disclosure(current: Mapping[str, Any], prior: Mapping[str, Any]) -> dict[str, Any]`
  — keys exactly `days_current`, `days_prior`, `current_period`, `prior_period`, `differs`,
  `note`.
- `opening_balance(instants: Iterable[Mapping[str, Any]], period_start: dt.date | str) -> dict[str, Any] | None`
- `average_based_ratio(numerator: float, closing: float, opening: float | None) -> dict[str, Any]`
  — keys exactly `value`, `basis`, `denominator`, `note`.
- `derive_q4(annual: Mapping[str, Any], nine_month: Mapping[str, Any]) -> dict[str, Any]`
  — keys exactly `status`, `value`, `unit`, `period_start`, `period_end`, `days`,
  `decimals`, `fact_ids`, `accessions`, `note`, `reason` (`reason` None when derived; the
  period fields, `days`, `decimals`, `fact_ids`, `accessions` and `note` None when refused).

Dates are accepted as `datetime.date` or ISO `YYYY-MM-DD` strings; emitted dates are ISO
strings.

Changed behaviour, no new signature: `XbrlFactstoreScreen.screen` (R2).

## Notes

- **Fixtures** (`test/spec/period-disclosure/fixtures/`, each source with URL, SHA-256 and
  retrieval date):
  - `store_rows.json`: rows read from `xbrl_db` (read-only, 2026-11-07):
    `mv_financial_facts` rows of the C1/C3/C4/C5 facts, and the `mv_geographic_facts` /
    `mv_segment_matrix` rows the C2 screens read (GOOGL and TSLA FY2026 Q2 US, MSFT FY2026
    Q3 US, MCD FY2023 RestructuringCharges).
  - `companyfacts_excerpts.json`: entries copied unchanged from
    `data/sec_companyfacts/CIK<cik>.json` (`https://data.sec.gov/api/xbrl/companyfacts/CIK<cik>.json`).
  - `fsds_num.json`: FSDS `num` lines (`https://www.sec.gov/files/dera/data/financial-statement-data-sets/<q>.zip`;
    local `data/cache/sec_fsds/`), with the SHA-256 of the zip and of the filtered `num.tsv`.
    FSDS `ddate` is the end rounded to a month end (AVGO's 2026-05-03 is `20260430`).
  - `ixbrl_excerpts.json`: the `ix:nonFraction` elements and their contexts for MCD f-904..906,
    GOOGL f-599/f-603 and TSLA f-1188/f-1190, from the filings' primary documents under
    `data/storage/sec_edgar/`. These segment facts are not in FSDS `num`.
- **Corpus effect of R1** (2026-11-07): the current 'annual' bucket holds rows of 305–3,048
  days for CEG, CRM, CSCO, INTC, MCD, MRK, MU, UNP and VST; 'half' holds COST's 112- and
  119-day fourth quarters (they become `quarter`) and CSCO's 132 days; 'quarter' holds 1- to
  81-day rows. All move to their band or to `other`.
- **ORCL FY2022** (`0001564590-22-023675`) tags its annual country table ("For the Year Ended
  May 31, 2022", US $20,246M) with a 3-month context. Under R2 an FY geographic screen does
  not rank it: the tagged period is a quarter. This is the filer's error, kept faithfully.
- **AVGO geographic members** are not reachable by name today (`geo_code` is
  `AmericasMember`, not a two-letter code), so C2 uses the US rows of GOOGL, TSLA and MSFT;
  AVGO's 91/182-day rows are C1's FSDS oracle.
- **Opening balance and S1.** R4 takes the opening balance from the current filing's
  comparative column, which is the latest filing for that date; `restatement-s1` owns the
  general rule.
- **Out of scope:** `FactCandidate.to_dict()` still emits the stored end (the eval's
  precision grader compares stored ends); changing it is a separate change.
