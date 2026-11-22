"""Spec tests for coverage-card C1-C7: the metric list, the status rule, the reader over the
``CoverageCardSource`` seam, the empty-cell report, the S5 hook and the CLI
(docs/specs/coverage-card.md).

The only fake is ``FakeSource`` at the seam, serving hand-built rows. Every expected value
is written by hand or by the rule as the spec states it (``_status``); no library function
serves as an oracle for another.
"""

from __future__ import annotations

import io
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.coverage.base import CoverageCardSource
from src.ingest.sec_edgar.coverage.card import (
    card_metrics,
    cell_from_row,
    classify_cell,
    coverage_card,
    coverage_cards,
    empty_cells,
    render_report,
    screen_eligibility,
)
from src.ingest.sec_edgar.coverage.cli import main

# Requirement 1, in the table's order.
METRICS = [
    ("Revenue", "concept", "Revenue"),
    ("CostOfRevenue", "concept", "CostOfRevenue"),
    ("OperatingIncome", "concept", "OperatingIncome"),
    ("NetIncome", "concept", "NetIncome"),
    ("TotalAssets", "concept", "TotalAssets"),
    ("StockholdersEquity", "concept", "StockholdersEquity"),
    ("CashFromOperations", "concept", "CashFromOperations"),
    ("Capex", "concept", "Capex"),
    ("SegmentAxis", "axis", "Revenue"),
    ("GeographyAxis", "axis", "Revenue"),
]
METRIC_IDS = [m for m, _k, _f in METRICS]
AXES = {"SegmentAxis", "GeographyAxis"}
EMPTY = {"component_only", "absent"}

GAAP22 = "http://fasb.org/us-gaap/2022"
GAAP24 = "http://fasb.org/us-gaap/2024"
ACME24 = "http://acme.example/20241231"

NVDA = "0001045810"  # NVIDIA's public CIK
ACME = "0000900001"  # made up
ANON = "0000900002"  # made up, no ticker


# ------------------------------------------------------------------ the rule (oracle)
def _status(s, e, c):
    """Requirement 6 written out: the first non-empty tier wins."""
    if s:
        return "standard", tuple(s)
    if e:
        return "extension", tuple(e)
    if c:
        return "component_only", tuple(c)
    return "absent", ()


def _row(cik, ticker, year, metric, s=(), e=(), c=(), status=None):
    """A plain 8-tuple in CoverageRow's field order (the seam accepts any such sequence)."""
    st_ = _status(s, e, c)[0] if status is None else status
    return (cik, ticker, year, metric, st_, tuple(s), tuple(e), tuple(c))


class FakeSource(CoverageCardSource):
    """The seam over hand-built rows; records each call."""

    def __init__(self, rows, honour_filters=True):
        self._rows = list(rows)
        self._honour = honour_filters
        self.calls = []

    def rows(self, cik, fiscal_year):
        self.calls.append((cik, fiscal_year))
        if not self._honour:
            return list(self._rows)
        return [r for r in self._rows
                if (cik is None or r[0] == cik)
                and (fiscal_year is None or r[2] == fiscal_year)]


# ------------------------------------------------------------------ point data
STD = {  # one standard qname per concept metric, and the two standard axes
    "Revenue": (f"{GAAP22}:Revenues",),
    "CostOfRevenue": (f"{GAAP22}:CostOfRevenue",),
    "OperatingIncome": (f"{GAAP22}:OperatingIncomeLoss",),
    "NetIncome": (f"{GAAP22}:NetIncomeLoss",),
    "TotalAssets": (f"{GAAP22}:Assets",),
    "StockholdersEquity": (f"{GAAP22}:StockholdersEquity",),
    "CashFromOperations": (f"{GAAP22}:NetCashProvidedByUsedInOperatingActivities",),
    "Capex": (f"{GAAP22}:PaymentsToAcquirePropertyPlantAndEquipment",),
    "SegmentAxis": ("us-gaap:StatementBusinessSegmentsAxis",),
    "GeographyAxis": ("srt:StatementGeographicalAxis",),
}
ACME_SALES = f"{ACME24}:TotalNetSales"
ACME_COMPONENTS = (f"{GAAP24}:CostDirectMaterial", f"{GAAP24}:CostDirectLabor")


