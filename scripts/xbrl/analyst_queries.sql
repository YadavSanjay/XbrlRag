-- =============================================================================
-- XbrlRag — Analyst Query Reference for mv_financial_facts and related views
-- =============================================================================
-- Validated against 60 filings (AAPL, AMD, AMZN, AVGO, GOOGL, META, MSFT, MU,
-- NVDA, ORCL, TSLA, TSM) across FY2022–FY2026.
--
-- Database  : xbrl_db (local PostgreSQL, port 5432)
-- Key views : mv_financial_facts, mv_financial_facts_multidim,
--             mv_yoy_comparison, mv_segment_matrix, mv_geographic_facts,
--             mv_derived_metrics, mv_fact_prose_links
--
-- ── De-duplication pattern ───────────────────────────────────────────────────
-- A single 10-K filing reports the *current* fiscal year AND prior comparative
-- periods (e.g. NVDA FY2026 10-K contains facts for FY2026, FY2025, FY2024).
-- All three appear in mv_financial_facts with fiscal_year=2026 but different
-- period_end dates.  Unless you want all historical comparatives, always add:
--
--   ORDER BY period_end DESC, fact_id DESC
--
-- and take the first row (or use DISTINCT ON) to get the current-FY value only.
--
-- ── Currency note ────────────────────────────────────────────────────────────
-- TSM reports in TWD (millions TWD).  Add  AND unit = 'USD'  to exclude TSM
-- from cross-filer USD comparisons, or filter by  unit = 'TWD'  to study TSM
-- on its own terms.  TSM's USD-equivalent revenue appears in mv_geographic_facts
-- and mv_yoy_comparison only when TSM tags USD-denominated subsidiary facts.
-- =============================================================================


-- =============================================================================
-- SECTION 1: INCOME STATEMENT
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1a. Single company: latest annual revenue with citation anchor
--     Purpose: resolve "What was NVDA's revenue in FY2026?" in one hit.
--     Eval template aq-1a keys rule S1 instead (the fy filing's own column as the
--     latest later filing restates it): sql_derived.s1_lookup_pick over the filer's
--     rows from every filing (docs/specs/suite-templates-s1.md).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (cik)
       ticker, fiscal_year, round(value / 1e9, 2) AS revenue_B,
       unit, period_end,
       html_anchor,                  -- fragment URL to jump to the tagged fact
       array_length(linked_chunk_ids, 1) AS n_prose_chunks
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  concept_family     = 'Revenue'
  AND  segment_axis       IS NULL
  AND  period_type        = 'duration'
  AND  is_canonical_filing = TRUE
ORDER  BY cik, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 1b. Cross-filer revenue comparison — current FY only (USD filers)
--     Purpose: "Which of our covered companies has the highest revenue?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year, round(value / 1e9, 2) AS revenue_B,
       period_end
FROM   mv_financial_facts
WHERE  concept_family     = 'Revenue'
  AND  segment_axis       IS NULL
  AND  period_type        = 'duration'
  AND  is_canonical_filing = TRUE
  AND  unit               = 'USD'
ORDER  BY ticker, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 1c. Full P&L summary for one company/year
--     Purpose: build a compact income statement from tagged facts.
--     Covers Revenue → Gross Profit → Operating Income → Pre-tax → Net Income.
--     Eval template aq-1c keys rule S1 per line, as aq-1a (see 1a).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (concept_family)
       concept_family,
       round(value / 1e9, 2) AS value_B,
       unit,
       period_end,
       html_anchor
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  segment_axis       IS NULL
  AND  period_type        = 'duration'
  AND  is_canonical_filing = TRUE
  AND  concept_family IN (
         'Revenue', 'CostOfRevenue', 'GrossProfit',
         'ResearchAndDevelopment', 'SellingGeneralAndAdministrative',
         'OperatingExpenses', 'OperatingIncome',
         'InterestExpense', 'InterestIncome',
         'PretaxIncome', 'TaxExpense', 'NetIncome'
       )
