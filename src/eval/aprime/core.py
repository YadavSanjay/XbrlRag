# A' draw: constituents, CIKs, pool, hash ranking, eligibility, pulse check, draw.
# Spec: docs/specs/aprime-draw.md (requirements 1-10).

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re

from src.eval.aprime.base import FilingIndexSource

# The 11 GICS sector names, spelled exactly (requirement 4).
GICS_SECTORS = (
    "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy",
    "Financials", "Health Care", "Industrials", "Information Technology", "Materials",
    "Real Estate", "Utilities",
)
EXCLUDED_SECTORS = ("Financials", "Real Estate")
STRATUM_S_SECTORS = ("Information Technology", "Communication Services")

# The prospective set P's filers, pre-registration v0 section 3.
P_CIKS = {
    "0000320193": "AAPL",
    "0000804328": "QCOM",
    "0000032604": "EMR",
    "0001744489": "DIS",
    "0000829224": "SBUX",
}

RANK_PREFIX = "xbrlrag-aprime-v1|"
STRATA = ("S", "D")

_COLUMNS = ("symbol", "name", "sector")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HEX128_RE = re.compile(r"[0-9a-fA-F]{128}")


def _cik10(cik: object) -> str:
    return str(int(str(cik).strip())).zfill(10)


def load_constituents(text: str) -> list[dict]:
    """Parses the constituents CSV.

    Args:
        text: CSV text with at least the columns symbol, name and sector.

    Returns:
        ``{"symbol", "name", "sector"}`` per data row, in file order.

    Raises:
        ValueError: On a missing column, an empty or duplicate symbol, or no rows.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = [(f or "").strip() for f in (reader.fieldnames or [])]
    missing = [c for c in _COLUMNS if c not in fields]
    if missing:
        raise ValueError(f"constituents CSV lacks column(s): {', '.join(missing)}")
    reader.fieldnames = fields

    rows: list[dict] = []
    seen: dict[str, str] = {}
    for line_no, raw in enumerate(reader, start=2):
        row = {c: (raw.get(c) or "").strip() for c in _COLUMNS}
        if not row["symbol"]:
            raise ValueError(f"constituents CSV line {line_no}: empty symbol")
        norm = normalize_symbol(row["symbol"])
        if norm in seen:
            raise ValueError(f"constituents CSV line {line_no}: symbol {row['symbol']!r} "
                             f"duplicates {seen[norm]!r}")
        seen[norm] = row["symbol"]
        rows.append(row)
    if not rows:
        raise ValueError("constituents CSV has no data rows")
    return rows


def normalize_symbol(symbol: str) -> str:
    """Normalizes a ticker the way SEC writes it (``brk.b`` -> ``BRK-B``).

    Args:
        symbol: The ticker as written.

    Returns:
        The stripped, upper-cased ticker with ``.`` and ``/`` replaced by ``-``.
    """
    return symbol.strip().upper().replace(".", "-").replace("/", "-")


def cik_map(company_tickers: dict) -> dict[str, str]:
    """Maps normalized tickers to 10-digit CIKs from SEC's ``company_tickers.json``.

    Args:
        company_tickers: The parsed document.

    Returns:
        ``{normalized ticker: cik10}``.

    Raises:
        ValueError: If one ticker carries two different CIKs.
    """
    out: dict[str, str] = {}
    for entry in company_tickers.values():
        ticker = normalize_symbol(str(entry["ticker"]))
        cik = _cik10(entry["cik_str"])
        if ticker in out and out[ticker] != cik:
            raise ValueError(f"ticker {ticker} maps to two CIKs: {out[ticker]} and {cik}")
        out[ticker] = cik
    return out


def build_pool(constituents: list[dict], ciks: dict[str, str], corpus_ciks: set[str]) -> dict:
    """Builds the candidate pool, with each exclusion and its reason.

    Args:
        constituents: From :func:`load_constituents`.
        ciks: From :func:`cik_map`.
        corpus_ciks: The CIKs already in the development corpus.

    Returns:
        ``{"candidates": [...], "excluded": [...], "counts": {...}}``.

    Raises:
        ValueError: On an unknown sector or symbols without a CIK.
    """
    for row in constituents:
        if row["sector"] not in GICS_SECTORS:
            raise ValueError(f"{row['symbol']}: unknown GICS sector {row['sector']!r}")
    no_cik = sorted({row["symbol"] for row in constituents
                     if normalize_symbol(row["symbol"]) not in ciks})
    if no_cik:
        raise ValueError(f"no CIK for symbol(s): {', '.join(no_cik)}")
    corpus = {_cik10(c) for c in corpus_ciks}

    excluded: list[dict] = []
    kept: list[dict] = []
    for row in constituents:
        symbol = normalize_symbol(row["symbol"])
        cik = ciks[symbol]
        if row["sector"] in EXCLUDED_SECTORS:
            reason = "sector"
        elif cik in corpus:
            reason = "corpus"
        elif cik in P_CIKS:
            reason = "prospective"
        else:
            kept.append({"symbol": symbol, "name": row["name"], "sector": row["sector"],
                         "cik": cik,
                         "stratum": "S" if row["sector"] in STRATUM_S_SECTORS else "D"})
            continue
        excluded.append({"symbol": symbol, "cik": cik, "reason": reason})

    by_cik: dict[str, list[dict]] = {}
    for cand in kept:
        by_cik.setdefault(cand["cik"], []).append(cand)
    candidates: list[dict] = []
    for cik, group in by_cik.items():
        group.sort(key=lambda c: c["symbol"])
        candidates.append(group[0])
        excluded.extend({"symbol": c["symbol"], "cik": cik, "reason": "duplicate_cik"}
                        for c in group[1:])

    candidates.sort(key=lambda c: c["cik"])
    excluded.sort(key=lambda e: e["symbol"])
    return {
        "candidates": candidates,
        "excluded": excluded,
        "counts": {
            "constituents": len(constituents),
            "S": sum(c["stratum"] == "S" for c in candidates),
            "D": sum(c["stratum"] == "D" for c in candidates),
            "excluded": len(excluded),
        },
    }


def rank_key(output_value: str, cik: str) -> str:
    """The SHA-256 ranking key of one candidate under one pulse.

    Args:
        output_value: The pulse's outputValue.
        cik: The candidate's CIK.

    Returns:
        A lowercase hex digest.
    """
    text = RANK_PREFIX + output_value.upper() + "|" + _cik10(cik)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def rank(candidates: list[dict], output_value: str) -> list[dict]:
    """Orders candidates by their ranking key.

    Args:
        candidates: Pool candidates.
        output_value: The pulse's outputValue.

    Returns:
        Copies with ``key`` and 1-based ``rank`` added, sorted by ``(key, cik)``.
    """
    keyed = [{**c, "key": rank_key(output_value, c["cik"])} for c in candidates]
    keyed.sort(key=lambda c: (c["key"], c["cik"]))
    for i, c in enumerate(keyed, start=1):
        c["rank"] = i
    return keyed


def fiscal_year_of(report_date: str) -> int:
    """Maps a 10-K's period-of-report date to its fiscal year.

    Args:
        report_date: ``YYYY-MM-DD``.

    Returns:
        The year, or the previous year for January 1-7.

    Raises:
        ValueError: If the date is malformed.
    """
    if not isinstance(report_date, str) or not _DATE_RE.fullmatch(report_date):
        raise ValueError(f"malformed report date {report_date!r}")
    day = dt.date.fromisoformat(report_date)
    if day.month == 1 and day.day <= 7:
        return day.year - 1
    return day.year


def check_eligibility(filings: list[dict], years: tuple[int, ...] = (2023, 2024, 2025)) -> dict:
    """Checks that each year has an original inline-XBRL 10-K.

    Args:
        filings: Filing-index records.
        years: The fiscal years required.

    Returns:
        ``{"eligible", "missing_years", "not_inline_years"}``.
    """
    seen: dict[int, bool] = {}  # fiscal year -> any inline 10-K
    for rec in filings:
        if rec.get("form") != "10-K" or not rec.get("report_date"):
            continue
        fy = fiscal_year_of(rec["report_date"])
        seen[fy] = seen.get(fy, False) or bool(rec.get("is_inline_xbrl"))
    missing = sorted(y for y in set(years) if y not in seen)
    not_inline = sorted(y for y in set(years) if y in seen and not seen[y])
    return {"eligible": not missing and not not_inline, "missing_years": missing,
            "not_inline_years": not_inline}


def _utc_instant(text: object, label: str) -> dt.datetime:
    """Parses an ISO 8601 time written in UTC (``Z`` or ``+00:00``)."""
    if not isinstance(text, str) or not (text.endswith("Z") or text.endswith("+00:00")):
        raise ValueError(f"{label} {text!r} is not an ISO 8601 UTC time")
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} {text!r} is not an ISO 8601 UTC time") from exc
    if value.utcoffset() != dt.timedelta(0):
        raise ValueError(f"{label} {text!r} is not an ISO 8601 UTC time")
    return value


def _check_output_value(pulse: dict) -> str:
    value = pulse.get("outputValue")
    if not isinstance(value, str):
        raise ValueError("pulse lacks a string outputValue")
    if not _HEX128_RE.fullmatch(value):
        raise ValueError("pulse outputValue is not 128 hexadecimal characters")
    return value


def check_pulse(pulse: dict, requested_utc: str) -> None:
    """Checks a NIST beacon pulse against the pre-registered minute.

    Args:
        pulse: The beacon's ``pulse`` object.
        requested_utc: The stated pulse time, ISO 8601 UTC, on a whole minute.

    Raises:
        ValueError: If the pulse is malformed or isn't the requested minute's.
    """
    stamp = pulse.get("timeStamp")
    if not isinstance(stamp, str):
        raise ValueError("pulse lacks a string timeStamp")
    _check_output_value(pulse)
    requested = _utc_instant(requested_utc, "requested time")
    if requested.second or requested.microsecond:
        raise ValueError(f"requested time {requested_utc!r} is not on a whole minute")
    if _utc_instant(stamp, "pulse timeStamp") != requested:
        raise ValueError(f"pulse timeStamp {stamp!r} is not the requested {requested_utc!r}")


def draw(
    pool: dict,
    pulse: dict,
    source: FilingIndexSource,
    per_stratum: int = 5,
    alternates: int = 2,
) -> dict:
    """Draws each stratum's filers and alternates in pulse-rank order.

    Args:
        pool: From :func:`build_pool`.
        pulse: The checked beacon pulse.
        source: Filing indexes, read once per candidate walked.
        per_stratum: Filers drawn per stratum.
        alternates: Ordered alternates per stratum.

    Returns:
        ``{"output_value", "pulse_timestamp", "strata": {"S": ..., "D": ...}}``.

    Raises:
        ValueError: If the pulse's outputValue is malformed.
    """
    output_value = _check_output_value(pulse)
    strata: dict[str, dict] = {}
    for stratum in STRATA:
        members = [c for c in pool["candidates"] if c["stratum"] == stratum]
        drawn: list[dict] = []
        alts: list[dict] = []
        checked: list[dict] = []
        for cand in rank(members, output_value):
            if len(drawn) >= per_stratum and len(alts) >= alternates:
                break
            elig = check_eligibility(source.filings(cand["cik"]))
            checked.append({"rank": cand["rank"], "symbol": cand["symbol"], "cik": cand["cik"],
                            "key": cand["key"], **elig})
            if not elig["eligible"]:
                continue
            entry = {k: cand[k] for k in ("symbol", "name", "sector", "cik", "rank", "key")}
            (drawn if len(drawn) < per_stratum else alts).append(entry)
        strata[stratum] = {"drawn": drawn, "alternates": alts, "checked": checked,
                           "shortfall": per_stratum + alternates - len(drawn) - len(alts)}
    return {"output_value": output_value.upper(), "pulse_timestamp": pulse["timeStamp"],
            "strata": strata}