def _point_rows():
    rows = []
    # NVIDIA FY2023: Capex absent, every other metric standard.
    for m in METRIC_IDS:
        rows.append(_row(NVDA, "NVDA", 2023, m, s=() if m == "Capex" else STD[m]))
    # NVIDIA FY2024: every metric standard.
    for m in METRIC_IDS:
        rows.append(_row(NVDA, "NVDA", 2024, m, s=STD[m]))
    # ACME FY2024: Revenue only by extension, CostOfRevenue only by components, Capex and the
    # segment axis absent.
    for m in METRIC_IDS:
        if m == "Revenue":
            rows.append(_row(ACME, "ACME", 2024, m, e=(ACME_SALES,)))
        elif m == "CostOfRevenue":
            rows.append(_row(ACME, "ACME", 2024, m, c=ACME_COMPONENTS))
        elif m in ("Capex", "SegmentAxis"):
            rows.append(_row(ACME, "ACME", 2024, m))
        else:
            rows.append(_row(ACME, "ACME", 2024, m, s=STD[m]))
    # ANON FY2024 (no ticker): geography axis absent.
    for m in METRIC_IDS:
        rows.append(_row(ANON, None, 2024, m, s=() if m == "GeographyAxis" else STD[m]))
    return rows


POINT_REPORT = (
    "ACME\t0000900001\t2024\tCostOfRevenue\tcomponent_only\t"
    "http://fasb.org/us-gaap/2024:CostDirectMaterial,http://fasb.org/us-gaap/2024:CostDirectLabor\n"
    "ACME\t0000900001\t2024\tCapex\tabsent\t-\n"
    "ACME\t0000900001\t2024\tSegmentAxis\tabsent\t-\n"
    "-\t0000900002\t2024\tGeographyAxis\tabsent\t-\n"
    "NVDA\t0001045810\t2023\tCapex\tabsent\t-\n"
    "5 empty cells across 3 of 4 filer-years\n"
)


# ------------------------------------------------------------------ strategies
CONCEPT_QN = st.sampled_from([
    f"{GAAP24}:Revenues", f"{GAAP24}:Assets", f"{GAAP24}:Depreciation",
    f"{ACME24}:TotalNetSales", f"{ACME24}:Other", "http://xbrl.ifrs.org/taxonomy/2024:Revenue",
])
AXIS_QN = st.sampled_from([
    "us-gaap:StatementBusinessSegmentsAxis", "ifrs-full:SegmentsAxis",
    "srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis",
])
TIER = st.lists(CONCEPT_QN, max_size=2, unique=True)


def _tiers(metric):
    if metric in AXES:
        return st.tuples(st.lists(AXIS_QN, max_size=2, unique=True), st.just([]), st.just([]))
    return st.tuples(TIER, TIER, TIER)


@st.composite
def datasets(draw):
    """Rows for 1-4 complete cards with arbitrary tiers, statuses by the rule."""
    keys = draw(st.lists(
        st.tuples(st.sampled_from([NVDA, ACME, ANON]), st.integers(2020, 2026)),
        min_size=1, max_size=4, unique=True))
    rows = []
    for cik, year in keys:
        ticker = draw(st.sampled_from(["NVDA", "ACME", None]))
        for m in METRIC_IDS:
            s, e, c = draw(_tiers(m))
            rows.append(_row(cik, ticker, year, m, s, e, c))
    return rows


def _expected_cards(rows):
    """(cik, year) -> metric -> the expected cell as a plain tuple, by the rule."""
    out = {}
    for cik, ticker, year, metric, _claimed, s, e, c in rows:
        status, qnames = _status(s, e, c)
        out.setdefault((cik, year), {})[metric] = (cik, ticker, year, metric, status, qnames)
    return out


