"""Select the 60 main-suite cases for the long_context arm (pre-registration v1).

The rule, fixed before any full long_context run:

1. Candidates are the ``lookup`` and ``compute`` cases of the committed main suite.
2. A filing is one ``(ticker, fy)`` pair. It is eligible when it has at least 2 lookup
   cases and at least 1 compute case.
3. Every ordering uses the key ``sha256(SEED + "|" + text)``, hex, ascending.
4. Filings: filers are ordered by key(ticker), and each filer's eligible filings by
   key(ticker|fy). Round-robin over the filers, taking each filer's next filing per pass,
   until 20 filings are chosen.
5. Cases: from each chosen filing, the 2 lookups and the 1 compute with the lowest
   key(case id).

Reads only the suite file; no database, network or clock. Writes the case ids as
canonical JSON and prints its SHA-256.

    conda run -n py312 python docs/eval/blog_v1/long_context_select.py \
        --suite test/eval/suites/sql_derived_v1.json \
        --out docs/eval/blog_v1/long_context_cases.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

SEED = "xbrlrag-longctx-v1"
N_FILINGS = 20
PER_FILING = {"lookup": 2, "compute": 1}


def key(text: str) -> str:
    """The ordering key of one string."""
    return hashlib.sha256(f"{SEED}|{text}".encode()).hexdigest()


def select(suite: dict) -> dict:
    """Applies the rule in the module docstring to a suite document."""
    by_filing: dict[tuple[str, int], dict[str, list[str]]] = defaultdict(
        lambda: {"lookup": [], "compute": []})
    for case in suite["cases"]:
        if case["kind"] in PER_FILING:
            by_filing[(case["ticker"], int(case["fy"]))][case["kind"]].append(case["id"])
    eligible = {f: kinds for f, kinds in by_filing.items()
                if all(len(kinds[k]) >= n for k, n in PER_FILING.items())}

    per_filer: dict[str, list[int]] = defaultdict(list)
    for ticker, fy in eligible:
        per_filer[ticker].append(fy)
    filers = sorted(per_filer, key=key)
    for ticker in filers:
        per_filer[ticker].sort(key=lambda fy, t=ticker: key(f"{t}|{fy}"))

    chosen: list[tuple[str, int]] = []
    depth = 0
    while len(chosen) < N_FILINGS and any(len(v) > depth for v in per_filer.values()):
        for ticker in filers:
            if len(chosen) < N_FILINGS and len(per_filer[ticker]) > depth:
                chosen.append((ticker, per_filer[ticker][depth]))
        depth += 1

    filings = []
    for ticker, fy in chosen:
        kinds = eligible[(ticker, fy)]
        ids = [cid for k, n in PER_FILING.items() for cid in sorted(kinds[k], key=key)[:n]]
        filings.append({"ticker": ticker, "fy": fy, "case_ids": ids})
    return {
        "rule": "docs/eval/blog_v1/long_context_select.py",
        "seed": SEED,
        "suite": {"name": suite.get("name"), "version": suite.get("version"),
                  "corpus_fingerprint": suite.get("corpus_fingerprint")},
        "eligible_filings": len(eligible),
        "filings": filings,
        "case_ids": [cid for f in filings for cid in f["case_ids"]],
    }


def main() -> int:
    """Selects the cases, writes them and prints the SHA-256 of the output."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--suite", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    suite_bytes = args.suite.read_bytes()
    result = select(json.loads(suite_bytes))
    result["suite"]["sha256"] = hashlib.sha256(suite_bytes).hexdigest()
    data = (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    args.out.write_bytes(data)
    print(f"{len(result['case_ids'])} cases from {len(result['filings'])} filings "
          f"({result['eligible_filings']} eligible)")
    print(f"{hashlib.sha256(data).hexdigest()}  {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
