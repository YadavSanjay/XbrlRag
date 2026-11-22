# CLAUDE.md — `src/ingest/sec_edgar/` (XBRL download + fact/Layer-0 ingest)

The ingest side of the **XBRL flow**: download iXBRL/XBRL from EDGAR, load numeric
facts into PostgreSQL via Arelle, and populate the Layer-0 registries + structure
layer that make those facts queryable.

- **Operator guides:** [`03-data-download.md`](../../../docs/guides/xbrl-flow/03-data-download.md),
  [`04-ingest-and-populate.md`](../../../docs/guides/xbrl-flow/04-ingest-and-populate.md)
- **Data model:** [`docs/design/XbrlRag_Materialized_Views.md`](../../../docs/design/XbrlRag_Materialized_Views.md)
  (what Arelle really populates, Layer-0, the MVs, invariants, the publish gate)

## Layout

- `downloader/` — `SECFilingDownloader` (edgartools). Declarative: downloads exactly
  the YAML `downloads:` list; **no `--since`/date-range flag**, dedup is deferred to
  ingest time. Writes `manifest.yaml` per filing (the contract every later stage reads),
  including `ixbrl_documents`: the filing's inline documents, primary first.
- `document_set.py` — which documents a filing has, which file Arelle loads, and whether the
  saved documents hold every fact of the instance.
- `ingest/` — the `python -m src.ingest.sec_edgar.ingest` CLI (`__main__.py`) driving
  `ArelleXBRLIngestor`, `Layer0Populator`, `ConceptLabelsPopulator`,
  `DimensionMembersPopulator`. `arelle_plugins/` holds the two custom Arelle plugins.
- `xbrl/` — `html_canonicalizer.py` (pure file transform → `canonical.htm`),
  `dom_position_populator.py` (fact citation anchors + section assignment),
  `xbrl_html_dom_parser.py`, `member_alias_detector.py` (`member_aliases`), `promotion.py`
  (the publish gate).
- Generators live in `src/ingest/util/` (`generate_concept_mapping.py`,
  `generate_geography_normalization.py`, `hifi_kpi.py`, `retry.py`).

## Invariants / gotchas

- **Ingest is incremental/idempotent by design** — no `--full`/`--incremental` flag.
  Duplicate filings are skipped: candidate `accession` rows are found by the primary
  document's file stem in `entry_url`, then confirmed by accession number (an amendment can
  reuse its original's file name; docs/specs/ingest-accession-identity.md).
  "Full reload" of a Layer-0 table = the `replace_source`/`replace_all` mode, not a flag.
- **A filing's inline XBRL can span several documents**
  ([spec](../../../docs/specs/ingest-multidoc-ixbrl.md)). The second can be typed as an exhibit,
  so the downloader reads every HTML attachment and an unreadable one fails the download. Such a
  filing's Arelle entry is the extracted `*_htm.xml` (`arelle_entry_name`), so `entry_url` names
  the instance while `primary_document` stays the `.htm`; `same_entry_path` treats the two as one.
  `canonical.htm` stays one file, the merge of the documents in filing order; a later document's
  colliding non-fact id is renamed, a fact id never is. A single-document filing changes by no
  byte. Before Arelle runs, `_process_one` refuses a filing whose documents hold fewer facts than
  its instance (`assert_document_set_complete`), whatever its manifest says.
- **A 10-Q's year and quarter come from the filer's annual calendar**
  ([spec](../../../docs/specs/tenq-fiscal-labels.md)): `fiscal_labels.place_quarter` against
  the labelled 10-K period ends, in the downloader's request match, `label_filings` and
  `apply_fiscal_labels`; DEI is only a cross-check (a WARNING when it differs). A 10-Q that
  cannot be placed keeps its DEI year and manifest quarter, each emitted as a `decision`
  event. A 10-Q request names the fiscal year by the calendar year it ends in and the
  fiscal quarter; old `<YEAR>/10-Q/Q<n>` folders keep their calendar names. Both fact MVs
  read `fiscal_period` from `filing_registry`. `fiscal_calendar` bounds come from
  `report_period_bounds` of the saved instance (manifest dates are only the fallback).
- **Ordering matters:** facts must be in Postgres before Layer-0 overrides/labels/MVs.
  Follow guide 4 §4.11. The multi-flag internal order is: arelle-ingest → geography →
  concept-mapping-overrides → dimension-members → concept-labels → patch-accession-numbers
  → registries → concept-mapping → extension-classifications → sync-ns-expanded (last: it
  reads every mapping row).
