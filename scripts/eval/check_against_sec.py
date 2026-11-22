"""Check the eval suite's expected values against the SEC's companyfacts XBRL data.

External test v1 (docs/specs/sec-companyfacts-check.md). Every lookup, compute
and screen case is checked against ``data.sec.gov/api/xbrl/companyfacts`` — the SEC's
own extraction of the same filings — on concept, unit, period and accession. Dimensional
and breakdown cases are out of companyfacts' reach; a seeded sample of them is written to
a hand-check list instead.

    # the whole suite (one SEC request per filer, cached under data/sec_companyfacts/)
    conda run -n py312 python scripts/eval/check_against_sec.py

    # another suite, output folder or seed
    conda run -n py312 python scripts/eval/check_against_sec.py \
        --suite test/eval/suites/sql_derived_v1.json --out-dir data/eval/sec_check --seed 7

Needs the local Postgres ``xbrl_db`` (read-only) and, for any filer not yet cached, an SEC
identity (``SEC_EDGAR_IDENTITY`` or ``--identity``). Costs $0: no LLM call.

Writes ``report.json`` (every case check, the summary, the hand-check selection and the
run's provenance), ``report.md`` (a table per kind, then every non-match case) and
``hand_check.md`` (the selected cases with their filings' EDGAR index URLs).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.fingerprint import git_sha  # noqa: E402
from src.eval.sec_check.base import CompanyFactsSource  # noqa: E402
from src.eval.sec_check.core import (  # noqa: E402
    CaseCheck,
    check_case,
    select_hand_checks,
    summarize,
)
from src.eval.sec_check.pg_store import PostgresFactStore  # noqa: E402
from src.eval.sec_check.report import (  # noqa: E402
    console_summary,
    render_hand_check,
    render_markdown,
)
from src.eval.sec_check.sec_client import SecCompanyFactsClient  # noqa: E402
from src.ingest.sec_edgar.downloader.sec_filing_downloader import (  # noqa: E402
    resolve_sec_identity,
)
from src.logging_config import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

DEFAULT_SUITE = REPO_ROOT / "test" / "eval" / "suites" / "sql_derived_v1.json"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "eval" / "sec_check"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "sec_companyfacts"
DEFAULT_SEED = 20261021


class _MemoSource(CompanyFactsSource):
    """Keeps each filer's parsed document in memory across cases."""

    def __init__(self, inner: CompanyFactsSource) -> None:
        self._inner = inner
        self._docs: dict[str, dict] = {}

    def company_facts(self, cik: str) -> dict:
        key = str(cik).zfill(10)
        if key not in self._docs:
            self._docs[key] = self._inner.company_facts(key)
        return self._docs[key]


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def run(suite_path: Path, out_dir: Path, seed: int, cache_dir: Path,
        identity: str | None) -> dict:
    """Checks every case of a suite and writes the three report files.

    Args:
        suite_path: The suite JSON.
        out_dir: Where ``report.json``, ``report.md`` and ``hand_check.md`` go.
        seed: The hand-check selection seed.
        cache_dir: The companyfacts cache directory.
        identity: The SEC ``User-Agent``; ``SEC_EDGAR_IDENTITY`` when None.

    Returns:
        The JSON report.
    """
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases: list[dict] = suite["cases"]
    cited = sorted({int(i) for c in cases for i in c.get("expected_fact_ids") or []})

    with PostgresFactStore() as store:
        records = store.facts(cited)  # one query; check_case then reads the store's cache
        logger.info("sec_check: %d cases, %d cited facts (%d found)", len(cases),
                    len(cited), len(records))
        # An identity is needed only when some filer must be fetched.
        ciks = {r["cik"] for r in records.values() if not r["dimensional"]}
        if any(not (cache_dir / f"CIK{cik}.json").exists() for cik in ciks):
            identity = resolve_sec_identity(identity)
        source = _MemoSource(SecCompanyFactsClient(identity or "", cache_dir))
        checks: list[CaseCheck] = [check_case(case, store, source) for case in cases]

    hand_checks = select_hand_checks(cases, seed)
    summary = summarize(checks)
    report = {
        "suite_path": _rel(suite_path),
        "corpus_fingerprint": suite.get("corpus_fingerprint"),
        "git_sha": git_sha(),
        "run_date": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "summary": summary,
        "hand_checks": hand_checks,
        "case_checks": checks,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str),
                                         encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "hand_check.md").write_text(render_hand_check(hand_checks, records),
                                           encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, runs the check and prints a one-screen summary.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.

    Returns:
        The exit code.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--suite", type=Path, default=DEFAULT_SUITE,
                   help="suite JSON (default: test/eval/suites/sql_derived_v1.json)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help="output folder (default: data/eval/sec_check/)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"hand-check selection seed (default: {DEFAULT_SEED})")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                   help="companyfacts cache folder (default: data/sec_companyfacts/)")
    p.add_argument("--identity", default=None,
                   help="SEC User-Agent 'Name email' (default: SEC_EDGAR_IDENTITY)")
    args = p.parse_args(argv)
    configure_logging()

    report = run(args.suite, args.out_dir, args.seed, args.cache_dir, args.identity)
    print(f"SEC companyfacts check — {report['suite_path']} "
          f"(corpus {report['corpus_fingerprint']}, git {report['git_sha']})")
    print(console_summary(report["summary"]))
    print(f"hand checks: {len(report['hand_checks'])}")
    print(f"wrote {_rel(args.out_dir)}/report.json, report.md, hand_check.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
