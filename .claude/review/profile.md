---
python: "conda run --no-capture-output -n py312 python"
env: {"PYTHONPATH": "{repo}"}
gate: ["{python} -m ruff check {changed_py}", "{python} -m pytest -q {tests}"]
tests_map: {"src/retriever/xbrl/**": ["test/unit/test_xbrl_factstore_lookup.py", "test/unit/test_xbrl_plan_executor.py", "test/unit/test_xbrl_plan_validator.py", "test/unit/test_xbrl_plan_generator.py", "test/unit/test_xbrl_calculator_tool.py", "test/unit/test_xbrl_screen_and_decompose.py", "test/unit/test_xbrl_faithfulness_verifier.py", "test/unit/test_xbrl_synthesizer_degrade.py"],
  "src/ingest/sec_edgar/**": ["test/unit/test_arelle_ingest_funnel.py", "test/unit/test_arelle_subprocess.py", "test/unit/test_arelle_cmd_resolution.py", "test/unit/test_filing_store.py", "test/unit/test_filing_store_moto.py", "test/unit/test_ingest_s3_funnel.py", "test/unit/test_html_canonicalizer_funnel.py", "test/unit/test_xbrl_html_dom_parser.py", "test/unit/test_publish_gate.py", "test/unit/test_storage_root_resolution.py", "test/unit/test_ingestion_reliability.py", "test/unit/test_member_alias_detector.py"],
  "src/chunking/**": ["test/unit/test_xbrl_html_base_chunking.py"],
  "src/vectorstores/**": ["test/unit/test_qdrant_client_env.py", "test/unit/test_merge_search_results.py"],
  "src/embeddings/**": ["test/unit/test_embeddings.py", "test/unit/test_remote_embedder.py", "test/unit/test_remote_reranker.py", "test/unit/test_torch_free_contract.py"],
  "src/pipelines/xbrl/**": ["test/unit/test_query_cache.py", "test/unit/test_xbrl_screen_and_decompose.py", "test/unit/test_xbrl_plan_executor.py"],
  "src/llm/**": ["test/unit/test_llm.py", "test/unit/test_llm_gateway.py", "test/unit/test_llm_features.py", "test/unit/test_escalation.py", "test/unit/test_run_budget.py", "test/unit/test_instrumented_openai_client.py", "test/unit/test_xbrl_gateway_factory.py", "test/unit/test_prompt_cache_tokens.py", "test/unit/test_model_ref.py"],
  "prompts/**": ["test/unit/test_prompt_registry.py", "test/unit/test_prompt_versioning.py", "test/unit/test_prompt_matrix.py", "test/unit/test_xbrl_plan_validator.py"],
  "src/eval/**": ["test/unit/test_eval_core.py", "test/unit/test_numeric_grader.py", "test/unit/test_sql_derived_suite.py", "test/unit/test_eval_store_api.py", "test/unit/test_drift.py", "test/unit/test_regression_runner.py", "test/unit/test_prompt_matrix.py"],
  "configs/eval/**": ["test/unit/test_sql_derived_suite.py", "test/unit/test_eval_core.py"],
  "scripts/eval/**": ["test/unit/test_sql_derived_suite.py", "test/unit/test_regression_runner.py", "test/unit/test_drift.py"],
  "test/eval/**": ["test/unit/test_sql_derived_suite.py", "test/unit/test_numeric_grader.py", "test/unit/test_drift.py"],
  "scripts/ddl/**": ["test/unit/test_layer0_ddl_idempotent.py", "test/integration/test_audit_views_live.py", "test/unit/test_xbrl_factstore_lookup.py"],
  "src/api/**": ["test/unit/test_api_v1.py", "test/unit/test_api_pipeline_probe.py", "test/unit/test_eval_store_api.py", "test/unit/test_metrics_exporter.py"],
  "src/mcp_server/**": ["test/unit/test_mcp_server.py", "test/unit/test_api_v1.py"],
  "src/observability/**": ["test/unit/test_trace_context.py", "test/unit/test_audit_store.py", "test/integration/test_audit_views_live.py", "test/unit/test_ingestion_audit.py", "test/unit/test_metrics_exporter.py"],
  "src/db/**": ["test/unit/test_db_conn.py", "test/unit/test_deploy_config.py"],
  "src/config/**": ["test/unit/test_deploy_config.py", "test/unit/test_storage_root_resolution.py"],
  "src/__init__.py": ["test/unit/test_deploy_config.py", "test/unit/test_storage_root_resolution.py"],
  "scripts/harness/**": ["test/unit/test_feature_manifest.py", "test/unit/test_reaudit.py", "test/unit/test_watchdog.py", "test/unit/test_subagents.py"],
  "test/unit/**": ["test/unit"], "test/integration/**": ["test/unit"]}
