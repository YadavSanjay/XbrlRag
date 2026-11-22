-- ==========================================================================
-- mv_label_index — per-concept English preferred label  [Layer 1a]
-- ==========================================================================
-- The best English label per concept: standard `.../role/label` preferred over
-- terseLabel / verboseLabel, and a filer-specific override preferred over the
-- standard taxonomy label when both exist. Used for resolver UX and answer
-- formatting.
--
-- It reads the Layer-0 `concept_labels` table rather than joining
-- network -> relationship -> resource -> label_resource because the public-schema
-- Arelle loader drops every concept-to-label arc (relationship.to_resource_id is
-- always NULL). XbrlRag walks those arcs at ingest and writes concept_labels.
--
-- Ordering: prefix 02_ places this BEFORE 10_mv_financial_facts —
--   10_mv_financial_facts.sql reads it for concept labels.
--
-- PROVENANCE: captured from the live xbrl_db with pg_get_viewdef() rather than
--   hand-written. This MV was created ad hoc and never committed, so a fresh
--   instance could not build 10_mv_financial_facts. This file is the canonical
--   definition; docs/design/XbrlRag_Materialized_Views.md §4 describes its
--   purpose.
-- ==========================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_label_index CASCADE;

CREATE MATERIALIZED VIEW mv_label_index AS
SELECT DISTINCT ON (concept, (COALESCE(cik_override, ''::text))) concept,
    cik_override AS cik,
    label,
    xml_lang,
    label_role
   FROM concept_labels cl
  WHERE lower(xml_lang) ~~ 'en%'::text
  ORDER BY concept, (COALESCE(cik_override, ''::text)), (
        CASE lower(xml_lang)
            WHEN 'en-us'::text THEN 0
            WHEN 'en'::text THEN 1
            ELSE 2
        END), (
        CASE label_role
            WHEN 'http://www.xbrl.org/2003/role/label'::text THEN 0
            WHEN 'http://www.xbrl.org/2003/role/terseLabel'::text THEN 1
            WHEN 'http://www.xbrl.org/2003/role/verboseLabel'::text THEN 2
            ELSE 3
        END);

CREATE UNIQUE INDEX ix_mli_pk ON public.mv_label_index USING btree (concept, cik);
CREATE INDEX ix_mli_concept ON public.mv_label_index USING btree (concept);
