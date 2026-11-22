"""Download SEC 10-K / 10-Q / 20-F filings (XBRL / iXBRL) into local storage.

Supported form types:

* ``10-K``  — Annual report (US domestic filers)
* ``10-Q``  — Quarterly report (US domestic filers)
* ``20-F``  — Annual report for Foreign Private Issuers (FPIs), e.g. TSM/TSMC.
              Financially equivalent to a 10-K; uses IFRS taxonomy concepts
              (``ifrs-full:*``) instead of US GAAP (``us-gaap:*``).

Output layout::

    <output_root>/<TICKER>/<FY>/<FILING_TYPE>/<files>            # 10-K / 20-F (original)
    <output_root>/<TICKER>/<FY>/10-K_A/<files>                   # 10-K/A (amendment)
    <output_root>/<TICKER>/<FY>/20-F_A/<files>                   # 20-F/A (amendment)
    <output_root>/<TICKER>/<FY>/<FILING_TYPE>/Q<n>/<files>       # 10-Q (original)
    <output_root>/<TICKER>/<FY>/10-Q_A/Q<n>/<files>              # 10-Q/A (amendment)

CLI accepts ``10-K`` / ``10-Q`` / ``20-F`` as ``filing_type`` regardless of
whether the latest matching filing on EDGAR turns out to be an amendment.
When the most recent filing for a given ticker / fiscal-year is an amendment
(``10-K/A``, ``10-Q/A``, or ``20-F/A``), the downloader:

* saves files under the sanitised amended-form directory (``10-K_A`` / ``20-F_A`` / ``10-Q_A``),
* records ``form: 10-K/A`` (or ``20-F/A`` / ``10-Q/A``) in ``manifest.yaml``, and
* sets ``is_amendment: true`` in ``manifest.yaml``.

The primary iXBRL HTML, any further inline XBRL document, and the XBRL instance /
schema / linkbase files (``.htm``, ``.xml``, ``.xsd``) are saved verbatim, along
with a ``manifest.yaml`` describing the resolved filing. A second inline document
is recognised by its content, because EDGAR can type it as an exhibit
(docs/specs/ingest-multidoc-ixbrl.md).
"""

from __future__ import annotations

import calendar
import logging
import math
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from edgar import Company, configure_http, set_identity

from src.ingest.sec_edgar.document_set import inline_fact_ids
from src.ingest.sec_edgar.fiscal_labels import place_quarter
from src.ingest.sec_edgar.report_period import report_period_bounds
from src.ingest.sec_edgar.storage import (
    FilingStore,
    LocalFilingStore,
    default_local_root,
    local_root,
)
from src.ingest.util.retry import retry_call

logger = logging.getLogger(__name__)

# Forms accepted on the CLI / in config files.
# 20-F is the FPI annual report (IFRS taxonomy); treated as annual like 10-K.
SUPPORTED_FORMS = ("10-K", "10-Q", "20-F")
# Canonical XBRL package filename suffixes: instance + the four linkbases.
_XBRL_LINKBASE_SUFFIXES = ("_htm.xml", "_lab.xml", "_pre.xml", "_def.xml", "_cal.xml")
# DEI concepts we extract from the XBRL instance document for period metadata.
_DEI_DATE_FACTS = frozenset(
    {"DocumentPeriodStartDate", "DocumentPeriodEndDate", "CurrentFiscalYearEndDate"}
)
_HTML_SUFFIXES = (".htm", ".html")
# EDGAR's rendered statement pages; never a document the filer submitted.
_VIEWER_PAGE = re.compile(r"R\d+\.htm")
_EDGAR_READ_TIMEOUT_VAR = "XBRLRAG_EDGAR_READ_TIMEOUT_S"
_EDGAR_READ_TIMEOUT_DEFAULT_S = 120.0


def _amended_form(base_form: str) -> str:
    """Return the amended-filing variant of *base_form* (e.g. ``10-K`` → ``10-K/A``)."""
    return f"{base_form}/A"


def _dir_safe_form(form: str) -> str:
    """Return a filesystem-safe directory name for *form*.

    ``10-K/A`` → ``10-K_A``, ``10-K`` → ``10-K`` (unchanged).
    """
    return form.replace("/", "_")


