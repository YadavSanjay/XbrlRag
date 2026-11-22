-- ============================================================================
-- mv_filing_completeness — expected concept coverage per filing (Layer 4)
-- ============================================================================
-- Post-ingest quality gate. Checks what fraction of the ~50 expected concept
-- families (concept_mapping where preference_rank = 1) are actually present for
-- each (cik, fiscal_year).
--
-- GATE-B (semantic, ADVISORY): the ingest publish gate records coverage_pct for
-- observability and query-time confidence but only ENFORCES hold-back on
-- coverage_pct < 0.80 when explicitly configured (GATE-A / mv_anchor_coverage is
-- the hard gate; see src/ingest/sec_edgar/xbrl/promotion.py). At query time the
-- resolver demotes resolution confidence proportionally for sparse filings,
-- preventing overconfident answers from an incomplete ingest.
--
-- ACTUAL span (WS-1 correction): `actual` counts an expected family as present
-- when the filing reports it in ANY undimensioned canonical fact, instant OR
-- duration. The design's `period_type='duration'` filter structurally excluded
-- every balance-sheet (instant) family — half of the 50 expected — so even a
-- complete filing capped near 0.5 coverage. Note that phantom (cik, fiscal_year)
-- rows still appear here (a filing's DEI focus-year vs comparative artifacts);
-- the publish gate avoids them by evaluating per real accession via
-- filing_registry, not by scanning this view.
--
-- Depends on mv_financial_facts (10_) + concept_mapping + entity_master; prefix
-- 41_ orders it after the base MV.
--
-- Design: docs/design/XbrlRag_Materialized_Views.md §4 and §6 (publish gate).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_filing_completeness CASCADE;

CREATE MATERIALIZED VIEW mv_filing_completeness AS
WITH expected AS (
    SELECT concept_family, qname
      FROM concept_mapping
     WHERE preference_rank = 1
       AND cik_override    IS NULL
       AND is_deprecated   = FALSE
),
actual AS (
    SELECT DISTINCT cik, fiscal_year, concept_family
      FROM mv_financial_facts
     WHERE is_canonical_filing = TRUE
       AND segment_axis IS NULL
       AND concept_family IS NOT NULL
)
SELECT
    a_all.cik,
    em.trading_symbol                               AS ticker,
    a_all.fiscal_year,
    COUNT(DISTINCT ex.concept_family)               AS expected_concept_count,
    COUNT(DISTINCT ac.concept_family)               AS found_concept_count,
    COUNT(DISTINCT ex.concept_family)
      - COUNT(DISTINCT ac.concept_family)           AS missing_count,
    ARRAY_AGG(DISTINCT ex.concept_family)
        FILTER (WHERE ac.cik IS NULL)               AS missing_concept_families,
    COUNT(DISTINCT ac.concept_family)::FLOAT
      / NULLIF(COUNT(DISTINCT ex.concept_family), 0) AS coverage_pct
FROM (SELECT DISTINCT cik, fiscal_year FROM mv_financial_facts
       WHERE is_canonical_filing = TRUE) a_all
LEFT JOIN entity_master em ON em.cik = a_all.cik
CROSS JOIN expected ex
LEFT JOIN actual ac
    ON  ac.cik            = a_all.cik
   AND  ac.fiscal_year    = a_all.fiscal_year
   AND  ac.concept_family = ex.concept_family
GROUP BY a_all.cik, em.trading_symbol, a_all.fiscal_year;

CREATE UNIQUE INDEX ix_mfc_pk       ON mv_filing_completeness (cik, fiscal_year);
CREATE INDEX        ix_mfc_coverage ON mv_filing_completeness (coverage_pct);
CREATE INDEX        ix_mfc_gaps     ON mv_filing_completeness (cik, fiscal_year)
    WHERE coverage_pct < 0.80;