ORDER  BY concept_family, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 1d. Multi-year revenue trend for a company
--     Purpose: "Show me NVDA revenue for the past 5 fiscal years."
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (fiscal_year)
       ticker, fiscal_year, round(value / 1e9, 2) AS revenue_B,
       period_end
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  concept_family     = 'Revenue'
  AND  segment_axis       IS NULL
  AND  period_type        = 'duration'
  AND  is_canonical_filing = TRUE
ORDER  BY fiscal_year DESC, period_end DESC, fact_id DESC;


-- =============================================================================
-- SECTION 2: YEAR-OVER-YEAR COMPARISON  (mv_yoy_comparison)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 2a. YoY revenue growth — most recent filing for every USD filer
--     Purpose: "How did each company's revenue grow last year?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker,
       fy_current,
       round(value_current / 1e9, 1)    AS curr_B,
       round(value_prior   / 1e9, 1)    AS prior_B,
       round(yoy_pct_change * 100,  1)  AS yoy_pct
FROM   mv_yoy_comparison
WHERE  concept_family = 'Revenue'
  AND  segment_axis   IS NULL
  AND  unit           = 'USD'
ORDER  BY ticker, fy_current DESC, value_current DESC;


-- ---------------------------------------------------------------------------
-- 2b. YoY operating income growth — screen for operating leverage
--     Purpose: "Which companies expanded operating margins last year?"
--     High yoy_pct on OperatingIncome relative to Revenue = positive leverage.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fy_current,
       round(value_current / 1e9, 1)    AS oi_curr_B,
       round(value_prior   / 1e9, 1)    AS oi_prior_B,
       round(yoy_pct_change * 100,  1)  AS oi_yoy_pct
FROM   mv_yoy_comparison
WHERE  concept_family = 'OperatingIncome'
  AND  segment_axis   IS NULL
  AND  unit           = 'USD'
ORDER  BY ticker, fy_current DESC, value_current DESC;


-- ---------------------------------------------------------------------------
-- 2c. YoY comparison for a single concept — specific filer + two years
--     Purpose: exact fact IDs for both years (for dual-citation in a response).
-- ---------------------------------------------------------------------------
SELECT fy_current, fy_prior,
       round(value_current / 1e9, 2) AS curr_B,
       round(value_prior   / 1e9, 2) AS prior_B,
       round(yoy_pct_change * 100, 1) AS yoy_pct,
       fact_id_current, fact_id_prior
FROM   mv_yoy_comparison
WHERE  ticker         = 'NVDA'
  AND  concept_family = 'Revenue'
  AND  segment_axis   IS NULL
  AND  fy_current     = 2026
  AND  unit           = 'USD'
ORDER  BY value_current DESC
LIMIT  1;


-- =============================================================================
-- SECTION 3: SEGMENT REVENUE BREAKDOWN
-- =============================================================================
-- pct_of_axis_total is each member's share of the consolidated (undimensioned) total of
-- the same filing, concept, unit and period, not of the sum over the axis. Overlapping
-- members (an aggregate and its children) each keep their own share, so shares of one
-- axis need not sum to 100% (docs/specs/segment-geo-views.md).

-- ---------------------------------------------------------------------------
-- 3a. Product/service segments — single company latest FY
--     Purpose: "What was NVDA's Data Center revenue vs Gaming in FY2026?"
--     segment_label comes from dimension_catalog (readable member name).
--     Eval template aq-3a keys one filing's split instead (S1): the latest filing
--     that reports the year's total and a single-dimension split of it, else the
--     next (breakdown_filings / breakdown_total; docs/specs/dimensional-s1.md R5).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (segment_member)
       segment_label                        AS segment_name,
       round(value / 1e9, 2)               AS revenue_B,
       round(pct_of_axis_total * 100, 1)   AS pct_of_total,
       segment_rank
