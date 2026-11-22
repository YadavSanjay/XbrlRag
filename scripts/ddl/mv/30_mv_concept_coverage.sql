-- mv_concept_coverage (Layer 2) — which QName a filer actually uses, per period.
-- Period-aware: adds fiscal_period so 10-Q coverage is tracked separately from FY.
DROP MATERIALIZED VIEW IF EXISTS mv_concept_coverage CASCADE;

CREATE MATERIALIZED VIEW mv_concept_coverage AS
SELECT f.cik, f.ticker, f.fiscal_year, f.fiscal_period, f.concept,
       cm.concept_family, cm.preference_rank, COUNT(*) AS fact_count,
       MIN(f.period_end) AS earliest_period, MAX(f.period_end) AS latest_period,
       (cm.preference_rank = MIN(cm.preference_rank) OVER
           (PARTITION BY f.cik, f.fiscal_year, f.fiscal_period, cm.concept_family)) AS is_preferred_for_filer
FROM mv_financial_facts f
LEFT JOIN concept_mapping cm
       ON cm.qname = f.concept
      AND (cm.cik_override IS NULL OR cm.cik_override = f.cik)
WHERE f.is_canonical_filing = TRUE
GROUP BY f.cik, f.ticker, f.fiscal_year, f.fiscal_period, f.concept, cm.concept_family, cm.preference_rank;

CREATE INDEX ix_mcc_family ON mv_concept_coverage (cik, concept_family, fiscal_year, fiscal_period);
CREATE INDEX ix_mcc_qname  ON mv_concept_coverage (cik, concept);
