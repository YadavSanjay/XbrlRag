# Sector and industry tags per filer, and the named universe groups derived from them.
#
# Peer-relative questions ("among retailers", "the semiconductor median") need a universe to
# rank over. The tags live in ``configs/entities/sectors.yaml`` and the groups in
# ``configs/eval/v2/groups.yaml``; both are committed, validated config that fails loud.
#
# Spec: [docs/specs/sector-tags.md](../../../docs/specs/sector-tags.md).

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from src import PROJECT_ROOT

DEFAULT_SECTORS_PATH = Path(PROJECT_ROOT) / "configs" / "entities" / "sectors.yaml"
DEFAULT_GROUPS_PATH = Path(PROJECT_ROOT) / "configs" / "eval" / "v2" / "groups.yaml"

# The eleven GICS sector names (2023 GICS structure), case-sensitive.
GICS_SECTORS = frozenset({
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
})
# The corpus-wide universe; built from the corpus, never a named group (S3).
COVERED = "covered"
RULE_KEYS = ("sectors", "industries", "tickers")

_CIK = re.compile(r"[0-9]{10}")
_GROUP_ID = re.compile(r"[a-z][a-z0-9_]*")
_SECTORS_KEYS = frozenset({"as_of", "source", "filers"})
_FILER_KEYS = frozenset({"cik", "ticker", "gics_sector", "industry"})
_FILER_OPTIONAL_KEYS = frozenset({"note"})
_GROUPS_KEYS = frozenset({"as_of", "sectors_file", "definitions", "groups"})


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc


