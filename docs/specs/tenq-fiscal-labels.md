# tenq-fiscal-labels — a 10-Q's fiscal year, quarter and dates come from the filer's fiscal calendar

Area G of the fiscal-period investigation of 2026-11-07 (`data/eval/fiscal_periods/report.md`,
findings G2, G3, G4; its proposed criteria 5-7). Three defects, all PROVEN on the corpus:

- **G2: ORCL's Q1 FY2027 10-Q is filed under fiscal 2026.** Oracle tagged
  `DocumentFiscalYearFocus = 2026` on 0001193125-26-389274 (quarter ended 2026-08-31), while the
  filing reads "In the first quarter of fiscal 2027". companyfacts and FSDS repeat the DEI value.
  `label_filings` guards annual forms only, and a 10-Q takes bare DEI, so all 592 facts of the
  filing are labelled 2026. "ORCL revenue Q1 FY2026" returns 19,345M (June-August 2026); the
  true value is 14,926M (June-August 2025, 0001193125-25-200095). If that earlier 10-Q were
  ingested, both filings would key to one `(cik, fiscal_year, fiscal_period)` and one would
  supersede the other.
- **G3: the registry's quarter is the calendar quarter.** The downloader matches a 10-Q request
  by the calendar quarter of the period-end month (`_period_matches`), and that number becomes
  `filing_registry.fiscal_period`, `fiscal_calendar` and `publish_manifest` keys, while
  `mv_financial_facts` uses DEI. They disagree on 9 of the 28 ingested 10-Qs (AAPL, MSFT, MU,
  NVDA, ORCL). A fiscal quarter that ends in October-December cannot be requested at all
  (the request takes quarter 1-3): 48 of 394 corpus-filer 10-Qs since 2022.
- **G4: `fiscal_calendar` start dates are computed from the end date.** Without DEI
  `DocumentPeriodStartDate` (no corpus filing tags it), the downloader writes end - 12 months + 1
  (or - 3 months + 1). That is wrong for 61 of 150 filings; 53-week years show 365 days. AAPL
  FY2023 is stored as 2022-10-01..2023-09-30; its statements cover 2022-09-25..2023-09-30.

## Requirement

1. **Placing a quarter (pure).** `place_quarter(period_end, annual_periods)` gives the fiscal year
   and quarter of a quarterly period from the filer's annual periods, a mapping of annual
   period end (inclusive, SEC's date) to that year's fiscal-year label. With `q = period_end`:
   - `P` is the latest annual end with `0 < q - P <= 371` days, if any; `E` is the earliest
     annual end with `0 < E - q <= 371` days, if any.
   - The fiscal year is `annual_periods[E]` when `E` exists, else `annual_periods[P] + 1`.
   - The quarter is `n = round((q - P) / 91.3125 days)` when `P` exists, else
     `n = 4 - round((E - q) / 91.3125 days)`.
   - The result is `(fiscal_year, n)` when `1 <= n <= 3`; otherwise, and when neither `P` nor
     `E` exists, it is `None`. A period ending on an annual end is not a quarter, so `None`.
   - The labels are taken as given: a filer that names its year by its start (Home Depot) gets
     that year's own label, never the calendar year of an end date.
2. **A 10-Q's fiscal year is the label of the fiscal year that contains it** (report criterion
   5). In `label_filings` (and so `assign_fiscal_labels`), a `10-Q` or `10-Q/A` takes the year
   `place_quarter` gives against the labels the rule assigned to the same filer's annual
   filings in the call. DEI is only a cross-check: when the placed year differs from the
   filing's DEI, the label is the placed year and a WARNING names the accession, both years and
   the period end. When `place_quarter` gives `None`, the filing keeps today's rule: its DEI,
   else the calendar year of its period end. Annual labels are untouched, and every other form
   (10-KT, 8-K ...) passes through as today. Result: 0001193125-26-389274 is fiscal 2027; the 15
   earlier ORCL 10-Qs keep SEC's `fy`.
3. **One quarter label on every surface** (report criterion 6). `apply_fiscal_labels` also sets
   each 10-Q record's `quarter`, and so `fiscal_period`, to the quarter `place_quarter` gives
   against the filer's labelled annual records in the run; a record it cannot place keeps its
   manifest quarter. `filing_registry`, `fiscal_calendar` and (through the registry)
   `publish_manifest` are all written from these records, so they carry one label. For the 9
   mismatched filings that label equals FSDS `sub.fp` and the filer's own fiscal year (see
   Notes for the oracle). ORCL's two first quarters, 0001193125-25-200095 and
   0001193125-26-389274, key to `(2026, Q1)` and `(2027, Q1)`: distinct supersession groups.
