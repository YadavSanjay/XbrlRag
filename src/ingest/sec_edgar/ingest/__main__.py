"""CLI entry point for XbrlRag Layer-0 table population.

Populates filing_registry, fiscal_calendar (from manifest.yaml files)
and concept_mapping (from a CSV file) into the xbrl_db PostgreSQL database.

Examples::

    # Populate all three tables (registries + concept_mapping)
    python -m src.ingest.sec_edgar.ingest \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv

    # Registries only (filing_registry + fiscal_calendar)
    python -m src.ingest.sec_edgar.ingest

    # Concept mapping only
    python -m src.ingest.sec_edgar.ingest \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv \\
        --skip-registries

    # Validate manifests + CSV without writing to DB
    python -m src.ingest.sec_edgar.ingest \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv \\
        --dry-run

    # Override DB connection
    python -m src.ingest.sec_edgar.ingest \\
        --db-host localhost --db-port 5432 --db-name xbrl_db \\
        --db-user postgres --db-password postgres

    # Replace all 'hifi_kpi' rows and reload from new CSV
    python -m src.ingest.sec_edgar.ingest \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv \\
        --concept-mapping-mode replace_source \\
        --skip-registries

    # Re-derive the ns_expanded concept_mapping rows and check them
    # (--dry-run only reports the drift, and exits 1 when there is any)
    python -m src.ingest.sec_edgar.ingest --sync-ns-expanded --skip-registries

    # Classify filer extensions (FSDS cache + raw calc tables) and replace the
    # extension_classifier rows; --dry-run classifies and writes only the report
    python -m src.ingest.sec_edgar.ingest --extension-classifications --skip-registries

    # Ingest ALL filings via arelleCmdLine (skips duplicates automatically)
    python -m src.ingest.sec_edgar.ingest --arelle-ingest

    # Ingest one ticker
    python -m src.ingest.sec_edgar.ingest --arelle-ingest --ticker NVDA

    # Ingest one ticker + year
    python -m src.ingest.sec_edgar.ingest --arelle-ingest --ticker NVDA --year 2026

    # Ingest one ticker + year + form type
    python -m src.ingest.sec_edgar.ingest --arelle-ingest --ticker NVDA --year 2026 --form-type 10-K

    # Ingest a specific filing directory
    python -m src.ingest.sec_edgar.ingest \\
        --arelle-ingest --filing-dir data/storage/sec_edgar/NVDA/2026/10-K

    # Dry-run: show what would be ingested without calling arelleCmdLine
    python -m src.ingest.sec_edgar.ingest --arelle-ingest --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src import PROJECT_ROOT
from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import ArelleXBRLIngestor
from src.ingest.sec_edgar.ingest.concept_labels_populator import (
    ConceptLabelsPopulator,
    DimensionMembersPopulator,
)
from src.ingest.sec_edgar.ingest.layer0_populator import Layer0Populator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate XbrlRag Layer-0 tables (filing_registry, fiscal_calendar, concept_mapping)."
    )

    # --- Storage roots ---
    parser.add_argument(
        "--sec-root",
        default=None,
        help="Root of the SEC EDGAR local storage tree (overrides SEC_EDGAR_ROOT env var).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env in current directory).",
    )

    # --- Control flags ---
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate all inputs; no database writes.",
    )
    parser.add_argument(
        "--skip-registries",
        action="store_true",
        help="Skip filing_registry and fiscal_calendar population.",
    )

    # --- concept labels ---
    parser.add_argument(
        "--concept-labels",
        action="store_true",
        help=(
            "Post-ingest: export concept-to-label arcs via Arelle CLI and "
            "upsert into concept_labels table. "
            "Requires ARELLE_CMD in .env. "
            "Run after arelleCmdLine xbrlDB ingest (and after patch-accession-numbers)."
        ),
    )
    parser.add_argument(
        "--dimension-members",
        action="store_true",
        help=(
            "Post-ingest: export dimension axis+member labels via Arelle CLI "
            "and patch dimension_catalog.member_label where NULL. "
            "Run after the SQL INSERT that populates dimension_catalog "
            "(which leaves member_label NULL because Arelle drops label arcs)."
        ),
    )
    parser.add_argument(
        "--dimension-members-accessions",
        nargs="+",
        default=None,
        metavar="ACCESSION_NO",
        help="Limit --dimension-members to specific EDGAR accession numbers.",
    )

    parser.add_argument(
        "--concept-labels-accessions",
        nargs="+",
        default=None,
        metavar="ACCESSION_NO",
        help=(
            "Limit --concept-labels to specific EDGAR accession numbers "
            "(e.g. 0001045810-26-000021). Processes all loaded accessions if omitted."
        ),
    )

    # --- geography normalization ---
    parser.add_argument(
        "--geography-normalization-csv",
        default=None,
        metavar="CSV_PATH",
        help=(
            "Path to geography_normalization CSV. "
            "Rows starting with 'PENDING:' are automatically skipped. "
            "Generate with: python src/ingest/util/generate_geography_normalization.py --from-db"
        ),
    )
    parser.add_argument(
        "--geography-normalization-mode",
        default="upsert",
        choices=["upsert", "insert_new", "replace_all"],
        help="How to handle existing rows: upsert (default), insert_new, replace_all.",
    )

    # --- per-filer concept_mapping overrides ---
    parser.add_argument(
        "--concept-mapping-overrides",
        action="store_true",
        help=(
            "Post-ingest: detect per-filer concept_mapping rank overrides. "
            "For each ingested (cik, fiscal_year), checks if a lower-ranked QName "
            "has more facts than rank-1 for any concept_family and inserts a "
            "cik_override row to promote it. Run after xbrlDB ingest."
        ),
    )
    parser.add_argument(
        "--override-targets",
        nargs="+",
        default=None,
        metavar="CIK:FY",
        help=(
            "Limit --concept-mapping-overrides to specific CIK:FY pairs "
            "(e.g. 0001045810:2026 0000723125:2025). "
            "Discovers all ingested filings if omitted."
        ),
    )
    parser.add_argument(
        "--sync-ns-expanded",
        action="store_true",
        help=(
            "Re-derive every ns_expanded concept_mapping row from its base row and "
            "fail if any still drifts (docs/specs/ns-expand-rank-sync.md). Runs last. "
            "With --dry-run, only reports the current drift."
        ),
    )
    parser.add_argument(
        "--extension-classifications",
        action="store_true",
        help=(
            "Classify filer extensions from the FSDS cache and the raw calculation "
            "tables and replace the extension_classifier concept_mapping rows "
            "(docs/specs/extension-classification.md R7). Runs after any concept-mapping "
            "write and before the MV build. With --dry-run, classifies and reports only."
        ),
    )
    parser.add_argument(
        "--fsds-dir",
        default=str(Path(PROJECT_ROOT) / "data" / "cache" / "sec_fsds" / "filtered"),
        help="FSDS quarter folders for --extension-classifications.",
    )
    parser.add_argument(
        "--extension-report",
        default=str(
            Path(PROJECT_ROOT) / "data" / "reports" / "extension_classification.json"
        ),
        help="JSON report of --extension-classifications (agreements, "
        "disagreements, unclassified extensions with reasons).",
    )

    # --- accession number patch ---
    parser.add_argument(
        "--patch-accession-numbers",
        action="store_true",
        help=(
            "Post-ingest fix: update filing_accession_number in Arelle's "
            "accession table where Arelle stored a default/timestamp value "
            "instead of the correct EDGAR accession number. "
            "Run this after each arelleCmdLine xbrlDB ingest."
        ),
    )

    # --- Concept mapping ---
    parser.add_argument(
        "--concept-mapping-csv",
        default=None,
        metavar="CSV_PATH",
        help=(
            "Path to concept_mapping CSV file to ingest. "
            "If omitted, concept_mapping population is skipped."
        ),
    )
    parser.add_argument(
        "--concept-mapping-mode",
        default="upsert",
        choices=["upsert", "insert_new", "replace_source"],
        help=(
            "How to handle existing concept_mapping rows. "
            "'upsert' (default): update on conflict. "
            "'insert_new': skip existing rows. "
            "'replace_source': delete matching source rows first, then insert."
        ),
    )

    # --- Arelle XBRL ingest ---
    arelle_grp = parser.add_argument_group("arelle xbrl ingest")
    arelle_grp.add_argument(
        "--arelle-ingest",
        action="store_true",
        help=(
            "Invoke arelleCmdLine to ingest iXBRL primary documents into xbrl_db. "
            "Duplicate filings are skipped via accession.entry_url check. "
            "Combine with --ticker / --year / --form-type / --filing-dir to narrow scope."
        ),
    )
    arelle_grp.add_argument(
        "--ticker",
        default=None,
        metavar="TICKER",
        help="Limit --arelle-ingest to a single ticker (e.g. NVDA).",
    )
    arelle_grp.add_argument(
        "--year",
        default=None,
        metavar="YEAR",
        help="Limit --arelle-ingest to a specific filing year (e.g. 2026). Requires --ticker.",
    )
    arelle_grp.add_argument(
        "--form-type",
        default=None,
        metavar="FORM",
        help="Limit --arelle-ingest to a specific form type (e.g. 10-K). Requires --ticker and --year.",
    )
    arelle_grp.add_argument(
        "--filing-dir",
        default=None,
        metavar="DIR",
        help="Ingest a single specific filing directory. Overrides --ticker/--year/--form-type.",
    )
    arelle_grp.add_argument(
        "--arelle-cmd",
        default=None,
        metavar="PATH",
        help="Path to arelleCmdLine executable (overrides ARELLE_CMD env var).",
    )

    # --- DB connection overrides ---
    parser.add_argument("--db-host",     default=None)
    parser.add_argument("--db-port",     type=int, default=None)
    parser.add_argument("--db-name",     default=None)
    parser.add_argument("--db-user",     default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(args.env_file)

    pop = Layer0Populator.from_env(sec_edgar_root=args.sec_root)

    if args.db_host:     pop._db_conn_params["host"]     = args.db_host
    if args.db_port:     pop._db_conn_params["port"]     = args.db_port
    if args.db_name:     pop._db_conn_params["dbname"]   = args.db_name
    if args.db_user:     pop._db_conn_params["user"]     = args.db_user
    if args.db_password: pop._db_conn_params["password"] = args.db_password

    all_errors: list[str] = []

    # --- arelle xbrl ingest ---
    if args.arelle_ingest:
        ingestor = ArelleXBRLIngestor.from_env(
            arelle_cmd=args.arelle_cmd,
            sec_edgar_root=args.sec_root,
        )
        if args.db_host:     ingestor._db_conn_params["host"]     = args.db_host
        if args.db_port:     ingestor._db_conn_params["port"]     = args.db_port
        if args.db_name:     ingestor._db_conn_params["dbname"]   = args.db_name
        if args.db_user:     ingestor._db_conn_params["user"]     = args.db_user
        if args.db_password: ingestor._db_conn_params["password"] = args.db_password

        if args.filing_dir:
            ai_stats = ingestor.ingest_filing(Path(args.filing_dir), dry_run=args.dry_run)
        elif args.ticker:
            ai_stats = ingestor.ingest_ticker(
                args.ticker,
                year=args.year,
                form_type=args.form_type,
                dry_run=args.dry_run,
            )
        else:
            ai_stats = ingestor.ingest_all(dry_run=args.dry_run)

        all_errors.extend(ai_stats.errors)
        _print_arelle_summary(ai_stats, args.dry_run)

    # --- geography normalization ---
    if args.geography_normalization_csv:
        gn_stats = pop.populate_geography_normalization(
            csv_path=args.geography_normalization_csv,
            mode=args.geography_normalization_mode,
            dry_run=args.dry_run,
        )
        all_errors.extend(gn_stats.get("errors", []))
        _print_geography_summary(gn_stats)

    # --- per-filer concept_mapping overrides ---
    if args.concept_mapping_overrides:
        targets = None
        if args.override_targets:
            targets = []
            for t in args.override_targets:
                cik, fy = t.split(":", 1)
                targets.append((cik.zfill(10), int(fy)))
        ov_stats = pop.populate_concept_mapping_overrides(
            targets=targets,
            dry_run=args.dry_run,
        )
        all_errors.extend(ov_stats.get("errors", []))
        _print_override_summary(ov_stats)

    # --- dimension member labels (post-arelle-ingest) ---
    if args.dimension_members:
        dm_pop = DimensionMembersPopulator.from_env()
        dm_stats = dm_pop.populate(
            accession_nos=args.dimension_members_accessions,
            dry_run=args.dry_run,
        )
        all_errors.extend(dm_stats.get("errors", []))
        _print_dimension_members_summary(dm_stats)

    # --- concept labels (post-arelle-ingest) ---
    if args.concept_labels:
        cl_pop = ConceptLabelsPopulator.from_env()
        cl_stats = cl_pop.populate(
            accession_nos=args.concept_labels_accessions,
            dry_run=args.dry_run,
        )
        all_errors.extend(cl_stats.get("errors", []))
        _print_concept_labels_summary(cl_stats)

    # --- accession number patch (post-arelle-ingest fix) ---
    if args.patch_accession_numbers:
        patch_stats = pop.patch_accession_numbers(dry_run=args.dry_run)
        all_errors.extend(patch_stats.get("errors", []))
        _print_patch_summary(patch_stats)

    # --- filing_registry + fiscal_calendar ---
    if not args.skip_registries:
        reg_stats = pop.populate_registries(dry_run=args.dry_run)
        all_errors.extend(reg_stats.get("errors", []))
        _print_registry_summary(reg_stats, args.dry_run)

    # --- concept_mapping ---
    if args.concept_mapping_csv:
        cm_stats = pop.populate_concept_mapping(
            csv_path=args.concept_mapping_csv,
            mode=args.concept_mapping_mode,
            dry_run=args.dry_run,
        )
        all_errors.extend(cm_stats.get("errors", []))
        _print_concept_mapping_summary(cm_stats)

    # --- extension classifier rows (after every other mapping write) ---
    if args.extension_classifications:
        ec_stats = pop.populate_extension_classifications(
            fsds_dir=args.fsds_dir,
            report_path=args.extension_report,
            dry_run=args.dry_run,
        )
        _print_extension_summary(ec_stats)

    # --- ns_expanded re-derivation + drift check (last: it reads every mapping row) ---
    if args.sync_ns_expanded:
        ns_stats = pop.sync_ns_expanded(dry_run=args.dry_run)
        _print_ns_expanded_summary(ns_stats)
        if ns_stats["dry_run"] and ns_stats["drifted_before"]:
            all_errors.append(
                f"{ns_stats['drifted_before']} ns_expanded row(s) drift from their base rows"
            )

    if all_errors:
        print(f"\n  Total errors ({len(all_errors)}):")
        for e in all_errors:
            print(f"    - {e}")

    return 1 if all_errors else 0


def _print_extension_summary(stats: dict) -> None:
    """Print the extension classifier's counts and mapping rows.

    Args:
        stats: The dict returned by ``Layer0Populator.populate_extension_classifications``.
    """
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== concept_mapping extension_classifier{dry} ===")
    print(f"  FSDS quarters:              {stats['fsds_quarters']}")
    print(f"  ingested accessions:        {stats['accessions_ingested']}")
    print(f"  of them in FSDS:            {stats['accessions_in_fsds']}")
    print(f"  filings with statements:    {stats['filings']}")
    print(f"  extensions (subjects):      {stats['subjects']}")
    print(f"  classified:                 {stats['classified']}")
    print(f"  agreements with hand list:  {stats['agreements']}")
    print(f"  disagreements:              {stats['disagreements']}")
    print(f"  unclassified by reason:     {stats['unclassified']}")
    print(f"  mapping rows:               {len(stats['rows'])}")
    for r in stats["rows"]:
        print(
            f"    {r['cik_override']} {r['concept_family']} {r['qname']} "
            f"{r['concept_role']} rank {r['preference_rank']}"
            + (f" ({r['substitution_code']})" if r["substitution_code"] else "")
        )
    if not stats["dry_run"]:
        print(f"  rows deleted (previous):    {stats['rows_deleted']}")
    if stats.get("report_path"):
        print(f"  report:                     {stats['report_path']}")


def _print_ns_expanded_summary(stats: dict) -> None:
    """Print the ``ns_expanded`` re-derivation stats.

    Args:
        stats: The dict returned by ``Layer0Populator.sync_ns_expanded``.
    """
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== concept_mapping ns_expanded sync{dry} ===")
    print(f"  ns_expanded rows before:    {stats['ns_expanded_before']}")
    print(f"  drifted before:             {stats['drifted_before']}")
    if stats["dry_run"]:
        print(f"  would insert:               {stats['ns_expanded_would_insert']}")
    else:
        print(f"  deleted:                    {stats['ns_expanded_deleted']}")
        print(f"  inserted:                   {stats['ns_expanded_inserted']}")
        print(f"  checked in sync:            {stats['ns_expanded_checked']}")


def _print_geography_summary(stats: dict) -> None:
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== Geography Normalization{dry} ===")
    print(f"  CSV:                        {stats['csv_path']}")
    print(f"  Mode:                       {stats['mode']}")
    print(f"  Rows parsed:                {stats['rows_parsed']}")
    print(f"  Rows OK:                    {stats['rows_ok']}")
    print(f"  Rows skipped (errors):      {stats['rows_skipped']}")
    print(f"  Rows skipped (PENDING):     {stats['rows_pending']}")
    if not stats["dry_run"]:
        print(f"  Rows written to DB:         {stats['rows_written']}")
    if stats["errors"]:
        print(f"  Errors ({len(stats['errors'])}):")
        for e in stats["errors"]:
            print(f"    - {e}")


def _print_override_summary(stats: dict) -> None:
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== Concept Mapping Overrides{dry} ===")
    print(f"  Targets checked:           {stats['targets_checked']}")
    print(f"  Overrides {'(would insert)' if stats['dry_run'] else 'inserted'}:{stats['overrides_inserted']:>3d}")
    print(f"  Already optimal:           {stats['already_optimal']}")
    print(f"  No facts (concept absent): {stats['no_facts_found']}")
    print(f"  Reviewed (kept as is):     {stats.get('reviewed_kept', 0)}")
    overrides = [d for d in stats["detail"] if d["action"] == "override"]
    if overrides:
        print(f"\n  Override decisions:")
        for d in overrides:
            print(
                f"    {d['concept_family']:<36s} | CIK={d['cik']} FY={d['fiscal_year']}"
            )
            print(
                f"      rank 1:  {d['winner_qname']!r} (global rank {d['winner_rank']})"
            )
            print(f"      over:    {d['rank1_qname']!r} (global rank 1)")


def _print_dimension_members_summary(stats: dict) -> None:
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== Dimension Member Labels{dry} ===")
    print(f"  Accessions found:          {stats['accessions_found']}")
    print(f"  Arelle succeeded:          {stats['arelle_ok']}")
    print(f"  Arelle failed:             {stats['arelle_failed']}")
    print(f"  Axis+member pairs exported:{stats['pairs_exported']}")
    if not stats["dry_run"]:
        print(f"  dimension_catalog rows patched: {stats['rows_updated']}")
    if stats["warnings"]:
        print(f"  Warnings ({len(stats['warnings'])}):")
        for w in stats["warnings"]:
            print(f"    ! {w}")


def _print_concept_labels_summary(stats: dict) -> None:
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== Concept Labels Population{dry} ===")
    print(f"  Accessions found:          {stats['accessions_found']}")
    print(f"  Arelle succeeded:          {stats['arelle_ok']}")
    print(f"  Arelle failed:             {stats['arelle_failed']}")
    print(f"  Label rows exported:       {stats['rows_exported']}")
    if not stats["dry_run"]:
        print(f"  Rows written to DB:        {stats['rows_written']}")
    if stats["warnings"]:
        print(f"  Warnings ({len(stats['warnings'])}):")
        for w in stats["warnings"]:
            print(f"    ! {w}")


def _print_patch_summary(stats: dict) -> None:
    dry = " (DRY RUN)" if stats["dry_run"] else ""
    print(f"\n=== Accession Number Patch{dry} ===")
    print(f"  Manifests processed:       {stats['manifests_processed']}")
    print(f"  Updated:                   {stats['updated']}")
    print(f"  Already correct:           {stats['already_correct']}")
    print(f"  No match (not ingested?):  {stats['no_match']}")
    print(f"  Multiple matches (ambig):  {stats['multiple_matches']}")
    print(f"  Skipped (no stem):         {stats['skipped_no_stem']}")
    if stats["warnings"]:
        print(f"  Warnings ({len(stats['warnings'])}):")
        for w in stats["warnings"]:
            print(f"    ! {w}")


def _print_arelle_summary(stats, dry_run: bool) -> None:
    dry = " (DRY RUN)" if dry_run else ""
    print(f"\n=== Arelle XBRL Ingest{dry} ===")
    print(f"  Discovered:              {stats.total_discovered}")
    print(f"  Skipped (duplicate):     {stats.skipped_duplicate}")
    print(f"  Skipped (no manifest):   {stats.skipped_no_manifest}")
    print(f"  Skipped (unsupported):   {stats.skipped_unsupported}")
    if dry_run:
        print(f"  Would ingest:            {stats.dry_run_would_ingest}")
    else:
        print(f"  Attempted:               {stats.attempted}")
        print(f"  Succeeded:               {stats.succeeded}")
        print(f"  Failed:                  {stats.failed}")
    if stats.errors:
        print(f"  Errors ({len(stats.errors)}):")
        for e in stats.errors:
            print(f"    - {e}")


def _print_registry_summary(stats: dict, dry_run: bool) -> None:
    print("\n=== Registry Population ===")
    print(f"  Manifests found:           {stats['manifests_found']}")
    print(f"  Manifests parsed OK:        {stats['manifests_ok']}")
    print(f"  Manifests skipped (errors): {stats['manifests_skipped']}")
    if not dry_run:
        print(f"  filing_registry upserted:  {stats['filing_registry_upserted']}")
        print(f"  fiscal_calendar upserted:  {stats['fiscal_calendar_upserted']}")
        print(f"  fiscal_calendar stale:     {stats['fiscal_calendar_stale_deleted']}")
        print(f"  superseded_by resolved:    {stats['superseded_updated']}")


def _print_concept_mapping_summary(stats: dict) -> None:
    print("\n=== Concept Mapping Population ===")
    print(f"  CSV:                        {stats['csv_path']}")
    print(f"  Mode:                       {stats['mode']}")
    print(f"  Rows parsed:                {stats['rows_parsed']}")
    print(f"  Rows OK:                    {stats['rows_ok']}")
    print(f"  Rows skipped (errors):      {stats['rows_skipped']}")
    if not stats["dry_run"]:
        print(f"  Rows written to DB:         {stats['rows_written']}")


if __name__ == "__main__":
    sys.exit(main())
