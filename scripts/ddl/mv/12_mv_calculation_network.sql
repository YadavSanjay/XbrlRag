-- ============================================================================
-- mv_calculation_network — the filer's calculation arcs, each once (Layer 1a)
-- ============================================================================
-- One row per (accession, link role, parent, child, weight) of the filer's own
-- calculation linkbase, under EITHER summation arcrole:
--   http://www.xbrl.org/2003/arcrole/summation-item   (XBRL 2.1)
--   https://xbrl.org/2023/arcrole/summation-item      (Calculations 1.1)
-- Consumed by 40_mv_sum_of_parts_checks (Layer 4), which mirrors the Python
-- reference src/ingest/sec_edgar/xbrl/calc_check.py::check_calculations
-- (docs/specs/calc-check-c11.md).
--
-- Each arc appears ONCE. The previous version LEFT JOINed custom_role_type on
-- uri_id alone; filers reuse a role URI across years, so every arc fanned out
-- 1-7 times (78,034 rows for 22,807 real arcs). The role definition is not
-- needed by the check and is no longer joined. An arc repeated in the input
-- (several networks, or both arcroles) collapses under SELECT DISTINCT; two
-- copies with DIFFERENT weights stay two rows, which 40_ reads as a weight
-- conflict (the tree's checks become 'inapplicable').
--
-- Reads only raw xbrlDB tables (network / relationship / uri / element / qname)
-- — no mv_financial_facts dependency — so it builds in Layer 1a and is never
-- dropped by a CASCADE.
--
-- Design: docs/design/XbrlRag_Materialized_Views.md §4 and §6 (publish gate).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_calculation_network CASCADE;

CREATE MATERIALIZED VIEW mv_calculation_network AS
SELECT DISTINCT
    n.accession_id,
    a.filing_accession_number                                   AS accession_no,
    uri_role.uri                                                AS link_role_uri,
    parent_q.namespace || ':' || parent_q.local_name            AS parent_concept,
    child_q.namespace || ':' || child_q.local_name              AS child_concept,
    rel.calculation_weight::numeric                             AS weight
FROM network n
JOIN accession a       ON a.accession_id = n.accession_id
JOIN uri uri_arcrole   ON uri_arcrole.uri_id = n.arcrole_uri_id
JOIN uri uri_role      ON uri_role.uri_id    = n.extended_link_role_uri_id
JOIN relationship rel  ON rel.network_id = n.network_id
JOIN element parent_el ON parent_el.element_id = rel.from_element_id
JOIN qname   parent_q  ON parent_q.qname_id    = parent_el.qname_id
JOIN element child_el  ON child_el.element_id  = rel.to_element_id
JOIN qname   child_q   ON child_q.qname_id     = child_el.qname_id
WHERE uri_arcrole.uri IN ('http://www.xbrl.org/2003/arcrole/summation-item',
                          'https://xbrl.org/2023/arcrole/summation-item');

CREATE INDEX ix_mcn_parent ON mv_calculation_network (accession_no, parent_concept);
CREATE INDEX ix_mcn_child  ON mv_calculation_network (accession_no, child_concept);
CREATE INDEX ix_mcn_role   ON mv_calculation_network (accession_no, link_role_uri);
