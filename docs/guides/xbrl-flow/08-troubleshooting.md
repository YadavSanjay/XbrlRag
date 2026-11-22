# XBRL Flow — 8. Troubleshooting

Failure modes an operator can still hit in the XBRL flow, grouped by component, each as
**Symptom / Cause / Fix**. Most problems trace to one of three things: the two stateful stores
(PostgreSQL, Qdrant) or the external Arelle subprocess. Start with
[General diagnostics](#general-diagnostics) when you don't know which.

**In this doc:** [PostgreSQL](#postgresql) · [Arelle / fact ingest](#arelle--fact-ingest) ·
[Qdrant / vector load](#qdrant--vector-load) · [Retrieval / answers](#retrieval--answers) ·
[API / UI](#api--ui) · [v1 API / MCP](#v1-analyst-api--mcp) ·
[Evaluation](#evaluation-guide-11) · [General diagnostics](#general-diagnostics)

On the AWS ingest runner, `run …` below is the shell function defined in
[guide 4](./04-ingest-and-populate.md) (Running on AWS) that runs a command inside the ingest
container; locally, use `conda run -n py312 python …` instead.

## PostgreSQL

| Symptom | Cause | Fix |
|---------|-------|-----|
| `psycopg2.OperationalError: could not connect` | Postgres down, wrong `XBRL_DB_*`, or `xbrl_db` missing | Start Postgres; create `xbrl_db`; check the `XBRL_DB_*` settings. `scripts/dbq.py "SELECT 1"` prints `user@host:port/db sslmode=…` to stderr, the quickest way to see where you are pointed. |
| Need SQL on the AWS runner, no `psql` | The ingest image ships a Python driver only | `run python scripts/dbq.py "SELECT count(*) FROM fact"`; it uses the pipeline's own `conn_params()`, so it tests the same TLS and host path. |
| `relation "fact" does not exist` | Arelle's public-schema tables not created yet | `bootstrap_db.py --phase schema --with-base` on a fresh database, or let the first `--arelle-ingest` create them, before any populator that reads `fact`. |
| `relation "mv_financial_facts" does not exist` | Materialized views never built | `python scripts/rebuild_mvs.py`. |
| `relation "filing_registry"` / `"audit.query_audit"` does not exist | Layer-0 or audit DDL not applied | Apply `scripts/ddl/layer_0/*.sql` and `python scripts/ddl/audit/_apply.py` ([guide 2](./02-postgres-setup.md)). |
| **Every** numeric lookup returns NOT_FOUND | `concept_family` is NULL across `mv_financial_facts`: the namespace expansion never ran | `python -m src.ingest.sec_edgar.ingest --sync-ns-expanded --skip-registries` (or `bootstrap_db.py --only layer0-populate`), then rebuild the MVs. |
| A mapping write fails with `NsExpandedDriftError` | An `ns_expanded` copy still differs from its base row after re-derivation, or collides with a hand-written row; the write rolled back | Read the rows named; `--sync-ns-expanded --dry-run` reports the drift without writing ([spec](../../specs/ns-expand-rank-sync.md)). |
| Low confidence / "concept not found" | `concept_mapping` lacks the family, or the wrong tag for that filer | Load the concept-mapping CSV; add a per-filer override (`--concept-mapping-overrides --override-targets CIK:FY`). |
| A lookup answers with a component (D&A as amortization only, Debt as short-term borrowings) | A component concept ranks too high for that filer | Components rank from 50 and are never rank 1; check `concept_mapping` for the family and filer, reload, rebuild MVs ([spec](../../specs/concept-rank-integrity.md)). |
| Answers changed for filers you did not ingest | A scoped ingest (`--ticker`) still re-runs the global steps (`layer0-populate`, the MV rebuild) for every filer | Expected; rebuild and diff the eval suite after any load ([guide 11](./11-evaluation.md)). |
| 20-F (TSM) facts not found | IFRS `concept_mapping` rows (`source='ifrs'`) absent | Load the IFRS rows; note the TWD-currency caveat in `analyst_queries.sql`. |
| Stale numbers after re-ingest | MVs not refreshed | Rebuild MVs after any new facts. |
| `cannot drop mv_financial_facts … other objects depend on it` | Dependent MVs | Use `scripts/rebuild_mvs.py`: it drops with CASCADE and recreates every managed MV from `scripts/ddl/mv/`, failing if an unmanaged MV was orphaned. |
| Segment or geographic shares sum to more than 100% | Expected: each share is of the **consolidated** total, and filers tag overlapping members (an aggregate and its children) | Don't sum shares across members; use the `breakdown` op, which picks one partition ([fact-store data model](../../design/XbrlRag_Materialized_Views.md)). |

## Arelle / fact ingest

| Symptom | Cause | Fix |
|---------|-------|-----|
| Ingest **hangs** with no progress | `--store-to-XBRL-DB` lacks the trailing `postgres` product type, so xbrlDB probes an HTTP endpoint | Use the project ingest CLI (it sets it); calling Arelle directly, append `,postgres`. |
| `arelle_not_found` (returncode -2) | `ARELLE_CMD` path wrong | Point `ARELLE_CMD` at the real `arelleCmdLine`, or pass `--arelle-cmd`. |
| `AttributeError: ModelInlineFact … xAttributes` | The iXBRL patch plugin didn't load before xbrlDB | Load plugins as `<xbrl_db_ixbrl_patch.py>\|xbrlDB` (the CLI does). |
| `timeout` (returncode -1) on big filings | Slow validation, or the first filing of a fiscal year inserting that year's whole us-gaap taxonomy | Raise `ARELLE_TIMEOUT` (default 600 s); timeouts are retried up to `ARELLE_MAX_ATTEMPTS` (default 2). Expect one slow filing per taxonomy edition. Read the last output in the timeout message before tuning. |
| `[xpgDB:MissingPostgresInterface] Postgres interface is not installed` | xbrlDB talks to Postgres via **pg8000**, which pip `arelle-release` doesn't pull in (Linux and containers only) | `pg8000` is in [`requirements-ingest.txt`](../../../requirements-ingest.txt); rebuild or re-pull the ingest image. |
| `[webCache:retrievalError] [Errno 99] Cannot assign requested address`, then `[FileNotLoadable] http://www.xbrl.org/…` | The XBRL 2.1 core schemas are served over plain HTTP (port 80), and the subnet allows only 443 egress or resolves IPv6 with no route | Seed the Arelle web cache ([guide 4](./04-ingest-and-populate.md)) and run with `ARELLE_INTERNET_CONNECTIVITY=offline`; fix egress separately. Reaching `xbrl.fasb.org` / `xbrl.sec.gov` proves nothing: those are on 443. |
| Container ingest slow on **every** run | `docker run --rm` without a volume for `~/.config/arelle` discards the web cache | Mount `-v /var/lib/arag-arelle-cache:/home/arag/.config/arelle`, owned by uid 10001. |
| `SELECT count(*) FROM fact` shows nothing **while** Arelle runs | xbrlDB loads a filing in one transaction, invisible until commit | Not a stall. Watch `pg_stat_activity`; `relation "concept" does not exist` mid-run is expected. |
| `concept-labels` stalls exactly `ARELLE_TIMEOUT` per accession; heartbeat shows `last output: (none yet)` | Arelle ran **online** and re-validates every cached HTTP entry against an unreachable port-80 host before logging anything | Set `ARELLE_INTERNET_CONNECTIVITY=offline`, and confirm the logged `CMD:` line ends with `--internetConnectivity offline` (if not, the variable never reached the container). |
| `arelleCmdLine: command not found` on the AWS runner | You are on the EC2 host, not in the container; `run` is a shell function lost with the SSM session | Re-define `run` ([guide 4](./04-ingest-and-populate.md)). |
| `pull access denied … authorization token has expired` | ECR login expired (12 h); reads like a permissions or typo problem | `aws ecr get-login-password --region <region> \| sudo docker login --username AWS --password-stdin <registry>`; it must be `sudo`, because `run` uses `sudo docker`. |
| `password authentication failed for user "arag_admin"` at `arelle-ingest` on the runner | The RDS master password is a managed secret that rotates; `/opt/arag/.env` is written once at boot and is now stale | Run `arag-render-secrets` before the ingest ([guide 4 §4.13.2](./04-ingest-and-populate.md#4132-ingest-into-rds-on-the-runner-local-storage-mode)). |
| A single directory literally named `{sec_edgar,normalized,…}` appears on the runner | Brace expansion is a bashism; the SSM session runs plain `sh` | One `mkdir -p` per directory. |
| `docker run` fails with an empty image name | The image URI was grepped out of the `arag-ingest` wrapper, which carries it inline with no `IMAGE=` assignment | Get it from `sudo /usr/local/bin/arag-resolve-image`. |
| A code fix seems absent after a rebuild | `arag-resolve-image` only prints the pointer; it neither publishes nor pulls | Publish the pointer from the infra repo, `docker pull` explicitly, then probe for a symbol only the new build has. |
| `ModuleNotFoundError: No module named 'torch'` in an image | An import chain leaked into the torch-free path | Route the import through a torch-free module and add the entry point to `TORCH_FREE_MODULES` in `test/unit/test_torch_free_contract.py`. |
| `validation_error` (returncode > 0) | A genuinely invalid or odd filing | Permanent, not retried; inspect Arelle's output for that accession. |
| `filing_accession_number` is a timestamp | Arelle's default | Run `--patch-accession-numbers --skip-registries` after **every** Arelle ingest. |
| Re-ingest re-loads filings already loaded | The stored `accession.entry_url` no longer contains the primary document's file stem, which is how loaded filings are matched | Check `entry_url` for that filing. |
| Concept labels / dimension members empty | The label export plugin never ran | `--concept-labels --dimension-members`. |
| `--dimension-members` reports success, but segment and geography rows still show raw member QNames | It only fills `member_label` where `dimension_catalog` already has rows; against an empty table it patches nothing | Run `layer0-populate` first, then `--concept-labels --dimension-members` ([guide 4 §4.7](./04-ingest-and-populate.md#47-concept-labels--dimension-members)). |
| On AWS: `fact_dom_position` is 0, `chunk_metadata` empty, the vector load reports `targets=0`, and nothing errors | The run used pure S3 storage; the read-side stages resolve `canonical.htm` relative to `SEC_EDGAR_ROOT` and silently skip every filing | Use the hybrid mode: `XBRLRAG_STORAGE_BACKEND=local` with the filings on the runner's disk ([guide 4 §4.13](./04-ingest-and-populate.md#413-running-on-aws-the-on-demand-ingest-runner)). |

### Is Arelle stalled, or just slow?

The heartbeat (`Arelle still running on X: Ns elapsed; last output: …`) shows the process is
alive, not that it progresses. Check these in order; they take seconds:

| Signal | Working | Blocked |
|---|---|---|
| Heartbeat `last output` | advances, or names a real phase | `(none yet)` for minutes |
| `du -sh /var/lib/arag-arelle-cache/cache`, twice 30 s apart | grows during a real taxonomy fetch | static |
| EC2 network-in metric | MB during a real download | flat, about 100 KB per 15 min (SSM agent chatter) |

Static cache, no output and no traffic means it waits on TCP connects that never complete and will
burn the full timeout; raising `ARELLE_TIMEOUT` only multiplies the waste. While diagnosing, set
`ARELLE_TIMEOUT=120` so a wrong guess costs two minutes; restore it for real ingest runs.

## Qdrant / vector load

| Symptom | Cause | Fix |
|---------|-------|-----|
| Connection refused on load or narrative search | Qdrant not reachable at `QDRANT_HOST`/`QDRANT_PORT` (default `localhost:6333`) | Start Qdrant on a named volume; or set `QDRANT_HOST`/`QDRANT_PORT` (env wins over the load YAML). |
| Qdrant exits (code 101) on start: `Panic … gridstore/src/blob.rs … Failed to deserialize …`, then `Failed to load local shard "./storage/collections/<name>/0"` | A payload-index file was zeroed: storage on a **Windows-host bind mount** plus an unclean stop | Rebuild that collection: see [below](#qdrant-wont-start-corrupted-collection). |
| Vector load processes **0 filings** | `chunk_metadata` not populated (discovery requires it) | `python -m src.chunking.xbrl_html_base_chunking --chunk-metadata` first. |
| Chunks missing `xbrl_fact_ids` / concepts | Cross-links not built | `python -m src.chunking.xbrl_html_base_chunking --cross-links`. |
| `canonical.htm not found` during load | Canonicalizer not run | `python -m src.ingest.sec_edgar.xbrl.html_canonicalizer`. |
| Load fails every ~5 min with `xbrl_load_to_vectordb failed for <accession>` and `points_count` stops growing | An embed call exceeds `RAGINFER_TIMEOUT_S` (default 300 s); RagInference keeps working on the abandoned request, so the next one queues and times out too | Set `RAGINFER_TIMEOUT_S=1800`; rerun only the missing filings with `--accessions …` (the pipeline doesn't skip filings already loaded). |
| `FileNotFoundError: '/data/normalized/<TICKER>/<FY>/<FORM>'` from `mkdir` | The output mount is root-owned, but the image runs as uid 10001 | On the host, `chown -R 10001:10001` the trees the container writes (normalized output, Arelle cache). |
| `input_root=/app/D:/...` in a stage's startup log | A Windows path resolved against the container's working directory | Pass `-e SEC_EDGAR_ROOT=… -e XBRLRAG_NORMALIZED_ROOT=…`; roots resolve through `storage.local_root(role)`. |
| Narrative branch returns nothing | Empty `xbrl_sec10k_text`, or over-tight entity/period/section filters | Check `points_count`; loosen the filter; confirm the entity resolved. |
| Section filter misses content | Exact `section_path` mismatch | Filter on `section_path_ancestors` (prefix match). |

### Qdrant won't start: corrupted collection

Qdrant writes through memory-mapped files, which a Windows-host bind mount doesn't reliably
persist; a hard kill (sleep, reboot, Docker Desktop restart) can then zero a payload-index file.
If `docker logs` shows `WARN qdrant: … Unrecognized filesystem - cannot guarantee data safety`,
storage is exposed. Only one collection breaks, but Qdrant aborts on the first bad shard, so
everything looks down. The collection is derived data: rebuild it, don't repair segments.

```powershell
# 1. Quarantine the broken collection (keep it until the reload is verified)
Move-Item <storage>/collections/xbrl_sec10k_text <somewhere>/qdrant_xbrl_text_corrupt_<date>

# 2. Copy the healthy storage onto a named volume (mmap-safe)
docker volume create qdrant_data
docker run --rm -v <storage>:/src:ro -v qdrant_data:/dst --entrypoint sh qdrant/qdrant:v1.18.2 -c "cp -a /src/. /dst/"

# 3. Replace the container: pinned tag, named volume
docker rename <old-container> qdrant_bindmount_old
docker run -d --name qdrant --restart unless-stopped -p 6333:6333 -p 6334:6334 -v qdrant_data:/qdrant/storage qdrant/qdrant:v1.18.2
docker logs qdrant    # no "Unrecognized filesystem" WARN; every collection recovered

# 4. Re-embed and reload (RagInference must be up; a CPU-only full reload takes hours)
$env:RAGINFER_TIMEOUT_S = "1800"
conda run -n py312 --no-capture-output python -m src.pipelines.xbrl.load_to_vectordb_pipeline --all
```

Then check that `points_count` (`curl http://localhost:6333/collections/xbrl_sec10k_text`)
matches `SELECT count(*) FROM chunk_metadata` for non-superseded filings, and run one narrative
query, before deleting the quarantined directory and the old container.

**Prevention:** never bind-mount Qdrant storage from a Windows path (the compose stack uses the
named volume `qdrantdata`); `docker stop qdrant` before sleep or shutdown; pin the image tag; take
a snapshot after a big load (`POST /collections/xbrl_sec10k_text/snapshots`).

## Retrieval / answers

> **UI misbehaving after a fresh populate?** Run the acceptance gate first:
> `python scripts/dbq.py -f scripts/checks/post_ingest_acceptance.sql`. Most "the UI is wrong"
> reports are a populate stage that reported success while a later table stayed empty
> ([guide 4](./04-ingest-and-populate.md) maps tables to UI symptoms).

| Symptom | Cause | Fix |
|---------|-------|-----|
| Raw `us-gaap:*` QNames instead of labels | `concept_labels` empty: the stage failed or was skipped | `bootstrap_db.py --phase data --only concept-labels`, with `ARELLE_INTERNET_CONNECTIVITY=offline`. |
| Segment / geography rows show raw member QNames | `dimension_catalog.member_label` never populated | `--dimension-members`. |
| Company typeahead returns nothing | `entity_master` empty, or `pg_trgm` missing | Run the `registries` step; `CREATE EXTENSION pg_trgm;`. |
| Citations don't anchor to the source document | `fact_dom_position` empty | Run `dom-positions`; it needs `canonical.htm`, so check `canonical-html` actually processed filings. |
| Narrative answers empty, numeric ones fine | Qdrant collections empty, or `chunk_metadata` never populated | Chunk, then `load_to_vectordb_pipeline --all`; confirm `points_count > 0`. |
| Trace Explorer empty or erroring | `audit` schema not applied | `bootstrap_db.py --phase schema`. |
| "I couldn't resolve the company/period", or degraded to narrative | Ambiguous or unresolved entity or period | `entity_master` / `fiscal_calendar` must hold the filer and year; the dispatcher degrades on ambiguity by design. |
| Plan generation fails | The LLM produced an invalid plan after retries and one tier escalation | Check the provider key; read the trace's plan events; the validator rejects bad ops and forward references. |
| Numbers look wrong | Wrong period, unit or concept chosen | Read the `numeric_lineage` event's `resolution_notes` in the trace. The lookup picks one unit (reporting currency by default), orders concepts by `concept_pref_rank`, and treats rounded duplicates as one value. Check the expected value too. |
| Right fact found, but the answer refused or `AMBIGUOUS` | Low confidence (two concepts, two precisions, a label spelled differently) | The trace holds the candidates; ties are refused on purpose rather than guessed. |
| Statements dropped from the answer | The faithfulness verifier marked them unsupported | Expected when a claim lacks support; check that the cited facts and chunks exist. |
| LLM 401 / 429 | Key missing, or rate limited | Set the provider key for the active profile; the gateway has a rate limiter and bounded retries. |

## API / UI

| Symptom | Cause | Fix |
|---------|-------|-----|
| Observability pages show "store not reachable" | Audit DB down | Check `audit_db` in `/api/health`; start Postgres or apply the audit DDL. |
| `query_id` is null in the answer | The legacy `basic`/`hyde`/`subquery` path has no trace | Use `mode: auto` or `xbrl`. |
| UI can't reach the API | Wrong origin or proxy target | Set `XBRLRAG_UI_ORIGINS` (API) and `VITE_BFF_TARGET` (UI); defaults are 5173 ↔ 8000. |
| SSE stream ends with an error | Pipeline exception | The terminal `result` event carries `error`; check `/api/logs` and the trace. |
| `/api/logs` empty or missing lines | It tails the API process's own log; eval, ingest and scripts write their own `logs/xbrlrag_<process>_<pid>.log` | Read that file, or set `XBRLRAG_LOG_FILE` to it. |

## v1 Analyst API / MCP

See [guide 9](./09-programmatic-access-rest-and-mcp.md).

| Symptom | Cause | Fix |
|---------|-------|-----|
| App fails to start after a dependency update (`Router.__init__() got an unexpected keyword 'on_startup'`) | An old FastAPI (before Starlette 1.0 support) is running against `starlette>=1.0` | Reinstall the pinned set from `requirements-serve.txt` (`fastapi==0.162.0`, `starlette>=1.3.1,<2`, `mcp>=1.30,<2`). |
| `/api/v1/facts/lookup` returns `unresolved: true` | Ticker or name not in `entity_master` | Check `GET /api/v1/coverage?ticker=…`; populate the registries. |
| `FactPoint.status` `NOT_FOUND` for a real company | No filing ingested for that fiscal year | Check `GET /api/v1/coverage`. |
| `status` `AMBIGUOUS` / `UNRESOLVED_DIMENSION` | Broad or misspelled `concept` / `dimension` hint | Use a `concept_family` from `GET /api/v1/concepts`; narrow the member. |
| MCP host discovers no tools | Server not running, or wrong transport or URL | stdio: the host spawns `python -m src.mcp_server.server`; http: `XBRLRAG_MCP_TRANSPORT=streamable-http` and `http://<host>:8765/mcp`. |

## Evaluation ([guide 11](./11-evaluation.md))

| Symptom | Cause | Fix |
|---------|-------|-----|
| `relation "eval.eval_run" does not exist`, or a missing `result_payload` column | Eval DDL not applied or outdated; `bootstrap_db.py` doesn't create it | `conda run -n py312 python scripts/ddl/eval/_apply.py`. |
| `/tech/eval` lists runs but trace links 404 | The run was taken without `--persist-audit` | Re-run with `--persist-audit`. |
| A whole run lost at persist time | A NUL byte in a new persisted field: Postgres rejects U+0000 and the write is atomic | Route the field through `src/db/scrub.py` `scrub_nul`. |
| Accuracy jumped or dropped implausibly | A narrowed run (`--kind`, `--rerun-failed`, `--sample`) is `tier='partial'` over a biased subset | Compare only full-suite runs, or re-grade with `compare_runs.py`. |
| High `n_error`, concentrated in one kind | Transient LLM or backend failures, not quality | `--resume <run_id> --retry-errors transport`, or re-run the slice. |
| A run overspent `--max-cost-usd` | The cap is checked between cases; a running case can overshoot | Size small-slice caps per case. |
| Narrative cases crash the process | In-process reranker under unbounded batching | They run serialized by design (`DEFAULT_KIND_CONCURRENCY`); don't raise it. `RAGINFER_URL` moves reranking out of process. |

## General diagnostics

```powershell
curl http://localhost:8000/api/health            # DB + pipeline import status
curl http://localhost:6333/collections           # Qdrant collections
conda run -n py312 python scripts/dbq.py "SELECT count(*) FROM mv_financial_facts"
curl http://localhost:8000/api/traces/<query_id> # one query's trace
curl http://localhost:8000/api/lineage/<query_id>
curl http://localhost:8000/api/ingestion/runs    # ingestion health
```

Raise verbosity with `LOG_LEVEL=DEBUG` (or `--log-level DEBUG` on the ingest CLIs). Most ingest
CLIs accept `--dry-run`.

## Related

[Fact-store data model](../../design/XbrlRag_Materialized_Views.md) ·
[Observability & tracing](../../design/XbrlRag_Observability_Tracing.md) ·
[`analyst_queries.sql`](../../../scripts/xbrl/analyst_queries.sql) ·
[9. Programmatic access](./09-programmatic-access-rest-and-mcp.md)
