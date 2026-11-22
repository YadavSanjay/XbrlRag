-- Per-filer dimension member labels extracted from context_dimension at ingest.
-- Only EXPLICIT dimension rows are inserted (member_qname_id IS NOT NULL).
-- TYPED dimensions (member_qname_id IS NULL, e.g. RevenueRemainingPerformanceObligation
-- satisfaction period axis) are excluded -- they carry no member concept.
-- member_label is always NULL after the SQL INSERT because the Arelle plugin drops
-- label arcs. Run DimensionMembersPopulator to patch member_label via the Arelle CLI.
CREATE TABLE IF NOT EXISTS dimension_catalog (
    cik             TEXT NOT NULL,
    accession_no    TEXT NOT NULL,
    axis_qname      TEXT NOT NULL,
    member_qname    TEXT NOT NULL,
    member_label    TEXT,
    member_label_lc TEXT GENERATED ALWAYS AS (lower(member_label)) STORED,
    first_seen_fy   INT,
    last_seen_fy    INT,
    PRIMARY KEY (cik, axis_qname, member_qname)
);
CREATE INDEX IF NOT EXISTS ix_dc_label ON dimension_catalog (cik, axis_qname, member_label_lc);