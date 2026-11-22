"""CLI entry point for the SEC filing downloader.

Examples::

    # Batch download from the default YAML config.
    python -m src.ingest.sec_edgar.downloader

    # Use a non-default config file.
    python -m src.ingest.sec_edgar.downloader --config configs/pipelines/sec_edgar_downloads.yaml

    # Single ad-hoc download.
    python -m src.ingest.sec_edgar.downloader --ticker XOM --filing-type 10-K --fiscal-year 2024
    python -m src.ingest.sec_edgar.downloader --ticker AMZN --filing-type 10-Q --fiscal-year 2024 --quarter 2
    python -m src.ingest.sec_edgar.downloader --ticker TSM --filing-type 20-F --fiscal-year 2024
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from src.ingest.sec_edgar.downloader.sec_filing_downloader import SECFilingDownloader
from src.ingest.sec_edgar.storage import local_root

DEFAULT_CONFIG = Path("configs/pipelines/sec_edgar_downloads.yaml")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download SEC 10-K / 10-Q / 20-F filings (XBRL / iXBRL)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to a YAML config describing filings to download.",
    )
    parser.add_argument("--ticker", help="Single-filing mode: company ticker.")
    parser.add_argument(
        "--filing-type",
        choices=["10-K", "10-Q", "20-F"],
        help="Single-filing mode: filing type (20-F for FPI annual reports, e.g. TSM).",
    )
    parser.add_argument(
        "--fiscal-year",
        type=int,
        help="Single-filing mode: fiscal year, the calendar year it ends in (e.g. 2024).",
    )
    parser.add_argument(
        "--quarter",
        type=int,
        choices=[1, 2, 3],
        help="Fiscal quarter for 10-Q filings (1, 2, or 3) of --fiscal-year.",
    )
    parser.add_argument(
        "--output-root",
        default=local_root("sec_edgar"),
        help="Override the output root directory.",
    )
    parser.add_argument(
        "--identity",
        default=None,
        help="EDGAR User-Agent identity (Name email@domain); default: SEC_EDGAR_IDENTITY.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.ticker:
        if not args.filing_type or not args.fiscal_year:
            parser.error("--ticker requires --filing-type and --fiscal-year")
        dl = SECFilingDownloader(output_root=args.output_root, identity=args.identity)
        dl.download(
            ticker=args.ticker,
            filing_type=args.filing_type,
            fiscal_year=args.fiscal_year,
            quarter=args.quarter,
        )
        return 0

    if not args.config.exists():
        parser.error(f"Config not found: {args.config}")
    dl = SECFilingDownloader.from_config(args.config, identity=args.identity)
    dl.download_from_config(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
