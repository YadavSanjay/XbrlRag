# fsds-reader — a reader for SEC's Financial Statement Data Sets

The bulk **Financial Statement Data Sets** (FSDS) are SEC's own extraction of every
filing's primary financial statements: `sub` (submissions), `num` (values), `pre`
(statement lines: the filer's labels, concepts, order and sign) and `tag` (concepts,
extensions flagged). They are the only SEC-side source for presentation and filer
extensions, and they are independent of our Arelle ingest
([test strategy](../design/XbrlRag_Test_Strategy.md), "The SEC reference data and its
reader").

This change specifies a small reader. It joins and filters the tab-separated tables; it
does not parse XBRL. The risk it carries is misreading a column (period encoding,
`negating`, co-registrants, segments, units), so every rule below is pinned to SEC's
published readme (copied in each quarter's zip as `readme.htm`) or to a statement line
checked by hand in the rendered filing.

**Test-only.** The reader lives in `src/eval/sec_reference/fsds.py` and is used only
when reference fixtures are built. Nothing in the product imports it.

**Data.** The quarters 2022q1–2026q3 are downloaded to `data/cache/sec_fsds/raw/`
(gitignored, with a SHA-256 manifest) and filtered to our 33 filers in
`data/cache/sec_fsds/filtered/{quarter}/{sub,num,pre,tag}.tsv`. A filtered file is the
zip's file with only some rows kept: the header and every kept line are byte-for-byte
SEC's. The spec tests read committed excerpts of those files (below), never `data/`.

## Requirement

### Tables

1. **`load_tables(dirs)`** reads `sub.tsv`, `num.tsv`, `pre.tsv` and `tag.tsv` from each
   directory in `dirs` and returns an `FsdsTables` (a `TypedDict` with keys `sub`, `num`,
   `pre`, `tag`, each a `list[dict[str, str]]`).
   - The format is SEC's (readme §4): UTF-8, tab-delimited, `\n`-terminated lines, the
     first line holding the lowercase column names.
   - Each row is a dict from column name to the raw field string. Columns are found by
     the header, not by position. An empty field is `""`.
   - There is **no quoting**: a `"` inside a field (a label such as `"Other" items`) is a
     literal character, and the row keeps it.
   - Rows come in directory order, then file order. Nothing is deduplicated.
   - A directory missing any of the four files raises `FileNotFoundError`.
2. **`submission(tables, adsh)`** returns the first `sub` row whose `adsh` equals
   `adsh`, or `None`.

### Statement lines (`pre`)

3. **Custom tags.** `is_custom(version, adsh)` is `version == adsh`. The readme: a
   standard tag's `version` names its taxonomy (`us-gaap/2024`, `ifrs/2024`); a custom
   tag's `version` is the accession number of the submission that defined it, and
   `tag.custom` is `1` exactly then.
4. **`statement_lines(tables, adsh, stmt=None)`** returns one `StatementLine` (a
   `TypedDict`) per `pre` row of `adsh` (and of `stmt`, when given):
   - keys `adsh`, `report: int`, `line: int`, `stmt`, `inpth: bool`, `tag`, `version`,
     `custom: bool`, `plabel`, `negating: bool`;
   - `inpth` and `negating` are true for the field `"1"`, false for `"0"`;
   - `custom` is `is_custom(version, adsh)`;
   - sorted by `(report, line)` as integers. Parenthetical lines (`inpth`) are kept.

### Values (`num`)

5. **`num_values(tables, adsh, tag=None, consolidated=True)`** returns one `NumValue`
   (a `TypedDict`) per `num` row of `adsh` (and of `tag`, when given), in table order:
   - keys `adsh`, `tag`, `version`, `custom: bool`, `ddate: str`, `qtrs: int`, `uom`,
     `segments: str | None`, `coreg: str | None`, `value: float | None`;
   - `ddate` is the field's `yyyymmdd` written as ISO `yyyy-mm-dd`;
   - an empty `segments`, `coreg` or `value` field becomes `None`. An empty `value` is a
     nil fact: it never becomes 0;
   - with `consolidated=True`, only rows with `segments is None` **and** `coreg is None`
     are returned. The readme: `segments` holds the axis/member pairs, and a `NULL`
     `coreg` "indicates the consolidated entity". A co-registrant's rows (CEG's
     `ConstellationEnergyGenerationLLC`) carry both a `LegalEntity=` segment and `coreg`.

### Periods and units

6. **`fsds_period(period_start, period_end)`** returns the FSDS encoding
   `(ddate, qtrs)` of an XBRL period. Dates are ISO strings as XBRL writes them (the end
   is the last day of the period, inclusive; **not** the fact store's next-midnight
   end). `period_start` is `None` for an instant.
   - `ddate` is "the end date ... rounded to the nearest month end" (readme): of the
     month end of `period_end`'s month and the month end of the previous month, the one
     closer to `period_end` (`period_end` itself when it is a month end). An exact tie
     raises `ValueError`, since the readme doesn't say which way it goes.
   - `qtrs` is "the count of the number of quarters ..., rounded to the nearest whole
     number; 0 indicates a point-in-time value": `0` for an instant, else
     `round(days * 4 / 365.25)` with `days = (period_end - period_start).days + 1`.
   - `period_start` after `period_end` raises `ValueError`.

   So a 52/53-week year encodes as 4 quarters ending at a month end: AAPL's FY2023
   (2022-09-25..2023-09-30, 53 weeks) is `("2023-09-30", 4)`, COST's FY2023
   (2022-08-29..2023-09-03, 53 weeks) is `("2023-08-31", 4)`, and AVGO's FY2024
   (2023-10-30..2024-11-03) is `("2024-10-31", 4)`.
7. **`fsds_uom(unit)`** maps an XBRL / companyfacts unit to the FSDS `uom`. A per-share
   unit `X/shares` becomes `X` (`USD/shares` → `USD`, `TWD/shares` → `TWD`); any other
   unit is returned unchanged. FSDS records per-share amounts under the currency: across
   all 301,943 `num` rows kept for our filers there is no `/shares` uom, and AAPL's FY2024
   basic EPS (6.11) is a `USD` row, while companyfacts lists it under `USD/shares`.
8. **`period_value(tables, adsh, tag, period_start, period_end, uom=None,
   version=None)`** returns the consolidated value of `tag` for that period:
   - the candidates are `num_values(tables, adsh, tag, consolidated=True)` rows with
     `(ddate, qtrs) == fsds_period(period_start, period_end)`, with `uom ==
     fsds_uom(uom)` when `uom` is given and with that `version` when it is given;
   - among the candidates' non-`None` values: none gives `None`; one distinct value is
     returned; more than one distinct value raises `ValueError` (for example TSM's 20-F
     carries the current year in TWD and as a USD convenience translation, so its
     revenue needs a `uom`).

### Sign and statements

9. **`presented_value(line, value)`** is the value as the statement shows it: `-value`
   when `line["negating"]`, else `value`; `None` stays `None`. The readme defines
   `negating` as "whether the plabel is negating"; the rendered filings confirm the
   direction (AAPL FY2024 cash-flow "Other": the fact is +2,266 and the statement shows
   (2,266); the FY2022 fact is −1,006 and the statement shows 1,006).
10. **`statement(tables, adsh, stmt, period_start, period_end, uom=None)`** returns one
    `StatementValue` (a `TypedDict` with keys `line: StatementLine`, `value: float |
    None`, `presented: float | None`) per `statement_lines(tables, adsh, stmt)` row, in
    that order. `value` is `period_value(tables, adsh, line["tag"], period_start,
    period_end, uom, line["version"])` and `presented` is `presented_value(line,
    value)`. A `ValueError` from `period_value` propagates.
11. **As filed, per accession.** Every function reads only the rows of the accession it
    is given. A prior period restated in a later filing keeps each filing's own value:
    AAPL's FY2022 cash-flow "Other" is −111 in the FY2022 10-K and −1,006 in the
    FY2023 10-K.

### Agreement with companyfacts

12. For a standard tag, the reader agrees with SEC's companyfacts API: every
    consolidated `num` row of a standard tag in the fixtures has a companyfacts entry
    for the same accession (`accn`), taxonomy (`us-gaap/…` → `us-gaap`, `ifrs/…` →
    `ifrs-full`), tag, unit (`fsds_uom` of the companyfacts unit) and period
    (`fsds_period(start, end)`), with an equal value; and `period_value` on that entry's
    period returns that value. Equal means `abs(a - b) <= 1e-9 * max(abs(a), abs(b),
    1)`; FSDS keeps four decimals, companyfacts the XBRL value.

## Acceptance criteria

- **C1**: `load_tables` returns SEC's rows by column name, raw strings, directories
  concatenated in order, `"` kept literally, column order irrelevant, a missing file
  raises `FileNotFoundError`; `submission` finds the row. Oracle: SEC readme §4 and the
  committed fixture files' own bytes.
- **C2**: `fsds_period` gives SEC's `(ddate, qtrs)` for real periods (AAPL 53-week FY2023,
  COST 53-week FY2023, AVGO FY2024, MSFT June, ORCL May, TSM December, CEG instant)
  exactly; `ddate` is a month end within 15 days of the end; 13·k weeks are k quarters;
  an instant is 0; ties and reversed periods raise `ValueError`. Oracle: SEC readme
  (ddate, qtrs) and the SEC-produced `num` rows of those filings.