def choose_filings(candidates: Sequence[Any]) -> list[Any]:
    """Choose which of one period's EDGAR filings to download.

    The latest original, then the latest amendment filed after it, so a Part III-only
    amendment cannot displace the statements it amends
    (docs/specs/annual-amendment-handling.md).

    Args:
        candidates: Filings already matched to the requested period; each has
            ``.form``, ``.filing_date`` and ``.accession_no``.

    Returns:
        The filings to download, ordered by filing date.
    """

    def rank(filing: Any) -> tuple[str, str]:
        filed = getattr(filing, "filing_date", "")
        filed_iso = filed.isoformat() if isinstance(filed, date) else str(filed)
        return filed_iso, str(getattr(filing, "accession_no", ""))

    def is_amendment(filing: Any) -> bool:
        return str(getattr(filing, "form", "")).upper().strip().endswith("/A")

    originals = [f for f in candidates if not is_amendment(f)]
    amendments = [f for f in candidates if is_amendment(f)]
    if not originals:
        return [max(amendments, key=rank)] if amendments else []
    original = max(originals, key=rank)
    later = [a for a in amendments if rank(a)[0] > rank(original)[0]]
    return [original] + ([max(later, key=rank)] if later else [])


@dataclass(frozen=True)
class FilingRequest:
    """A single filing to download.

    ``filing_type`` must be a base form (``10-K``, ``10-Q``, or ``20-F``); the
    downloader automatically considers the amended variant when resolving the
    actual filing.  ``20-F`` is an annual FPI report — it behaves like ``10-K``
    (no ``quarter`` field; period matched by fiscal year).

    ``fiscal_year`` is the calendar year in which the fiscal year ends, for every form. A
    10-Q request's ``quarter`` is the *fiscal* quarter (1-3) of that fiscal year, so NVDA's
    quarter ended April 2026 is ``(2027, 1)`` and Apple's quarter ended December 2025 is
    ``(2026, 1)`` (docs/specs/tenq-fiscal-labels.md, requirement 5).
    """

    ticker: str
    filing_type: str
    fiscal_year: int
    quarter: int | None = (
        None  # required for 10-Q (1..3); must be None for 10-K / 20-F
    )

    # Annual form types: no quarter, period matched by fiscal year only.
    _ANNUAL_FORMS: frozenset = frozenset({"10-K", "20-F"})

    def __post_init__(self) -> None:
        ft = self.filing_type.upper()
        if ft not in SUPPORTED_FORMS:
            raise ValueError(
                f"Unsupported filing_type={self.filing_type!r}; expected one of {SUPPORTED_FORMS}"
            )
        if ft == "10-Q":
            if self.quarter not in (1, 2, 3):
                raise ValueError(
                    f"10-Q request must set quarter in {{1, 2, 3}}; got {self.quarter!r}"
                )
        elif self.quarter is not None:
            raise ValueError(f"{ft} request must not set a quarter.")
        object.__setattr__(self, "ticker", self.ticker.upper())
        object.__setattr__(self, "filing_type", ft)


def is_inline_xbrl_document(payload: bytes | str) -> bool:
    """Whether an HTML attachment carries inline XBRL facts.

    Args:
        payload: The attachment's content.

    Returns:
        True when the document binds the inline XBRL namespace and holds at least one fact
        element. EDGAR's document type plays no part: a second inline document can be typed
        as an exhibit.
    """
    data = payload.encode("utf-8", "replace") if isinstance(payload, str) else payload
    return bool(inline_fact_ids(data))


def filing_date_floor(request: FilingRequest) -> date:
    """Return the earliest date a filing for *request* can have been filed.

    Args:
        request: The period asked for.

    Returns:
        January 1 of the request's fiscal year for an annual form, of the year before for a
        10-Q (a fiscal year ending in January of Y has its first quarter end in April of
        Y - 1). There is no upper bound: an amendment can come years after its original.
    """
    if request.filing_type in FilingRequest._ANNUAL_FORMS:
        return date(request.fiscal_year, 1, 1)
    return date(request.fiscal_year - 1, 1, 1)


def select_filings(company: Any, request: FilingRequest) -> list[Any]:
    """Choose the filings for *request* from the company's filing index.

    Reads only index fields (``form``, ``filing_date``, ``report_date``, ``accession_no``):
    no filing text is fetched until a chosen filing is downloaded. A 10-Q row is placed on
    the company's fiscal calendar, whose year ends are the report dates of its ``10-K`` index
    rows (each labelled with its calendar year); see ``_period_matches``.

    Args:
        company: An edgartools company, or anything with its ``get_filings``.
        request: The period asked for.

    Returns:
        The filings to download, as ``choose_filings`` orders them.

    Raises:
        LookupError: No filing of the base or amended form matches the period.
    """
    base_form = request.filing_type  # e.g. "10-K"
    amended_form = _amended_form(base_form)  # e.g. "10-K/A"
    floor = filing_date_floor(request)
    annual_periods = (
        {} if base_form in FilingRequest._ANNUAL_FORMS else _annual_report_dates(company)
    )

    candidates: list[Any] = []
    for form_to_fetch in (base_form, amended_form):
        filings = company.get_filings(form=form_to_fetch)
        if not filings or len(filings) == 0:
            continue
        for filing in filings:
            filed = _parse_date(getattr(filing, "filing_date", None))
            period = _parse_date(getattr(filing, "report_date", None))
            if filed is None or filed < floor or period is None:
                continue
            if _period_matches(period, request, annual_periods):
                candidates.append(filing)

    if not candidates:
        raise LookupError(
            f"No {base_form} or {amended_form} matched ticker={request.ticker}, "
            f"fiscal_year={request.fiscal_year}, quarter={request.quarter}."
        )
    return choose_filings(candidates)


