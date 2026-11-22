"""H-X tooling: validate, check answer keys (SQL only), build and seal the external set H-X.

H-X is the 75-question hand-written external set of the v2 evaluation, over development
filers only: 24 cross-filer, 15 cross-period, 18 compound, 9 honesty and 9 text questions
(pre-registration Amendment 1.1). This module reuses set B's parsing, agreement and seal
(:mod:`src.eval.set_b.core`) unchanged. No message, report or resolution produced here
contains a question, note, answer, passage, amount, accession or ticker: only case ids,
field paths, kinds, sub-types, statuses and counts. Only :func:`build_hx_suite` copies
case content, into the suite itself.

Spec: docs/specs/hx-authoring.md (requirements 1-15); per-kind warning targets:
docs/specs/hx-kind-targets.md.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from src.eval.set_b.core import SCALES, UNITS, find_classes, parse_amount
from src.eval.set_b.hx_base import HxKeyFactStore

KINDS = ("cross_filer", "cross_period", "compound", "honesty", "text")
SUBTYPES: dict[str, tuple[str, ...]] = {
    "cross_filer": ("screen_top", "screen_bottom", "count", "sum", "median",
                    "peer_relative"),
    "cross_period": ("trend", "cagr", "point_in_time"),
    "honesty": ("ambiguous", "false_premise", "decline"),
}
RANKING_SUBTYPES = ("screen_top", "screen_bottom")
DEPENDENT_KINDS = ("cross_filer", "cross_period", "text")
EXCLUSION_REASONS = ("not_filed", "not_tagged", "currency", "other")
DECLINE_REASONS = ("out_of_corpus", "out_of_period", "not_tagged", "out_of_scope")
FY_RANGE = (2022, 2026)
# Per-kind case targets (pre-registration Amendment 1.1); 75 in all. validate_hx warns
# for every kind whose count differs from its own target.
TARGET_PER_KIND: dict[str, int] = {
    "cross_filer": 24,
    "cross_period": 15,
    "compound": 18,
    "honesty": 9,
    "text": 9,
}

# Status order for the report's counts; any other status follows, sorted.
STATUSES = ("verified", "ambiguous", "not_found", "arith_mismatch", "skipped")

# Relative floor of the arithmetic check (set B's compute check, requirement 11).
_ARITH_REL = 1e-3

_ACCESSION_RE = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_ACCESSION_HINT = "must look like 0000000000-00-000000"

SUITE_NAME = "heldout_x"
SUITE_VERSION = "v1"
CASE_SUITE = "heldout_x_v1"
CASE_ORIGIN = "handwritten:h_x"
CASE_ID_PREFIX = "hx-"
SPLIT_TAG = "split:external"


def _join(base: str, key: str) -> str:
    """A field path: ``key`` under ``base`` (``rows[0]`` + ``fy`` -> ``rows[0].fy``)."""
    return f"{base}.{key}" if base else key


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# --------------------------------------------------------------------------- validation


class _Checker:
    """Checks one case, collecting ``"<id>: <path>: <reason>"`` errors.

    Reasons never echo a value from the case. Every ticker the case names is recorded
    in ``named`` (for the mix) and in ``positions`` (for the compound dependent check).
    """

    def __init__(self, prefix: str, sink: list[str], dev: frozenset[str],
                 held: frozenset[str], allow_outside: bool) -> None:
        self.prefix = prefix
        self.sink = sink
        self.dev = dev
        self.held = held
        self.allow_outside = allow_outside
        self.named: set[str] = set()
        self.positions: list[tuple[str, str]] = []

    def add(self, path: str, reason: str) -> None:
        self.sink.append(f"{self.prefix}: {path}: {reason}")

    def text(self, obj: dict, key: str, path: str, *, required: bool) -> bool:
        """A non-empty string field; True when usable."""
        value = obj.get(key)
        if value is None:
            if required:
                self.add(path, "missing")
            return False
        if not _is_text(value):
            self.add(path, "must be a non-empty string")
            return False
        return True

    def fy(self, obj: dict, key: str, path: str, *, required: bool) -> int | None:
        value = obj.get(key)
        if value is None:
            if required:
                self.add(path, "missing")
            return None
        if not _is_int(value):
            self.add(path, "must be an integer")
            return None
        if not FY_RANGE[0] <= value <= FY_RANGE[1]:
            self.add(path, f"must be in {FY_RANGE[0]}-{FY_RANGE[1]}")
            return None
        return int(value)

    def unit(self, obj: dict, path: str) -> None:
        value = obj.get("unit")
        if value is None:
            self.add(path, "missing")
        elif not isinstance(value, str) or value not in UNITS:
            self.add(path, f"unknown unit (use one of {', '.join(sorted(UNITS))})")

    def list_field(self, obj: dict, key: str, path: str, *,
                   required: bool = True) -> list | None:
        value = obj.get(key)
        if value is None:
            if required:
                self.add(path, "missing")
            return None
        if not isinstance(value, list):
            self.add(path, "must be a list")
            return None
        return value

    def choice(self, obj: dict, key: str, path: str, allowed: tuple[str, ...],
               what: str) -> str | None:
        value = obj.get(key)
        if value is None:
            self.add(path, "missing")
            return None
        if not isinstance(value, str) or value not in allowed:
            self.add(path, f"unknown {what} (use one of {', '.join(allowed)})")
            return None
        return value

    def ticker(self, value: object, path: str) -> str | None:
        """Checks a ticker's type and filer set; returns it when it is a usable str."""
        if value is None:
            self.add(path, "missing")
            return None
        if not _is_text(value):
            self.add(path, "must be a non-empty string")
            return None
        tk = str(value)
        self.named.add(tk)
        self.positions.append((path, tk))
        if tk in self.held:
            self.add(path, "names a held-out filer")
        elif tk not in self.dev and not self.allow_outside:
            self.add(path, "is not a development filer")
        return tk

    def amount(self, item: dict, base: str, *, accession_required: bool = False,
               ) -> float | None:
        """Checks ``expected`` / ``scale`` / ``accession`` under ``base``; the parsed value."""
        scale = item.get("scale")
        if scale is not None and (not isinstance(scale, str) or scale not in SCALES):
            self.add(_join(base, "scale"), "unknown scale (use one of units, thousands, "
                                           "millions, billions)")
            scale_ok = False
        else:
            scale_ok = True
        value = None
        raw = item.get("expected")
        if raw is None:
            self.add(_join(base, "expected"), "missing")
        elif isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            self.add(_join(base, "expected"), "must be a number or a string")
        elif scale_ok:
            try:
                value = parse_amount(raw, scale)[0]
            except ValueError as exc:
                reason = ("a percentage takes no scale" if "percentage" in str(exc)
                          else "not an accepted amount")
                self.add(_join(base, "expected"), reason)
        acc = item.get("accession")
        if acc is None:
            if accession_required:
                self.add(_join(base, "accession"), "missing")
        elif not isinstance(acc, str) or _ACCESSION_RE.fullmatch(acc) is None:
            self.add(_join(base, "accession"), _ACCESSION_HINT)
        return value

    def str_list(self, obj: dict, key: str, path: str) -> None:
        """A required list of at least one non-empty string."""
        items = self.list_field(obj, key, path)
        if items is None:
            return
        if not items:
            self.add(path, "needs at least 1 entry")
        for i, item in enumerate(items):
            if not _is_text(item):
                self.add(f"{path}[{i}]", "must be a non-empty string")


