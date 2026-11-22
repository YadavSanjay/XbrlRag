"""EDGAR check for the prospective set (blog v1, pre-registration v0).

For each candidate filer, prints its fiscal year-end, its latest 10-K and the filing dates
of its last four 10-Ks, so a reader can confirm that no FY2026 10-K existed at the check
time. Reads EDGAR filing indexes only (the submissions API), never financial data.

Usage:
    python docs/eval/blog_v1/edgar_check.py [REPO_ROOT]

``SEC_EDGAR_IDENTITY`` ("Name email@domain") must be set, in the environment or in
``REPO_ROOT/.env``; SEC requires a contactable User-Agent.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.request
from typing import Any

from dotenv import load_dotenv

CANDIDATES = ["AAPL", "MU", "AVGO", "QCOM", "V", "DIS", "SBUX", "EMR", "ACN", "COST", "DE"]
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_PAUSE_S = 0.2  # stays well under SEC's 10 requests/second limit


def _get_json(url: str, identity: str) -> Any:
    """Fetches one EDGAR JSON document.

    Args:
        url: The EDGAR URL.
        identity: The User-Agent identity SEC requires.

    Returns:
        The parsed JSON.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": identity, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    time.sleep(_PAUSE_S)
    return data


def main(argv: list[str]) -> int:
    """Prints the check table for every candidate.

    Args:
        argv: Command-line arguments; ``argv[1]``, if given, is the repo root holding ``.env``.

    Returns:
        The process exit code.
    """
    if len(argv) > 1:
        load_dotenv(os.path.join(argv[1], ".env"))
    else:
        load_dotenv()
    identity = (os.environ.get("SEC_EDGAR_IDENTITY") or "").strip()
    if not identity:
        print("SEC_EDGAR_IDENTITY is not set; cannot query EDGAR.", file=sys.stderr)
        return 1

    tickers = _get_json(_TICKERS_URL, identity)
    cik_of = {v["ticker"]: int(v["cik_str"]) for v in tickers.values()}
    print(f"checked_at_utc={dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}")
    print(
        f"{'tkr':5} {'cik':>8} {'FYE':>5} {'latest 10-K period':>18} {'filed':>11}"
        "  past 10-K filed dates (period->filed)"
    )
    for ticker in CANDIDATES:
        cik = cik_of.get(ticker)
        if not cik:
            print(f"{ticker:5} not found")
            continue
        sub = _get_json(_SUBMISSIONS_URL.format(cik=cik), identity)
        rec = sub["filings"]["recent"]
        tenks = sorted(
            (
                (rec["reportDate"][i], rec["filingDate"][i])
                for i in range(len(rec["form"]))
                if rec["form"][i] == "10-K"
            ),
            reverse=True,
        )
        period, filed = tenks[0] if tenks else ("-", "-")
        history = ", ".join(f"{p}->{f}" for p, f in tenks[:4])
        print(
            f"{ticker:5} {cik:>8} {sub.get('fiscalYearEnd', '?'):>5} {period:>18}"
            f" {filed:>11}  {history}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