def edgar_read_timeout_s() -> float:
    """Return the read timeout for EDGAR requests, from ``XBRLRAG_EDGAR_READ_TIMEOUT_S``.

    Returns:
        The configured seconds, or the default of 120 when the variable is unset.

    Raises:
        ValueError: The variable is set to anything but a positive finite number.
    """
    raw = os.environ.get(_EDGAR_READ_TIMEOUT_VAR)
    if raw is None:
        return _EDGAR_READ_TIMEOUT_DEFAULT_S
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(f"{_EDGAR_READ_TIMEOUT_VAR} must be a number; got {raw!r}") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{_EDGAR_READ_TIMEOUT_VAR} must be positive and finite; got {raw!r}")
    return seconds


def resolve_sec_identity(identity: str | None = None) -> str:
    """Return the EDGAR User-Agent identity, failing loudly when none is configured.

    SEC mandates a contactable "Name email@domain" User-Agent and rate-limits and
    blocks per identity, so the repo ships no default: each operator sets their own
    via ``SEC_EDGAR_IDENTITY`` (normally in ``.env``) or ``--identity``.

    Args:
        identity: An explicit identity (``--identity`` or the YAML key); wins over the env.

    Returns:
        The stripped identity string.

    Raises:
        ValueError: If neither ``identity`` nor ``SEC_EDGAR_IDENTITY`` is set.
    """
    value = (identity or os.environ.get("SEC_EDGAR_IDENTITY") or "").strip()
    if not value:
        raise ValueError(
            "No SEC EDGAR identity configured. Set SEC_EDGAR_IDENTITY in .env "
            "(e.g. SEC_EDGAR_IDENTITY=\"Your Name you@example.com\") or pass --identity."
        )
    return value