def _check_cross_filer(obj: dict, base: str, ck: _Checker,
                       allowed: tuple[str, ...] = SUBTYPES["cross_filer"]) -> list[str] | None:
    """Requirement 3; returns the rows' usable tickers (None when ``rows`` is unusable)."""
    sub_path = _join(base, "subtype")
    subtype = obj.get("subtype")
    if subtype is None:
        ck.add(sub_path, "missing")
        subtype = None
    elif not isinstance(subtype, str) or subtype not in SUBTYPES["cross_filer"]:
        ck.add(sub_path, f"unknown subtype (use one of {', '.join(allowed)})")
        subtype = None
    elif subtype not in allowed:
        ck.add(sub_path, f"must be one of {', '.join(allowed)} here")
        subtype = None
    ck.fy(obj, "fy", _join(base, "fy"), required=True)
    ck.unit(obj, _join(base, "unit"))
    ck.text(obj, "concept", _join(base, "concept"), required=False)
    ck.text(obj, "member", _join(base, "member"), required=False)

    universe = ck.list_field(obj, "universe", _join(base, "universe"))
    uni: list[str] = []
    if universe is not None:
        for i, value in enumerate(universe):
            path = f"{_join(base, 'universe')}[{i}]"
            tk = ck.ticker(value, path)
            if tk is None:
                continue
            if tk in uni:
                ck.add(path, "repeats an earlier universe entry")
                continue
            uni.append(tk)
        if len(uni) < 2:
            ck.add(_join(base, "universe"), "needs at least 2 distinct filers")

    excluded: set[str] = set()
    exclusions = ck.list_field(obj, "exclusions", _join(base, "exclusions"), required=False)
    for i, entry in enumerate(exclusions or []):
        path = f"{_join(base, 'exclusions')}[{i}]"
        if not isinstance(entry, dict):
            ck.add(path, "must be a mapping")
            continue
        tk = ck.ticker(entry.get("ticker"), _join(path, "ticker"))
        if tk is not None:
            if universe is not None and tk not in uni:
                ck.add(_join(path, "ticker"), "is not in the universe")
            elif tk in excluded:
                ck.add(_join(path, "ticker"), "is excluded twice")
            else:
                excluded.add(tk)
        ck.choice(entry, "reason", _join(path, "reason"), EXCLUSION_REASONS, "reason")
    ranked: list[str] | None = None
    if universe is not None:
        ranked = [t for t in uni if t not in excluded]
        if uni and not ranked:
            ck.add(_join(base, "exclusions"), "leaves no ranked filer")

    rows_path = _join(base, "rows")
    rows = ck.list_field(obj, "rows", rows_path)
    if rows is None:
        return None
    row_tickers: list[str] = []
    values: list[float | None] = []
    for i, row in enumerate(rows):
        path = f"{rows_path}[{i}]"
        if not isinstance(row, dict):
            ck.add(path, "must be a mapping")
            values.append(None)
            continue
        tk = ck.ticker(row.get("ticker"), _join(path, "ticker"))
        if tk is not None:
            if tk in row_tickers:
                ck.add(_join(path, "ticker"), "repeats an earlier row's filer")
            else:
                if ranked is not None and tk not in ranked:
                    ck.add(_join(path, "ticker"), "is not a ranked filer")
                row_tickers.append(tk)
        values.append(ck.amount(row, path))

    covers = ranked is None or set(row_tickers) == set(ranked)
    if subtype in ("screen_top", "screen_bottom"):
        if not rows:
            ck.add(rows_path, "needs at least 1 row")
        parsed = [v for v in values if v is not None]
        if len(parsed) == len(values):
            pairs = list(zip(parsed, parsed[1:], strict=False))
            in_order = (all(a >= b for a, b in pairs) if subtype == "screen_top"
                        else all(a <= b for a, b in pairs))
            if not in_order:
                ck.add(rows_path, "rows are not in rank order")
    elif subtype == "count":
        expected = obj.get("expected")
        path = _join(base, "expected")
        if expected is None:
            ck.add(path, "missing")
        elif not _is_int(expected) or expected < 0:
            ck.add(path, "must be a non-negative integer")
        elif expected != len(rows):
            ck.add(path, "does not equal the number of rows")
    elif subtype == "sum":
        if len(rows) < 2:
            ck.add(rows_path, "a sum needs at least 2 rows")
        ck.amount(obj, base)
    elif subtype == "median":
        if not covers:
            ck.add(rows_path, "rows must cover exactly the ranked filers")
        ck.amount(obj, base)
    elif subtype == "peer_relative":
        subject = ck.ticker(obj.get("ticker"), _join(base, "ticker"))
        if subject is not None and subject not in row_tickers:
            ck.add(_join(base, "ticker"), "the subject is not one of the rows")
        if not covers:
            ck.add(rows_path, "rows must cover exactly the ranked filers")
        expected = obj.get("expected")
        path = _join(base, "expected")
        if expected is None:
            ck.add(path, "missing")
        elif not _is_int(expected) or not 1 <= expected <= len(rows):
            ck.add(path, "must be a rank from 1 to the number of rows")
    return row_tickers


