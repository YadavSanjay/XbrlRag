-- ============================================================================
-- mv_financial_facts — primary denormalized retrieval surface (Layer 1b)
-- ============================================================================
-- Versioned build script, committed so form-type / period changes are
-- reviewable and repeatable (purpose: docs/design/XbrlRag_Materialized_Views.md §4.1).
-- Run via scripts/rebuild_mvs.py, which DROP ... CASCADEs and rebuilds this MV and
-- everything that depends on it.
--
-- CHANGES vs. the original design-doc definition:
--   1. 10-Q filings are now included (form filter below). A 10-K/20-F row is
--      fiscal_period='FY'; a 10-Q row is fiscal_period in {Q1,Q2,Q3}.
--   2. New `duration_kind` column classifies a duration fact by its length so
--      the retrieval layer can distinguish a discrete quarter (~3 months) from
--      a year-to-date cumulative figure (~6/9 months) that a 10-Q reports at
--      the SAME period_end. Balance-sheet (instant) facts get 'instant';
--      a 52- or 53-week duration gets 'annual'; any length outside the four bands
--      gets 'other' (docs/specs/period-disclosure.md R1, R7.1).
--   3. docs/specs/concept-family-coverage.md: the family join below ranks by role
--      first (primary, substitute, component), then rank; a filer's own row wins
--      only within one family and on a tie, so a cik-scoped row of one family never
--      takes a fact that is a better-ranked member of another (Oracle's
--      ServiceRevenue ranking lists the total-revenue element at 2, Revenue at 1).
--      New columns: concept_role, substitution_code, substitution_note (from
--      concept_mapping) and `presented` (a line item of a primary statement).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_financial_facts CASCADE;