class SECFilingDownloader:
    """Resolve and persist SEC XBRL/iXBRL filings for a company + period.

    When the most recent filing for a requested period is an amendment
    (``10-K/A`` / ``10-Q/A``), it is downloaded transparently with the
    appropriate directory name and ``is_amendment: true`` in the manifest.
    """

    DEFAULT_OUTPUT_ROOT = default_local_root("sec_edgar")

    # Permanent errors — never worth retrying (bad request / no matching filing).
    _NON_RETRYABLE = (LookupError, ValueError, FileNotFoundError, KeyError)

    def __init__(
        self,
        output_root: str | Path = DEFAULT_OUTPUT_ROOT,
        identity: str | None = None,
        max_attempts: int = 3,
        retry_backoff: float = 2.0,
        store: FilingStore | None = None,
    ) -> None:
        # Storage backend (CL #4): local FS in deploy_mode=local (write in place,
        # unchanged), S3 in aws. Attachments + period metadata are always assembled
        # on a real local dir first (period extraction reads the saved files back);
        # in S3 mode that dir is a temp staging area published per-filing.
        self._store = store or FilingStore.from_env(root=output_root)
        if isinstance(self._store, LocalFilingStore):
            self.output_root = self._store.root  # in-place write == today's behavior
            self._staging = False
        else:
            self.output_root = Path(tempfile.mkdtemp(prefix="xbrlrag_dl_")).resolve()
            self._staging = True
            logger.info("S3 filing store: staging downloads in %s", self.output_root)
        identity = resolve_sec_identity(identity)
        self.identity = identity
        # Bounded exponential-backoff retry for transient EDGAR/network errors
        # (timeouts, connection resets, 5xx/429 throttling). max_attempts=1 disables.
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff = retry_backoff
        # SEC EDGAR mandates a descriptive User-Agent; edgartools forwards this.
        set_identity(identity)
        # edgartools' own read timeout is 30 s, which a stalled EDGAR request can outlast.
        configure_http(timeout=edgar_read_timeout_s())
        logger.info(
            "SECFilingDownloader ready: identity=%r output_root=%s",
            identity,
            self.output_root,
        )

    def _retrying(self, fn: Any, label: str) -> Any:
        """Run *fn* with this downloader's bounded retry/backoff policy."""
        return retry_call(
            fn,
            max_attempts=self.max_attempts,
            base_backoff=self.retry_backoff,
            non_retryable=self._NON_RETRYABLE,
            label=label,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls, config_path: str | Path, identity: str | None = None
    ) -> SECFilingDownloader:
        cfg = _load_yaml(config_path)
        return cls(
            # Env wins over the YAML: the checked-in config carries a local-dev
            # path, and the container must not need it edited. A YAML absolute
            # Windows path is a *relative* path on POSIX, so letting it win writes
            # to /app/D:/... instead of the mounted root.
            output_root=os.environ.get("SEC_EDGAR_ROOT")
            or cfg.get("output_root")
            or local_root("sec_edgar"),
            # --identity > SEC_EDGAR_IDENTITY > the YAML key, which ships blank.
            identity=identity or os.environ.get("SEC_EDGAR_IDENTITY") or cfg.get("identity"),
        )

    @staticmethod
    def parse_requests(config_path: str | Path) -> list[FilingRequest]:
        cfg = _load_yaml(config_path)
        requests: list[FilingRequest] = []
        for entry in cfg.get("downloads", []) or []:
            requests.append(
                FilingRequest(
                    ticker=str(entry["ticker"]),
                    filing_type=str(entry["filing_type"]),
                    fiscal_year=int(entry["fiscal_year"]),
                    quarter=(
                        int(entry["quarter"])
                        if entry.get("quarter") is not None
                        else None
                    ),
                )
            )
        return requests

    def download_from_config(self, config_path: str | Path) -> list[Path]:
        results: list[Path] = []
        for req in self.parse_requests(config_path):
            try:
                results.extend(self.download(req))
            except Exception:
                logger.exception("Failed to download %s", req)
        return results

    def download(
        self,
        request: FilingRequest | None = None,
        *,
        ticker: str | None = None,
        filing_type: str | None = None,
        fiscal_year: int | None = None,
        quarter: int | None = None,
    ) -> list[Path]:
        """Download the period's original filing and its latest later amendment.

        Both are kept so the registry can decide which one is canonical: a Part III-only
        amendment must not displace the statements it amends
        (docs/specs/annual-amendment-handling.md).

        Returns:
            One filing directory per downloaded filing, in filing-date order.
        """
        if request is None:
            if ticker is None or filing_type is None or fiscal_year is None:
                raise ValueError(
                    "Pass a FilingRequest or ticker, filing_type, and fiscal_year keyword args."
                )
            request = FilingRequest(
                ticker=ticker,
                filing_type=filing_type,
                fiscal_year=fiscal_year,
                quarter=quarter,
            )

        filings = self._retrying(
            lambda: self._select_filings(request),
            label=f"EDGAR query {request.ticker} {request.filing_type} FY{request.fiscal_year}",
        )
        return [self._download_filing(request, filing) for filing in filings]

    def _download_filing(self, request: FilingRequest, filing: Any) -> Path:
        """Save one resolved filing's XBRL package and manifest; return its directory."""
        # Determine the *actual* form type from the resolved filing object.
        # It may differ from request.filing_type for an amendment (the request asked
        # for "10-K", this filing is the "10-K/A").
        actual_form: str = (
            (getattr(filing, "form", request.filing_type) or request.filing_type)
            .upper()
            .strip()
        )
        is_amendment: bool = actual_form.endswith("/A")

        target_dir = self._target_dir(request, actual_form)
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = self._save_xbrl_attachments(filing, target_dir, actual_form)

        # Extract period dates + FYE from the saved XBRL instance document.
        period_meta = _extract_period_metadata(filing, actual_form, target_dir, saved)

        self._write_manifest(
            filing,
            request,
            target_dir,
            saved_files=saved,
            actual_form=actual_form,
            is_amendment=is_amendment,
            period_meta=period_meta,
        )

        # S3 mode: publish the fully-assembled filing dir to the store under its
        # {TICKER}/{FY}/{FORM}/... key, then drop the local staging copy. Local
        # mode is a no-op (files are already at their final path).
        if self._staging:
            rel_key = target_dir.relative_to(self.output_root).as_posix()
            self._publish_dir_to_store(target_dir, rel_key)

        period_label = f"FY{request.fiscal_year}" + (
            f" Q{request.quarter}" if request.quarter else ""
        )
        logger.info(
            "Downloaded %s %s %s -> %s (%d files)%s",
            request.ticker,
            actual_form,
            period_label,
            target_dir,
            len(saved),
            " [AMENDMENT]" if is_amendment else "",
        )
        return target_dir

    # ------------------------------------------------------------------
    # Filing resolution
    # ------------------------------------------------------------------

    def _select_filings(self, request: FilingRequest) -> list[Any]:
        """Return the filings to download for *request*, in filing-date order.

        ``select_filings`` reads the filing index only. Which of the filings is canonical
        is decided at registry time, from the facts each carries.
        """
        chosen = select_filings(Company(request.ticker), request)
        for filing in chosen:
            logger.debug(
                "Resolved %s %s FY%s Q%s → accession=%s form=%s filing_date=%s",
                request.ticker,
                request.filing_type,
                request.fiscal_year,
                request.quarter,
                getattr(filing, "accession_no", "?"),
                (getattr(filing, "form", request.filing_type) or request.filing_type)
                .upper()
                .strip(),
                getattr(filing, "filing_date", "?"),
            )
        return chosen

    def _target_dir(self, request: FilingRequest, actual_form: str) -> Path:
        """Return the output directory for the resolved filing.

        The directory name uses the *actual* form, sanitised for the filesystem
        (``/`` replaced by ``_``)::

            10-K   → <root>/<TICKER>/<FY>/10-K/
            10-K/A → <root>/<TICKER>/<FY>/10-K_A/
            10-Q   → <root>/<TICKER>/<FY>/10-Q/Q<n>/
            10-Q/A → <root>/<TICKER>/<FY>/10-Q_A/Q<n>/
        """
        form_dir = _dir_safe_form(actual_form)
        base = self.output_root / request.ticker / str(request.fiscal_year) / form_dir
        if request.filing_type == "10-Q":
            return base / f"Q{request.quarter}"
        # Annual forms (10-K, 20-F) have no quarter subdirectory.
        return base

    # ------------------------------------------------------------------
    # Attachment writing
    # ------------------------------------------------------------------

    def _publish_dir_to_store(self, local_dir: Path, rel_key: str) -> None:
        """Upload every file under *local_dir* to the store at ``rel_key/...``.

        Used only in S3 (staging) mode; removes the local staging copy after a
        successful publish so the temp root doesn't grow unbounded.
        """
        for path in sorted(local_dir.rglob("*")):
            if path.is_file():
                key = f"{rel_key}/{path.relative_to(local_dir).as_posix()}"
                self._store.write_bytes(key, path.read_bytes())
        shutil.rmtree(local_dir, ignore_errors=True)
        logger.info("published filing to store: %s", rel_key)

    def _save_xbrl_attachments(
        self, filing: Any, target_dir: Path, actual_form: str
    ) -> list[str]:
        attachments = getattr(filing, "attachments", None)
        if attachments is None:
            raise RuntimeError(
                f"Filing {getattr(filing, 'accession_no', '?')} exposes no attachments."
            )

        saved: list[str] = []
        for att in attachments:
            name = _attachment_filename(att)
            if not name:
                continue
            is_artifact = _is_xbrl_artifact(att, name, actual_form)
            # Only an HTML attachment's content can say whether it is an inline document.
            must_read = _may_be_inline_document(name)
            if not is_artifact and not must_read:
                continue
            dest = target_dir / name
            try:
                payload = self._retrying(
                    lambda a=att: _read_attachment(a, self.identity),
                    label=f"read attachment {name}",
                )
                if must_read and not payload:
                    raise RuntimeError("the attachment came back empty")
            except Exception as exc:
                if must_read:
                    raise RuntimeError(
                        f"Could not read {name} of filing "
                        f"{getattr(filing, 'accession_no', '?')}: without its bytes it may be "
                        f"an inline document that was lost."
                    ) from exc
                logger.exception(
                    "Could not read attachment %s after retries; skipping", name
                )
                continue
            if not is_artifact and not is_inline_xbrl_document(payload):
                continue
            if isinstance(payload, str):
                dest.write_text(payload, encoding="utf-8")
            else:
                dest.write_bytes(payload)
            saved.append(name)

        if not saved:
            raise RuntimeError(
                f"No XBRL/iXBRL attachments found in filing "
                f"{getattr(filing, 'accession_no', '?')}."
            )
        return saved

    @staticmethod
    def _write_manifest(
        filing: Any,
        request: FilingRequest,
        target_dir: Path,
        saved_files: list[str],
        actual_form: str,
        is_amendment: bool,
        period_meta: dict[str, Any],
    ) -> None:
        """Write ``manifest.yaml`` describing the downloaded filing.

        ``filing_type`` records the *requested* base form (``10-K`` / ``10-Q``)
        so callers know what was originally asked for.  ``form`` records the
        *resolved* form (e.g. ``10-K/A``) and ``is_amendment`` is ``true`` when
        the resolved filing is an amendment.

        Period fields:

        * ``fiscal_year`` / ``quarter`` — the request's: the calendar year the fiscal
          year ends in and, for a 10-Q, the fiscal quarter. The registries relabel both
          from the filer's annual calendar (``apply_fiscal_labels``).
        * ``period_start`` / ``period_end`` — ISO-8601 dates (``YYYY-MM-DD``)
          for the start and end of the statement period; from
          ``report_period_bounds`` of the saved instance when it gives a result, else
          XBRL DEI ``DocumentPeriodStartDate`` / ``DocumentPeriodEndDate``, otherwise
          approximated from ``period_of_report``.
        * ``fye_month`` / ``fye_day`` — fiscal-year-end month and day as
          integers; sourced from XBRL DEI ``CurrentFiscalYearEndDate`` (present
          in both 10-K and 10-Q), with ``period_of_report`` as fallback for
          10-K.  ``null`` when not determinable (e.g. 10-Q without XBRL DEI).
        """
        manifest = {
            "ticker": request.ticker,
            "filing_type": request.filing_type,  # requested base form (10-K / 10-Q)
            "form": actual_form,  # resolved form (10-K/A, 10-K, …)
            "is_amendment": is_amendment,
            "fiscal_year": request.fiscal_year,
            "quarter": request.quarter,
            # ── Period dates ────────────────────────────────────────────────
            "period_start": period_meta.get("period_start"),
            "period_end": period_meta.get("period_end"),
            "period_of_report": str(getattr(filing, "period_of_report", "")),
            # ── Fiscal-year-end ─────────────────────────────────────────────
            "fye_month": period_meta.get("fye_month"),
            "fye_day": period_meta.get("fye_day"),
            # ── Filing identity ─────────────────────────────────────────────
            "accession_no": getattr(filing, "accession_no", None),
            "cik": getattr(filing, "cik", None),
            "filing_date": str(getattr(filing, "filing_date", "")),
            "primary_document": getattr(filing, "primary_document", None),
            "ixbrl_documents": _inline_documents(
                target_dir, saved_files, getattr(filing, "primary_document", None)
            ),
            "files": sorted(saved_files),
        }
        (target_dir / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------


def _load_yaml(path: str | Path) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_FYE_DATE = re.compile(r"^--(\d{2})-(\d{2})$")  # XBRL CurrentFiscalYearEndDate: --MM-DD


def _parse_date(value: Any) -> date | None:
    """Coerce *value* to :class:`datetime.date`, or return ``None``.

    Accepts ``datetime.date``, ``datetime.datetime``, ISO-8601 strings
    (``YYYY-MM-DD``), or any object with ``.year`` / ``.month`` / ``.day``
    attributes.
    """
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            return date(int(value.year), int(value.month), int(value.day))
        except (TypeError, ValueError):
            pass
    m = _ISO_DATE.match(str(value).strip())
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _subtract_months(d: date, months: int) -> date:
    """Subtract *months* calendar months from *d*, clamping to last day of month."""
    month = d.month - months % 12
    year = d.year - months // 12
    if month <= 0:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    return d.replace(year=year, month=month, day=min(d.day, max_day))


def _annual_report_dates(company: Any) -> dict[date, int]:
    """The company's ``10-K`` index report dates, each labelled with its calendar year.

    Args:
        company: An edgartools company, or anything with its ``get_filings``.

    Returns:
        Report date → calendar year, the annual calendar a 10-Q row is placed on.
    """
    filings = company.get_filings(form="10-K")
    if not filings or len(filings) == 0:
        return {}
    out: dict[date, int] = {}
    for filing in filings:
        end = _parse_date(getattr(filing, "report_date", None))
        if end is not None:
            out[end] = end.year
    return out


def _period_matches(
    period_end: date,
    request: FilingRequest,
    annual_periods: Mapping[date, int] | None = None,
) -> bool:
    """Whether an index row's period end is the period *request* asks for.

    An annual request matches the calendar year of the period end. A 10-Q request matches
    the fiscal year and quarter ``place_quarter`` gives the period end against
    ``annual_periods`` (docs/specs/tenq-fiscal-labels.md, requirement 5); when it cannot be
    placed (no 10-K row near it), the calendar year and the calendar quarter of its month
    are matched instead, as before that change.

    Args:
        period_end: The day the row's period ends.
        request: The period asked for.
        annual_periods: The company's 10-K report dates → calendar year; unused for an
            annual request.

    Returns:
        True when the row is the period asked for.
    """
    if request.filing_type in FilingRequest._ANNUAL_FORMS:
        return period_end.year == request.fiscal_year
    placed = place_quarter(period_end, annual_periods or {})
    if placed is not None:
        return placed == (request.fiscal_year, request.quarter)
    calendar_quarter = (period_end.month - 1) // 3 + 1
    return period_end.year == request.fiscal_year and calendar_quarter == request.quarter


def _may_be_inline_document(filename: str) -> bool:
    """Whether an attachment must be read to rule out that it is an inline document."""
    return filename.lower().endswith(_HTML_SUFFIXES) and not _VIEWER_PAGE.fullmatch(filename)


def _inline_documents(
    target_dir: Path, saved_files: Sequence[str], primary: str | None
) -> list[str]:
    """List a filing's inline documents: the primary, then the others in attachment order.

    Args:
        target_dir: Where the attachments were saved.
        saved_files: The saved names, in attachment order.
        primary: The filing's primary document.

    Returns:
        The names for ``manifest["ixbrl_documents"]``.
    """
    others = [
        name
        for name in saved_files
        if name != primary
        and name.lower().endswith(_HTML_SUFFIXES)
        and is_inline_xbrl_document((target_dir / name).read_bytes())
    ]
    return ([primary] if primary else []) + others


def _attachment_filename(att: Any) -> str | None:
    for attr in ("document", "filename", "name"):
        value = getattr(att, attr, None)
        if value:
            return Path(str(value)).name
    return None


def _is_xbrl_artifact(att: Any, filename: str, form: str) -> bool:
    """Whether an attachment is kept whatever its content.

    An inline document that is neither of these is kept by ``_save_xbrl_attachments``,
    which reads its content.

    The primary iXBRL HTML is identified by SEC ``document_type`` equal to the
    filing form.  For amended filings (``10-K/A``) some EDGAR submissions label
    the attachment with the base form (``10-K``) rather than the amended form,
    so both variants are accepted.

    XBRL package members (schema, instance, linkbases) are matched by filename
    suffix because modern submissions often leave their document_type blank.
    Plain exhibits (``EX-21``, ``EX-23``, ``EX-31.*``, ``EX-32.*`` …) and SEC
    viewer artifacts are dropped.
    """
    doc_type = ""
    for attr in ("document_type", "type", "purpose"):
        value = getattr(att, attr, None)
        if value:
            doc_type = str(value).upper().strip()
            break

    # Accept the primary iXBRL document regardless of whether EDGAR tagged it
    # with the full amended form (10-K/A) or the base form (10-K).
    base_form = form[:-2] if form.endswith("/A") else form  # "10-K/A" → "10-K"
    if doc_type in (form, base_form):
        return True

    # XBRL package members: identified by canonical filename suffix.
    lower = filename.lower()
    if any(lower.endswith(suffix) for suffix in _XBRL_LINKBASE_SUFFIXES):
        return True
    if lower.endswith(".xsd"):
        return True

    return False


def _parse_xbrl_dei(path: Path) -> dict[str, str]:
    """Extract DEI date facts from a saved XBRL instance document (``*_htm.xml``).

    Returns a mapping of bare concept name → raw string value, e.g.::

        {
            "DocumentPeriodStartDate":    "2025-01-27",
            "DocumentPeriodEndDate":      "2026-01-25",
            "CurrentFiscalYearEndDate":   "--01-25",
        }

    Only the first occurrence of each concept is kept (DEI facts are
    document-level; they appear once per filing).  Returns an empty dict
    on any failure so callers can fall back gracefully.
    """
    facts: dict[str, str] = {}
    try:
        tree = ET.parse(str(path))
        root = tree.getroot()
        for elem in root.iter():
            # Strip the namespace URI: "{http://xbrl.sec.gov/dei/...}DocumentPeriodEndDate"
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local in _DEI_DATE_FACTS and local not in facts:
                text = (elem.text or "").strip()
                if text:
                    facts[local] = text
        logger.debug("XBRL DEI from %s: %s", path.name, facts)
    except Exception as exc:
        logger.debug("XBRL DEI parse failed for %s: %s", path, exc)
    return facts


def _extract_period_metadata(
    filing: Any,
    actual_form: str,
    target_dir: Path,
    saved_files: list[str],
) -> dict[str, Any]:
    """Derive ``period_start``, ``period_end``, ``fye_month``, ``fye_day``.

    Resolution order:

    1. **Statement period** — ``report_period_bounds`` of the saved ``*_htm.xml``: the
       year- or quarter-length context without dimensions that ends on the DEI context's
       end (docs/specs/tenq-fiscal-labels.md, requirement 7). Exact for 52/53-week years,
       and a 10-Q's quarter rather than its year-to-date DEI context. When it gives a
       result, both ``period_start`` and ``period_end`` come from it.

    2. **XBRL DEI** — parse ``*_htm.xml`` for ``DocumentPeriodStartDate``,
       ``DocumentPeriodEndDate``, ``CurrentFiscalYearEndDate`` (the fiscal-year end is
       always read here).

    3. **Arithmetic fallback** when neither gives a start:

       * ``period_end``   — ``period_of_report`` from the filing object (ISO date).
       * ``period_start`` — ``period_end`` minus 12 months + 1 day (10-K) or
         minus 3 months + 1 day (10-Q).  This is an approximation; it will be
         off by 0–2 days for variable FYEs.
       * ``fye_month`` / ``fye_day`` — month/day of ``period_end`` for 10-K
         (the fiscal-year-end date *is* ``period_of_report``).  ``null`` for
         10-Q without XBRL DEI.
    """
    base_form = actual_form[:-2] if actual_form.endswith("/A") else actual_form

    # ── 1. period_end from filing object (period_of_report) ──────────────────
    period_end_dt = _parse_date(getattr(filing, "period_of_report", None))
    period_end_iso: str | None = (
        period_end_dt.strftime("%Y-%m-%d") if period_end_dt else None
    )

    # ── 2. XBRL DEI from saved *_htm.xml ──────────────────────────────────────
    dei: dict[str, str] = {}
    xbrl_file = next(
        (target_dir / f for f in saved_files if f.lower().endswith("_htm.xml")),
        None,
    )
    if xbrl_file and xbrl_file.exists():
        dei = _parse_xbrl_dei(xbrl_file)

    # ── 3. period_end (prefer DEI DocumentPeriodEndDate over period_of_report) ──
    if "DocumentPeriodEndDate" in dei:
        d = _parse_date(dei["DocumentPeriodEndDate"])
        if d:
            period_end_dt = d
            period_end_iso = d.strftime("%Y-%m-%d")

    # ── 4. period_start ───────────────────────────────────────────────────────
    period_start_iso: str | None = None
    if "DocumentPeriodStartDate" in dei:
        d = _parse_date(dei["DocumentPeriodStartDate"])
        if d:
            period_start_iso = d.strftime("%Y-%m-%d")
    elif period_end_dt:
        if base_form in ("10-K", "20-F"):
            # Approximate: same month/day one year prior + 1 day.
            # Applies to both US annual (10-K) and FPI annual (20-F).
            prev_year = period_end_dt.year - 1
            clamped_day = min(
                period_end_dt.day,
                calendar.monthrange(prev_year, period_end_dt.month)[1],
            )
            prev = period_end_dt.replace(year=prev_year, day=clamped_day)
            period_start_iso = (prev + timedelta(days=1)).strftime("%Y-%m-%d")
        elif base_form == "10-Q":
            # Approximate: 3 calendar months back + 1 day.
            prev = _subtract_months(period_end_dt, 3)
            period_start_iso = (prev + timedelta(days=1)).strftime("%Y-%m-%d")

    # ── 4b. The statement period of the saved instance wins over both ─────────
    if xbrl_file and xbrl_file.exists():
        bounds = report_period_bounds(xbrl_file.read_bytes(), actual_form)
        if bounds is not None:
            period_start_iso = bounds[0].isoformat()
            period_end_dt = bounds[1]
            period_end_iso = bounds[1].isoformat()

    # ── 5. FYE month / day ────────────────────────────────────────────────────
    fye_month: int | None = None
    fye_day: int | None = None

    fye_raw = dei.get("CurrentFiscalYearEndDate", "")
    fye_m = _FYE_DATE.match(fye_raw)
    if fye_m:
        fye_month = int(fye_m.group(1))
        fye_day = int(fye_m.group(2))
    elif period_end_dt and base_form in ("10-K", "20-F"):
        # For annual filings (10-K and 20-F), period_of_report IS the FYE date.
        fye_month = period_end_dt.month
        fye_day = period_end_dt.day

    return {
        "period_start": period_start_iso,
        "period_end": period_end_iso,
        "fye_month": fye_month,
        "fye_day": fye_day,
    }


def _read_attachment(att: Any, identity: str) -> bytes | str:
    # edgartools Attachment exposes either a .download() method or a .content
    # property depending on version; try both, then fall back to a plain HTTP
    # fetch of the attachment URL.
    if hasattr(att, "download") and callable(att.download):
        return att.download()
    if hasattr(att, "content"):
        return att.content
    url = getattr(att, "url", None)
    if url:
        import urllib.request

        req = urllib.request.Request(
            url, headers={"User-Agent": identity}
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - SEC URL is trusted
            return resp.read()
    raise RuntimeError(f"Attachment object {att!r} provides no readable content.")
