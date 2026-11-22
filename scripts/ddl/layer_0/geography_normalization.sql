-- Used by mv_geographic_facts. Maps SRT country/region member QNames to
-- ISO codes for cross-filer geographic comparison.
CREATE TABLE IF NOT EXISTS geography_normalization (
    member_qname    TEXT PRIMARY KEY,
    iso_code        TEXT,
    geo_name        TEXT,
    geo_type        TEXT                          -- 'country' | 'region' | 'segment'
);