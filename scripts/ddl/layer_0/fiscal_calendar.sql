-- Maps (CIK, fiscal_year, fiscal_period) to exact date bounds.
-- Arelle puts fiscal_year on fact rows, but fiscal_year start date and
-- FYE month/day (needed to detect fiscal vs calendar year mismatch) are not.
CREATE TABLE IF NOT EXISTS fiscal_calendar (
    cik             TEXT NOT NULL,
    ticker          TEXT,
    fiscal_year     INT  NOT NULL,
    fiscal_period   TEXT NOT NULL DEFAULT 'FY',
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    fye_month       INT,
    fye_day         INT,
    source_concept  TEXT DEFAULT 'dei:CurrentFiscalYearEndDate',
    PRIMARY KEY (cik, fiscal_year, fiscal_period)
);