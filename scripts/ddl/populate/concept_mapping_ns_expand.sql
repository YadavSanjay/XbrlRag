-- Post-deployment data fix 2 for `concept_mapping`, from
-- docs/design/XbrlRag_Materialized_Views.md §5.1. Fix 1 (the namespace expansion)
-- moved to Python: see below.
--
-- NOTE: keep this file free of semicolons except the statement terminator.
-- `dbq.py` splits on every semicolon, including ones inside `--` comments.
--
-- FIX 1 (no longer here) — `concept_mapping.qname` is seeded short-form
-- (`us-gaap:Revenues`), but the LATERAL join inside `mv_financial_facts` builds its key
-- as `q.namespace || ':' || q.local_name` (`http://fasb.org/us-gaap/2025:Revenues`).
-- The `ns_expanded` rows bridge the two: one full-namespace copy per base row and
-- namespace version in the `qname` table. Without them `concept_family` is NULL on
-- every row of `mv_financial_facts` and every lookup returns NOT_FOUND.
--
-- This file used to INSERT those copies where missing and never update one, so copies
-- kept stale ranks after a base-rank change (docs/specs/ns-expand-rank-sync.md). They
-- are now re-derived from the base rows, and checked for drift in the same
-- transaction, by `Layer0Populator._sync_ns_expanded`. It runs after every
-- concept_mapping write (`--concept-mapping-csv`, `--concept-mapping-overrides`) and
-- last in `bootstrap_db.py`'s `layer0-populate` step, after this file. By hand:
--   conda run -n py312 python -m src.ingest.sec_edgar.ingest --sync-ns-expanded --skip-registries
--
-- FIX 2 — the `auto_override` seed maps `us-gaap:Revenues` to Revenue, ProductRevenue
-- AND ServiceRevenue at the same preference_rank. The LATERAL's
-- `ORDER BY preference_rank ASC LIMIT 1` breaks that tie arbitrarily, so ProductRevenue
-- could win. Demote the two narrower families so Revenue is always canonical. It updates
-- base and expanded rows alike, and the re-derivation that follows copies the demoted
-- base ranks, so the two agree.
--
-- The statement is conflict-scoped (it fires only on an exact tie with Revenue), so
-- re-running is safe.
--
-- Run (read-only is the dbq default, so --commit is required), then re-derive:
--   conda run -n py312 python scripts/dbq.py -f scripts/ddl/populate/concept_mapping_ns_expand.sql --commit
--   conda run -n py312 python -m src.ingest.sec_edgar.ingest --sync-ns-expanded --skip-registries
-- THEN rebuild the views, or the snapshot keeps the old families:
--   conda run -n py312 python scripts/bootstrap_db.py --phase mv
UPDATE concept_mapping AS cm
SET preference_rank = cm.preference_rank +
    CASE cm.concept_family
        WHEN 'ProductRevenue' THEN 1
        WHEN 'ServiceRevenue' THEN 2
        ELSE 0
    END
WHERE cm.concept_family IN ('ProductRevenue', 'ServiceRevenue')
  AND EXISTS (
      SELECT 1 FROM concept_mapping cm2
      WHERE cm2.qname           = cm.qname
        AND cm2.cik_override IS NOT DISTINCT FROM cm.cik_override
        AND cm2.preference_rank = cm.preference_rank
        AND cm2.concept_family  = 'Revenue'
  );
