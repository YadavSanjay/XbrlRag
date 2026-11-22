# fiscal-label-alignment — one fiscal-year label per filing, on every surface

Today a filing has two fiscal-year labels. `mv_financial_facts` and
`mv_financial_facts_multidim` take DEI `DocumentFiscalYearFocus`; `filing_registry`,
`fiscal_calendar` and everything built on them take the manifest's `fiscal_year`, which is the
calendar year of the period end. The two disagree for 6 of the 155 filings in the corpus, and
in one case DEI itself is wrong:

- **Salesforce (CRM, cik 0001108524).** Both 0001108524-25-000006 (period end 2025-01-31) and
  0001108524-26-000060 (period end 2026-01-31) tag DEI 2025. Salesforce calls them fiscal 2025
  and fiscal 2026; the second tag is the filer's slip. `XbrlFactstoreLookup.lookup` orders
  `period_end DESC`, so `{CRM, fy 2025, Revenue}` returns 41,525M from the January-2026 year:
  OK, confidence 0.8, cited and wrong. The right answer is 37,895M. Fiscal 2026 returns
  NOT_FOUND. Companyfacts carries the same `fy`, so `fiscal_year_key` has the same fault.
- **Home Depot (HD, cik 0000354950).** Period ends 2024-01-28, 2025-02-02 and 2026-02-01 are
  registry 2024/2025/2026 and DEI 2023/2024/2025. DEI is right (Home Depot labels by start
  year), so under rule S2 the registry is the wrong side.
- **NVIDIA (two 10-Qs).** Registry 2026, DEI 2027; DEI is right.

## Requirement

The rule (requirements 1-6) is one pure function, `assign_fiscal_labels`, over one filer's
filings.

1. **A filing's label is its DEI `DocumentFiscalYearFocus`** (S2), except as below. Annual
   forms are `10-K`, `10-K/A`, `20-F`, `20-F/A`, `40-F`, `40-F/A`. Only annual forms take part
   in requirements 2-5.
2. **Two annual filings with different period ends never share a label, and labels strictly
   increase with period end.** Walk the filer's distinct period ends in ascending order. The
   earlier filing always keeps its label. If a later period's DEI is not greater than the
   prior period's label, the later period takes `prior label + k`, where
   `k = max(1, round(days between the two period ends / 365.25))`. So:
   - DEI 2025, 2025 on consecutive years gives 2025, 2026 (CRM).
   - Three in a row, 2025, 2025, 2025, gives 2025, 2026, 2027.
   - A DEI lower than the prior label is treated the same: 2025, 2024 gives 2025, 2026.
   - A collision across a two-year hole in the filings: DEI 2023, 2023 with period ends two
     years apart gives 2023, 2025. A short year (period ends 184 days apart) gives `+1`.
3. **A DEI that jumps forward stands.** It is not a collision: DEI 2013, 2015 on consecutive
   years stays 2013, 2015 (Salesforce's own sequence: its January-2014 10-K tags 2013 and its
   January-2015 10-K tags 2015). The label sequence may therefore have a gap.
4. **Filings of one period end are one period, never a collision.** They all take one label. The
   period's DEI is that of its earliest filed filing that has one (a tie on `filed_date` goes to
   the smallest accession). So a 10-K/A shares its original's label whatever the amendment
   tags.