def _mapping(where: str, value: Any) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _text(where: str, value: Any) -> str:
    """A non-empty string, trimmed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value.strip()


def _iso_date(where: str, value: Any) -> str:
    """An ISO calendar date ``YYYY-MM-DD`` (a YAML date is accepted and re-serialized)."""
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        try:
            if dt.date.fromisoformat(text).isoformat() == text:
                return text
        except ValueError:
            pass
    raise ValueError(f"{where}: expected an ISO date YYYY-MM-DD, got {value!r}")


def _check_keys(where: str, keys: Any, required: frozenset[str],
                optional: frozenset[str] = frozenset()) -> None:
    keys = set(keys)
    unknown = keys - required - optional
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    missing = required - keys
    if missing:
        raise ValueError(f"{where}: missing key(s) {sorted(missing)}")


def load_sector_tags(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the sector tags.

    Args:
        path: The sectors YAML file; None loads ``configs/entities/sectors.yaml``.

    Returns:
        ``{"as_of", "source", "filers": {cik: {"ticker", "gics_sector", "industry"}}}``.

    Raises:
        ValueError: The file is malformed (see the spec's C2).
    """
    path = DEFAULT_SECTORS_PATH if path is None else path
    doc = _mapping(str(path), _read_yaml(path))
    _check_keys(str(path), doc, _SECTORS_KEYS)
    as_of = _iso_date(f"{path}: as_of", doc["as_of"])
    source = _text(f"{path}: source", doc["source"])
    entries = doc["filers"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: filers must be a non-empty list")

    filers: dict[str, dict[str, str]] = {}
    tickers: set[str] = set()
    for i, raw in enumerate(entries):
        where = f"{path}: filers[{i}]"
        entry = _mapping(where, raw)
        _check_keys(where, entry, _FILER_KEYS, _FILER_OPTIONAL_KEYS)
        cik = entry["cik"]
        if not isinstance(cik, str) or not _CIK.fullmatch(cik):
            raise ValueError(f"{where}: cik must be a ten-digit string, got {cik!r}")
        ticker = _text(f"{where}.ticker", entry["ticker"])
        sector = _text(f"{where}.gics_sector", entry["gics_sector"])
        if sector not in GICS_SECTORS:
            raise ValueError(f"{where}: {sector!r} is not a GICS sector name")
        industry = _text(f"{where}.industry", entry["industry"])
        if "note" in entry and entry["note"] is not None and not isinstance(entry["note"], str):
            raise ValueError(f"{where}.note: expected a string")
        if cik in filers:
            raise ValueError(f"{where}: duplicate cik {cik}")
        if ticker in tickers:
            raise ValueError(f"{where}: duplicate ticker {ticker}")
        tickers.add(ticker)
        filers[cik] = {"ticker": ticker, "gics_sector": sector, "industry": industry}
    return {"as_of": as_of, "source": source, "filers": dict(sorted(filers.items()))}


def _resolve_rule(gid: str, rule: Any, tags: dict[str, Any],
                  ticker_set: set[str]) -> list[str]:
    """The sorted tickers one group rule selects."""
    rule = _mapping(f"definitions.{gid}", rule)
    unknown = set(rule) - set(RULE_KEYS)
    if unknown:
        raise ValueError(f"definitions.{gid}: unknown rule key(s) {sorted(map(str, unknown))}")
    if len(rule) != 1:
        raise ValueError(f"definitions.{gid}: needs exactly one of {list(RULE_KEYS)}")
    kind, values = next(iter(rule.items()))
    if (not isinstance(values, list) or not values
            or not all(isinstance(v, str) and v.strip() for v in values)):
        raise ValueError(f"definitions.{gid}.{kind}: expected a non-empty list of strings")
    wanted = {v.strip() for v in values}
    filers = tags["filers"].values()
    if kind == "tickers":
        unknown_tickers = wanted - ticker_set
        if unknown_tickers:
            raise ValueError(
                f"definitions.{gid}: ticker(s) not in the tags {sorted(unknown_tickers)}")
        members = wanted
    else:
        field = "gics_sector" if kind == "sectors" else "industry"
        members = {f["ticker"] for f in filers if f[field] in wanted}
    if not members:
        raise ValueError(f"definitions.{gid}: the rule selects no filer")
    return sorted(members)


def build_groups(
    tags: dict[str, Any], definitions: dict[str, dict[str, list[str]]]
) -> dict[str, list[str]]:
    """Resolve each group definition to the sorted tickers it selects.

    Args:
        tags: As returned by :func:`load_sector_tags`.
        definitions: Group id to exactly one rule (``sectors``, ``industries`` or ``tickers``).

    Returns:
        Group id to sorted, de-duplicated tickers.

    Raises:
        ValueError: A rule is malformed, names an unknown ticker or selects nobody.
    """
    ticker_set = {f["ticker"] for f in tags["filers"].values()}
    return {gid: _resolve_rule(gid, rule, tags, ticker_set)
            for gid, rule in definitions.items()}


def _group_id(where: str, gid: Any) -> str:
    if not isinstance(gid, str) or not _GROUP_ID.fullmatch(gid):
        raise ValueError(f"{where}: group id {gid!r} must match ^[a-z][a-z0-9_]*$")
    if gid == COVERED:
        raise ValueError(f"{where}: '{COVERED}' is built from the corpus, never a group")
    return gid


def load_universe_groups(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the named universe groups file.

    Args:
        path: The groups YAML file; None loads ``configs/eval/v2/groups.yaml``.

    Returns:
        ``{"as_of", "sectors_file", "definitions", "groups"}``.

    Raises:
        ValueError: The file is malformed (see the spec's C4).
    """
    path = DEFAULT_GROUPS_PATH if path is None else path
    doc = _mapping(str(path), _read_yaml(path))
    _check_keys(str(path), doc, _GROUPS_KEYS)
    as_of = _iso_date(f"{path}: as_of", doc["as_of"])
    sectors_file = _text(f"{path}: sectors_file", doc["sectors_file"])

    definitions: dict[str, dict[str, list[str]]] = {}
    for gid, rule in _mapping(f"{path}: definitions", doc["definitions"]).items():
        gid = _group_id(f"{path}: definitions", gid)
        rule = _mapping(f"{path}: definitions.{gid}", rule)
        definitions[gid] = {str(k): list(v) if isinstance(v, list) else v
                            for k, v in rule.items()}

    groups: dict[str, list[str]] = {}
    for gid, members in _mapping(f"{path}: groups", doc["groups"]).items():
        gid = _group_id(f"{path}: groups", gid)
        if (not isinstance(members, list) or not members
                or not all(isinstance(t, str) and t.strip() for t in members)):
            raise ValueError(f"{path}: groups.{gid}: expected a non-empty list of tickers")
        tickers = [t.strip() for t in members]
        if len(set(tickers)) != len(tickers):
            raise ValueError(f"{path}: groups.{gid}: a ticker is listed twice")
        groups[gid] = tickers

    if set(definitions) != set(groups):
        raise ValueError(
            f"{path}: definitions and groups name different ids: "
            f"{sorted(set(definitions) ^ set(groups))}")
    return {"as_of": as_of, "sectors_file": sectors_file, "definitions": definitions,
            "groups": groups}


def check_groups(
    groups_doc: dict[str, Any],
    tags: dict[str, Any],
    corpus_tickers: set[str],
    lexicon_universes: Mapping[str, Sequence[str]],
) -> list[str]:
    """Report every inconsistency between the groups, the tags, the corpus and the lexicon.

    Args:
        groups_doc: As returned by :func:`load_universe_groups`.
        tags: As returned by :func:`load_sector_tags`.
        corpus_tickers: The tickers of the filers in the corpus.
        lexicon_universes: Universe id to its phrases, from the v2 lexicon.

    Returns:
        One problem string per fault, each naming the group id; empty when consistent.
    """
    definitions = groups_doc.get("definitions") or {}
    groups = groups_doc.get("groups") or {}
    ticker_set = {f["ticker"] for f in tags["filers"].values()}
    problems: list[str] = []
    for gid in sorted(set(definitions) | set(groups)):
        if gid == COVERED:
            problems.append(f"{COVERED}: reserved for the corpus-wide universe, not a group")
        members = list(groups.get(gid) or [])
        if gid not in definitions:
            problems.append(f"{gid}: has members but no definition")
        else:
            try:
                built = _resolve_rule(gid, definitions[gid], tags, ticker_set)
            except ValueError as exc:
                problems.append(f"{gid}: definition does not resolve: {exc}")
            else:
                if built != members:
                    problems.append(
                        f"{gid}: committed members {members} differ from the build {built}")
        for ticker in members:
            if ticker not in corpus_tickers:
                problems.append(f"{gid}: member {ticker} is not a corpus filer")
        if gid != COVERED and not list(lexicon_universes.get(gid) or []):
            problems.append(f"{gid}: the lexicon has no phrase for this universe")
    return problems


def resolve_group_ciks(
    group_id: str, groups: dict[str, list[str]], tags: dict[str, Any]
) -> list[str]:
    """Resolve a named group to the CIKs of its members.

    Args:
        group_id: The universe id, e.g. ``semiconductors``.
        groups: Group id to tickers.
        tags: As returned by :func:`load_sector_tags`.

    Returns:
        The members' CIKs, sorted ascending.

    Raises:
        ValueError: Unknown group id, ``covered``, or a member absent from the tags.
    """
    if group_id == COVERED:
        raise ValueError(f"'{COVERED}' is built from the corpus, not a named group")
    if group_id not in groups:
        raise ValueError(f"unknown universe group {group_id!r}")
    cik_by_ticker = {f["ticker"]: cik for cik, f in tags["filers"].items()}
    missing = sorted(set(groups[group_id]) - set(cik_by_ticker))
    if missing:
        raise ValueError(f"group {group_id!r}: member(s) not in the sector tags {missing}")
    return sorted({cik_by_ticker[t] for t in groups[group_id]})
