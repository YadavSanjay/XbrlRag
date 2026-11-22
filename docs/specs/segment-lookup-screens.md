# segment-lookup-screens — a lookup or screen answers the member asked, or says why it cannot

Area J of the 2026-11-07 segments/IFRS investigation found three consumer defects in how a
one-dimension lookup and the segment and geographic screens pick a fact. Each produces a
confident wrong number or a false reason, with real citations:

- **J3: a one-dimension lookup answers with a cell of a two-dimension table.** The flat view
  `mv_financial_facts` files a multi-dimension fact under its *first* non-default dimension only.
  A lookup for one member therefore finds a cell that also carries another dimension, and
  answers with it as if it were the member's total. Eli Lilly FY2023 (10-K
  0000059478-24-000065), Revenue `{product: Trulicity}`, answers **OK 1,699,200,000**. That is
  fact 219336, the Trulicity × Non-US cell. FSDS 2024q1 has US 5,433,300,000 + Non-US
  1,699,200,000 = 7,132,500,000. The US cell (fact 219333) lists its geographic dimension first,
  so the flat query never sees it. The lookup still earns the +0.2 dimension credit, so the
  strict part is reported at confidence 0.8.
- **J4: the segment screen matches `LIKE '%hint%'` on any axis, keeps multi-dimension cells,
  and ranks each filer on its largest match.** FY2024 Revenue:
  - "other" ranks Coca-Cola at 99,043M. That fact is on
    `EquityMethodInvestmentNonconsolidatedInvesteeAxis`: revenue of KO's equity-method
    investees, not KO's. It also ranks Netflix at 124M, a reclassification out of AOCI.
  - "cloud" ranks Microsoft on its secondary-categorisation axis "Microsoft Cloud" (137,400M,
    100%), and Salesforce on "Service Cloud" alone, the largest of its three clouds.
  - "international" ranks Lockheed Martin on `MajorCustomersAxis`.
- **J5: the geographic screen gives false exclusion reasons and cannot resolve Europe.** FY2024
  China revenue:
  - TSMC reports China 331,673,300,000 TWD (fact 89572), but the query keeps USD rows only,
    so TSMC is reported as `metric_not_tagged` ("metric not reported").
  - NVIDIA reports "China Including Hong Kong" for FY2024. Its latest `CN` row is FY2022, so it
    is reported as `period_not_filed` ("no FY2024 figure").
  - `GEO_NAME_TO_CODE` maps "europe" and "emea" to `EU`, a code no row carries. Every Europe or
    EMEA screen is NOT_FOUND.

## Definitions

- **Member key**: `member_label_key` of [segment-label-match](segment-label-match.md).
  Lowercase, `[member]` removed, `&` read as `and`, words of `[a-z0-9]` joined, one trailing
  `segment`/`segments`/`member` dropped. `Europe Segment [Member]` → `europe`.
- **Compact label**: the same without dropping the trailing word.
- **A row** is a row of the screen's view (`mv_segment_matrix`, `mv_geographic_facts`). It
  carries one fact, with that fact's first non-default dimension as `segment_axis` /
  `segment_member` / `segment_label`.
- **A single-dimension fact** has exactly one non-default dimension
  (`mv_financial_facts.has_multiple_dimensions` is false).
- **A cell** is a fact with two or more non-default dimensions.
- **Business axes**: the axes whose local name is `StatementBusinessSegmentsAxis` or
  `ProductOrServiceAxis`.
- **Neutral dimension**: `is_neutral_dimension` of [multidim-recovery](multidim-recovery.md).

## Requirement

### 1. One-dimension lookup (J3)

A lookup step with exactly one entry in `dimensions` answers OK only with one of these:

- **(a) A fact for the member itself.** The answer is a fact whose only non-default
  dimensions are the requested member and, possibly, neutral dimensions. This is today's
  behaviour for single-dimension facts and for the neutral-member recovery of multidim-recovery.
  When such a fact exists, no cell is used, and no cell counts as a rival value.
- **(b) A stated sum of cells.** The member is reported only in cells over one other axis. The
  filer's own single-dimension facts on that axis, at the same concept, unit and period, sum to
  its undimensioned total, which shows that the members partition the whole. The answer is then
  the sum of the member's cells over every member of that partition:
  - `value` is the sum;
  - `FactLookupResult.sum_of` (and `to_dict()["sum_of"]`) is the list of the summed facts' ids;
  - `citation` is the first of them;
  - a `resolution_notes` entry states the sum and the axis.

  An answer that is not a sum has `sum_of` None, and `to_dict()` then has no `sum_of` key.

