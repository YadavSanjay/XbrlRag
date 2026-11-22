"""C3: the committed aq-2c template keys the S1 growth (docs/specs/yoy-growth-s1.md,
requirement 2).

``SqlDerivedBuilder`` runs `aq-2c-yoy-growth` as committed over the fixture store through the
fake database (the I/O boundary; suite-templates-s1 Notes). Values are FSDS ``num`` (checked
when the conftest loads); the answering filings are chosen by hand from ``dei.json``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

TEMPLATE = "aq-2c-yoy-growth"
GROWTH_TOL = 1e-6  # FSDS values are exact integers in millions; the hand ratio is exact
TICKERS = ("INTC", "TSM")

# ticker, family, fy, unit, (current value, accession), (prior value, accession)
CASES = [
    ("INTC", "Revenue", 2023, "USD", (54_228e6, "0000050863-26-000011"),
     (63_054e6, "0000050863-25-000009")),
    ("TSM", "NetIncome", 2023, "TWD", (851_027.7e6, "0001628280-26-025362"),
     (993_294.7e6, "0001193125-25-083423")),
    ("INTC", "Revenue", 2025, "USD", (52_853e6, "0000050863-26-000011"),
     (53_101e6, "0000050863-26-000011")),
]


def _store(kit):
    """The fixture store of the two filers' revenue and net income rows."""
    return [r for t in TICKERS for r in kit.store_rows(t, ["Revenue", "NetIncome"])]


def _row_at(rows, fact_id):
    found = [r for r in rows if r["fact_id"] == fact_id]
    assert len(found) == 1, fact_id
    return found[0]


@pytest.fixture(scope="module")
def built(kit):
    rows = _store(kit)
    return rows, kit.build_suite(rows, tickers=TICKERS)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}-FY{c[2]}")
def test_the_case_keys_both_s1_legs_and_their_growth(kit, built, case):
    # oracle: FSDS num of the answering accessions; each year's latest later-labelled report
    # chosen by hand from dei.json; the growth computed by hand
    rows, suite = built
    ticker, family, fy, unit, (cur_v, cur_a), (pri_v, pri_a) = case
    c = kit.case_of(suite, TEMPLATE, ticker, fy, family)
    assert c is not None
    assert len(c.expected_fact_ids) == 2
    current, prior = (_row_at(rows, f) for f in c.expected_fact_ids)
    assert current["accession_no"] == cur_a
    assert kit.iso(current["period_end"]) == kit.own_end(ticker, fy)
    assert prior["accession_no"] == pri_a
    assert kit.iso(prior["period_end"]) == kit.own_end(ticker, fy - 1)
    assert current["unit"] == unit and prior["unit"] == unit
    assert c.unit == unit
    assert c.expected_value == pytest.approx(cur_v / pri_v - 1, abs=GROWTH_TOL)  # exact ratio
    assert c.alt_values == [pytest.approx(100 * (cur_v / pri_v - 1), abs=100 * GROWTH_TOL)]
    assert c.expect_not_meaningful is False
    assert c.kind == "compute"


@pytest.mark.criterion("C3")
def test_no_case_without_both_years(kit, built):
    # oracle: dei.json — no corpus annual report labelled 2021 (INTC, TSM) or 2026 (INTC);
    # the fixture store holds no operating income; FY2023 has a case (above)
    _, suite = built
    assert kit.case_of(suite, TEMPLATE, "INTC", 2022, "Revenue") is None
    assert kit.case_of(suite, TEMPLATE, "TSM", 2022, "NetIncome") is None
    assert kit.case_of(suite, TEMPLATE, "INTC", 2026, "Revenue") is None
    assert not [c for c in suite.cases if c.concept == "OperatingIncome"]
    assert kit.case_of(suite, TEMPLATE, "INTC", 2023, "Revenue") is not None


@pytest.mark.criterion("C3")
def test_the_third_phrasing_names_the_prior_year(kit):
    # oracle: the committed third phrasing "... in fiscal {fy} compared to fiscal {fy_prior}?"
    # with fy_prior = fy - 1 (requirement 2)
    suite = kit.build_suite(_store(kit), tickers=("INTC",), fiscal_years=(2023,),
                            paraphrases=3)
    third = [c for c in suite.cases if c.id.endswith("#p2")]
    assert len(third) == 1
    assert "fiscal 2023" in third[0].question
    assert "fiscal 2022" in third[0].question


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=15)
@given(st.randoms(use_true_random=False))
def test_every_case_holds_s1_and_row_order_never_matters(kit, built, rnd):
    # oracle: S1 by hand (conftest s1_holds) for fy and fy - 1; the growth by hand from the
    # two cited rows' values
    rows, suite = built
    assert suite.cases
    for c in suite.cases:
        current, prior = (_row_at(rows, f) for f in c.expected_fact_ids)
        assert kit.s1_holds(current, rows, c.fy)
        assert kit.s1_holds(prior, rows, c.fy - 1)
        cv, pv = current["value"], prior["value"]
        assert c.expected_value == pytest.approx((cv - pv) / abs(pv), abs=GROWTH_TOL)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    again = kit.build_suite(shuffled, tickers=TICKERS)
    key = {c.id: (c.expected_value, c.expected_fact_ids) for c in suite.cases}
    assert {c.id: (c.expected_value, c.expected_fact_ids) for c in again.cases} == key