- **Arelle must get the trailing `postgres` product type** on `--store-to-XBRL-DB` or
  xbrlDB does an HTTP Rexster probe and **hangs**. The CLI sets this; direct calls must too.
- The **iXBRL patch plugin loads before xbrlDB** (`<xbrl_db_ixbrl_patch.py>|xbrlDB`) to
  fix a `ModelInlineFact.xAttributes` crash.
- **xbrlDB drives Postgres through `pg8000`, not psycopg2** — a separate driver from the
  one the rest of the repo uses. The pip `arelle-release` does not depend on it, so it is
  pinned explicitly in `requirements-ingest.txt`; without it every ingest dies at the
  DB-write step with `xpgDB:MissingPostgresInterface`. The frozen Windows Arelle bundles
  it, which is why this only bites on Linux/containers.
- **`ARELLE_INTERNET_CONNECTIVITY=offline` is required wherever egress is restricted, even
  with a complete cache.** In *online* mode Arelle re-validates every cached HTTP entry
  before emitting a single log line; a cache seeded from another machine has stale mtimes,
  so the whole thing gets re-checked against `www.xbrl.org:80` and stalls for the full
  timeout with no output (`concept-labels` burned 600 s per accession on the AWS runner
  until the flag was set). Every Arelle stage reads this env var
  (`arelle_xbrl_ingestor` and both populators in `concept_labels_populator`); a new stage
  must too.
- **Arelle's web cache is load-bearing, not an optimization.** Resolving a DTS fetches the
  full us-gaap/dei/srt/country taxonomy plus the XBRL 2.1 core schemas — and the latter are
  served over **plain HTTP from `www.xbrl.org`**, which a 443-only egress policy blocks
  (`[Errno 99] Cannot assign requested address` → stall until timeout). Under Docker the
  cache dir (`~/.config/arelle/cache`) **must be a mounted volume** or `--rm` throws it away
  every run. `ARELLE_INTERNET_CONNECTIVITY=offline` turns a cache miss into an immediate,
  legible failure instead of a stall.
- **A filing's entire load is one transaction** — schema DDL included. Nothing is visible to
  another session until commit, so `count(*)` is useless as a progress meter mid-run (and
  `relation "concept" does not exist` is expected). Watch `pg_stat_activity` instead.
- The subprocess is **streamed, not buffered**: lines land at DEBUG as they arrive, a
  heartbeat lands at INFO every `ARELLE_HEARTBEAT_SECS`, and a timeout returns Arelle's last
  output rather than a bare "Timeout after Ns". Don't revert to `capture_output=True` — a
  silent 10-minute subprocess is undiagnosable in production.
- **Always run `--patch-accession-numbers --skip-registries` after every Arelle ingest**
  — Arelle writes a timestamp into `filing_accession_number`; the patch fixes it, and
  the accession-number check in the incremental skip depends on it.
- Arelle failures are **classified retryable-vs-permanent** (`timeout` retried up to
  `ARELLE_MAX_ATTEMPTS`; `validation_error`/`arelle_not_found` permanent) and recorded to
  `audit.ingestion_run_item`.
- **This path is LLM-free** — no `XBRL_QUERY_MODEL_TIERS` involvement.
- **MV rebuild** after ingest is `python scripts/rebuild_mvs.py` — all **19** managed MVs
  from `scripts/ddl/mv/` (Layer-1a prerequisites, resolver views, the two base MVs + six
  dependents, the four integrity gates, and the `mv_coverage_card` coverage card), with a pre/post guard that fails if an
  unmanaged MV was orphaned. Run it after every load.
- **`concept_mapping` needs its namespace expansion.** The views match facts on the
  `ns_expanded` rows only; without them `concept_family` is NULL in every row of
  `mv_financial_facts` and every lookup returns NOT_FOUND. Those rows are **derived, never
  edited**: `Layer0Populator._sync_ns_expanded` deletes them all and re-derives them from
  the base rows (`expand_concept_mapping`), then fails on drift (`NsExpandedDriftError`)
  in the same transaction (docs/specs/ns-expand-rank-sync.md). It runs after every mapping
  write (`--concept-mapping-csv` in every mode, `--concept-mapping-overrides`) and last in
  `bootstrap_db.py`'s `layer0-populate` step; by hand, `--sync-ns-expanded` (`--dry-run`
  reports drift and exits 1). `concept_mapping_ns_expand.sql` now only holds the
  ProductRevenue/ServiceRevenue demotion. Never write `ns_expanded` rows any other way:
  insert-if-missing left stale ranks behind after the 2026-10/11 rank changes.
