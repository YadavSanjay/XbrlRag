-- ============================================================================
-- mv_coverage_card — which canonical metrics each filer-year tags, and how (Layer 4)
-- ============================================================================
-- One row per (cik, fiscal_year, metric) for every filer-year with a canonical FY
-- filing in mv_financial_facts, for each of the ten committed card metrics (eight
-- concept families and two Revenue axes). Every card has all ten rows; a metric the
-- filer does not report is an `absent` row, not a missing one.
--
-- Concept metric: a concept is reported when the canonical FY filing has a current-
-- period, undimensioned, non-nil, annual-or-instant fact of it. Its rank is that of its
-- non-deprecated concept_mapping row in the metric's family — the filer's cik_override
-- row when there is one, else the generic row; a concept with no row does not count.
-- Tier: rank 50-99 (component band) -> component_qnames; else a filer-extension
-- namespace (is_extended) -> extension_qnames; else standard_qnames.
--
-- Axis metric: reported when a current-period, non-nil duration fact on a standard axis
-- of the metric has a concept at a primary rank (< 50 or >= 100) in the Revenue family,
-- resolved for the filer as above. Only standard_qnames (the axes found) is ever filled.
--
-- Status: the first non-empty tier (standard > extension > component_only > absent).
-- src/ingest/sec_edgar/coverage/card.py::classify_cell states the same rule and the
-- reader fails loud on a row that disagrees with it.
--
-- Depends on mv_financial_facts (10_) + concept_mapping; prefix 42_ orders it after the
-- base MV, whose CASCADE drop removes this view before it is recreated here.
--
-- Spec: docs/specs/coverage-card.md. Design: docs/design/XbrlRag_v2_Plan.md §4.5.
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_coverage_card CASCADE;

