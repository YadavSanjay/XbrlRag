-- mv_fact_prose_links (Layer 3) — fact <-> prose chunk association.
-- STRONG links come from chunk_cross_links (DOM character-range overlap).
-- SOFT links are section-level; now joined on accession_no (same filing) rather
-- than (cik, fiscal_year), so a 10-Q fact links only to that 10-Q's prose, not
-- to a same-fiscal-year 10-K chunk.
DROP MATERIALIZED VIEW IF EXISTS mv_fact_prose_links CASCADE;

CREATE MATERIALIZED VIEW mv_fact_prose_links AS
SELECT DISTINCT ON (fact_id, chunk_id)
       fact_id, chunk_id, link_type, confidence
FROM (
    SELECT ccl.fact_id, ccl.chunk_id,
           'dom_overlap'::text AS link_type, ccl.confidence, 1 AS prio
      FROM chunk_cross_links ccl

    UNION ALL

    SELECT f.fact_id, cm.chunk_id,
           'section_match'::text AS link_type, 0.40 AS confidence, 2 AS prio
      FROM mv_financial_facts f
      JOIN chunk_metadata cm
            ON cm.accession_no  = f.accession_no
           AND cm.section_path  = f.section_path
     WHERE f.section_path IS NOT NULL
) u
ORDER BY fact_id, chunk_id, prio;

CREATE UNIQUE INDEX ix_mfpl_pk    ON mv_fact_prose_links (fact_id, chunk_id);
CREATE INDEX        ix_mfpl_chunk ON mv_fact_prose_links (chunk_id);
