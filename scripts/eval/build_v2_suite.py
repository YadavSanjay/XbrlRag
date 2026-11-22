"""Build the v2 dev and validation suites from the corpus, SEC keys and the phrasebook.

Phase 1 of the v2 plan (docs/specs/v2-suite-build.md). Reads the corpus inventory from the
local ``xbrl_db`` (read-only), keys every candidate intent from SEC companyfacts through the
live, cached ``SecSource``, cross-checks each key against the fact store, phrases and splits
the keyed intents, and writes ``xbrl_v2_dev.json``, ``xbrl_v2_validation.json``,
``xbrl_v2_manifest.json`` and ``intents.json`` into ``--out-dir``.

    # offline: every companyfacts payload must already be in the SEC cache
    conda run -n py312 python scripts/eval/build_v2_suite.py --total 600 --per-intent 3 \
        --share 0.25 --salt v2-2026-10 --sec-cache-only

    # fetch what is missing (needs SEC_EDGAR_IDENTITY or --identity)
    conda run -n py312 python scripts/eval/build_v2_suite.py --total 600 --per-intent 3 \
        --share 0.25 --salt v2-2026-10

Quotas per shape are ``shape_quotas(--total)`` (the plan's 35/15/10 dev mix). The
universes are ``covered`` (every inventory filer the lexicon names) plus the named groups
of ``--groups`` (``configs/eval/v2/groups.yaml``, read by ``load_universe_groups``; each
id must be in the lexicon).
Only filers and metrics the lexicon names are enumerated; the rest are reported and left
out. The SEC cache lives under ``_sec_api/`` of the ``sec_edgar`` FilingStore. Costs $0:
no LLM call.

Every key input and screen member is witnessed against the filer's own statement (SEC's
Financial Statement Data Sets, ``--fsds-dir``; docs/specs/key-statement-witness.md) with
the committed ``configs/eval/v2/statement_lines.yaml``, ``note_concepts.yaml`` and
``key_resolutions.yaml``. A key the statement disagrees with and no reviewed resolution
covers fails the build, naming each blocked key; the counts land in the manifest's
``key_independence``. Before any key is selected, ``key_resolutions.yaml`` is checked
against the pinned original list ``key_resolutions.locked.yaml``: an original entry may only
be removed, and any other entry needs a complete review record by a reviewer of
``configs/eval/v2/reviewers.yaml`` (docs/specs/reviewed-resolutions.md); an unreviewed
addition fails the build. After the witness, every resolution must cover a blocked key the
build kept (a stale or speculative one fails the build); the counts land in
``key_resolutions``.

With ``--compound N`` (N > 0) it also builds the compound suites (a top-3 ranking with a
dependent ask; docs/specs/compound-shape.md) from the same sources with the phrasebook of
``--compound-phrasebook``, ``N`` intents split over ``compound.rank_by_ratio`` (the odd one)
and ``compound.leader_growth``, and writes ``xbrl_v2_dev_compound.json``,
``xbrl_v2_validation_compound.json``, ``xbrl_v2_compound_manifest.json`` and
``intents_compound.json`` beside the main four. The compound build runs after the main one
with the same statement witness: a compound candidate meeting a key the witness blocks (and
no reviewed resolution covers) is left out as ``witness_blocked`` instead of failing the
build, and adds nothing to the main build's block report; the compound manifest's
``key_independence`` and ``key_resolutions`` count both builds' witnessed keys. With
``--compound 0`` (the default) the outputs are the main four only, unchanged.

**Held-out mode** (``--split heldout_filers``; docs/specs/hf-suite-build.md) builds H-F, the
sealed held-out-filer suite, blind, from the database the environment selects (the held-out
profile ``heldout.ps1`` selects ``xbrl_heldout``):

    . <heldout-root>\\heldout.ps1
    conda run -n py312 python scripts/eval/build_v2_suite.py --split heldout_filers \
        --total 130 --per-intent 3 --share 0 --compound 17 --salt xbrlrag-v2-hf-1 \
        --groups configs/eval/v2/groups.yaml --sec-cache-only \
        --lexicon-overlay <ho>/overlay.yaml --heldout-tags <ho>/sectors_heldout.yaml \
        --fsds-dir <ho>/fsds --out-dir <ho>/suites --log-dir <ho>/logs

``--lexicon-overlay``, ``--heldout-tags`` and ``--out-dir`` are required and must lie outside
the repository, ``--share`` must be 0, and the active log file must lie outside the repository
(``--log-dir`` or ``XBRLRAG_LOG_DIR``); otherwise it exits 2 before any database read. The
lexicon is the committed one merged with the overlay, whose companies are the held-out filers;
``covered`` is every filer the merged lexicon names; the named groups apply the committed
definitions to the committed and the held-out sector tags. Only intents that involve a
held-out filer are built, a witness block leaves its candidate out (``witness_blocked``), and
it writes ``heldout_hf*.json`` (never an ``xbrl_v2_*`` file). The console and the log show
counts and hashes only: never a ticker, key, value or question; an error is shown by its type,
its traceback goes to the log file.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.heldout_build import (  # noqa: E402
    build_heldout_compound_suites,
    build_heldout_suites,
    heldout_groups,
    heldout_summary,
    merge_lexicon_overlay,
    require_outside,
    write_heldout_compound_suites,
    write_heldout_suites,
)
from src.eval.suites.intents import (  # noqa: E402
    check_lexicon_phrasings,
    load_lexicon,
    load_phrasebook,
)
from src.eval.suites.v2_sources import (  # noqa: E402
    PostgresFactStoreSource,
    RefusingTransport,
    connect_readonly,
    read_candidate_concepts,
    read_corpus_inventory,
    restrict_to_lexicon,
)
from src.eval.suites.v2_suite import (  # noqa: E402
    COMPOUND_SHAPES,
    build_compound_suites,
    build_v2_suites,
    shape_quotas,
    write_compound_suites,
    write_v2_suites,
)
from src.eval.suites.v2_witness import KeyWitness  # noqa: E402
from src.logging_config import configure_logging, current_log_file, get_logger  # noqa: E402
from src.retriever.xbrl.entity_sectors import (  # noqa: E402
    load_sector_tags,
    load_universe_groups,
)

logger = get_logger(__name__)

DEFAULT_LEXICON = REPO_ROOT / "configs" / "eval" / "v2" / "lexicon.yaml"
DEFAULT_PHRASEBOOK = REPO_ROOT / "configs" / "eval" / "v2" / "phrasebook.yaml"
DEFAULT_COMPOUND_PHRASEBOOK = REPO_ROOT / "configs" / "eval" / "v2" / "phrasebook_compound.yaml"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "eval" / "v2_suites"
DEFAULT_FSDS_DIR = REPO_ROOT / "data" / "cache" / "sec_fsds" / "filtered"
COVERED = "covered"
HELDOUT_SPLIT = "heldout_filers"
# Stands in for the EDGAR identity when no request can be made.
_CACHE_ONLY_IDENTITY = "XbrlRag v2-suite-build cache-only (no requests)"


def load_groups(path: Path | None, lexicon_universes: Any) -> dict[str, list[str]]:
    """Reads the named universe groups.

    Args:
        path: A groups file in the ``configs/eval/v2/groups.yaml`` format (read through
            ``load_universe_groups``), or None for no groups.
        lexicon_universes: The lexicon's universes; every group id must be one of them.

    Returns:
        universe id -> tickers.

    Raises:
        ValueError: The file is malformed (``load_universe_groups``; it also refuses
            ``covered``), or names a universe the lexicon cannot phrase.
    """
    if path is None:
        return {}
    groups: dict[str, list[str]] = load_universe_groups(path)["groups"]
    for gid in groups:
        if gid not in lexicon_universes:
            raise ValueError(f"{path}: universe {gid!r} is not in the lexicon")
    return {gid: list(members) for gid, members in groups.items()}


def fsds_quarters(root: Path) -> list[Path]:
    """The FSDS quarter directories under ``root`` (each holding ``sub.tsv``).

    Args:
        root: The filtered FSDS cache, one ``<yyyy>q<n>`` directory per quarter.

    Returns:
        The quarter directories, sorted.

    Raises:
        FileNotFoundError: ``root`` holds no quarter.
    """
    dirs = []
    if root.is_dir():
        dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "sub.tsv").is_file())
    if not dirs:
        raise FileNotFoundError(f"{root}: no FSDS quarter directories (the statement witness "
                                "needs them; see docs/specs/key-statement-witness.md)")
    return dirs


def compound_quotas(total: int) -> dict[str, int]:
    """Splits ``total`` compound intents over the two compound shapes, as evenly as possible.

    Args:
        total: The number of compound intents, an int >= 0.

    Returns:
        ``{shape: quota}``; ``compound.rank_by_ratio`` takes the odd one.

    Raises:
        ValueError: ``total`` is negative.
    """
    if total < 0:
        raise ValueError(f"--compound must be >= 0, got {total}")
    rank_by_ratio, leader_growth = COMPOUND_SHAPES
    return {rank_by_ratio: (total + 1) // 2, leader_growth: total // 2}


def _print_build(build: dict[str, Any]) -> None:
    """Prints a build's per-shape selection and per-split case counts."""
    manifest = build["manifest"]
    intents = manifest["intents"]
    for shape, quota in intents["quota"].items():
        dropped = intents["dropped_by_shape"].get(shape, {})
        print(f"  {shape:20s} quota {quota:4d}  selected {intents['selected'][shape]:4d}  "
              f"candidates {intents['candidates'][shape]:5d}  dropped {dropped}")
    for split in ("dev", "validation"):
        counts = manifest["cases"][split]
        print(f"{split}: {counts['total']} cases {counts['by_key_status']} "
              f"{manifest['content_hashes'][split]}")
    print(f"disagreements: {len(manifest['disagreements'])}")