def _check_cross_period(obj: dict, base: str, ck: _Checker) -> None:
    """Requirement 4."""
    subtype = ck.choice(obj, "subtype", _join(base, "subtype"), SUBTYPES["cross_period"],
                        "subtype")
    ck.ticker(obj.get("ticker"), _join(base, "ticker"))
    ck.unit(obj, _join(base, "unit"))
    ck.text(obj, "concept", _join(base, "concept"), required=False)
    ck.text(obj, "member", _join(base, "member"), required=False)
    if subtype in ("trend", "cagr"):
        rows_path = _join(base, "rows")
        rows = ck.list_field(obj, "rows", rows_path)
        if rows is not None:
            years: list[int] = []
            for i, row in enumerate(rows):
                path = f"{rows_path}[{i}]"
                if not isinstance(row, dict):
                    ck.add(path, "must be a mapping")
                    continue
                fy = ck.fy(row, "fy", _join(path, "fy"), required=True)
                if fy is not None:
                    if fy in years:
                        ck.add(_join(path, "fy"), "repeats an earlier row's year")
                    else:
                        years.append(fy)
                ck.amount(row, path)
            if subtype == "trend" and len(rows) < 2:
                ck.add(rows_path, "a trend needs at least 2 rows")
            if subtype == "cagr" and len(rows) != 2:
                ck.add(rows_path, "a cagr needs exactly 2 rows")
        if subtype == "cagr":
            ck.amount(obj, base)
    elif subtype == "point_in_time":
        ck.fy(obj, "fy", _join(base, "fy"), required=True)
        accessions: dict[str, Any] = {}
        for name in ("as_originally_reported", "latest"):
            path = _join(base, name)
            item = obj.get(name)
            if item is None:
                ck.add(path, "missing")
                continue
            if not isinstance(item, dict):
                ck.add(path, "must be a mapping")
                continue
            ck.amount(item, path, accession_required=True)
            accessions[name] = item.get("accession")
        orig, latest = accessions.get("as_originally_reported"), accessions.get("latest")
        if isinstance(orig, str) and isinstance(latest, str) and orig == latest:
            ck.add(_join(_join(base, "latest"), "accession"),
                   "must differ from as_originally_reported.accession")