5. **Missing DEI.** A period with no DEI among its filings gets, in order: `prior label + 1` if
   the prior period end is 350-380 days earlier; else `next period's DEI - 1` if the next period
   end is 350-380 days later and that period has a DEI; else the calendar year of its period
   end (the manifest's value today). Requirement 2 then applies to the result. The function
   never returns `None`: `filing_registry.fiscal_year` is NOT NULL.
6. **Every other form passes through.** A 10-Q, 10-Q/A, 10-KT or anything else takes its DEI, or
   the calendar year of its period end when it has none. It takes no part in requirements 2-5,
   neither changing an annual filing's label nor being changed by one.
7. **`filing_registry` and `fiscal_calendar` carry the label.** `apply_fiscal_labels` turns the
   parsed manifests plus each filing's DEI into relabelled copies, per filer, changing nothing
   else. `Layer0Populator.populate_registries` uses them for both upserts and for
   `_resolve_superseded_by`, reading DEI from the raw fact tables (the MVs are not rebuilt yet
   at registry time). Result: HD reads 2023/2024/2025, CRM keeps 2023/2024/2025/2026, and
   NVIDIA's two 10-Qs read 2027. The downloader's calendar-year `fiscal_year` request, the
   manifests and the on-disk `{TICKER}/{YEAR}/{FORM}` paths do not change.
8. **A relabelled filing leaves no stale calendar row.** `fiscal_calendar` is keyed on
   `(cik, fiscal_year, fiscal_period)` and is upserted, so relabelling HD would leave
   `(HD, 2026, FY)` behind. `stale_fiscal_calendar_keys` names such rows and
   `populate_registries` deletes them. Filers with no record in the run are untouched.
9. **Both fact MVs carry the label.** `fiscal_year` in `10_mv_financial_facts.sql` and
   `11_mv_financial_facts_multidim.sql` is the same per-filing expression, no longer bare
   `dei.fiscal_year_focus`, and independent of the fact's own period. So comparative columns
   stay filed under their filing's label, as they do today: the January-2026 filing's January-2025
   column is under fy 2026, and looking up fy 2025 reads the January-2025 filing. After a
   rebuild `XbrlFactstoreLookup.lookup` needs no change: CRM fy 2025 returns 37,895M and fy 2026
   returns 41,525M; total assets 102,928M and 112,305M.
10. **SEC keys identify the year by period end.** `annual_filing_labels` applies the same rule to
    a companyfacts document:
    - A filing is an accession with entries of an annual form and `fp` `FY`. Its DEI is its
      `fy` (the greatest, if its entries differ; `None` if none has one). Its `form_type` and
      `filed_date` are those of its entries.
    - Its period end is the latest `end` among its annual-length durations (350-380 days, any
      taxonomy) that is not after the entry's `filed` date.
    - A filing with no such duration is absent from the result and takes no part in the rule.
      Instants never set a period end: cover-page `dei` instants date after the year end
      (a Part III 10-K/A has nothing else), and one real 20-F carries only a `dei` instant
      and a stale `srt` instant.
    - `fiscal_year_key` then reads the entries of the filings labelled `fiscal_year`, plus those
      of an absent filing whose `fy` is `fiscal_year`. Everything else about it, including S1,
      is unchanged. The filing labelled F in `v2_suite` follows the same labels, so a
      point-in-time key for CRM reads the right filing.

**In scope:** requirements 1-10. **Out of scope:**

- The registry's 10-Q `fiscal_period`, which is calendar-derived and differs from DEI on 9 of
  28 10-Qs. It affects `publish_manifest` correlation (see Notes) and is a separate change.
- A collision between two 10-Qs. None exists in the corpus.
- Changing `publish_gate_clause`, `mv_filing_completeness` or any query code: they read
  `fiscal_year` and come right once its sources agree.
- A held-out key or suite, and any re-measurement of accuracy.

## Acceptance criteria

- **C1**: `assign_fiscal_labels` gives HD 2023/2024/2025, WMT 2023/2024/2025/2026 and, for a
  run whose DEI strictly increases, exactly the DEI. Oracle: the filings' raw iXBRL and Home
  Depot's and Walmart's own year labels; S2.
- **C2**: CRM gets 2023/2024/2025/2026; three in a row and a DEI below the prior label follow
  requirement 2; labels strictly increase; earlier labels never change when a later filing is
  added; order of input does not matter. Oracle: Salesforce's own fiscal 2025 and fiscal 2026
  labels; `label[i] = max(dei[i], label[i-1] + 1)` computed in the test.
- **C3**: a forward DEI jump stands; a collision across a hole gives `prior + k`; a short year
  gives `+1`. Oracle: requirements 2-3, Salesforce's January-2014 and January-2015 tags.
- **C4**: filings of one period end share one label, taken from the earliest filed; adding an
  amendment changes no other filing's label. Oracle: requirement 4; Salesforce's two
  January-2021 companyfacts accessions.
- **C5**: a missing DEI resolves by prior, then next, then calendar year, and is never `None`.
  Oracle: requirement 5, by hand.
- **C6**: 10-Q and 10-KT filings take their DEI or calendar year and neither change nor are
  changed by annual filings. Oracle: requirement 6; a January-year-end filer's 10-Q, by hand.
- **C7**: `apply_fiscal_labels` relabels per filer and changes nothing else, leaves its inputs
  alone, keeps a 10-K and 10-K/A in one supersession group and separates CRM's two filings;
  `Layer0Populator` calls it. Oracle: requirement 7; the filings above.
- **C8**: `stale_fiscal_calendar_keys` names exactly HD's `(2026, FY)` row and nothing of a filer
  absent from the run. Oracle: requirement 8, by hand; `Layer0Populator` calls it.
- **C9**: both MV definitions select the same `fiscal_year` expression, which is not bare
  `dei.fiscal_year_focus` and does not read the fact's period; the multidim MV groups by it.
  Oracle: requirement 9, read from the DDL source.
- **C10**: `fiscal_year_key` over a CRM document gives 37,895M and 41,525M for 2025 and 2026,
  total assets 102,928M and 112,305M, each with its own period, exact. Oracle: the two filings'
  raw iXBRL values; Salesforce's labels.
- **C11**: for a document with no collision, labels equal `fy`; a filing with no annual-length
  duration is absent and moves nothing; an absent filing's `fy` still selects its own entries.
  Oracle: requirement 10, by hand.
- **C12**: `key_intent` keys CRM's fiscal 2026, and point-in-time reads FY2024 and FY2025 from
  the filing labelled 2026 and FY2024 from the one labelled 2025, exact. Oracle: the two
  filings' raw iXBRL values.

## Interface

- `src.ingest.sec_edgar.fiscal_labels.assign_fiscal_labels(filings: Iterable[Mapping[str, Any]]) -> dict[str, int]`
- `src.ingest.sec_edgar.ingest.layer0_populator.apply_fiscal_labels(records: Iterable[ManifestRecord], dei_fiscal_years: Mapping[str, int | None]) -> list[ManifestRecord]`
- `src.ingest.sec_edgar.ingest.layer0_populator.stale_fiscal_calendar_keys(existing: Iterable[tuple[str, int, str]], records: Iterable[ManifestRecord]) -> set[tuple[str, int, str]]`
- `src.eval.keys.core.annual_filing_labels(company_facts: dict) -> dict[str, int]`

`filings` rows are plain mappings because a spec stub cannot add a dataclass. Behaviour of the
existing `fiscal_year_key` and of `key_intent` changes as in requirement 10.

## Notes

**Oracles.** The CRM, HD and WMT filing rows and values were read from the live `xbrl_db` on
2026-10-30, whose `mv_dei_metadata` and fact rows are the filings' raw iXBRL tags. The fiscal
years are Salesforce's and Home Depot's own labels. No value comes from running this change's
code. The MVs store durations and instants with Arelle's exclusive end
(`period_end` 2025-02-01 for the year ending 2025-01-31); `filing_registry` and companyfacts use
the inclusive date, which is what the tests use. Other filers in the tests are synthetic. No
held-out filer appears anywhere in this spec's tests or fixtures.

**The rule was tried before locking.** A scratch implementation over all 155 corpus filings
moves exactly the 6 filings above: 3 HD and 2 NVIDIA in the registry, and CRM's January-2026
filing in the MVs. Over the cached companyfacts of all 33 corpus filers, labels differ from `fy`
only for CRM (January 2021 and January 2026) and for NVIDIA's four filings of 2011-2014 (`fy`
one behind NVIDIA's own label, all before the corpus window). Instants as a fallback period end
made TSM's FY2024 key wrong in that scan, which is why requirement 10 ignores them.

**A forward slip cascades.** The rule cannot tell a repeated backward slip from a forward slip
followed by correct tags. DEI 2023, 2025, 2025, 2026 gives 2023, 2025, 2026, 2027: unique, but
every later label is one high and stays so. No corpus filer does this. The implementer logs a
WARNING for each period moved off its DEI, so that it shows up at ingest.

**Which side reads what.** Requirement 9 leaves the expression to the implementer. The simplest
is `fr.fiscal_year`: both MVs already join `filing_registry`, and the form filter makes that join
inner in effect. Whatever is chosen must equal the registry's label for every filing.

**Bootstrap order is safe.** `scripts/bootstrap_db.py` runs `arelle-ingest` →
`patch-accessions` → `registries`, so DEI exists when labels are computed. A manifest whose facts
are not loaded yet gets its calendar year until the next registries run.

**Consumers surveyed.**

| Reads | Surface | Effect |
|---|---|---|
| MV label | `mv_yoy_comparison`, `mv_derived_metrics`, `mv_segment_matrix`, `mv_geographic_facts`, `mv_concept_coverage`, `mv_sum_of_parts_checks`, `mv_filing_completeness`, `mv_coverage_card`, `mv_fact_prose_links`, lookup, screen, breakdown, `sec_check` | CRM only; fixed by rebuild |
| Registry label | `publish_manifest` (via `promotion.py`), `mv_entity_registry`, `dimension_catalog`, `_resolve_pit_accession`, `long_context` filing resolver, `/api/v1` filing list, `concept_mapping` year windows | HD and NVIDIA; fixed by requirement 7 |
| `fiscal_calendar` | `entity_extractor` date bounds, `mv_fiscal_period_bounds`, `concept_labels_populator` ticker join | HD and NVIDIA; fixed by requirements 7-8 |
| Registry label, copied | `chunk_metadata.fiscal_year`, Qdrant payload `fiscal_year`, the `FY{label}` prefix in embedded text | HD and NVIDIA; needs a reload, see below |
| Companyfacts `fy` | `fiscal_year_key`, `sec_live`, `v2_suite._original_annual_accession` | CRM; fixed by requirement 10 |

Three existing faults are fixed by requirement 7 and are worth knowing when reading older runs,
all because registry and MV differ by one for HD: `lookup(as_originally_reported)` and
`as_of` for HD pick the accession by registry year and the facts by MV year, which name
different filings; the `long_context` arm reads the filing ending
January N for an HD "fiscal N" case, which is fiscal N-1; and narrative retrieval filters HD
chunks by registry year, with the same off-by-one.

**`publish_manifest` today.** `publish_gate_clause` joins the MV's `(cik, fiscal_year,
fiscal_period)` to `publish_manifest`, whose `fiscal_year` is the registry's. For HD that is
off by one: holding back the filing ending January 2024 (manifest 2024) would hide MV fy 2024,
the filing ending February 2025, and leave the held-back filing served; MV fy 2023 has no manifest
row at all. CRM's 26-000060 (manifest 2026) matches nothing. The bug is real but inert today:
all 155 rows are `is_published = TRUE`. `promotion.py` has the same fault on its inputs
(`mv_filing_completeness` and `mv_sum_of_parts_checks` joined on the registry year), so HD
is graded on a neighbour's coverage, and fy 2026 gets NULL completeness for HD and CRM, which
passes. GATE-B is advisory. Requirements 7 and 9 fix both once the gate is re-run. The 10-Q
`fiscal_period` difference (out of scope) still misses the gate for those 9 filings.

**Rollout, for the operator; nothing here is done by the spec.** In order: registries run
(labels, stale rows); `python scripts/rebuild_mvs.py`; `python -m
src.ingest.sec_edgar.xbrl.promotion` over all filings, so `publish_manifest.fiscal_year` follows;
re-chunk and reload the HD and NVIDIA filings after the running vector load has finished, since
`chunk_id` does not depend on the label but the payload and the embedded `FY{label}` text do;
rebuild and diff `sql_derived_v1.json` (only CRM FY2025/FY2026 cases should move) and the v2
suites. Then check on the live database:

```sql
SELECT cik, fiscal_year, period_end FROM filing_registry
 WHERE cik IN ('0001108524','0000354950') AND fiscal_period = 'FY' ORDER BY 1, 3;