Otherwise the lookup refuses: status `UNRESOLVED_DIMENSION` or `NOT_FOUND`, `value` None, and a
note that names the cells found. A cell is never an OK answer for the member alone, and it
earns no dimension credit. Which dimension the view happens to list first never changes the
result.

The two-or-more-dimension path (`len(dimensions) >= 2`) is unchanged.

### 2. Segment screen: which rows may rank (J4)

A screen step whose `dimension` has a `segment`, `product` or `business` key carries a hint `h`.
It ranks only rows that satisfy all of these:

1. the row is on a business axis;
2. its fact is a single-dimension fact;
3. its fiscal year is the step's `fy` (today's rule, [screen-exact-fiscal-year](screen-exact-fiscal-year.md));
4. its label **matches** `h`. The match is **exact** when the label's member key equals `h`'s
   member key. It is a **containment** match when `h`'s member key is a substring of the label's
   compact form and the match is not exact.

### 3. Segment screen: one value per filer

For each filer, the candidate rows are those of Requirement 2.

- If any candidate is an exact match, only the exact candidates count. Otherwise all
  containment candidates count.
- **Ranked:** the counted rows carry one member key and one value. The filer is ranked on that
  value. Its row has `member_match` `"exact"` or `"contains"`, and `label` the member label.
- A `"contains"` row is stated: `resolution_notes` has an entry that names the ticker and the
  label.
- Never the largest of several members: rows with two or more member keys, or two or more
  distinct values, are not ranked (Requirement 4).

### 4. Exclusion reasons (J4, J5)