CREATE MATERIALIZED VIEW mv_coverage_card AS
WITH card_metric (metric, kind, concept_family, metric_order) AS (
    VALUES
        ('Revenue',            'concept', 'Revenue',            1),
        ('CostOfRevenue',      'concept', 'CostOfRevenue',      2),
        ('OperatingIncome',    'concept', 'OperatingIncome',    3),
        ('NetIncome',          'concept', 'NetIncome',          4),
        ('TotalAssets',        'concept', 'TotalAssets',        5),
        ('StockholdersEquity', 'concept', 'StockholdersEquity', 6),
        ('CashFromOperations', 'concept', 'CashFromOperations', 7),
        ('Capex',              'concept', 'Capex',              8),
        ('SegmentAxis',        'axis',    'Revenue',            9),
        ('GeographyAxis',      'axis',    'Revenue',           10)
),
standard_axis (metric, axis) AS (
    VALUES
        ('SegmentAxis',   'us-gaap:StatementBusinessSegmentsAxis'),
        ('SegmentAxis',   'ifrs-full:SegmentsAxis'),
        ('GeographyAxis', 'srt:StatementGeographicalAxis'),
        ('GeographyAxis', 'ifrs-full:GeographicalAreasAxis')
),
-- The facts of canonical FY filings; fiscal_year is the DEI focus year.
fy_fact AS (
    SELECT cik, ticker, fiscal_year, concept, is_extended, segment_axis, value,
           period_type, duration_kind, is_current_period
      FROM mv_financial_facts
     WHERE is_canonical_filing = TRUE
       AND fiscal_period = 'FY'
),
filer_year AS (
    SELECT cik, MAX(ticker) AS ticker, fiscal_year
      FROM fy_fact
     GROUP BY cik, fiscal_year
),
-- A concept's rank in a card family for a filer: the filer's override row, else the
-- generic row; deprecated rows are left out.
family_rank AS (
    SELECT DISTINCT ON (c.cik, cm.concept_family, cm.qname)
           c.cik, cm.concept_family, cm.qname AS concept, cm.preference_rank AS rank
      FROM (SELECT DISTINCT cik FROM filer_year) c
      JOIN concept_mapping cm
        ON cm.cik_override IS NULL OR cm.cik_override = c.cik
     WHERE COALESCE(cm.is_deprecated, FALSE) = FALSE
       AND cm.concept_family IN (SELECT concept_family FROM card_metric)
     ORDER BY c.cik, cm.concept_family, cm.qname, (cm.cik_override IS NOT NULL) DESC
),
concept_reported AS (
    SELECT DISTINCT cik, fiscal_year, concept, is_extended
      FROM fy_fact
     WHERE is_current_period
       AND segment_axis IS NULL
       AND value IS NOT NULL
       AND duration_kind IN ('annual', 'instant')
),
concept_tier AS (
    SELECT r.cik, r.fiscal_year, m.metric, r.concept, fr.rank,
           CASE
               WHEN fr.rank BETWEEN 50 AND 99 THEN 'component'
               WHEN r.is_extended            THEN 'extension'
               ELSE                               'standard'
           END AS tier
      FROM concept_reported r
      JOIN card_metric m ON m.kind = 'concept'
      JOIN family_rank fr
        ON fr.cik            = r.cik
       AND fr.concept_family = m.concept_family
       AND fr.concept        = r.concept
),
concept_tiers AS (
    SELECT cik, fiscal_year, metric,
           ARRAY_AGG(concept ORDER BY rank, concept) FILTER (WHERE tier = 'standard')
               AS standard_qnames,
           ARRAY_AGG(concept ORDER BY rank, concept) FILTER (WHERE tier = 'extension')
               AS extension_qnames,
           ARRAY_AGG(concept ORDER BY rank, concept) FILTER (WHERE tier = 'component')
               AS component_qnames
      FROM concept_tier
     GROUP BY cik, fiscal_year, metric
),
axis_reported AS (
    SELECT DISTINCT f.cik, f.fiscal_year, sa.metric, sa.axis
      FROM fy_fact f
      JOIN standard_axis sa ON sa.axis = f.segment_axis
      JOIN card_metric m    ON m.metric = sa.metric
      JOIN family_rank fr
        ON fr.cik            = f.cik
       AND fr.concept_family = m.concept_family
       AND fr.concept        = f.concept
     WHERE f.is_current_period
       AND f.period_type = 'duration'
       AND f.value IS NOT NULL
       AND (fr.rank < 50 OR fr.rank >= 100)
),
axis_tiers AS (
    SELECT cik, fiscal_year, metric,
           ARRAY_AGG(axis ORDER BY axis) AS standard_qnames,
           NULL::text[]                  AS extension_qnames,
           NULL::text[]                  AS component_qnames
      FROM axis_reported
     GROUP BY cik, fiscal_year, metric
),
tiers AS (
    SELECT * FROM concept_tiers
    UNION ALL
    SELECT * FROM axis_tiers
),
cell AS (
    SELECT fy.cik, fy.ticker, fy.fiscal_year, m.metric, m.metric_order,
           COALESCE(t.standard_qnames,  '{}'::text[]) AS standard_qnames,
           COALESCE(t.extension_qnames, '{}'::text[]) AS extension_qnames,
           COALESCE(t.component_qnames, '{}'::text[]) AS component_qnames
      FROM filer_year fy
     CROSS JOIN card_metric m
      LEFT JOIN tiers t
        ON t.cik         = fy.cik
       AND t.fiscal_year = fy.fiscal_year
       AND t.metric      = m.metric
)
SELECT cik, ticker, fiscal_year, metric,
       CASE
           WHEN cardinality(standard_qnames)  > 0 THEN 'standard'
           WHEN cardinality(extension_qnames) > 0 THEN 'extension'
           WHEN cardinality(component_qnames) > 0 THEN 'component_only'
           ELSE                                         'absent'
       END AS status,
       standard_qnames, extension_qnames, component_qnames
  FROM cell
 ORDER BY cik, fiscal_year, metric_order;

CREATE UNIQUE INDEX ix_mcard_pk     ON mv_coverage_card (cik, fiscal_year, metric);
CREATE INDEX        ix_mcard_status ON mv_coverage_card (metric, status);
