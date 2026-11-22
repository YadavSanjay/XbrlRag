-- Post-ingest acceptance check — every DB gate the API/UI depends on.
--
-- Run after a full populate, BEFORE pointing the UI at the database:
--     run python scripts/dbq.py -f /app/scripts/checks/post_ingest_acceptance.sql
--     conda run -n py312 python scripts/dbq.py -f scripts/checks/post_ingest_acceptance.sql
--
-- Each row carries its own PASS/FAIL and, on failure, the stage to re-run. The
-- point is that ingest can report success stage-by-stage while a later table is
-- still empty — and the symptom then shows up in the UI as a bad answer rather
-- than as an error, which is much harder to trace back.
--
-- Thresholds: GATE-A (anchor coverage) >= 0.95 and GATE-B (filing completeness)
-- >= 0.65 match the publish gate (guide 4 section 4.10a; DEFAULT_MIN_ANCHOR and DEFAULT_MIN_COMPLETENESS in
-- src/ingest/sec_edgar/xbrl/promotion.py).

-- ---------------------------------------------------------------------------
-- 1. Core fact store + Layer-0 registries
-- ---------------------------------------------------------------------------
SELECT
    check_name,
    n,
    CASE WHEN n >= min_expected THEN 'PASS' ELSE 'FAIL' END AS status,
    remediation
FROM (
    SELECT 'fact'                  AS check_name, (SELECT count(*) FROM fact)                  AS n, 1 AS min_expected,
           'arelle-ingest'                                                        AS remediation
    UNION ALL
    SELECT 'accession',            (SELECT count(*) FROM accession),            1,
           'arelle-ingest'
    UNION ALL
    SELECT 'filing_registry',      (SELECT count(*) FROM filing_registry),      1,
           'registries  -- UI: period/company resolution fails without this'
    UNION ALL
    SELECT 'entity_master',        (SELECT count(*) FROM entity_master),        1,
           'registries  -- UI: entity typeahead returns nothing'
    UNION ALL
    SELECT 'fiscal_calendar',      (SELECT count(*) FROM fiscal_calendar),      1,
           'registries  -- UI: "which fiscal year?" resolution fails'
    UNION ALL
    SELECT 'concept_mapping',      (SELECT count(*) FROM concept_mapping),      1,
           'concept-mapping  -- UI: "concept not found" / low confidence'
    UNION ALL
    SELECT 'concept_labels',       (SELECT count(*) FROM concept_labels),       1,
           'concept-labels  -- UI: raw QNames instead of readable labels'
    UNION ALL
    SELECT 'geography_normalization', (SELECT count(*) FROM geography_normalization), 1,
           'geography  -- UI: geographic screens return nothing'
    UNION ALL
    SELECT 'fact_dom_position',    (SELECT count(*) FROM fact_dom_position),    1,
           'dom-positions  -- UI: citations do not anchor to the source document'
    UNION ALL
    SELECT 'chunk_metadata',       (SELECT count(*) FROM chunk_metadata),       1,
           'xbrl_html_base_chunking  -- vector load discovers 0 filings without it'
) AS t
ORDER BY status DESC, check_name;

-- ---------------------------------------------------------------------------
-- 1b. concept_family coverage on mv_financial_facts.
--
-- The single most damaging gap, and the least visible. The fact store filters on
-- concept_family; if the ns_expanded concept_mapping rows were never derived
-- (Layer0Populator --sync-ns-expanded, run by layer0-populate) it is NULL
-- for every row, and EVERY numeric lookup returns NOT_FOUND for every company and
-- every year — while the ingest reports complete success end to end.
--
-- Remedy: bootstrap_db.py --only layer0-populate, then --phase mv.
-- ---------------------------------------------------------------------------
SELECT
    count(*)              AS rows_total,
    count(concept_family) AS rows_with_family,
    CASE WHEN count(*) = 0
              THEN 'FAIL (mv_financial_facts empty -- run --phase mv)'
         WHEN count(concept_family) = 0
              THEN 'FAIL (concept_family NULL everywhere -- run --only layer0-populate, then --phase mv)'
         ELSE 'PASS' END  AS status