def _sec_source(cache_only: bool, identity: str | None) -> Any:
    """The live SEC source; with ``cache_only`` its transport refuses every request."""
    from src.eval.keys.sec_live import LiveSecSource
    from src.ingest.sec_edgar.storage import FilingStore

    store = FilingStore.from_env(role="sec_edgar")
    if cache_only:
        return LiveSecSource(store, transport=RefusingTransport(),
                             identity=identity or _CACHE_ONLY_IDENTITY)
    return LiveSecSource(store, identity=identity)


def heldout_preflight(args: argparse.Namespace, repo_root: Path,
                      log_file: Path | None) -> list[str]:
    """The reasons a held-out build may not start (docs/specs/hf-suite-build.md, req. 12).

    Checked before any database read: the overlay, the held-out tags and the output
    directory are given and lie outside the repository, the share is 0, and the active log
    file lies outside the repository. The reasons name flags, never a path's content.

    Args:
        args: The parsed arguments.
        repo_root: The repository's root.
        log_file: The active log file (``current_log_file()``), or None.

    Returns:
        The reasons; empty when the build may start.
    """
    problems = []
    for flag, value in (("--lexicon-overlay", args.lexicon_overlay),
                        ("--heldout-tags", args.heldout_tags), ("--out-dir", args.out_dir)):
        if value is None:
            problems.append(f"{flag} is required with --split {HELDOUT_SPLIT}")
            continue
        try:
            require_outside(value, repo_root)
        except ValueError:
            problems.append(f"{flag} must lie outside the repository")
    if args.share != 0:
        problems.append(f"--share must be 0 with --split {HELDOUT_SPLIT}")
    if log_file is None:
        problems.append("no log file is configured (a held-out build logs into the held-out "
                        "tree: pass --log-dir or set XBRLRAG_LOG_DIR)")
    else:
        try:
            require_outside(log_file, repo_root)
        except ValueError:
            problems.append("the active log file lies inside the repository (pass --log-dir "
                            "or set XBRLRAG_LOG_DIR)")
    return problems


