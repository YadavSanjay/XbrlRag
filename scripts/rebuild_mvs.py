"""Rebuild the XBRL materialized views from the committed DDL in scripts/ddl/mv/.

This supersedes the old rebuild_dependent_mvs.py: the Layer-1a prerequisites, the
base MVs (mv_financial_facts, mv_financial_facts_multidim), their six dependents,
and the WS-1 data-integrity gate MVs (mv_anchor_coverage, mv_calculation_network,
mv_sum_of_parts_checks, mv_filing_completeness) and the Layer-4 coverage card
(mv_coverage_card) all live as versioned .sql files, applied in lexical filename
order (01_..04_, 05_, 06_, 07_, 10_, 11_, 12_, 30_..35_, 40_, 41_, 42_).

The 01_..04_ + 06_/07_ files are the Layer-1a/2 views that 10_ and 11_ read
(mv_dei_metadata, mv_label_index, mv_presentation_network,
mv_fact_section_assignment) plus the two resolver views (mv_entity_registry,
mv_fiscal_period_bounds). They were created ad hoc for a long time and captured
into DDL later, which is why their definitions come from pg_get_viewdef rather
than being hand-authored — without them a fresh instance cannot build 10_.

CASCADE behavior: dropping mv_dei_metadata (01_) or mv_fact_section_assignment
(04_) cascades all the way down through mv_financial_facts to its eight
dependents; every one of those is recreated later in the same run because the
files are applied in dependency order. mv_anchor_coverage and
mv_calculation_network read only raw tables and are never cascaded. Every .sql
file DROPs its own MV first, so the run is idempotent. A pre/post guard asserts
that no materialized view OUTSIDE the managed set is left dropped (i.e. nothing
was silently orphaned by a CASCADE).

Usage:
    conda run -n py312 python scripts/rebuild_mvs.py
"""

import glob
import os
import re
import time

import psycopg2
from dotenv import load_dotenv

DDL_DIR = os.path.join(os.path.dirname(__file__), "ddl", "mv")

# MVs this runner is responsible for creating (must equal the set it may drop).
MANAGED = {
    "mv_financial_facts",
    "mv_financial_facts_multidim",
    "mv_concept_coverage",
    "mv_segment_matrix",
    "mv_geographic_facts",
    "mv_yoy_comparison",
    "mv_derived_metrics",
    "mv_fact_prose_links",
    # WS-1 data-integrity gate MVs (A1)
    "mv_anchor_coverage",
    "mv_calculation_network",
    "mv_sum_of_parts_checks",
    "mv_filing_completeness",
    # Layer-4 per-filer-year coverage card (42_; docs/specs/coverage-card.md)
    "mv_coverage_card",
    # Layer-1a prerequisites of 10_/11_ (captured from the live DB into 01_..04_)
    "mv_dei_metadata",
    "mv_label_index",
    "mv_presentation_network",
    "mv_fact_section_assignment",
    # Layer-2 resolver views (06_/07_)
    "mv_entity_registry",
    "mv_fiscal_period_bounds",
}


def _connect():
    load_dotenv()
    return psycopg2.connect(
        host=os.environ.get("XBRL_DB_HOST", "localhost"),
        port=int(os.environ.get("XBRL_DB_PORT", "5432")),
        dbname=os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        user=os.environ.get("XBRL_DB_USER", "postgres"),
        password=os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    )


def _matviews(cur) -> set:
    cur.execute("SELECT matviewname FROM pg_matviews")
    return {r[0] for r in cur.fetchall()}


def main() -> None:
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()

    before = _matviews(cur)
    unmanaged_before = before - MANAGED

    files = sorted(glob.glob(os.path.join(DDL_DIR, "*.sql")))
    if not files:
        raise SystemExit(f"No .sql files found in {DDL_DIR}")

    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            sql = f.read()
        mv = next((w for w in re.findall(r"mv_\w+", sql)), name)
        t0 = time.time()
        print(f"  Applying {name} ({mv}) ...", end="", flush=True)
        cur.execute(sql)
        print(f" done in {time.time() - t0:.1f}s")

    after = _matviews(cur)
    unmanaged_after = after - MANAGED

    # Guard: no MV outside the managed set may have been orphaned by a CASCADE.
    orphaned = unmanaged_before - unmanaged_after
    if orphaned:
        raise SystemExit(
            f"ERROR: these unmanaged MVs were dropped and not recreated: {sorted(orphaned)}"
        )
    missing = MANAGED - after
    if missing:
        raise SystemExit(f"ERROR: managed MVs missing after rebuild: {sorted(missing)}")

    print(
        f"\nAll {len(files)} MVs rebuilt. Managed={len(MANAGED)}, "
        f"unmanaged untouched={len(unmanaged_after)}."
    )
    conn.close()


if __name__ == "__main__":
    main()
