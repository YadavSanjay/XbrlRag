-- ============================================================================
-- mv_derived_metrics (Layer 3) — pre-computed ratio library with citations
-- ============================================================================
-- Grain: (cik, fiscal_year, fiscal_period, metric_name). One row per metric per
-- period. Flow inputs use the period's PRIMARY duration (annual for FY, the
-- discrete quarter for Q1-Q4); stock inputs use the period-end balance. Every
-- row carries component_fact_ids (JSONB) so the synthesizer can cite the exact
-- audited facts behind the ratio.
--
-- Item 2a expansion. Beyond the original margins it adds returns, leverage,
-- liquidity, efficiency, per-share and growth/CAGR families. Two data-model
-- realities in this universe shape the SQL:
--   * concept_family names must match what concept_mapping actually populates:
--     NetIncome (not NetIncomeLoss), CashFromOperations (not OperatingCashFlow),
--     Capex (not CapitalExpenditures), PretaxIncome (not PreTaxIncome),
--     TaxExpense (not IncomeTaxExpense). The pre-expansion view used the wrong
--     names, so NetMargin/FreeCashFlow/EffectiveTaxRate silently produced no rows.
--   * StockholdersEquity and EPS are tagged by ~1 filer, so equity is DERIVED as
--     TotalAssets - TotalLiabilities and diluted EPS as NetIncome / diluted shares.
--
-- Averaging (per the analyst convention; docs/specs/period-disclosure.md R4, R7.5):
-- ratios with a balance-sheet denominator (ROA/ROE/ROIC, asset & inventory turnover,
-- DSO/DIO) use (opening + closing) / 2. The opening balance is the SAME filing's
-- instant of the same concept and unit stored at the fiscal year's start (its
-- comparative balance-sheet column; stored next-midnight, so it equals the year's
-- period_start), the lowest fact_id among several, as
-- src/retriever/xbrl/period_disclosure.py opening_balance picks it. When the filing
-- has none, the closing balance is the denominator and `formula` says so
-- ("... (closing; opening balance not filed)"), as average_based_ratio does. (The
-- prior-year filing's balance was used before: a filer's first corpus year then
-- divided by the closing balance while `formula` said avg(...).) Those
-- annualized/multi-year metrics are computed for FY only; period-agnostic ratios
-- (margins, liquidity, coverage, leverage, book value) are computed for every
-- period including 10-Q quarters.
--
-- Nothing depends on this MV, so it can be rebuilt standalone.
-- ============================================================================
DROP MATERIALIZED VIEW IF EXISTS mv_derived_metrics CASCADE;

