-- The public-schema Arelle loader drops every concept-to-label arc (its
-- relationship insert is gated on both endpoints being ModelConcept), so
-- there is no SQL path from element → label inside xbrlDB itself. This table
-- is populated by a small Arelle-Python ingest script that walks
-- modelXbrl.relationshipSet(XbrlConst.conceptLabel) on each loaded DTS and
-- writes the (concept, label_role, xml_lang, label) rows we need.
--
-- cik_override = NULL for shared us-gaap / dei / srt / country / exch labels
-- (one row per concept covers every filer); cik_override = the filer's CIK
-- for an extension concept's filer-specific label.
CREATE TABLE IF NOT EXISTS concept_labels (
    concept       TEXT NOT NULL,                  -- e.g. 'us-gaap:Revenues' or 'nvda:DataCenterMember'
    cik_override  TEXT,                           -- NULL for standard-taxonomy labels
    xml_lang      TEXT NOT NULL DEFAULT 'en-US',
    label_role    TEXT NOT NULL DEFAULT 'http://www.xbrl.org/2003/role/label',
    label         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS concept_labels_uq ON concept_labels (concept, COALESCE(cik_override, ''),xml_lang,label_role);
CREATE INDEX IF NOT EXISTS ix_cl_concept ON concept_labels (concept);