def _as_plain(cards):
    return {k: {m: tuple(cell) for m, cell in card.items()} for k, card in cards.items()}


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_card_metrics_are_the_committed_list():
    # oracle: the table in requirement 1, by hand
    got = card_metrics()
    assert [tuple(m) for m in got] == METRICS
    assert [m.metric for m in got] == METRIC_IDS
    assert [m.concept_family for m in got if m.kind == "axis"] == ["Revenue", "Revenue"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_card_metrics_are_stable_and_unique():
    # oracle: requirement 1 — ten unique ids; a concept metric reads its own family; two calls
    # give the same list
    first, second = card_metrics(), card_metrics()
    assert first == second
    ids = [m.metric for m in first]
    assert len(ids) == 10 and len(set(ids)) == 10
    assert {m.kind for m in first} == {"concept", "axis"}
    assert all(m.concept_family == m.metric for m in first if m.kind == "concept")
    assert {m.metric for m in first if m.kind == "axis"} == AXES


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_classify_cell_point_cases():
    # oracle: requirement 6, by hand on the spec's point cases
    assert classify_cell(["us-gaap:Revenues"], ["nvda:X"], []) == (
        "standard", ("us-gaap:Revenues",))
    assert classify_cell([], ["abc:TotalNetSales"], ["us-gaap:Depreciation"]) == (
        "extension", ("abc:TotalNetSales",))
    assert classify_cell([], [], ["us-gaap:Depreciation",
                                  "us-gaap:AmortizationOfIntangibleAssets"]) == (
        "component_only", ("us-gaap:Depreciation", "us-gaap:AmortizationOfIntangibleAssets"))
    assert classify_cell([], [], []) == ("absent", ())


@pytest.mark.criterion("C2")
@given(TIER, TIER, TIER, CONCEPT_QN)
def test_classify_cell_matches_the_rule(s, e, c, extra):
    # oracle: requirement 6 written out (`_status`); a standard qname always wins
    assert classify_cell(s, e, c) == _status(s, e, c)
    status, qnames = classify_cell([*s, extra] if extra not in s else s, e, c)
    assert status == "standard"
    assert extra in qnames
    if s:
        assert classify_cell(s, [], []) == classify_cell(s, e, c) == ("standard", tuple(s))


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_cell_from_row_point_cases():
    # oracle: requirements 5-7, by hand
    row = _row(ACME, "ACME", 2024, "Revenue", e=(ACME_SALES,))
    assert tuple(cell_from_row(row)) == (ACME, "ACME", 2024, "Revenue", "extension",
                                         (ACME_SALES,))
    comp = _row(ACME, "ACME", 2024, "CostOfRevenue", c=ACME_COMPONENTS)
    assert tuple(cell_from_row(comp)) == (ACME, "ACME", 2024, "CostOfRevenue",
                                          "component_only", ACME_COMPONENTS)
    assert tuple(cell_from_row(_row(NVDA, "NVDA", 2023, "Capex"))) == (
        NVDA, "NVDA", 2023, "Capex", "absent", ())
    with pytest.raises(ValueError):  # status disagrees with the rule
        cell_from_row(_row(ACME, "ACME", 2024, "Revenue", e=(ACME_SALES,), status="standard"))
    with pytest.raises(ValueError):  # not a card metric
        cell_from_row(_row(ACME, "ACME", 2024, "EBITDA", s=(f"{GAAP24}:Revenues",)))
    with pytest.raises(ValueError):  # an axis cell is standard or absent only
        cell_from_row(_row(ACME, "ACME", 2024, "SegmentAxis", e=(f"{ACME24}:BusinessAxis",)))


@pytest.mark.criterion("C3")
@given(st.sampled_from([m for m in METRIC_IDS if m not in AXES]), TIER, TIER, TIER,
       st.sampled_from(["standard", "extension", "component_only", "absent"]))
def test_cell_from_row_accepts_exactly_the_rule(metric, s, e, c, claimed):
    # oracle: requirement 6 (`_status`) — the rule's status round-trips; any other raises
    expected, qnames = _status(s, e, c)
    row = _row(NVDA, "NVDA", 2024, metric, s, e, c, status=claimed)
    if claimed == expected:
        assert tuple(cell_from_row(row)) == (NVDA, "NVDA", 2024, metric, expected, qnames)
    else:
        with pytest.raises(ValueError):
            cell_from_row(row)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_coverage_card_point_case():
    # oracle: requirement 7 on the hand-built rows; the CIK is zero-padded before the call
    src = FakeSource(_point_rows())
    card = coverage_card(src, "1045810", 2023)
    assert src.calls == [(NVDA, 2023)]
    assert list(card) == METRIC_IDS
    assert tuple(card["Revenue"]) == (NVDA, "NVDA", 2023, "Revenue", "standard",
                                      STD["Revenue"])
    assert tuple(card["Capex"]) == (NVDA, "NVDA", 2023, "Capex", "absent", ())
    assert tuple(card["SegmentAxis"]) == (NVDA, "NVDA", 2023, "SegmentAxis", "standard",
                                          ("us-gaap:StatementBusinessSegmentsAxis",))
    # A source that ignores the filters: other filer-years' rows are ignored.
    loose = coverage_card(FakeSource(_point_rows(), honour_filters=False), ACME, 2024)
    assert {tuple(c)[0] for c in loose.values()} == {ACME}
    assert tuple(loose["CostOfRevenue"])[4:] == ("component_only", ACME_COMPONENTS)


@pytest.mark.criterion("C4")
def test_coverage_card_refusals():
    # oracle: requirement 3 — every card has all ten metrics; no rows means no card
    rows = _point_rows()
    with pytest.raises(LookupError):
        coverage_card(FakeSource(rows), NVDA, 2030)
    missing = [r for r in rows if r[:4] != (NVDA, "NVDA", 2023, "Capex")]
    with pytest.raises(ValueError):
        coverage_card(FakeSource(missing), NVDA, 2023)
    with pytest.raises(ValueError):
        coverage_cards(FakeSource(missing))
    repeated = rows + [_row(NVDA, "NVDA", 2023, "Revenue", s=STD["Revenue"])]
    with pytest.raises(ValueError):
        coverage_card(FakeSource(repeated), NVDA, 2023)
    bad = rows + [_row(ANON, None, 2025, m, status="standard") for m in METRIC_IDS]
    with pytest.raises(ValueError):
        coverage_cards(FakeSource(bad))


@pytest.mark.criterion("C4")
def test_coverage_cards_point_case():
    # oracle: requirement 7 on the hand-built rows; one unfiltered call, keys ascending
    src = FakeSource(_point_rows())
    cards = coverage_cards(src)
    assert src.calls == [(None, None)]
    assert list(cards) == [(ACME, 2024), (ANON, 2024), (NVDA, 2023), (NVDA, 2024)]
    assert _as_plain(cards) == _expected_cards(_point_rows())
    assert tuple(cards[(ANON, 2024)]["GeographyAxis"]) == (
        ANON, None, 2024, "GeographyAxis", "absent", ())


@pytest.mark.criterion("C4")
@given(datasets(), st.randoms(use_true_random=False))
def test_cards_agree_and_ignore_row_order(rows, rnd):
    # oracle: requirements 6-7 (`_expected_cards`); coverage_cards agrees with coverage_card
    # per key, and shuffling the source's rows changes nothing
    expected = _expected_cards(rows)
    cards = coverage_cards(FakeSource(rows))
    assert _as_plain(cards) == expected
    assert list(cards) == sorted(expected)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    assert _as_plain(coverage_cards(FakeSource(shuffled))) == expected
    for cik, year in expected:
        assert {m: tuple(c) for m, c in coverage_card(FakeSource(shuffled), cik, year).items()} \
            == expected[(cik, year)]


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_empty_cells_and_report_point_case():
    # oracle: requirement 8 and C5's line format, the report written out by hand
    cards = coverage_cards(FakeSource(_point_rows()))
    assert [tuple(c)[:5] for c in empty_cells(cards)] == [
        (ACME, "ACME", 2024, "CostOfRevenue", "component_only"),
        (ACME, "ACME", 2024, "Capex", "absent"),
        (ACME, "ACME", 2024, "SegmentAxis", "absent"),
        (ANON, None, 2024, "GeographyAxis", "absent"),
        (NVDA, "NVDA", 2023, "Capex", "absent"),
    ]
    assert render_report(cards) == POINT_REPORT


@pytest.mark.criterion("C5")
@given(datasets(), st.randoms(use_true_random=False))
def test_report_lists_each_empty_cell_once(rows, rnd):
    # oracle: requirement 8 — an empty cell is component_only or absent; one line each, in
    # (cik, year, metric order), then the summary; card order does not matter
    expected = _expected_cards(rows)
    want = [cell for key in sorted(expected) for m in METRIC_IDS
            if (cell := expected[key][m])[4] in EMPTY]
    cards = coverage_cards(FakeSource(rows))
    assert [tuple(c) for c in empty_cells(cards)] == want
    keys = list(cards)
    rnd.shuffle(keys)
    reordered = {k: cards[k] for k in keys}
    report = render_report(reordered)
    assert report == render_report(cards)
    lines = report.split("\n")
    assert lines[-1] == ""
    assert len(lines) - 1 == len(want) + 1
    n_years = len({(c[0], c[2]) for c in want})
    assert lines[-2] == f"{len(want)} empty cells across {n_years} of {len(expected)} filer-years"
    for line, cell in zip(lines[:len(want)], want, strict=True):
        assert line.split("\t")[:5] == [cell[1] or "-", cell[0], str(cell[2]), cell[3], cell[4]]


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_screen_eligibility_point_cases():
    # oracle: requirement 9 and S5, by hand on the point cards
    cards = coverage_cards(FakeSource(_point_rows()))
    ok = screen_eligibility(cards, "Revenue", "1045810", 2023)
    assert (ok.rankable, ok.reason) == (True, None)
    assert tuple(ok.cell) == (NVDA, "NVDA", 2023, "Revenue", "standard", STD["Revenue"])
    ext = screen_eligibility(cards, "Revenue", ACME, 2024)
    assert (ext.rankable, ext.reason, ext.cell.status) == (True, None, "extension")
    comp = screen_eligibility(cards, "CostOfRevenue", ACME, 2024)
    assert (comp.rankable, comp.reason, comp.cell.status) == (
        False, "component_only", "component_only")
    absent = screen_eligibility(cards, "Capex", NVDA, 2023)
    assert (absent.rankable, absent.reason, absent.cell.status) == (
        False, "metric_not_tagged", "absent")
    assert tuple(screen_eligibility(cards, "Capex", NVDA, 2030)) == (
        False, "period_not_filed", None)
    with pytest.raises(ValueError):
        screen_eligibility(cards, "EBITDA", NVDA, 2023)


REASON = {"standard": None, "extension": None, "component_only": "component_only",
          "absent": "metric_not_tagged"}


@pytest.mark.criterion("C6")
@given(datasets(), st.sampled_from(METRIC_IDS), st.sampled_from([NVDA, ACME, ANON]),
       st.integers(2020, 2026))
def test_screen_eligibility_follows_the_rule(rows, metric, cik, year):
    # oracle: requirement 9 written out (`REASON`) on the rule's statuses
    expected = _expected_cards(rows)
    cards = coverage_cards(FakeSource(rows))
    got = screen_eligibility(cards, metric, cik.lstrip("0"), year)
    if (cik, year) not in expected:
        assert tuple(got) == (False, "period_not_filed", None)
    else:
        cell = expected[(cik, year)][metric]
        assert tuple(got.cell) == cell
        assert got.reason == REASON[cell[4]]
        assert got.rankable is (cell[4] in ("standard", "extension"))


# --------------------------------------------------------------------------- C7
@pytest.mark.criterion("C7")
def test_cli_point_cases():
    # oracle: requirement 8 and C7, the expected output written out by hand
    out = io.StringIO()
    assert main([], FakeSource(_point_rows()), out) == 0
    assert out.getvalue() == POINT_REPORT
    out = io.StringIO()
    assert main(["--strict"], FakeSource(_point_rows()), out) == 1
    assert out.getvalue() == POINT_REPORT
    out = io.StringIO()
    assert main(["--cik", "1045810", "--fiscal-year", "2023"], FakeSource(_point_rows()),
                out) == 0
    assert out.getvalue() == ("NVDA\t0001045810\t2023\tCapex\tabsent\t-\n"
                              "1 empty cells across 1 of 1 filer-years\n")
    out = io.StringIO()
    assert main(["--cik", NVDA, "--strict"], FakeSource(_point_rows()), out) == 1
    assert out.getvalue() == ("NVDA\t0001045810\t2023\tCapex\tabsent\t-\n"
                              "1 empty cells across 1 of 2 filer-years\n")
    out = io.StringIO()
    assert main(["--cik", NVDA, "--fiscal-year", "2024", "--strict"],
                FakeSource(_point_rows()), out) == 0
    assert out.getvalue() == "0 empty cells across 0 of 1 filer-years\n"


@pytest.mark.criterion("C7")
def test_cli_no_cards():
    # oracle: C7 — exit 2 and a "no coverage cards" line when nothing matches
    out = io.StringIO()
    assert main([], FakeSource([]), out) == 2
    assert out.getvalue().startswith("no coverage cards")
    out = io.StringIO()
    assert main(["--fiscal-year", "2019"], FakeSource(_point_rows()), out) == 2
    assert out.getvalue().startswith("no coverage cards")


@pytest.mark.criterion("C7")
@given(datasets())
def test_cli_strict_exit_follows_the_empty_cells(rows):
    # oracle: requirement 8 (`_status`) — --strict exits 1 exactly when the rule finds an
    # empty cell; the output has one line per empty cell plus the summary
    expected = _expected_cards(rows)
    n_empty = sum(1 for card in expected.values() for cell in card.values()
                  if cell[4] in EMPTY)
    out = io.StringIO()
    assert main(["--strict"], FakeSource(rows), out) == (1 if n_empty else 0)
    assert out.getvalue().count("\n") == n_empty + 1
    out_plain = io.StringIO()
    assert main([], FakeSource(random.Random(0).sample(rows, len(rows))), out_plain) == 0
    assert out_plain.getvalue() == out.getvalue()
