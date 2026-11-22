-- mv_geographic_facts (Layer 3) — geographic facts with their code and share of total.
-- Period-aware: current-period facts of canonical filings only.
--
-- docs/specs/segment-geo-views.md (J1, J2); the Python reference is
-- src/ingest/sec_edgar/xbrl/segment_geo.py (resolve_geography, shares_of_total), and
-- test/integration/test_segment_geo_views_live.py checks the two agree.
--   geo_code / geo_name / geo_type: the geography_normalization rows whose member_qname,
--     written in the views' QName form (split at the last ':', the standard-namespace prefix
--     rule of 10_mv_financial_facts.sql), equals segment_member exactly. One distinct
--     (iso_code, geo_name, geo_type) among them gives the result; none, or rows that
--     disagree, leave the member unmapped (geo_code NULL, geo_name = its label). The code is
--     never derived from the QName's text.
--   pct_of_total: value / the consolidated (segment_axis IS NULL) total of the same
--     accession, concept, unit, period_start and period_end. Among non-nil totals the most
--     precise (decimals NULL = INF) decide; more than one value there means no total.
--     NULL when there is no total or it is zero. Not a sum over the axis.
DROP MATERIALIZED VIEW IF EXISTS mv_geographic_facts CASCADE;

CREATE MATERIALIZED VIEW mv_geographic_facts AS
WITH gn_key AS (
    SELECT CASE
               WHEN position(':' IN gn.member_qname) = 0 THEN gn.member_qname
               WHEN ns.namespace LIKE 'http://fasb.org/us-gaap/%'        THEN 'us-gaap:'   || ns.local_name
               WHEN ns.namespace LIKE 'http://fasb.org/srt/%'            THEN 'srt:'       || ns.local_name
               WHEN ns.namespace LIKE 'http://xbrl.sec.gov/dei/%'        THEN 'dei:'       || ns.local_name
               WHEN ns.namespace LIKE 'http://xbrl.sec.gov/ecd/%'        THEN 'ecd:'       || ns.local_name
               WHEN ns.namespace LIKE 'http://xbrl.sec.gov/country/%'    THEN 'country:'   || ns.local_name
               WHEN ns.namespace LIKE 'http://xbrl.ifrs.org/taxonomy/%'  THEN 'ifrs-full:' || ns.local_name
               WHEN ns.namespace LIKE 'https://xbrl.ifrs.org/taxonomy/%' THEN 'ifrs-full:' || ns.local_name
               ELSE gn.member_qname
           END AS member_key,
           gn.iso_code, gn.geo_name, gn.geo_type
      FROM geography_normalization gn
     CROSS JOIN LATERAL (
         SELECT regexp_replace(gn.member_qname, ':[^:]*$', '') AS namespace,
                substring(gn.member_qname FROM '[^:]*$')       AS local_name
     ) ns
     WHERE gn.member_qname IS NOT NULL
),
consolidated_total AS (
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
       f.segment_member                               AS geo_member,
       NULLIF(geo.iso_code, '')                       AS geo_code,
       COALESCE(NULLIF(geo.geo_name, ''), f.segment_label) AS geo_name,
       geo.geo_type,
       f.value, f.unit, f.section_path, f.fact_id,
       f.value / NULLIF(tot.total, 0)                 AS pct_of_total
FROM mv_financial_facts f
LEFT JOIN LATERAL (
    -- At most one row: the single distinct mapping of the member's exact QName.
    SELECT (ARRAY_AGG(d.iso_code))[1] AS iso_code,
           (ARRAY_AGG(d.geo_name))[1] AS geo_name,
           (ARRAY_AGG(d.geo_type))[1] AS geo_type
      FROM (
        SELECT DISTINCT k.iso_code, k.geo_name, k.geo_type
          FROM gn_key k
         WHERE k.member_key = f.segment_member
      ) d
    HAVING COUNT(*) = 1
) geo ON TRUE
LEFT JOIN consolidated_total tot
       ON tot.accession_no = f.accession_no
      AND tot.concept      = f.concept
      AND tot.unit         = f.unit
      AND tot.period_start IS NOT DISTINCT FROM f.period_start
      AND tot.period_end   IS NOT DISTINCT FROM f.period_end
WHERE f.segment_axis IN (
          'srt:StatementGeographicalAxis',
          'ifrs-full:GeographicalAreasAxis'
      )
  AND f.is_canonical_filing = TRUE
  AND f.is_current_period   = TRUE
;

CREATE INDEX ix_mgf_lookup  ON mv_geographic_facts (cik, fiscal_year, fiscal_period, concept_family);
CREATE INDEX ix_mgf_geocode ON mv_geographic_facts (cik, fiscal_year, fiscal_period, geo_code);