CREATE MATERIALIZED VIEW mv_derived_metrics AS
WITH prim AS (   -- primary-duration flows (annual for FY, discrete quarter for Qn), deduped
    SELECT DISTINCT ON (cik, fiscal_year, fiscal_period, concept_family)
           cik, ticker, fiscal_year, fiscal_period, concept_family, value, fact_id,
           accession_no, period_start
      FROM mv_financial_facts
     WHERE segment_axis IS NULL AND is_canonical_filing = TRUE AND is_current_period = TRUE
       AND period_type = 'duration' AND concept_family IS NOT NULL
       AND concept_pref_rank NOT BETWEEN 50 AND 99  -- component band: never a derived metric's input
       AND duration_kind = CASE WHEN fiscal_period = 'FY' THEN 'annual' ELSE 'quarter' END
     ORDER BY cik, fiscal_year, fiscal_period, concept_family, concept_pref_rank NULLS LAST,
              (value IS NULL), fact_id  -- a valued row before a filed nil (sign-and-nil)
),
inst AS (        -- period-end balance-sheet facts, deduped
    SELECT DISTINCT ON (cik, fiscal_year, fiscal_period, concept_family)
           cik, ticker, fiscal_year, fiscal_period, concept_family, value, fact_id,
           accession_no, concept, unit
      FROM mv_financial_facts
     WHERE segment_axis IS NULL AND is_canonical_filing = TRUE AND is_current_period = TRUE
       AND period_type = 'instant' AND concept_family IS NOT NULL
       AND concept_pref_rank NOT BETWEEN 50 AND 99
     ORDER BY cik, fiscal_year, fiscal_period, concept_family, concept_pref_rank NULLS LAST,
              (value IS NULL), fact_id  -- a valued row before a filed nil (sign-and-nil)
),
eq AS (          -- equity DERIVED as TotalAssets - TotalLiabilities (per period)
    SELECT ta.cik, ta.ticker, ta.fiscal_year, ta.fiscal_period,
           ta.value - tl.value AS equity, ta.fact_id AS ta_fact, tl.fact_id AS tl_fact
      FROM inst ta
      JOIN inst tl USING (cik, fiscal_year, fiscal_period)
     WHERE ta.concept_family = 'TotalAssets' AND tl.concept_family = 'TotalLiabilities'
),
-- The fiscal year's start in each FY filing: the stored start of its current-period
-- annual flows (one date in practice; MIN keeps it deterministic).
fy_start AS (
    SELECT cik, fiscal_year, accession_no, MIN(period_start) AS period_start
      FROM prim
     WHERE fiscal_period = 'FY'
     GROUP BY cik, fiscal_year, accession_no
),
-- Opening/closing averaging (FY only): the opening balance is the same filing's
-- valued instant of the same concept and unit at the year's start.
bal_avg AS (
    SELECT c.cik, c.fiscal_year, c.fiscal_period, c.concept_family,
           c.value AS end_v, c.fact_id AS end_f,
           o.value AS begin_v, o.fact_id AS begin_f,
           CASE WHEN o.value IS NOT NULL THEN (c.value + o.value) / 2.0 ELSE c.value END AS avg_v,
           (o.value IS NULL) AS closing_only
      FROM inst c
      LEFT JOIN fy_start fs
             ON fs.cik = c.cik AND fs.fiscal_year = c.fiscal_year AND fs.accession_no = c.accession_no
      LEFT JOIN LATERAL (
          SELECT ob.value, ob.fact_id
            FROM mv_financial_facts ob
           WHERE ob.accession_no = c.accession_no AND ob.cik = c.cik
             AND ob.concept = c.concept AND ob.unit IS NOT DISTINCT FROM c.unit
             AND ob.period_type = 'instant' AND ob.segment_axis IS NULL
             AND ob.value IS NOT NULL AND ob.period_end = fs.period_start
           ORDER BY ob.fact_id
           LIMIT 1
      ) o ON TRUE
     WHERE c.fiscal_period = 'FY'
       AND c.concept_family IN ('TotalAssets', 'TotalLiabilities', 'Inventory', 'AccountsReceivable')
),
assets_avg AS (SELECT * FROM bal_avg WHERE concept_family = 'TotalAssets'),
inv_avg    AS (SELECT * FROM bal_avg WHERE concept_family = 'Inventory'),
ar_avg     AS (SELECT * FROM bal_avg WHERE concept_family = 'AccountsReceivable'),
-- Equity = TotalAssets - TotalLiabilities at both dates; averaged only when both
-- opening balances were filed.
eq_avg AS (
    SELECT ta.cik, ta.fiscal_year, ta.fiscal_period,
           ta.end_v - tl.end_v AS end_v, ta.end_f AS ta_fact, tl.end_f AS tl_fact,
           ta.begin_f AS ta_begin_fact, tl.begin_f AS tl_begin_fact,
           CASE WHEN ta.begin_v IS NOT NULL AND tl.begin_v IS NOT NULL
                THEN ((ta.end_v - tl.end_v) + (ta.begin_v - tl.begin_v)) / 2.0
                ELSE ta.end_v - tl.end_v END AS avg_v,
           (ta.begin_v IS NULL OR tl.begin_v IS NULL) AS closing_only
      FROM bal_avg ta
      JOIN bal_avg tl USING (cik, fiscal_year, fiscal_period)
     WHERE ta.concept_family = 'TotalAssets' AND tl.concept_family = 'TotalLiabilities'
)
SELECT cik, ticker, fiscal_year, fiscal_period, metric_name, value, formula, components AS component_fact_ids
FROM (
    -- ===== Margins & flow ratios (all periods) =====
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'GrossMarginPct' AS metric_name,
           CASE WHEN rev.value <> 0 THEN (rev.value - cogs.value) / rev.value END AS value,
           '(Revenue - CostOfRevenue) / Revenue' AS formula,
           -- GrossProfit / Revenue is the same margin from the filer's reported gross
           -- profit; when it is tagged, either fact may be cited for the second input
           -- (docs/specs/alternative-inputs-grounding.md).
           jsonb_build_object(
             'revenue', rev.fact_id,
             'cost_of_revenue', CASE WHEN gp.fact_id IS NULL THEN to_jsonb(cogs.fact_id)
                                     ELSE jsonb_build_array(cogs.fact_id, gp.fact_id) END
           ) AS components
      FROM prim rev JOIN prim cogs USING (cik, fiscal_year, fiscal_period)
      LEFT JOIN prim gp
             ON gp.cik = rev.cik AND gp.fiscal_year = rev.fiscal_year
            AND gp.fiscal_period = rev.fiscal_period AND gp.concept_family = 'GrossProfit'
     WHERE rev.concept_family = 'Revenue' AND cogs.concept_family = 'CostOfRevenue'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'OperatingMarginPct',
           CASE WHEN rev.value <> 0 THEN oi.value / rev.value END,
           'OperatingIncome / Revenue',
           jsonb_build_object('revenue', rev.fact_id, 'operating_income', oi.fact_id)
      FROM prim rev JOIN prim oi USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND oi.concept_family = 'OperatingIncome'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'NetMarginPct',
           CASE WHEN rev.value <> 0 THEN ni.value / rev.value END,
           'NetIncome / Revenue',
           jsonb_build_object('revenue', rev.fact_id, 'net_income', ni.fact_id)
      FROM prim rev JOIN prim ni USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND ni.concept_family = 'NetIncome'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'FreeCashFlowMarginPct',
           CASE WHEN rev.value <> 0 THEN (ocf.value - capex.value) / rev.value END,
           '(CashFromOperations - Capex) / Revenue',
           jsonb_build_object('revenue', rev.fact_id, 'operating_cash_flow', ocf.fact_id, 'capex', capex.fact_id)
      FROM prim rev
      JOIN prim ocf   USING (cik, fiscal_year, fiscal_period)
      JOIN prim capex USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND ocf.concept_family = 'CashFromOperations'
       AND capex.concept_family = 'Capex'

    UNION ALL
    SELECT ocf.cik, ocf.ticker, ocf.fiscal_year, ocf.fiscal_period, 'FreeCashFlow',
           ocf.value - capex.value,
           'CashFromOperations - Capex',
           jsonb_build_object('operating_cash_flow', ocf.fact_id, 'capex', capex.fact_id)
      FROM prim ocf JOIN prim capex USING (cik, fiscal_year, fiscal_period)
     WHERE ocf.concept_family = 'CashFromOperations' AND capex.concept_family = 'Capex'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'RnDIntensityPct',
           CASE WHEN rev.value <> 0 THEN rd.value / rev.value END,
           'ResearchAndDevelopment / Revenue',
           jsonb_build_object('revenue', rev.fact_id, 'rnd', rd.fact_id)
      FROM prim rev JOIN prim rd USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND rd.concept_family = 'ResearchAndDevelopment'

    UNION ALL
    -- The filer's reported (tagged) rate when it has one, else TaxExpense / PretaxIncome:
    -- the fact store answers "effective tax rate" with the reported fact, so the
    -- ground truth must too (docs/specs/within-concept-confidence.md). The reported
    -- rate is rounded (Broadcom FY2025 -1.7% vs -1.747% derived).
    SELECT pti.cik, pti.ticker, pti.fiscal_year, pti.fiscal_period, 'EffectiveTaxRate',
           COALESCE(etr.value, CASE WHEN pti.value <> 0 THEN tax.value / pti.value END),
           CASE WHEN etr.value IS NOT NULL THEN 'EffectiveTaxRate (reported)'
                ELSE 'TaxExpense / PretaxIncome' END,
           CASE WHEN etr.value IS NOT NULL
                THEN jsonb_build_object('effective_tax_rate', etr.fact_id)
                ELSE jsonb_build_object('pretax_income', pti.fact_id, 'tax', tax.fact_id) END
      FROM prim pti JOIN prim tax USING (cik, fiscal_year, fiscal_period)
      LEFT JOIN prim etr
             ON etr.cik = pti.cik AND etr.fiscal_year = pti.fiscal_year
            AND etr.fiscal_period = pti.fiscal_period
            AND etr.concept_family = 'EffectiveTaxRate'
     WHERE pti.concept_family = 'PretaxIncome' AND tax.concept_family = 'TaxExpense'

    UNION ALL
    SELECT oi.cik, oi.ticker, oi.fiscal_year, oi.fiscal_period, 'InterestCoverage',
           CASE WHEN ie.value <> 0 THEN oi.value / ie.value END,
           'OperatingIncome / InterestExpense',
           jsonb_build_object('operating_income', oi.fact_id, 'interest_expense', ie.fact_id)
      FROM prim oi JOIN prim ie USING (cik, fiscal_year, fiscal_period)
     WHERE oi.concept_family = 'OperatingIncome' AND ie.concept_family = 'InterestExpense'

    -- ===== Filings-derivable non-GAAP-style measures =====
    -- These are computed purely from tagged 10-K/10-Q facts. Management's own
    -- "adjusted EBITDA" / "non-GAAP EPS" reconciliations often live only in an
    -- earnings 8-K exhibit (not ingested here), so these are proxies built from
    -- GAAP components, labeled as such in the formula.
    UNION ALL
    SELECT oi.cik, oi.ticker, oi.fiscal_year, oi.fiscal_period, 'EBITDA',
           oi.value + da.value,
           'OperatingIncome + DepreciationAndAmortization (proxy)',
           jsonb_build_object('operating_income', oi.fact_id, 'dep_amort', da.fact_id)
      FROM prim oi JOIN prim da USING (cik, fiscal_year, fiscal_period)
     WHERE oi.concept_family = 'OperatingIncome' AND da.concept_family = 'DepreciationAndAmortization'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'EBITDAMarginPct',
           CASE WHEN rev.value <> 0 THEN (oi.value + da.value) / rev.value END,
           '(OperatingIncome + DepreciationAndAmortization) / Revenue (proxy)',
           jsonb_build_object('revenue', rev.fact_id, 'operating_income', oi.fact_id, 'dep_amort', da.fact_id)
      FROM prim rev
      JOIN prim oi USING (cik, fiscal_year, fiscal_period)
      JOIN prim da USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND oi.concept_family = 'OperatingIncome'
       AND da.concept_family = 'DepreciationAndAmortization'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'SbcAdjOperatingMarginPct',
           CASE WHEN rev.value <> 0 THEN (oi.value + sbc.value) / rev.value END,
           '(OperatingIncome + ShareBasedCompensation) / Revenue (non-GAAP style)',
           jsonb_build_object('revenue', rev.fact_id, 'operating_income', oi.fact_id, 'sbc', sbc.fact_id)
      FROM prim rev
      JOIN prim oi  USING (cik, fiscal_year, fiscal_period)
      JOIN prim sbc USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND oi.concept_family = 'OperatingIncome'
       AND sbc.concept_family = 'ShareBasedCompensation'

    -- ===== Liquidity & leverage (period-end balances; all periods) =====
    UNION ALL
    SELECT ca.cik, ca.ticker, ca.fiscal_year, ca.fiscal_period, 'CurrentRatio',
           CASE WHEN cl.value <> 0 THEN ca.value / cl.value END,
           'CurrentAssets / CurrentLiabilities',
           jsonb_build_object('current_assets', ca.fact_id, 'current_liabilities', cl.fact_id)
      FROM inst ca JOIN inst cl USING (cik, fiscal_year, fiscal_period)
     WHERE ca.concept_family = 'CurrentAssets' AND cl.concept_family = 'CurrentLiabilities'

    UNION ALL
    SELECT ca.cik, ca.ticker, ca.fiscal_year, ca.fiscal_period, 'QuickRatio',
           CASE WHEN cl.value <> 0 THEN (ca.value - inv.value) / cl.value END,
           '(CurrentAssets - Inventory) / CurrentLiabilities',
           jsonb_build_object('current_assets', ca.fact_id, 'inventory', inv.fact_id, 'current_liabilities', cl.fact_id)
      FROM inst ca
      JOIN inst cl  USING (cik, fiscal_year, fiscal_period)
      JOIN inst inv USING (cik, fiscal_year, fiscal_period)
     WHERE ca.concept_family = 'CurrentAssets' AND cl.concept_family = 'CurrentLiabilities'
       AND inv.concept_family = 'Inventory'

    UNION ALL
    SELECT cash.cik, cash.ticker, cash.fiscal_year, cash.fiscal_period, 'CashRatio',
           CASE WHEN cl.value <> 0 THEN cash.value / cl.value END,
           'CashAndEquivalents / CurrentLiabilities',
           jsonb_build_object('cash', cash.fact_id, 'current_liabilities', cl.fact_id)
      FROM inst cash JOIN inst cl USING (cik, fiscal_year, fiscal_period)
     WHERE cash.concept_family = 'CashAndEquivalents' AND cl.concept_family = 'CurrentLiabilities'

    UNION ALL
    SELECT d.cik, e.ticker, d.fiscal_year, d.fiscal_period, 'DebtToEquity',
           CASE WHEN e.equity <> 0 THEN d.value / e.equity END,
           'Debt / (TotalAssets - TotalLiabilities)',
           jsonb_build_object('debt', d.fact_id, 'total_assets', e.ta_fact, 'total_liabilities', e.tl_fact)
      FROM inst d JOIN eq e USING (cik, fiscal_year, fiscal_period)
     WHERE d.concept_family = 'Debt'

    UNION ALL
    SELECT d.cik, d.ticker, d.fiscal_year, d.fiscal_period, 'NetDebt',
           d.value - cash.value,
           'Debt - CashAndEquivalents',
           jsonb_build_object('debt', d.fact_id, 'cash', cash.fact_id)
      FROM inst d JOIN inst cash USING (cik, fiscal_year, fiscal_period)
     WHERE d.concept_family = 'Debt' AND cash.concept_family = 'CashAndEquivalents'

    -- ===== Per-share / book value (period-end; all periods) =====
    UNION ALL
    SELECT e.cik, e.ticker, e.fiscal_year, e.fiscal_period, 'BookValuePerShare',
           CASE WHEN sh.value <> 0 THEN e.equity / sh.value END,
           '(TotalAssets - TotalLiabilities) / SharesOutstanding',
           jsonb_build_object('total_assets', e.ta_fact, 'total_liabilities', e.tl_fact, 'shares', sh.fact_id)
      FROM eq e JOIN inst sh USING (cik, fiscal_year, fiscal_period)
     WHERE sh.concept_family = 'SharesOutstanding'

    -- ===== Annual-only: returns, turnover, days, per-share flow, growth =====
    UNION ALL
    SELECT ni.cik, ni.ticker, ni.fiscal_year, ni.fiscal_period, 'ReturnOnAssetsPct',
           CASE WHEN a.avg_v <> 0 THEN ni.value / a.avg_v END,
           CASE WHEN a.closing_only THEN 'NetIncome / TotalAssets (closing; opening balance not filed)'
                ELSE 'NetIncome / avg(TotalAssets)' END,
           jsonb_build_object('net_income', ni.fact_id, 'assets_end', a.end_f, 'assets_begin', a.begin_f)
      FROM prim ni JOIN assets_avg a USING (cik, fiscal_year, fiscal_period)
     WHERE ni.concept_family = 'NetIncome' AND ni.fiscal_period = 'FY'

    UNION ALL
    SELECT ni.cik, ni.ticker, ni.fiscal_year, ni.fiscal_period, 'ReturnOnEquityPct',
           CASE WHEN e.avg_v <> 0 THEN ni.value / e.avg_v END,
           CASE WHEN e.closing_only THEN 'NetIncome / (TotalAssets - TotalLiabilities) (closing; opening balance not filed)'
                ELSE 'NetIncome / avg(TotalAssets - TotalLiabilities)' END,
           jsonb_build_object('net_income', ni.fact_id, 'total_assets', e.ta_fact, 'total_liabilities', e.tl_fact,
                              'total_assets_begin', e.ta_begin_fact, 'total_liabilities_begin', e.tl_begin_fact)
      FROM prim ni JOIN eq_avg e USING (cik, fiscal_year, fiscal_period)
     WHERE ni.concept_family = 'NetIncome' AND ni.fiscal_period = 'FY'

    UNION ALL
    SELECT oi.cik, oi.ticker, oi.fiscal_year, oi.fiscal_period, 'ReturnOnInvestedCapitalPct',
           CASE WHEN (d.value + e.avg_v) <> 0 AND pti.value <> 0
                THEN (oi.value * (1 - tax.value / pti.value)) / (d.value + e.avg_v) END,
           CASE WHEN e.closing_only THEN 'OperatingIncome*(1 - TaxExpense/PretaxIncome) / (Debt + equity) (closing; opening balance not filed)'
                ELSE 'OperatingIncome*(1 - TaxExpense/PretaxIncome) / (Debt + avg equity)' END,
           jsonb_build_object('operating_income', oi.fact_id, 'tax', tax.fact_id,
                              'pretax_income', pti.fact_id, 'debt', d.fact_id,
                              'total_assets', e.ta_fact, 'total_liabilities', e.tl_fact,
                              'total_assets_begin', e.ta_begin_fact,
                              'total_liabilities_begin', e.tl_begin_fact)
      FROM prim oi
      JOIN prim tax USING (cik, fiscal_year, fiscal_period)
      JOIN prim pti USING (cik, fiscal_year, fiscal_period)
      JOIN inst d   USING (cik, fiscal_year, fiscal_period)
      JOIN eq_avg e USING (cik, fiscal_year, fiscal_period)
     WHERE oi.concept_family = 'OperatingIncome' AND tax.concept_family = 'TaxExpense'
       AND pti.concept_family = 'PretaxIncome' AND d.concept_family = 'Debt'
       AND oi.fiscal_period = 'FY'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'AssetTurnover',
           CASE WHEN a.avg_v <> 0 THEN rev.value / a.avg_v END,
           CASE WHEN a.closing_only THEN 'Revenue / TotalAssets (closing; opening balance not filed)'
                ELSE 'Revenue / avg(TotalAssets)' END,
           jsonb_build_object('revenue', rev.fact_id, 'assets_end', a.end_f, 'assets_begin', a.begin_f)
      FROM prim rev JOIN assets_avg a USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND rev.fiscal_period = 'FY'

    UNION ALL
    SELECT cogs.cik, cogs.ticker, cogs.fiscal_year, cogs.fiscal_period, 'InventoryTurnover',
           CASE WHEN iv.avg_v <> 0 THEN cogs.value / iv.avg_v END,
           CASE WHEN iv.closing_only THEN 'CostOfRevenue / Inventory (closing; opening balance not filed)'
                ELSE 'CostOfRevenue / avg(Inventory)' END,
           jsonb_build_object('cost_of_revenue', cogs.fact_id, 'inventory_end', iv.end_f, 'inventory_begin', iv.begin_f)
      FROM prim cogs JOIN inv_avg iv USING (cik, fiscal_year, fiscal_period)
     WHERE cogs.concept_family = 'CostOfRevenue' AND cogs.fiscal_period = 'FY'

    UNION ALL
    SELECT rev.cik, rev.ticker, rev.fiscal_year, rev.fiscal_period, 'DaysSalesOutstanding',
           CASE WHEN rev.value <> 0 THEN ar.avg_v / rev.value * 365.0 END,
           CASE WHEN ar.closing_only THEN 'AccountsReceivable / Revenue * 365 (closing; opening balance not filed)'
                ELSE 'avg(AccountsReceivable) / Revenue * 365' END,
           jsonb_build_object('revenue', rev.fact_id, 'ar_end', ar.end_f, 'ar_begin', ar.begin_f)
      FROM prim rev JOIN ar_avg ar USING (cik, fiscal_year, fiscal_period)
     WHERE rev.concept_family = 'Revenue' AND rev.fiscal_period = 'FY'

    UNION ALL
    SELECT cogs.cik, cogs.ticker, cogs.fiscal_year, cogs.fiscal_period, 'DaysInventoryOutstanding',
           CASE WHEN cogs.value <> 0 THEN iv.avg_v / cogs.value * 365.0 END,
           CASE WHEN iv.closing_only THEN 'Inventory / CostOfRevenue * 365 (closing; opening balance not filed)'
                ELSE 'avg(Inventory) / CostOfRevenue * 365' END,
           jsonb_build_object('cost_of_revenue', cogs.fact_id, 'inventory_end', iv.end_f, 'inventory_begin', iv.begin_f)
      FROM prim cogs JOIN inv_avg iv USING (cik, fiscal_year, fiscal_period)
     WHERE cogs.concept_family = 'CostOfRevenue' AND cogs.fiscal_period = 'FY'

    UNION ALL
    SELECT ni.cik, ni.ticker, ni.fiscal_year, ni.fiscal_period, 'DilutedEpsDerived',
           CASE WHEN sh.value <> 0 THEN ni.value / sh.value END,
           'NetIncome / WeightedAverageSharesDiluted',
           jsonb_build_object('net_income', ni.fact_id, 'diluted_shares', sh.fact_id)
      FROM prim ni JOIN prim sh USING (cik, fiscal_year, fiscal_period)
     WHERE ni.concept_family = 'NetIncome' AND sh.concept_family = 'WeightedAverageSharesDiluted'

    UNION ALL
    SELECT ocf.cik, ocf.ticker, ocf.fiscal_year, ocf.fiscal_period, 'FreeCashFlowPerShare',
           CASE WHEN sh.value <> 0 THEN (ocf.value - capex.value) / sh.value END,
           '(CashFromOperations - Capex) / WeightedAverageSharesDiluted',
           jsonb_build_object('operating_cash_flow', ocf.fact_id, 'capex', capex.fact_id, 'diluted_shares', sh.fact_id)
      FROM prim ocf
      JOIN prim capex USING (cik, fiscal_year, fiscal_period)
      JOIN prim sh    USING (cik, fiscal_year, fiscal_period)
     WHERE ocf.concept_family = 'CashFromOperations' AND capex.concept_family = 'Capex'
       AND sh.concept_family = 'WeightedAverageSharesDiluted' AND ocf.fiscal_period = 'FY'

    -- ===== Growth & CAGR (FY, multi-year windows) =====
    UNION ALL
    SELECT cur.cik, cur.ticker, cur.fiscal_year, cur.fiscal_period, 'RevenueGrowthPct',
           CASE WHEN pr.value <> 0 THEN (cur.value - pr.value) / abs(pr.value) END,
           '(Revenue[Y] - Revenue[Y-1]) / Revenue[Y-1]',
           jsonb_build_object('revenue_current', cur.fact_id, 'revenue_prior', pr.fact_id)
      FROM prim cur
      JOIN prim pr ON pr.cik = cur.cik AND pr.concept_family = 'Revenue'
                  AND pr.fiscal_period = cur.fiscal_period AND pr.fiscal_year = cur.fiscal_year - 1
     WHERE cur.concept_family = 'Revenue' AND cur.fiscal_period = 'FY'

    UNION ALL
    SELECT cur.cik, cur.ticker, cur.fiscal_year, cur.fiscal_period, 'NetIncomeGrowthPct',
           CASE WHEN pr.value <> 0 THEN (cur.value - pr.value) / abs(pr.value) END,
           '(NetIncome[Y] - NetIncome[Y-1]) / NetIncome[Y-1]',
           jsonb_build_object('net_income_current', cur.fact_id, 'net_income_prior', pr.fact_id)
      FROM prim cur
      JOIN prim pr ON pr.cik = cur.cik AND pr.concept_family = 'NetIncome'
                  AND pr.fiscal_period = cur.fiscal_period AND pr.fiscal_year = cur.fiscal_year - 1
     WHERE cur.concept_family = 'NetIncome' AND cur.fiscal_period = 'FY'

    UNION ALL
    SELECT cur.cik, cur.ticker, cur.fiscal_year, cur.fiscal_period, 'Revenue3YrCagrPct',
           CASE WHEN p3.value > 0 AND cur.value > 0
                THEN power(cur.value / p3.value, 1.0 / 3.0) - 1 END,
           '(Revenue[Y] / Revenue[Y-3])^(1/3) - 1',
           jsonb_build_object('revenue_current', cur.fact_id, 'revenue_3yr_prior', p3.fact_id)
      FROM prim cur
      JOIN prim p3 ON p3.cik = cur.cik AND p3.concept_family = 'Revenue'
                  AND p3.fiscal_period = cur.fiscal_period AND p3.fiscal_year = cur.fiscal_year - 3
     WHERE cur.concept_family = 'Revenue' AND cur.fiscal_period = 'FY'
) metric
WHERE value IS NOT NULL;

CREATE INDEX ix_mdm_lookup ON mv_derived_metrics (cik, fiscal_year, fiscal_period, metric_name);
CREATE INDEX ix_mdm_ticker ON mv_derived_metrics (ticker, fiscal_year, fiscal_period, metric_name);
CREATE INDEX ix_mdm_metric ON mv_derived_metrics (metric_name, fiscal_year, fiscal_period);
