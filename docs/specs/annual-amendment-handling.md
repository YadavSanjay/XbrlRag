# annual-amendment-handling — a partial amendment must not replace the filing it amends

Found during the accuracy plan's suite refresh
([XbrlRag_Accuracy_Plan_Oct2026.md](../design/XbrlRag_Accuracy_Plan_Oct2026.md) §4, step 1).

## Requirement

Three filer-years hold no financial statements: TSLA FY2024, TSLA FY2025 and AMD FY2025
have 2 facts each in `mv_financial_facts` (2026-10-14), where every other annual filing has
about 1,000 or more. Each period has a later `10-K/A` that adds only Part III (executive
compensation, directors) and carries cover-page facts, not statements. Three stages each
treat "latest filed" as "replaces":

1. **Download.** `SecFilingDownloader._select_filing` keeps only the latest-filed
   candidate, so the 10-K/A is downloaded and the 10-K is not.
2. **Registry.** `Layer0Populator._resolve_superseded_by` chains filings by filed date, so
   even with both ingested the 10-K/A is canonical (`superseded_by IS NULL`). Every query
   surface filters on that, so the original's facts would stay invisible.
3. **Publish gate.** GATE-A checks the share of a filing's own facts that are anchored.
   2 of 2 scores 1.0, so an annual filing with no statements is published.

After this change:

- The downloader fetches the original and the latest later amendment.
- An amendment replaces the original only when it carries the primary financial
  statements.
- The gate holds back a canonical filing that has none.

A full restatement (an amendment that re-files the statements) still replaces the original,
as today.

## Acceptance criteria

- **C1**: `choose_filings(candidates)` takes the EDGAR filings already matched to one
  requested period. Each candidate has `.form` (e.g. `10-K`, `10-K/A`, `20-F/A`, `10-Q`),
  `.filing_date` (a `date` or ISO string) and `.accession_no`. It returns the filings to
  download, ordered by filing date:
  - The latest original (form without `/A`), then the latest amendment filed strictly after
    it, if there is one.
  - With no original: only the latest amendment.
  - With no candidates: an empty list.
  - Ties on filing date go to the larger `accession_no`.

  Oracle: requirement; synthetic filings dated in TSLA's pattern (10-K in late January,
  Part III 10-K/A in late April).
- **C2**: `has_primary_statements(concept_families)` is `True` iff the families include
  `TotalAssets` and at least one of `Revenue`, `NetIncome`. `None` entries are ignored.
  - Cover-page only (all `None`, or `EntityCommonStockSharesOutstanding`-style families
    that map to nothing): `False`.
  - Balance sheet without an income statement: `False`.

  Oracle: the definition — the balance sheet plus an income-statement total.
- **C3**: `resolve_supersession(group, full_statement)` takes the `ManifestRecord`s of one
  (cik, fiscal year, fiscal period) and a map from accession to "carries primary
  statements". It returns a map from every record's accession to its new `superseded_by`:
  - **Eligible:** originals (`is_amendment` false), plus amendments whose
    `full_statement` entry is true (a missing entry counts as false).
  - **Canonical:** the latest `filed_date` among the eligible, ties to the larger
    `accession_no`. If none is eligible, the latest record overall, as today.
  - The canonical record maps to `None`; every other record maps to the canonical
    accession.

  Oracle: requirement. Point cases: TSLA FY2025 (10-K canonical over a Part III 10-K/A) and
  a full restatement (the 10-K/A canonical).
- **C4**: `PublishGate._decide(row)` reads two optional row keys, `is_canonical` and
  `has_primary_statements`:
  - A row with `is_canonical` true and `has_primary_statements` false is not published:
    `gate_a_passed` false, and a held-back reason that starts with `GATE-A` and mentions
    primary statements. This holds whatever the anchor coverage.
  - Any other combination decides exactly as a row without the two keys: non-canonical,
    statements present, or either key `None`. A non-canonical partial amendment is never
    held back for missing statements, because the read-time gate filters by
    (cik, fiscal year, fiscal period) and would also hide the original it amends.

  Oracle: requirement; the read-time filter in `xbrl_factstore_lookup.publish_gate_clause`.

## Interface

New functions (stubbed in the spec PR):

- `src.ingest.sec_edgar.downloader.sec_filing_downloader.choose_filings(candidates: Sequence[Any]) -> list[Any]`
- `src.ingest.sec_edgar.ingest.layer0_populator.has_primary_statements(concept_families: Iterable[str | None]) -> bool`
- `src.ingest.sec_edgar.ingest.layer0_populator.resolve_supersession(group: Sequence[ManifestRecord], full_statement: Mapping[str, bool]) -> dict[str, str | None]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `PublishGate._decide` gains the C4 rule; `_CANDIDATE_SQL` supplies `is_canonical`
  (`fr.superseded_by IS NULL`) and `has_primary_statements`. The latter comes from the
  filing's facts through `concept_mapping`, evaluated with `has_primary_statements` or the
  equivalent SQL predicate.
- The downloader downloads every filing `choose_filings` returns. An original goes to
  `<FORM>/` and an amendment to `<FORM>_A/`, as today, each with its own `manifest.yaml`.
- The registry pass calls `resolve_supersession` for each (cik, fiscal year, fiscal
  period). It builds `full_statement` from the amendments' ingested facts (`fact` ⨝
  `concept_mapping`). The registry pass runs after the Arelle ingest, so the facts exist.

## Notes

- Evidence (2026-10-14, local `xbrl_db`): `filing_registry` lists only `10-K/A` for TSLA
  FY2024, TSLA FY2025 and AMD FY2025; `mv_financial_facts` has 2 facts and 0 revenue facts
  for each. All three are in `publish_manifest` as published.
- Operator follow-up after the impl merges: download the three originals, scoped ingest,
  registries, then the structure, MV, gate and vector steps (commands in the 2026-10-14
  session notes). Then rebuild the eval suite: TSLA adds strata and, under
  `stable-case-selection`, only adds cases.
- Known risk, out of scope: the read-time gate is keyed per period, so a non-canonical
  filing held back for another reason (e.g. GATE-A anchor coverage on a broken amendment)
  still hides the canonical filing's facts. Keying it per accession is a separate change.
- Test fakes: candidate filings are simple objects (EDGAR is a network boundary). The pure
  functions and `_decide` need no DB.