def _check_text(obj: dict, base: str, ck: _Checker) -> None:
    """Requirement 7."""
    ck.ticker(obj.get("ticker"), _join(base, "ticker"))
    ck.fy(obj, "fy", _join(base, "fy"), required=True)
    ck.text(obj, "reference_answer", _join(base, "reference_answer"), required=True)
    ck.str_list(obj, "key_points", _join(base, "key_points"))
    path = _join(base, "passages")
    passages = ck.list_field(obj, "passages", path)
    if passages is None:
        return
    if not passages:
        ck.add(path, "needs at least 1 passage")
    for i, passage in enumerate(passages):
        p = f"{path}[{i}]"
        if not isinstance(passage, dict):
            ck.add(p, "must be a mapping")
            continue
        acc = passage.get("accession")
        if acc is None:
            ck.add(_join(p, "accession"), "missing")
        elif not isinstance(acc, str) or _ACCESSION_RE.fullmatch(acc) is None:
            ck.add(_join(p, "accession"), _ACCESSION_HINT)
        ck.text(passage, "quote", _join(p, "quote"), required=True)


def _check_honesty(obj: dict, ck: _Checker) -> None:
    """Requirement 6."""
    subtype = ck.choice(obj, "subtype", "subtype", SUBTYPES["honesty"], "subtype")
    tickers = ck.list_field(obj, "tickers", "tickers", required=False)
    for i, value in enumerate(tickers or []):
        ck.ticker(value, f"tickers[{i}]")
    ck.fy(obj, "fy", "fy", required=False)
    if subtype == "ambiguous":
        ck.str_list(obj, "assumptions", "assumptions")
    elif subtype == "false_premise":
        ck.text(obj, "correction", "correction", required=True)
    elif subtype == "decline":
        ck.choice(obj, "reason", "reason", DECLINE_REASONS, "reason")


def _check_compound(obj: dict, ck: _Checker) -> None:
    """Requirement 5."""
    ranking = obj.get("ranking")
    ranking_rows: list[str] | None = None
    if ranking is None:
        ck.add("ranking", "missing")
    elif not isinstance(ranking, dict):
        ck.add("ranking", "must be a mapping")
    else:
        ranking_rows = _check_cross_filer(ranking, "ranking", ck, RANKING_SUBTYPES)

    dependent = obj.get("dependent")
    if dependent is None:
        ck.add("dependent", "missing")
        return
    if not isinstance(dependent, dict):
        ck.add("dependent", "must be a mapping")
        return
    kind = ck.choice(dependent, "kind", "dependent.kind", DEPENDENT_KINDS, "dependent kind")
    if kind is None:
        return
    start = len(ck.positions)
    if kind == "cross_filer":
        _check_cross_filer(dependent, "dependent", ck)
    elif kind == "cross_period":
        _check_cross_period(dependent, "dependent", ck)
    else:
        _check_text(dependent, "dependent", ck)
    if ranking_rows is None:
        return
    for path, tk in ck.positions[start:]:
        if tk not in ranking_rows:
            ck.add(path, "is not one of the ranking's rows")


