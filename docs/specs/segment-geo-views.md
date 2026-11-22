# segment-geo-views — exact geography codes and true shares of total in the dimensional views

Area J of [`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md), findings J1, J2 and
J8 of the read-only investigation of `xbrl_db` (2026-11-07, `data/eval/segments_ifrs/report.md`).
The other findings of that area (J3 to J7, J6 IFRS mapping) are separate changes.

Two views give wrong numbers that nothing downstream notices:

- **J1, geography codes.** `scripts/ddl/mv/32_mv_geographic_facts.sql` joins
  `geography_normalization` on `split_part(member_qname, ':', 2)`. Both sides hold URIs
  (`http://xbrl.sec.gov/country/2024:CN`, `http://www.tesla.com/20231231:OtherInternationalMember`),
  so the "local name" it compares is a URI fragment (`//xbrl.sec.gov/country/2024`). No standard
  member ever joins (0 of 119); an extension member joins every row of its namespace and
  `LIMIT 1` without `ORDER BY` keeps an arbitrary one (25 of 73 get another member's code); an
  unmatched member gets the fragment itself as `geo_code` (84 members, 251 rows, 21 filers;
  e.g. Walmart `//www.walmart.com/20240131`, the "corrupt AMD geo code" deferred in
  [breakdown-op.md](breakdown-op.md)). Consumer impact: the FY2023 China revenue screen ranks
  Tesla's "Other international" 29,793M (fact 78192) as China; Tesla's China revenue is 21,745M
  (fact 78189).
- **J2, share of total.** `pct_of_axis_total` (`31_mv_segment_matrix.sql`) and `pct_of_total`
  (`32_`) divide by the sum of every fact on the axis: multi-dimension cells, overlapping levels
  (META's United States inside US & Canada), repeated facts and other units all land in the
  denominator. 995 of 1,326 segment rows and 477 of 827 geography rows are more than 0.1 pp off
  (Adobe FY2025 Subscription shows 19.9%, true 96.4%; Union Pacific FY2024 Mexico 100%, true
  12.4%).
- **J8, consolidated means undimensioned.** No defect found (0 mismatches), but nothing pins the
  rule the share's denominator now depends on: a context that carries only default members is
  the consolidated entity, and any explicit or typed member makes it a part.

## Requirement

1. **Consolidated.** `is_consolidated(dimensions)` is True exactly when the context has no
   non-default dimension. Entries with `is_default` True (Arelle records the default member of
   every axis it applies; `xbrl_db` holds them in `context_dimension.is_default`) are ignored.
   A typed dimension (an entry whose `member` is None) is never default, so a typed-dimension
   fact is never consolidated. An empty list is consolidated. In the views,
   `mv_financial_facts.segment_axis IS NULL` exactly when the fact's context is consolidated
   (today's behaviour, locked).
2. **Geography.** `resolve_geography(segment_member, segment_label, normalization)` gives a
   geographic member its `geo_code`, `geo_name` and `geo_type` from the
   `geography_normalization` rows by **exact QName**:
   - A row's key is its `member_qname` (`namespace:local`, split at the **last** `:`) written in
     the views' QName form, the rule `mv_financial_facts` uses for `segment_member`: a namespace
     starting `http://fasb.org/us-gaap/` becomes `us-gaap`, `http://fasb.org/srt/` `srt`,
     `http://xbrl.sec.gov/dei/` `dei`, `http://xbrl.sec.gov/ecd/` `ecd`,
     `http://xbrl.sec.gov/country/` `country`, `http://xbrl.ifrs.org/taxonomy/` or
     `https://xbrl.ifrs.org/taxonomy/` `ifrs-full`; any other namespace stays as written. So
     `http://xbrl.sec.gov/country/2024:CN` has key `country:CN`, and
     `http://www.tesla.com/20231231:OtherInternationalMember` keeps its URI.
   - The matching rows are those whose key equals `segment_member` exactly (case-sensitive).
     If there is at least one and all of them agree on (`iso_code`, `geo_name`, `geo_type`)
     (the same standard member under several taxonomy years does), the result is that row's
     values: `geo_code` = `iso_code`, None when it is None or empty; `geo_name` = the row's
     `geo_name`, or `segment_label` when that is None or empty; `geo_type` = the row's.
   - Otherwise (no matching row, or matching rows that disagree) the member is **unmapped**:
     `geo_code` None, `geo_name` = `segment_label`, `geo_type` None.
   - The result never depends on row order or on rows with another key, and `geo_code` is never
     derived from the QName's text.
3. **Share of total.** `shares_of_total(facts)` gives every non-consolidated fact its share of
   the consolidated total:
   - The total of a fact is taken from the consolidated facts (Requirement 1) with the same
     `accession_no`, `concept`, `unit`, `period_start` (None for an instant) and `period_end`,
     whose value is not nil. If they all have one value, that is the total. If they differ, the
     ones with the highest precision (`decimals` None, i.e. INF, is highest) decide: one value
     among them is the total, more than one means there is no total.
   - The share is `value / total`. It is None when the fact is nil, there is no total, or the
     total is zero. Other facts never enter it: not the other members of the axis, not
     multi-dimension cells, not facts in another unit or period.
   - Every non-consolidated fact of the input is a key of the result; consolidated facts are
     not. A share is a float.
4. **Wiring (not locked; implementer).**
   - `32_mv_geographic_facts.sql`: join `geography_normalization` per Requirement 2 (normalize
     `gn.member_qname` with the same prefix rule, compare for equality, at most one result per
     member, no `split_part` fallback); `pct_of_total` per Requirement 3.
   - `31_mv_segment_matrix.sql`: `pct_of_axis_total` per Requirement 3 (column name kept);
     `segment_rank` unchanged.
   - In SQL the denominator is the `mv_financial_facts` row with `segment_axis IS NULL` and the
     same `accession_no`, `concept`, `unit`, `period_start`, `period_end`; `NULLIF(…, 0)`.
   - An integration test (`test/integration/`) that, on the fixture filings of this spec, the
     rebuilt views' `geo_code`/`geo_name`/`geo_type`, `pct_of_total` and `pct_of_axis_total`
     equal what `resolve_geography` and `shares_of_total` give for the same rows.
   - Update the view headers, `docs/design/` view reference and `scripts/xbrl/analyst_queries.sql`
     comments that describe the old share. Consumers (`xbrl_factstore_screen.py` geographic
     screens, the breakdown op, the SQL-derived suite) see new codes (EU, APAC, NON-US now
     appear) and new shares: rebuild and diff the suite after the view change (root
     `CLAUDE.md`).

## Acceptance criteria

- **C1**: Consolidated = no non-default member (J8 locked). Eli Lilly's 10-K
  `0000059478-24-000065`: the FY2023 `Revenues` facts whose contexts hold only default entries
  (dozens of Arelle default entries each, no explicit member) are consolidated and all equal FSDS's undimensioned
  value 34,124,100,000; the United States fact 219519 (`country:US`, 21,791,000,000, FSDS
  `Geographical=US`) is not. A typed-dimension fact is never consolidated and never serves as a
  total. Exact. Oracle: FSDS 2024q1 `num.txt` (empty `segments` = consolidated; zip SHA-256 in
  `fixtures/fsds_excerpt.json`); XBRL Dimensions 1.0 (a default member is the absence of the
  dimension). Property: on every fixture fact, `view.segment_axis is None` equals
  `is_consolidated`; adding or removing default entries never changes the answer, and any
  explicit or typed entry makes it False.
- **C2**: Geography codes exact and deterministic. Every geographic member of Meta's 10-K
  `0001326801-25-000017` (FY2024), TSMC's 20-F `0001193125-25-083423` (FY2024) and Tesla's 10-K
  `0001628280-24-002390` (FY2023), resolved against the committed `geography_normalization`
  snapshot, gets exactly the code in the table below. Country members get their ISO 3166-1
  alpha-2 code; unmapped members get `geo_code` None and their own label, never a URI
  fragment. Tesla's only FY2023 `Revenues` member coded `CN` is fact 78189 (21,745M); fact
  78192 (29,793M, "Other international") is `ROW`. Oracle: ISO 3166-1 alpha-2; each member's
  own label against the `geography_normalization` row of the identical QName (by hand); Tesla's
  filing (`f-1645` in context `c-369`, `country:CN`, row "China"; `f-1648` in `c-372`,
  `tsla:OtherInternationalMember`, row "Other international"). Property: permuting the table,
  adding rows under other keys (including the same namespace) or adding agreeing duplicates
  never changes a result; matching rows that disagree, or none, give None; a member with no
  row never gets a code.

  | Filing | Member | geo_code |
  |---|---|---|
  | META | `country:US`, `country:CN` | US, CN |
  | META | `http://www.facebook.com/20241231:USCanadaMember`, `…:RestOfWorldMember` | US-CA, ROW |
  | META | `srt:EuropeMember`, `srt:AsiaPacificMember`, `us-gaap:NonUsMember` | EU, APAC, NON-US |
  | TSM | `country:TW`, `country:US`, `country:CN`, `country:JP` | TW, US, CN, JP |
  | TSM | `http://www.tsmc.com/20241231:EmeaMember`, `…:EuropeMiddleEastAndAfricaMember` | EMEA, EMEA |
  | TSM | `http://www.tsmc.com/20241231:OtherCountryMember`, `…:OthersMember` | ROW, ROW |
  | TSM | `ifrs-full:CountryOfDomicileMember`, `ifrs-full:ForeignCountriesMember` | None, None |
  | TSLA | `country:US`, `country:CN`, `country:DE` | US, CN, DE |
  | TSLA | `http://www.tesla.com/20231231:OtherInternationalMember`, `…:ShanghaiChinaMember` | ROW, CN |

- **C3**: Share of the consolidated total, within 0.1 pp. Adobe's 10-K `0000796343-26-000003`
  FY2025: Subscription 22,904M of 23,769M = 96.36% (facts 171025 and 171423); the cell
  Subscription × Digital Media 17,389M = 73.16%. Union Pacific's 10-K `0000100885-25-000042`
  FY2024: Mexico 3,000M of 24,250M = 12.37% (fact 242744). Meta FY2024 (`0001326801-25-000017`):
  US & Canada 63,207M = 38.42% and United States 59,730M = 36.31% of 164,501M, both kept
  although they overlap; US & Canada, Europe, Asia-Pacific and Rest of World sum to 100%. No
  total, a zero total, a nil fact or conflicting equal-precision totals give None; a less precise
  duplicate total yields to the more precise one. Oracle: FSDS `num.txt` (2026q1 for Adobe,
  2025q1 for Union Pacific and Meta; empty `segments` is the total), the division done in the
  test. Property: adding any other non-consolidated fact, permuting the facts, repeating a total
  or multiplying every value by the same positive factor leaves every other share unchanged; a
  fact in a unit or period with no total gets None.

## Interface

Plain mappings in and plain values out, so the views' rows map onto them directly.

- `src.ingest.sec_edgar.xbrl.segment_geo.is_consolidated(dimensions: Iterable[Mapping[str, Any]]) -> bool`
  - a dimension entry has the keys `axis` (str), `member` (str in the views' QName form, or
    None for a typed dimension) and `is_default` (bool); other keys are ignored.
- `src.ingest.sec_edgar.xbrl.segment_geo.resolve_geography(segment_member: str, segment_label: str | None, normalization: Iterable[Mapping[str, Any]]) -> dict[str, str | None]`
  - a normalization row has the keys `member_qname`, `iso_code`, `geo_name`, `geo_type`
    (the `geography_normalization` columns);
  - the result has exactly the keys `geo_code`, `geo_name`, `geo_type`.
- `src.ingest.sec_edgar.xbrl.segment_geo.shares_of_total(facts: Iterable[Mapping[str, Any]]) -> dict[int, float | None]`
  - a fact has the keys `fact_id` (int), `accession_no`, `concept`, `unit`, `period_start`
    (None for an instant), `period_end`, `value` (`Decimal`, or None when nil), `decimals` (int,
    or None for INF) and `dimensions` (entries as for `is_consolidated`).

## Notes

- **Fixtures** (`test/spec/segment-geo-views/fixtures/`), all retrieved 2026-11-07:
  - `<filer>_fy<year>_revenue(s).json`: every fact of one revenue concept in the filing (all
    contexts) from `xbrl_db` (read-only), with each context's explicit and default
    `context_dimension` rows in the views' QName form and the fact's `mv_financial_facts` row
    (`view`). Each names its accession, EDGAR URL, local copy and that copy's SHA-256.
    Adobe `0000796343-26-000003`, Union Pacific `0000100885-25-000042`, Meta
    `0001326801-25-000017`, Tesla `0001628280-24-002390`, Eli Lilly `0000059478-24-000065`.
  - `<filer>_fy<year>_geo_members.json`: every distinct member on a geographic axis of the
    Meta, TSMC (`0001193125-25-083423`) and Tesla filings, with label and an example fact.
  - `geography_normalization.json`: the whole table (373 rows) as loaded.
  - `fsds_excerpt.json`: the filings' `sub.txt` row and the concept's fiscal-year `num.txt` rows
    (all segments), copied from the SEC quarter zips (URL, SHA-256 and size per zip, matching
    `data/cache/sec_fsds/raw/manifest.json`). FSDS 2024q1 has no `Revenues` rows for Tesla's
    filing, so `tsla_fy2023_filing_excerpt.json` copies the four `ix:nonFraction` elements, their
    contexts and table rows from the filing itself.
  - `xbrl_db` dates a duration end as the next day (2025-01-01 ends fiscal 2024); the functions
    compare dates as given.
- **Codes outside ISO 3166.** Regions and filer groupings use the house codes of
  `src/ingest/util/generate_geography_normalization.py` (`ROW`, `EMEA`, `APAC`, `US-CA`,
  `NON-US`, …). This change fixes how the views read the table, not the table: whether TSMC's
  IFRS `CountryOfDomicileMember` should be mapped, or a label rule refined, is a table change.
- **Out of scope.** One-dimension lookups answered from multi-dimension cells (J3), screens'
  axis and member selection and exclusion reasons (J4, J5, J8 consumers), IFRS family mapping
  (J6), overlapping partitions in the breakdown op (J7).