- **C3**: `statement_lines` returns the filing's lines in `(report, line)` order with typed
  flags; `custom` is true exactly for `version == adsh` and agrees with `tag.custom`
  (MSFT `DepreciationAmortizationAndOther`, AVGO's and ORCL's extension lines, TSM's
  extensions under IFRS); a stmt filter returns a subset. Oracle: SEC readme (pre, tag)
  and the rendered statements, checked by hand.
- **C4**: `num_values(consolidated=True)` drops every row with segments or a
  co-registrant (CEG total assets 2023-12-31: 50,758 consolidated, 50,738 for
  Constellation Energy Generation, LLC); nil values are `None`; order-independent.
  Oracle: SEC readme (num) and CEG's rendered balance sheet.
- **C5**: `period_value` returns the as-filed consolidated value (AAPL FY2024 net sales
  391,035; MSFT FY2024 revenue 245,122; ORCL FY2025 revenues 57,399; COST FY2023 revenue
  242,290; TSM 2024 revenue NT$2,894,307.7 and US$88,268.0 million; AAPL basic EPS 6.11
  given `USD/shares`); `None` when absent; `ValueError` when ambiguous; unaffected by
  added segment or co-registrant rows. Exact (FSDS values are integers or 4-decimal
  amounts). Oracle: the rendered statements, checked by hand, and SEC readme.