gate_env_failures: ["psycopg2\\.OperationalError", "could not connect to server", "Connection refused", "xbrl_db.*(not reachable|does not exist)", "relation \".*\" does not exist", "(?i)qdrant.*(Connection|Unavailable)", "RAGINFER_URL", "ARELLE_CMD", "FileNotFoundError.*data[/\\\\]"]
contract_surfaces: ["prompts/templates/xbrl/**", "prompts/prompt_registry.py", "src/llm/config.py", "src/retriever/xbrl/plan_validator.py", "src/retriever/xbrl/plan_executor.py", "src/retriever/xbrl/llm_xbrl_synthesizer.py", "src/retriever/xbrl/llm_xbrl_faithfulness_verifier.py", "src/vectorstores/xbrl_qdrant_vectordb_client.py", "src/chunking/xbrl_html_base_chunking.py", "scripts/ddl/**", "scripts/xbrl/analyst_queries.sql", "src/ingest/sec_edgar/xbrl/promotion.py", "src/api/v1/router.py", "src/mcp_server/server.py", "src/observability/metrics_exporter.py", "configs/monitoring/**", "configs/eval/sql_derived_templates.yaml", "test/eval/suites/sql_derived_v1.json", "test/eval/golden/*.json", "test/unit/test_torch_free_contract.py", "src/__init__.py", ".env.example", "deploy/**", "infra/**"]
hotspots: ["src/__init__.py", "src/llm/**", "src/observability/trace_context.py", "src/db/conn.py", "src/config/deploy.py", "src/logging_config.py", "src/ingest/sec_edgar/storage.py", "src/retriever/xbrl/xbrl_factstore_lookup.py", "src/eval/core/**"]
criticality: {"outputs": {
    "facts": ["src/retriever/xbrl/{xbrl_factstore_lookup,xbrl_factstore_screen,xbrl_calculator_tool,plan_executor}.py"],
    "answer": ["src/pipelines/xbrl/retrieval_pipeline.py", "src/retriever/xbrl/{llm_xbrl_synthesizer,llm_xbrl_faithfulness_verifier}.py"],
    "narrative": ["src/retriever/xbrl/xbrl_narrative_retriever.py"],
    "ingest": ["src/ingest/sec_edgar/ingest/__main__.py", "src/ingest/sec_edgar/xbrl/promotion.py"],
    "index": ["src/pipelines/xbrl/load_to_vectordb_pipeline.py"],
    "eval": ["src/eval/core/graders/dispatch.py"]},
  "data_edges": ["src/ingest/sec_edgar/ingest/** -> src/retriever/xbrl/{xbrl_factstore_lookup,xbrl_factstore_screen}.py",
    "scripts/ddl/{base,layer_0,mv,populate}/*.{sql,ddl} -> src/retriever/xbrl/{xbrl_factstore_lookup,xbrl_factstore_screen}.py",
    "src/ingest/sec_edgar/xbrl/promotion.py -> src/pipelines/xbrl/query_cache.py",
    "src/ingest/sec_edgar/xbrl/html_canonicalizer.py -> src/chunking/xbrl_html_base_chunking.py",
    "src/pipelines/xbrl/load_to_vectordb_pipeline.py -> src/retriever/xbrl/xbrl_narrative_retriever.py",
    "prompts/templates/xbrl/** -> prompts/prompt_registry.py",
    "scripts/xbrl/analyst_queries.sql -> src/eval/suites/**"],
  "non_value": ["src/observability/**", "src/api/routes/observability.py", "src/api/routes/metrics_prom.py",
    "src/logging_config.py", "src/mcp_server/**", "src/eval/core/{report,store}.py",
    "src/{ingest/pdfparse,preprocess,pipelines/sec10k,eval/deepeval}/**",
    "src/llm/sec10k_table_summary_reader.py",
    "src/retriever/{base_retriever,hyde_retriever,llm_query_rewriter,llm_query_router,bge_reranker}.py"],
  "pins": {"trunk": ["src/retriever/xbrl/plan_validator.py", "src/llm/config.py", "src/llm/run_budget.py",
    "src/vectorstores/xbrl_qdrant_vectordb_client.py", "scripts/ddl/mv/10_mv_financial_facts.sql"]}}
