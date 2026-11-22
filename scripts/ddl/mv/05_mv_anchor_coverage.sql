-- ============================================================================
-- mv_anchor_coverage — source-file load gate (Layer 1a)  [WS-1 / A1, GATE-A]
-- ============================================================================
-- A fact loaded with fact.xml_id = NULL has no fact_dom_position row, so its
-- citation has no anchor. Arelle takes xml_id from the element's id, and EDGAR's
-- extracted `*_htm.xml` keeps those ids, so an instance load (the entry of a
-- multi-document filing, docs/specs/ingest-multidoc-ixbrl.md) does not null it.
-- This view measures, per accession, what fraction of facts carry an xml_id.
--
-- GATE-A (structural): the ingest publish gate holds back any accession with
-- xmlid_coverage_pct < 0.95: its facts cannot be cited. See
-- src/ingest/sec_edgar/xbrl/promotion.py.
--
-- Reads only raw xbrlDB tables (no mv_financial_facts dependency), so the file
-- prefix (05_) places it BEFORE 10_mv_financial_facts — it survives the
-- DROP ... CASCADE that 10_ issues on mv_financial_facts.
--
-- Design: docs/design/XbrlRag_Materialized_Views.md §4 and §6 (publish gate).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_anchor_coverage CASCADE;

CREATE MATERIALIZED VIEW mv_anchor_coverage AS
SELECT
    a.filing_accession_number                                   AS accession_no,
    LPAD(ctx.entity_identifier, 10, '0')                        AS cik,
    COUNT(*)                                                    AS fact_count,
    COUNT(f.xml_id)                                             AS facts_with_xmlid,
    COUNT(f.xml_id)::FLOAT / NULLIF(COUNT(*), 0)                AS xmlid_coverage_pct
FROM fact f
JOIN accession a   ON a.accession_id = f.accession_id
JOIN context   ctx ON ctx.context_id = f.context_id
WHERE ctx.entity_scheme = 'http://www.sec.gov/CIK'
GROUP BY a.filing_accession_number, ctx.entity_identifier;

CREATE UNIQUE INDEX ix_mac_pk   ON mv_anchor_coverage (accession_no);
CREATE INDEX        ix_mac_fail ON mv_anchor_coverage (cik)
    WHERE xmlid_coverage_pct < 0.95;