- **C6**: `presented_value` negates exactly the negating lines (AAPL cash-flow "Other",
  AAPL capital expenditure, AVGO interest expense, TSM finance costs). Oracle: the
  rendered statements' parentheses, checked by hand, and SEC readme (pre.negating).
- **C7**: `statement` joins lines to values for a period in line order, a restated prior
  period keeps each filing's own value (AAPL FY2022 "Other": −111 vs −1,006), and every
  row equals `period_value`/`presented_value` of its line. Oracle: the rendered
  statements of both AAPL filings, checked by hand.
- **C8**: every consolidated standard-tag `num` row in the fixtures agrees with
  companyfacts (relative 1e-9), and `period_value` reproduces each matched companyfacts
  value. Oracle: SEC companyfacts API (committed excerpt).

## Interface

All in `src/eval/sec_reference/fsds.py`:

- `FsdsTables(TypedDict)`: `sub`, `num`, `pre`, `tag: list[dict[str, str]]`
- `StatementLine(TypedDict)`, `NumValue(TypedDict)`, `StatementValue(TypedDict)` as above
- `load_tables(dirs: Sequence[str | os.PathLike[str]]) -> FsdsTables`
- `submission(tables: FsdsTables, adsh: str) -> dict[str, str] | None`
- `is_custom(version: str, adsh: str) -> bool`
- `statement_lines(tables: FsdsTables, adsh: str, stmt: str | None = None) -> list[StatementLine]`
- `num_values(tables: FsdsTables, adsh: str, tag: str | None = None, consolidated: bool = True) -> list[NumValue]`
- `fsds_period(period_start: str | None, period_end: str) -> tuple[str, int]`
- `fsds_uom(unit: str) -> str`
- `period_value(tables: FsdsTables, adsh: str, tag: str, period_start: str | None, period_end: str, uom: str | None = None, version: str | None = None) -> float | None`
- `presented_value(line: StatementLine, value: float | None) -> float | None`
- `statement(tables: FsdsTables, adsh: str, stmt: str, period_start: str | None, period_end: str, uom: str | None = None) -> list[StatementValue]`

