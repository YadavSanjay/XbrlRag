-- STRONG (DOM character-range overlap) fact ↔ prose chunk links: a row exists
-- only when a tagged iXBRL element physically sat inside a chunk's HTML range
-- (written by the cross-linker step). Empty for pure-prose chunks by design —
-- section-level SOFT links are provided by mv_fact_prose_links (Layer 3).
CREATE TABLE IF NOT EXISTS chunk_cross_links (
    fact_id         INT  NOT NULL,
    chunk_id        TEXT NOT NULL,
    link_source     TEXT DEFAULT 'dom_range_overlap',
    confidence      NUMERIC(4,3),
    PRIMARY KEY (fact_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS ix_ccl_chunk ON chunk_cross_links (chunk_id);