def _subtype_key(case: dict) -> str | None:
    """``"<kind>.<subtype>"`` when the case's subtype is valid for its kind."""
    kind, subtype = case.get("kind"), case.get("subtype")
    if isinstance(kind, str) and kind in SUBTYPES and isinstance(subtype, str) \
            and subtype in SUBTYPES[kind]:
        return f"{kind}.{subtype}"
    return None


def validate_hx(cases: list[dict], dev_filers: frozenset[str],
                heldout_filers: frozenset[str]) -> dict:
    """Checks H-X structure and filers; messages carry ids, paths and counts only.

    Args:
        cases: The authoring cases.
        dev_filers: The development filers.
        heldout_filers: The held-out filers, never allowed.

    Returns:
        ``{"errors": [...], "warnings": [...], "mix": {...}}``.

    Raises:
        ValueError: If the two filer sets intersect.
    """
    dev, held = frozenset(dev_filers), frozenset(heldout_filers)
    overlap = dev & held
    if overlap:
        raise ValueError(f"the development and held-out filer sets share {len(overlap)} "
                         "ticker(s)")
    errors: list[str] = []
    seen_ids: set[str] = set()
    filers: set[str] = set()
    kind_mix: Counter[str] = Counter()
    subtype_mix: Counter[str] = Counter()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"#{index}: case: must be a mapping")
            continue
        cid = case.get("id")
        usable_id = _is_text(cid)
        allow_outside = (case.get("kind") == "honesty" and case.get("subtype") == "decline"
                         and case.get("reason") == "out_of_corpus")
        ck = _Checker(str(cid) if usable_id else f"#{index}", errors, dev, held,
                      allow_outside)
        if not usable_id:
            ck.add("id", "missing" if cid is None else "must be a non-empty string")
        elif cid in seen_ids:
            ck.add("id", "duplicate id")
        else:
            seen_ids.add(str(cid))
        ck.text(case, "question", "question", required=True)
        note = case.get("note")
        if note is not None and not isinstance(note, str):
            ck.add("note", "must be a string")

        kind = ck.choice(case, "kind", "kind", KINDS, "kind")
        if kind is not None:
            kind_mix[kind] += 1
            key = _subtype_key(case)
            if key is not None:
                subtype_mix[key] += 1
        if kind == "cross_filer":
            _check_cross_filer(case, "", ck)
        elif kind == "cross_period":
            _check_cross_period(case, "", ck)
        elif kind == "compound":
            _check_compound(case, ck)
        elif kind == "honesty":
            _check_honesty(case, ck)
        elif kind == "text":
            _check_text(case, "", ck)
        filers |= ck.named

    warnings = [f"{kind}: {kind_mix.get(kind, 0)} cases, target {TARGET_PER_KIND[kind]}"
                for kind in KINDS if kind_mix.get(kind, 0) != TARGET_PER_KIND[kind]]
    mix = {"kind": {kind: kind_mix.get(kind, 0) for kind in KINDS},
           "subtype": dict(sorted(subtype_mix.items())),
           "filers": len(filers), "total": len(cases)}
    return {"errors": errors, "warnings": warnings, "mix": mix}


# --------------------------------------------------------------------------- arithmetic


def aggregate(op: str, values: dict[str, float], subject: str | None = None) -> float | None:
    """Sum, median, count or rank of written values.

    Args:
        op: ``sum``, ``median``, ``count`` or ``rank``.
        values: Name to value.
        subject: The name ranked, for ``rank``.

    Returns:
        The aggregate, or None for the median of nothing.

    Raises:
        ValueError: If the op is unknown or the subject is missing.
    """
    if op == "sum":
        return float(math.fsum(values.values()))
    if op == "median":
        ordered = sorted(values.values())
        n = len(ordered)
        if n == 0:
            return None
        mid = n // 2
        if n % 2:
            return float(ordered[mid])
        return (ordered[mid - 1] + ordered[mid]) / 2.0
    if op == "count":
        return float(len(values))
    if op == "rank":
        if subject is None or subject not in values:
            raise ValueError("rank needs a subject that is one of the values")
        pivot = values[subject]
        return float(1 + sum(1 for v in values.values() if v > pivot))
    raise ValueError("unknown aggregate op (use one of sum, median, count, rank)")


