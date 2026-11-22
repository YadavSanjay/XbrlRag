-- Populate `dimension_catalog` rows from `context_dimension`, for filings already
-- loaded in xbrl_db. See docs/design/XbrlRag_Materialized_Views.md
-- §3.
--
-- NOTE: keep this file free of semicolons except the final statement terminator.
-- `dbq.py` splits on every semicolon, including ones inside `--` comments.
--
-- WHY THIS FILE EXISTS: `scripts/ddl/layer_0/dimension_catalog.sql` creates the table
-- and nothing in the ingest code inserts rows into it. `DimensionMembersPopulator`
-- (--dimension-members) only UPDATEs `member_label` WHERE NULL, so on a table with no
-- rows it patches nothing and reports success. Result on a from-scratch database
-- (the AWS RDS build): dimension_catalog stays at 0 rows, `mv_financial_facts.
-- segment_label` is NULL for every row, and every SEGMENT query misses -- XbrlRag matches
-- a segment member by its human-readable label, not by member QName. Third instance of
-- the same class as entity_master and concept_mapping_ns_expand.
--
-- ORDER MATTERS. Run this INSERT first, then the Arelle label patch:
--   conda run -n py312 python scripts/dbq.py -f scripts/ddl/populate/dimension_catalog.sql --commit
--   <on the ingest runner>  run python -m src.ingest.sec_edgar.ingest --dimension-members --skip-registries
--   conda run -n py312 python scripts/bootstrap_db.py --phase mv
-- `member_label` is NULL for every row after this INSERT by design: the Arelle
-- public-schema plugin drops every concept-to-label arc (relationship.to_resource_id
-- is always NULL after plugin load), so the label JOIN chain below can only yield NULL.
-- The Arelle in-memory label model supplies the labels in the second step.
--
-- Idempotent via ON CONFLICT, so re-run it after every ingest.
--
-- CIK comes from context.entity_identifier (the plugin never populates the entity
-- table), and the 10-K/20-F filter is applied through filing_registry because
-- accession.document_type is always NULL.
--
-- WHY the subquery + GROUP BY (not SELECT DISTINCT): the same
-- (cik, axis_qname, member_qname) triple recurs across fiscal years for one company
-- (NVDA reuses segment members across its 2024, 2025 and 2026 10-Ks). DISTINCT only
-- collapses fully identical rows, so rows sharing the conflict key but differing on
-- fiscal_year still reach ON CONFLICT twice and raise "ON CONFLICT DO UPDATE command
-- cannot affect row a second time". Pre-aggregating with MIN/MAX collapses each key to
-- exactly one candidate row.
INSERT INTO dimension_catalog (cik, accession_no, axis_qname, member_qname, member_label, first_seen_fy, last_seen_fy)
SELECT
    cik,
    MIN(filing_accession_number)    AS accession_no,   -- accession for earliest appearance
    axis_qname,
    member_qname,
    MAX(member_label)               AS member_label,   -- always NULL, patched by DimensionMembersPopulator
    MIN(fiscal_year)                AS first_seen_fy,
    MAX(fiscal_year)                AS last_seen_fy
FROM (
    SELECT
        LPAD(ctx.entity_identifier, 10, '0')            AS cik,
        a.filing_accession_number,
        aq.namespace || ':' || aq.local_name            AS axis_qname,
        mq.namespace || ':' || mq.local_name            AS member_qname,
        lr.label                                        AS member_label,
        fr.fiscal_year
    FROM context_dimension cd
    JOIN context   ctx ON ctx.context_id  = cd.context_id
    JOIN accession a   ON a.accession_id  = ctx.accession_id
    JOIN filing_registry fr
           ON fr.accession_no = a.filing_accession_number
          AND fr.cik          = LPAD(ctx.entity_identifier, 10, '0')
    JOIN qname     aq  ON aq.qname_id     = cd.dimension_qname_id
    JOIN qname     mq  ON mq.qname_id     = cd.member_qname_id
    -- Member label via element -> relationship -> resource -> label_resource. Always
    -- NULL because the Arelle plugin drops label arcs. Kept for forward-compatibility.
    LEFT JOIN element      mel ON mel.qname_id      = cd.member_qname_id
    LEFT JOIN relationship r   ON r.to_element_id   = mel.element_id
    LEFT JOIN resource     res ON res.resource_id   = r.to_resource_id
    LEFT JOIN label_resource lr ON lr.resource_id   = res.resource_id
        AND lr.xml_lang = 'en-US'
    WHERE cd.is_default = FALSE
      AND cd.member_qname_id IS NOT NULL              -- exclude typed dimensions (no member concept)
      AND mq.namespace IS NOT NULL                    -- exclude Arelle internal typed-domain refs
      AND ctx.entity_scheme = 'http://www.sec.gov/CIK'
      AND (fr.form_type LIKE '10-K%' OR fr.form_type LIKE '20-F%')
      AND fr.superseded_by IS NULL
) sub
GROUP BY cik, axis_qname, member_qname
ON CONFLICT (cik, axis_qname, member_qname)
DO UPDATE SET
    last_seen_fy  = GREATEST(dimension_catalog.last_seen_fy,  EXCLUDED.last_seen_fy),
    first_seen_fy = LEAST(dimension_catalog.first_seen_fy,    EXCLUDED.first_seen_fy),
    member_label  = COALESCE(EXCLUDED.member_label, dimension_catalog.member_label);