- **Concept-family additions go in the reviewed override file**
  (`configs/concept_mapping/concept_family_overrides.yaml`, validated by
  `generate_concept_mapping`), not in the hardcoded dicts. Its filer entries load as
  `reviewed_override` rows, which `--concept-mapping-overrides` skips per (cik, family):
  a reviewed decision is never overwritten by the facts-count rule.
- **Roles, substitutes and cik-scoped members** live in
  `configs/concept_mapping/concept_family_coverage.yaml` (docs/specs/concept-family-coverage.md).
  They load as `concept_role` / `substitution_*` columns and `family_coverage` rows, which
  the facts-count pass also skips; its own rows copy their QName's generic role
  (`_sync_override_roles`). `ext:<LocalName>` rows (cik-scoped only) expand to every
  extension namespace with that local name. The coverage file's `fallback` primaries
  (short-term debt proceeds, cash including restricted cash) are never a filer's
  facts-count rank 1 (`facts_count_ranking`, docs/specs/screen-candidate-order.md R3).
  Reload steps: guide 4 §4.6.1.
- **Extension classifier** (`xbrl/extension_classifier.py`, docs/specs/extension-classification.md):
  a pure rule that names an extension's family only from the filer's own filings (a
  statement identity of its calculation trees, or a same-label, equal-value standard line)
  and otherwise leaves it unclassified with a reason. Extensions are (cik, local name),
  never a namespace URI; one filer never resolves another's. The hand-listed `filers:`
  entries always win (`extension_mapping_rows`). Wired into the reload (spec R7) by
  `ingest/extension_classifications.py` + `Layer0Populator.populate_extension_classifications`
  (`--extension-classifications`; bootstrap step `extension-classify`, after
  `layer0-populate`, before `mv-build`). Its input is the **cached FSDS** `pre`/`num`
  (`--fsds-dir`, default `data/cache/sec_fsds/filtered`) plus the **raw** calculation
  tables (12_'s SELECT), never an MV, so one MV build follows it; a filing not yet in a
  cached FSDS quarter is simply not classified. It replaces the `extension_classifier`
  rows as a set, ranked after every primary the filer holds (IFRS included) and after the
  facts-count pass's highest rank (a substitute after the component band), then
  re-derives `ns_expanded` in the same transaction. Not a protected source: the
  facts-count pass still re-ranks the pair's standard members. Run it after every other
  mapping write (it reads the stored ranking); a CSV load with a newly hand-listed `ext:`
  row deletes the classifier's row of that key. Report: `data/reports/extension_classification.json`
  (agreements, disagreements, unclassified with reasons).
- **A scoped ingest (`--ticker`) re-runs global steps.** `layer0-populate` and the MV
  rebuild cover every filer, so a scoped load can change answers for other filers. Rebuild and diff the eval suite
  after any load.
- **Roots come from env, and empty discovery is fatal.** Every stage resolves its root
  through `storage.local_root(role)` (`SEC_EDGAR_ROOT` / `XBRLRAG_NORMALIZED_ROOT`), which
  wins over the pipeline YAMLs — whose path keys ship deliberately blank. A stage that
  discovers 0 filings **fails** rather than reporting `ok`, so a mis-set root is never a
  silent no-op.
- **A filing is not served until it passes the publish gate.** `promotion.py` records a
  `publish_manifest` row per filing (CLI: `python -m src.ingest.sec_edgar.xbrl.promotion`,
  not part of `bootstrap_db.py`); **GATE-A (anchor coverage) is
  enforced hard**, while **GATE-B (completeness + sum-of-parts) is advisory** — recorded
  per filing but only blocking under `XBRLRAG_PUBLISH_GATE_B_ENFORCE=1`. The sum-of-parts
  signal is XBRL Calculations 1.1 (`xbrl/calc_check.py`, mirrored by the `12_`/`40_` MVs and
  kept in step by `test/integration/test_calc_check_view_live.py`): only a *strict* failure
  (`fail`, no dimensions) counts, per accession, and the known filer errors in
  `configs/ingest/calc_c11_exceptions.csv` are excused. That list is **shrink-only**: never
  add rows to make a filing pass (docs/specs/calc-check-c11.md). Read-time deny-lists
  in the six factstore query sites keep a held-back filing out of answers.
  GATE-A also holds back a **canonical** filing with no primary statements, never a
  non-canonical one.
- **"Latest filed" is not "replaces".** A Part III-only 10-K/A (executive comp,
  directors; cover-page facts only) must never replace the 10-K: the downloader keeps the
  original + the later amendment, and `resolve_supersession` makes an amendment canonical only when its facts
  carry the primary statements (`has_primary_statements`). The read-time gate is keyed
  per **(cik, fiscal year, period)**, so holding back any filing of a period hides the
  whole period: after re-ingesting an original, re-run the gate on its amendment too
  ([spec](../../../docs/specs/annual-amendment-handling.md)).

Connection: every populator's `from_env()` reads `XBRL_DB_*` + `SEC_EDGAR_ROOT`
(DB params via the shared `src/db/conn.py` `conn_params()`, which adds
`sslmode` — `prefer` local / `require` under `deploy_mode=aws`). Arelle path:
`ARELLE_CMD` (local: frozen `<Arelle install>/arelleCmdLine.exe`; AWS Linux: the
pip `arelle-release==2.41.0` console script, baked into `infra/docker/Dockerfile.ingest`).

**`ARELLE_CMD` takes two shapes — always resolve, never `Path(cmd).exists()`.** It is an
absolute path to the frozen build on Windows and a bare console-script name on Linux.
`subprocess` PATH-resolves a bare name happily, so a stat-based check passes on the dev box
and fails in the container — meaning one stage runs and the next claims Arelle is missing.
Every consumer routes through [`src/ingest/util/arelle_cmd.py`](../util/arelle_cmd.py)
(`arelle_cmd_from_env` → `resolve_arelle_cmd`); the platform-aware default lives there too,
so a `D:/…` string never reaches a Linux error message. A new call site must use it.

**`EDGAR/transform` is required.** It holds the SEC's `ixt-sec` transforms. The frozen Windows
build bundles it, and the ingest image installs it from a pinned commit, by SHA-256. Without it
Arelle carries on and stores `(ixTransformValueError)`, so GATE-A holds back any filing with that
marker. `transform_backfill` repairs such facts in place, keeping each `fact_id`; run the gate only
after it (docs/specs/ixt-sec-transforms.md). xbrlDB doubles each `%` in stored text, and the export
plugin matches that.

## Storage backend — local FS vs S3 (`storage.py`)

The ingest path is the only part of the system that touches filing bytes on disk
(query serving is filesystem-free). All of it goes through one seam,
[`storage.py`](storage.py) `FilingStore`, selected by `XBRLRAG_DEPLOY_MODE`
(`local` default | `aws`) via `FilingStore.from_env(role=...)`:

- **Two roles, one bucket / two prefixes.** `sec_edgar` (raw downloads;
  `SEC_EDGAR_ROOT`) and `normalized` (canonical HTML; `XBRLRAG_NORMALIZED_ROOT`). Local
  maps each to its own root; S3 co-locates them as
  `s3://$XBRLRAG_S3_BUCKET/$XBRLRAG_S3_PREFIX/<role>/…`.
- **Keys mirror the on-disk layout** `{TICKER}/{YEAR}/{FORM}/…`. `LocalFilingStore`
  is a behavior-preserving `pathlib` wrapper (local unchanged); `S3FilingStore` uses
  `boto3` (ingest-only dep, not in `requirements-serve.txt`).
- **Discovery** uses `iter_manifest_keys` (no `rglob` on S3); **Arelle** and other
  external/Path-based parsers get real files via `localize_dir()` / `localize_file()`
  (temp download in S3 mode, cleaned up on exit).
- **Funnelled through the store:** downloader write (stage-then-publish), canonicalizer
  (`_canonicalize_html` takes bytes), Arelle ingestor (discovery + `localize_dir`).
  **Open (CL #4d):** the read-side stages resolve
  `canonical.htm` from `accession.entry_url` via `relative_to(sec_edgar_root)`; in S3
  mode Arelle records a *localized temp path* in `entry_url`, so that resolution
  needs a stable rel-key instead. Until then AWS ingest runs in hybrid mode (filings on the
  runner's local disk, `XBRLRAG_STORAGE_BACKEND=local`). See
  [`docs/design/XbrlRag_AWS_Deployment.md`](../../../docs/design/XbrlRag_AWS_Deployment.md) §5
  and guide 4 §4.13.
