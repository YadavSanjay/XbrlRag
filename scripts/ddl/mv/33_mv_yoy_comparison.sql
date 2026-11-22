-- mv_yoy_comparison (Layer 3) — pre-joined year-over-year deltas.
-- One basis (docs/specs/restatement-s1.md, requirement 5; src/retriever/xbrl/restatement.py
-- yoy_pairs states the same pairing in Python):
--   * the current side is each canonical filing's own column: rows ending exactly on its
--     DEI DocumentPeriodEndDate (stored next-midnight, so document_period_end + 1);
--   * the prior side is the SAME filing's comparative column (the filer's own comparison,
--     on the current basis after a split or a recast): same measure, a period ending 350 to
--     380 days earlier and, for a duration, a length within 14 days; the latest such end,
--     then the greatest fact_id. Pairing with the prior filing's current column mixed
--     bases (NVDA FY2025 EPS 2.94 vs the pre-split 11.93 = -75.4%; its own 1.19 = +147.1%);
--   * only when the current filing has no prior column is the prior-labelled filing's own
--     column used, and `same_filing` says which basis a row is on;
--   * the fallback matches fiscal_period AND duration_kind, so Q2-vs-Q2 (quarter) and
--     FY-vs-FY (annual) compare like-for-like.
--
-- WS-1 / A2 dimensional-member matching (namespace-insensitive + alias-aware):
--   * concept, segment_axis and segment_member are matched on LOCAL NAME (the part after
--     the last ':'): SEC filers re-declare the us-gaap namespace and their extension
--     members under a new date-stamped namespace every year
--     (broadcom.com/20221030:X -> broadcom.com/20231029:X), which is the SAME member.
--   * in the prior-filing fallback, a genuine reclassification (the member's LOCAL name
--     changes) is bridged via member_aliases (populated by member_alias_detector.py). This
--     MV must be rebuilt after that table is populated for a new rename to take effect;
--     member_aliases starts empty, so the EXISTS clause is inert until then.
--   * same unit only. A 20-F tags its reporting currency AND a USD convenience
--     translation; without this, TSMC FY2024 revenue paired TWD 2,894B with USD 70.6B as
--     "+3,999%".
--
-- Year lengths (docs/specs/period-disclosure.md R3, R7.4): each side's stored period and
-- its length in days (`period_end - period_start`, stored ends being next-midnight), so an
-- answer comparing a 53-week year with a 52-week one can say so
-- (src/retriever/xbrl/period_disclosure.py year_length_disclosure). NULL for instants.
DROP MATERIALIZED VIEW IF EXISTS mv_yoy_comparison CASCADE;

CREATE MATERIALIZED VIEW mv_yoy_comparison AS
SELECT
    curr.cik, curr.ticker, curr.concept, curr.concept_family,
    curr.fiscal_period, curr.duration_kind,
    curr.segment_axis, curr.segment_member, curr.segment_label, curr.section_path,
    curr.fiscal_year  AS fy_current,
    curr.value        AS value_current,
    curr.unit,
    curr.fact_id      AS fact_id_current,
    curr.accession_no AS accession_current,
    -- The prior period's year: one before the current label on either basis.
    CASE WHEN same.fact_id IS NOT NULL THEN curr.fiscal_year - 1
         ELSE fb.fiscal_year END                         AS fy_prior,
    COALESCE(same.value, fb.value)                       AS value_prior,
    COALESCE(same.fact_id, fb.fact_id)                   AS fact_id_prior,
    COALESCE(same.accession_no, fb.accession_no)         AS accession_prior,
    (same.fact_id IS NOT NULL)                           AS same_filing,
    curr.period_start                                    AS period_start_current,
    curr.period_end                                      AS period_end_current,
    (curr.period_end - curr.period_start)                AS days_current,
    COALESCE(same.period_start, fb.period_start)         AS period_start_prior,
    COALESCE(same.period_end, fb.period_end)             AS period_end_prior,
    (COALESCE(same.period_end, fb.period_end)
     - COALESCE(same.period_start, fb.period_start))     AS days_prior,
    (curr.value - COALESCE(same.value, fb.value))        AS yoy_change,
    CASE WHEN COALESCE(same.value, fb.value) != 0
         THEN (curr.value - COALESCE(same.value, fb.value))
              / ABS(COALESCE(same.value, fb.value))
         ELSE NULL END                                   AS yoy_pct_change
