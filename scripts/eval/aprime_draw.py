"""Build the candidate pool for held-out set A', then draw its filers from a NIST pulse.

External test v1 (docs/specs/aprime-draw.md). ``pool`` runs before
pre-registration v1, and its output is committed and hashed there. ``draw`` runs after the
pulse minute that v1 states, and its output goes into pre-registration v2. Both read EDGAR
filing indexes only, never financial data, and cost $0.

    # 1. the pool: a saved S&P 100 list (symbol,name,sector) and where it came from
    conda run -n py312 python scripts/eval/aprime_draw.py pool \
        --constituents docs/eval/external_test_v1/sealed/aprime_constituents.csv \
        --source "<URL and retrieval time>" --out docs/eval/external_test_v1/sealed/aprime_pool.json

    # 2. the draw, at or after the pre-registered pulse minute
    conda run -n py312 python scripts/eval/aprime_draw.py draw \
        --pool docs/eval/external_test_v1/sealed/aprime_pool.json --pulse-time 2026-11-02T15:00Z \
        --out aprime_draw.json

``pool`` reads the corpus CIKs from the local Postgres ``xbrl_db`` (read-only). Both steps
need an SEC identity (``SEC_EDGAR_IDENTITY`` or ``--identity``) unless every document is
already in the cache folder.

The v2 held-out draw (docs/specs/heldout-draw-v2.md) has its own two subcommands. They
exclude Utilities too, the development waves and left-outs listed in
``configs/eval/v2/heldout_exclusions.yaml``, rank under the ``xbrlrag-heldout-v2|`` prefix
and draw 4 filers and 2 alternates per stratum:

    conda run -n py312 python scripts/eval/aprime_draw.py pool-v2 \
        --constituents <saved S&P 100 CSV> --source "<URL and retrieval time>" \
        --out heldout_v2_pool.json
    conda run -n py312 python scripts/eval/aprime_draw.py draw-v2 \
        --pool heldout_v2_pool.json --seed-from-pool --out heldout_v2_draw.json

``--seed-from-pool`` seeds the ranking with the SHA-512 of the committed pool file (no
free parameter, no wait); ``--pulse-time <minute, UTC>`` uses a NIST beacon pulse instead.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.aprime.beacon_source import NistBeaconSource  # noqa: E402
from src.eval.aprime.core import (  # noqa: E402
    build_pool,
    check_pulse,
    cik_map,
    draw,
    load_constituents,
)
from src.eval.aprime.edgar_source import EdgarSubmissionsSource  # noqa: E402
from src.eval.aprime.v2 import (  # noqa: E402
    SCHEME_V2,
    build_pool_v2,
    draw_v2,
    load_exclusion_lists,
)
from src.eval.set_b.core import canonical_bytes, sha256_hex  # noqa: E402
from src.ingest.sec_edgar.downloader.sec_filing_downloader import (  # noqa: E402
    resolve_sec_identity,
)
from src.logging_config import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "eval" / "aprime" / "edgar_cache"
DEFAULT_EXCLUSIONS = REPO_ROOT / "configs" / "eval" / "v2" / "heldout_exclusions.yaml"

_CORPUS_SQL = """
SELECT cik FROM entity_master
UNION
SELECT cik FROM publish_manifest
"""
_STATEMENT_TIMEOUT_MS = 60_000
_CONNECT_TIMEOUT_S = 10


def corpus_ciks() -> list[str]:
    """Returns every CIK in the development corpus, 10-digit, sorted (read-only).

    Returns:
        The union of ``entity_master.cik`` and ``publish_manifest.cik``.
    """
    from src.db.conn import connect

    conn = connect(connect_timeout=_CONNECT_TIMEOUT_S,
                   options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}")
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(_CORPUS_SQL)
            return sorted(str(int(r[0])).zfill(10) for r in cur.fetchall())
    finally:
        conn.close()


def _write(path: Path, payload: dict) -> str:
    """Writes canonical JSON and returns its SHA-256."""
    data = canonical_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_hex(data)


def cmd_pool(args: argparse.Namespace) -> int:
    """Builds the pool file and prints its hash.

    Args:
        args: The parsed ``pool`` arguments.

    Returns:
        The exit code.
    """
    constituents_bytes = args.constituents.read_bytes()
    constituents = load_constituents(constituents_bytes.decode("utf-8"))
    if args.tickers_json is not None:
        tickers_bytes = args.tickers_json.read_bytes()
    else:
        source = EdgarSubmissionsSource(resolve_sec_identity(args.identity), args.cache_dir)
        tickers_bytes = source.company_tickers()
    corpus = corpus_ciks()

    pool = build_pool(constituents, cik_map(json.loads(tickers_bytes)), set(corpus))
    payload = {
        **pool,
        "source": args.source,
        "constituents_sha256": sha256_hex(constituents_bytes),
        "tickers_sha256": sha256_hex(tickers_bytes),
        "corpus_ciks": corpus,
    }
    digest = _write(args.out, payload)
    counts = pool["counts"]
    print(f"constituents {counts['constituents']}: S {counts['S']}, D {counts['D']}, "
          f"excluded {counts['excluded']}")
    for reason in ("sector", "corpus", "prospective", "duplicate_cik"):
        symbols = [e["symbol"] for e in pool["excluded"] if e["reason"] == reason]
        print(f"  excluded ({reason}): {', '.join(symbols) or '-'}")
    print(f"{digest}  {args.out.name}")
    return 0


def cmd_draw(args: argparse.Namespace) -> int:
    """Fetches the pulse, draws both strata, writes the draw file and prints its hash.

    Args:
        args: The parsed ``draw`` arguments.

    Returns:
        The exit code.
    """
    requested = dt.datetime.fromisoformat(args.pulse_time)
    if requested.tzinfo is None or requested > dt.datetime.now(dt.UTC):
        print(f"refusing: pulse time {args.pulse_time} is not a UTC time in the past",
              file=sys.stderr)
        return 2
    pool_bytes = args.pool.read_bytes()
    pool = json.loads(pool_bytes)

    pulse = NistBeaconSource().pulse(args.pulse_time)
    check_pulse(pulse, args.pulse_time)
    source = EdgarSubmissionsSource(resolve_sec_identity(args.identity), args.cache_dir)
    result = draw(pool, pulse, source)

    payload = {**result, "pulse": pulse, "pool_sha256": sha256_hex(pool_bytes)}
    digest = _write(args.out, payload)
    for stratum, entry in result["strata"].items():
        drawn = ", ".join(e["symbol"] for e in entry["drawn"])
        alts = ", ".join(e["symbol"] for e in entry["alternates"])
        print(f"{stratum}: drawn {drawn or '-'}; alternates {alts or '-'}; "
              f"checked {len(entry['checked'])}; shortfall {entry['shortfall']}")
    print(f"{digest}  {args.out.name}")
    return 0


def cmd_pool_v2(args: argparse.Namespace) -> int:
    """Builds the v2 held-out pool file, prints its warnings and its hash.

    Args:
        args: The parsed ``pool-v2`` arguments.

    Returns:
        The exit code.
    """
    constituents_bytes = args.constituents.read_bytes()
    constituents = load_constituents(constituents_bytes.decode("utf-8"))
    exclusions_bytes = args.exclusions.read_bytes()
    lists = load_exclusion_lists(str(args.exclusions))
    if args.tickers_json is not None:
        tickers_bytes = args.tickers_json.read_bytes()
    else:
        source = EdgarSubmissionsSource(resolve_sec_identity(args.identity), args.cache_dir)
        tickers_bytes = source.company_tickers()
    corpus = corpus_ciks()

    pool = build_pool_v2(constituents, cik_map(json.loads(tickers_bytes)), set(corpus), lists)
    payload = {
        **pool,
        "scheme": SCHEME_V2,
        "source": args.source,
        "constituents_sha256": sha256_hex(constituents_bytes),
        "tickers_sha256": sha256_hex(tickers_bytes),
        "exclusions_sha256": sha256_hex(exclusions_bytes),
        "corpus_ciks": corpus,
    }
    digest = _write(args.out, payload)
    counts = pool["counts"]
    print(f"constituents {counts['constituents']}: S {counts['S']}, D {counts['D']}, "
          f"excluded {counts['excluded']}, warnings {counts['warnings']}")
    for reason in ("sector", "corpus", "wave1", "wave2", "left_out", "duplicate_cik"):
        symbols = [e["symbol"] for e in pool["excluded"] if e["reason"] == reason]
        print(f"  excluded ({reason}): {', '.join(symbols) or '-'}")
    for warning in pool["warnings"]:
        print(f"  warning: {warning['list']} symbol {warning['symbol']} is "
              f"{warning['reason'].replace('_', ' ')}")
    print(f"{digest}  {args.out.name}")
    return 0


POOL_SEED_TIMESTAMP = "seed-from-pool"


def pool_seed(pool_bytes: bytes) -> dict:
    """A pulse-shaped seed taken from the committed pool file itself.

    The seed is the SHA-512 of the pool file's bytes: 128 hex digits, the shape of a beacon
    ``outputValue``, so the locked ranking (``rank_key_v2``) applies unchanged. Nobody
    chooses it: the pool is built mechanically from committed lists before any draw.

    Args:
        pool_bytes: The pool file as committed.

    Returns:
        ``{"outputValue": <upper-case hex>, "timeStamp": "seed-from-pool"}``.
    """
    return {"outputValue": hashlib.sha512(pool_bytes).hexdigest().upper(),
            "timeStamp": POOL_SEED_TIMESTAMP}


def cmd_draw_v2(args: argparse.Namespace) -> int:
    """Fetches the pulse, draws the v2 held-out filers, writes the draw file and its hash.

    Args:
        args: The parsed ``draw-v2`` arguments.

    Returns:
        The exit code: 2 for a future or naive pulse time or a pool of another scheme.
    """
    if args.pulse_time is not None:
        requested = dt.datetime.fromisoformat(args.pulse_time)
        if requested.tzinfo is None or requested > dt.datetime.now(dt.UTC):
            print(f"refusing: pulse time {args.pulse_time} is not a UTC time in the past",
                  file=sys.stderr)
            return 2
    pool_bytes = args.pool.read_bytes()
    pool = json.loads(pool_bytes)
    if not isinstance(pool, dict) or pool.get("scheme") != SCHEME_V2:
        scheme = pool.get("scheme") if isinstance(pool, dict) else None
        print(f"refusing: pool scheme {scheme!r} is not {SCHEME_V2!r}", file=sys.stderr)
        return 2

    if args.pulse_time is not None:
        pulse = NistBeaconSource().pulse(args.pulse_time)
        check_pulse(pulse, args.pulse_time)
        seed_record = {"pulse": pulse}
    else:
        pulse = pool_seed(pool_bytes)
        seed_record = {"seed": {"source": "sha512 of the pool file's bytes",
                                "value": pulse["outputValue"]}}
    source = EdgarSubmissionsSource(resolve_sec_identity(args.identity), args.cache_dir)
    result = draw_v2(pool, pulse, source)

    payload = {**result, "scheme": SCHEME_V2, **seed_record,
               "pool_sha256": sha256_hex(pool_bytes)}
    digest = _write(args.out, payload)
    for stratum, entry in result["strata"].items():
        drawn = ", ".join(e["symbol"] for e in entry["drawn"])
        alts = ", ".join(e["symbol"] for e in entry["alternates"])
        print(f"{stratum}: drawn {drawn or '-'}; alternates {alts or '-'}; "
              f"checked {len(entry['checked'])}; shortfall {entry['shortfall']}")
    print(f"{digest}  {args.out.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments and runs one subcommand.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        The exit code.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--identity", default=None,
                        help="SEC User-Agent 'Name email' (default: SEC_EDGAR_IDENTITY)")
        sp.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help="EDGAR document cache (default: data/eval/aprime/edgar_cache/)")

    sp = sub.add_parser("pool", help="build the candidate pool")
    sp.add_argument("--constituents", type=Path, required=True,
                    help="CSV with columns symbol,name,sector (GICS)")
    sp.add_argument("--source", required=True, help="where the list came from, and when")
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--tickers-json", type=Path, default=None,
                    help="a saved company_tickers.json instead of fetching it")
    common(sp)

    sp = sub.add_parser("draw", help="draw the filers from a NIST beacon pulse")
    sp.add_argument("--pool", type=Path, required=True)
    sp.add_argument("--pulse-time", required=True, help="the pre-registered minute, UTC")
    sp.add_argument("--out", type=Path, required=True)
    common(sp)

    sp = sub.add_parser("pool-v2", help="build the v2 held-out candidate pool")
    sp.add_argument("--constituents", type=Path, required=True,
                    help="CSV with columns symbol,name,sector (GICS)")
    sp.add_argument("--source", required=True, help="where the list came from, and when")
    sp.add_argument("--out", type=Path, required=True)
    sp.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS,
                    help="wave/left-out lists (default: configs/eval/v2/heldout_exclusions.yaml)")
    sp.add_argument("--tickers-json", type=Path, default=None,
                    help="a saved company_tickers.json instead of fetching it")
    common(sp)

    sp = sub.add_parser("draw-v2", help="draw the v2 held-out filers")
    sp.add_argument("--pool", type=Path, required=True)
    seed = sp.add_mutually_exclusive_group(required=True)
    seed.add_argument("--pulse-time", help="a pre-registered NIST beacon minute, UTC")
    seed.add_argument("--seed-from-pool", action="store_true",
                      help="seed the ranking with the SHA-512 of the pool file")
    sp.add_argument("--out", type=Path, required=True)
    common(sp)

    args = p.parse_args(argv)
    configure_logging()
    commands = {"pool": cmd_pool, "draw": cmd_draw, "pool-v2": cmd_pool_v2,
                "draw-v2": cmd_draw_v2}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