def heldout_lexicon_and_groups(args: argparse.Namespace) -> tuple[Any, list[str],
                                                                   dict[str, list[str]]]:
    """The merged lexicon, the held-out tickers and the named groups of a held-out build.

    Args:
        args: The parsed arguments (``--lexicon``, ``--lexicon-overlay``, ``--groups``,
            ``--heldout-tags``).

    Returns:
        ``(lexicon, held-out tickers sorted, universe id -> tickers)``.

    Raises:
        ValueError: A malformed overlay, tags or groups file, stale committed groups, or a
            group the lexicon cannot phrase.
    """
    base = load_lexicon(args.lexicon)
    check_lexicon_phrasings(base)  # the overlay adds filers only, never a phrasing
    lexicon = merge_lexicon_overlay(base, args.lexicon_overlay)
    heldout = sorted(set(lexicon.companies) - set(base.companies))
    heldout_tags = load_sector_tags(args.heldout_tags)
    groups: dict[str, list[str]] = {}
    if args.groups is not None:
        doc = load_universe_groups(args.groups)
        dev_tags = load_sector_tags(REPO_ROOT / doc["sectors_file"])
        groups = heldout_groups(doc, dev_tags, heldout_tags)
        for gid in groups:
            if gid not in lexicon.universes:
                raise ValueError(f"{args.groups}: universe {gid!r} is not in the lexicon")
    return lexicon, heldout, groups


def _log_traceback_to_file(exc: BaseException) -> None:
    """Writes an error's traceback to the file handlers only, never to the console."""
    record = logger.makeRecord(logger.name, logging.ERROR, __file__, 0, "held-out build failed",
                               (), (type(exc), exc, exc.__traceback__))
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.handle(record)


