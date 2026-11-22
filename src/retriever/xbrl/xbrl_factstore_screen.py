"""Cross-filer `screen` step — the ranking/screening analogue of
`xbrl_factstore_lookup` (design doc §2.2; new for the NUMERIC_SCREEN intent).

Where `XbrlFactstoreLookup` resolves *one* company's fact, `XbrlFactstoreScreen`
answers "which companies…" questions: it runs **one** deterministic SQL over the
appropriate `mv_*` view across *all* covered filers and returns a *ranked list*
of companies, each row carrying its own audited `fact_id`/citation so the
synthesizer can cite per company and the faithfulness verifier can check each
statement against that row's own value.

View selected from the plan `screen` step's `dimension` / `metric_kind`, each
modeled on the canonical query in `scripts/xbrl/analyst_queries.sql`:

  * geographic slice  (`dimension.geography`)      -> mv_geographic_facts  (§4b/§4c)
  * segment slice     (`dimension.segment`)        -> mv_segment_matrix    (§3c)
  * derived ratio     (`metric_kind: derived:<M>`) -> mv_derived_metrics   (§7a-d)
  * flat total metric (`dimension: NONE`)          -> mv_financial_facts   (§1b/§5b/§5c)

Per-filer de-duplication: each per-view query takes the latest fiscal year *<=*
the requested `fy` per ticker (`DISTINCT ON (ticker) ORDER BY ticker, fiscal_year
DESC, …`). That tells the screen each filer's latest year, but only rows FOR `fy`
are ranked (`select_fiscal_year`): a filer whose latest annual figure is older is
returned in `excluded`, never ranked on the older year as if it were `fy`
(docs/specs/screen-exact-fiscal-year.md). The segment and geographic queries are the
exception: they return every row, and the screen picks one member per filer in Python
and states why every other filer is not ranked (docs/specs/segment-lookup-screens.md).
The flat query also returns every candidate row of each filer's latest year, and
`select_preferred_concept` picks one per filer with the lookup's own
`family_resolution.candidate_order` (docs/specs/screen-candidate-order.md): the screen
and a lookup cite the same fact for one filer, family and year. For a defined family
(`Debt`, `DeferredRevenue`) `select_screen_picks` keeps every filer at the row
`family_resolution.measure_column` names, and the filer is ranked by its S4 measure
(docs/specs/s4-screen-defined-members.md).
Ranking/threshold/top-N are then applied
in Python on the chosen metric, over every filer: no query carries a row limit
(docs/specs/screen-full-universe.md). Connection params come from the same `XBRL_DB_*` env vars as
`XbrlFactstoreLookup`.
"""

from __future__ import annotations

import collections.abc
import functools
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.defined_measure import (
    DEFINED_FAMILIES,
    defined_measure,
    screen_member,
)
from src.retriever.xbrl.defined_measure_rows import (
    defined_rules,
    measure_lines,
    read_filing_rows,
)
from src.retriever.xbrl.entity_sectors import load_universe_groups
from src.retriever.xbrl.family_resolution import (
    ROLE_PRIMARY,
    candidate_order,
    measure_column,
    row_role,
)
from src.retriever.xbrl.named_company_screen import NAMED, member_list
from src.retriever.xbrl.reporting_currency import choose_lookup_unit
from src.retriever.xbrl.restatement import (
    dimension_identity,
    latest_dimensional_restatement,
    latest_restatement,
)
from src.retriever.xbrl.screen_disclosure import (
    COVERED as COVERED_UNIVERSE,
)
from src.retriever.xbrl.screen_disclosure import (
    build_screen_disclosure,
    resolve_universe_members,
    stated_period_end,
)
from src.retriever.xbrl.xbrl_calculator_tool import DIMENSIONLESS_UNITS, is_currency
from src.retriever.xbrl.xbrl_factstore_lookup import (
    INSTANT_CONCEPT_FAMILIES,
    Citation,
    XbrlFactstoreLookup,
    _emit_sql_event,
    _like_escape,
    compact_label,
    coverage_columns_present,
    member_label_key,
    publish_gate_clause,
)
from src.retriever.xbrl.year_length_answers import screen_year_lengths

logger = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _cached_universe_groups() -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups = load_universe_groups()["groups"]
    return tuple((gid, tuple(members)) for gid, members in sorted(groups.items()))


def universe_groups() -> dict[str, list[str]]:
    """The named universe groups a screen may rank (``configs/eval/v2/groups.yaml``).

    Read once per process; a malformed file raises (``load_universe_groups``).

    Returns:
        Group id to member tickers.
    """
    return {gid: list(members) for gid, members in _cached_universe_groups()}

# Common geography name/alias -> ISO-2 / region code as stored in
# mv_geographic_facts.geo_code. Two-letter uppercase input is used as-is.
GEO_NAME_TO_CODE: dict[str, str] = {
    "china": "CN",
    "prc": "CN",
    "mainland china": "CN",
    "united states": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "america": "US",
    "taiwan": "TW",
    "japan": "JP",
    "korea": "KR",
    "south korea": "KR",
    "germany": "DE",
    "singapore": "SG",
    "india": "IN",
    "eu": "EU",
}

# Regions a geographic screen resolves by the filer's own member label, never by
# geo_code: request member key -> region, and region -> the member keys that match it
# (docs/specs/segment-lookup-screens.md, Requirement 5).
REGION_OF_REQUEST: dict[str, str] = {
    "europe": "europe",
    "emea": "emea",
    "europemiddleeastandafrica": "emea",
}
REGION_MEMBER_KEYS: dict[str, frozenset[str]] = {
    "europe": frozenset({"europe"}),
    "emea": frozenset({"emea", "europemiddleeastandafrica"}),
}
# Compact-label words that make a member related to a region screen.
_REGION_RELATED_WORDS = ("europe", "emea")

# The axes a segment screen ranks (segment-lookup-screens Requirement 2): business
# segments and products, never investees, customers or a secondary categorisation.
SEGMENT_SCREEN_AXES = ("ProductOrServiceAxis", "StatementBusinessSegmentsAxis")

# SQL forms of an axis's local name and of a label's compact form (`compact_label`).
_AXIS_LOCAL_SQL = "regexp_replace({col}, '^.*[:}}]', '')"
_COMPACT_SQL = (
    "regexp_replace(replace(replace(lower({col}), '[member]', ' '), '&', ' and '),"
    " '[^a-z0-9]', '', 'g')"
)


def select_fiscal_year(
    rows: collections.abc.Sequence[dict], fy: int
) -> tuple[list[dict], list[dict]]:
    """Keep the rows for fiscal year *fy*; list the filers that have only earlier years.

    A screen for fiscal year N must not rank a filer on an older year's figure
    (docs/specs/screen-exact-fiscal-year.md).

    Args:
        rows: Per-filer rows as the screen queries return them, each with ``ticker``
            and ``fiscal_year``.
        fy: The requested fiscal year.

    Returns:
        ``(kept, excluded)``: the rows whose ``fiscal_year`` is *fy*, in input order,
        and one ``{"ticker", "latest_fiscal_year"}`` per filer with no such row,
        sorted by ticker.
    """
    kept = [r for r in rows if r.get("fiscal_year") == fy]
    kept_tickers = {r.get("ticker") for r in kept}
    latest: dict[Any, int | None] = {}
    for r in rows:
        ticker = r.get("ticker")
        if ticker in kept_tickers:
            continue
        year = r.get("fiscal_year")
        prior = latest.get(ticker)
        if ticker not in latest or (year is not None and (prior is None or year > prior)):
            latest[ticker] = year
    excluded = [
        {"ticker": t, "latest_fiscal_year": latest[t]}
        for t in sorted(latest, key=str)
    ]
    return kept, excluded


def split_nil_rows(
    rows: collections.abc.Sequence[dict],
) -> tuple[list[dict], list[dict]]:
    """Set aside the rows whose value is a filed nil (stored as NULL).

    A nil says "nothing is reported here": it is never 0 and never ranked
    (docs/specs/sign-and-nil.md R2).

    Args:
        rows: Per-filer rows as the screen queries return them.

    Returns:
        ``(valued, nil)``: the rows with a value, in input order, and one
        ``{"ticker", "reason": "nil", "fact_id"}`` per nil row, sorted by ticker.
    """
    valued = [r for r in rows if r.get("value") is not None]
    nil = [
        {"ticker": r.get("ticker"), "reason": "nil", "fact_id": str(r.get("fact_id"))}
        for r in rows
        if r.get("value") is None
    ]
    return valued, sorted(nil, key=lambda e: str(e["ticker"]))


def choose_member_row(
    rows: Sequence[dict], is_exact: collections.abc.Callable[[dict], bool]
) -> tuple[str, dict]:
    """One filer's value for a member screen, or why it has none.

    docs/specs/segment-lookup-screens.md, Requirement 3: exact matches, when there are
    any, are the only rows that count; the counted rows must carry one member key and
    one value. A filed nil (value None) is left to the caller's nil handling.

    Args:
        rows: The filer's single-dimension candidate rows at the fiscal year, each with
            ``segment_label``, ``unit``, ``value`` and ``fact_id``.
        is_exact: Whether a row is an exact match.

    Returns:
        ``("currency", {"currency"})`` when a row is in a unit other than USD,
        ``("ambiguous_member", {"labels"})`` for several member keys or values, else
        ``("ranked", {"row", "member_match"})``.
    """
    foreign = sorted(
        {str(r["unit"]) for r in rows if r.get("unit") is not None and r["unit"] != "USD"}
    )
    if foreign:
        return "currency", {"currency": foreign[0]}
    exact = [r for r in rows if is_exact(r)]
    counted = exact or list(rows)
    keys = {member_label_key(r.get("segment_label") or "") for r in counted}
    valued = [r for r in counted if r.get("value") is not None]
    if len(keys) > 1 or len({float(r["value"]) for r in valued}) > 1:
        labels = sorted({str(r.get("segment_label")) for r in counted})
        return "ambiguous_member", {"labels": labels}
    pick = min(valued or counted, key=lambda r: str(r.get("fact_id")))
    return "ranked", {"row": pick, "member_match": "exact" if exact else "contains"}