FROM   mv_segment_matrix
WHERE  ticker        = 'NVDA'
  AND  fiscal_year   = 2026
  AND  segment_axis  = 'srt:ProductOrServiceAxis'
  AND  concept_family = 'Revenue'
ORDER  BY segment_member, value DESC;


-- ---------------------------------------------------------------------------
-- 3b. Business segment comparison — AMZN NA / International / AWS (FY2024)
--     Purpose: "How did Amazon Web Services compare to the consumer segments?"
--     Eval template aq-3b keys one filing's split instead (S1): the latest filing
--     that reports the year's total and a single-dimension split of it, else the
--     next (breakdown_filings / breakdown_total; docs/specs/dimensional-s1.md R5).
--     Member QNames for company-specific segments use the full namespace URI;
--     use  segment_member LIKE '%SegmentName%'  to avoid hardcoding the year.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (segment_member)
       segment_label                        AS segment_name,
       round(value / 1e9, 2)               AS revenue_B,
       round(pct_of_axis_total * 100, 1)   AS pct_of_total,
       segment_rank
FROM   mv_segment_matrix
WHERE  ticker        = 'AMZN'
  AND  fiscal_year   = 2024
  AND  segment_axis  = 'us-gaap:StatementBusinessSegmentsAxis'
ORDER  BY segment_member, value DESC;


-- ---------------------------------------------------------------------------
-- 3c. Cross-filer cloud/AI segment revenue (FY2024) — screen for AI exposure
--     Purpose: "How large is the AI/cloud segment at each company?"
--     Each filer uses its own member name; the LIKE filter catches them all.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker, segment_member)
       ticker, fiscal_year,
       segment_label                        AS segment_name,
       round(value / 1e9, 2)               AS revenue_B,
       round(pct_of_axis_total * 100, 1)   AS pct_of_total,
       segment_axis
FROM   mv_segment_matrix
WHERE  fiscal_year = 2024
  AND  unit        = 'USD'
  AND  (
         segment_member LIKE '%DataCenter%'
      OR segment_member LIKE '%Cloud%'
      OR segment_member LIKE '%AWS%'
      OR segment_member LIKE '%Azure%'
      OR segment_member LIKE '%GoogleCloud%'
      OR segment_member LIKE '%IntelligentCloud%'
  )
ORDER  BY ticker, segment_member, value DESC;


-- ---------------------------------------------------------------------------
-- 3d. Dimensional revenue — single member via mv_financial_facts
--     Purpose: exact fact row for NVDA Data Center with citation anchor.
--     Use mv_financial_facts (not mv_segment_matrix) when you need html_anchor
--     or linked_chunk_ids for citation in the generated response.
--     Eval template aq-3d keys rule S1 instead: the fy filing's own fact as the
--     latest later filing with the same dimensions restates it
--     (sql_derived.s1_dimensional_pick; docs/specs/dimensional-s1.md R2-R3).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (cik)
       ticker, fiscal_year,
       segment_label,
       round(value / 1e9, 2)  AS revenue_B,
       period_end, html_anchor,
       linked_chunk_ids
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  concept_family     = 'Revenue'
  AND  segment_axis       = 'srt:ProductOrServiceAxis'
  AND  segment_member     LIKE '%DataCenterMember'   -- filer-extension: use LIKE
  AND  is_canonical_filing = TRUE
ORDER  BY cik, period_end DESC, fact_id DESC;


-- =============================================================================
-- SECTION 4: GEOGRAPHIC REVENUE  (mv_geographic_facts)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 4a. Geographic revenue breakdown — one company latest FY
--     Purpose: "What percentage of NVDA revenue came from the US in FY2026?"
--     geo_code is the ISO-2 code (US, CN, TW, …) or a house region code (EU, APAC,
--     ROW, NON-US, …) from the geography_normalization row of the member's exact
--     QName; NULL when no row (or disagreeing rows) name it, geo_name then = label.
--     Eval template aq-4a keys one filing's split instead (S1): the latest filing
--     that reports the year's total and a single-dimension split of it, else the
--     next (breakdown_filings / breakdown_total; docs/specs/dimensional-s1.md R5).
--     pct_of_total is the share of the consolidated total (not of the axis sum).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (geo_code)
       geo_code, geo_name,
       round(value / 1e9, 2)          AS revenue_B,
       round(pct_of_total * 100, 1)   AS pct_of_total
