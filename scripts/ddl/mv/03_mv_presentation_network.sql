-- ==========================================================================
-- mv_presentation_network — section ordering and role labels  [Layer 1a]
-- ==========================================================================
-- The presentation linkbase exposed over the `parent-child` arcrole. The role
-- definition string from custom_role_type carries the SEC convention
-- "<sortcode> - <type> - <name>" (e.g. "1001 - Statement - Consolidated Balance
-- Sheets"), which this view parses into a clean section label and sort order.
--
-- Reads raw xbrlDB tables only (accession, custom_role_type, network,
-- relationship, uri).
--
-- Ordering: prefix 03_ places this BEFORE 10_mv_financial_facts —
--   04_mv_fact_section_assignment.sql selects from it.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_presentation_network CASCADE;

CREATE MATERIALIZED VIEW mv_presentation_network AS
SELECT n.accession_id,
    a.filing_accession_number AS accession_no,
    n.extended_link_role_uri_id AS link_role_uri_id,
    uri_role.uri AS link_role_uri,
    crt.definition AS role_definition,
    NULLIF(TRIM(BOTH FROM SUBSTRING(crt.definition FROM POSITION((' - '::text) IN (crt.definition)) + 3)), ''::text) AS section_label,
    rel.from_element_id AS parent_element_id,
    rel.to_element_id AS child_element_id,
    rel.tree_sequence,
    rel.tree_depth,
    rel.preferred_label_role_uri_id
   FROM network n
     JOIN accession a ON a.accession_id = n.accession_id
     JOIN uri uri_arcrole ON uri_arcrole.uri_id = n.arcrole_uri_id
     JOIN uri uri_role ON uri_role.uri_id = n.extended_link_role_uri_id
     LEFT JOIN custom_role_type crt ON crt.uri_id = n.extended_link_role_uri_id
     JOIN relationship rel ON rel.network_id = n.network_id
  WHERE uri_arcrole.uri::text = 'http://www.xbrl.org/2003/arcrole/parent-child'::text;

CREATE INDEX ix_mpn_child ON public.mv_presentation_network USING btree (accession_no, child_element_id);
CREATE INDEX ix_mpn_role ON public.mv_presentation_network USING btree (accession_no, link_role_uri);
