-- ============================================================================
-- member_aliases — dimensional member reclassification map  [WS-1 / A2]
-- ============================================================================
-- When a filer RENAMES a dimension member between years (a true reclassification
-- — the member's local name changes, e.g. us-gaap segment "OEM & Other" ->
-- "OEM & Embedded"), a YoY dimensional query matches the prior year under the
-- old member and the current year under the new one, so one side comes back NULL
-- — a SILENT WRONG ANSWER (Plan-Extractor §6.1, the highest-risk failure class).
--
-- This table records old_member -> new_member per (cik, axis) so the YoY surface
-- (mv_yoy_comparison) can bridge the rename. It is populated by
-- src/ingest/sec_edgar/xbrl/member_alias_detector.py, which walks each filing's
-- prior-year COMPARATIVE column: when a fact value the PRIOR filing reported
-- under member M_old reappears, same (concept, period, value), under a
-- DIFFERENT-local-name member M_new in the current filing's comparative column,
-- that is a reclassification signal.
--
-- NB (namespace drift is NOT a rename): most cross-year member-QName changes in
-- SEC data are pure namespace-version drift — the SAME local name re-declared in
-- a new date-stamped extension namespace (e.g. broadcom.com/20221030:X ->
-- broadcom.com/20231029:X). Those are handled directly by mv_yoy_comparison
-- matching members on LOCAL NAME (namespace-insensitive), NOT via this table.
-- Only genuine local-name renames land here.
--
-- Apply with:  psql "$XBRL_DB_*" -f scripts/ddl/layer_0/member_aliases.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS member_aliases (
    cik               TEXT NOT NULL,
    axis_qname        TEXT NOT NULL,
    old_member_qname  TEXT NOT NULL,        -- as the prior filing reported it
    new_member_qname  TEXT NOT NULL,        -- as the current filing's comparative reports it
    detected_in_fy    INT,                  -- filing fiscal_year that revealed the rename
    support_count     INT NOT NULL DEFAULT 1,  -- # of supporting (concept, period) facts
    confidence        DOUBLE PRECISION,     -- support-based heuristic in [0, 1]
    old_member_label  TEXT,
    new_member_label  TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cik, axis_qname, old_member_qname, new_member_qname)
);

CREATE INDEX IF NOT EXISTS ix_ma_new ON member_aliases (cik, axis_qname, new_member_qname);
CREATE INDEX IF NOT EXISTS ix_ma_old ON member_aliases (cik, axis_qname, old_member_qname);