FROM   mv_geographic_facts
WHERE  ticker        = 'NVDA'
  AND  fiscal_year   = 2026
  AND  concept_family = 'Revenue'
  AND  unit          = 'USD'
ORDER  BY geo_code, value DESC;


-- ---------------------------------------------------------------------------
-- 4b. Cross-filer US vs Non-US revenue split (FY2024)
--     Purpose: "How dependent is each company on the US market?"
--     Caution: a filer that tags overlapping regions (US inside US & Canada) is
--     double-counted by the International sums; pick one partition per filer first.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker, is_us)
       ticker, fiscal_year,
       CASE WHEN geo_code = 'US' THEN 'US' ELSE 'International' END AS market,
       round(SUM(value) OVER (PARTITION BY ticker, fiscal_year,
             CASE WHEN geo_code = 'US' THEN 'US' ELSE 'International' END) / 1e9, 2)
                                       AS revenue_B,
       round(SUM(pct_of_total) OVER (PARTITION BY ticker, fiscal_year,
             CASE WHEN geo_code = 'US' THEN 'US' ELSE 'International' END) * 100, 1)
                                       AS pct_of_total
FROM   (
    SELECT ticker, fiscal_year, value, pct_of_total, geo_code,
           geo_code = 'US' AS is_us
    FROM   mv_geographic_facts
    WHERE  fiscal_year    = 2024
      AND  concept_family = 'Revenue'
      AND  unit           = 'USD'
) g
ORDER  BY ticker, is_us DESC;


-- ---------------------------------------------------------------------------
-- 4c. China revenue exposure across covered filers (FY2024)
--     Purpose: "Which companies have significant China revenue exposure?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year,
       round(value / 1e9, 2)          AS china_revenue_B,
       round(pct_of_total * 100, 1)   AS pct_of_total
FROM   mv_geographic_facts
WHERE  geo_code     = 'CN'
  AND  concept_family = 'Revenue'
  AND  fiscal_year  IN (2024, 2025)
  AND  unit         = 'USD'
ORDER  BY ticker, fiscal_year DESC, value DESC;


-- =============================================================================
-- SECTION 5: BALANCE SHEET
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 5a. Balance sheet snapshot — key instant-period items for one company
--     Purpose: "What is NVDA's balance sheet as of FY2026 year-end?"
--     Balance sheet facts use period_type='instant' (point-in-time, not duration).
--     Eval template aq-5a keys rule S1, as aq-1a (see 1a): the balance at the fy
--     filing's own period end; a 10-K without one has no case.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (concept_family)
       concept_family,
       round(value / 1e9, 2) AS value_B,
       unit, period_end, html_anchor
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  period_type        = 'instant'
  AND  segment_axis       IS NULL
  AND  is_canonical_filing = TRUE
  AND  concept_family IN (
         'TotalAssets', 'CurrentAssets', 'CashAndEquivalents',
         'ShortTermInvestments', 'AccountsReceivable', 'Inventory',
         'PropertyPlantAndEquipment', 'Goodwill', 'IntangibleAssets',
         'TotalLiabilities', 'CurrentLiabilities', 'AccountsPayable',
         'DeferredRevenue', 'Debt', 'StockholdersEquity', 'RetainedEarnings'
       )
