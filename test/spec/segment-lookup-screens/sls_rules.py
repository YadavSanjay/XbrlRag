"""The spec's rules restated for the tests (docs/specs/segment-lookup-screens.md).

Written from the spec text, not from the code: the member key and compact label
(Definitions), the segment screen's choice (Requirements 2-3), the exclusion order
(Requirement 4) and the geographic resolution (Requirement 5). Records are in the shape
of the fixtures (see conftest.py).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

BUSINESS_AXES = {"StatementBusinessSegmentsAxis", "ProductOrServiceAxis"}
_TRAILING = {"segment", "segments", "member"}

REGION_KEYS = {
    "europe": {"europe"},
    "emea": {"emea", "europemiddleeastandafrica"},
}
REGION_OF_REQUEST = {"europe": "europe", "emea": "emea", "europemiddleeastandafrica": "emea"}


def words(text: str | None) -> list[str]:
    s = (text or "").lower().replace("[member]", " ").replace("&", " and ")
    return re.findall(r"[a-z0-9]+", s)


def member_key(text: str | None) -> str:
    """Definitions: member key (segment-label-match Requirement 1)."""
    w = words(text)
    if len(w) > 1 and w[-1] in _TRAILING:
        w = w[:-1]
    return "".join(w)


def compact(text: str | None) -> str:
    """Definitions: compact label (no trailing word dropped)."""
    return "".join(words(text))


def local_axis(axis: str | None) -> str | None:
    return re.split(r"[:}]", axis)[-1] if axis else None


def bare(label: str) -> str:
    """A label without its ``[Member]`` suffix, for comparing listed labels."""
    return re.sub(r"\s*\[member\]\s*", " ", label, flags=re.IGNORECASE).strip().lower()


# ------------------------------------------------------------------ outcomes
def _choose(single: Sequence[dict], exact_of) -> tuple[str, dict]:
    """Requirement 3 over single-dimension candidates: ('ranked', row) or
    ('ambiguous_member', {labels}) or ('currency', {currency})."""
    foreign = sorted({r["unit"] for r in single if r["unit"] != "USD"})
    if foreign:
        return "currency", {"currency": foreign[0]}
    exact = [r for r in single if exact_of(r)]
    counted = exact or list(single)
    keys = {member_key(r["segment_label"]) for r in counted}
    values = {r["value"] for r in counted}
    if len(keys) > 1 or len(values) > 1:
        return "ambiguous_member", {"labels": sorted({r["segment_label"] for r in counted})}
    return "ranked", {"value": counted[0]["value"],
                      "member_match": "exact" if exact else "contains",
                      "label": counted[0]["segment_label"]}


def _finish(out: dict, ticker: str, fy_rows: list[dict], older: list[dict]) -> None:
    if older:
        latest = max(r["fiscal_year"] for r in older)
        out[ticker] = ("period_not_filed", {"latest_fiscal_year": latest})
    elif fy_rows:
        out[ticker] = ("member_not_found", {})
    else:
        out[ticker] = ("metric_not_tagged", {})


def _by_ticker(records: Iterable[dict], roster: Iterable[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {t: [] for t in roster}
    for r in records:
        out.setdefault(r["ticker"], []).append(r)
    return out


def segment_outcomes(records: Sequence[dict], roster: Sequence[str], hint: str, fy: int) -> dict:
    """Per ticker: (outcome, details) for a segment screen of `hint` (Requirements 2-4)."""
    key = member_key(hint)

    def matches(r: dict) -> bool:
        return local_axis(r["segment_axis"]) in BUSINESS_AXES and key in compact(r["segment_label"])

    out: dict[str, tuple[str, dict]] = {}
    for ticker, rows in _by_ticker(records, roster).items():
        rows = [r for r in rows if r.get("segment_axis") and r["concept_family"] == "Revenue"]
        fy_rows = [r for r in rows if r["fiscal_year"] == fy]
        cand = [r for r in fy_rows if matches(r)]
        single = [r for r in cand if not r["has_multiple_dimensions"]]
        if single:
            out[ticker] = _choose(single, lambda r: member_key(r["segment_label"]) == key)
        elif cand:
            out[ticker] = ("multi_dimension_only", {})
        else:
            older = [r for r in rows if r["fiscal_year"] < fy and matches(r)]
            _finish(out, ticker, fy_rows, older)
    return out


def country_outcomes(records: Sequence[dict], roster: Sequence[str], code: str, name: str,
                     fy: int) -> dict:
    """Per ticker: (outcome, details) for a geographic screen of a country (Requirement 5)."""
    place = compact(name)
    out: dict[str, tuple[str, dict]] = {}
    for ticker, rows in _by_ticker(records, roster).items():
        rows = [r for r in rows if r.get("is_geographic_row") and r["concept_family"] == "Revenue"]
        fy_rows = [r for r in rows if r["fiscal_year"] == fy]
        cand = [r for r in fy_rows if r.get("geo_code") == code]
        related = [r for r in fy_rows if r.get("geo_code") != code
                   and not str(r.get("segment_member") or "").startswith("country:")
                   and place in compact(r["segment_label"])]
        if cand:
            usd = [r for r in cand if r["unit"] == "USD"]
            if len(usd) < len(cand):
                out[ticker] = ("currency", {"currency": sorted({r["unit"] for r in cand
                                                                if r["unit"] != "USD"})[0]})
            else:
                best = max(usd, key=lambda r: r["value"])
                out[ticker] = ("ranked", {"value": best["value"]})
        elif related:
            labels = sorted({r["segment_label"] for r in related})
            out[ticker] = ("different_member", {"labels": labels})
        else:
            older = [r for r in rows if r["fiscal_year"] < fy and r.get("geo_code") == code]
            _finish(out, ticker, fy_rows, older)
    return out


def region_outcomes(records: Sequence[dict], roster: Sequence[str], request: str, fy: int) -> dict:
    """Per ticker: (outcome, details) for a Europe or EMEA screen (Requirement 5)."""
    region = REGION_OF_REQUEST[member_key(request)]
    keys = REGION_KEYS[region]
    other = set().union(*(k for reg, k in REGION_KEYS.items() if reg != region))

    def matches(r: dict) -> bool:
        return member_key(r["segment_label"]) in keys

    def is_related(r: dict) -> bool:
        c = compact(r["segment_label"])
        return member_key(r["segment_label"]) in other or "europe" in c or "emea" in c

    out: dict[str, tuple[str, dict]] = {}
    for ticker, rows in _by_ticker(records, roster).items():
        rows = [r for r in rows if r.get("is_geographic_row") and r["concept_family"] == "Revenue"]
        fy_rows = [r for r in rows if r["fiscal_year"] == fy]
        cand = [r for r in fy_rows if matches(r)]
        single = [r for r in cand if not r["has_multiple_dimensions"]]
        related = [r for r in fy_rows if not matches(r) and is_related(r)]
        if single:
            out[ticker] = _choose(single, matches)
        elif cand:
            out[ticker] = ("multi_dimension_only", {})
        elif related:
            labels = sorted({r["segment_label"] for r in related})
            out[ticker] = ("different_member", {"labels": labels})
        else:
            older = [r for r in rows if r["fiscal_year"] < fy and matches(r)]
            _finish(out, ticker, fy_rows, older)
    return out


def ranked_of(outcomes: dict) -> list[tuple[str, float]]:
    """The ranked (ticker, value) pairs, largest first (values are distinct in the tests)."""
    pairs = [(t, d["value"]) for t, (o, d) in outcomes.items() if o == "ranked"]
    return sorted(pairs, key=lambda p: (-p[1], p[0]))


def exclusions_of(outcomes: dict) -> dict[str, tuple[str, dict]]:
    return {t: od for t, od in outcomes.items() if od[0] != "ranked"}