CREATE MATERIALIZED VIEW mv_financial_facts AS
SELECT
    -- Identity
    f.fact_id,
    LPAD(ctx.entity_identifier, 10, '0')                        AS cik,
    em.trading_symbol                                           AS ticker,
    a.filing_accession_number                                   AS accession_no,
    -- The filing's label, shared with filing_registry, not bare DEI: DEI can repeat
    -- across two years (docs/specs/fiscal-label-alignment.md). A per-filing value, so
    -- comparative columns stay filed under their filing's label.
    fr.fiscal_year                                              AS fiscal_year,
    -- The registry's quarter too ('FY' | 'Q1'..'Q3'), placed on the filer's annual
    -- calendar, not DEI DocumentFiscalPeriodFocus: one label on the MV, filing_registry,
    -- fiscal_calendar and publish_manifest (docs/specs/tenq-fiscal-labels.md).
    fr.fiscal_period                                            AS fiscal_period,
    dei.document_type,
    -- The filing's DEI DocumentPeriodEndDate (an inclusive SEC date). Its own column ends
    -- exactly here: period_end = document_period_end + 1 (period_end is next-midnight),
    -- never the nearest end (docs/specs/restatement-s1.md, requirement 1).
    dei.document_period_end,

    -- Concept QName
    q.namespace || ':' || q.local_name                          AS concept,
    cm.concept_family,
    cm.preference_rank                                          AS concept_pref_rank,
    cm.concept_role,
    cm.substitution_code,
    cm.substitution_note,
    -- A line item of one of the filing's primary statements: a child in its
    -- presentation linkbase under a role whose definition reads
    -- '<sort> - Statement - <title>' (EDGAR Filer Manual), not a parenthetical (any case:
    -- Coca-Cola writes PARENTHETICAL). The
    -- cover page ('Document' / 'Cover' roles) is never presented.
    EXISTS (
        SELECT 1
          FROM mv_presentation_network pn
         WHERE pn.accession_no     = a.filing_accession_number
           AND pn.child_element_id = f.element_id
           AND pn.role_definition LIKE '% - Statement - %'
           AND pn.role_definition NOT ILIKE '%parenthetical%'
    )                                                           AS presented,
    el.is_numeric,
    el.is_monetary,
    CASE
        WHEN q.namespace LIKE 'http://fasb.org/us-gaap/%'        THEN FALSE
        WHEN q.namespace LIKE 'http://fasb.org/srt/%'            THEN FALSE
        WHEN q.namespace LIKE 'http://xbrl.sec.gov/%'            THEN FALSE
        WHEN q.namespace LIKE 'http://www.xbrl.org/%'            THEN FALSE
        WHEN q.namespace LIKE 'http://xbrl.ifrs.org/taxonomy/%'  THEN FALSE
        WHEN q.namespace LIKE 'https://xbrl.ifrs.org/taxonomy/%' THEN FALSE
        ELSE TRUE
    END                                                         AS is_extended,

    -- Value
    f.effective_value                                           AS value,
    u.uom_str                                                   AS unit,
    f.decimals_value,
    f.fact_value                                                AS raw_value,

    -- Period type (instant | duration | forever)
    CASE
        WHEN ctx.period_instant IS NOT NULL                                THEN 'instant'
        WHEN ctx.period_start IS NULL AND ctx.period_end IS NULL
             AND ctx.period_instant IS NULL                                THEN 'forever'
        ELSE                                                                    'duration'
    END                                                         AS period_type,
    ctx.period_start,
    COALESCE(ctx.period_end, ctx.period_instant)                AS period_end,

    -- Duration length in days (NULL for instant / forever facts)
    CASE
        WHEN ctx.period_instant IS NOT NULL          THEN NULL
        WHEN ctx.period_start IS NULL                THEN NULL
        ELSE (ctx.period_end - ctx.period_start)
    END                                                         AS period_days,

    -- Duration classification. A 10-Q reports both a discrete-quarter (~3mo)
    -- and a year-to-date cumulative (~6/9mo) figure at the same period_end;
    -- this lets the resolver pick the one the query means. The bands are inclusive
    -- and mirror src/retriever/xbrl/period_disclosure.py duration_class
    -- (docs/specs/period-disclosure.md R1, R7.1): inside them the class equals FSDS
    -- `qtrs`; every other length (CEG's 334-day stub, MCD's program-to-date totals,
    -- 1- to 81-day facts) is 'other' and never answers a quarter or a year.
    CASE
        WHEN ctx.period_instant IS NOT NULL          THEN 'instant'
        WHEN ctx.period_start IS NULL                THEN NULL
        WHEN (ctx.period_end - ctx.period_start) BETWEEN  84 AND 119 THEN 'quarter'     -- 12-17 weeks
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 168 AND 189 THEN 'half'        -- 24-27 weeks (YTD @ Q2)
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 252 AND 280 THEN 'nine_month'  -- 36-40 weeks (YTD @ Q3)
        WHEN (ctx.period_end - ctx.period_start) BETWEEN 350 AND 380 THEN 'annual'      -- 52 or 53 weeks
        ELSE                                                              'other'       -- stubs, monthly, multi-year
    END                                                         AS duration_kind,

    -- TRUE when this fact belongs to the filing's CURRENT-period column, FALSE
    -- for a prior-year comparative column reported in the same filing.
    -- Analytical views filter on this to collapse a filing's 3-5 comparative
    -- columns (which all share fiscal_year = the filing's label) down to one
    -- value, preventing the self-join fan-out that would otherwise mix e.g.
    -- FY2026 revenue with FY2022 COGS. The retrieval lookup path does not need
    -- it (it already picks the latest period_end).
    --
    -- Defined as "closest distinct period_end to the DEI DocumentPeriodEndDate
    -- within this filing" rather than exact equality: filers on a 52/53-week
    -- calendar report a statement period_end that drifts ~1 day from the tagged
    -- DocumentPeriodEndDate, so exact match yields ZERO current facts. The
    -- per-filing minimum gap robustly selects the statement period (gap ~1 day)
    -- over comparatives (gap ~365 days) and cover-page "as of" dates (gap ~20
    -- days) without a magic tolerance.
    (
        ABS(COALESCE(ctx.period_end, ctx.period_instant) - dei.document_period_end)
        = MIN(ABS(COALESCE(ctx.period_end, ctx.period_instant) - dei.document_period_end))
              OVER (PARTITION BY a.accession_id)
    )                                                           AS is_current_period,

    -- Single primary dimension (NULL when context.specifies_dimensions = FALSE)
    CASE WHEN ctx.specifies_dimensions THEN
        CASE
            WHEN aq.namespace LIKE 'http://fasb.org/us-gaap/%'         THEN 'us-gaap'
            WHEN aq.namespace LIKE 'http://fasb.org/srt/%'             THEN 'srt'
            WHEN aq.namespace LIKE 'http://xbrl.sec.gov/dei/%'         THEN 'dei'
            WHEN aq.namespace LIKE 'http://xbrl.sec.gov/ecd/%'         THEN 'ecd'
            WHEN aq.namespace LIKE 'http://xbrl.sec.gov/country/%'     THEN 'country'
            WHEN aq.namespace LIKE 'http://xbrl.ifrs.org/taxonomy/%'   THEN 'ifrs-full'
            WHEN aq.namespace LIKE 'https://xbrl.ifrs.org/taxonomy/%'  THEN 'ifrs-full'
            ELSE aq.namespace
        END || ':' || aq.local_name
    ELSE NULL END                                               AS segment_axis,
    CASE WHEN ctx.specifies_dimensions THEN
        CASE
            WHEN mq.namespace LIKE 'http://fasb.org/us-gaap/%'         THEN 'us-gaap'
            WHEN mq.namespace LIKE 'http://fasb.org/srt/%'             THEN 'srt'
            WHEN mq.namespace LIKE 'http://xbrl.sec.gov/dei/%'         THEN 'dei'
            WHEN mq.namespace LIKE 'http://xbrl.sec.gov/ecd/%'         THEN 'ecd'
            WHEN mq.namespace LIKE 'http://xbrl.sec.gov/country/%'     THEN 'country'
            WHEN mq.namespace LIKE 'http://xbrl.ifrs.org/taxonomy/%'   THEN 'ifrs-full'
            WHEN mq.namespace LIKE 'https://xbrl.ifrs.org/taxonomy/%'  THEN 'ifrs-full'
            ELSE mq.namespace
        END || ':' || mq.local_name
    ELSE NULL END                                               AS segment_member,
    dc.member_label                                             AS segment_label,

    (cd_count.dim_count > 1)                                    AS has_multiple_dimensions,

    COALESCE(pres_sec.section_path, sa.section_path)            AS section_path,
    pres_sec.role_uri                                           AS presentation_role_uri,

    fdp.xml_id,
    fdp.html_anchor,
    fdp.page_num,

    lbl.label                                                   AS concept_label,

    ARRAY(
        SELECT chunk_id FROM chunk_cross_links WHERE fact_id = f.fact_id
    )                                                           AS linked_chunk_ids,

    -- Point-in-time support: canonical-version flag + filing provenance
    (fr.superseded_by IS NULL)                                  AS is_canonical_filing,
    fr.filed_date,
    fr.is_amendment

FROM fact f
JOIN accession  a    ON a.accession_id   = f.accession_id
JOIN element    el   ON el.element_id    = f.element_id
JOIN qname      q    ON q.qname_id       = el.qname_id
JOIN context    ctx  ON ctx.context_id   = f.context_id
JOIN mv_dei_metadata dei ON dei.accession_id = a.accession_id
LEFT JOIN filing_registry fr
       ON fr.accession_no = a.filing_accession_number
      AND fr.cik          = LPAD(ctx.entity_identifier, 10, '0')
LEFT JOIN entity_master em ON em.cik = LPAD(ctx.entity_identifier, 10, '0')
LEFT JOIN LATERAL (
    -- One family per fact. Within a family the filer's own row replaces the generic
    -- one; across families the best role, then the lowest rank wins, the filer's row
    -- breaking a tie (src/ingest/util/generate_concept_mapping.py::fact_family states
    -- the same rule).
    SELECT per_family.concept_family, per_family.preference_rank,
           per_family.concept_role, per_family.substitution_code,
           per_family.substitution_note
      FROM (
        SELECT DISTINCT ON (m.concept_family)
               m.concept_family,
               m.preference_rank,
               COALESCE(m.concept_role,
                        CASE WHEN m.preference_rank BETWEEN 50 AND 99
                             THEN 'component' ELSE 'primary' END) AS concept_role,
               m.substitution_code,
               m.substitution_note,
               (m.cik_override IS NOT NULL)                        AS is_filer_row
          FROM concept_mapping m
         WHERE m.qname = q.namespace || ':' || q.local_name
           AND (m.cik_override IS NULL
                OR m.cik_override = LPAD(ctx.entity_identifier, 10, '0'))
         ORDER BY m.concept_family, (m.cik_override IS NOT NULL) DESC
      ) per_family
     ORDER BY CASE per_family.concept_role
                  WHEN 'primary'    THEN 0
                  WHEN 'substitute' THEN 1
                  ELSE 2
              END,
              per_family.preference_rank ASC,
              per_family.is_filer_row DESC,
              per_family.concept_family ASC
     LIMIT 1
) cm ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COALESCE(
            STRING_AGG(qm.local_name, '*' ORDER BY qm.local_name)
              FILTER (WHERE um.location_id IN (1,2)),
            'pure'
        )
        ||
        COALESCE(
            '/' || STRING_AGG(qm.local_name, '*' ORDER BY qm.local_name)
                     FILTER (WHERE um.location_id = 3),
            ''
        )                                                       AS uom_str
      FROM unit_measure um
      JOIN qname qm ON qm.qname_id = um.qname_id
     WHERE um.unit_id = f.unit_id
) u ON TRUE
LEFT JOIN LATERAL (
    SELECT cd.dimension_qname_id, cd.member_qname_id
      FROM context_dimension cd
     WHERE cd.context_id = ctx.context_id
       AND cd.is_default = FALSE
     ORDER BY cd.context_dimension_id ASC
     LIMIT 1
) cd1 ON ctx.specifies_dimensions = TRUE
LEFT JOIN qname aq ON aq.qname_id = cd1.dimension_qname_id
LEFT JOIN qname mq ON mq.qname_id = cd1.member_qname_id
LEFT JOIN dimension_catalog dc
    ON  dc.cik          = LPAD(ctx.entity_identifier, 10, '0')
   AND  dc.axis_qname   = aq.namespace || ':' || aq.local_name
   AND  dc.member_qname = mq.namespace || ':' || mq.local_name
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS dim_count
      FROM context_dimension cd2
     WHERE cd2.context_id = ctx.context_id
       AND cd2.is_default = FALSE
) cd_count ON TRUE
LEFT JOIN mv_fact_section_assignment pres_sec ON pres_sec.fact_id = f.fact_id
LEFT JOIN fact_dom_position fdp ON fdp.fact_id = f.fact_id
LEFT JOIN section_assignment sa ON sa.fact_id = f.fact_id
LEFT JOIN LATERAL (
    SELECT label
      FROM mv_label_index
     WHERE concept = q.namespace || ':' || q.local_name
       AND (cik IS NULL OR cik = LPAD(ctx.entity_identifier, 10, '0'))
     ORDER BY (cik IS NOT NULL) DESC
     LIMIT 1
) lbl ON TRUE