Every universe member that is not ranked has exactly one exclusion in `disclosure["exclusions"]`
(and in `excluded` where today's code lists it). The reason is the first that applies, in this
order:

| # | Reason | When | Extra keys |
|---|---|---|---|
| 1 | `currency` | its matching fiscal-year rows are in a unit other than USD | `currency` (the first such unit, sorted) |
| 2 | `ambiguous_member` | Requirement 3 found two or more member keys or values | `labels`: the counted rows' labels, sorted and de-duplicated |
| 3 | `multi_dimension_only` | (segment and region screens) the member matches only cells at the fiscal year | — |
| 4 | `different_member` | (geographic screens) the filer reports a related but different member at the fiscal year (Requirement 5) | `labels`: those members' labels, sorted and de-duplicated |
| 5 | `period_not_filed` | it has matching rows only for earlier fiscal years | `latest_fiscal_year` |
| 6 | `member_not_found` | it has fiscal-year rows in the screen's view for the concept family, none matching | — |
| 7 | `metric_not_tagged` / `not_in_corpus` | as today (screen-universe) | — |

"Matching" means the screen's own match: Requirement 2 items 1 and 4 for a segment screen, and
Requirement 5 for a geographic one. `metric_not_tagged` is used only for a roster member with no
fiscal-year row at all, which agrees with screen-full-universe Requirement 4.

### 5. Geographic screen (J5)

A screen step whose `dimension` has a `geography`, `geo`, `country` or `region` key names a place
`g`.

- **Country.** `g` is a two-letter code or a country name of `GEO_NAME_TO_CODE`.
  - A row matches when its `geo_code` is the code, as today. Cells are not excluded here, and
    ranking keeps today's rule: the largest USD value per filer.
  - A filer whose matching fiscal-year rows are all non-USD is a `currency` exclusion
    (TSM China).
  - **Related member:** a fiscal-year row whose member is not in the SEC country taxonomy
    (`segment_member` does not start with `country:`). Its member label's compact form
    contains the country name's compact form (`china`), and its `geo_code` is not the code.
  - A filer with no matching fiscal-year row but a related member is `different_member` with
    those labels (NVIDIA "China Including Hong Kong"). This holds even when it has a matching
    row in an earlier year.
- **Region.** `g` names a region of this table, compared by member key:

  | Region | Request names (member key) | Member keys that match |
  |---|---|---|
  | Europe | `europe` | `europe` |
  | EMEA | `emea`, `europemiddleeastandafrica` | `emea`, `europemiddleeastandafrica` |

  - The match is by the filer's **member label**, never by `geo_code` or `geo_name`. Today those
    two columns hold `EuropeMember`, `EMEAMember` or another filer's region name for these
    members (segment-geo-views, J1).
  - Only single-dimension facts rank. A filer whose region member exists only in cells is
    `multi_dimension_only`.
  - A filer with several distinct values for the member is `ambiguous_member`.
  - Ranked rows carry `member_match` `"exact"`.
  - **Related member:** a fiscal-year row whose member key is a key of the other region, or whose
    compact label contains `europe` or `emea`. A filer that has such a row and no match is
    `different_member` with those labels. A Europe screen lists the EMEA filers there, and an
    EMEA screen the Europe filers.
- `eu` and any other name are out of scope. They resolve as today, so `eu` still means `EU`,
  which matches no row.

## Acceptance criteria

- **C1** (report C4): A one-dimension lookup either answers OK with the member's full value or
  refuses. It never answers OK with a strict part.
  - Point (LLY FY2023, `{product: Trulicity}`): the result is one of these:
    - OK, `value` 7,132,500,000 (rel 1e-12), with `sum_of` = {219333, 219336};
    - `UNRESOLVED_DIMENSION` / `NOT_FOUND` with `value` None.

    With the US cell 219333 removed from the store, it must refuse.
  - Controls, which pass today and must keep passing:
    - LLY Jardiance (single-dimension fact 219594) is OK at 2,744,700,000;
    - HD FY2025 (10-K 0001628280-26-019436) `{product: appliances}`, whose only fact is the
      Appliances × Primary Segment cell 213903, is OK at 13,987,000,000 citing 213903, or refuses
      with `value` None.
  - Property, over synthetic filers whose product member exists in cells over a partitioning
    geographic axis (any subset of the cells present, either dimension listed first, with or
    without a single-dimension fact for the member):
    - with the single-dimension fact, the result is OK at its value;
    - otherwise, the result is OK at the sum of **all** the member's cells with `sum_of` equal to
      all their ids, or a refusal with `value` None;
    - listing the other dimension first never changes status or value.

  Oracle: FSDS 2024q1 / 2026q1 `num` cells (summed in the test); the synthetic store's
  construction.
- **C2** (report C7): The segment screen ranks only business-axis, single-dimension rows whose
  label matches, at one value per filer.
  - Point, FY2024 Revenue over the committed 12-filer excerpt:
    - "other": exactly COST 49,969,000,000 (`exact`), AMZN 5,425,000,000 (`contains`) and GOOGL
      1,648,000,000 (`contains`), in that order. KO and NFLX are not ranked.
    - "cloud": exactly GOOGL 43,229,000,000 (`contains`). The MSFT row 137,400,000,000 is not
      ranked.
    - "international": exactly AMZN 142,906,000,000 (`exact`), WMT 114,641,000,000
      (`contains`), UPS 17,960,000,000 (`contains`). LMT is not ranked.
    - Every `contains` row has a note naming its ticker and label.
  - Metamorphic property, synthetic filers: adding rows the rules exclude never changes the
    ranked tickers, values or `member_match`. These are rows off the business axes, cells, or
    labels that do not match, at any value (larger than every ranked value included). Over the
    clean store, the ranking equals the rule computed in the test.

  Oracle: FSDS `num` lines quoted in the fixture; the filers' member labels; Requirement 3
  restated in the test.
- **C3** (report C7, C8): Every unranked segment-screen member carries the reason of
  Requirement 4.
  - Point, the same excerpt:
    - "cloud": `ambiguous_member` for MSFT (labels Dynamics Products And Cloud Services,
      Intelligent Cloud, Office Products And Cloud Services, Server Products And Cloud
      Services), ORCL (its four cloud members) and CRM (Marketing and Commerce Cloud, Sales
      Cloud, Service Cloud). `member_not_found` for AMZN, COST, KO, LMT, MCD, NFLX, UPS, WMT.
    - "other": `ambiguous_member` for MSFT and CRM; `multi_dimension_only` for UPS and WMT;
      `member_not_found` for KO, LMT, MCD, NFLX, ORCL.
    - "international": `ambiguous_member` for MCD; `member_not_found` for COST, CRM, GOOGL, KO,
      LMT, MSFT, NFLX, ORCL.
  - Property, synthetic filers built to fall in each reason (a non-USD unit, several members,
    cells only, an earlier year only, other members only, no row): the reason is Requirement 4's
    and no member is both ranked and excluded.

  Oracle: the filers' member labels; Requirement 4 restated in the test.
- **C4** (report C8): Geographic country screens carry true exclusion reasons.
  - Point, FY2024 Revenue "China" over the committed 15-filer excerpt:
    - ranked exactly AAPL 66,952,000,000; META 18,350,000,000; QCOM 17,826,000,000; AMD
      6,231,000,000; MRK 5,494,000,000; MU 3,045,000,000; LLY 1,660,400,000;
    - TSM is `currency` with `currency` `TWD` (fact 89572, 331,673,300,000 TWD in the 20-F);
    - NVDA is `different_member` with labels exactly ["China Including Hong Kong [Member]"] (not
      "Other Countries", whose stored `geo_name` wrongly reads "China Including Hong Kong") and
      is not `period_not_filed`;
    - AVGO is `different_member` with ["China (including Hong Kong) [Member]"];
    - ADBE, CRM, GOOGL, LMT, ORCL are `member_not_found`.
  - Property, synthetic filers (USD country row, foreign-currency row, related extension
    member, earlier year only, other countries only, nothing): the reason follows Requirement 4,
    and ranked values are the largest USD values.

  Oracle: FSDS `num` lines; the TSM 20-F iXBRL fact `ixv-111673`; member labels; Requirement 4
  restated.
- **C5** (report C8): Europe and EMEA resolve to the filer's own region members or give a
  reason.
  - Point, the same 15-filer excerpt, FY2024 Revenue:
    - "Europe" ranks exactly META 38,361,000,000; CRM 8,128,000,000; LMT 7,716,000,000 (its
      single-dimension fact, not a segment cell); LLY 6,920,700,000; AMD 1,625,000,000; MU
      818,000,000. ADBE, AVGO, GOOGL, MRK, ORCL, TSM are `different_member` (EMEA labels).
      AAPL, NVDA, QCOM are `member_not_found`.
    - "EMEA" ranks exactly GOOGL 102,127,000,000; MRK 14,041,000,000; ORCL 13,030,000,000; AVGO
      8,182,000,000; ADBE 5,554,000,000. TSM is `currency` `TWD` (102,760,900,000 TWD, iXBRL
      `ixv-111679`). AMD, CRM, LLY, LMT, META, MU are `different_member` (Europe labels).
      AAPL, NVDA, QCOM are `member_not_found`.
  - Metamorphic property:
    - rewriting every row's `geo_code` and `geo_name` to arbitrary values leaves the Europe and
      EMEA results unchanged;
    - a filer with one region member is ranked under its own region and `different_member`
      under the other;
    - request spellings that share a member key ("Europe", "EUROPE", "europe") give the same
      result.

  Oracle: FSDS `num` lines; the TSM iXBRL fact; member labels; the region table.

## Interface

No new functions. Changed behaviour of existing interfaces:

- `src.retriever.xbrl.xbrl_factstore_lookup.XbrlFactstoreLookup.lookup(step: dict) -> FactLookupResult`:
  Requirement 1.
- `src.retriever.xbrl.xbrl_factstore_lookup.FactLookupResult` gains `sum_of: list[int] | None = None`.
  `to_dict()` adds `"sum_of"` only when it is set.
- `src.retriever.xbrl.xbrl_factstore_screen.XbrlFactstoreScreen.screen(step: dict) -> ScreenResult`:
  Requirements 2–5.
  - Segment and region rows carry `member_match`.
  - The new exclusion reasons appear in `disclosure["exclusions"]`. `excluded` keeps its
    current content: the `period_not_filed` filers.
- `src.retriever.xbrl.screen_disclosure` may give the new reasons their own phrases. The
  phrases are not locked.

## Notes

### Fake database contracts

Both fakes live in `test/spec/segment-lookup-screens/conftest.py`. They replace
`psycopg2.connect` (the I/O boundary) and serve the committed fixture records. The SQL is not
parsed. A query is answered from the first relation after a `FROM` that is one of the views
below, and joins are not emulated. A parameter counts only when its `%(name)s` placeholder is
in the SQL text. Any other query raises `LookupError` naming it.

**Lookup fake.**
- The publish-manifest probe (`to_regclass`) and the coverage-column probe (`pg_attribute`)
  get `(False,)`.
- `concept_mapping` gets the fixture's rows, in the order of `_expand_concept_family`.
- `dimension_catalog` gets the cik's member labels on the `axes`.
- `filing_registry` gets no rows.
- `mv_financial_facts` gets one row per fixture fact, with the stored first-dimension columns and
  `has_multiple_dimensions`. It honours:
  - `cik`, `fy` (equality), `concept_family`, `fiscal_period`, `period_type`, `duration_kind`
    and `pit_accession`;
  - `segment_axis IS [NOT] NULL`;
  - `axis_like` (`LIKE` on `segment_axis`) and `member_qname`;
  - `member_like`, matched against the compact label exactly as today's SQL computes it;
  - `fact_ids`;
  - `ORDER BY period_end DESC, concept_pref_rank, fact_id DESC`, and `LIMIT %(limit)s`.
- `mv_financial_facts_multidim` gets the fixture's cells, with `dimension_labels` and
  `dimensions`. It honours:
  - `cik`, `fy`, `fiscal_period`, `period_type`, `duration_kind` and `pit_accession`;
  - `concept_qnames` (and reports `concept_ranks` at the concept's position) or
    `concept_like`;
  - `hint_<i>`, matched as today's SQL matches it;
  - `fact_ids`;
  - the current order and `LIMIT`.

**Screen fake.**
- `entity_master` gets the roster, and the publish probe gets `(False,)`.
- `mv_segment_matrix`, `mv_geographic_facts`, `mv_financial_facts` and
  `mv_financial_facts_multidim` get the fixture's rows. Every row also carries `segment_label`,
  `segment_member`, `has_multiple_dimensions`, `concept_pref_rank` and `period_end`. Some of
  these are columns a view may need to gain; an implementation may equally read them from
  `mv_financial_facts` by `fact_ids`.
- It honours:
  - `fiscal_year <= %(fy)s` or `= %(fy)s`;
  - `concept_family`, `fiscal_period`, `duration_kind`, `geo_code`;
  - `member_like` (`LIKE` on `lower(segment_label)`);
  - `fact_ids`, `ticker(s)` and `cik(s)`;
  - `segment_axes` (a list of axis local names);
  - a literal `unit = 'USD'`;
  - a `has_multiple_dimensions` false predicate (`= FALSE`, `IS FALSE`, `IS NOT TRUE` or
    `NOT has_multiple_dimensions`);
  - `segment_axis IS [NOT] NULL`.
- `SELECT DISTINCT ON (ticker)` returns one row per ticker, as Postgres does under today's
  `ORDER BY ticker, fiscal_year DESC, value DESC`: the latest fiscal year, then the largest
  value. The per-filer choice of Requirement 3 must therefore be made over rows that are not
  collapsed by `DISTINCT ON`.

### Compatibility with screen-full-universe

The locked screen-full-universe fake (spec branch, 2026-11-07) answers every segment query
with rows that carry `segment_label` "Cloud" but no `segment_axis` and no
`has_multiple_dimensions`, and it ignores all predicates. Requirement 2's axis and
single-dimension filters therefore have to be applied so that those rows still rank. Two ways
work:

- filter in SQL, through the `segment_axes` parameter and the `has_multiple_dimensions`
  predicate that this spec's fake honours; or
- the screen-full-universe fake gains the two columns (a re-lock on its own branch).

The label rule is compatible as it stands: "Cloud" equals the hint "cloud". Recorded for the
human. The two specs do not overlap otherwise: that spec owns the LIMIT, this one the
per-filer choice and the reasons.

### Fixtures

The fixtures are in `test/spec/segment-lookup-screens/fixtures/`. They are excerpts of the local
`xbrl_db` (read-only, 2026-11-07) over committed corpus filings, with the oracle lines next to
them:

- `lookup_lly_hd.json`: LLY FY2023 Revenue (0000059478-24-000065) and HD FY2025 Revenue
  (0001628280-26-019436). FSDS 2024q1 and 2026q1 `num` lines.
- `screen_segment_fy2024.json`: FY2024 Revenue rows with labels containing other / cloud /
  international for AMZN, COST, CRM, GOOGL, KO, LMT, MCD, MSFT, NFLX, ORCL, UPS, WMT. FSDS
  2024q1–2025q1 `num` lines.
- `screen_geographic_fy2024.json`: FY2024 Revenue geographic rows for AAPL, ADBE, AMD, AVGO,
  CRM, GOOGL, LLY, LMT, META, MRK, MU, NVDA (FY2022–FY2024), ORCL, QCOM, TSM. FSDS lines and two
  TSM 20-F iXBRL excerpts. FSDS carries no TSM geographic lines.

Each fixture records every filing's EDGAR URL, the SHA-256 of its primary document as
ingested, and its retrieval date. It also records the FSDS quarter's zip URL, SHA-256 and
retrieval time (from the FSDS cache manifest). All filers are development-corpus filers; no
held-out filer or file is used.

### Out of scope

- The geographic join (`geo_code` / `geo_name` for extension members, J1) and share-of-total
  (J2) belong to segment-geo-views. This spec reads labels, not those columns, for regions.
- The screen row cap belongs to screen-full-universe.
- Synonyms ("cloud" for Amazon Web Services) are out of scope. AMZN is `member_not_found` under
  "cloud".
- Cells in country screens are out of scope: the country path keeps today's rule, and
  WMT/QCOM China are cells.
- Neutral second dimensions in screens are out of scope: one NVIDIA FY2024 row would be
  `multi_dimension_only`.
- `eu` and other region names are out of scope.
- Sum answers for lookups with two or more dimensions are out of scope.
