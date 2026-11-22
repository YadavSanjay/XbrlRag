"""C1: restated durations (docs/specs/suite-templates-s1.md, requirement 1).

aq-6a's measures: a cash-flow amount of a fiscal year is the fy filing's own column as the
latest later-labelled report restates it. Values are FSDS ``num`` of the named accession
(checked when the conftest loads); dates are the DEI ``DocumentPeriodEndDate``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.sql_derived import s1_lookup_pick

TOL = 5e4  # half of $0.1M: amounts tagged in millions or hundred-thousands

# ticker, family, fy, value (FSDS), the answering accession, the original value never keyed
POINTS = [
    ("INTC", "CashFromFinancing", 2022, 1_115_000_000.0, "0000050863-25-000009",
     1_361_000_000.0),
    ("INTC", "CashFromInvesting", 2022, -10_231_000_000.0, "0000050863-25-000009",
     -10_477_000_000.0),
    ("META", "Capex", 2022, 31_186_000_000.0, "0001326801-25-000017", 31_431_000_000.0),
    ("META", "Capex", 2023, 27_045_000_000.0, "0001628280-26-003942", 27_266_000_000.0),
    ("INTC", "CashFromFinancing", 2025, 11_587_000_000.0, "0000050863-26-000011", None),
]
# filer-measure-years with an own column, for the properties
GRID = [("INTC", f, fy) for f in ("CashFromFinancing", "CashFromInvesting", "Revenue")
        for fy in (2022, 2023, 2024, 2025)] + [("META", "Capex", fy)
                                               for fy in (2022, 2023, 2024, 2025)]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", POINTS, ids=lambda c: f"{c[0]}-{c[1]}-FY{c[2]}")
def test_the_latest_report_answers_a_restated_duration(kit, case):
    # oracle: FSDS num of the restating and original accessions (conftest ORACLE_POINTS);
    # the latest later-labelled 10-K reporting the year, chosen by hand from dei.json
    ticker, family, fy, value, accession, original = case
    pick = s1_lookup_pick(kit.store_rows(ticker, [family]), fy)
    assert pick is not None
    assert pick["accession_no"] == accession
    assert pick["value"] == pytest.approx(value, abs=TOL)  # tagged in millions
    if original is not None:
        assert pick["value"] != pytest.approx(original, abs=TOL)
    assert kit.iso(pick["period_end"]) == kit.own_end(ticker, fy)
    assert pick["unit"] == "USD"


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from(GRID), st.randoms(use_true_random=False), st.data())
def test_row_order_and_unrelated_filings_never_change_the_pick(kit, item, rnd, data):
    # oracle: S1 depends only on the fy filing and the latest restating one (requirement 1)
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_lookup_pick(rows, fy)
    assert base is not None
    assert kit.s1_holds(base, rows, fy)
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    again = s1_lookup_pick(shuffled, fy)
    assert again is not None and again["fact_id"] == base["fact_id"]
    others = sorted(kit.filings_of(rows) - {kit.own_filing(ticker, fy), base["accession_no"]})
    drop = set(data.draw(st.lists(st.sampled_from(others), unique=True))) if others else set()
    kept = [r for r in rows if r["accession_no"] not in drop]
    after = s1_lookup_pick(kept, fy)
    assert after is not None and after["fact_id"] == base["fact_id"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from(GRID), st.integers(min_value=1, max_value=9))
def test_a_later_restating_report_becomes_the_pick(kit, item, bump):
    # oracle: S1 by hand — a canonical filing labelled later and filed last that reports the
    # period wins, with its own value
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_lookup_pick(rows, fy)
    assert base is not None
    synthetic = dict(base, accession_no="9999999999-30-000001", fiscal_year=2030,
                     filed_date="2030-01-31", document_period_end="2029-12-31",
                     fact_id=999_000_000 + bump, value=base["value"] + bump * 1e6)
    pick = s1_lookup_pick([*rows, synthetic], fy)
    assert pick is not None
    assert pick["accession_no"] == "9999999999-30-000001"
    assert pick["value"] == pytest.approx(base["value"] + bump * 1e6, abs=TOL)  # copied value