def cagr(start: float, end: float, years: int) -> float | None:
    """Compound annual growth rate, None unless start, end and years are positive.

    Args:
        start: The earlier value.
        end: The later value.
        years: The years between them.

    Returns:
        ``(end / start) ** (1 / years) - 1`` or None.
    """
    if not (start > 0 and end > 0 and years > 0):
        return None
    return float((end / start) ** (1.0 / years) - 1.0)


def _agrees(result: float | None, raw: object, scale: str | None) -> bool:
    """Set B's compute check: within half the last written digit or 0.1%."""
    if result is None:
        return False
    expected, half_unit = parse_amount(raw, scale)
    return abs(result - expected) <= max(half_unit, _ARITH_REL * abs(expected))


# --------------------------------------------------------------------------- resolution


class _Fetcher:
    """Reads each ``facts_for`` / ``facts_in_filing`` argument tuple at most once."""

    def __init__(self, store: HxKeyFactStore) -> None:
        self._store = store
        self._cache: dict[tuple[str, str | None, int], list[dict]] = {}

    def get(self, ticker: str, fiscal_year: int, accession: str | None) -> list[dict]:
        key = (ticker, accession, int(fiscal_year))
        if key not in self._cache:
            if accession is None:
                self._cache[key] = self._store.facts_for(ticker, int(fiscal_year))
            else:
                self._cache[key] = self._store.facts_in_filing(ticker, accession,
                                                               int(fiscal_year))
        return self._cache[key]


def _status(classes: list[list[int]]) -> str:
    if len(classes) == 1:
        return "verified"
    return "ambiguous" if classes else "not_found"


def _resolve_item(fetch: _Fetcher, items: list[dict], name: str, item: dict, ticker: str,
                  fy: int, part: dict) -> float:
    """Resolves one amount item into ``items``; returns its written value."""
    amount = parse_amount(item["expected"], item.get("scale"))
    member = part.get("member")
    candidates = fetch.get(ticker, fy, item.get("accession"))
    classes = find_classes(candidates, amount, part["unit"], member is not None,
                           part.get("concept"), member)
    items.append({"name": name, "status": _status(classes), "classes": classes})
    return amount[0]


def _resolve_part(fetch: _Fetcher, part: dict, kind: str, base: str,
                  items: list[dict]) -> bool | None:
    """Resolves one part's items; returns its arithmetic verdict (None: no check)."""
    if kind == "text":
        return None
    if kind == "cross_filer":
        by_name: dict[str, float] = {}
        by_ticker: dict[str, float] = {}
        for i, row in enumerate(part["rows"]):
            name = _join(base, f"rows[{i}]")
            value = _resolve_item(fetch, items, name, row, row["ticker"], int(part["fy"]),
                                  part)
            by_name[name] = value
            by_ticker[row["ticker"]] = value
        subtype = part["subtype"]
        if subtype in ("sum", "median"):
            return _agrees(aggregate(subtype, by_name), part["expected"], part.get("scale"))
        if subtype == "count":
            return aggregate("count", by_name) == part["expected"]
        if subtype == "peer_relative":
            try:
                rank = aggregate("rank", by_ticker, subject=part["ticker"])
            except ValueError:
                return False
            return rank == part["expected"]
        return None
    if kind == "cross_period":
        subtype = part["subtype"]
        if subtype == "point_in_time":
            for name in ("as_originally_reported", "latest"):
                _resolve_item(fetch, items, _join(base, name), part[name], part["ticker"],
                              int(part["fy"]), part)
            return None
        by_fy: dict[int, float] = {}
        for i, row in enumerate(part["rows"]):
            by_fy[int(row["fy"])] = _resolve_item(fetch, items, _join(base, f"rows[{i}]"),
                                                  row, part["ticker"], int(row["fy"]), part)
        if subtype == "cagr":
            first, last = min(by_fy), max(by_fy)
            return _agrees(cagr(by_fy[first], by_fy[last], last - first), part["expected"],
                           part.get("scale"))
        return None
    raise ValueError("unknown kind")


