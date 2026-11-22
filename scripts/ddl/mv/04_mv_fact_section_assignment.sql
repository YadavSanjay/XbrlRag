-- ==========================================================================
-- mv_fact_section_assignment — fact -> presentation-network section path  [Layer 1a]
-- ==========================================================================
-- Maps every fact that appears in the presentation linkbase (essentially every
-- financial-statement fact) to its section path, depth and ordering. The Layer-0
-- `section_assignment` table is then only consulted for NARRATIVE facts (MD&A,
-- Risk Factors, footnotes) — see the note in
-- src/ingest/sec_edgar/xbrl/dom_position_populator.py.
--
-- Depends on mv_presentation_network (03_), so it must be created after it.
--
-- Ordering: prefix 04_ places this BEFORE 10_mv_financial_facts —
--   10_ and 11_ both JOIN it for section_path.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_fact_section_assignment CASCADE;

CREATE MATERIALIZED VIEW mv_fact_section_assignment AS
SELECT DISTINCT ON (f.fact_id) f.fact_id,
    p.accession_no,
    p.link_role_uri AS role_uri,
    p.role_definition,
    COALESCE(p.section_label, 'Section_'::text || p.link_role_uri_id::text) AS section_path,
    p.tree_depth AS depth_in_role,
    p.tree_sequence AS sequence_in_role
   FROM fact f
     JOIN mv_presentation_network p ON p.accession_id = f.accession_id AND p.child_element_id = f.element_id
  ORDER BY f.fact_id, p.tree_depth, p.tree_sequence;

CREATE UNIQUE INDEX ix_mfsa_fact ON public.mv_fact_section_assignment USING btree (fact_id);
CREATE INDEX ix_mfsa_role ON public.mv_fact_section_assignment USING btree (accession_no, role_uri);
CREATE INDEX ix_mfsa_section ON public.mv_fact_section_assignment USING btree (section_path);