SELECT fiscal_year, value FROM mv_financial_facts
 WHERE cik = '0001108524' AND concept_family = 'Revenue' AND segment_axis IS NULL
   AND duration_kind = 'annual' AND is_current_period AND fiscal_year IN (2025, 2026);
SELECT fr.accession_no FROM filing_registry fr
  JOIN mv_financial_facts m ON m.accession_no = fr.accession_no
 WHERE m.fiscal_year <> fr.fiscal_year LIMIT 5;  -- expect no rows
```

**DDL and wiring are read from source.** The MV definitions need Postgres and the registries
need a connection, and spec tests have neither, so C9 and the wiring checks of C7 and C8 read
source text. That is the best check available here; the queries above are the real one.

**Implementer, also do:** add one sentence to S2 in `docs/design/XbrlRag_Eval_Semantics.md` (not
editable on a spec branch), for example: "The exception: when two annual filings of one filer
with different period ends would share a label, the later one takes the prior label + 1
(docs/specs/fiscal-label-alignment.md)." Update the `fiscal_year` comments in the two MV files and
the `publish_manifest` and `fiscal_year_key` docstrings that this makes stale. Run all of
`test/spec`: the retired `sec-keys`, `sec-keys-report-forms` and `v2-suite-build` tests read
`fiscal_year_key` and must keep passing, and they did against the scratch implementation.