def resolve_hx_case(case: dict, store: HxKeyFactStore) -> dict:
    """Confirms one H-X case's numeric answer key against the fact store, by SQL only.

    Args:
        case: A validated authoring case.
        store: The fact store.

    Returns:
        ``{"id", "kind", "status", "items", "classes"}``.
    """
    kind = case["kind"]
    out: dict = {"id": case["id"], "kind": kind, "status": "", "items": [], "classes": []}
    if kind in ("text", "honesty"):
        out["status"] = "skipped"
        return out
    fetch = _Fetcher(store)
    items: list[dict] = []
    if kind == "compound":
        _resolve_part(fetch, case["ranking"], "cross_filer", "ranking", items)
        dependent = case["dependent"]
        arith = _resolve_part(fetch, dependent, dependent["kind"], "dependent", items)
    else:
        arith = _resolve_part(fetch, case, kind, "", items)
    out["items"] = items
    out["classes"] = [i["classes"][0] if i["status"] == "verified" else [] for i in items]
    bad = [i["status"] for i in items if i["status"] != "verified"]
    if bad:
        out["status"] = bad[0]
    elif arith is False:
        out["status"] = "arith_mismatch"
    else:
        out["status"] = "verified"
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}es"


def render_hx_report(resolutions: list[dict]) -> str:
    """Renders the H-X answer-key check: ids, kinds, statuses and counts only.

    Only the id, kind, status, item names and item statuses and class counts are read
    from each resolution; any other key is ignored.

    Args:
        resolutions: The results of :func:`resolve_hx_case`.

    Returns:
        The report text.
    """
    lines = ["H-X answer-key check", ""]
    width = max([len(str(r["id"])) for r in resolutions] + [2])
    kind_width = max([len(str(r.get("kind", ""))) for r in resolutions] + [4])
    for r in resolutions:
        status = str(r["status"])
        items = r.get("items") or []
        lines.append(f"{str(r['id']):<{width}}  {str(r.get('kind', '')):<{kind_width}}  "
                     f"{status:<14}  items={len(items)}")
        if status in ("verified", "skipped"):
            continue
        for item in items:
            if item["status"] == "verified":
                continue
            lines.append(f"    {item['name']}: {item['status']}, "
                         f"{_plural(len(item['classes']), 'class')}")
    counts = Counter(str(r["status"]) for r in resolutions)
    order = [s for s in STATUSES if s in counts] + sorted(set(counts) - set(STATUSES))
    lines.append("")
    lines.append(f"total: {len(resolutions)}")
    lines.extend(f"{status}: {counts[status]}" for status in order)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- build


def _value(item: dict) -> float:
    return parse_amount(item["expected"], item.get("scale"))[0]


def _cross_filer_fields(part: dict, classes: list[list[int]]) -> dict:
    subtype = part["subtype"]
    out: dict = {
        "fy": int(part["fy"]),
        "universe": sorted(part["universe"]),
        "expected_exclusions": sorted(
            ({"ticker": e["ticker"], "reason": e["reason"]}
             for e in part.get("exclusions") or []), key=lambda e: e["ticker"]),
        "expected_fact_ids": [c[0] for c in classes],
        "fact_id_classes": classes,
    }
    if subtype in ("screen_top", "screen_bottom"):
        out.update({"kind": "screen", "unit": part["unit"],
                    "expected_rows": [{"entity": r["ticker"], "value": _value(r)}
                                      for r in part["rows"]],
                    "order_sensitive": True, "top_n": len(part["rows"]), "rel_tol": 1e-4})
    elif subtype in ("count", "peer_relative"):
        out.update({"kind": "compute", "expected_value": float(part["expected"]),
                    "unit": "pure", "rel_tol": 0.0})
        if subtype == "peer_relative":
            out["ticker"] = part["ticker"]
    elif subtype in ("sum", "median"):
        out.update({"kind": "compute", "expected_value": _value(part), "unit": part["unit"],
                    "rel_tol": 1e-2})
    else:
        raise ValueError("unknown cross_filer subtype")
    return out


