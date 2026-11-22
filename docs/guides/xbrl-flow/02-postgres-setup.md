# XBRL Flow — 2. PostgreSQL Setup

PostgreSQL (`xbrl_db`) is the system of record for the XBRL flow: the numeric facts, the
Layer-0 registries that make them queryable, the materialized views the retriever reads, and
the `audit` and `eval` schemas. This guide stands up the database and its schema; loading
data is [guide 4](./04-ingest-and-populate.md). What each table and view means is in the
[fact-store data model](../../design/XbrlRag_Materialized_Views.md).

## 2.1 Create the database

```powershell
# Defaults the code expects: user postgres, db xbrl_db, port 5432
psql -h localhost -U postgres -c "CREATE DATABASE xbrl_db;"
```

Connection settings come from `XBRL_DB_*` ([guide 1 §1.4](./01-setup-and-dependencies.md)).
`bootstrap_db.py` creates the one extension XbrlRag needs, `pg_trgm` (the trigram index on
`entity_master` for fuzzy company names), after the base step. For RDS, point `XBRL_DB_HOST` at
the endpoint; `sslmode` becomes `require` under `XBRLRAG_DEPLOY_MODE=aws`
([local / AWS deployment](../../design/XbrlRag_AWS_Deployment.md)).

## 2.2 What gets created, and by whom

