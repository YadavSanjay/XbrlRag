-- mv_segment_matrix (Layer 3) — per-segment values with share of total + rank.
-- Period-aware + fan-out-fixed: filters is_current_period (so a filing's prior
-- comparative columns don't pollute the RANK) and partitions the rank by
-- fiscal_period + duration_kind so quarterly and annual (and YTD) breakdowns are
-- ranked within their own period.
--
-- pct_of_axis_total (name kept) is the fact's share of the CONSOLIDATED total, not of
-- the sum over the axis (docs/specs/segment-geo-views.md, J2): value / the
-- segment_axis IS NULL fact of the same accession, concept, unit, period_start and
-- period_end. Among non-nil totals the most precise (decimals NULL = INF) decide;
-- more than one value there means no total. NULL when there is no total or it is
-- zero. Overlapping members (US inside US & Canada) and multi-dimension cells each
-- keep their own share. Python reference:
-- src/ingest/sec_edgar/xbrl/segment_geo.py::shares_of_total, checked against this
-- view by test/integration/test_segment_geo_views_live.py.
DROP MATERIALIZED VIEW IF EXISTS mv_segment_matrix CASCADE;

CREATE MATERIALIZED VIEW mv_segment_matrix AS
WITH consolidated_total AS (
    SELECT accession_no, concept, unit, period_start, period_end,
           CASE WHEN COUNT(DISTINCT value) = 1 THEN MIN(value) END AS total
      FROM (
        SELECT t.accession_no, t.concept, t.unit, t.period_start, t.period_end, t.value,
               RANK() OVER (PARTITION BY t.accession_no, t.concept, t.unit,
                                         t.period_start, t.period_end
                            ORDER BY t.decimals_value DESC NULLS FIRST) AS precision_rank
          FROM mv_financial_facts t
         WHERE t.segment_axis IS NULL
           AND t.value IS NOT NULL
      ) ranked
     WHERE precision_rank = 1
     GROUP BY accession_no, concept, unit, period_start, period_end
)
SELECT f.cik, f.ticker, f.fiscal_year, f.fiscal_period, f.duration_kind,
       f.concept, f.concept_family,
       f.segment_axis, f.segment_member, f.segment_label,
       f.value, f.unit, f.section_path, f.fact_id,
       f.value / NULLIF(tot.total, 0) AS pct_of_axis_total,
       RANK() OVER (
           PARTITION BY f.cik, f.fiscal_year, f.fiscal_period, f.duration_kind,
                        f.concept, f.segment_axis
           ORDER BY f.value DESC NULLS LAST  -- a filed nil (value NULL) is never rank 1
       ) AS segment_rank
FROM mv_financial_facts f
LEFT JOIN consolidated_total tot
       ON tot.accession_no = f.accession_no
      AND tot.concept      = f.concept
      AND tot.unit         = f.unit
      AND tot.period_start IS NOT DISTINCT FROM f.period_start
      AND tot.period_end   IS NOT DISTINCT FROM f.period_end
WHERE f.segment_axis IS NOT NULL
  AND f.is_canonical_filing = TRUE
  AND f.is_current_period   = TRUE
  AND f.period_type = 'duration'
;

CREATE INDEX ix_msm_lookup ON mv_segment_matrix (cik, fiscal_year, fiscal_period, concept_family, segment_axis);
CREATE INDEX ix_msm_member ON mv_segment_matrix (cik, fiscal_year, fiscal_period, segment_axis, segment_member);
CREATE INDEX ix_msm_rank   ON mv_segment_matrix (cik, fiscal_year, fiscal_period, concept_family, segment_rank);