FROM mv_financial_facts;

-- ---------------------------------------------------------------------------
-- 2. dimension_catalog member labels — populated by --dimension-members.
--    Empty member_label is not fatal, but segment/geography answers render the
--    raw member QName, which reads as a bug to anyone using the UI.
-- ---------------------------------------------------------------------------
SELECT
    count(*)                                              AS members_total,
    count(member_label)                                   AS members_labelled,
    CASE WHEN count(*) = 0 THEN 'FAIL (no dimension_catalog rows)'
         WHEN count(member_label) = 0 THEN 'FAIL (labels never populated -- run --dimension-members)'
         ELSE 'PASS' END                                  AS status
FROM dimension_catalog;

-- ---------------------------------------------------------------------------
-- 3. Materialized views — the query path reads these, not the base tables.
--    A missing MV surfaces at query time as: relation "mv_..." does not exist.
-- ---------------------------------------------------------------------------
SELECT
    matviewname,
    CASE WHEN ispopulated THEN 'PASS' ELSE 'FAIL (run: bootstrap_db.py --phase mv)' END AS status
FROM pg_matviews
WHERE schemaname = 'public'
ORDER BY matviewname;

-- ---------------------------------------------------------------------------
-- 4. The two real "loaded correctly" gates (guide 4 section 4.10a).
-- ---------------------------------------------------------------------------
SELECT accession_no,
       xmlid_coverage_pct,
       CASE WHEN xmlid_coverage_pct >= 0.95 THEN 'PASS' ELSE 'FAIL (GATE-A)' END AS status
FROM mv_anchor_coverage
ORDER BY xmlid_coverage_pct;

SELECT cik,
       fiscal_year,
       coverage_pct,
       CASE WHEN coverage_pct >= 0.65 THEN 'PASS' ELSE 'FAIL (GATE-B)' END AS status
FROM mv_filing_completeness
ORDER BY coverage_pct;

-- ---------------------------------------------------------------------------
-- 5. Audit schema — the UI's Trace Explorer reads these. Missing tables mean
--    the Traces tab errors; empty tables just mean no queries have run yet.
-- ---------------------------------------------------------------------------
SELECT 'audit.query_audit' AS relation,
       to_regclass('audit.query_audit') IS NOT NULL AS present,
       CASE WHEN to_regclass('audit.query_audit') IS NULL
            THEN 'FAIL (apply scripts/ddl/audit -- bootstrap_db.py --phase schema)'
            ELSE 'PASS' END AS status
UNION ALL
SELECT 'audit.query_event',
       to_regclass('audit.query_event') IS NOT NULL,
       CASE WHEN to_regclass('audit.query_event') IS NULL
            THEN 'FAIL (apply scripts/ddl/audit -- bootstrap_db.py --phase schema)'
            ELSE 'PASS' END
UNION ALL
SELECT 'audit.ingestion_run',
       to_regclass('audit.ingestion_run') IS NOT NULL,
       CASE WHEN to_regclass('audit.ingestion_run') IS NULL
            THEN 'FAIL (apply scripts/ddl/audit -- bootstrap_db.py --phase schema)'
            ELSE 'PASS' END;

-- ---------------------------------------------------------------------------
-- 6. pg_trgm — entity typeahead uses trigram similarity. Without the extension
--    the UI's company search silently returns nothing.
-- ---------------------------------------------------------------------------
SELECT 'pg_trgm' AS extension,
       CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')
            THEN 'PASS' ELSE 'FAIL (CREATE EXTENSION pg_trgm;)' END AS status;

-- ---------------------------------------------------------------------------
-- 7. What each ingest run actually did — read this when a gate above fails.
-- ---------------------------------------------------------------------------
SELECT run_id, pipeline, status, counts, started_at
FROM audit.ingestion_run
ORDER BY started_at DESC
LIMIT 20;