ORDER  BY concept_family, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 5b. Total assets cross-filer comparison (FY2024)
--     Purpose: "How does NVDA's asset base compare to peers?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year, round(value / 1e9, 2) AS total_assets_B
FROM   mv_financial_facts
WHERE  concept_family     = 'TotalAssets'
  AND  fiscal_year        = 2024
  AND  period_type        = 'instant'
  AND  segment_axis       IS NULL
  AND  is_canonical_filing = TRUE
  AND  unit               = 'USD'
ORDER  BY ticker, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 5c. Net cash and debt levels cross-filer (FY2024)
--     Purpose: "Which companies carry the most debt / have the most cash?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker, concept_family)
       ticker, fiscal_year, concept_family,
       round(value / 1e9, 2) AS value_B, period_end
FROM   mv_financial_facts
WHERE  concept_family IN ('CashAndEquivalents', 'ShortTermInvestments', 'Debt')
  AND  fiscal_year        = 2024
  AND  period_type        = 'instant'
  AND  segment_axis       IS NULL
  AND  is_canonical_filing = TRUE
  AND  unit               = 'USD'
ORDER  BY ticker, concept_family, period_end DESC, fact_id DESC;


-- =============================================================================
-- SECTION 6: CASH FLOW
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 6a. Cash flow summary for one company
--     Purpose: "What was NVDA's free cash flow in FY2026?"
--     Eval template aq-6a keys rule S1, as aq-1a (see 1a): a restated cash flow
--     keys the restating filing's figure.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (concept_family)
       concept_family,
       round(value / 1e9, 2) AS value_B,
       unit, period_end, html_anchor
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  period_type        = 'duration'
  AND  segment_axis       IS NULL
  AND  is_canonical_filing = TRUE
  AND  concept_family IN (
         'CashFromOperations', 'CashFromInvesting', 'CashFromFinancing',
         'Capex', 'DividendsPaid', 'StockRepurchases', 'ShareBasedCompensation'
       )
ORDER  BY concept_family, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 6b. Cross-filer CapEx intensity (CapEx / Revenue) — FY2024
--     Purpose: "Which semiconductor companies are investing most heavily?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (r.ticker)
       r.ticker, r.fiscal_year,
       round(c.value / 1e9, 2)                         AS capex_B,
       round(r.value / 1e9, 2)                         AS revenue_B,
       round(c.value * 100.0 / NULLIF(r.value, 0), 1) AS capex_pct_rev
FROM   mv_financial_facts r
JOIN   mv_financial_facts c
         ON  c.cik         = r.cik
        AND  c.fiscal_year = r.fiscal_year
        AND  c.concept_family = 'Capex'
        AND  c.segment_axis IS NULL
        AND  c.period_type = 'duration'
        AND  c.period_end  = r.period_end
WHERE  r.concept_family     = 'Revenue'
  AND  r.fiscal_year        = 2024
  AND  r.segment_axis       IS NULL
  AND  r.period_type        = 'duration'
  AND  r.is_canonical_filing = TRUE
  AND  r.unit               = 'USD'
ORDER  BY r.ticker, r.period_end DESC, r.fact_id DESC;


-- =============================================================================
-- SECTION 7: PROFITABILITY METRICS  (mv_derived_metrics)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 7a. Pre-computed gross margin comparison — all filers, latest FY
--     Purpose: instant profitability screen — no manual formula needed.
--     mv_derived_metrics is the fastest path for ratio queries because the
--     formula is pre-applied; component fact_ids enable dual-citation.
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year,
       round(value * 100, 1)  AS gross_margin_pct,
       formula,
       component_fact_ids     -- JSONB {revenue: fact_id, cogs: fact_id}
FROM   mv_derived_metrics
WHERE  metric_name = 'GrossMarginPct'
ORDER  BY ticker, fiscal_year DESC;


-- ---------------------------------------------------------------------------
-- 7b. Full margin waterfall comparison for FY2024
--     Purpose: "Compare gross, operating, and net margins across peers."
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker, metric_name)
       ticker, fiscal_year, metric_name,
       round(value * 100, 1) AS pct
