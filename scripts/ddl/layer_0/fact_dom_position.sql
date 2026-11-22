-- DOM coordinates + citation anchor for EVERY iXBRL-tagged fact (financial
-- statements AND narrative). This is the universal anchor/page source: the
-- iXBRL element carries an HTML id (e.g. id="F-00001") which Arelle stores in
-- fact.xml_id, so the HTML DOM step resolves xml_id → fact.fact_id and records
-- the element's character range + page here. Replaces the html_anchor /
-- derived_page columns section_assignment used to carry for narrative facts
-- only — financial-statement facts (revenue, net income) now get a working
-- citation anchor too, which they previously lacked.
--
-- NB: the filing MUST be loaded into Arelle from the iXBRL *.htm primary
-- document, not the de-inlined *_htm.xml instance — the latter strips the HTML
-- id attributes, leaving fact.xml_id NULL and breaking this whole bridge.
-- mv_anchor_coverage (Layer 1a) is the gate that catches a wrong-file load.
CREATE TABLE IF NOT EXISTS fact_dom_position (
    fact_id      INT  NOT NULL PRIMARY KEY,        -- public-schema fact.fact_id
    accession_no TEXT NOT NULL,                    -- = accession.filing_accession_number
    xml_id       TEXT NOT NULL,                    -- = fact.xml_id (the iXBRL element id)
    char_start   INT,                              -- offset in source .htm
    char_end     INT,
    page_num     INT,                              -- from inline page markers
    html_anchor  TEXT GENERATED ALWAYS AS ('#' || xml_id) STORED
);
CREATE INDEX IF NOT EXISTS ix_fdp_accession ON fact_dom_position (accession_no);
CREATE INDEX IF NOT EXISTS ix_fdp_xmlid     ON fact_dom_position (accession_no, xml_id);