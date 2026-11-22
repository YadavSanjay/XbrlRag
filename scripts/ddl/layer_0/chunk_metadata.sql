-- SQL-side mirror of every prose chunk written to Qdrant. Gives untagged prose
-- (which has no fact_id, so cannot appear in section_assignment or
-- chunk_cross_links) a queryable home, enabling section-level fact↔prose
-- association (mv_fact_prose_links) and the HYBRID reverse path
-- (prose query → related fact). section_path uses the SAME HTML SectionTree
-- vocabulary as section_assignment, so narrative facts and prose chunks share a
-- join key. Written by the chunker step.
CREATE TABLE IF NOT EXISTS chunk_metadata (
    chunk_id        TEXT PRIMARY KEY,
    cik             TEXT NOT NULL,
    accession_no    TEXT NOT NULL,
    fiscal_year     INT,
    section_path    TEXT,
    char_start      INT,                           -- offset in source .htm
    char_end        INT,
    page_num        INT,
    prev_chunk_id   TEXT,
    next_chunk_id   TEXT,
    has_xbrl_facts  BOOLEAN DEFAULT FALSE           -- TRUE iff a chunk_cross_links row exists
);
CREATE INDEX IF NOT EXISTS ix_cm_section   ON chunk_metadata (cik, fiscal_year, section_path);
CREATE INDEX IF NOT EXISTS ix_cm_accession ON chunk_metadata (accession_no);