4. **Both fact MVs read the quarter from the registry.** `fiscal_period` in
   `10_mv_financial_facts.sql` and `11_mv_financial_facts_multidim.sql` is
   `fr.fiscal_period`, the same expression in both and named in the multidim MV's `GROUP BY`,
   no longer `dei.fiscal_period_focus`. `publish_gate_clause` joins the MV's
   `(cik, fiscal_year, fiscal_period)` to `publish_manifest`, so both sides then agree.
5. **Every fiscal quarter can be requested.** A 10-Q `FilingRequest(ticker, "10-Q",
   fiscal_year=Y, quarter=n)` means fiscal quarter `n` of the fiscal year that ends in calendar
   year `Y`; the annual request's convention (`fiscal_year` = calendar year of the period end)
   is unchanged. `select_filings` places each 10-Q and 10-Q/A index row with `place_quarter`,
   whose annual periods are the report dates of the company's `10-K` index rows, each labelled
   with its calendar year, and matches `(Y, n)`. An index with no 10-K row near the quarter
   falls back to today's calendar match. `filing_date_floor` of a 10-Q request is January 1 of
   `Y - 1` (a January year end's first quarter ends in April of `Y - 1`); an annual request's
   floor is unchanged. Selection still reads the index only. Results: AAPL `(2026, 1)` is the
   quarter ended 2025-12-27 (0000320193-26-000006), MSFT `(2026, 2)` the quarter ended
   2025-12-31, ORCL `(2026, 2)` the quarter ended 2025-11-30, ORCL `(2027, 1)` is
   0001193125-26-389274.
6. **A filing's statement period (pure)** (report criterion 7).
   `report_period_bounds(instance, form_type)` reads an XBRL instance (`*_htm.xml`, bytes) and
   returns `(start, end)`, SEC's inclusive dates as written in the instance:
   - `end` is the `endDate` of the context that `dei:DocumentType` is reported in;
   - `start` is the `startDate` of a context without `segment` whose duration ends on `end` and
     lasts 350-380 days (both ends counted) for an annual form (`fiscal_labels.ANNUAL_FORMS`) or
     80-100 days for a `10-Q` or `10-Q/A`. A 10-Q's DEI context is often year-to-date (AMZN
     Q2 2026: 2026-01-01..06-30); its statement period is the quarter (2026-04-01..06-30).
   - It returns `None` when the instance has no `dei:DocumentType`, no such context, or the form
     is neither annual nor quarterly. Context order and dimensional contexts never matter.
7. **`fiscal_calendar` carries those bounds.** The downloader's `_extract_period_metadata`
   writes `period_start`/`period_end` from `report_period_bounds` of the saved instance whenever
   it gives a result (DEI `DocumentPeriodStartDate` and the month arithmetic are only the
   fallback). `Layer0Populator._parse_manifest` does the same from the instance named in the
   manifest's `files` (the one ending `_htm.xml`, in the manifest's folder) when that file
   exists, so every existing manifest is corrected at the next registries run without a
   re-download. AAPL FY2023 becomes 2022-09-25..2023-09-30 (371 days), INTC FY2022 starts
   2021-12-26, AMZN Q2 2026 starts 2026-04-01; 0 days off.

**In scope:** requirements 1-7. **Out of scope:**

- Quarterly answer keys: `src/eval/keys` reads annual companyfacts entries only.
- Q4 (FY - 9M), the 53-week YoY disclosure, and `duration_kind` edges: `period-disclosure`.
- Restatements and stale comparatives: `restatement-s1`, `stale-comparative`.
- `mv_financial_facts.period_end` keeps Arelle's exclusive end (SEC's end + 1 day);
  `fiscal_calendar` and `filing_registry` keep SEC's inclusive dates. This change does not touch
  either convention.
- A held-out key or suite, and any re-measurement of accuracy.

## Acceptance criteria

- **C1**: `place_quarter` places the 16 ORCL 10-Qs from fiscal 2022 Q1 (0001564590-21-047772) to
  fiscal 2027 Q1 against ORCL's annual periods: 0001193125-26-389274 is `(2027, 1)` and the other
  15 are SEC's `(fy, fp)`; `label_filings` over ORCL's 10-Ks and 10-Qs gives 2027 for
  0001193125-26-389274 and companyfacts `fy` for every other filing; exact. Oracle: the
  filing's own text ("first quarter of fiscal 2027", "For the quarterly period ended August 31,
  2026"); companyfacts `fy`/`fp` for the rest.
- **C2**: on synthetic 52/53-week and month-end calendars, `place_quarter` returns the quarter
  and label each period was built with (14-week quarters included), shifting every date by
  whole years shifts the label by the same number, the label is the given annual label
  (start-year naming included), a quarter after the last annual end takes `label + 1`, a year
  end or a period far from any annual end is `None`; in `label_filings` a placeable 10-Q's
  label never depends on its DEI, and an unplaceable one keeps its DEI; exact. Oracle: the
  construction, by hand.
