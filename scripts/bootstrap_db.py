"""Ordered bootstrap for ``xbrl_db``: schema -> data -> materialized views.

Runs the three phases in the only order that works, **wrapping** the pieces that
already exist (``scripts/ddl/``, the ingest CLI, ``scripts/rebuild_mvs.py``)
rather than duplicating them:

===========  ============================================================
``schema``   ``base`` -> ``extensions`` -> ``layer0`` -> ``audit``
``data``     the guide-4 §4.11 ingest order (facts -> registries ->
             semantic layer -> structure)
``mv``       build from ``scripts/ddl/mv/``, then REFRESH in dependency order
===========  ============================================================

Every step is idempotent and re-runnable **except** ``base``, which opens with
``DROP SCHEMA public CASCADE``. It is therefore opt-in (``--with-base``) and
refuses to run against a database that already holds XbrlRag objects unless
``--force``.

One ordering trap this encodes so you do not have to remember it: ``pg_trgm``
installs into ``public``, so the base DDL's ``DROP SCHEMA public CASCADE`` takes
the extension with it — extensions are (re)created **after** ``base``, never
before.

Usage::

    # See the ordered plan without touching anything
    conda run -n py312 python scripts/bootstrap_db.py --all --plan

    # Fresh instance, end to end
    conda run -n py312 python scripts/bootstrap_db.py --all --with-base \\
        --concept-mapping-csv data/storage/datasets/hifikpi/concept_mapping_depth0.csv \\
        --geography-csv data/storage/datasets/geography_normalization_seed.csv

    # One phase at a time
    conda run -n py312 python scripts/bootstrap_db.py --phase schema --with-base
    conda run -n py312 python scripts/bootstrap_db.py --phase data --ticker NVDA
    conda run -n py312 python scripts/bootstrap_db.py --phase mv

    # Resume after a failure, or run a single step
    conda run -n py312 python scripts/bootstrap_db.py --all --from dom-positions
    conda run -n py312 python scripts/bootstrap_db.py --only mv-refresh

Run from the repo root (needs ``src`` importable). Reads ``XBRL_DB_*`` from
``.env`` via the shared :func:`src.db.conn.connect`.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.db.conn import connect  # noqa: E402  (needs REPO_ROOT on sys.path)

DDL_BASE = os.path.join(REPO_ROOT, "scripts", "ddl", "base", "xbrlPublicPostgresDB.ddl")
DDL_LAYER0_DIR = os.path.join(REPO_ROOT, "scripts", "ddl", "layer_0")
# Layer-0 tables that the DDL creates but NO ingest code ever fills. Each file
# says why it exists; all are idempotent (ON CONFLICT DO UPDATE) so they re-run
# safely after every ingest. Skipping them leaves a database that looks healthy
# stage-by-stage and fails at query time — see the `layer0-populate` step.
DDL_POPULATE_DIR = os.path.join(REPO_ROOT, "scripts", "ddl", "populate")
AUDIT_APPLY = os.path.join(REPO_ROOT, "scripts", "ddl", "audit", "_apply.py")
REBUILD_MVS = os.path.join(REPO_ROOT, "scripts", "rebuild_mvs.py")

# Extensions XbrlRag needs. pg_trgm backs entity_master's GIN trigram index; it
# lives in `public`, so it must be created AFTER the base DDL drops that schema.
EXTENSIONS = ["pg_trgm"]

PHASES = ("schema", "data", "mv")


# --------------------------------------------------------------------------- #
# step model
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    id: str
    phase: str
    title: str
    run: Callable[[], None]
    destructive: bool = False
    # Set when the step cannot run as configured (e.g. an optional CSV was not
    # supplied). It is listed in the plan and skipped, not failed.
    skip_reason: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _exec_sql(sql: str) -> None:
    conn = connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


def _exec_sql_file(path: str) -> None:
    # utf-8-sig: the vendored Arelle DDL carries a BOM, which psycopg2 would
    # otherwise send as a leading ﻿ and Postgres would reject.
    with open(path, encoding="utf-8-sig") as fh:
        _exec_sql(fh.read())


def _run(cmd: list[str], label: str) -> None:
    print(f"    $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"ERROR: {label} failed (exit {proc.returncode})")


def _run_module(module: str, *cli_args: str) -> None:
    _run([sys.executable, "-m", module, *cli_args], module)


def _run_script(path: str, *cli_args: str) -> None:
    _run([sys.executable, path, *cli_args], os.path.basename(path))


def _scalar(sql: str):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _names(sql: str) -> list[str]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# step bodies
# --------------------------------------------------------------------------- #
def _apply_base(force: bool) -> None:
    """Apply the vendored Arelle public-schema DDL. DESTRUCTIVE."""
    existing = _names(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('entity_master', 'filing_registry', 'concept_mapping')"
    )
    matviews = _names("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
    if (existing or matviews) and not force:
        raise SystemExit(
            "REFUSING to apply the base DDL: this database already holds XbrlRag objects\n"
            f"  Layer-0 tables : {sorted(existing) or 'none'}\n"
            f"  materialized views: {len(matviews)}\n"
            "The base DDL opens with `DROP SCHEMA public CASCADE` and would destroy "
            "all of it, plus every loaded fact.\n"
            "Use a fresh database, or pass --force if you genuinely mean to wipe this one."
        )
    if not os.path.exists(DDL_BASE):
        raise SystemExit(f"ERROR: base DDL not found at {DDL_BASE}")
    _exec_sql_file(DDL_BASE)


def _apply_extensions() -> None:
    for ext in EXTENSIONS:
        print(f"    CREATE EXTENSION IF NOT EXISTS {ext}")
        _exec_sql(f"CREATE EXTENSION IF NOT EXISTS {ext};")


def _apply_layer0() -> None:
    files = sorted(glob.glob(os.path.join(DDL_LAYER0_DIR, "*.sql")))
    if not files:
        raise SystemExit(f"ERROR: no .sql files in {DDL_LAYER0_DIR}")
    for path in files:
        print(f"    {os.path.basename(path)}")
        _exec_sql_file(path)


def _apply_populate() -> None:
    """Fill the Layer-0 tables no ingest code populates.

    The three files are mutually independent (they read `fact`, `context_dimension`
    and `concept_mapping` respectively), so alphabetical order is fine. What is
    *not* free-order is the step's position in the plan: `dimension_catalog` rows
    must exist before `concept-labels` runs, because DimensionMembersPopulator only
    UPDATEs `member_label WHERE NULL` — against an empty table it patches nothing
    and reports success.
    """
    files = sorted(glob.glob(os.path.join(DDL_POPULATE_DIR, "*.sql")))
    if not files:
        raise SystemExit(f"ERROR: no .sql files in {DDL_POPULATE_DIR}")
    for path in files:
        print(f"    {os.path.basename(path)}")
        _exec_sql_file(path)
    # entity_master.sql leaves company_name / trading_symbol to this module
    # (docs/specs/identity-and-stand-in.md).
    _run_module("src.ingest.sec_edgar.ingest.entity_identity")
    # Last: re-derive every ns_expanded concept_mapping row from its base row (after
    # concept_mapping_ns_expand.sql's ProductRevenue/ServiceRevenue demotion) and fail
    # on drift, in one transaction (docs/specs/ns-expand-rank-sync.md).
    _run_module("src.ingest.sec_edgar.ingest", "--sync-ns-expanded", "--skip-registries")


def _refresh_order() -> list[str]:
    """Materialized views in dependency order (a view after everything it reads).

    Derived from pg_depend at runtime rather than hardcoded, so MVs without
    committed DDL (the Layer-1a set) are ordered correctly too.
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'"
            )
            all_mvs = {r[0] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT DISTINCT dependent.relname, source.relname
                FROM pg_depend d
                JOIN pg_rewrite r      ON r.oid = d.objid
                JOIN pg_class dependent ON dependent.oid = r.ev_class
                JOIN pg_class source    ON source.oid = d.refobjid
                JOIN pg_namespace ns    ON ns.oid = source.relnamespace
                WHERE source.relkind = 'm' AND dependent.relkind = 'm'
                  AND dependent.oid <> source.oid AND ns.nspname = 'public'
                """
            )
            reads: dict[str, set[str]] = {mv: set() for mv in all_mvs}
            for dependent, source in cur.fetchall():
                if dependent in reads and source in all_mvs:
                    reads[dependent].add(source)
    finally:
        conn.close()

    ordered: list[str] = []
    remaining = dict(reads)
    while remaining:
        ready = sorted(
            mv for mv, deps in remaining.items() if not (deps - set(ordered))
        )
        if not ready:  # cycle — impossible for MVs, but never loop forever
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        for mv in ready:
            remaining.pop(mv)
    return ordered


def _refresh_mvs() -> None:
    order = _refresh_order()
    if not order:
        raise SystemExit(
            "ERROR: no materialized views found — run the 'mv-build' step first."
        )
    conn = connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for mv in order:
                t0 = time.time()
                print(f"    REFRESH {mv} ...", end="", flush=True)
                cur.execute(f'REFRESH MATERIALIZED VIEW public."{mv}"')
                print(f" {time.time() - t0:.1f}s")
    finally:
        conn.close()
    print(f"    refreshed {len(order)} materialized views")


def _build_mvs() -> None:
    _run_script(REBUILD_MVS)


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def build_plan(args: argparse.Namespace) -> list[Step]:
    scope: list[str] = []
    if args.ticker:
        scope += ["--ticker", args.ticker]
    if args.year:
        scope += ["--year", args.year]
    ingest_scope = scope + (["--form-type", args.form] if args.form else [])
    html_scope = scope + (["--form", args.form] if args.form else [])

    ingest = "src.ingest.sec_edgar.ingest"

    steps: list[Step] = [
        Step(
            id="base",
            phase="schema",
            title="Arelle public schema (scripts/ddl/base/)",
            run=lambda: _apply_base(args.force),
            destructive=True,
            skip_reason=None if args.with_base else "not requested (pass --with-base)",
            notes=["opens with DROP SCHEMA public CASCADE"],
        ),
        Step(
            id="extensions",
            phase="schema",
            title=f"extensions ({', '.join(EXTENSIONS)})",
            run=_apply_extensions,
            notes=["must run AFTER base — pg_trgm lives in public"],
        ),
        Step(
            id="layer0",
            phase="schema",
            title="XbrlRag Layer-0 tables (scripts/ddl/layer_0/*.sql)",
            run=_apply_layer0,
        ),
        Step(
            id="audit",
            phase="schema",
            title="audit schema (scripts/ddl/audit/_apply.py)",
            run=lambda: _run_script(AUDIT_APPLY),
        ),
        Step(
            id="arelle-ingest",
            phase="data",
            title="XBRL facts via arelleCmdLine",
            run=lambda: _run_module(ingest, "--arelle-ingest", *ingest_scope),
            notes=["needs ARELLE_CMD; skips already-loaded filings"],
        ),
        Step(
            id="patch-accessions",
            phase="data",
            title="patch filing_accession_number",
            run=lambda: _run_module(
                ingest, "--patch-accession-numbers", "--skip-registries"
            ),
            notes=["always after an Arelle ingest"],
        ),
        Step(
            id="registries",
            phase="data",
            title="filing_registry + fiscal_calendar",
            run=lambda: _run_module(ingest),
        ),
        Step(
            id="concept-mapping",
            phase="data",
            title="concept_mapping (semantic layer)",
            run=lambda: _run_module(
                ingest,
                "--concept-mapping-csv",
                args.concept_mapping_csv or "",
                "--concept-mapping-mode",
                args.concept_mapping_mode,
                "--skip-registries",
            ),
            skip_reason=(
                None if args.concept_mapping_csv else "no --concept-mapping-csv given"
            ),
        ),
        Step(
            id="layer0-populate",
            phase="data",
            title="entity_master (+ identity) + dimension_catalog + concept_mapping fixes",
            run=_apply_populate,
            notes=[
                "no ingest code fills these — see scripts/ddl/populate/",
                "MUST precede concept-labels (member_label patch needs the rows)",
            ],
        ),
        Step(
            id="extension-classify",
            phase="data",
            title="concept_mapping extension_classifier rows (FSDS cache + calc arcs)",
            run=lambda: _run_module(
                ingest,
                "--extension-classifications",
                "--fsds-dir",
                args.fsds_dir,
                "--skip-registries",
            ),
            skip_reason=(
                None
                if os.path.isdir(args.fsds_dir)
                else f"no FSDS cache at {args.fsds_dir} (pass --fsds-dir)"
            ),
            notes=[
                "after layer0-populate (reads the stored ranking), before mv-build",
                "reads no MV: one MV build follows (extension-classification R7)",
            ],
        ),
        Step(
            id="concept-labels",
            phase="data",
            title="concept_labels + dimension_catalog.member_label",
            run=lambda: _run_module(
                ingest, "--concept-labels", "--dimension-members", "--skip-registries"
            ),
            notes=["needs ARELLE_CMD"],
        ),
        Step(
            id="geography",
            phase="data",
            title="geography_normalization",
            run=lambda: _run_module(
                ingest,
                "--geography-normalization-csv",
                args.geography_csv or "",
                "--geography-normalization-mode",
                args.geography_mode,
                "--skip-registries",
            ),
            skip_reason=None if args.geography_csv else "no --geography-csv given",
        ),
        Step(
            id="canonical-html",
            phase="data",
            title="canonical.htm (html_canonicalizer)",
            run=lambda: _run_module(
                "src.ingest.sec_edgar.xbrl.html_canonicalizer", *html_scope
            ),
            notes=["pure file transform, no DB writes"],
        ),
        Step(
            id="dom-positions",
            phase="data",
            title="fact_dom_position + section_assignment",
            run=lambda: _run_module("src.ingest.sec_edgar.xbrl.dom_position_populator"),
        ),
        Step(
            id="mv-build",
            phase="mv",
            title="build MVs from scripts/ddl/mv/",
            run=_build_mvs,
            notes=["CREATE ... AS also populates; drops+recreates, so idempotent"],
        ),
        Step(
            id="mv-refresh",
            phase="mv",
            title="REFRESH every MV in dependency order",
            run=_refresh_mvs,
            skip_reason=None if not args.no_refresh else "--no-refresh given",
            notes=["redundant right after mv-build; use for a data-only refresh"],
        ),
    ]
    return steps


def select(steps: list[Step], args: argparse.Namespace) -> list[Step]:
    ids = [s.id for s in steps]

    if args.only:
        unknown = [x for x in args.only if x not in ids]
        if unknown:
            raise SystemExit(f"ERROR: unknown step(s) {unknown}. Known: {ids}")
        chosen = [s for s in steps if s.id in args.only]
    else:
        chosen = list(steps)
        if args.phase:
            chosen = [s for s in chosen if s.phase in args.phase]
        if args.start_from:
            if args.start_from not in ids:
                raise SystemExit(
                    f"ERROR: unknown --from step '{args.start_from}'. Known: {ids}"
                )
            start = ids.index(args.start_from)
            chosen = [s for s in chosen if ids.index(s.id) >= start]
    if args.skip:
        unknown = [x for x in args.skip if x not in ids]
        if unknown:
            raise SystemExit(f"ERROR: unknown --skip step(s) {unknown}. Known: {ids}")
        chosen = [s for s in chosen if s.id not in args.skip]

    # `base` carries its own skip_reason from build_plan() unless --with-base was
    # given, so even `--only base` stays inert without the explicit opt-in.
    return chosen


def print_plan(chosen: list[Step]) -> None:
    print("\nPlan\n" + "=" * 72)
    phase = None
    for i, s in enumerate(chosen, 1):
        if s.phase != phase:
            phase = s.phase
            print(f"\n[{phase}]")
        flag = " (DESTRUCTIVE)" if s.destructive else ""
        state = f"  SKIP — {s.skip_reason}" if s.skip_reason else ""
        print(f"  {i:2}. {s.id:<17} {s.title}{flag}{state}")
        for n in s.notes:
            print(f"      · {n}")
    print()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Bootstrap xbrl_db in order: schema -> data -> materialized views.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sel = p.add_argument_group("selection")
    sel.add_argument(
        "--all", action="store_true", help="Run every phase (default if no selector)."
    )
    sel.add_argument(
        "--phase",
        action="append",
        choices=PHASES,
        help="Run only this phase; repeatable.",
    )
    sel.add_argument(
        "--only", nargs="+", metavar="STEP", help="Run exactly these step ids."
    )
    sel.add_argument(
        "--from", dest="start_from", metavar="STEP", help="Resume from this step id."
    )
    sel.add_argument(
        "--skip", nargs="+", metavar="STEP", default=[], help="Drop these step ids."
    )

    saf = p.add_argument_group("safety")
    saf.add_argument(
        "--plan", action="store_true", help="Print the ordered plan and exit."
    )
    saf.add_argument(
        "--with-base",
        action="store_true",
        help="Include the DESTRUCTIVE base-schema step (DROP SCHEMA public CASCADE).",
    )
    saf.add_argument(
        "--force",
        action="store_true",
        help="Let --with-base run even though the database already holds XbrlRag objects.",
    )

    ing = p.add_argument_group("ingest inputs / scope")
    ing.add_argument("--concept-mapping-csv", default=None, metavar="CSV")
    ing.add_argument(
        "--concept-mapping-mode",
        default="upsert",
        choices=["upsert", "insert_new", "replace_source"],
    )
    ing.add_argument(
        "--fsds-dir",
        default=os.path.join(REPO_ROOT, "data", "cache", "sec_fsds", "filtered"),
        metavar="DIR",
        help="FSDS quarter folders for the extension-classify step.",
    )
    ing.add_argument("--geography-csv", default=None, metavar="CSV")
    ing.add_argument(
        "--geography-mode",
        default="upsert",
        choices=["upsert", "insert_new", "replace_all"],
    )
    ing.add_argument(
        "--ticker", default=None, help="Scope the fact ingest + canonicalizer."
    )
    ing.add_argument("--year", default=None, help="Requires --ticker.")
    ing.add_argument(
        "--form", default=None, help="Requires --ticker and --year (e.g. 10-K)."
    )
    ing.add_argument(
        "--no-refresh", action="store_true", help="Skip the mv-refresh step."
    )

    p.add_argument("--env-file", default=".env", help="Path to .env (default: .env).")
    args = p.parse_args(argv)

    if not (args.all or args.phase or args.only or args.start_from):
        args.all = True

    load_dotenv(args.env_file)

    steps = build_plan(args)
    chosen = select(steps, args)
    if not chosen:
        raise SystemExit("ERROR: selection matched no steps.")

    print_plan(chosen)
    if args.plan:
        return 0

    runnable = [s for s in chosen if not s.skip_reason]
    destructive = [s.id for s in runnable if s.destructive]
    if destructive:
        print(f"!! DESTRUCTIVE steps in this run: {destructive}")
        print("!! The base DDL drops the public schema and everything in it.\n")

    started = time.time()
    for i, step in enumerate(chosen, 1):
        if step.skip_reason:
            print(f"[{i}/{len(chosen)}] {step.id}: SKIP — {step.skip_reason}")
            continue
        print(f"[{i}/{len(chosen)}] {step.id}: {step.title}", flush=True)
        t0 = time.time()
        step.run()
        print(f"    ok ({time.time() - t0:.1f}s)\n", flush=True)

    print(
        f"Bootstrap complete in {time.time() - started:.1f}s "
        f"({len(runnable)} steps run, {len(chosen) - len(runnable)} skipped)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
