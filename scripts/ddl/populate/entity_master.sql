-- Populate `entity_master` from DEI cover-page facts already loaded in xbrl_db.
--
-- NOTE: keep this file free of semicolons except the two statement terminators.
-- `dbq.py` splits on every semicolon, including ones inside `--` comments, and a
-- comment-only chunk fails with the unhelpful "can't execute an empty query".
--
-- WHY THIS FILE EXISTS: `scripts/ddl/layer_0/entity_master.sql` creates the table
-- and nothing in the ingest code ever fills it. The populator was specified in
-- `docs/design/XbrlRag_Materialized_Views.md` §3 but no ingest code implements it, so a complete ingest leaves the table
-- empty — and an empty `entity_master` silently degrades the whole query path:
--
--   * `/api/companies` typeahead returns nothing.
--   * `/api/v1/coverage?ticker=X` returns [] even for ingested filings, because it
--     filters on `em.trading_symbol` across a LEFT JOIN (an inner join in effect).
--   * The entity extractor cannot resolve "NVIDIA" -> CIK, so the XBRL planner
--     emits no numeric plan and the pipeline falls back to the (expensive)
--     narrative branch with `degraded: true`.
--
-- NOT in `layer_0/`: `bootstrap_db.py::_apply_layer0` globs that directory during
-- the *schema* phase, which runs before any fact exists — this needs to run AFTER
-- the arelle ingest. Idempotent (`ON CONFLICT DO UPDATE`), so re-run it after every
-- ingest — it only ever adds or refreshes rows.
--
-- Run (read-only is the dbq default — `--commit` is required):
--   conda run -n py312 python scripts/dbq.py -f scripts/ddl/populate/entity_master.sql --commit
--   conda run -n py312 python -m src.ingest.sec_edgar.ingest.entity_identity
--
-- company_name and trading_symbol are NOT set here: a new row gets NULLs and an
-- existing row keeps its values. `entity_identity` (run right after, as bootstrap's
-- layer0-populate step does) sets them from the cover facts, ignoring co-registrants
-- and keeping a stored symbol that is still listed. Choosing them here with MAX()/MIN()
-- named CEG after its co-registrant and turned GOOGL into GOOG on every re-run
-- (docs/specs/identity-and-stand-in.md).
--
-- CIK comes from context.entity_identifier, with DEI cover-page facts pivoted into
-- one row per CIK. The file is two statements, run in order:
--
--   1. The INSERT fills exchange, sic_code, filer_category, fye_month, fye_day and
--      last_seen_accession from the accession with the highest DEI
--      DocumentFiscalYearFocus (`dei_latest`). It does not write last_seen_fy.
--   2. The UPDATE then sets last_seen_fy on every row from `filing_registry` alone:
--      the greatest fiscal-year label over the filer's annual filings (10-K, 20-F,
--      40-F and their amendments, the forms of `fiscal_labels.ANNUAL_FORMS`), or NULL
--      when the filer has none. 10-Qs, 10-KTs and other forms never count. It
--      overwrites the stored value on every run, so the column can fall as well as
--      rise (docs/specs/last-seen-fy.md). It needs the labels that bootstrap's
--      `registries` step writes, which runs before `layer0-populate`.
--
-- last_seen_accession is still the accession with the highest DEI fiscal year, of any
-- form. It can name a 10-Q, or a filing older than the last_seen_fy label (a 10-K
-- that mistags DEI), so a row may pair an annual year with a filing of another
-- period. That is kept on purpose: `entity_identity` reads the name and symbol from
-- this accession, and moving it would be a separate change.
--
-- Two-CTE design (mirrors the dimension_catalog pattern):
--   dei_per_accession - one row per (CIK, accession): pivot the DEI facts.
--   dei_latest        - one row per CIK: DISTINCT ON picks the accession with the
--                       highest DEI fiscal year, so the INSERT sees exactly one row
--                       per conflict key and avoids the "ON CONFLICT DO UPDATE
--                       command cannot affect row a second time" error that a flat
--                       GROUP BY triggers when a CIK spans multiple accessions.
WITH dei_per_accession AS (
    -- One row per (CIK, accession): pivot cover-page DEI facts for that filing.
    SELECT
        LPAD(ctx.entity_identifier, 10, '0')                                        AS cik,
        MAX(f.fact_value) FILTER (WHERE q.local_name = 'SecurityExchangeName')      AS exchange,
        NULLIF(a.standard_industrial_classification, -1)                            AS sic_code,
        MAX(f.fact_value) FILTER (WHERE q.local_name = 'EntityFilerCategory')       AS filer_category,
        EXTRACT(MONTH FROM TO_DATE(
            '2000' || MAX(f.fact_value) FILTER (WHERE q.local_name = 'CurrentFiscalYearEndDate'),
            'YYYY--MM-DD'))::INT                                                    AS fye_month,
        EXTRACT(DAY FROM TO_DATE(
            '2000' || MAX(f.fact_value) FILTER (WHERE q.local_name = 'CurrentFiscalYearEndDate'),
            'YYYY--MM-DD'))::INT                                                    AS fye_day,
        (MAX(f.fact_value) FILTER (WHERE q.local_name = 'DocumentFiscalYearFocus'))::INT
                                                                                    AS dei_fy,
        a.filing_accession_number                                                   AS last_seen_accession
    FROM accession a
    JOIN fact     f   ON f.accession_id = a.accession_id
    JOIN context  ctx ON ctx.context_id = f.context_id
    JOIN element  el  ON el.element_id  = f.element_id
    JOIN qname    q   ON q.qname_id     = el.qname_id
    WHERE q.namespace LIKE 'http://xbrl.sec.gov/dei/%'
      AND ctx.entity_scheme = 'http://www.sec.gov/CIK'
    GROUP BY ctx.entity_identifier,
             a.standard_industrial_classification,
             a.filing_accession_number
),
dei_latest AS (
    -- Collapse to one row per CIK: pick the accession with the highest
    -- DEI fiscal year (NULLS LAST so filings without DocumentFiscalYearFocus are
    -- deprioritised). All descriptive fields come from that same row.
    SELECT DISTINCT ON (cik)
        cik, exchange,
        sic_code, filer_category, fye_month, fye_day,
        last_seen_accession
    FROM dei_per_accession
    ORDER BY cik, dei_fy DESC NULLS LAST
)
INSERT INTO entity_master (cik, exchange,
                           sic_code, filer_category, fye_month, fye_day,
                           last_seen_accession)
SELECT cik, exchange,
       sic_code, filer_category, fye_month, fye_day,
       last_seen_accession
FROM dei_latest
ON CONFLICT (cik) DO UPDATE
   SET exchange           = COALESCE(EXCLUDED.exchange,            entity_master.exchange),
       sic_code            = COALESCE(EXCLUDED.sic_code,            entity_master.sic_code),
       filer_category      = COALESCE(EXCLUDED.filer_category,      entity_master.filer_category),
       fye_month           = COALESCE(EXCLUDED.fye_month,           entity_master.fye_month),
       fye_day             = COALESCE(EXCLUDED.fye_day,             entity_master.fye_day),
       last_seen_accession = EXCLUDED.last_seen_accession;

-- last_seen_fy: the latest annual label in filing_registry, NULL with no annual filing.
-- A correlated subquery, not UPDATE ... FROM, so a filer with no annual filing is reset.
UPDATE entity_master AS em
   SET last_seen_fy = (SELECT MAX(fr.fiscal_year)
                         FROM filing_registry AS fr
                        WHERE fr.cik = em.cik
                          AND fr.form_type IN ('10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A'));