def _share_of_own_total(
    row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> float | None:
    """A member's value over its own filing's undimensioned total of the period.

    docs/specs/dimensional-s1.md, requirement 4: the total has the same concept local
    name, unit, period start and end, and is one value among its most precise facts.

    Args:
        row: The answering member row.
        rows: The filer's stored rows, any filing.

    Returns:
        The share, or None when the total is missing, inconsistent or zero.
    """
    value = row.get("value")
    if value is None:
        return None
    concept = str(row.get("concept") or "").rsplit(":", 1)[-1]
    totals = [
        r for r in rows
        if r.get("accession_no") == row.get("accession_no")
        and dimension_identity(r) == ()
        and str(r.get("concept") or "").rsplit(":", 1)[-1] == concept
        and r.get("unit") == row.get("unit")
        and str(r.get("period_start")) == str(row.get("period_start"))
        and str(r.get("period_end")) == str(row.get("period_end"))
        and r.get("value") is not None
    ]
    if not totals:
        return None
    # As the views compute it: the most precise facts (no decimals = exact) must agree.
    precision = [float("inf") if t.get("decimals") is None else t["decimals"] for t in totals]
    top = max(precision)
    values = {float(t["value"]) for t, p in zip(totals, precision, strict=True) if p == top}
    if len(values) != 1:
        return None
    total = values.pop()
    return float(value) / total if total else None


def _member_duration_kind(concept_family: str, fiscal_period: str, ytd: bool) -> str | None:
    """The ``duration_kind`` a segment or geographic screen ranks (period-disclosure R2).

    A 10-Q files a quarter and a year-to-date figure under one fiscal period, and a 10-K
    can file quarter-length member facts; the views' per-filer pick (largest value) would
    otherwise rank the longer one. Instant families rank instants.

    Args:
        concept_family: The screened family.
        fiscal_period: ``FY``, ``Q1``, ``Q2`` or ``Q3``.
        ytd: The cumulative figure (half or nine months) of a Q2 or Q3.

    Returns:
        The kind to pin, or None for an unknown period (no pin).
    """
    if concept_family in INSTANT_CONCEPT_FAMILIES:
        return "instant"
    return XbrlFactstoreLookup._derive_duration_kind(fiscal_period, ytd)


def _latest_year(rows: Sequence[dict]) -> int | None:
    years: list[int] = [int(r["fiscal_year"]) for r in rows if r.get("fiscal_year") is not None]
    return max(years) if years else None


def _by_ticker(rows: Sequence[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("ticker") is not None:
            out.setdefault(str(r["ticker"]), []).append(r)
    return out


def select_preferred_concept(
    rows: collections.abc.Sequence[dict],
) -> tuple[list[dict], list[dict]]:
    """One row per filer, chosen as a lookup chooses; filers that may not rank apart.

    See docs/specs/screen-candidate-order.md, requirement 1 (and screen-concept-rank.md).
    Per ticker, the rows of the filer's latest ``fiscal_year`` are ordered by
    ``family_resolution.candidate_order``, the lookup's one rule (primary before
    substitute, later period, presented line before a notes-only fact, rank, valued,
    precision, fact id). A primary first row is kept as it is. Otherwise the filer is
    listed apart: with the concept of its first row when that is a stated substitute (a
    screen never ranks one), or, when no row may answer (components only), with the
    concept of its lowest-ranked row (larger ``fact_id`` on a tie). The result does not
    depend on the order of ``rows``.

    Args:
        rows: Candidate rows of the flat screen query, several per filer and any years,
            with ``concept_pref_rank``, ``concept_role``, ``presented``, ``decimals``,
            ``period_end``, ``value`` and ``fact_id``.

    Returns:
        ``(kept, component_only)``: the picked row per ticker (the input dict), sorted by
        ticker, and one ``{"ticker", "fiscal_year", "concept"}`` per filer listed apart,
        sorted by ticker. Every input ticker is in exactly one of them.
    """
    kept: list[dict] = []
    component_only: list[dict] = []
    for t, own in _latest_year_rows(rows):
        order = candidate_order(own)
        if order and row_role(own[order[0]]) == ROLE_PRIMARY:
            kept.append(own[order[0]])
            continue
        if order:
            first = own[order[0]]
        else:
            first = min(own, key=_lowest_rank_key)
        component_only.append(
            {"ticker": t, "fiscal_year": first.get("fiscal_year"),
             "concept": first.get("concept")}
        )
    return kept, component_only


def select_screen_picks(
    rows: collections.abc.Sequence[dict], concept_family: str
) -> tuple[list[dict], list[dict]]:
    """The flat screen's pick per filer; for a defined family, every filer's measure column.

    See docs/specs/s4-screen-defined-members.md, requirement 2. A family outside
    ``DEFINED_FAMILIES`` is ``select_preferred_concept`` exactly. For ``Debt`` and
    ``DeferredRevenue`` no filer is listed apart: per ticker, the rows of its latest
    ``fiscal_year`` give the row ``family_resolution.measure_column`` names, which only
    names the filing and date the S4 measure is built from.

    Args:
        rows: Candidate rows of the flat screen query, several per filer and any years.
        concept_family: The screened family.

    Returns:
        ``(kept, component_only)`` as ``select_preferred_concept`` returns them; for a
        defined family, ``component_only`` is empty and every input ticker is kept once.
    """
    if concept_family not in DEFINED_FAMILIES:
        return select_preferred_concept(rows)
    kept: list[dict] = []
    for _t, own in _latest_year_rows(rows):
        column = measure_column(own)
        if column is not None:
            kept.append(own[column])
    return kept, []


def _latest_year_rows(
    rows: collections.abc.Sequence[dict],
) -> list[tuple[Any, list[dict]]]:
    """Per ticker (sorted as strings), the rows of the filer's latest ``fiscal_year``.

    Rows without a ``fiscal_year`` are the filer's rows only when none has one.

    Args:
        rows: Candidate rows of the flat screen query.

    Returns:
        ``(ticker, rows)`` pairs, the rows in input order.
    """
    by_ticker: dict[Any, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r.get("ticker"), []).append(r)
    out: list[tuple[Any, list[dict]]] = []
    for t in sorted(by_ticker, key=str):
        mine = by_ticker[t]
        years = [int(r["fiscal_year"]) for r in mine if r.get("fiscal_year") is not None]
        year = max(years) if years else None
        own = [r for r in mine if (r.get("fiscal_year") is None) == (year is None)
               and (year is None or int(r["fiscal_year"]) == year)]
        out.append((t, own))
    return out


def _lowest_rank_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Lowest rank first (a missing rank last), then the larger ``fact_id``."""
    rank = row.get("concept_pref_rank")
    return ((1, 0) if rank is None else (0, rank), -(row.get("fact_id") or 0))


def select_reporting_currency(
    rows: Sequence[dict],
    observations: Mapping[str, Sequence[tuple[str | None, str | None]]],
) -> tuple[list[dict], list[dict]]:
    """Keep the rows of filers that report in USD; list the filers that report otherwise.

    A screen never ranks a filer on a convenience translation
    (docs/specs/screen-reporting-currency.md). A filer's reporting currency is the unit
    ``choose_lookup_unit`` returns for its observations with no requested unit.

    Args:
        rows: Per-filer USD rows as the flat screen query returns them, each with
            ``ticker``.
        observations: Ticker -> ``(unit, period_end)`` pairs of every row the flat
            screen would consider for that filer and fiscal year, in any unit and at any
            period, period ends as ISO dates.

    Returns:
        ``(kept, excluded)``: the rows, in input order, whose ticker has a USD reporting
        currency, no single reporting currency, or no entry in *observations*; and one
        ``{"ticker", "reason": "currency", "currency"}`` per other ticker, sorted by
        ticker.
    """
    foreign: dict[str, str] = {}
    for ticker in {r.get("ticker") for r in rows}:
        if ticker not in observations:
            continue
        unit = choose_lookup_unit(observations[ticker])
        if unit is not None and unit != "USD":
            foreign[ticker] = unit
    kept = [r for r in rows if r.get("ticker") not in foreign]
    excluded = [
        {"ticker": t, "reason": "currency", "currency": foreign[t]}
        for t in sorted(foreign, key=str)
    ]
    return kept, excluded


def derived_metric_unit(metric_name: str, component_units: Sequence[str | None]) -> str | None:
    """The unit of a derived metric from its component facts' units.

    See docs/specs/units-currency-integrity.md (Requirement 1).

    Args:
        metric_name: A `mv_derived_metrics.metric_name`.
        component_units: The stored unit of every component fact, alternatives included.

    Returns:
        The currency (monetary metrics), ``"<currency>/shares"`` (per-share metrics) or
        ``"pure"`` (ratios); None when the units do not make one coherent unit.
    """
    units = list(component_units)
    if not units or any(u is None for u in units) or metric_name not in DERIVED_METRICS:
        return None
    # A dimensionless unit ("Rate") reads exactly like "pure".
    units = ["pure" if u in DIMENSIONLESS_UNITS else u for u in units]
    currencies = {u for u in units if u is not None and is_currency(u)}
    if len(currencies) > 1:
        return None
    if metric_name in MONETARY_DERIVED_METRICS:
        if len(currencies) == 1 and all(u in currencies for u in units):
            return next(iter(currencies))
        return None
    if metric_name in PER_SHARE_DERIVED_METRICS:
        if (
            len(currencies) == 1
            and "shares" in units
            and all(u in currencies or u == "shares" for u in units)
        ):
            return f"{next(iter(currencies))}/shares"
        return None
    return "pure"


def _component_ids(component_fact_ids: Any) -> list[int]:
    """Every fact id a derived row cites: a JSON object of ids or id lists, or a list."""
    if isinstance(component_fact_ids, str):
        try:
            component_fact_ids = json.loads(component_fact_ids)
        except ValueError:
            return []
    values = (
        list(component_fact_ids.values())
        if isinstance(component_fact_ids, Mapping)
        else component_fact_ids
    )
    if not isinstance(values, (list, tuple)):
        return []
    out: list[int] = []
    for v in values:
        for x in v if isinstance(v, (list, tuple)) else [v]:
            if isinstance(x, bool):
                continue
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, str) and x.strip().isdigit():
                out.append(int(x))
    return out


def derived_unit_exclusion(
    ticker: Any, unit: str | None, component_units: Sequence[str | None]
) -> dict | None:
    """Why a derived row with *unit* is not ranked, or None when it is ranked.

    A derived screen ranks USD amounts, USD per share and ratios
    (docs/specs/units-currency-integrity.md, Requirement 2).

    Args:
        ticker: The row's ticker.
        unit: `derived_metric_unit` of its components.
        component_units: The components' stored units.

    Returns:
        ``{"ticker", "reason": "unit_mismatch", "units"}`` for no coherent unit,
        ``{"ticker", "reason": "currency", "currency"}`` for a non-USD amount, else None.
    """
    if unit is None:
        distinct = sorted(set(component_units), key=lambda u: (u is None, u or ""))
        return {"ticker": ticker, "reason": "unit_mismatch", "units": distinct}
    currency = unit.split("/", 1)[0]
    if unit != "pure" and currency != "USD":
        return {"ticker": ticker, "reason": "currency", "currency": currency}
    return None


def _iso_period_end(end: Any) -> str | None:
    """A stored period end as an ISO string for ``choose_lookup_unit``; None stays None."""
    if end is None:
        return None
    return end.isoformat() if hasattr(end, "isoformat") else str(end)


def normalize_metric_name(name: str) -> str:
    """A derived metric name's comparable form (docs/specs/screen-metric-names.md).

    Args:
        name: A metric name as planned or as stored.

    Returns:
        Lowercase alphanumerics without "yoy"/"yearoveryear" and one trailing
        "percentage"/"percent"/"pct".
    """
    s = re.sub(r"[^a-z0-9]", "", name.lower())
    s = s.replace("yearoveryear", "").replace("yoy", "")
    for suffix in ("percentage", "percent", "pct"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def resolve_derived_metric(
    name: str,
    known: collections.abc.Collection[str],
    aliases: collections.abc.Mapping[str, str],
) -> str | None:
    """The known derived metric a planned name refers to.

    Args:
        name: The planned ``derived:<Name>`` name.
        known: The metric names that exist.
        aliases: Normalised synonym → canonical name.

    Returns:
        The canonical name, or None when it does not resolve to exactly one.
    """
    if name in known:
        return name
    key = normalize_metric_name(name)
    hits = [k for k in known if normalize_metric_name(k) == key]
    if hits:
        return hits[0] if len(hits) == 1 else None
    target = aliases.get(key)
    return target if target in known else None


# mv_derived_metrics.metric_name values. Mirrors scripts/ddl/mv/34_mv_derived_metrics.sql:
# add a name here when a metric is added there, or screens cannot reach it.
DERIVED_METRICS = frozenset({
    "AssetTurnover", "BookValuePerShare", "CashRatio", "CurrentRatio",
    "DaysInventoryOutstanding", "DaysSalesOutstanding", "DebtToEquity", "DilutedEpsDerived",
    "EBITDA", "EBITDAMarginPct", "EffectiveTaxRate", "FreeCashFlow", "FreeCashFlowMarginPct",
    "FreeCashFlowPerShare", "GrossMarginPct", "InterestCoverage", "InventoryTurnover",
    "NetDebt", "NetIncomeGrowthPct", "NetMarginPct", "OperatingMarginPct", "QuickRatio",
    "ReturnOnAssetsPct", "ReturnOnEquityPct", "ReturnOnInvestedCapitalPct",
    "Revenue3YrCagrPct", "RevenueGrowthPct", "RnDIntensityPct", "SbcAdjOperatingMarginPct",
})

# Derived metrics in a currency, or a currency per share; every other one is a ratio
# (docs/specs/units-currency-integrity.md, Requirement 1).
MONETARY_DERIVED_METRICS = frozenset({"EBITDA", "FreeCashFlow", "NetDebt"})
PER_SHARE_DERIVED_METRICS = frozenset(
    {"BookValuePerShare", "DilutedEpsDerived", "FreeCashFlowPerShare"}
)

# Synonyms the planner uses that normalisation alone does not map, keyed by
# normalize_metric_name() form (docs/specs/screen-metric-names.md).
DERIVED_METRIC_ALIASES: dict[str, str] = {
    "netprofitmargin": "NetMarginPct",
    "profitmargin": "NetMarginPct",
    "netincomemargin": "NetMarginPct",
    "salesgrowth": "RevenueGrowthPct",
    "revenuegrowthrate": "RevenueGrowthPct",
    "netincomegrowthrate": "NetIncomeGrowthPct",
    "rdintensity": "RnDIntensityPct",
    "researchanddevelopmentintensity": "RnDIntensityPct",
    "roe": "ReturnOnEquityPct",
    "roa": "ReturnOnAssetsPct",
    "roic": "ReturnOnInvestedCapitalPct",
    "fcf": "FreeCashFlow",
    "fcfmargin": "FreeCashFlowMarginPct",
}


class ScreenStatus(Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


@dataclass
class ScreenResult:
    """Outcome of one cross-filer `screen` step."""

    step_id: str | None
    status: ScreenStatus
    rows: list[dict] = field(default_factory=list)
    resolution_notes: list[str] = field(default_factory=list)
    error: str | None = None
    # Filers left out because their latest figure predates the requested fiscal
    # year: {"ticker", "latest_fiscal_year"} (docs/specs/screen-exact-fiscal-year.md).
    excluded: list[dict] = field(default_factory=list)
    # The universe ranked, every member not ranked and why, and the ranked filers' period
    # ends (docs/specs/screen-universe.md); None for an ERROR before the universe resolved.
    disclosure: dict | None = None
    # {"limit", "rows_before_cap", "returned"} when an explicit row_limit removed ranked
    # rows (docs/specs/screen-full-universe.md); None when nothing was cut.
    row_cap: dict | None = None
    # A growth screen's ranked rows whose two compared years differ in length
    # (docs/specs/period-year-length-answers.md, `screen_year_lengths` entries).
    year_lengths: list[dict] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return self.status == ScreenStatus.OK

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "step_id": self.step_id,
            "status": self.status.value,
            "n_rows": len(self.rows),
            "rows": self.rows,
            "excluded": self.excluded,
            "resolution_notes": self.resolution_notes,
            "error": self.error,
        }
        if self.disclosure is not None:
            d["disclosure"] = self.disclosure
        if self.row_cap is not None:
            d["row_cap"] = self.row_cap
        if self.year_lengths:
            d["year_lengths"] = self.year_lengths
        return d


class XbrlFactstoreScreen:
    """Resolves a single `screen` plan step to a ranked list of company rows.

    Example:
        >>> screen = XbrlFactstoreScreen.from_env()
        >>> step = {"id": "s1", "op": "screen", "concept_family": "Revenue",
        ...         "fy": 2024, "dimension": {"geography": "China"},
        ...         "metric_kind": "value", "order": "desc", "top_n": None}
        >>> [r["ticker"] for r in screen.screen(step).rows]
    """

    def __init__(
        self,
        db_conn_params: dict | None = None,
        row_limit: int | None = None,
    ):
        """Creates a screen.

        Args:
            db_conn_params: psycopg2 connection parameters.
            row_limit: Cap on the rows returned, applied after ranking, ``top_n`` and
                ``min_threshold`` and reported as ``row_cap``; None (the default) for no
                cap. No query is ever capped (docs/specs/screen-full-universe.md).

        Raises:
            ValueError: ``row_limit`` is not None or a positive int.
        """
        if row_limit is not None and (
            isinstance(row_limit, bool) or not isinstance(row_limit, int) or row_limit < 1
        ):
            raise ValueError(f"row_limit must be None or a positive int, got {row_limit!r}")
        self._db_conn_params = db_conn_params or {}
        self.row_limit = row_limit

    @classmethod
    def from_env(
        cls, dotenv_path: str | None = None, **kwargs
    ) -> XbrlFactstoreScreen:
        load_dotenv(dotenv_path)
        from src.db.conn import conn_params

        return cls(db_conn_params=conn_params(), **kwargs)

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def screen(self, step: dict) -> ScreenResult:
        """Resolve one `screen` step to a ranked `ScreenResult`."""
        step_id = step.get("id")
        if step.get("op") != "screen":
            return ScreenResult(
                step_id,
                ScreenStatus.ERROR,
                error=f"not a screen step: op={step.get('op')!r}",
            )

        fy = step.get("fy")
        if fy is None:
            return ScreenResult(
                step_id,
                ScreenStatus.ERROR,
                error="screen step missing required field 'fy'",
            )

        concept_family = step.get("concept_family") or "Revenue"
        metric_kind = str(step.get("metric_kind") or "value")
        dimension = step.get("dimension", "NONE")
        # Period selection (10-Q support). Default 'FY' screens across annual
        # filings, preserving the pre-quarterly behavior; a quarter screen sets
        # fiscal_period='Q1'|'Q2'|'Q3' (+ ytd for the cumulative figure).
        fiscal_period = (step.get("fiscal_period") or "FY").upper()
        ytd = bool(step.get("ytd", False))
        notes: list[str] = []

        # The universe: every corpus filer, a named group (docs/specs/screen-universe.md) or
        # a member list, ranked as the group "named" of its tickers
        # (docs/specs/named-company-screen.md R6). An unknown or unreadable one is an error,
        # never a silent ranking of every filer.
        universe = step.get("universe")
        if universe is None:
            universe = COVERED_UNIVERSE
        groups = universe_groups()
        listed = member_list(universe)
        if listed is not None:
            universe, groups = NAMED, {NAMED: listed}
        if not isinstance(universe, str) or (
            universe != COVERED_UNIVERSE and universe not in groups
        ):
            return ScreenResult(
                step_id,
                ScreenStatus.ERROR,
                error=(
                    f"unknown screen universe {universe!r}; valid: "
                    f"{', '.join([COVERED_UNIVERSE, *sorted(groups)])}"
                ),
            )
        component_only: list[dict] = []
        currency: list[dict] = []
        # Segment and geographic screens: why each filer they do not rank is not ranked
        # (docs/specs/segment-lookup-screens.md, Requirement 4).
        member_reasons: list[dict] = []
        # Derived screens: fact id -> stored period of each fiscal-year-fy component fact,
        # for the year-length entries (docs/specs/period-year-length-answers.md).
        periods: dict[int, dict] = {}

        try:
            with self._connect() as conn:
                roster = self._query_roster(conn, int(fy))
                if metric_kind.startswith("derived:"):
                    planned = metric_kind.split(":", 1)[1].strip()
                    metric = resolve_derived_metric(
                        planned, DERIVED_METRICS, DERIVED_METRIC_ALIASES
                    )
                    if metric is None:
                        # Unknown name: no rows, so the step is NOT_FOUND, with the
                        # valid names in the trace rather than a silent miss.
                        notes.append(
                            f"unknown derived metric {planned!r}; valid: "
                            f"{', '.join(sorted(DERIVED_METRICS))}"
                        )
                        raw = []
                    else:
                        if metric != planned:
                            notes.append(f"derived metric {planned!r} resolved to {metric!r}")
                        raw = self._query_derived(
                            conn, metric, fy, fiscal_period, ytd, notes, currency, periods
                        )
                elif isinstance(dimension, dict) and any(
                    k in dimension for k in ("geography", "geo", "country", "region")
                ):
                    raw = self._query_geographic(
                        conn, dimension, concept_family, int(fy), fiscal_period, notes,
                        member_reasons, ytd=ytd,
                    )
                elif isinstance(dimension, dict) and any(
                    k in dimension for k in ("segment", "product", "business")
                ):
                    raw = self._query_segment(
                        conn, dimension, concept_family, int(fy), fiscal_period, notes,
                        member_reasons, ytd=ytd,
                    )
                else:
                    raw = self._query_flat(
                        conn, concept_family, fy, fiscal_period, ytd, notes, component_only,
                        currency, member_reasons,
                    )
        except Exception as e:  # pragma: no cover - exercised only with a broken DB
            logger.error(f"factstore screen failed for step {step_id!r}: {e}")
            return ScreenResult(step_id, ScreenStatus.ERROR, error=str(e))

        # Rank only the universe's members.
        members = resolve_universe_members(universe, roster, groups)
        member_set = set(members)
        raw = [r for r in raw if r.get("ticker") in member_set]
        component_only = [c for c in component_only if c.get("ticker") in member_set]
        currency = [c for c in currency if c.get("ticker") in member_set]
        member_reasons = [c for c in member_reasons if c.get("ticker") in member_set]
        if universe != COVERED_UNIVERSE:
            notes.append(f"universe {universe!r}: {len(members)} member(s)")

        # Rank only fiscal-year-fy figures; a filer whose latest is older is listed as
        # excluded, never ranked on that older year.
        raw, excluded = select_fiscal_year(raw, int(fy))
        # A filed nil (xsi:nil, stored value NULL) reports nothing: never ranked,
        # disclosed with its fact (docs/specs/sign-and-nil.md R2).
        raw, nil_rows = split_nil_rows(raw)
        if nil_rows:
            notes.append(
                "not ranked: filed nil (nothing reported) for "
                + ", ".join(f"{e['ticker']} (fact {e['fact_id']})" for e in nil_rows)
            )
        # The member screens find their period_not_filed filers themselves; `excluded`
        # lists them as before.
        excluded = sorted(
            excluded + [
                {"ticker": c["ticker"], "latest_fiscal_year": c.get("latest_fiscal_year")}
                for c in member_reasons if c.get("reason") == "period_not_filed"
            ],
            key=lambda e: str(e["ticker"]),
        )
        member_reasons = [c for c in member_reasons if c.get("reason") != "period_not_filed"]
        if member_reasons:
            notes.append(
                "not ranked: "
                + ", ".join(
                    f"{c['ticker']} ({c['reason']}"
                    + (f": {', '.join(c['labels'])}" if c.get("labels") else "")
                    + (f": {c['currency']}" if c.get("currency") else "")
                    + ")"
                    for c in member_reasons
                )
            )
        given = nil_rows + [
            {"ticker": e["ticker"], "reason": "period_not_filed",
             "latest_fiscal_year": e["latest_fiscal_year"]}
            for e in excluded
        ] + [
            {"ticker": c["ticker"], "reason": "component_only",
             "fiscal_year": c.get("fiscal_year"), "concept": c.get("concept")}
            for c in component_only
        ] + [dict(c) for c in currency] + [dict(c) for c in member_reasons]
        # Every ranked member in rank order, before top_n and min_threshold, with its
        # stored period end; built from copies so the returned rows keep their own ranks.
        full = self._rank_and_filter(
            [dict(r) for r in raw], {**step, "top_n": None, "min_threshold": None},
            metric_kind, [],
        )
        disclosure = build_screen_disclosure(
            universe, int(fy), members, roster, full, given
        )
        if excluded:
            notes.append(
                f"excluded {len(excluded)} filer(s) with no FY{fy} figure: "
                + ", ".join(
                    f"{e['ticker']} (latest FY{e['latest_fiscal_year']})" for e in excluded
                )
            )
        if not raw:
            notes.append(
                f"no rows for concept_family={concept_family}, fy={fy}, "
                f"metric_kind={metric_kind}, dimension={dimension!r}"
            )
            return ScreenResult(
                step_id,
                ScreenStatus.NOT_FOUND,
                rows=[],
                resolution_notes=notes,
                excluded=excluded,
                disclosure=disclosure,
            )

        rows = self._rank_and_filter(raw, step, metric_kind, notes)
        # An explicit result-size cap applies to the ranked rows only, and is reported
        # (docs/specs/screen-full-universe.md, Requirement 6).
        row_cap: dict | None = None
        if self.row_limit is not None and len(rows) > self.row_limit:
            row_cap = {
                "limit": self.row_limit,
                "rows_before_cap": len(rows),
                "returned": self.row_limit,
            }
            notes.append(
                f"row_limit {self.row_limit}: returned the top {self.row_limit} "
                f"of {len(rows)} ranked row(s)"
            )
            rows = rows[: self.row_limit]
        for r in rows:
            if "period_end" in r:  # flat rows: state the period's last day
                r["period_end"] = stated_period_end(r["period_end"])
        year_lengths = screen_year_lengths(rows, periods) if periods else []
        return ScreenResult(
            step_id, ScreenStatus.OK, rows=rows, resolution_notes=notes, excluded=excluded,
            disclosure=disclosure, row_cap=row_cap, year_lengths=year_lengths,
        )

    # ------------------------------------------------------------------
    # Ranking / filtering (pure)
    # ------------------------------------------------------------------

    def _rank_and_filter(
        self, raw: list[dict], step: dict, metric_kind: str, notes: list[str]
    ) -> list[dict]:
        order = str(step.get("order") or "desc").lower()
        reverse = order != "asc"
        sort_key = "pct_of_total" if metric_kind == "pct_of_total" else "value"

        def _key(r: dict) -> float:
            v = r.get(sort_key)
            return float(v) if v is not None else float("-inf")

        ranked = sorted(raw, key=_key, reverse=reverse)

        min_threshold = step.get("min_threshold")
        if min_threshold is not None:
            before = len(ranked)
            kept = [
                r
                for r in ranked
                if r.get(sort_key) is not None
                and float(r[sort_key]) >= float(min_threshold)
            ]
            if kept:
                ranked = kept
                notes.append(
                    f"min_threshold {min_threshold} on {sort_key}: kept {len(kept)}/{before}"
                )
            else:
                # A threshold that excludes EVERY filer (usually an over-aggressive
                # cutoff inferred from a vague "significant"/"major") would return
                # an empty screen — worse than the ranked list. Ignore it and
                # return the full ranking so the caller can judge significance.
                notes.append(
                    f"min_threshold {min_threshold} on {sort_key} excluded all {before} "
                    f"companies; ignoring threshold and returning the full ranking"
                )

        top_n = step.get("top_n")
        if isinstance(top_n, int) and top_n > 0:
            ranked = ranked[:top_n]

        for i, r in enumerate(ranked, start=1):
            r["rank"] = i
        return ranked

    # ------------------------------------------------------------------
    # Per-view queries (each: latest FY <= fy per ticker)
    # ------------------------------------------------------------------

    def _query_geographic(
        self,
        conn,
        dimension: dict,
        concept_family: str,
        fy: int,
        fiscal_period: str,
        notes: list[str],
        reasons_out: list[dict],
        *,
        ytd: bool = False,
    ) -> list[dict]:
        """The ranked rows of a geographic screen; every other filer's reason to ``reasons_out``.

        A country is matched by ``geo_code`` (the largest USD value per filer is
        ranked); Europe and EMEA by the filer's own member label
        (docs/specs/segment-lookup-screens.md, Requirement 5).
        """
        geo_raw = str(
            dimension.get("geography")
            or dimension.get("geo")
            or dimension.get("country")
            or dimension.get("region")
            or ""
        ).strip()
        region = REGION_OF_REQUEST.get(member_label_key(geo_raw))
        if region is not None:
            return self._query_region(
                conn, region, concept_family, fy, fiscal_period, notes, reasons_out, ytd=ytd
            )
        geo_code = (
            geo_raw.upper()
            if len(geo_raw) == 2
            else GEO_NAME_TO_CODE.get(geo_raw.lower())
        )
        if not geo_code:
            notes.append(f"could not resolve geography {geo_raw!r} to a geo_code")
            return []
        notes.append(
            f"geographic screen: geo_code={geo_code}, concept_family={concept_family}, fiscal_period={fiscal_period}"
        )
        # The country's name, for members that name it outside the SEC country taxonomy.
        name = geo_raw if len(geo_raw) != 2 else next(
            (n for n, c in GEO_NAME_TO_CODE.items() if c == geo_code), ""
        )
        place = compact_label(name)
        params = {
            "geo_code": geo_code,
            "concept_family": concept_family,
            "fy": fy,
            "fiscal_period": fiscal_period,
            "duration_kind": _member_duration_kind(concept_family, fiscal_period, ytd),
        }
        matching = _by_ticker(self._fetch(
            conn, self._geographic_sql(conn, "geo_code = %(geo_code)s", "<="), params
        ))
        at_fy = _by_ticker([
            r for r in self._fetch(conn, self._geographic_sql(conn, "", "="), params)
            if r.get("fiscal_year") == fy
        ])
        out: list[dict] = []
        for ticker in sorted(set(matching) | set(at_fy)):
            cand = [r for r in matching.get(ticker, []) if r.get("fiscal_year") == fy]
            related = [
                r for r in at_fy.get(ticker, [])
                if place and r.get("geo_code") != geo_code
                and not str(r.get("segment_member") or "").startswith("country:")
                and place in compact_label(r.get("segment_label"))
            ]
            if cand:
                foreign = sorted({str(r.get("unit")) for r in cand if r.get("unit") != "USD"})
                if foreign:
                    reasons_out.append(
                        {"ticker": ticker, "reason": "currency", "currency": foreign[0]}
                    )
                    continue
                valued = [r for r in cand if r.get("value") is not None]
                best = max(valued, key=lambda r: float(r["value"])) if valued else cand[0]
                out.append(self._row(
                    best,
                    label=f"{best.get('geo_name') or geo_code} {concept_family}",
                    fact_id=best.get("fact_id"),
                ))
            elif related:
                reasons_out.append({
                    "ticker": ticker, "reason": "different_member",
                    "labels": sorted({str(r.get("segment_label")) for r in related}),
                })
            elif matching.get(ticker):
                reasons_out.append({
                    "ticker": ticker, "reason": "period_not_filed",
                    "latest_fiscal_year": _latest_year(matching[ticker]),
                })
            else:
                reasons_out.append({"ticker": ticker, "reason": "member_not_found"})
        return self._restate_members(
            conn, out, fy, concept_family,
            _member_duration_kind(concept_family, fiscal_period, ytd), notes,
        )

    def _query_region(
        self,
        conn,
        region: str,
        concept_family: str,
        fy: int,
        fiscal_period: str,
        notes: list[str],
        reasons_out: list[dict],
        *,
        ytd: bool = False,
    ) -> list[dict]:
        """A Europe or EMEA screen, matched by the filer's member label (Requirement 5).

        ``geo_code`` and ``geo_name`` are never read here: for these members they hold
        ``EuropeMember``, ``EMEAMember`` or another filer's region name
        (segment-geo-views, J1).
        """
        keys = REGION_MEMBER_KEYS[region]
        other = frozenset().union(*(k for reg, k in REGION_MEMBER_KEYS.items() if reg != region))
        notes.append(
            f"region screen: {region} by member label ({', '.join(sorted(keys))}), "
            f"concept_family={concept_family}, fiscal_period={fiscal_period}"
        )
        params: dict[str, Any] = {
            "concept_family": concept_family,
            "fy": fy,
            "fiscal_period": fiscal_period,
            "duration_kind": _member_duration_kind(concept_family, fiscal_period, ytd),
            "this_fy": fy,
        }
        # One query: every row of the fiscal year (for member_not_found), and earlier
        # rows only when their label could match or relate (for period_not_filed).
        like = ["fiscal_year = %(this_fy)s"]
        for i, word in enumerate(_REGION_RELATED_WORDS):
            params[f"region_like_{i}"] = f"%{word}%"
            like.append(f"{_COMPACT_SQL.format(col='d.segment_label')} LIKE %(region_like_{i})s")
        scope = "(" + " OR ".join(like) + ")"

        def matches(r: dict) -> bool:
            return member_label_key(r.get("segment_label") or "") in keys

        def is_related(r: dict) -> bool:
            c = compact_label(r.get("segment_label"))
            return member_label_key(r.get("segment_label") or "") in other or any(
                w in c for w in _REGION_RELATED_WORDS
            )

        rows = _by_ticker(self._fetch(conn, self._geographic_sql(conn, scope, "<="), params))
        out: list[dict] = []
        for ticker in sorted(rows):
            mine = rows[ticker]
            fy_rows = [r for r in mine if r.get("fiscal_year") == fy]
            cand = [r for r in fy_rows if matches(r)]
            # Only single-dimension facts rank; `has_multiple_dimensions` is the fact's
            # own column in mv_financial_facts, read through the query's LATERAL.
            single = [r for r in cand if not r.get("has_multiple_dimensions")]
            related = [r for r in fy_rows if not matches(r) and is_related(r)]
            older = [r for r in mine if r.get("fiscal_year") != fy and matches(r)]
            if single:
                outcome, detail = choose_member_row(single, matches)
                if outcome == "ranked":
                    r = detail["row"]
                    row = self._row(r, label=str(r.get("segment_label")), fact_id=r.get("fact_id"))
                    row["member_match"] = "exact"
                    out.append(row)
                else:
                    reasons_out.append({"ticker": ticker, "reason": outcome, **detail})
            elif cand:
                reasons_out.append({"ticker": ticker, "reason": "multi_dimension_only"})
            elif related:
                reasons_out.append({
                    "ticker": ticker, "reason": "different_member",
                    "labels": sorted({str(r.get("segment_label")) for r in related}),
                })
            elif older:
                reasons_out.append({
                    "ticker": ticker, "reason": "period_not_filed",
                    "latest_fiscal_year": _latest_year(older),
                })
            elif fy_rows:
                reasons_out.append({"ticker": ticker, "reason": "member_not_found"})
        return self._restate_members(
            conn, out, fy, concept_family,
            _member_duration_kind(concept_family, fiscal_period, ytd), notes,
        )

    @staticmethod
    def _geographic_sql(conn, predicate: str, year_op: str) -> str:
        """A query of ``mv_geographic_facts`` with each fact's flat member columns.

        The view carries no member label, member QName or dimension count; they are
        read from ``mv_financial_facts`` by ``fact_id``. Every unit is returned: the
        caller states a foreign currency rather than dropping it.

        Args:
            conn: An open connection.
            predicate: An extra trusted predicate ("" for none).
            year_op: ``<=`` (latest year up to ``fy``) or ``=`` (``fy`` only).

        Returns:
            The SQL text, parameters ``concept_family``, ``fy``, ``fiscal_period`` and
            whatever ``predicate`` names.
        """
        extra = f"AND {predicate}" if predicate else ""
        return f"""
            SELECT ticker, cik, fiscal_year, value, unit, pct_of_total,
                   geo_code, geo_name, section_path, fact_id,
                   d.segment_label, d.segment_member, d.has_multiple_dimensions
            FROM   mv_geographic_facts
            CROSS  JOIN LATERAL (
                   SELECT m.segment_label, m.segment_member, m.has_multiple_dimensions
                   FROM   mv_financial_facts m
                   WHERE  m.fact_id = mv_geographic_facts.fact_id) d
            WHERE  concept_family = %(concept_family)s
              AND  fiscal_year   {year_op} %(fy)s
              AND  fiscal_period  = %(fiscal_period)s
              AND  (%(duration_kind)s::text IS NULL OR duration_kind = %(duration_kind)s)
              {extra}
              {publish_gate_clause(conn, "mv_geographic_facts")}
            ORDER  BY ticker, fiscal_year DESC, value DESC NULLS LAST, fact_id
        """

    def _query_segment(
        self,
        conn,
        dimension: dict,
        concept_family: str,
        fy: int,
        fiscal_period: str,
        notes: list[str],
        reasons_out: list[dict],
        *,
        ytd: bool = False,
    ) -> list[dict]:
        """The ranked rows of a segment screen; every other filer's reason to ``reasons_out``.

        Only business-axis, single-dimension rows whose label matches rank, at one
        value per filer (docs/specs/segment-lookup-screens.md, Requirements 2-4). The
        axis, single-dimension and label-containment scoping are SQL predicates; the
        exact-or-containment choice per filer is made here.
        """
        member = (
            str(
                dimension.get("segment")
                or dimension.get("product")
                or dimension.get("business")
                or ""
            )
            .strip()
            .lower()
        )
        key = member_label_key(member)
        if not key:
            notes.append("segment screen missing a member hint")
            return []
        notes.append(
            f"segment screen: member~{member!r} on {', '.join(SEGMENT_SCREEN_AXES)}, "
            f"concept_family={concept_family}, fiscal_period={fiscal_period}"
        )
        params: dict[str, Any] = {
            "concept_family": concept_family,
            "fy": fy,
            "fiscal_period": fiscal_period,
            "duration_kind": _member_duration_kind(concept_family, fiscal_period, ytd),
            "segment_axes": list(SEGMENT_SCREEN_AXES),
            "member_key": key,
            "member_like": f"%{_like_escape(member)}%",
        }

        def exact(r: dict) -> bool:
            return member_label_key(r.get("segment_label") or "") == key

        # The containment match is the SQL `member_key` / `member_like` predicate; only
        # the exact-or-containment choice is made here.
        matching = _by_ticker(
            self._fetch(conn, self._segment_sql(conn, "<=", matched=True), params)
        )
        single = _by_ticker([
            r for r in self._fetch(
                conn, self._segment_sql(conn, "=", matched=True, single=True), params
            )
            if r.get("fiscal_year") == fy
        ])
        present = {
            str(r.get("ticker"))
            for r in self._fetch(conn, self._segment_sql(conn, "=", matched=False), params)
            if r.get("fiscal_year") == fy
        }
        out: list[dict] = []
        for ticker in sorted(set(matching) | set(single) | present):
            mine = matching.get(ticker, [])
            if single.get(ticker):
                outcome, detail = choose_member_row(single[ticker], exact)
                if outcome != "ranked":
                    reasons_out.append({"ticker": ticker, "reason": outcome, **detail})
                    continue
                r = detail["row"]
                label = r.get("segment_label") or member
                row = self._row(r, label=label, fact_id=r.get("fact_id"))
                row["member_match"] = detail["member_match"]
                if detail["member_match"] == "contains":
                    notes.append(
                        f"{ticker}: ranked on member {label!r}, which contains "
                        f"{member!r} (no member named exactly {member!r})"
                    )
                out.append(row)
            elif any(r.get("fiscal_year") == fy for r in mine):
                reasons_out.append({"ticker": ticker, "reason": "multi_dimension_only"})
            elif mine:
                reasons_out.append({
                    "ticker": ticker, "reason": "period_not_filed",
                    "latest_fiscal_year": _latest_year(mine),
                })
            elif ticker in present:
                reasons_out.append({"ticker": ticker, "reason": "member_not_found"})
        return self._restate_members(
            conn, out, fy, concept_family,
            _member_duration_kind(concept_family, fiscal_period, ytd), notes,
        )

    @staticmethod
    def _segment_sql(conn, year_op: str, *, matched: bool, single: bool = False) -> str:
        """A query of ``mv_segment_matrix``, every unit, without a per-filer collapse.

        Args:
            conn: An open connection.
            year_op: ``<=`` (years up to ``fy``) or ``=`` (``fy`` only).
            matched: Only rows on `SEGMENT_SCREEN_AXES` whose compact label contains
                the hint (``segment_axes``; ``member_key``, the hint's key in compact
                alphanumerics, so it carries no LIKE wildcard; ``member_like``, the
                lowercased hint as an escaped containment pattern).
            single: Only single-dimension facts (read from ``mv_financial_facts``).

        Returns:
            The SQL text.
        """
        match = ""
        if matched:
            match = (
                f"AND {_AXIS_LOCAL_SQL.format(col='segment_axis')} = ANY(%(segment_axes)s)"
                # A label that contains the hint as written also contains its key
                # in compact form, so the first test only short-cuts the second.
                " AND (lower(segment_label) LIKE %(member_like)s"
                f" OR {_COMPACT_SQL.format(col='segment_label')}"
                " LIKE '%%' || %(member_key)s || '%%')"
            )
        single_predicate = "AND NOT d.has_multiple_dimensions" if single else ""
        return f"""
            SELECT ticker, cik, fiscal_year, value, unit,
                   pct_of_axis_total AS pct_of_total, segment_axis, segment_label,
                   section_path, fact_id, d.has_multiple_dimensions
            FROM   mv_segment_matrix
            CROSS  JOIN LATERAL (
                   SELECT m.has_multiple_dimensions
                   FROM   mv_financial_facts m
                   WHERE  m.fact_id = mv_segment_matrix.fact_id) d
            WHERE  concept_family = %(concept_family)s
              AND  fiscal_year   {year_op} %(fy)s
              AND  fiscal_period  = %(fiscal_period)s
              AND  (%(duration_kind)s::text IS NULL OR duration_kind = %(duration_kind)s)
              {match}
              {single_predicate}
              {publish_gate_clause(conn, "mv_segment_matrix")}
            ORDER  BY ticker, fiscal_year DESC, value DESC NULLS LAST, fact_id
        """

    def _query_flat(
        self,
        conn,
        concept_family: str,
        fy: int,
        fiscal_period: str,
        ytd: bool,
        notes: list[str],
        component_only_out: list[dict] | None = None,
        currency_out: list[dict] | None = None,
        defined_out: list[dict] | None = None,
    ) -> list[dict]:
        period_type = (
            "instant" if concept_family in INSTANT_CONCEPT_FAMILIES else "duration"
        )
        duration_kind = (
            XbrlFactstoreLookup._derive_duration_kind(fiscal_period, ytd)
            if period_type == "duration"
            else None
        )
        notes.append(
            f"flat screen: concept_family={concept_family}, period_type={period_type}, "
            f"fiscal_period={fiscal_period}, duration_kind={duration_kind}"
        )
        params: dict[str, Any] = {
            "concept_family": concept_family,
            "fy": fy,
            "fiscal_period": fiscal_period,
            "period_type": period_type,
        }
        duration_predicate = ""
        if duration_kind is not None:
            duration_predicate = "AND duration_kind = %(duration_kind)s"
            params["duration_kind"] = duration_kind
        # Every candidate row of each filer's latest fiscal year <= fy, with the columns
        # the lookup orders on (role, presented, precision); select_screen_picks picks one
        # per filer with the lookup's candidate_order (docs/specs/screen-candidate-order.md
        # R1, R4), and for a defined family names every filer's measure column
        # (docs/specs/s4-screen-defined-members.md R2). The literal unit = 'USD' stays:
        # a USD convenience translation is handled by select_reporting_currency below.
        coverage = (
            "concept_role, presented"
            if coverage_columns_present(conn, "mv_financial_facts")
            else "NULL::text AS concept_role, FALSE AS presented"
        )
        decimals = XbrlFactstoreLookup._DECIMALS_SQL.format(table="c")
        sql = f"""
            WITH c AS (
                SELECT ticker, cik, fiscal_year, value, unit, period_end,
                       section_path, html_anchor, page_num, accession_no, fact_id,
                       concept, concept_pref_rank, {coverage},
                       period_start, segment_axis, segment_member,
                       MAX(fiscal_year) OVER (PARTITION BY ticker) AS latest_fiscal_year
                FROM   mv_financial_facts
                WHERE  concept_family     = %(concept_family)s
                  AND  fiscal_year       <= %(fy)s
                  AND  fiscal_period      = %(fiscal_period)s
                  AND  segment_axis       IS NULL
                  AND  period_type        = %(period_type)s
                  {duration_predicate}
                  AND  is_canonical_filing = TRUE
                  AND  is_current_period   = TRUE
                  AND  unit               = 'USD'
                  {publish_gate_clause(conn, "mv_financial_facts")}
            )
            SELECT c.ticker, c.cik, c.fiscal_year, c.value, c.unit, c.period_end,
                   c.section_path, c.html_anchor, c.page_num, c.accession_no, c.fact_id,
                   c.concept, c.concept_pref_rank, c.concept_role, c.presented,
                   c.period_start, c.segment_axis, c.segment_member,
                   {decimals}
            FROM   c
            WHERE  c.fiscal_year = c.latest_fiscal_year
            ORDER  BY c.ticker, c.fact_id
        """
        rows, component_only = select_screen_picks(
            self._fetch(conn, sql, params), concept_family
        )
        # A defined family's non-primary pick is ranked only by its S4 measure
        # (docs/specs/s4-screen-defined-members.md R3); its role is read before S1.
        unmeasurable = {
            r.get("ticker") for r in rows
            if concept_family in DEFINED_FAMILIES and row_role(r) != ROLE_PRIMARY
        }
        # A filer whose fy figure is in another reporting currency is never ranked on its
        # USD convenience translation (docs/specs/screen-reporting-currency.md). Rows for an
        # older year are left to select_fiscal_year's `excluded`.
        current = [r for r in rows if r.get("fiscal_year") == fy]
        if current:
            observations = self._query_unit_observations(
                conn, current, params, duration_predicate
            )
            _, foreign = select_reporting_currency(current, observations)
            if foreign:
                dropped = {e["ticker"] for e in foreign}
                rows = [r for r in rows if r.get("ticker") not in dropped]
                if currency_out is not None:
                    currency_out.extend(foreign)
                notes.append(
                    "not ranked: "
                    + ", ".join(f"{e['ticker']} reports in {e['currency']}" for e in foreign)
                )
        rows = self._restate(conn, rows, fy, params, duration_predicate, notes)
        if concept_family in DEFINED_FAMILIES:
            rows = self._apply_defined_measures(
                conn, rows, concept_family, fy, notes, defined_out
            )
            # A fiscal-year-fy pick that reached no measure (no filing or date to read)
            # stands on its own row only when it is a primary of the family.
            no_measure = [r for r in rows if r.get("fiscal_year") == fy
                          and "definition" not in r and r.get("ticker") in unmeasurable]
            if no_measure:
                rows = [r for r in rows if not any(r is m for m in no_measure)]
                component_only = sorted(
                    component_only + [
                        {"ticker": r.get("ticker"), "fiscal_year": r.get("fiscal_year"),
                         "concept": r.get("concept")}
                        for r in no_measure
                    ],
                    key=lambda c: str(c["ticker"]),
                )
        if component_only_out is not None:
            component_only_out.extend(component_only)
        if component_only:
            # Parts of the measure, or a stated substitute, never stand in for it in a
            # ranking; name the filers instead (docs/specs/screen-candidate-order.md R1).
            notes.append(
                f"not ranked: {len(component_only)} filer(s) tag only components or a "
                f"stated substitute of {concept_family}: "
                + ", ".join(
                    f"{c['ticker']} (FY{c['fiscal_year']}: "
                    f"{str(c['concept']).rsplit(':', 1)[-1]})"
                    for c in component_only
                )
            )
        out = []
        for r in rows:
            row = self._row(
                r,
                label=concept_family,
                fact_id=r.get("fact_id"),
                html_anchor=r.get("html_anchor"),
                page_num=r.get("page_num"),
                accession_no=r.get("accession_no"),
            )
            # The STORED (next-midnight) end; screen() states it after the disclosure
            # is built from it (docs/specs/screen-universe.md).
            row["period_end"] = r.get("period_end")
            if r.get("component_fact_ids"):
                row["component_fact_ids"] = r["component_fact_ids"]
                row["definition"] = r.get("definition")
            out.append(row)
        return out

    def _apply_defined_measures(
        self,
        conn,
        rows: list[dict],
        concept_family: str,
        fy: int,
        notes: list[str],
        reasons_out: list[dict] | None,
    ) -> list[dict]:
        """Ranks each FY-``fy`` filer of a Debt or DeferredRevenue screen by its S4 measure.

        docs/specs/s4-lookup-deferred-revenue-debt.md, requirement 10 and Wiring: one read
        of every pick's filing (its balance-sheet lines and note facts at the pick's date);
        a ``ranked`` member carries the measure's value, its first part as the citation and
        every part in ``component_fact_ids``; a ``metric_not_tagged`` or ``excluded``
        member is not ranked (never by a fallback concept) and goes to ``reasons_out``.

        Args:
            conn: An open connection.
            rows: The flat picks, one per filer (restated by S1).
            concept_family: ``Debt`` or ``DeferredRevenue``.
            fy: The requested fiscal year.
            notes: Resolution notes, appended to.
            reasons_out: Why each unranked filer is not ranked, appended to.

        Returns:
            The picks to rank: FY-``fy`` picks replaced by their measures, others unchanged.
        """
        current = [r for r in rows if r.get("fiscal_year") == fy and r.get("accession_no")
                   and r.get("period_end") is not None]
        if not current:
            return rows
        filings = read_filing_rows(
            conn, self._fetch,
            [(r["accession_no"], r["period_end"], str(r.get("cik") or "")) for r in current],
            "USD",
        )
        rule = defined_rules()[concept_family]
        out: list[dict] = []
        ranked: list[str] = []
        unranked: list[str] = []
        for r in rows:
            filing = filings.get((str(r.get("accession_no")), r.get("period_end")))
            if r.get("fiscal_year") != fy or filing is None:
                out.append(r)
                continue
            measure = defined_measure(
                concept_family, measure_lines(filing, rule), filing.notes, rule, "USD"
            )
            if measure is None:
                out.append(r)
                continue
            member = screen_member(measure)
            if member["status"] != "ranked":
                reason = member["reason"] or member["status"]
                unranked.append(f"{r.get('ticker')} ({reason})")
                if reasons_out is not None:
                    reasons_out.append({"ticker": r.get("ticker"), "reason": reason})
                continue
            first = filing.facts.get(member["fact_ids"][0], {})
            out.append({
                **r,
                "value": member["value"],
                "unit": "USD",
                "fact_id": member["fact_ids"][0],
                "concept": member["concept"],
                "concept_pref_rank": None,
                "html_anchor": first.get("html_anchor"),
                "page_num": first.get("page_num"),
                "section_path": first.get("section_path"),
                "component_fact_ids": {
                    f"part_{n}": fid for n, fid in enumerate(member["fact_ids"], start=1)
                },
                "definition": {"concept": measure["concept"], "basis": measure["basis"],
                               "disclosure": measure["disclosure"]},
            })
            ranked.append(f"{r.get('ticker')} ({measure['basis']})")
        log_event(
            "decision", decision="s4_defined_measure", op="screen",
            concept_family=concept_family, fy=fy, ranked=ranked, unranked=unranked,
        )
        notes.append(f"{concept_family} ranked by its S4 definition: " + ", ".join(ranked))
        if unranked:
            notes.append("not ranked (no defined measure): " + ", ".join(unranked))
        return out

    # The fields a restating filing's row replaces in a flat screen pick; the filer and the
    # requested fiscal year stay.
    _RESTATED_FIELDS = (
        "value", "unit", "period_start", "period_end", "section_path", "html_anchor",
        "page_num", "accession_no", "fact_id", "concept", "concept_pref_rank",
    )

    def _restate(
        self,
        conn,
        rows: list[dict],
        fy: int,
        params: dict[str, Any],
        duration_predicate: str,
        notes: list[str],
    ) -> list[dict]:
        """Each fiscal-year-``fy`` pick as the latest later-labelled report restates it (S1).

        docs/specs/restatement-s1.md, requirement 4: the pick (the fy filing's own column)
        is replaced by ``latest_restatement`` over the filer's canonical rows of the measure
        in later-labelled filings of any fiscal period. The row keeps ``fiscal_year == fy``.
        Among the restating filing's rows, ``candidate_order`` chooses, as for the pick
        (docs/specs/screen-candidate-order.md R1): an exact fact before its rounded
        duplicate.

        Args:
            conn: An open connection.
            rows: The flat picks, one per filer.
            fy: The requested fiscal year.
            params: The flat query's parameters (``concept_family``, ``fy``, ...).
            duration_predicate: The flat query's duration-kind predicate, or "".
            notes: Resolution notes, appended to.

        Returns:
            The picks, restated where a later report restates them, in input order.
        """
        current = [r for r in rows if r.get("fiscal_year") == fy and r.get("ticker")]
        if not current:
            return rows
        # The unit is a parameter, and fiscal_period is not passed: a later 10-Q's
        # comparative of a year-end balance restates it too (spec Notes).
        # The restating filing's rows are chosen among as the pick was: by the lookup's
        # candidate_order (a rounded duplicate never beats the exact fact;
        # docs/specs/screen-candidate-order.md R1), so they carry its columns too.
        coverage = (
            "concept_role, presented"
            if coverage_columns_present(conn, "mv_financial_facts")
            else "NULL::text AS concept_role, FALSE AS presented"
        )
        sql = f"""
            SELECT ticker, cik, fiscal_year, fiscal_period, value, unit, period_start,
                   period_end, section_path, html_anchor, page_num, accession_no, fact_id,
                   concept, concept_pref_rank, segment_axis, segment_member, filed_date,
                   is_canonical_filing, {coverage},
                   {XbrlFactstoreLookup._DECIMALS_SQL.format(table="mv_financial_facts")}
            FROM   mv_financial_facts
            WHERE  concept_family     = %(concept_family)s
              AND  fiscal_year        > %(fy)s
              AND  segment_axis       IS NULL
              AND  period_type        = %(period_type)s
              {duration_predicate}
              AND  is_canonical_filing = TRUE
              AND  unit               = %(unit)s
              AND  ticker             = ANY(%(tickers)s)
              {publish_gate_clause(conn, "mv_financial_facts")}
        """
        query_params = {k: v for k, v in params.items() if k != "fiscal_period"} | {
            "unit": "USD",
            "tickers": sorted({str(r["ticker"]) for r in current}),
        }
        later: dict[str, list[dict]] = {}
        for r in self._fetch(conn, sql, query_params):
            later.setdefault(str(r.get("ticker")), []).append(r)
        out = []
        restated: list[str] = []
        for r in rows:
            if r.get("fiscal_year") != fy or not later.get(str(r.get("ticker"))):
                out.append(r)
                continue
            answer = latest_restatement([r], later[str(r["ticker"])])
            order = candidate_order(answer)
            best = (
                answer[order[0]] if order
                else max(answer, key=lambda a: a.get("fact_id") or 0)
            )
            if best.get("accession_no") == r.get("accession_no"):
                out.append(r)
                continue
            out.append({**r, **{k: best.get(k) for k in self._RESTATED_FIELDS}})
            restated.append(f"{r['ticker']} ({best.get('accession_no')})")
        if restated:
            notes.append(
                f"FY{fy} as restated by the latest later report (S1): " + ", ".join(restated)
            )
        return out

    def _restate_members(
        self,
        conn,
        rows: list[dict],
        fy: int,
        concept_family: str,
        duration_kind: str | None,
        notes: list[str],
    ) -> list[dict]:
        """Each member pick as the latest later-labelled report with its dimensions states it.

        docs/specs/dimensional-s1.md, requirement 4: the views hold each canonical filing's
        own-column rows, so the pick of fiscal year ``fy`` is replaced by
        ``latest_dimensional_restatement`` over the filer's stored rows of the family and
        duration kind from every filing. The views carry no period or accession, so the
        pick's group keys are read from ``mv_financial_facts`` by ``fact_id``. The row keeps
        its filer, ``fiscal_year``, label and ``member_match``; it takes the answering row's
        value, unit, fact and accession, and its share of that filing's own total.

        Args:
            conn: An open connection.
            rows: The ranked member rows (`_row` shape), one per filer.
            fy: The requested fiscal year.
            concept_family: The screened family.
            duration_kind: The screened duration kind, or None.
            notes: Resolution notes, appended to.

        Returns:
            The rows, restated where a later report restates them, in input order. A row
            whose fact is not found in ``mv_financial_facts`` is returned unchanged.
        """
        tickers = sorted({str(r["ticker"]) for r in rows if r.get("ticker")})
        if not tickers:
            return rows
        sql = f"""
            SELECT ticker, cik, fiscal_year, fiscal_period, value, unit,
                   period_start, period_end, section_path, html_anchor, page_num,
                   accession_no, fact_id, concept, segment_axis, segment_member,
                   has_multiple_dimensions, filed_date, is_canonical_filing,
                   {XbrlFactstoreLookup._DECIMALS_SQL.format(table="mv_financial_facts")}
            FROM   mv_financial_facts
            WHERE  concept_family = %(concept_family)s
              AND  fiscal_year   >= %(fy)s
              AND  (%(duration_kind)s::text IS NULL OR duration_kind = %(duration_kind)s)
              AND  ticker         = ANY(%(tickers)s)
              {publish_gate_clause(conn, "mv_financial_facts")}
        """
        params = {"concept_family": concept_family, "fy": fy,
                  "duration_kind": duration_kind, "tickers": tickers}
        by_ticker: dict[str, list[dict]] = {}
        for r in self._fetch(conn, sql, params):
            by_ticker.setdefault(str(r.get("ticker")), []).append(r)
        out: list[dict] = []
        restated: list[str] = []
        for row in rows:
            stored = by_ticker.get(str(row.get("ticker")), [])
            pick = next(
                (r for r in stored if str(r.get("fact_id")) == str(row.get("fact_id"))),
                None,
            )
            if pick is None:
                out.append(row)
                continue
            answer = latest_dimensional_restatement([pick], stored)
            best = max(answer, key=lambda a: a.get("fact_id") or 0)
            value = best.get("value")
            citation = dict(row.get("citation") or {})
            citation.update(
                fact_id=best.get("fact_id"),
                accession_no=best.get("accession_no"),
                html_anchor=best.get("html_anchor"),
                page_num=best.get("page_num"),
                section_path=best.get("section_path"),
            )
            out.append({
                **row,
                "value": float(value) if value is not None else None,
                "unit": best.get("unit"),
                "fact_id": str(best.get("fact_id")),
                "pct_of_total": _share_of_own_total(best, stored),
                "citation": citation,
            })
            if best.get("accession_no") != pick.get("accession_no"):
                restated.append(f"{row['ticker']} ({best.get('accession_no')})")
        if restated:
            notes.append(
                f"FY{fy} members as restated by the latest later report (S1): "
                + ", ".join(restated)
            )
        return out

    def _query_unit_observations(
        self,
        conn,
        rows: list[dict],
        params: dict[str, Any],
        duration_predicate: str,
    ) -> dict[str, list[tuple[str | None, str | None]]]:
        """Every ``(unit, period_end)`` the flat screen would consider for each filer.

        The flat query's filters without its unit and current-period predicates, so the
        comparatives in every unit count (docs/specs/screen-reporting-currency.md).

        Args:
            rows: The picked flat rows, each with ``ticker`` and ``fiscal_year``.
            params: The flat query's parameters (``concept_family``, ``fy``, ...).
            duration_predicate: The flat query's duration-kind predicate, or "".

        Returns:
            Ticker -> its observations at its row's fiscal year.
        """
        year_of = {str(r["ticker"]): r.get("fiscal_year") for r in rows if r.get("ticker")}
        sql = f"""
            SELECT ticker, fiscal_year, unit, period_end
            FROM   mv_financial_facts
            WHERE  concept_family     = %(concept_family)s
              AND  fiscal_year       <= %(fy)s
              AND  fiscal_period      = %(fiscal_period)s
              AND  segment_axis       IS NULL
              AND  period_type        = %(period_type)s
              {duration_predicate}
              AND  is_canonical_filing = TRUE
              AND  ticker             = ANY(%(tickers)s)
              {publish_gate_clause(conn, "mv_financial_facts")}
        """
        query_params = params | {"tickers": sorted(year_of)}
        observations: dict[str, list[tuple[str | None, str | None]]] = {}
        for r in self._fetch(conn, sql, query_params):
            ticker = str(r.get("ticker"))
            if ticker in year_of and r.get("fiscal_year") == year_of[ticker]:
                observations.setdefault(ticker, []).append(
                    (r.get("unit"), _iso_period_end(r.get("period_end")))
                )
        return observations

    def _query_roster(self, conn, fy: int) -> list[str]:
        """The corpus filers' tickers (``entity_master``), sorted.

        ``fy`` is passed although the query does not use it: fakes of the per-view
        queries filter every query by it (docs/specs/screen-universe.md, Notes).
        """
        sql = """
            SELECT cik, trading_symbol AS ticker
            FROM   entity_master
            WHERE  trading_symbol IS NOT NULL
            ORDER  BY trading_symbol
        """
        rows = self._fetch(conn, sql, {"fy": fy})
        return sorted({str(r["ticker"]) for r in rows if r.get("ticker")})

    def _query_derived(
        self,
        conn,
        metric_name: str,
        fy: int,
        fiscal_period: str,
        ytd: bool,
        notes: list[str],
        unit_out: list[dict] | None = None,
        periods_out: dict[int, dict] | None = None,
    ) -> list[dict]:
        """The derived view's rows, latest fiscal year <= fy per filer.

        Rows for ``fy`` are kept only in one rankable unit (USD, USD per share or a
        ratio; docs/specs/units-currency-integrity.md): the units come from the rows'
        component facts, the others are appended to ``unit_out`` as exclusions. The same
        query's stored periods of those facts go to ``periods_out``
        (docs/specs/period-year-length-answers.md).
        """
        # mv_derived_metrics is keyed by (cik, fiscal_year, fiscal_period, metric_name):
        # one row per metric per period (FY uses annual flows, Qn the discrete
        # quarter), so the period filter is fiscal_period alone.
        notes.append(
            f"derived screen: metric_name={metric_name}, fiscal_period={fiscal_period}"
        )
        sql = f"""
            SELECT DISTINCT ON (ticker)
                   ticker, cik, fiscal_year, value, metric_name, formula, component_fact_ids
            FROM   mv_derived_metrics
            WHERE  metric_name = %(metric_name)s
              AND  fiscal_year <= %(fy)s
              AND  fiscal_period = %(fiscal_period)s
              {publish_gate_clause(conn, "mv_derived_metrics")}
            ORDER  BY ticker, fiscal_year DESC, value DESC NULLS LAST
        """
        rows = self._fetch(
            conn,
            sql,
            {
                "metric_name": metric_name,
                "fy": fy,
                "fiscal_period": fiscal_period,
            },
        )
        out: list[dict] = []
        for r in rows:
            # mv_derived_metrics has no single fact_id — cite the components and
            # give the row a stable synthetic id so downstream can reference it.
            synthetic = f"{r.get('metric_name')}:{r.get('cik')}:{r.get('fiscal_year')}"
            row = self._row(r, label=r.get("metric_name"), fact_id=None, unit=None)
            row["fact_id"] = synthetic
            row["component_fact_ids"] = r.get("component_fact_ids")
            row["formula"] = r.get("formula")
            row["citation"]["fact_id"] = None
            out.append(row)
        return self._apply_derived_units(
            conn, out, metric_name, int(fy), notes, unit_out, periods_out
        )

    def _apply_derived_units(
        self,
        conn,
        rows: list[dict],
        metric_name: str,
        fy: int,
        notes: list[str],
        unit_out: list[dict] | None,
        periods_out: dict[int, dict] | None = None,
    ) -> list[dict]:
        """Give each fiscal-year-``fy`` derived row its unit; drop those not rankable.

        ``mv_derived_metrics`` has no unit column, so the units of the facts each row
        cites are read in a query of their own. A row none of whose component facts is
        found keeps unit None and is ranked; an older row is left to
        `select_fiscal_year`.

        Args:
            conn: An open connection.
            rows: The derived rows, each with ``component_fact_ids``.
            metric_name: The derived metric.
            fy: The requested fiscal year.
            notes: Resolution notes, appended to.
            unit_out: Receives one exclusion per row not ranked.
            periods_out: Receives fact id -> stored ``period_start`` / ``period_end`` of
                each component fact found.

        Returns:
            The rows kept, in input order.
        """
        ids_of = [
            _component_ids(r.get("component_fact_ids")) if r.get("fiscal_year") == fy else []
            for r in rows
        ]
        all_ids = sorted({i for ids in ids_of for i in ids})
        units_by_id = (
            self._query_fact_units(conn, all_ids, fy, periods_out) if all_ids else {}
        )
        kept: list[dict] = []
        excluded: list[dict] = []
        for r, ids in zip(rows, ids_of, strict=True):
            if r.get("fiscal_year") != fy or not any(i in units_by_id for i in ids):
                kept.append(r)  # older year, or no component found: ranked as before
                continue
            units = [units_by_id.get(i) for i in ids]
            unit = derived_metric_unit(metric_name, units)
            reason = derived_unit_exclusion(r.get("ticker"), unit, units)
            if reason is None:
                r["unit"] = unit
                kept.append(r)
            else:
                excluded.append(reason)
        if excluded:
            excluded.sort(key=lambda e: str(e["ticker"]))
            notes.append(
                "derived screen left out filer(s) not in one rankable unit: "
                + ", ".join(
                    f"{e['ticker']} ({e.get('currency') or e.get('units')})" for e in excluded
                )
            )
            if unit_out is not None:
                unit_out.extend(excluded)
        return kept

    def _query_fact_units(
        self, conn, fact_ids: list[int], fy: int, periods_out: dict[int, dict] | None = None
    ) -> dict[int, str | None]:
        """The stored unit of each fact id found in ``mv_financial_facts``.

        ``fy`` is passed although the query does not use it: fakes of the per-view
        queries filter every query by it (docs/specs/units-currency-integrity.md, Notes).
        Each found fact's stored ``period_start`` / ``period_end`` go to ``periods_out``.
        """
        sql = """
            SELECT fact_id, unit, period_start, period_end
            FROM   mv_financial_facts
            WHERE  fact_id = ANY(%(fact_ids)s)
        """
        wanted = set(fact_ids)
        out: dict[int, str | None] = {}
        for r in self._fetch(conn, sql, {"fact_ids": list(fact_ids), "fy": fy}):
            fid = r.get("fact_id")
            if isinstance(fid, str) and fid.isdigit():
                fid = int(fid)
            if isinstance(fid, int) and fid in wanted:
                out[fid] = r.get("unit")
                if periods_out is not None:
                    periods_out[fid] = {
                        "period_start": r.get("period_start"),
                        "period_end": r.get("period_end"),
                    }
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row(
        r: dict,
        *,
        label: str,
        fact_id: Any,
        unit: str | None = "__from_row__",
        html_anchor: str | None = None,
        page_num: int | None = None,
        accession_no: str | None = None,
    ) -> dict:
        value = r.get("value")
        citation = Citation(
            fact_id=fact_id,
            accession_no=accession_no,
            html_anchor=html_anchor,
            page_num=page_num,
            section_path=r.get("section_path"),
        )
        return {
            "ticker": r.get("ticker"),
            "cik": r.get("cik"),
            "fiscal_year": r.get("fiscal_year"),
            "value": float(value) if value is not None else None,
            "unit": r.get("unit") if unit == "__from_row__" else unit,
            "pct_of_total": (
                float(r["pct_of_total"]) if r.get("pct_of_total") is not None else None
            ),
            "label": label,
            "fact_id": str(fact_id) if fact_id is not None else None,
            "citation": citation.to_dict(),
        }

    @staticmethod
    def _fetch(conn, sql: str, params: dict) -> list[dict]:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            t0 = time.perf_counter()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            _emit_sql_event(
                cur,
                sql,
                params,
                len(rows),
                (time.perf_counter() - t0) * 1000.0,
                "factstore_screen",
            )
            return rows


if __name__ == "__main__":
    screen = XbrlFactstoreScreen.from_env()
    examples = [
        {
            "id": "s1",
            "op": "screen",
            "concept_family": "Revenue",
            "fy": 2024,
            "dimension": {"geography": "China"},
            "metric_kind": "value",
            "order": "desc",
            "top_n": None,
        },
        {
            "id": "s2",
            "op": "screen",
            "concept_family": "Debt",
            "fy": 2024,
            "dimension": "NONE",
            "metric_kind": "value",
            "order": "desc",
            "top_n": 3,
        },
    ]
    for step in examples:
        print(json.dumps(screen.screen(step).to_dict(), indent=2, default=str))