- **C3**: `apply_fiscal_labels` over the corpus manifests of AAPL, MSFT, MU, NVDA and ORCL gives
  the 9 mismatched 10-Qs the FSDS quarter and the filer's fiscal year, keeps every 10-K's label,
  keys ORCL's 0001193125-25-200095 and 0001193125-26-389274 apart, and its 10-Q labels do not
  depend on the manifests' `quarter`; exact. Oracle: FSDS `sub` (`period`, `fye`, `fp`; `fy`
  with SEC's known offsets handled, see Notes) and the ORCL filing text.
- **C4**: both fact MVs select `fr.fiscal_period AS fiscal_period`, the same expression, and
  the multidim MV groups by it. Oracle: requirement 4, read from the DDL source.
- **C5**: `select_filings` returns the 10-Q of a fiscal-quarter request for 6 real requests
  (AAPL, MSFT, MU and ORCL quarters ending October-December among them), and for every 10-Q of
  the five filers filed since 2020-09 the request `(fy, fp)` of that 10-Q returns exactly it, in any
  index order; `filing_date_floor` of a 10-Q request is January 1 of `Y - 1`; exact. Oracle:
  companyfacts `fy`/`fp` and the ORCL filing text.
- **C6**: `report_period_bounds` gives AAPL 0000320193-23-000106 2022-09-25..2023-09-30
  (371 days), INTC 0000050863-23-000006 2021-12-26..2022-12-31, AMZN 0001018724-26-000026
  2026-04-01..2026-06-30, ORCL 0001193125-26-101045 2025-12-01..2026-02-28 and
  0001193125-26-389274 2026-06-01..2026-08-31; on synthetic instances it ignores context order,
  dimensional contexts and year-to-date or trailing durations, and returns `None` when no
  statement-length context exists; 0 days. Oracle: companyfacts revenue entries of the same
  accessions (start, end); the construction, by hand.
- **C7**: `_extract_period_metadata` and `Layer0Populator._parse_manifest` give AAPL FY2023
  `period_start` 2022-09-25 and AMZN Q2 2026 2026-04-01 from the saved instance, whatever start
  the manifest holds; 0 days. Oracle: companyfacts revenue entries.

## Interface

- `src.ingest.sec_edgar.fiscal_labels.place_quarter(period_end: date, annual_periods: Mapping[date, int]) -> tuple[int, int] | None`
- `src.ingest.sec_edgar.report_period.report_period_bounds(instance: bytes, form_type: str) -> tuple[date, date] | None`

Behaviour changes, signatures unchanged: `label_filings` / `assign_fiscal_labels` (requirement
2), `apply_fiscal_labels` (3), `select_filings` and `filing_date_floor` (5),
`_extract_period_metadata` and `Layer0Populator._parse_manifest` (7). The result of
`place_quarter` is a plain `(fiscal_year, quarter)` tuple because a spec stub cannot add a
NamedTuple or dataclass.

## Notes

**Fixtures** (`test/spec/tenq-fiscal-labels/fixtures/`), each with its source URL, SHA-256 of the
source file and retrieval date; all are public SEC data, copied from the local caches, never
fetched by a test:

| File | Content | Source |
|---|---|---|
| `companyfacts_filings.json` | One row per 10-K/10-Q accession of ORCL, AAPL, MSFT, MU, NVDA filed since 2020-09-01: form, filed, `fy`, `fp` exactly as companyfacts carries them, and the period end (latest end of its quarter- or year-length durations not after `filed`) | `data.sec.gov/api/xbrl/companyfacts/CIK*.json`, 2026-11-07 |
| `fsds_sub.json` | Verbatim `sub.txt` rows of the 9 mismatched 10-Qs, ORCL 0001193125-25-200095 and NVDA 0001045810-25-000023 | FSDS 2025q1, 2025q3, 2026q1-q3 zips |
| `manifests.json` | The 33 corpus `manifest.yaml` of the five filers, parsed | `data/storage/sec_edgar`, as the downloader wrote them |
| `instances/*.xml` | Verbatim excerpts of 5 XBRL instances (contexts, DEI facts, revenue facts) | EDGAR `*_htm.xml` |
| `companyfacts_periods.json` | Verbatim companyfacts revenue entries of those 5 accessions | companyfacts |
| `orcl_q1_fy2027_text.json` | The cover line and three "first quarter of fiscal 2027" sentences of 0001193125-26-389274 | EDGAR `orcl-20260831.htm` |

