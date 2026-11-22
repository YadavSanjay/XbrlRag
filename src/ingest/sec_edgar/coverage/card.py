# The coverage card: the committed metric list, the status rule, the reader over the
# CoverageCardSource seam, the empty-cell report, and the S5 screen-eligibility hook.
# Spec: docs/specs/coverage-card.md.

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from src.ingest.sec_edgar.coverage.base import CoverageCardSource

# The statuses, in tier order (requirement 6).
STANDARD = "standard"
EXTENSION = "extension"
COMPONENT_ONLY = "component_only"
ABSENT = "absent"

# An empty cell is a development case for the concept families (requirement 8).
EMPTY_STATUSES = frozenset({COMPONENT_ONLY, ABSENT})

# S5 exclusion reasons, in the v2 suite's vocabulary (requirement 9).
REASON_COMPONENT_ONLY = "component_only"
REASON_NOT_TAGGED = "metric_not_tagged"
REASON_NOT_FILED = "period_not_filed"

_ROW_WIDTH = 8
_CIK_WIDTH = 10


class CardMetric(NamedTuple):
    """A canonical metric of the card: a concept family, or an axis over ``Revenue``.

    Attributes:
        metric: The metric id.
        kind: ``concept`` or ``axis``.
        concept_family: The ``concept_mapping`` family the metric reads.
    """

    metric: str
    kind: str
    concept_family: str


class CoverageCell(NamedTuple):
    """One card entry: the status of a metric in a filer-year and the qnames it rests on.

    Attributes:
        cik: The filer's 10-digit CIK.
        ticker: The filer's trading symbol, if known.
        fiscal_year: The fiscal year.
        metric: The card metric id.
        status: ``standard``, ``extension``, ``component_only`` or ``absent``.
        qnames: The winning tier's qnames; empty for ``absent``.
    """

    cik: str
    ticker: str | None
    fiscal_year: int
    metric: str
    status: str
    qnames: tuple[str, ...]


class Eligibility(NamedTuple):
    """Whether a filer-year may be ranked on a metric, and why not (S5).

    Attributes:
        rankable: True when the filer-year may be ranked.
        reason: The exclusion reason, or ``None`` when rankable.
        cell: The card entry a key cites, or ``None`` when there is no card.
    """

    rankable: bool
    reason: str | None
    cell: CoverageCell | None


_METRICS = (
    CardMetric("Revenue", "concept", "Revenue"),
    CardMetric("CostOfRevenue", "concept", "CostOfRevenue"),
    CardMetric("OperatingIncome", "concept", "OperatingIncome"),
    CardMetric("NetIncome", "concept", "NetIncome"),
    CardMetric("TotalAssets", "concept", "TotalAssets"),
    CardMetric("StockholdersEquity", "concept", "StockholdersEquity"),
    CardMetric("CashFromOperations", "concept", "CashFromOperations"),
    CardMetric("Capex", "concept", "Capex"),
    CardMetric("SegmentAxis", "axis", "Revenue"),
    CardMetric("GeographyAxis", "axis", "Revenue"),
)
_BY_ID = {m.metric: m for m in _METRICS}
_ORDER = {m.metric: i for i, m in enumerate(_METRICS)}


def card_metrics() -> tuple[CardMetric, ...]:
    """Returns the card's committed metrics, in card order (requirement 1).

    Returns:
        The ten metrics: eight concept metrics, then the segment and geography axes.
    """
    return _METRICS


def pad_cik(cik: str) -> str:
    """Zero-pads a CIK to 10 digits.

    Args:
        cik: A CIK, with or without leading zeros.

    Returns:
        The 10-digit CIK.
    """
    return str(cik).strip().zfill(_CIK_WIDTH)


