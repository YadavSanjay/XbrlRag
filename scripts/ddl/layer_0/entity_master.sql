-- Replaces the v1 entity_ticker AND absorbs the per-filer metadata fields
-- (filer_category, fye_month/day, sic_code) that the semantic-schema loader
-- populates on `entity` but the public-schema plugin does not. Populated at
-- ingest from DEI cover-page facts of each newly loaded accession.
CREATE TABLE IF NOT EXISTS entity_master (
    cik                 TEXT PRIMARY KEY,         -- LPAD(context.entity_identifier, 10, '0')
    company_name        TEXT,                     -- dei:EntityRegistrantName
    trading_symbol      TEXT,                     -- dei:TradingSymbol (latest)
    exchange            TEXT,                     -- dei:SecurityExchangeName (latest)
    sic_code            INT,                      -- accession.standard_industrial_classification when RSS, else SEC submission JSON
    filer_category      TEXT,                     -- dei:EntityFilerCategory
    fye_month           INT,                      -- parsed from dei:CurrentFiscalYearEndDate (--MM-DD)
    fye_day             INT,
    last_seen_fy        INT,
    last_seen_accession TEXT
);
CREATE INDEX IF NOT EXISTS ix_em_ticker    ON entity_master (trading_symbol);
CREATE INDEX IF NOT EXISTS ix_em_name_trgm ON entity_master USING GIN (lower(company_name) gin_trgm_ops);
-- Sector tags (docs/specs/sector-tags.md): GICS sector and a reviewed industry label,
-- filled from configs/entities/sectors.yaml by
-- `python -m src.ingest.sec_edgar.ingest.sector_tags`, never by layer0-populate.
-- Nullable: a filer the tags do not name has neither.
ALTER TABLE entity_master ADD COLUMN IF NOT EXISTS gics_sector TEXT;
ALTER TABLE entity_master ADD COLUMN IF NOT EXISTS industry    TEXT;