**The C3 oracle and SEC's `fy` offsets.** FSDS `sub.fy` is not the filer's year: it is one behind
for NVIDIA, Salesforce and Walmart in older data sets (NVDA 0001045810-25-000023, fiscal 2025,
has `fy` 2024), and it repeats a wrong DEI (ORCL 0001193125-26-389274 has `fy` 2026). So the
tests never key on `sub.fy`. They derive the fiscal year and quarter from `sub.period` (the
balance-sheet date rounded to a month end) and `sub.fye` (the year-end month and day): the year
is the calendar year of the first year end on or after `period`, the quarter is the number of
whole quarters from the preceding year end to `period`. This holds because Apple, Microsoft,
Micron, NVIDIA and Oracle all name a fiscal year after the calendar year it ends in, which the
fixture loader checks against every 10-K's companyfacts `fy`. The loader also checks that the
derived quarter equals `sub.fp` for every row, that the derived year equals `sub.fy` except for
ORCL 0001193125-26-389274 (the DEI error) and NVDA 0001045810-25-000023 (the offset), and that
the derived year of 0001193125-26-389274 is the 2027 of its own text. A fixture that broke any
of these would fail at load, not pass silently.

**A second DEI slip, outside the fixtures.** NVIDIA's 10-Q 0001045810-20-000147 (quarter ended
2020-07-26, its fiscal 2021 Q2: the next 10-Q, ended 2020-10-25, is fy 2021 Q3) carries companyfacts
`fy` 2020. The fixtures start at filings of 2020-09-01 so that no test keys on it without an
oracle of NVIDIA's own text; requirement 2 would label it 2021. Pre-corpus, so no store effect.

**Why the 371-day window and 91.3125.** A 53-week year is 371 days, so every quarter end lies
within 371 days of both its year's ends. 91.3125 is a quarter of 365.25 days; real quarter ends
lie within 7 days of `n` quarters from the prior year end (13 or 14 weeks, or 3 calendar months),
far from the rounding boundary at 45 days. Placing by the prior year end first makes a 14-week
first quarter of a 53-week year (AAPL fiscal 2023 Q1, 98 days) quarter 1.

**Consumers that come right with the labels.** `entity_extractor` date bounds and
`mv_fiscal_period_bounds` (from `fiscal_calendar`); `publish_gate_clause` (MV vs
`publish_manifest`); `_resolve_superseded_by` and `stale_fiscal_calendar_keys` (record keys);
point-in-time lookups that pair a registry accession with a DEI quarter (today a false
NOT_FOUND for those 9 filings). The `fiscal-label-alignment` retired tests of requirement 6
("a 10-Q keeps its own tag even when annual filings exist") and the `ingest-multidoc-ixbrl`
retired floor test of a 10-Q request change in this spec PR, each with a comment naming this
change; the other retired tests of both still hold under the rules above.

**Rollout, for the operator; nothing here is done by the spec.** Update
`configs/pipelines/sec_edgar_downloads.yaml`: its 10-Q entries are calendar quarters today (the
file's NOTE says so) and must name fiscal quarters (NVDA `(2026, 2)` becomes `(2027, 1)`, AAPL
`(2026, 1)` becomes `(2026, 2)`, ...) so that the same filings are selected; the
`{TICKER}/{YEAR}/10-Q/Q{n}` folders of existing downloads stay where they are. Then run
registries (labels, quarters, bounds and stale rows), `python scripts/rebuild_mvs.py`, the
promotion gate over all filings, and rebuild and diff `sql_derived_v1.json` and the v2 suites.
Chunks of 0001193125-26-389274 carry `fiscal_year` in their payload and the `FY{label}` text
prefix, so that filing is re-chunked and reloaded once the vector load is idle. Check:

```sql
SELECT accession_no, fiscal_year, fiscal_period FROM filing_registry
 WHERE form_type = '10-Q' ORDER BY cik, period_end;          -- AAPL -26-000013 is (2026, Q2) ...
SELECT fr.accession_no FROM filing_registry fr JOIN mv_dei_metadata d USING (accession_no)
 WHERE fr.form_type = '10-Q' AND fr.fiscal_period <> d.fiscal_period_focus;  -- expect no rows
SELECT period_start, period_end FROM fiscal_calendar
 WHERE cik = '0000320193' AND fiscal_year = 2023 AND fiscal_period = 'FY';    -- 2022-09-25
```

**Implementer, also do:** update the NOTE in `sec_edgar_downloads.yaml`, the `FilingRequest`,
`_period_matches`, `_extract_period_metadata` and `_write_manifest` docstrings, the
`fiscal_period` comment in both MV files, and `docs/guides/xbrl-flow/03-data-download.md` where it
describes 10-Q requests; extend `test/unit/test_fiscal_labels_logging.py` for the new WARNING. Run
all of `test/spec`, not only this change's tests.

**No held-out filer** appears in this spec's tests or fixtures; no held-out set was read.
