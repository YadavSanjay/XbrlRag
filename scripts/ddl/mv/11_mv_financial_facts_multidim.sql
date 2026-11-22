-- ============================================================================
-- mv_financial_facts_multidim — multi-axis facts as JSONB (Layer 1b)
-- ============================================================================
-- Versioned build script (see 10_mv_financial_facts.sql header). Facts that
-- carry 2+ simultaneous dimensions, which the flat mv_financial_facts cannot
-- represent. Independent of mv_financial_facts (built straight from raw xbrlDB
-- tables), so it is dropped/recreated standalone.
--
-- CHANGES vs. the original design-doc definition:
--   1. 10-Q filings are now included (form filter below).
--   2. Adds period_start / period_days / duration_kind so a multidim quarterly
--      lookup can distinguish a discrete quarter from a YTD cumulative figure,
--      matching mv_financial_facts (the same inclusive bands and 'other';
--      docs/specs/period-disclosure.md R1, R7.1).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_financial_facts_multidim CASCADE;

CREATE MATERIALIZED VIEW mv_financial_facts_multidim AS
SELECT
    f.fact_id,
    LPAD(ctx.entity_identifier, 10, '0')                        AS cik,
    a.filing_accession_number                                   AS accession_no,
    -- The filing's label; see 10_mv_financial_facts.sql.
    fr.fiscal_year                                              AS fiscal_year,
    -- The registry's quarter, as in 10_mv_financial_facts.sql (docs/specs/tenq-fiscal-labels.md).
    fr.fiscal_period                                            AS fiscal_period,
    q.namespace || ':' || q.local_name                          AS concept,
    f.effective_value                                           AS value,
    u.uom_str                                                   AS unit,
    CASE WHEN ctx.period_instant IS NOT NULL
         THEN 'instant' ELSE 'duration' END                     AS period_type,
    ctx.period_start,
    COALESCE(ctx.period_end, ctx.period_instant)                AS period_end,
    CASE
        WHEN ctx.period_instant IS NOT NULL          THEN NULL
        WHEN ctx.period_start IS NULL                THEN NULL
        ELSE (ctx.period_end - ctx.period_start)
    END                                                         AS period_days,
    CASE
        WHEN ctx.period_instant IS NOT NULL          THEN 'instant'
        WHEN ctx.period_start IS NULL                THEN NULL
        WHEN (ctx.period_end - ctx.period_start) BETWEEN  84 AND 119 THEN 'quarter'     -- 12-17 weeks
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 168 AND 189 THEN 'half'        -- 24-27 weeks (YTD @ Q2)
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 252 AND 280 THEN 'nine_month'  -- 36-40 weeks (YTD @ Q3)
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 350 AND 380 THEN 'annual'      -- 52 or 53 weeks
        ELSE                                                              'other'       -- stubs, monthly, multi-year
    END                                                         AS duration_kind,
    -- See 10_mv_financial_facts.sql for the rationale (closest distinct
    -- period_end to DocumentPeriodEndDate within the filing).
    (
        ABS(COALESCE(ctx.period_end, ctx.period_instant) - dei.document_period_end)
        = MIN(ABS(COALESCE(ctx.period_end, ctx.period_instant) - dei.document_period_end))
              OVER (PARTITION BY a.filing_accession_number)
    )                                                           AS is_current_period,
    pres_sec.section_path,
    fdp.xml_id,
    fdp.html_anchor,
    fdp.page_num,

    JSONB_OBJECT_AGG(
        aq.namespace || ':' || aq.local_name,
        mq.namespace || ':' || mq.local_name
    )                                                           AS dimensions,
    JSONB_OBJECT_AGG(
        aq.namespace || ':' || aq.local_name,
        COALESCE(dc.member_label, mq.local_name)
    )                                                           AS dimension_labels,
    COUNT(cd.context_dimension_id)                              AS dimension_count

FROM fact f
JOIN accession  a    ON a.accession_id   = f.accession_id
JOIN element    el   ON el.element_id    = f.element_id
JOIN qname      q    ON q.qname_id       = el.qname_id
JOIN context    ctx  ON ctx.context_id   = f.context_id
JOIN mv_dei_metadata dei ON dei.accession_id = a.accession_id
JOIN filing_registry fr
       ON fr.accession_no = a.filing_accession_number
      AND fr.cik          = LPAD(ctx.entity_identifier, 10, '0')
JOIN context_dimension cd ON cd.context_id  = ctx.context_id
                          AND cd.is_default  = FALSE
JOIN qname aq  ON aq.qname_id = cd.dimension_qname_id
JOIN qname mq  ON mq.qname_id = cd.member_qname_id
LEFT JOIN dimension_catalog dc
    ON  dc.cik          = LPAD(ctx.entity_identifier, 10, '0')
   AND  dc.axis_qname   = aq.namespace || ':' || aq.local_name
   AND  dc.member_qname = mq.namespace || ':' || mq.local_name
LEFT JOIN LATERAL (
    SELECT COALESCE(STRING_AGG(qm.local_name, '*' ORDER BY qm.local_name)
                      FILTER (WHERE um.location_id IN (1,2)), 'pure')
        || COALESCE('/' || STRING_AGG(qm.local_name, '*' ORDER BY qm.local_name)
                             FILTER (WHERE um.location_id = 3), '')             AS uom_str
      FROM unit_measure um
      JOIN qname qm ON qm.qname_id = um.qname_id
     WHERE um.unit_id = f.unit_id
) u ON TRUE
LEFT JOIN mv_fact_section_assignment pres_sec ON pres_sec.fact_id = f.fact_id
LEFT JOIN fact_dom_position fdp ON fdp.fact_id = f.fact_id

WHERE (fr.form_type LIKE '10-K%'
       OR fr.form_type LIKE '20-F%'
       OR fr.form_type LIKE '10-Q%')
  AND ctx.specifies_dimensions = TRUE
  AND el.is_numeric            = TRUE
  AND ctx.entity_scheme        = 'http://www.sec.gov/CIK'

GROUP BY f.fact_id, ctx.entity_identifier, a.filing_accession_number,
         fr.fiscal_year, fr.fiscal_period, dei.document_period_end,
         q.namespace, q.local_name, f.effective_value, u.uom_str,
         ctx.period_instant, ctx.period_start, ctx.period_end, pres_sec.section_path,
         fdp.xml_id, fdp.html_anchor, fdp.page_num

HAVING COUNT(cd.context_dimension_id) >= 2;

CREATE UNIQUE INDEX ix_mffm_pk ON mv_financial_facts_multidim (fact_id);
CREATE INDEX ix_mffm_lookup    ON mv_financial_facts_multidim (cik, fiscal_year, fiscal_period, concept);
CREATE INDEX ix_mffm_dims      ON mv_financial_facts_multidim USING GIN (dimensions);