| Group | Objects | Created by | When |
|-------|---------|------------|------|
| **Arelle public schema** | `fact`, `context`, `accession`, `element`, `qname`, `relationship`, ... | the xbrlDB plugin on the first `--arelle-ingest`, or [`scripts/ddl/base/xbrlPublicPostgresDB.ddl`](../../../scripts/ddl/base/README.md) via `bootstrap_db.py --with-base` | new instance |
| **Layer-0** | the 14 tables in `scripts/ddl/layer_0/` (list below) | `bootstrap_db.py` step `layer0` | before ingest |
| **audit** | `audit.query_audit`, `audit.query_event`, `audit.ingestion_run`, `audit.ingestion_run_item`, `audit.v_*` views | `bootstrap_db.py` step `audit` ([§2.4](#24-apply-the-audit--observability-ddl)) | before queries with audit on |
| **eval** | `eval.eval_run`, `eval.eval_case_result`, `eval.eval_suite` + 7 `eval.v_*` views | `scripts/ddl/eval/_apply.py` ([§2.4a](#24a-apply-the-eval-ddl-optional)) | only to run the eval suites |
| **Materialized views** | the 19 managed `mv_*` in `scripts/ddl/mv/` | `bootstrap_db.py --phase mv` or `scripts/rebuild_mvs.py` ([§2.5](#25-materialized-views-after-data-is-loaded)) | after facts and Layer-0 are loaded |

The order is **base → extensions → layer0 → audit → (data) → mv**.

### Standing up a brand-new instance

[`scripts/bootstrap_db.py`](../../../scripts/bootstrap_db.py) runs the schema, data and MV
phases in that order:

```powershell
conda run -n py312 python scripts/bootstrap_db.py --all --plan                  # print the plan, run nothing
conda run -n py312 python scripts/bootstrap_db.py --phase schema --with-base    # schema only, fresh instance
conda run -n py312 python scripts/bootstrap_db.py --phase schema                # schema without the base DDL
```

> ⚠️ **The base step is destructive.** `xbrlPublicPostgresDB.ddl` opens with
> `DROP SCHEMA public CASCADE` (upstream Arelle's own reset), which would take every Layer-0
> table and `mv_*` with it. `bootstrap_db.py` runs it only with `--with-base`, and refuses on a
> database that already holds XbrlRag objects unless you add `--force`. On RDS, skip it: the
> first `--arelle-ingest` creates the public tables.

Two properties of the vendored dump that bite on managed Postgres
(detail: [`scripts/ddl/base/README.md`](../../../scripts/ddl/base/README.md)):

- **A role named `postgres` must exist.** The dump carries 227 `ALTER ... OWNER TO postgres`
  statements. Create the role first if the master user has another name; don't edit the
  vendored file, whose checksum is its provenance.
- **`pgcrypto` arrives as 33 bare `LANGUAGE c` functions**, which RDS/Aurora refuse for
  non-superusers. Nothing in XbrlRag calls them, so those statements may fail safely.

Every data-phase and MV step is covered in [guide 4](./04-ingest-and-populate.md).

## 2.3 Apply the Layer-0 DDL

`bootstrap_db.py --phase schema` applies these as its `layer0` step. Every file is
`CREATE ... IF NOT EXISTS`, so re-running is safe, but it is not a migration: a table whose
definition has since changed is skipped, so a Layer-0 schema change needs its own migration
(the files use `ADD COLUMN IF NOT EXISTS` where a column was added later). By hand:

```powershell
conda run -n py312 python scripts/bootstrap_db.py --only layer0
```

| Table | Purpose |
|-------|---------|
| `filing_registry` | canonical filing tracker: form, fiscal year/period, dates, `is_amendment`, `superseded_by` |
| `fiscal_calendar` | (cik, fiscal_year, fiscal_period) → exact date bounds and FYE |
| `entity_master` | per-filer metadata from cover-page facts; drives the company typeahead and entity resolution |
| `concept_mapping` | concept family → ranked QNames: the semantic layer ([guide 4 §4.6](./04-ingest-and-populate.md#46-concept-mapping-the-semantic-layer)) |
| `concept_labels` | concept → human label |
| `dimension_catalog` | per-filer dimension axis + member pairs and their labels |
| `geography_normalization` | geographic member → ISO code / name / type; feeds `mv_geographic_facts` |
| `member_aliases` | dimension-member renames between years; read by `mv_yoy_comparison` |
| `fact_dom_position` | byte-range citation anchor for every tagged fact |
| `section_assignment` | narrative section path for facts outside the statements |
| `chunk_metadata` | SQL mirror of every prose chunk in Qdrant |
| `chunk_cross_links` | fact ↔ chunk overlap links |
| `publish_manifest` | per-filing publish-gate result |
| `query_cache_epoch` | marker the publish gate bumps; the query cache flushes when it changes |

Purpose, invariants and population of each:
[fact-store data model §3](../../design/XbrlRag_Materialized_Views.md#3-layer-0--xbrlrag-owned-tables).

## 2.4 Apply the audit / observability DDL

[`scripts/ddl/audit/_apply.py`](../../../scripts/ddl/audit/_apply.py) (the `audit` step of
`bootstrap_db.py`) reads `XBRL_DB_*` and applies `query_audit.sql`, `ingestion_runs.sql` and
`analyst_views.sql` in that order, or only the files you name:

```powershell
conda run -n py312 python scripts/ddl/audit/_apply.py
conda run -n py312 python scripts/ddl/audit/_apply.py scripts/ddl/audit/analyst_views.sql
```

| Object | Purpose |
|--------|---------|
| `audit.query_audit` | one rollup row per query (cost, latency, branch, intent, answer) |
| `audit.query_event` | one row per trace event (`decision`, `llm_call`, `sql_query`, `numeric_lineage`, ...), JSONB payload |
| `audit.ingestion_run` / `audit.ingestion_run_item` | one row per ingest invocation / per filing in it (status, failure class, attempts) |
| `audit.v_*` | read-only reporting views; also the source of `GET /metrics` ([guide 7 §7.7](./07-api-ui-observability.md)) |

## 2.4a Apply the eval DDL (optional)

Needed only to run the eval suites and populate `/tech/eval`; `bootstrap_db.py` does not apply
it. The views are `CREATE OR REPLACE`, so re-applying is safe.

```powershell
conda run -n py312 python scripts/ddl/eval/_apply.py
```

See [guide 11](./11-evaluation.md).

## 2.5 Materialized views (after data is loaded)

The MVs are the queryable surface the fact-store steps read. All 19 are committed SQL under
[`scripts/ddl/mv/`](../../../scripts/ddl/mv/): the Layer-1a prerequisites (`01_`–`04_`), the
anchor-coverage gate (`05_`), the resolver views (`06_`, `07_`), the period-aware base MVs
`mv_financial_facts` and `mv_financial_facts_multidim` with `mv_calculation_network`
(`10_`–`12_`), the analytical dependents (`30_`–`35_`), the integrity gates (`40_`, `41_`) and
the coverage card (`42_`).

```powershell
conda run -n py312 python scripts/rebuild_mvs.py
```

It applies them in filename order; each file drops its own view first, and a pre/post guard
fails the run if an MV outside the managed set was orphaned by a `CASCADE`. Run it after every
load ([guide 4 §4.10](./04-ingest-and-populate.md#410-materialized-views)). What each view is for:
[fact-store data model §4](../../design/XbrlRag_Materialized_Views.md#4-materialized-views).
Example analyst queries over the MVs:
[`scripts/xbrl/analyst_queries.sql`](../../../scripts/xbrl/analyst_queries.sql).

## 2.6 Verify

```powershell
conda run -n py312 python scripts/dbq.py "SELECT count(*) FROM filing_registry; SELECT count(*) FROM mv_financial_facts"
```

`scripts/dbq.py` connects through the same `conn_params()` as the pipeline and prints
`user@host:port/dbname sslmode=...` to stderr, so it also confirms which database you reached.
After a full load, run the consolidated check
`scripts/checks/post_ingest_acceptance.sql` ([guide 4 §4.13.5a](./04-ingest-and-populate.md#4135a-acceptance-check-before-pointing-the-ui-at-the-db)).

Next: [3. Data download](./03-data-download.md).