WHERE el.is_numeric = TRUE
  AND ctx.entity_scheme = 'http://www.sec.gov/CIK'
  -- Annual (10-K / 20-F) AND quarterly (10-Q) filings. document_type on
  -- accession is always NULL after a plugin load, so the form gate is applied
  -- via XbrlRag's filing_registry. 20-F uses the IFRS taxonomy (ifrs-full:*).
  AND (fr.form_type LIKE '10-K%'
       OR fr.form_type LIKE '20-F%'
       OR fr.form_type LIKE '10-Q%');

-- ── Indexes ─────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX ix_mff_pk        ON mv_financial_facts (fact_id);
CREATE INDEX ix_mff_lookup           ON mv_financial_facts (cik, fiscal_year, fiscal_period, concept);
CREATE INDEX ix_mff_family           ON mv_financial_facts (cik, fiscal_year, fiscal_period, concept_family);
CREATE INDEX ix_mff_nodim            ON mv_financial_facts (cik, fiscal_year, fiscal_period, concept)
    WHERE segment_axis IS NULL;
CREATE INDEX ix_mff_dim              ON mv_financial_facts
    (cik, fiscal_year, fiscal_period, concept, segment_axis, segment_member);
CREATE INDEX ix_mff_section          ON mv_financial_facts (cik, fiscal_year, section_path);
CREATE INDEX ix_mff_canonical        ON mv_financial_facts (cik, fiscal_year, fiscal_period, concept)
    WHERE is_canonical_filing = TRUE;
CREATE INDEX ix_mff_ticker           ON mv_financial_facts (ticker, fiscal_year, fiscal_period, concept_family);
CREATE INDEX ix_mff_asof             ON mv_financial_facts (cik, fiscal_year, fiscal_period, filed_date);
-- The S1 lookup reads a filer's measure across every filing (restatement-s1 R3), and
-- mv_yoy_comparison pairs within one accession (R5).
CREATE INDEX ix_mff_cik_family       ON mv_financial_facts (cik, concept_family);
CREATE INDEX ix_mff_accession        ON mv_financial_facts (accession_no);