## Notes

**Fixtures** (`test/spec/fsds-reader/fixtures/`): per quarter, the four files with SEC's
header and only the rows the tests need, copied byte-for-byte from the filtered quarter
files; `provenance.json` names each accession, its quarter, the zip URL, the zip's
SHA-256 and size, and the retrieval time. `companyfacts_excerpt.json` holds the
companyfacts entries (`start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`) of the
fixtures' standard tags for the fixture accessions only.

| Filer | Accession | Form, period | Why |
|---|---|---|---|
| AAPL | 0000320193-24-000123 | 10-K FY2024 (52 weeks to 2024-09-28) | full income statement; negated cash-flow lines |
| AAPL | 0000320193-23-000106 | 10-K FY2023 (53 weeks to 2023-09-30) | 53-week year; restated FY2022 "Other" |
| AAPL | 0000320193-22-000108 | 10-K FY2022 | FY2022 "Other" as first filed |
| MSFT | 0000950170-24-087843 | 10-K FY2024 (June FYE) | non-December FYE; custom `DepreciationAmortizationAndOther` |
| ORCL | 0000950170-25-087926 | 10-K FY2025 (May FYE) | custom revenue and cost lines |
| TSM | 0001193125-25-083423 | 20-F 2024 | IFRS (`ifrs/2024`), TWD plus USD convenience values, extensions |
| AVGO | 0001730168-24-000139 | 10-K FY2024 (53 weeks to 2024-11-03) | extension-heavy cost of revenue; negated interest expense |
| COST | 0000909832-23-000042 | 10-K FY2023 (53 weeks to 2023-09-03) | 53-week year whose end rounds back to August |
| CEG | 0001868275-24-000014 | 10-K FY2023 | co-registrant (Constellation Energy Generation, LLC); parenthetical BS line |

**Hand checks.** Each was read in the filing's rendered statement (our local copy of the
EDGAR primary document under `data/storage/sec_edgar/`); the test that uses it names the
line. All amounts are in millions as printed:

| Filing | Statement line | Printed |
|---|---|---|
| AAPL FY2024 | Income statement, "Total net sales", 2024 / 2023 / 2022 | 391,035 / 383,285 / 394,328 |
| AAPL FY2024 | Cash flows, "Other" (operating adjustments), 2024 / 2022 | (2,266) / 1,006 |
| AAPL FY2024 | Cash flows, "Payments for acquisition of property, plant and equipment", 2024 | (9,447) |
| AAPL FY2023 | Cash flows, "Other", 2022 column | 1,006 |
| AAPL FY2022 | Cash flows, "Other", 2022 column | 111 |
| MSFT FY2024 | Cash flows, "Depreciation, amortization, and other", 2024 | 22,287 |
| MSFT FY2024 | Income statements, "Total revenue", 2024 | 245,122 |
| ORCL FY2025 | Income statements, "Total revenues" / "Cloud services and license support", 2025 | 57,399 / 44,029 |
| AVGO FY2024 | Income statements, "Cost of products sold" / "Interest expense", fiscal 2024 | 9,797 / (3,953) |
| COST FY2023 | Income statements, "Total revenue", 53 weeks ended 2023-09-03 | 242,290 |
| TSM 2024 | Income statements, "NET REVENUE", 2024 NT$ / US$ | 2,894,307.7 / 88,268.0 |
| TSM 2024 | Income statements, "Finance costs", 2024 NT$ | (10,495.4) |
| CEG FY2023 | Balance sheets, "Total assets", 2023-12-31, Constellation Energy Corp / Constellation Energy Generation, LLC | 50,758 / 50,738 |

**What the reader does not do.** It does not choose a filing's "main" period or decide
which concept answers a question; callers pass the accession, tag and period. It does not
read `rfile`, `footnote` or the `sub` address columns.

**Known data quirk, not handled.** CEG FY2023 `pre` gives `OperatingIncomeLoss` (report 3,
line 19) the plabel "Gain (loss) on sales of assets and businesses"; the rendered line reads
"Operating income (loss)". Labels are reported as SEC publishes them.
