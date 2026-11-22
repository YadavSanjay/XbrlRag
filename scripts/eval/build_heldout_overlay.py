"""Write the lexicon overlay of the held-out filers from the database and the public draw.

Spec: docs/specs/hf-suite-build.md, requirement 3. Reads ``entity_master`` and
``filing_registry`` read-only from the database the environment selects (the held-out
profile ``heldout.ps1`` selects ``xbrl_heldout``), builds the overlay (companies and period
ends) of the drawn filers of the draw file, and writes it to ``--out``, which must lie
outside the repository:

    . <heldout-root>\\heldout.ps1
    conda run -n py312 python scripts/eval/build_heldout_overlay.py \
        --draw docs/eval/heldout_v2/heldout_v2_draw.json --out <ho>/overlay.yaml

**Self-check.** The same rule is applied to every filer of the committed lexicon the database
holds; each committed period end must be derived, with the same date (the period-end column
choice). On any mismatch nothing is written and it exits 1. A fiscal year the database holds
beyond the committed ones is counted, not a mismatch.

It prints counts only (drawn filers, filers written, filers left out, period ends written,
self-check filers and mismatches): never a ticker, name or date. Costs $0: no LLM call.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.heldout_build import (  # noqa: E402
    drawn_filers,
    lexicon_overlay,
    require_outside,
    write_lexicon_overlay,
)
from src.eval.suites.intents import Lexicon, load_lexicon  # noqa: E402
from src.eval.suites.v2_sources import connect_readonly  # noqa: E402

DEFAULT_LEXICON = REPO_ROOT / "configs" / "eval" / "v2" / "lexicon.yaml"
ENTITY_SQL = "SELECT cik, company_name, trading_symbol FROM entity_master"
FILING_SQL = ("SELECT cik, accession_no, form_type, fiscal_year, filed_date, period_end "
              "FROM filing_registry")


def self_check(lexicon: Lexicon, entities: Sequence[Sequence[Any]],
               filings: Sequence[Sequence[Any]]) -> tuple[int, int, int]:
    """Re-derives the committed lexicon's period ends from the database rows.

    Args:
        lexicon: The committed lexicon.
        entities: ``(cik, company_name, trading_symbol)`` rows.
        filings: ``(cik, accession, form_type, fiscal_year, filed, period_end)`` rows.

    A filer mismatches when a committed period end is not derived or differs; a fiscal year
    the database holds beyond the committed ones (a later filing ingested after the lexicon
    was written) is counted in the third number, not as a mismatch: it tests no committed
    value.

    Returns:
        ``(filers checked, filers with a mismatch, derived years the lexicon lacks)``.
    """
    pseudo = [{"cik": str(cik).strip().zfill(10), "symbol": str(symbol),
               "name": lexicon.companies[str(symbol).strip().upper()]["short"]}
              for cik, _name, symbol in entities
              if symbol is not None and str(symbol).strip().upper() in lexicon.companies]
    derived = lexicon_overlay(sorted(pseudo, key=lambda f: f["cik"]), entities,
                              filings)["period_ends"]
    checked = {str(f["symbol"]).strip().upper() for f in pseudo}
    mismatches = extra = 0
    for ticker in sorted(checked):
        committed = {y: d.isoformat() for y, d in lexicon.period_ends.get(ticker, {}).items()}
        found = derived.get(ticker, {})
        if any(found.get(y) != end for y, end in committed.items()):
            mismatches += 1
        extra += len(set(found) - set(committed))
    return len(checked), mismatches, extra


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, builds and writes the overlay, and prints counts.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 written, 1 self-check failed, 2 refused.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--draw", type=Path, required=True,
                   help="the draw file (docs/eval/heldout_v2/heldout_v2_draw.json)")
    p.add_argument("--out", type=Path, required=True,
                   help="the overlay YAML to write (outside the repository)")
    p.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON,
                   help="the committed lexicon (default: configs/eval/v2/lexicon.yaml)")
    args = p.parse_args(argv)
    try:
        out = require_outside(args.out, REPO_ROOT)
    except ValueError:
        print("refused: --out must lie outside the repository", file=sys.stderr)
        return 2

    drawn = drawn_filers(json.loads(args.draw.read_text(encoding="utf-8")))
    lexicon = load_lexicon(args.lexicon)
    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute(ENTITY_SQL)
            entities = [tuple(r) for r in cur.fetchall()]
            cur.execute(FILING_SQL)
            filings = [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()

    overlay = lexicon_overlay(drawn, entities, filings)
    checked, mismatches, extra = self_check(lexicon, entities, filings)
    n_written = len(overlay["companies"])
    n_ends = sum(len(v) for v in overlay["period_ends"].values())
    print(f"drawn filers {len(drawn)}; filers written {n_written}; filers left out "
          f"{len(drawn) - n_written}; period ends written {n_ends}")
    print(f"self-check: filers {checked}; mismatches {mismatches}; years beyond the "
          f"committed lexicon {extra}")
    if mismatches:
        print("self-check failed: nothing written", file=sys.stderr)
        return 1
    write_lexicon_overlay(overlay, out)
    print("overlay written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
