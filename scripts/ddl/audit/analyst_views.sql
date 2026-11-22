-- Analyst / ops reporting views over the audit.* tables.
-- Phase 3 (analytics layer) of
-- docs/design/XbrlRag_Observability_Tracing.md.
--
-- Read-only views that turn the raw query-side and ingestion-side audit data
-- into at-a-glance metrics: cost, latency percentiles, branch mix, LLM
-- economics, the numeric-lineage audit trail, and ingestion health.
--
-- REQUIRES the base tables (query_audit.sql, ingestion_runs.sql) to exist first.
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/audit/analyst_views.sql

CREATE SCHEMA IF NOT EXISTS audit;

-- ======================================================================
-- Query side
-- ======================================================================

-- Most-recent queries, trimmed for a quick scan.
CREATE OR REPLACE VIEW audit.v_recent_queries AS
SELECT
    query_id,
    created_at,
    left(query, 80)            AS query,
    branch,
    intent,
    degraded,
    fully_answered,
    round(total_latency_ms)    AS latency_ms,
    total_cost_usd             AS cost_usd,
    llm_calls,
    total_retries,
    error
FROM audit.query_audit
ORDER BY created_at DESC;

-- Daily volume / cost / reliability rollup.
CREATE OR REPLACE VIEW audit.v_query_daily AS
SELECT
    date_trunc('day', created_at)                       AS day,
    count(*)                                            AS queries,
    count(*) FILTER (WHERE error IS NOT NULL)           AS errored,
    count(*) FILTER (WHERE degraded)                    AS degraded,
    count(*) FILTER (WHERE fully_answered)              AS fully_answered,
    round(sum(total_cost_usd), 4)                       AS cost_usd,
    round(avg(total_cost_usd), 6)                       AS avg_cost_usd,
    sum(total_tokens)                                   AS tokens,
    sum(llm_calls)                                      AS llm_calls,
    sum(total_retries)                                  AS retries,
    round(avg(total_latency_ms))                        AS avg_latency_ms,
    round((percentile_cont(0.95)
           WITHIN GROUP (ORDER BY total_latency_ms))::numeric) AS p95_latency_ms
FROM audit.query_audit
GROUP BY 1
ORDER BY 1 DESC;

-- Overall end-to-end latency percentiles (SLA view).
CREATE OR REPLACE VIEW audit.v_query_latency AS
SELECT
    count(*)                                                                     AS queries,
    round(avg(total_latency_ms))                                                 AS avg_ms,
    round((percentile_cont(0.50) WITHIN GROUP (ORDER BY total_latency_ms))::numeric) AS p50_ms,
    round((percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms))::numeric) AS p95_ms,
    round((percentile_cont(0.99) WITHIN GROUP (ORDER BY total_latency_ms))::numeric) AS p99_ms,
    round(max(total_latency_ms))                                                 AS max_ms
FROM audit.query_audit;

-- Per-stage latency percentiles (where does the time go?). Unnests the
-- stage_latency_ms JSONB into (stage, ms) samples.
CREATE OR REPLACE VIEW audit.v_stage_latency AS
SELECT
    stage,
    count(*)                                                          AS samples,
    round(avg(ms))                                                    AS avg_ms,
    round((percentile_cont(0.50) WITHIN GROUP (ORDER BY ms))::numeric) AS p50_ms,
    round((percentile_cont(0.95) WITHIN GROUP (ORDER BY ms))::numeric) AS p95_ms,
    round((percentile_cont(0.99) WITHIN GROUP (ORDER BY ms))::numeric) AS p99_ms
FROM (
    SELECT kv.key AS stage, kv.value::double precision AS ms
    FROM audit.query_audit q,
         LATERAL jsonb_each_text(COALESCE(q.stage_latency_ms, '{}'::jsonb)) AS kv
) s
GROUP BY stage
ORDER BY p95_ms DESC NULLS LAST;

