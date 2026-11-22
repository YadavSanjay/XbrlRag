-- Narrative (HTML SectionTree) section path for tagged facts OUTSIDE the
-- financial statements (MD&A, Risk Factors, footnotes). Financial-statement
-- section paths are derived from the XBRL presentation linkbase by
-- mv_fact_section_assignment (Layer 1a). The citation anchor + page now live in
-- fact_dom_position, so this table holds section_path only.
--
-- Sparse by nature: only tagged spans in narrative regions (e.g. a $ figure
-- inside an MD&A table) ever appear here. Pure prose has no fact_id and is NOT
-- represented in this table — its section membership lives in chunk_metadata.
--
-- fact_id is INT, matching public-schema fact.fact_id.
CREATE TABLE IF NOT EXISTS section_assignment (
    fact_id         INT  NOT NULL PRIMARY KEY,
    section_path    TEXT NOT NULL,
    assignment_src  TEXT DEFAULT 'section_tree'
);
CREATE INDEX IF NOT EXISTS ix_sa_section ON section_assignment (section_path);