FROM   mv_derived_metrics
WHERE  fiscal_year = 2024
  AND  metric_name IN (
         'GrossMarginPct', 'OperatingMarginPct', 'NetMarginPct'
       )
ORDER  BY ticker, metric_name, fiscal_year DESC;


-- ---------------------------------------------------------------------------
-- 7c. R&D intensity cross-filer (FY2024)
--     Purpose: "Which companies invest the most in R&D as a share of revenue?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year,
       round(value * 100, 1) AS rnd_pct_revenue,
       formula
FROM   mv_derived_metrics
WHERE  metric_name = 'RnDIntensityPct'
  AND  fiscal_year = 2024
ORDER  BY ticker, fiscal_year DESC;


-- ---------------------------------------------------------------------------
-- 7d. Free cash flow comparison (FY2024)
--     Purpose: "Which company generates the most free cash flow?"
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year,
       round(value / 1e9, 2) AS fcf_B,
       formula
FROM   mv_derived_metrics
WHERE  metric_name = 'FreeCashFlow'
  AND  fiscal_year = 2024
ORDER  BY ticker, fiscal_year DESC;


-- =============================================================================
-- SECTION 8: MULTI-DIMENSIONAL FACTS  (mv_financial_facts_multidim)
-- =============================================================================
-- These facts carry 2+ simultaneous dimensions and cannot be stored in the
-- flat segment_axis/segment_member columns of mv_financial_facts.
-- 'dimensions' is JSONB: {axis_qname: member_qname, ...}
-- 'dimension_labels' is JSONB: {axis_qname: "Human Label", ...}

-- ---------------------------------------------------------------------------
-- 8a. Fair value hierarchy (Level 1/2/3 investments) — NVDA FY2026
--     Purpose: "How are NVDA's investments classified by fair value level?"
--     FairValueByFairValueHierarchyLevelAxis × FinancialInstrumentAxis
-- ---------------------------------------------------------------------------
SELECT fact_id, fiscal_year,
       dimension_labels ->
           'http://fasb.org/us-gaap/2025:FairValueByFairValueHierarchyLevelAxis'
                                             AS fv_level,
       dimension_labels ->
           'http://fasb.org/us-gaap/2025:FinancialInstrumentAxis'
                                             AS instrument_type,
       round(value / 1e9, 2)               AS value_B,
       unit, period_end,
       html_anchor
FROM   mv_financial_facts_multidim
WHERE  cik              = '0001045810'    -- NVDA
  AND  fiscal_year      = 2026
  AND  unit             = 'USD'
  AND  dimensions ? 'http://fasb.org/us-gaap/2025:FairValueByFairValueHierarchyLevelAxis'
ORDER  BY value DESC;


-- ---------------------------------------------------------------------------
-- 8b. Equity components by class of stock — AAPL FY2024
--     Purpose: "How much did AAPL pay in share buybacks broken by equity component?"
--     StatementEquityComponentsAxis × StatementClassOfStockAxis
--     Eval template aq-8b keys rule S1 instead: the fy filing's own fact as the
--     latest later filing with the same dimensions restates it
--     (sql_derived.s1_dimensional_pick; docs/specs/dimensional-s1.md R2-R3).
-- ---------------------------------------------------------------------------
SELECT fact_id, fiscal_year,
       dimension_labels ->
           'http://fasb.org/us-gaap/2024:StatementEquityComponentsAxis'
                                             AS equity_component,
       round(value / 1e9, 2)               AS value_B,
       unit, period_end,
       html_anchor
FROM   mv_financial_facts_multidim
WHERE  cik              = '0000320193'    -- AAPL
  AND  fiscal_year      = 2024
  AND  unit             = 'USD'
  AND  dimension_count  >= 2
  AND  dimensions ? 'http://fasb.org/us-gaap/2024:StatementEquityComponentsAxis'
ORDER  BY ABS(value) DESC
LIMIT  20;


