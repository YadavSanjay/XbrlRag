"""The annual filings on disk, and the guard that keeps held-out filers out of the tool.

Filings are found by their ``manifest.yaml`` under the raw SEC root
(``{TICKER}/{FY}/{FORM}/manifest.yaml``, written by the downloader). Only annual forms are
indexed: 10-K, 20-F and 40-F, and their amendments. Every ticker of the v2 held-out pool
(``docs/eval/heldout_v2/heldout_v2_pool.json``, which contains the drawn filers) is refused,
whatever is on disk: the tool never lists, reads or serves a held-out filer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src import PROJECT_ROOT

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
POOL_FILE = Path(PROJECT_ROOT) / "docs" / "eval" / "heldout_v2" / "heldout_v2_pool.json"
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
# The filer's own fiscal-year label, as tagged on the cover page.
_DEI_FY_RE = re.compile(
    rb'name="dei:DocumentFiscalYearFocus"[^>]*>(?:\s*<[^>]+>)*\s*(\d{4})')


def dei_fiscal_year(document: Path) -> int | None:
    """The ``dei:DocumentFiscalYearFocus`` a filing's cover page tags, or None.

    This is the filer's own label of the year (HD's year ending January 2024 is its
    fiscal 2023), which the fact store and the evaluation keys use too; a download
    folder's year can differ from it.
    """
    try:
        match = _DEI_FY_RE.search(document.read_bytes())
    except OSError:
        return None
    return int(match.group(1)) if match else None


def valid_ticker(ticker: str) -> bool:
    """Whether ``ticker`` has the shape of a ticker (used before any path is built)."""
    return bool(_TICKER_RE.fullmatch(ticker))


def heldout_tickers(pool_file: Path = POOL_FILE) -> frozenset[str]:
    """The tickers the tool refuses: every candidate of the held-out pool.

    Args:
        pool_file: The pool record.

    Returns:
        The upper-cased symbols. A missing pool file refuses nothing extra, since
        held-out filers are never ingested into the development storage anyway.
    """
    if not pool_file.exists():
        return frozenset()
    doc = json.loads(pool_file.read_text(encoding="utf-8"))
    return frozenset(str(c["symbol"]).upper() for c in doc.get("candidates", []))


@dataclass(frozen=True)
class Filing:
    """One annual filing on disk.

    Attributes:
        ticker: The filer's ticker.
        cik: The filer's CIK (no leading zeros).
        fiscal_year: The fiscal-year label the evaluation uses: the fact store's
            ``filing_registry`` label when known, else the cover page's DEI
            ``DocumentFiscalYearFocus``, else the download folder's year.
        folder_year: The download folder's year (the manifest's ``fiscal_year``).
        dei_year: The cover page's ``DocumentFiscalYearFocus`` (None when untagged).
        label_source: ``registry``, ``dei`` or ``folder``: where ``fiscal_year`` came
            from.
        form: The form type (``10-K``, ``10-K/A``, ``20-F`` ...).
        accession: The dashed accession number.
        filing_date: ISO filing date.
        period_start: ISO start of the fiscal year the filing reports.
        period_end: ISO end of that fiscal year.
        raw_dir: The folder holding the raw documents.
        primary_document: The primary iXBRL document's file name.
        ixbrl_documents: Every inline document of the filing, primary first.
        canonical_path: The canonical HTML (for sections and text search), if present.
    """

    ticker: str
    cik: int
    fiscal_year: int
    folder_year: int
    dei_year: int | None
    label_source: str
    form: str
    accession: str
    filing_date: str
    period_start: str
    period_end: str
    raw_dir: Path
    primary_document: str
    ixbrl_documents: tuple[str, ...] = field(default=())
    canonical_path: Path | None = None

    @property
    def key(self) -> str:
        """A URL-safe id of the filing: the accession without dashes."""
        return self.accession.replace("-", "")

    @property
    def is_amendment(self) -> bool:
        """Whether the form is an amendment (``/A``)."""
        return self.form.endswith("/A")

    def sec_viewer_url(self, document: str | None = None) -> str:
        """The SEC inline viewer link of a document of this filing."""
        doc = document or self.primary_document
        return (f"https://www.sec.gov/ix?doc=/Archives/edgar/data/{self.cik}/"
                f"{self.key}/{doc}")

    def sec_index_url(self) -> str:
        """The SEC filing index page."""
        return (f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{self.key}/"
                f"{self.accession}-index.htm")


class Corpus:
    """The annual filings under the raw SEC root, held-out filers excluded."""

    def __init__(self, raw_root: Path, normalized_root: Path | None,
                 refused: frozenset[str],
                 registry_labels: dict[str, int] | None = None) -> None:
        """Indexes every annual filing's manifest.

        Args:
            raw_root: The raw SEC root (``SEC_EDGAR_ROOT``).
            normalized_root: The normalized root holding ``canonical.htm`` files.
            refused: Tickers never indexed (the held-out pool).
            registry_labels: ``{dashed accession: fiscal_year}`` from the fact store's
                ``filing_registry``; None when the database is not reachable.
        """
        self.raw_root = raw_root
        self.normalized_root = normalized_root
        self.refused = refused
        self.registry_labels = registry_labels or {}
        self.filings: list[Filing] = []
        self.skipped_refused = 0
        self._by_key: dict[str, Filing] = {}
        self._index()

    def _index(self) -> None:
        if not self.raw_root.is_dir():
            return
        for manifest in sorted(self.raw_root.glob("*/*/*/manifest.yaml")):
            ticker_dir = manifest.parents[2].name.upper()
            if ticker_dir in self.refused:
                self.skipped_refused += 1
                continue
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            form = str(doc.get("form") or "")
            if form not in ANNUAL_FORMS:
                continue
            ticker = str(doc.get("ticker") or ticker_dir).upper()
            if ticker in self.refused or not valid_ticker(ticker):
                continue
            raw_dir = manifest.parent
            canonical = None
            if self.normalized_root is not None:
                rel = raw_dir.relative_to(self.raw_root)
                candidate = self.normalized_root / rel / "canonical.htm"
                canonical = candidate if candidate.exists() else None
            primary = str(doc.get("primary_document") or "")
            docs = tuple(str(d) for d in (doc.get("ixbrl_documents") or [primary]) if d)
            folder_year = int(doc["fiscal_year"])
            accession = str(doc["accession_no"])
            dei_year = dei_fiscal_year(raw_dir / primary)
            if accession in self.registry_labels:
                year, source = self.registry_labels[accession], "registry"
            elif dei_year is not None:
                year, source = dei_year, "dei"
            else:
                year, source = folder_year, "folder"
            filing = Filing(
                ticker=ticker,
                cik=int(doc["cik"]),
                fiscal_year=year,
                folder_year=folder_year,
                dei_year=dei_year,
                label_source=source,
                form=form,
                accession=accession,
                filing_date=str(doc.get("filing_date") or ""),
                period_start=str(doc.get("period_start") or ""),
                period_end=str(doc.get("period_end") or doc.get("period_of_report") or ""),
                raw_dir=raw_dir,
                primary_document=primary,
                ixbrl_documents=docs or (primary,),
                canonical_path=canonical,
            )
            self.filings.append(filing)
            self._by_key[filing.key] = filing
        self.filings.sort(key=lambda f: (f.ticker, f.fiscal_year, f.filing_date))

    def tickers(self) -> list[str]:
        """Every indexed ticker, sorted."""
        return sorted({f.ticker for f in self.filings})

    def years(self) -> list[int]:
        """Every indexed fiscal year, sorted."""
        return sorted({f.fiscal_year for f in self.filings})

    def by_key(self, key: str) -> Filing | None:
        """The filing with accession ``key`` (dashes removed), or None."""
        return self._by_key.get(key.replace("-", ""))

    def for_ticker(self, ticker: str) -> list[Filing]:
        """The filer's annual filings, oldest first."""
        return [f for f in self.filings if f.ticker == ticker]

    def own_filing(self, ticker: str, fiscal_year: int) -> Filing | None:
        """The year's own original annual report (not an amendment), else an amendment."""
        own = [f for f in self.filings if f.ticker == ticker and f.fiscal_year == fiscal_year]
        originals = [f for f in own if not f.is_amendment]
        pool = originals or own
        return max(pool, key=lambda f: f.filing_date) if pool else None
