-- Evaluation-run persistence for the XBRL flow.
-- docs/design/XbrlRag_Evaluation.md.
--
-- Durable, queryable home for what src/eval/core/runner.py produces:
--   * one `eval.eval_run` row per suite x system run (the scorecard), and
--   * one `eval.eval_case_result` row per graded case (the drill-down).
--
-- A dedicated `eval` schema keeps dev/measurement data separable from the `audit`
-- schema's production request trail: different retention, different grants, and an
-- eval run must never be mistaken for user traffic in the cost/latency dashboards.
--
-- No FK from eval_case_result.query_id to audit.query_audit -- deliberately, and for
-- the same reason audit.query_event carries no FK into the fact tables. The two
-- schemas are pruned on different policies, so a dangling query_id must degrade the
-- UI's trace link, not break the eval record.
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/eval/eval_runs.sql

CREATE SCHEMA IF NOT EXISTS eval;

-- ======================================================================
-- One row per run: the scorecard the /tech/eval list renders.
-- ======================================================================
CREATE TABLE IF NOT EXISTS eval.eval_run (
    run_id               TEXT PRIMARY KEY,          -- RunReport.run_id
    suite                TEXT NOT NULL,             -- sql_derived | golden_xbrl | ...
    suite_version        TEXT,
    system               TEXT NOT NULL,             -- xbrlrag_full | xbrlrag_naive_rag | ...
    tier                 TEXT,                      -- smoke | standard | full
    git_sha              TEXT,
    -- Results are only comparable within one corpus state: a run that "improved" may
    -- simply have had more filings loaded. The compare view refuses to diff across
    -- two fingerprints without an explicit override.
    corpus_fingerprint   TEXT,
    started_at           TIMESTAMPTZ NOT NULL,
    finished_at          TIMESTAMPTZ,
    status               TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING|OK|ABORTED|ERROR
    n_planned            INT  NOT NULL DEFAULT 0,   -- cases selected
    n_cases              INT  NOT NULL DEFAULT 0,   -- cases actually graded
    n_skipped            INT  NOT NULL DEFAULT 0,   -- not attempted (budget abort)
    n_correct            INT  NOT NULL DEFAULT 0,
    n_partial            INT  NOT NULL DEFAULT 0,
    n_incorrect          INT  NOT NULL DEFAULT 0,
    n_refused            INT  NOT NULL DEFAULT 0,
    n_error              INT  NOT NULL DEFAULT 0,
    accuracy             DOUBLE PRECISION,
    citation_rate        DOUBLE PRECISION,
    grounding_precision  DOUBLE PRECISION,          -- the *right* fact, not merely a fact
    grounding_recall     DOUBLE PRECISION,
    silent_wrong_rate    DOUBLE PRECISION,          -- confident + cited + wrong: release gate
    refusal_rate         DOUBLE PRECISION,          -- the counterweight to silent-wrong
    error_rate           DOUBLE PRECISION,
    paraphrase_stability DOUBLE PRECISION,          -- routing determinism
    total_cost_usd       NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_p50_ms       DOUBLE PRECISION,
    latency_p95_ms       DOUBLE PRECISION,
    by_kind              JSONB,                     -- {kind: {accuracy, n, ...}}
    by_tag               JSONB,
    config               JSONB,                     -- runner config (workers, budget)
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_run_created  ON eval.eval_run (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_eval_run_suite    ON eval.eval_run (suite, system);
CREATE INDEX IF NOT EXISTS ix_eval_run_corpus   ON eval.eval_run (corpus_fingerprint);

-- ======================================================================
-- One row per graded case: the failure drill-down.
-- ======================================================================
CREATE TABLE IF NOT EXISTS eval.eval_case_result (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES eval.eval_run (run_id) ON DELETE CASCADE,
    case_id             TEXT NOT NULL,
    kind                TEXT,                       -- lookup|compute|dimensional|screen|breakdown|narrative
    grade               TEXT NOT NULL,              -- CORRECT|PARTIAL|INCORRECT|REFUSED|HEDGED_MISS|IMPRECISE|ERROR
    passed              BOOLEAN NOT NULL,
    silent_wrong        BOOLEAN NOT NULL DEFAULT FALSE,
    question            TEXT,
    expected            JSONB,                      -- {value|rows|text, fact_ids}
    produced            JSONB,                      -- {values, rows, fact_ids, refused, ...}
    matched_value       DOUBLE PRECISION,
    has_citation        BOOLEAN,
    grounding_precision DOUBLE PRECISION,
    grounding_recall    DOUBLE PRECISION,
    screen_metrics      JSONB,                      -- ranking / component-coverage detail
    reason              TEXT,
    error               TEXT,
    latency_ms          DOUBLE PRECISION,
    cost_usd            NUMERIC(12, 6),
    -- Correlation key into audit.query_audit. This is what lets a failing eval case
    -- link straight to its full trace (plan, SQL, every LLM prompt) in the existing
    -- Trace Explorer -- only populated when the run enabled --persist-audit.
    query_id            TEXT,
    paraphrase_group    TEXT,                       -- same truth, different phrasing
    origin              TEXT,                       -- analyst_queries.sql#7a | ...
    ticker              TEXT,
    fy                  INT,
    tags                TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS ix_ecr_run_grade  ON eval.eval_case_result (run_id, grade);
CREATE INDEX IF NOT EXISTS ix_ecr_run_kind   ON eval.eval_case_result (run_id, kind);
CREATE INDEX IF NOT EXISTS ix_ecr_query      ON eval.eval_case_result (query_id);
CREATE INDEX IF NOT EXISTS ix_ecr_group      ON eval.eval_case_result (run_id, paraphrase_group);
CREATE INDEX IF NOT EXISTS ix_ecr_case       ON eval.eval_case_result (case_id);
CREATE INDEX IF NOT EXISTS ix_ecr_tags       ON eval.eval_case_result USING GIN (tags);

-- ======================================================================
-- Grader honesty (docs/specs/eval-grader-honesty.md). Additive and idempotent, so it
-- applies cleanly over an existing schema. Runs from before versioning keep
-- grader_version NULL: they were scored by the earlier grader and are not comparable
-- one-for-one with versioned runs.
-- ======================================================================
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS grader_version   TEXT;
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS n_hedged_miss    INT NOT NULL DEFAULT 0;
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS n_needs_review   INT NOT NULL DEFAULT 0;
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS hedged_miss_rate DOUBLE PRECISION;
-- refused + hedged misses: every way the system declined, the counterweight to accuracy
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS decline_rate     DOUBLE PRECISION;
-- an unhedged failing breakdown asserting an amount no component/subset sum explains
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT FALSE;

-- ======================================================================
-- Arm-neutral grading, grader version 3 (docs/specs/arm-neutral-grading.md).
-- Nullable on purpose: a run graded before version 3 never measured these, and a
-- default 0 would read as "no confident-wrong answers". Re-grade its stored answers
-- with scripts/eval/compare_runs.py instead.
-- ======================================================================
-- wrong and neither refused nor hedged, cited or not: the failure metric for every arm
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS n_confident_wrong    INT;
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS confident_wrong_rate DOUBLE PRECISION;
-- right numbers regardless of citations and grounding
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS value_accuracy       DOUBLE PRECISION;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS confident_wrong BOOLEAN;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS value_correct   BOOLEAN;

-- ======================================================================
-- v2 slices: split, intent, phrasing family, answer key, judge sub-scores
-- (docs/specs/eval-run-columns.md). Additive and idempotent. Values come from the
-- case tags and the judge; validation is in Python (src/eval/core/run_columns.py),
-- not CHECK constraints, so this file stays re-appliable as plain SQL.
-- ======================================================================
-- dev | validation | heldout_filers | external | temporal | legacy. NOT NULL DEFAULT
-- 'legacy': rows written before this change, and any writer that omits the column,
-- are legacy runs, so no backfill is needed and no row reads as an unknown split.
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS split         TEXT NOT NULL DEFAULT 'legacy';
-- the judge's prompt and model version; NULL when no judge ran
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS judge_version TEXT;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS intent_id                TEXT;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS phrasing_family          TEXT;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS template_id              TEXT;
-- where the answer key came from: sec_companyfacts | sec_frames | html_hand | fact_store
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS key_source               TEXT;
-- verified | single-source
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS key_status               TEXT;
-- the judge's sub-scores in [0, 1], and its count of unsupported claims; NULL = no judge
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS judge_correctness        DOUBLE PRECISION;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS judge_completeness       DOUBLE PRECISION;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS judge_citation_support   DOUBLE PRECISION;
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS judge_unsupported_claims INT;
CREATE INDEX IF NOT EXISTS ix_eval_run_split  ON eval.eval_run (split);
CREATE INDEX IF NOT EXISTS ix_ecr_run_intent  ON eval.eval_case_result (run_id, intent_id);

-- ======================================================================
-- Checkpointed runs (docs/specs/eval-run-checkpoint.md). Additive and idempotent.
-- Each case row is written as the case is graded, with the exact payload a resumed run
-- restores it from (src/eval/core/checkpoint/run.py checkpoint_payload). NULL for rows
-- written before this change or re-imported from a report's JSON: a resume answers
-- those cases again. Apply before the next stored run: the store writes the column.
-- ======================================================================
ALTER TABLE eval.eval_case_result ADD COLUMN IF NOT EXISTS result_payload JSONB;

-- ======================================================================
-- Precision-aware grading, grader version 4 (docs/specs/grading-precision.md).
-- IMPRECISE: stated less precisely than the key's rule, right at its own precision;
-- neither correct nor wrong. Nullable on purpose, as version 3's columns: a run graded
-- before version 4 never measured it, and a default 0 would read as "none imprecise".
-- ======================================================================
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS n_imprecise    INT;
ALTER TABLE eval.eval_run ADD COLUMN IF NOT EXISTS imprecise_rate DOUBLE PRECISION;

-- ======================================================================
-- One row per generated suite: what was measured, and against which corpus.
-- ======================================================================
CREATE TABLE IF NOT EXISTS eval.eval_suite (
    suite              TEXT NOT NULL,
    version            TEXT NOT NULL,
    generated_at       TIMESTAMPTZ,
    corpus_fingerprint TEXT,
    n_cases            INT NOT NULL DEFAULT 0,
    by_kind            JSONB,
    description        TEXT,
    build_stats        JSONB,                       -- why cases were / were not generated
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (suite, version)
);

-- ======================================================================
-- Reporting views (mirror the audit.v_* shape so metrics_exporter can pick them up)
-- ======================================================================

-- Most-recent runs, trimmed for the /tech/eval list.
CREATE OR REPLACE VIEW eval.v_recent_runs AS
SELECT
    run_id, suite, suite_version, system, tier, status,
    created_at,
    n_cases, n_skipped,
    round(accuracy::numeric, 4)            AS accuracy,
    round(citation_rate::numeric, 4)       AS citation_rate,
    round(grounding_precision::numeric, 4) AS grounding_precision,
    round(silent_wrong_rate::numeric, 4)   AS silent_wrong_rate,
    round(refusal_rate::numeric, 4)        AS refusal_rate,
    total_cost_usd,
    round(latency_p95_ms)                  AS latency_p95_ms,
    corpus_fingerprint,
    git_sha
FROM eval.eval_run
ORDER BY created_at DESC;

-- Accuracy over time per suite x system, with the previous run's delta. Powers the
-- trend sparkline and makes a regression visible without opening two runs.
CREATE OR REPLACE VIEW eval.v_run_trend AS
SELECT
    run_id, suite, system, created_at, corpus_fingerprint,
    accuracy,
    accuracy - lag(accuracy) OVER w        AS accuracy_delta,
    silent_wrong_rate,
    silent_wrong_rate - lag(silent_wrong_rate) OVER w AS silent_wrong_delta,
    lag(run_id) OVER w                     AS prev_run_id,
    -- A corpus change is not a quality change; the UI must not present it as one.
    -- Guard on the lag being present: with no previous run there is nothing to have
    -- changed, and `IS DISTINCT FROM NULL` would flag every suite's first run.
    (lag(corpus_fingerprint) OVER w IS NOT NULL
     AND corpus_fingerprint IS DISTINCT FROM lag(corpus_fingerprint) OVER w)
                                           AS corpus_changed
FROM eval.eval_run
WHERE status <> 'RUNNING'
WINDOW w AS (PARTITION BY suite, system ORDER BY created_at);

-- Per-kind accuracy, exploded from the run's by_kind JSONB for easy SQL slicing.
CREATE OR REPLACE VIEW eval.v_run_by_kind AS
SELECT
    r.run_id, r.suite, r.system, r.created_at,
    k.key                                        AS kind,
    (k.value ->> 'n')::int                       AS n,
    (k.value ->> 'n_correct')::int               AS n_correct,
    (k.value ->> 'accuracy')::double precision   AS accuracy
FROM eval.eval_run r
CROSS JOIN LATERAL jsonb_each(COALESCE(r.by_kind, '{}'::jsonb)) AS k;

-- Every failing case, newest run first -- the triage queue.
CREATE OR REPLACE VIEW eval.v_case_failures AS
SELECT
    c.run_id, r.suite, r.system, r.created_at,
    c.case_id, c.kind, c.grade, c.silent_wrong,
    c.question, c.reason, c.matched_value,
    c.expected -> 'value'                  AS expected_value,
    c.grounding_recall,
    c.ticker, c.fy, c.origin, c.query_id
FROM eval.eval_case_result c
JOIN eval.eval_run r USING (run_id)
WHERE NOT c.passed
ORDER BY r.created_at DESC, c.kind, c.case_id;

-- The release gate: confident, cited, and wrong.
CREATE OR REPLACE VIEW eval.v_silent_wrong AS
SELECT
    c.run_id, r.suite, r.system, r.created_at,
    c.case_id, c.kind, c.question, c.reason,
    c.expected -> 'value'                  AS expected_value,
    c.produced -> 'numeric_values'         AS produced_values,
    c.expected -> 'fact_ids'               AS expected_fact_ids,
    c.produced -> 'fact_ids'               AS produced_fact_ids,
    c.query_id
FROM eval.eval_case_result c
JOIN eval.eval_run r USING (run_id)
WHERE c.silent_wrong
ORDER BY r.created_at DESC;

-- Paraphrase groups whose members disagree: routing / planning non-determinism.
-- Invisible to a small hand-built set; only a paraphrased suite can surface it.
CREATE OR REPLACE VIEW eval.v_paraphrase_instability AS
SELECT
    run_id,
    paraphrase_group,
    count(*)                                    AS n_variants,
    count(*) FILTER (WHERE passed)              AS n_passed,
    array_agg(case_id ORDER BY case_id)         AS case_ids,
    min(question)                               AS example_question
FROM eval.eval_case_result
WHERE paraphrase_group IS NOT NULL
GROUP BY run_id, paraphrase_group
HAVING count(*) > 1
   AND count(*) FILTER (WHERE passed) NOT IN (0, count(*))
ORDER BY run_id, paraphrase_group;

-- Which cases have historically been flaky, across runs of the same suite/system.
CREATE OR REPLACE VIEW eval.v_case_flakiness AS
SELECT
    r.suite, r.system, c.case_id,
    count(*)                                        AS n_runs,
    count(*) FILTER (WHERE c.passed)                AS n_passed,
    round(avg(CASE WHEN c.passed THEN 1 ELSE 0 END)::numeric, 4) AS pass_rate,
    max(c.question)                                 AS question
FROM eval.eval_case_result c
JOIN eval.eval_run r USING (run_id)
GROUP BY r.suite, r.system, c.case_id
HAVING count(*) > 1
   AND count(*) FILTER (WHERE c.passed) NOT IN (0, count(*))
ORDER BY pass_rate, n_runs DESC;