-- ---------------------------------------------------------------------------
-- 8c. Cross-filer: all 2-dimension+ facts in a given year (summary)
--     Purpose: "How many multi-axis tagged facts does each company have?"
-- ---------------------------------------------------------------------------
SELECT   em.trading_symbol                        AS ticker,
         mffm.fiscal_year,
         mffm.dimension_count,
         COUNT(*)                                 AS fact_count
FROM     mv_financial_facts_multidim mffm
JOIN     entity_master em ON em.cik = mffm.cik
WHERE    mffm.fiscal_year = 2024
  AND    mffm.unit        = 'USD'
GROUP BY em.trading_symbol, mffm.fiscal_year, mffm.dimension_count
ORDER BY em.trading_symbol, mffm.dimension_count;


-- =============================================================================
-- SECTION 9: NARRATIVE PROSE LINKAGE  (mv_fact_prose_links)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 9a. Find prose chunks that discuss a specific tagged fact
--     Purpose: given a numeric answer, find the supporting narrative context.
--     Use case: revenue fact answered → find the prose paragraph nearby.
-- ---------------------------------------------------------------------------
SELECT pl.link_type, pl.confidence,
       cm.section_path,
       cm.chunk_id
FROM   mv_fact_prose_links pl
JOIN   chunk_metadata cm ON cm.chunk_id = pl.chunk_id
WHERE  pl.fact_id = (
    SELECT fact_id FROM mv_financial_facts
    WHERE  ticker         = 'NVDA'
      AND  fiscal_year    = 2026
      AND  concept_family = 'Revenue'
      AND  segment_axis   IS NULL
      AND  period_type    = 'duration'
      AND  is_canonical_filing = TRUE
    ORDER  BY period_end DESC, fact_id DESC
    LIMIT  1
)
ORDER  BY pl.confidence DESC, pl.link_type;


-- ---------------------------------------------------------------------------
-- 9b. Facts linked to a specific narrative prose chunk
--     Purpose: given a chunk returned by semantic search, surface related facts.
--     (chunk_id would come from the vector search result payload)
-- ---------------------------------------------------------------------------
SELECT pl.fact_id, pl.link_type, pl.confidence,
       mff.concept, mff.concept_family,
       round(mff.value / 1e9, 2)  AS value_B,
       mff.unit, mff.period_end,
       mff.html_anchor
FROM   mv_fact_prose_links pl
JOIN   mv_financial_facts mff ON mff.fact_id = pl.fact_id
WHERE  pl.chunk_id = :chunk_id     -- bind parameter from vector search
ORDER  BY pl.confidence DESC, mff.concept_family;


-- =============================================================================
-- SECTION 10: IFRS / 20-F FILER (TSM)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 10a. TSM revenue in TWD — core income metrics
--      Purpose: TSM reports in New Taiwan Dollars (millions TWD); values
--      are in the same mv_financial_facts table but unit='TWD'.
--      1 USD ≈ 32 TWD; divide by 32,000 to get approximate USD billions.
--      Eval template aq-10a keys rule S1 over the TWD rows, as aq-1a (see 1a).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (concept_family)
       concept_family,
       round(value / 1e9, 1)         AS value_B_twd,
       round(value / 1e9 / 32, 2)    AS approx_usd_B,  -- rough FX for orientation
       unit, period_end, html_anchor
FROM   mv_financial_facts
WHERE  ticker             = 'TSM'
  AND  fiscal_year        = 2024
  AND  period_type        = 'duration'
  AND  segment_axis       IS NULL
  AND  is_canonical_filing = TRUE
  AND  concept_family IN (
         'Revenue', 'GrossProfit', 'OperatingIncome',
         'PretaxIncome', 'NetIncome', 'CashFromOperations', 'Capex'
       )
ORDER  BY concept_family, period_end DESC, fact_id DESC;