def _run_heldout(args: argparse.Namespace) -> int:
    """Runs a held-out build, reporting an error on the console by its type only."""
    # A fetch logs its URL, which names a CIK: keep it out of the console and the log.
    logging.getLogger("src.eval.keys.sec_live").setLevel(logging.CRITICAL)
    try:
        return _heldout_build(args)
    except Exception as exc:  # isolation point: an error's message may name a filer
        _log_traceback_to_file(exc)
        print(f"held-out build failed: {type(exc).__name__} (the traceback is in the log file)",
              file=sys.stderr)
        return 1


def _heldout_build(args: argparse.Namespace) -> int:
    """Builds and writes H-F (docs/specs/hf-suite-build.md, requirement 12); counts only."""
    lexicon, heldout, groups = heldout_lexicon_and_groups(args)
    phrasebook = load_phrasebook(args.phrasebook)
    quotas = shape_quotas(args.total)
    quarters = fsds_quarters(args.fsds_dir)
    compound_phrasebook = load_phrasebook(args.compound_phrasebook) if args.compound else None

    conn = connect_readonly()
    try:
        inventory, left_out = restrict_to_lexicon(read_corpus_inventory(conn), lexicon)
        if left_out["tickers"]:
            logger.warning("held-out build: %d filer(s) not in the lexicon left out",
                           len(left_out["tickers"]))
        if left_out["metrics"]:
            logger.info("held-out build: %d metric(s) not in the lexicon left out",
                        len(left_out["metrics"]))
        universes = {COVERED: sorted(inventory), **groups}
        witness = KeyWitness.from_paths(quarters, candidates=read_candidate_concepts(conn))
        build = build_heldout_suites(
            inventory, universes, _sec_source(args.sec_cache_only, args.identity),
            PostgresFactStoreSource(conn), phrasebook, lexicon, heldout=heldout,
            quotas=quotas, per_intent=args.per_intent, salt=args.salt, witness=witness)
        compound = None
        if compound_phrasebook is not None:
            compound = build_heldout_compound_suites(
                inventory, universes, _sec_source(args.sec_cache_only, args.identity),
                PostgresFactStoreSource(conn), compound_phrasebook, lexicon, heldout=heldout,
                quotas=compound_quotas(args.compound), per_intent=args.per_intent,
                salt=args.salt, witness=witness)
    finally:
        conn.close()

    out_dir = require_outside(args.out_dir, REPO_ROOT)
    print(f"filers {len(inventory)}; universes {len(universes)}")
    for line in heldout_summary(build):
        print(line)
    for key, path in write_heldout_suites(build, out_dir).items():
        print(f"wrote {key}: {path}")
    if compound is not None:
        print("compound:")
        for line in heldout_summary(compound):
            print(line)
        for key, path in write_heldout_compound_suites(compound, out_dir).items():
            print(f"wrote compound {key}: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, builds and writes the suites, and prints a summary.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON,
                   help="lexicon YAML (default: configs/eval/v2/lexicon.yaml)")
    p.add_argument("--phrasebook", type=Path, default=DEFAULT_PHRASEBOOK,
                   help="phrasebook YAML (default: configs/eval/v2/phrasebook.yaml)")
    p.add_argument("--total", type=int, required=True,
                   help="keyed intents wanted in all; split over shapes by shape_quotas")
    p.add_argument("--per-intent", type=int, required=True, help="phrasings per intent")
    p.add_argument("--share", type=float, required=True,
                   help="validation share of each split axis, in [0, 1]")
    p.add_argument("--salt", required=True, help="the split salt")
    p.add_argument("--groups", type=Path, default=None,
                   help="named universe groups file (e.g. configs/eval/v2/groups.yaml); "
                        "ids must be in the lexicon")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="where the four files are written (default: data/eval/v2_suites; "
                        "required, outside the repository, with --split heldout_filers)")
    p.add_argument("--sec-cache-only", action="store_true",
                   help="never fetch from the SEC: a payload missing from the cache fails "
                        "the build")
    p.add_argument("--identity", default=None,
                   help="EDGAR User-Agent (default: SEC_EDGAR_IDENTITY)")
    p.add_argument("--fsds-dir", type=Path, default=DEFAULT_FSDS_DIR,
                   help="filtered FSDS cache, one directory per quarter "
                        "(default: data/cache/sec_fsds/filtered)")
    p.add_argument("--compound", type=int, default=0,
                   help="compound intents wanted (docs/specs/compound-shape.md); 0 (the "
                        "default) builds none")
    p.add_argument("--compound-phrasebook", type=Path, default=DEFAULT_COMPOUND_PHRASEBOOK,
                   help="compound phrasebook YAML "
                        "(default: configs/eval/v2/phrasebook_compound.yaml)")
    p.add_argument("--split", choices=("dev", HELDOUT_SPLIT), default="dev",
                   help="dev (the default): the dev and validation suites; heldout_filers: "
                        "H-F, built blind (docs/specs/hf-suite-build.md)")
    p.add_argument("--lexicon-overlay", type=Path, default=None,
                   help="held-out mode: the lexicon overlay of the held-out filers (outside "
                        "the repository)")
    p.add_argument("--heldout-tags", type=Path, default=None,
                   help="held-out mode: the held-out filers' sector tags, in the format of "
                        "configs/entities/sectors.yaml (outside the repository)")
    p.add_argument("--log-dir", type=Path, default=None,
                   help="an absolute directory the log files go to (default: XBRLRAG_LOG_DIR, "
                        "else logs/)")
    args = p.parse_args(argv)
    if args.compound < 0:
        p.error(f"--compound must be >= 0, got {args.compound}")
    if args.log_dir is not None and not args.log_dir.is_absolute():
        p.error("--log-dir must be an absolute directory")
    configure_logging(log_dir=args.log_dir)  # None: XBRLRAG_LOG_DIR, else logs/ as before
    if args.split == HELDOUT_SPLIT:
        problems = heldout_preflight(args, REPO_ROOT, current_log_file())
        if problems:
            for problem in problems:
                print(f"refused: {problem}", file=sys.stderr)
            return 2
        return _run_heldout(args)
    if args.out_dir is None:
        args.out_dir = DEFAULT_OUT_DIR

    lexicon = load_lexicon(args.lexicon)
    check_lexicon_phrasings(lexicon)  # a mistyped phrasing fails the build
    phrasebook = load_phrasebook(args.phrasebook)
    groups = load_groups(args.groups, lexicon.universes)
    quotas = shape_quotas(args.total)
    quarters = fsds_quarters(args.fsds_dir)
    compound_phrasebook = load_phrasebook(args.compound_phrasebook) if args.compound else None

    conn = connect_readonly()
    try:
        inventory, left_out = restrict_to_lexicon(read_corpus_inventory(conn), lexicon)
        if left_out["tickers"]:
            logger.warning("v2 build: filers not in the lexicon left out: %s",
                           left_out["tickers"])
        if left_out["metrics"]:
            logger.info("v2 build: metrics not in the lexicon left out: %s",
                        left_out["metrics"])
        universes = {COVERED: sorted(inventory), **groups}
        witness = KeyWitness.from_paths(quarters, candidates=read_candidate_concepts(conn))
        build = build_v2_suites(
            inventory, universes, _sec_source(args.sec_cache_only, args.identity),
            PostgresFactStoreSource(conn), phrasebook, lexicon, quotas=quotas,
            per_intent=args.per_intent, share=args.share, salt=args.salt, witness=witness)
        compound = None
        if compound_phrasebook is not None:
            compound = build_compound_suites(
                inventory, universes, _sec_source(args.sec_cache_only, args.identity),
                PostgresFactStoreSource(conn), compound_phrasebook, lexicon,
                quotas=compound_quotas(args.compound), per_intent=args.per_intent,
                share=args.share, salt=args.salt, witness=witness)
    finally:
        conn.close()

    paths = write_v2_suites(build, args.out_dir)
    manifest = build["manifest"]
    print(f"filers {len(inventory)}; universes {sorted(universes)}")
    _print_build(build)
    independence = manifest["key_independence"]
    print(f"statement witness: {independence['n']} keys {independence['by_status']}; "
          f"resolved {independence['resolved']}; held {len(independence['held'])}")
    resolutions = manifest["key_resolutions"]
    print(f"key resolutions: {resolutions['n']} ({resolutions['original']} original, "
          f"{resolutions['reviewed']} reviewed) by bucket {resolutions['by_bucket']}")
    coverage = manifest["fsds"]["coverage"]
    print(f"fsds: {coverage['n']} quarters {coverage['first']}..{coverage['last']}, missing "
          f"{coverage['missing'] or 'none'}; not published "
          f"{manifest['fsds']['not_published']['n']}")
    for key, path in paths.items():
        print(f"wrote {key}: {path}")
    if compound is not None:
        print("compound:")
        _print_build(compound)
        for key, path in write_compound_suites(compound, args.out_dir).items():
            print(f"wrote compound {key}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