-- Routing distribution: which branch/intent, how often, how costly, how often degraded.
CREATE OR REPLACE VIEW audit.v_branch_mix AS
SELECT
    branch,
    intent,
    count(*)                                              AS queries,
    round(100.0 * count(*) / sum(count(*)) OVER (), 1)    AS pct,
    count(*) FILTER (WHERE degraded)                      AS degraded,
    round(avg(total_cost_usd), 6)                         AS avg_cost_usd,
    round(avg(total_latency_ms))                          AS avg_latency_ms
FROM audit.query_audit
GROUP BY branch, intent
ORDER BY queries DESC;

-- LLM economics per stage+provider+model(+series/tier)+prompt_version, sourced
-- from the per-call events. provider / model_series / model_tier were added in
-- the LLM-harness refactor (Phase 1); rows predating it carry NULL there.
-- DROP first: columns inserted mid-list, which CREATE OR REPLACE forbids.
DROP VIEW IF EXISTS audit.v_llm_calls;
CREATE OR REPLACE VIEW audit.v_llm_calls AS
SELECT
    payload->>'stage'                                      AS stage,
    payload->>'provider'                                   AS provider,
    payload->>'model'                                      AS model,
    payload->>'model_series'                               AS model_series,
    payload->>'model_tier'                                 AS model_tier,
    payload->>'prompt_version'                             AS prompt_version,
    count(*)                                               AS calls,
    count(*) FILTER (WHERE payload->>'outcome' = 'error')  AS errors,
    count(*) FILTER (WHERE payload->>'outcome' = 'refusal') AS refusals,
    sum((payload->>'retries')::int)                        AS retries,
    round(avg((payload->>'latency_ms')::double precision)) AS avg_latency_ms,
    round((percentile_cont(0.95)
           WITHIN GROUP (ORDER BY (payload->>'latency_ms')::double precision))::numeric) AS p95_latency_ms,
    sum((payload->>'prompt_tokens')::int)                  AS prompt_tokens,
    sum((payload->>'completion_tokens')::int)              AS completion_tokens,
    round(sum((payload->>'cost_usd')::numeric), 4)         AS cost_usd
FROM audit.query_event
WHERE kind = 'llm_call'
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY calls DESC;

-- Cost rollup per model (chargeback / budget): successful calls plus refusals,
-- whose cost_usd is non-zero only when the provider bills them.
CREATE OR REPLACE VIEW audit.v_model_cost AS
SELECT
    payload->>'model'                              AS model,
    count(*)                                       AS calls,
    sum((payload->>'prompt_tokens')::int)          AS prompt_tokens,
    sum((payload->>'completion_tokens')::int)      AS completion_tokens,
    round(sum((payload->>'cost_usd')::numeric), 4) AS cost_usd
FROM audit.query_event
WHERE kind = 'llm_call' AND payload->>'outcome' IN ('ok', 'refusal')
GROUP BY 1
ORDER BY cost_usd DESC NULLS LAST;

-- Model escalations: where a stage climbed a rung because a cheaper model
-- deteriorated (LLM-harness refactor Phase 4). Tracks how often each
-- stage/reason/tier-hop fires so model tiers (XBRL_QUERY_MODEL_TIERS) and
-- prompt versions can be re-tuned over time.
CREATE OR REPLACE VIEW audit.v_escalations AS
SELECT
    payload->>'stage'        AS stage,
    payload->>'reason'       AS reason,
    payload->>'from_model'   AS from_model,
    payload->>'to_model'     AS to_model,
    payload->>'from_tier'    AS from_tier,
    payload->>'to_tier'      AS to_tier,
    count(*)                 AS escalations,
    count(DISTINCT query_id) AS affected_queries
FROM audit.query_event
WHERE kind = 'model_escalation'
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY escalations DESC;