def _cross_period_fields(part: dict, classes: list[list[int]]) -> dict:
    subtype = part["subtype"]
    out: dict = {"ticker": part["ticker"], "expected_fact_ids": [c[0] for c in classes],
                 "fact_id_classes": classes}
    if subtype == "trend":
        rows = sorted(part["rows"], key=lambda r: int(r["fy"]))
        out.update({"kind": "breakdown", "fy": int(rows[-1]["fy"]),
                    "expected_rows": [{"entity": f"FY{int(r['fy'])}", "value": _value(r)}
                                      for r in rows],
                    "order_sensitive": False, "rel_tol": 1e-4})
    elif subtype == "cagr":
        value = _value(part)
        out.update({"kind": "compute", "fy": max(int(r["fy"]) for r in part["rows"]),
                    "expected_value": value,
                    "alt_values": [value * 100] if "%" in str(part["expected"]) else [],
                    "unit": "pure", "rel_tol": 1e-2})
    elif subtype == "point_in_time":
        names = ("as_originally_reported", "latest")
        out.update({"kind": "breakdown", "fy": int(part["fy"]),
                    "expected_rows": [{"entity": n, "value": _value(part[n])} for n in names],
                    "order_sensitive": False, "rel_tol": 1e-4,
                    "accessions": {n: part[n]["accession"] for n in names}})
    else:
        raise ValueError("unknown cross_period subtype")
    return out


def _text_fields(part: dict) -> dict:
    return {"kind": "narrative", "ticker": part["ticker"], "fy": int(part["fy"]),
            "expected_text": part["reference_answer"], "key_points": list(part["key_points"]),
            "passages": [{"accession": p["accession"], "quote": p["quote"]}
                         for p in part["passages"]]}


def _honesty_fields(case: dict) -> dict:
    out: dict = {"kind": "narrative", "require_citation": False}
    if case.get("tickers") is not None:
        out["tickers"] = sorted(case["tickers"])
    if case.get("fy") is not None:
        out["fy"] = int(case["fy"])
    subtype = case["subtype"]
    if subtype == "ambiguous":
        out["assumptions"] = list(case["assumptions"])
    elif subtype == "false_premise":
        out["expected_text"] = case["correction"]
    elif subtype == "decline":
        out["decline_reason"] = case["reason"]
    else:
        raise ValueError("unknown honesty subtype")
    return out


def _fields(part: dict, kind: str, classes: list[list[int]]) -> dict:
    """Everything but the common fields, for a part of ``kind``."""
    if kind == "cross_filer":
        return _cross_filer_fields(part, classes)
    if kind == "cross_period":
        return _cross_period_fields(part, classes)
    if kind == "text":
        return _text_fields(part)
    if kind == "honesty":
        return _honesty_fields(part)
    raise ValueError("unknown kind")


def _eval_case(case: dict, resolution: dict) -> dict:
    kind = case["kind"]
    subtype = case.get("subtype") if kind in SUBTYPES else None
    out: dict = {"id": CASE_ID_PREFIX + case["id"], "question": case["question"],
                 "suite": CASE_SUITE, "origin": CASE_ORIGIN,
                 "tags": [SPLIT_TAG, kind] + ([subtype] if subtype else [])}
    classes = [list(c) for c in resolution["classes"]]
    if kind == "compound":
        n_ranking = len(case["ranking"]["rows"])
        ranking = _fields(case["ranking"], "cross_filer", classes[:n_ranking])
        dependent = case["dependent"]
        dep_fields = _fields(dependent, dependent["kind"], classes[n_ranking:])
        out.update(ranking)
        out["parts"] = [{"part": "ranking", **ranking}, {"part": "dependent", **dep_fields}]
    else:
        out.update(_fields(case, kind, classes))
    return out


def build_hx_suite(cases: list[dict], resolutions: list[dict],
                   allow_unverified: bool = False) -> dict:
    """Builds the H-X EvalCase suite.

    Args:
        cases: The authoring cases.
        resolutions: Their :func:`resolve_hx_case` results.
        allow_unverified: Leave unverified cases out instead of raising.

    Returns:
        The suite dict.

    Raises:
        ValueError: If a case is unverified and ``allow_unverified`` is false.
    """
    by_id = {r["id"]: r for r in resolutions}
    unverified = sorted(c["id"] for c in cases
                        if by_id.get(c["id"], {}).get("status") not in ("verified", "skipped"))
    if unverified and not allow_unverified:
        raise ValueError(f"unverified case(s): {', '.join(unverified)}")
    skip = set(unverified)
    built = [_eval_case(c, by_id[c["id"]]) for c in cases if c["id"] not in skip]
    built.sort(key=lambda c: c["id"])
    suite: dict = {"name": SUITE_NAME, "version": SUITE_VERSION, "n_cases": len(built),
                   "cases": built}
    if allow_unverified:
        suite["excluded"] = unverified
    return suite