def classify_cell(standard_qnames: Sequence[str], extension_qnames: Sequence[str],
                  component_qnames: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """Decides a cell's status from its tiers (requirement 6).

    Args:
        standard_qnames: Reported standard-taxonomy concepts at a primary rank.
        extension_qnames: Reported filer-extension concepts at a primary rank.
        component_qnames: Reported concepts in the component band.

    Returns:
        ``(status, qnames)``: the status and the winning tier, in the given order.
    """
    for status, tier in ((STANDARD, standard_qnames), (EXTENSION, extension_qnames),
                         (COMPONENT_ONLY, component_qnames)):
        if len(tier) > 0:
            return status, tuple(tier)
    return ABSENT, ()


def cell_from_row(row: Sequence) -> CoverageCell:
    """Turns a validated card row into a cell.

    Args:
        row: A row of ``mv_coverage_card`` (``CoverageRow`` or any 8-sequence in its field
            order).

    Returns:
        The cell.

    Raises:
        ValueError: The row's status disagrees with ``classify_cell``, its metric is not a
            card metric, or an axis row carries extension or component qnames.
    """
    if len(row) != _ROW_WIDTH:
        raise ValueError(f"coverage row has {len(row)} fields, expected {_ROW_WIDTH}: {row!r}")
    cik, ticker, fiscal_year, metric, status, standard, extension, component = row
    card_metric = _BY_ID.get(metric)
    if card_metric is None:
        raise ValueError(f"coverage row has an unknown metric {metric!r}: {row!r}")
    standard, extension, component = (tuple(t or ()) for t in (standard, extension, component))
    if card_metric.kind == "axis" and (extension or component):
        raise ValueError(f"axis metric {metric!r} carries extension or component qnames: "
                         f"{row!r}")
    expected, qnames = classify_cell(standard, extension, component)
    if status != expected:
        raise ValueError(f"coverage row status {status!r} disagrees with its tiers "
                         f"({expected!r}): {row!r}")
    return CoverageCell(pad_cik(cik), ticker, int(fiscal_year), metric, expected, qnames)


def _card(cells: Sequence[CoverageCell]) -> dict[str, CoverageCell]:
    """Validates one filer-year's cells into a card in card-metric order."""
    by_metric: dict[str, CoverageCell] = {}
    for cell in cells:
        if cell.metric in by_metric:
            raise ValueError(f"coverage card {cell.cik} FY{cell.fiscal_year} repeats metric "
                             f"{cell.metric!r}")
        by_metric[cell.metric] = cell
    missing = [m.metric for m in _METRICS if m.metric not in by_metric]
    if missing:
        first = cells[0]
        raise ValueError(f"coverage card {first.cik} FY{first.fiscal_year} lacks metrics "
                         f"{missing}")
    return {m.metric: by_metric[m.metric] for m in _METRICS}


def cards_from_rows(rows: Iterable[Sequence], cik: str | None = None,
                    fiscal_year: int | None = None
                    ) -> dict[tuple[str, int], dict[str, CoverageCell]]:
    """Builds validated cards from card rows, optionally keeping one filer and/or year.

    Args:
        rows: Card rows (``CoverageRow`` or any 8-sequence in its field order).
        cik: Keep only this filer (zero-padded); ``None`` keeps every filer.
        fiscal_year: Keep only this fiscal year; ``None`` keeps every year.

    Returns:
        (cik, fiscal_year) -> metric -> cell, keys ascending.

    Raises:
        ValueError: A kept card lacks or repeats a metric, or a kept row is invalid.
    """
    want_cik = None if cik is None else pad_cik(cik)
    grouped: dict[tuple[str, int], list[CoverageCell]] = {}
    for row in rows:
        if len(row) == _ROW_WIDTH:
            if want_cik is not None and pad_cik(row[0]) != want_cik:
                continue
            if fiscal_year is not None and int(row[2]) != fiscal_year:
                continue
        cell = cell_from_row(row)
        grouped.setdefault((cell.cik, cell.fiscal_year), []).append(cell)
    return {key: _card(grouped[key]) for key in sorted(grouped)}


def coverage_card(source: CoverageCardSource, cik: str,
                  fiscal_year: int) -> dict[str, CoverageCell]:
    """Reads one filer-year's card.

    Args:
        source: The card rows.
        cik: The filer's CIK; zero-padded to 10 digits.
        fiscal_year: The fiscal year.

    Returns:
        metric -> cell, in card-metric order.

    Raises:
        LookupError: There is no card for the filer-year.
        ValueError: The card lacks or repeats a metric, or a row is invalid.
    """
    padded = pad_cik(cik)
    cards = cards_from_rows(source.rows(padded, fiscal_year), padded, fiscal_year)
    card = cards.get((padded, fiscal_year))
    if card is None:
        raise LookupError(f"no coverage card for CIK {padded} FY{fiscal_year}")
    return card


def coverage_cards(source: CoverageCardSource) -> dict[tuple[str, int], dict[str, CoverageCell]]:
    """Reads every card.

    Args:
        source: The card rows.

    Returns:
        (cik, fiscal_year) -> metric -> cell, keys ascending.

    Raises:
        ValueError: A card lacks or repeats a metric, or a row is invalid.
    """
    return cards_from_rows(source.rows(None, None))


def empty_cells(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]]) -> list[CoverageCell]:
    """Lists the cells whose status is ``component_only`` or ``absent``.

    Args:
        cards: Cards as ``coverage_cards`` returns them.

    Returns:
        The empty cells, by (cik, fiscal_year, card-metric order).
    """
    out: list[CoverageCell] = []
    for key in sorted(cards):
        card = cards[key]
        for metric in sorted(card, key=lambda m: _ORDER.get(m, len(_ORDER))):
            if card[metric].status in EMPTY_STATUSES:
                out.append(card[metric])
    return out


def render_report(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]]) -> str:
    """Renders the empty-cell report: one tab-separated line per empty cell, then a summary.

    Args:
        cards: Cards as ``coverage_cards`` returns them.

    Returns:
        The report text, ending with a newline.
    """
    cells = empty_cells(cards)
    lines = [f"{c.ticker or '-'}\t{c.cik}\t{c.fiscal_year}\t{c.metric}\t{c.status}\t"
             f"{','.join(c.qnames) or '-'}" for c in cells]
    years = len({(c.cik, c.fiscal_year) for c in cells})
    lines.append(f"{len(cells)} empty cells across {years} of {len(cards)} filer-years")
    return "\n".join(lines) + "\n"


def screen_eligibility(cards: Mapping[tuple[str, int], Mapping[str, CoverageCell]], metric: str,
                       cik: str, fiscal_year: int) -> Eligibility:
    """Says whether a filer-year may be ranked on a metric (S5), and the card entry to cite.

    Args:
        cards: Cards as ``coverage_cards`` returns them.
        metric: A card metric.
        cik: The filer's CIK; zero-padded to 10 digits.
        fiscal_year: The fiscal year.

    Returns:
        The eligibility: rankable for ``standard`` and ``extension``; otherwise the
        exclusion reason (``component_only``, ``metric_not_tagged`` or ``period_not_filed``).

    Raises:
        ValueError: ``metric`` is not a card metric.
    """
    if metric not in _BY_ID:
        raise ValueError(f"not a coverage-card metric: {metric!r}")
    card = cards.get((pad_cik(cik), fiscal_year))
    if card is None:
        return Eligibility(False, REASON_NOT_FILED, None)
    cell = card[metric]
    if cell.status in (STANDARD, EXTENSION):
        return Eligibility(True, None, cell)
    if cell.status == COMPONENT_ONLY:
        return Eligibility(False, REASON_COMPONENT_ONLY, cell)
    return Eligibility(False, REASON_NOT_TAGGED, cell)
