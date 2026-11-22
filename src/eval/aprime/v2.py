# v2 held-out draw: exclusion lists, pool, hash ranking with the v2 prefix, draw.
# Spec: docs/specs/heldout-draw-v2.md. Reuses the aprime-draw building blocks in core.py.

from __future__ import annotations

import hashlib

import yaml

from src.eval.aprime.base import FilingIndexSource
from src.eval.aprime.core import (
    GICS_SECTORS,
    STRATA,
    STRATUM_S_SECTORS,
    _check_output_value,
    _cik10,
    check_eligibility,
    normalize_symbol,
)

# Requirement 3: v2 also excludes Utilities.
EXCLUDED_SECTORS_V2 = ("Financials", "Real Estate", "Utilities")
# The list names, in the order their exclusion reasons apply (requirement 3).
LIST_KEYS = ("wave1", "wave2", "left_out")
RANK_PREFIX_V2 = "xbrlrag-heldout-v2|"
SCHEME_V2 = "xbrlrag-heldout-v2"


def parse_exclusion_lists(data: dict) -> dict:
    """Validates the v2 exclusion lists (requirement 1).

    Args:
        data: The parsed mapping ``{"wave1": [...], "wave2": [...], "left_out": [...]}``.

    Returns:
        The same three keys, each a list of normalized symbols in input order.

    Raises:
        ValueError: On a missing or unknown key, a bad list or symbol, or a duplicate.
    """
    if not isinstance(data, dict):
        raise ValueError(f"exclusion lists must be a mapping, got {type(data).__name__}")
    unknown = sorted(str(k) for k in data if k not in LIST_KEYS)
    if unknown:
        raise ValueError(f"exclusion lists: unknown key(s): {', '.join(unknown)}")
    missing = [k for k in LIST_KEYS if k not in data]
    if missing:
        raise ValueError(f"exclusion lists: missing key(s): {', '.join(missing)}")

    out: dict[str, list[str]] = {}
    seen: dict[str, str] = {}  # normalized symbol -> the list it first appeared in
    for key in LIST_KEYS:
        values = data[key]
        if not isinstance(values, list) or not values:
            raise ValueError(f"exclusion lists: {key} must be a non-empty list")
        symbols: list[str] = []
        for entry in values:
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError(f"exclusion lists: {key} has an invalid entry {entry!r}")
            symbol = normalize_symbol(entry)
            if symbol in seen:
                raise ValueError(f"exclusion lists: symbol {symbol} in {key} duplicates "
                                 f"its entry in {seen[symbol]}")
            seen[symbol] = key
            symbols.append(symbol)
        out[key] = symbols
    return out


def load_exclusion_lists(path: str) -> dict:
    """Reads a YAML exclusion-lists file and validates it (requirement 2).

    Args:
        path: The YAML file, e.g. ``configs/eval/v2/heldout_exclusions.yaml``.

    Returns:
        :func:`parse_exclusion_lists` of the file's mapping.

    Raises:
        ValueError: If the file's content is invalid.
    """
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: the document must be a mapping")
    return parse_exclusion_lists(doc)


def build_pool_v2(
    constituents: list[dict],
    ciks: dict[str, str],
    corpus_ciks: set[str],
    lists: dict,
) -> dict:
    """Builds the v2 candidate pool, with exclusions and warnings (requirement 3).

    Args:
        constituents: From ``core.load_constituents``.
        ciks: From ``core.cik_map``.
        corpus_ciks: The CIKs already in the development corpus.
        lists: From :func:`parse_exclusion_lists`.

    Returns:
        ``{"candidates", "excluded", "warnings", "counts"}``.

    Raises:
        ValueError: On an unknown sector or constituents without a CIK.
    """
    for row in constituents:
        if row["sector"] not in GICS_SECTORS:
            raise ValueError(f"{row['symbol']}: unknown GICS sector {row['sector']!r}")
    no_cik = sorted({row["symbol"] for row in constituents
                     if normalize_symbol(row["symbol"]) not in ciks})
    if no_cik:
        raise ValueError(f"no CIK for symbol(s): {', '.join(no_cik)}")
    corpus = {_cik10(c) for c in corpus_ciks}

    # Each list matches by symbol, or by the CIK of any listed symbol that has one.
    list_symbols = {key: set(lists[key]) for key in LIST_KEYS}
    list_ciks = {key: {_cik10(ciks[s]) for s in lists[key] if s in ciks} for key in LIST_KEYS}

    excluded: list[dict] = []
    kept: list[dict] = []
    symbols: set[str] = set()
    for row in constituents:
        symbol = normalize_symbol(row["symbol"])
        symbols.add(symbol)
        cik = _cik10(ciks[symbol])
        reason: str | None = None
        if row["sector"] in EXCLUDED_SECTORS_V2:
            reason = "sector"
        elif cik in corpus:
            reason = "corpus"
        else:
            for key in LIST_KEYS:
                if symbol in list_symbols[key] or cik in list_ciks[key]:
                    reason = key
                    break
        if reason is None:
            kept.append({"symbol": symbol, "name": row["name"], "sector": row["sector"],
                         "cik": cik,
                         "stratum": "S" if row["sector"] in STRATUM_S_SECTORS else "D"})
        else:
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

    warnings = [{"list": key, "symbol": s, "reason": "not_in_constituents"}
                for key in LIST_KEYS for s in lists[key] if s not in symbols]

    candidates.sort(key=lambda c: c["cik"])
    excluded.sort(key=lambda e: e["symbol"])
    warnings.sort(key=lambda w: (w["list"], w["symbol"]))
    return {
        "candidates": candidates,
        "excluded": excluded,
        "warnings": warnings,
        "counts": {
            "constituents": len(constituents),
            "S": sum(c["stratum"] == "S" for c in candidates),
            "D": sum(c["stratum"] == "D" for c in candidates),
            "excluded": len(excluded),
            "warnings": len(warnings),
        },
    }


def rank_key_v2(output_value: str, cik: str) -> str:
    """The v2 SHA-256 ranking key of one candidate under one pulse (requirement 4).

    Args:
        output_value: The pulse's outputValue.
        cik: The candidate's CIK.

    Returns:
        A lowercase hex digest.
    """
    text = RANK_PREFIX_V2 + output_value.upper() + "|" + _cik10(cik)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def rank_v2(candidates: list[dict], output_value: str) -> list[dict]:
    """Orders candidates by their v2 ranking key (requirement 4).

    Args:
        candidates: Pool candidates.
        output_value: The pulse's outputValue.

    Returns:
        Copies with ``key`` and 1-based ``rank`` added, sorted by ``(key, cik)``.
    """
    keyed = [{**c, "key": rank_key_v2(output_value, c["cik"])} for c in candidates]
    keyed.sort(key=lambda c: (c["key"], c["cik"]))
    for i, c in enumerate(keyed, start=1):
        c["rank"] = i
    return keyed


def draw_v2(
    pool: dict,
    pulse: dict,
    source: FilingIndexSource,
    per_stratum: int = 4,
    alternates: int = 2,
) -> dict:
    """Draws each stratum's held-out filers in v2 pulse-rank order (requirement 5).

    Args:
        pool: From :func:`build_pool_v2`.
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
        for cand in rank_v2(members, output_value):
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
