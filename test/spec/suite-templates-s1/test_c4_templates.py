"""C4: the templates (docs/specs/suite-templates-s1.md, requirement 2).

``SqlDerivedBuilder`` over the five single-fact templates as committed in
``configs/eval/sql_derived_templates.yaml`` and the fixture store, behind the fake database of
the spec's Notes (every row matching the SQL's ticker / family / unit / period equalities;
the year is selected by the pick, not by SQL).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

TOL = 5e4  # half of $0.1M (TWD 0.1M for TSM): amounts tagged in millions or finer

# template, ticker, fy, concept, value (FSDS / companyfacts), the cited accession, unit
POINTS = [
    ("aq-6a-cash-flow-lookup", "INTC", 2022, "CashFromFinancing", 1_115_000_000.0,
     "0000050863-25-000009", "USD"),
    ("aq-6a-cash-flow-lookup", "INTC", 2022, "CashFromInvesting", -10_231_000_000.0,
     "0000050863-25-000009", "USD"),
    ("aq-6a-cash-flow-lookup", "META", 2022, "Capex", 31_186_000_000.0,
     "0001326801-25-000017", "USD"),
    ("aq-6a-cash-flow-lookup", "META", 2023, "Capex", 27_045_000_000.0,
     "0001628280-26-003942", "USD"),
    ("aq-5a-balance-sheet-lookup", "LLY", 2024, "TotalAssets", 78_715_000_000.0,
     "0000059478-26-000013", "USD"),
    ("aq-5a-balance-sheet-lookup", "COST", 2026, "Goodwill", 994_000_000.0,
     "0000909832-26-000093", "USD"),
    ("aq-10a-tsm-twd-lookup", "TSM", 2023, "NetIncome", 851_027_700_000.0,
     "0001628280-26-025362", "TWD"),
    ("aq-1c-pnl-line-lookup", "TSM", 2023, "NetIncome", 851_027_700_000.0,
     "0001628280-26-025362", "TWD"),
    ("aq-1a-revenue-lookup", "INTC", 2022, "Revenue", 63_054_000_000.0,
     "0000050863-25-000009", "USD"),
]


@pytest.fixture(scope="module")
def suite(kit):
    return kit.build_suite(kit.all_rows())


def _cited(kit, case):
    """The store rows of a case's expected facts."""
    by_id = {r["fact_id"]: r for r in kit.all_rows()}
    return [by_id[f] for f in case.expected_fact_ids]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("point", POINTS, ids=lambda p: f"{p[0][:6]}-{p[1]}-FY{p[2]}-{p[3]}")
def test_each_template_keys_the_s1_fact(kit, suite, point):
    # oracle: FSDS num of the cited accession (conftest ORACLE_POINTS; COST FY2026 from
    # companyfacts) and the latest later-labelled report chosen by hand from dei.json
    template, ticker, fy, concept, value, accession, unit = point
    case = kit.case_of(suite, template, ticker, fy, concept)
    assert case is not None
    assert case.expected_value == pytest.approx(value, abs=TOL)  # tagged in millions or finer
    cited = _cited(kit, case)
    assert cited and {r["accession_no"] for r in cited} == {accession}
    assert {kit.iso(r["period_end"]) for r in cited} == {kit.own_end(ticker, fy)}
    assert case.unit == unit


@pytest.mark.criterion("C4")
def test_no_case_for_a_year_without_its_own_column(kit, suite):
    # oracle: DEI of 0000909832-25-000101 ends 2025-08-31; it tags goodwill only at earlier
    # ends (companyfacts)
    assert kit.case_of(suite, "aq-5a-balance-sheet-lookup", "COST", 2025, "Goodwill") is None


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_every_case_of_the_five_templates_obeys_s1(kit, suite):
    # oracle: the S1 rule applied by hand (conftest s1_holds) over the whole fixture store
    rows = kit.all_rows()
    cases = [c for c in suite.cases if c.id.split(":", 1)[0] in kit.TEMPLATE_IDS]
    assert len(cases) >= len(POINTS)
    for case in cases:
        for row in _cited(kit, case):
            assert row["is_canonical_filing"], case.id
            assert kit.s1_holds(row, rows, case.fy), case.id
            assert row["value"] == pytest.approx(case.expected_value, abs=TOL), case.id


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=5)
@given(st.randoms(use_true_random=False))
def test_row_order_never_changes_a_case(kit, suite, rnd):
    # oracle: S1 does not depend on the order the store returns rows in
    rows = kit.all_rows()
    rnd.shuffle(rows)
    again = kit.build_suite(rows)

    def summary(s):
        by_id = {r["fact_id"]: r for r in kit.all_rows()}
        return {c.id: (round(c.expected_value or 0.0, 0),
                       frozenset(by_id[f]["accession_no"] for f in c.expected_fact_ids))
                for c in s.cases if c.id.split(":", 1)[0] in kit.TEMPLATE_IDS}

    assert summary(again) == summary(suite)