FROM mv_financial_facts curr
-- The filer's own comparison: the current filing's comparative column.
LEFT JOIN LATERAL (
    SELECT prev.value, prev.fact_id, prev.accession_no, prev.period_start, prev.period_end
      FROM mv_financial_facts prev
     WHERE prev.accession_no = curr.accession_no
       AND prev.cik          = curr.cik
       AND SUBSTRING(prev.concept FROM '[^:]+$') = SUBSTRING(curr.concept FROM '[^:]+$')
       AND COALESCE(SUBSTRING(prev.segment_axis FROM '[^:]+$'), '___')
         = COALESCE(SUBSTRING(curr.segment_axis FROM '[^:]+$'), '___')
       AND COALESCE(SUBSTRING(prev.segment_member FROM '[^:]+$'), '___')
         = COALESCE(SUBSTRING(curr.segment_member FROM '[^:]+$'), '___')
       AND prev.unit IS NOT DISTINCT FROM curr.unit
       AND prev.value IS NOT NULL
       AND (curr.period_end - prev.period_end) BETWEEN 350 AND 380
       AND (
             (curr.period_start IS NULL AND prev.period_start IS NULL)
          OR (curr.period_start IS NOT NULL AND prev.period_start IS NOT NULL
              AND ABS((prev.period_end - prev.period_start)
                      - (curr.period_end - curr.period_start)) <= 14)
           )
     ORDER BY prev.period_end DESC, prev.fact_id DESC
     LIMIT 1
) same ON TRUE
-- Fallback: the prior-labelled filing's own column.
LEFT JOIN LATERAL (
    SELECT prior.fiscal_year, prior.value, prior.fact_id, prior.accession_no,
           prior.period_start, prior.period_end
      FROM mv_financial_facts prior
     WHERE same.fact_id IS NULL
       AND prior.cik          = curr.cik
       AND SUBSTRING(prior.concept FROM '[^:]+$') = SUBSTRING(curr.concept FROM '[^:]+$')
       AND prior.fiscal_year  = curr.fiscal_year - 1
       AND COALESCE(prior.fiscal_period, 'FY') = COALESCE(curr.fiscal_period, 'FY')
       AND COALESCE(prior.duration_kind, '_')  = COALESCE(curr.duration_kind, '_')
       AND COALESCE(SUBSTRING(prior.segment_axis FROM '[^:]+$'), '___')
         = COALESCE(SUBSTRING(curr.segment_axis FROM '[^:]+$'), '___')
       AND (
                  COALESCE(SUBSTRING(prior.segment_member FROM '[^:]+$'), '___')
                = COALESCE(SUBSTRING(curr.segment_member FROM '[^:]+$'), '___')
              -- A2: bridge a genuine reclassification (differing local names).
              OR EXISTS (
                     SELECT 1 FROM member_aliases ma
                      WHERE ma.cik = curr.cik
                        AND SUBSTRING(ma.axis_qname       FROM '[^:]+$') = SUBSTRING(curr.segment_axis   FROM '[^:]+$')
                        AND SUBSTRING(ma.new_member_qname FROM '[^:]+$') = SUBSTRING(curr.segment_member FROM '[^:]+$')
                        AND SUBSTRING(ma.old_member_qname FROM '[^:]+$') = SUBSTRING(prior.segment_member FROM '[^:]+$')
                 )
           )
       AND prior.period_type = curr.period_type
       AND prior.unit IS NOT DISTINCT FROM curr.unit
       AND prior.value IS NOT NULL
       AND prior.is_canonical_filing = TRUE
       AND prior.period_end = prior.document_period_end + 1
     ORDER BY prior.period_end DESC, prior.fact_id DESC
     LIMIT 1
) fb ON TRUE
WHERE curr.is_canonical_filing = TRUE
  AND curr.period_end = curr.document_period_end + 1
  AND curr.value IS NOT NULL
  AND (same.fact_id IS NOT NULL OR fb.fact_id IS NOT NULL)
;

CREATE INDEX ix_myoy_lookup ON mv_yoy_comparison (cik, fy_current, fiscal_period, concept);
CREATE INDEX ix_myoy_family ON mv_yoy_comparison (cik, fy_current, fiscal_period, concept_family);
CREATE INDEX ix_myoy_dim    ON mv_yoy_comparison (cik, fy_current, fiscal_period, concept_family, segment_member);