-- ---------------------------------------------------------------------------
-- 10b. TSM geographic revenue breakdown (FY2024)
--      Purpose: "What share of TSMC revenue came from which regions?"
--      IFRS filers use ifrs-full:GeographicalAreasAxis (captured in mv_geo).
--      Eval template aq-10b keys one filing's split instead (S1): the latest filing
--      that reports the year's total and a single-dimension split of it, else the
--      next (breakdown_filings / breakdown_total; docs/specs/dimensional-s1.md R5).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (geo_code)
       geo_code, geo_name,
       round(value / 1e9, 1)           AS revenue_B_twd,
       round(pct_of_total * 100, 1)    AS pct_of_total
FROM   mv_geographic_facts
WHERE  ticker       = 'TSM'
  AND  fiscal_year  = 2024
ORDER  BY geo_code, value DESC;


-- ---------------------------------------------------------------------------
-- 10c. IFRS vs US GAAP: revenue concept comparison
--      Purpose: illustrate cross-taxonomy resolution via concept_family.
--      Both rows have concept_family='Revenue' even though the concept
--      names differ (us-gaap:Revenues vs ifrs-full:Revenue).
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (ticker)
       ticker, fiscal_year,
       concept                           AS xbrl_concept,
       concept_family,
       round(value / 1e9, 2)            AS revenue_B,
       unit
FROM   mv_financial_facts
WHERE  concept_family     = 'Revenue'
  AND  fiscal_year        = 2024
  AND  segment_axis       IS NULL
  AND  period_type        = 'duration'
  AND  is_canonical_filing = TRUE
  AND  ticker IN ('NVDA', 'TSM')
ORDER  BY ticker, period_end DESC, fact_id DESC;


-- =============================================================================
-- SECTION 11: HELPER QUERIES (troubleshooting / exploration)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 11a. What concept families exist for a specific filer + year?
-- ---------------------------------------------------------------------------
SELECT concept_family, COUNT(*) AS fact_count
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  is_canonical_filing = TRUE
  AND  concept_family     IS NOT NULL
GROUP  BY concept_family
ORDER  BY fact_count DESC;


-- ---------------------------------------------------------------------------
-- 11b. What segment axes does a company use?
-- ---------------------------------------------------------------------------
SELECT DISTINCT segment_axis, COUNT(*) AS fact_count
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  is_canonical_filing = TRUE
  AND  segment_axis       IS NOT NULL
GROUP  BY segment_axis
ORDER  BY fact_count DESC;


-- ---------------------------------------------------------------------------
-- 11c. What segment members are available for a given axis?
--      (Resolving "data center" → exact QName for use in subsequent query)
-- ---------------------------------------------------------------------------
SELECT DISTINCT segment_member, segment_label, COUNT(*) AS fact_count
FROM   mv_financial_facts
WHERE  ticker             = 'NVDA'
  AND  fiscal_year        = 2026
  AND  segment_axis       = 'srt:ProductOrServiceAxis'
  AND  is_canonical_filing = TRUE
GROUP  BY segment_member, segment_label
ORDER  BY fact_count DESC;


-- ---------------------------------------------------------------------------
-- 11d. Full-text view of a specific fact row with all enrichment columns
--      Purpose: debugging / verifying Phase 5/6 pipeline correctness.
-- ---------------------------------------------------------------------------
SELECT *
FROM   mv_financial_facts
WHERE  fact_id = 77;   -- replace with any fact_id of interest


-- ---------------------------------------------------------------------------
-- 11e. View coverage: how many facts per view have concept_family populated?
-- ---------------------------------------------------------------------------
SELECT
    COUNT(*)                              AS total_rows,
    COUNT(*) FILTER (WHERE concept_family IS NOT NULL)  AS with_family,
    round(COUNT(*) FILTER (WHERE concept_family IS NOT NULL) * 100.0 / COUNT(*), 1) AS pct
FROM mv_financial_facts
WHERE is_canonical_filing = TRUE;
