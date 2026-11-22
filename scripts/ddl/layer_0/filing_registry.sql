-- XbrlRag's authoritative canonical-filing tracker, populated from the SEC
-- submission JSON / RSS at ingest. Carries form_type and amendment state,
-- which the plugin never sets on `accession` for non-RSS loads.
CREATE TABLE IF NOT EXISTS filing_registry (
    cik             TEXT NOT NULL,
    accession_no    TEXT NOT NULL,                -- = accession.filing_accession_number
    form_type       TEXT NOT NULL,                -- '10-K', '10-K/A', '10-Q', '20-F', '20-F/A', ...
    fiscal_year     INT  NOT NULL,
    fiscal_period   TEXT NOT NULL DEFAULT 'FY',
    filed_date      DATE NOT NULL,
    period_end      DATE NOT NULL,
    is_amendment    BOOLEAN GENERATED ALWAYS AS (form_type LIKE '%/A') STORED,
    superseded_by   TEXT,                         -- accession_no of later amendment (NULL = canonical)
    PRIMARY KEY (cik, accession_no)
);
CREATE INDEX IF NOT EXISTS ix_fr_canonical ON filing_registry (cik, fiscal_year, fiscal_period)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS ix_fr_form ON filing_registry (form_type, fiscal_year);