---
# XbrlRag — review profile (review-core pack)

The frontmatter drives the review-core scripts (gate, test scoping, blast map). This prose is
passed to reviewers verbatim. XbrlRag answers analyst questions over SEC filings with cited,
audited numbers: every figure must resolve to an XBRL `fact_id` through deterministic SQL, and
the LLM only narrates. The XBRL/iXBRL flow is the product; the PDF/VLM flow
(`src/ingest/pdfparse`, `src/preprocess`, `src/retriever/*` outside `xbrl/`, `src/pipelines/sec10k`,
`src/eval/deepeval`) is maintenance-only.

## Code map

TRUNK is about 44% of mapped modules, above `codemap.py build`'s 40% warning, and that is
deliberate. The XBRL product is most of the code: the fact-store schema SQL
(`scripts/ddl/{base,layer_0,mv,populate}`), the prompt templates and the query, ingest and LLM
modules all sit on a declared output's value path. Audit/eval DDL and the legacy PDF flow are kept
off TRUNK (narrowed data edge, `non_value`). The rest of the share is the infra band (the top 10%
by fan-in), which is relative: marking more modules `non_value` only promotes the next ones. Do
not report the share as a finding; do report a README, attribute file or audit/eval DDL that
shows up as TRUNK.

## Coding standard

Review touched code against the Google Python Style Guide with the house deviations in the global
`rules/python.md` (refactored units in full, small edits on the changed lines only), plus
`.claude/rules/rag.md`. Tool-checked rules (ruff, `tools/standards/check_standards.py`) are not
review findings unless the tools were bypassed or their config loosened.

## Data/runtime availability

- **Gate = ruff + pytest only.** ruff reads `ruff.toml` (tier 0: E/W/F, I, UP, B, N, G004,
  RUF013, RUF100, PGH003); existing findings are listed per file in `ruff-burndown.toml`, which
  may only shrink (the `check_standards.py ratchet`). A finding covered by the burn-down list is
  not a review finding; a new entry or a loosened rule is. There is no mypy config
  (`standards.toml` sets `mypy_at_stop = false`), so mypy is not gated. Tests live in `test/`
  (singular), not `tests/`; `pytest.ini` only defines the `integration` marker.
- **Many "unit" tests depend on live local services and silently SKIP when absent**:
  `test_xbrl_factstore_lookup.py` (`requires_db`, live Postgres `xbrl_db`), `test_audit_store.py`,
  `test_ingestion_audit.py`, `test_metrics_exporter.py` (audit schema),
  and everything in `test/integration/` (Postgres + Qdrant + OpenAI; the audit-views round trip
  is `test_audit_views_live.py` there). A green run with skips is
  **not** evidence the SQL paths work. Record the skipped DB-backed tests under **Skipped**; never
  count them as a pass. Check with `pytest -q -rs <files>`.
- `data/` (gitignored) holds raw filings, `canonical.htm`, vector DB and temp artifacts. Chunking,
  DOM-parser and canonicalizer tests may look there; absence is an environment gap, not a defect
  in the change. `snapshot_links` is deliberately omitted: unit tests build their own fixtures.
- The committed eval corpus `test/eval/suites/sql_derived_v1.json` (501 cases) and
  `test/eval/golden/*.json` are real files; `test_sql_derived_suite.py` runs against them.
- Never call live services during a review: no OpenAI, no Qdrant, no Postgres writes, no Arelle,
  no EDGAR. Measured accuracy lives in the Postgres `eval` schema (`/tech/eval`), not in the repo;
  a reviewer cannot re-measure it and must say so rather than guess.
- `XBRLRAG_DEPLOY_MODE=local` is the default; S3 paths are covered by `moto` tests only.

## Contract-surface notes (beyond the globs)