-- The numeric-lineage audit trail, flattened for querying. Every derived /
-- looked-up number that was shown to a user, tied to its source fact(s).
CREATE OR REPLACE VIEW audit.v_numeric_lineage AS
SELECT
    e.query_id,
    e.created_at,
    e.payload->>'op'             AS op,
    e.payload->>'step_id'        AS step_id,
    e.payload->>'status'         AS status,
    e.payload->>'expr'           AS expr,
    e.payload->>'result'         AS result,
    e.payload->'bindings'        AS bindings,
    e.payload->'source_fact_ids' AS source_fact_ids,
    e.payload->>'fact_id'        AS fact_id,
    e.payload->>'concept_family' AS concept_family,
    e.payload->>'cik'            AS cik,
    e.payload->>'fy'             AS fy
FROM audit.query_event e
WHERE e.kind = 'numeric_lineage';

-- Auditor question: "which answers used fact N?" — checks both a lookup's own
-- fact_id and any compute step that consumed it via source_fact_ids.
CREATE OR REPLACE FUNCTION audit.queries_for_fact(p_fact_id BIGINT)
RETURNS TABLE (query_id TEXT, created_at TIMESTAMPTZ, op TEXT, step_id TEXT, expr TEXT, result TEXT)
LANGUAGE sql STABLE AS $$
    SELECT e.query_id, e.created_at,
           e.payload->>'op', e.payload->>'step_id', e.payload->>'expr', e.payload->>'result'
    FROM audit.query_event e
    WHERE e.kind = 'numeric_lineage'
      AND (
          e.payload->>'fact_id' = p_fact_id::text
          OR EXISTS (
              SELECT 1
              FROM jsonb_each_text(COALESCE(e.payload->'source_fact_ids', '{}'::jsonb)) kv
              WHERE kv.value = p_fact_id::text
          )
      );
$$;

-- Queries where the faithfulness verifier could not complete (R6 signal).
CREATE OR REPLACE VIEW audit.v_verifier_failures AS
SELECT
    q.query_id,
    q.created_at,
    left(q.query, 80)        AS query,
    e.payload->>'status'     AS verifier_status,
    e.payload->>'error'      AS error
FROM audit.query_event e
JOIN audit.query_audit q USING (query_id)
WHERE e.kind = 'verifier_error'
ORDER BY q.created_at DESC;

-- ======================================================================
-- Ingestion side
-- ======================================================================

-- Most-recent ingestion runs.
CREATE OR REPLACE VIEW audit.v_ingestion_runs AS
SELECT
    run_id,
    pipeline,
    scope,
    dry_run,
    status,
    started_at,
    round(duration_ms)  AS duration_ms,
    items_total,
    items_succeeded,
    items_skipped,
    items_failed,
    host
FROM audit.ingestion_run
ORDER BY started_at DESC;

-- Daily ingestion throughput per pipeline.
CREATE OR REPLACE VIEW audit.v_ingestion_daily AS
SELECT
    date_trunc('day', started_at) AS day,
    pipeline,
    count(*)                      AS runs,
    sum(items_succeeded)          AS succeeded,
    sum(items_skipped)            AS skipped,
    sum(items_failed)             AS failed,
    round(avg(duration_ms))       AS avg_duration_ms
FROM audit.ingestion_run
GROUP BY 1, 2
ORDER BY 1 DESC;

-- Failure triage: counts per class, split retryable vs permanent.
CREATE OR REPLACE VIEW audit.v_ingestion_failures AS
SELECT
    failure_class,
    CASE WHEN failure_class = 'timeout' THEN 'retryable' ELSE 'permanent' END AS retryability,
    count(*)              AS failures,
    count(DISTINCT run_id) AS runs_affected
FROM audit.ingestion_run_item
WHERE status IN ('failed', 'error')
GROUP BY failure_class
ORDER BY failures DESC;

-- Retry effectiveness: did re-running transient failures actually help?
CREATE OR REPLACE VIEW audit.v_ingestion_retries AS
SELECT
    count(*) FILTER (WHERE attempts > 1)                                      AS items_retried,
    count(*) FILTER (WHERE attempts > 1 AND status = 'success')              AS retried_then_succeeded,
    count(*) FILTER (WHERE attempts > 1 AND status IN ('failed', 'error'))   AS retried_still_failed,
    coalesce(max(attempts), 1)                                               AS max_attempts
FROM audit.ingestion_run_item;