- **Fact-store schema** (`scripts/ddl/layer_0`, `mv/` = 18 managed MVs, `audit/`, `eval/`):
  query code selects named columns from `mv_financial_facts`, `mv_financial_facts_multidim`,
  `mv_geographic_facts`, `mv_segment_matrix`, `mv_derived_metrics`, `publish_manifest`,
  `query_cache_epoch`. A column rename/removal is a contract break even when no test fails
  (the DB-backed tests skip).
- **Prompts** are resolved by `(stage, provider, series, version)` from
  `prompts/templates/xbrl/registry.yaml`; the JSON response formats in the synthesizer/verifier
  modules and the `KNOWN_CONCEPT_FAMILIES` set in `plan_validator.py` must match the template text.
- **Qdrant payload schema** (`XBRLChunkPayload`, `_to_payload_dict`, `_XBRL_PAYLOAD_INDEXES`) is
  shared by the loader (`load_to_vectordb_pipeline.py`), the narrative retriever and the UI/API;
  collections are `xbrl_sec10k_text` / `xbrl_sec10k_metadata` (legacy PDF `sec10k_*` is separate).
- **Serving image is torch-free:** imports reachable from query-time modules must route through
  `src/retriever/endpoints.py` and be listed in `TORCH_FREE_MODULES`
  (`test/unit/test_torch_free_contract.py`).
- **Error contract differs by surface** (`src/api/CLAUDE.md`): BFF routes swallow errors into a 200
  with an `error` field; `/api/v1` returns real status codes; MCP returns `{"error","status_code"}`
  and must never write to stdout. Do not flag either behavior as inconsistent.
- **Env vars:** `XBRLRAG_*` is canonical; legacy `ARAG_*` is still honored (mirrored in
  `src/__init__.py`, new name wins).
- **Model tiers** come only from the active profile in `configs/llm/llm.yaml` (`XBRL_STAGE_TIERS` maps stage → tier); a model name
  hardcoded in a stage or doc is a defect.

## Terminology (do not flag)

- **ARag was renamed XbrlRag.** Lowercase `arag` is usually intentional: ECR images `arag*`,
  `/opt/arag/.env`, `arag-*` host scripts, `genai-dev-arag-*` bucket/tags, RDS role `arag_admin`,
  Prometheus metrics `arag_*`, alerts `Arag*`, groups/job `arag-xbrl*`, MCP server name
  `arag-analyst`, UI storage keys `arag:theme` / `arag:answers`, legacy `ARAG_*` env vars. Flag
  only a change that mixes old and new names *within one contract*, or renames a kept name without
  the infra repo.
- `src/eval/xbrl/numeric.py` is a re-export shim over `src/eval/core/graders/numeric.py`; edits
  belong in the latter.
- `src/llm/instrumented_openai_client.py` is a thin OpenAI-pinned facade over `LLMGateway`.
- `scripts/rebuild_dependent_mvs.py` is a deprecated shim; `scripts/rebuild_mvs.py` is current.
- `fy` in plan steps is the fiscal year as resolved by `EntityExtractor`/`fiscal_calendar`, not the
  calendar year of `period_end`.

## Exclusions (project-wide non-findings)

- Absence of auth on `src/api` and `src/mcp_server` (documented trusted-network trust model).
- The BFF returning 200 + `error`, and v1 raising real status codes (documented, deliberate).
- Query-time LLM-call fallbacks that degrade (`QueryDecomposer` any error -> single passthrough,
  `XbrlRagSynthesizer._degraded_response`, verifier `PARTIALLY_SUPPORTED` on a failed batch) are
  deliberate, provided they still mark `fully_answered=False` / record verifier status.
- `GATE-B` (completeness + sum-of-parts) being advisory unless `XBRLRAG_PUBLISH_GATE_B_ENFORCE=1`.
- A publish gate that is inert when `publish_manifest` is absent, and filings with no manifest row
  being served (backward compatibility, documented in `xbrl_factstore_lookup.py`).
- Churn from `ruff check --fix` (run on every edit via hook; there is no formatter) and line
  length.
- Legacy PDF-flow code and `src/eval/deepeval`: maintenance-only; flag only regressions or
  breakage, never missing features.
- `ui/`, `notebooks/`, `examples/`, `models/`, `benchmarks/` (not reviewed here).
