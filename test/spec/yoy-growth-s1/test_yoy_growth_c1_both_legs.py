"""C1: both legs of a YoY growth follow S1 (docs/specs/yoy-growth-s1.md, requirement 1).

Each leg is the year's own column as the latest later-labelled report restates it, as the
product's lookup answers it. Values are FSDS ``num`` of the named accession (checked when the
conftest loads); dates are the DEI ``DocumentPeriodEndDate``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.sql_derived import s1_yoy_pick

TOL = 5e4  # half of $0.1M: amounts tagged in millions or hundred-thousands
GROWTH_TOL = 1e-6  # FSDS values are exact integers in millions; the hand ratio is exact

# ticker, family, fy, unit, (current value, its accession), (prior value, its accession),
# values a leg must never take (as originally filed, or a convenience translation)
POINTS = [
    ("INTC", "Revenue", 2023, "USD",
     (54_228e6, "0000050863-26-000011"), (63_054e6, "0000050863-25-000009"), ()),
    ("INTC", "Revenue", 2024, "USD",
     (53_101e6, "0000050863-26-000011"), (54_228e6, "0000050863-26-000011"), ()),
    ("TSM", "NetIncome", 2023, "TWD",
     (851_027.7e6, "0001628280-26-025362"), (993_294.7e6, "0001193125-25-083423"),
     (27_793.2e6,)),
    ("INTC", "CashFromFinancing", 2023, "USD",
     (8_505e6, "0000050863-26-000011"), (1_115e6, "0000050863-25-000009"), (1_361e6,)),
    ("META", "Capex", 2023, "USD",
     (27_045e6, "0001628280-26-003942"), (31_186e6, "0001326801-25-000017"),
     (27_266e6, 31_431e6)),
]
# filer-measure-years with both own columns, for the properties
GRID = [("INTC", f, fy) for f in ("Revenue", "CashFromFinancing", "CashFromInvesting")
        for fy in (2023, 2024, 2025)] + [("META", "Capex", fy) for fy in (2023, 2024, 2025)] \
    + [("TSM", "NetIncome", fy) for fy in (2023, 2024, 2025)]


def _fact_ids(rows, accession, period_end, unit):
    """Store fact ids of one filing's rows at one period end and unit."""
    return {r["fact_id"] for r in rows if r["accession_no"] == accession
            and r["period_end"] == period_end and r["unit"] == unit}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", POINTS, ids=lambda c: f"{c[0]}-{c[1]}-FY{c[2]}")
def test_each_leg_is_the_latest_report_of_its_year(kit, case):
    # oracle: FSDS num of the restating accessions (conftest ORACLE_POINTS); the latest
    # later-labelled report of each year chosen by hand from dei.json; growth by hand
    ticker, family, fy, unit, (cur_v, cur_a), (pri_v, pri_a), never = case
    rows = kit.store_rows(ticker, [family])
    got = s1_yoy_pick(rows, fy)
    assert isinstance(got, dict)
    assert got["current"]["accession_no"] == cur_a
    assert got["prior"]["accession_no"] == pri_a
    assert got["current"]["value"] == pytest.approx(cur_v, abs=TOL)  # tagged in 0.1M
    assert got["prior"]["value"] == pytest.approx(pri_v, abs=TOL)  # tagged in 0.1M
    for bad in never:
        assert got["current"]["value"] != pytest.approx(bad, abs=TOL)
        assert got["prior"]["value"] != pytest.approx(bad, abs=TOL)
    assert kit.iso(got["current"]["period_end"]) == kit.own_end(ticker, fy)
    assert kit.iso(got["prior"]["period_end"]) == kit.own_end(ticker, fy - 1)
    assert got["unit"] == unit
    assert got["current"]["unit"] == unit and got["prior"]["unit"] == unit
    assert got["value"] == pytest.approx(cur_v / pri_v - 1, abs=GROWTH_TOL)  # exact ratio
    assert got["fy_prior"] == fy - 1
    assert got["not_meaningful"] is False
    assert got["fact_id_current"] == got["current"]["fact_id"]
    assert got["fact_id_prior"] == got["prior"]["fact_id"]
    assert got["fact_id_current"] in _fact_ids(rows, cur_a, kit.own_end(ticker, fy), unit)
    assert got["fact_id_prior"] in _fact_ids(rows, pri_a, kit.own_end(ticker, fy - 1), unit)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from(GRID), st.randoms(use_true_random=False), st.data())
def test_legs_hold_s1_and_ignore_row_order_and_unrelated_filings(kit, item, rnd, data):
    # oracle: S1 by hand (conftest s1_holds) for each leg; the growth by hand from the two
    # legs; S1 depends only on each year's own filing and its latest restating one
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_yoy_pick(rows, fy)
    assert isinstance(base, dict)
    assert kit.s1_holds(base["current"], rows, fy)
    assert kit.s1_holds(base["prior"], rows, fy - 1)
    c, p = base["current"]["value"], base["prior"]["value"]
    assert base["value"] == pytest.approx((c - p) / abs(p), abs=GROWTH_TOL)  # same floats
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    again = s1_yoy_pick(shuffled, fy)
    assert isinstance(again, dict)
    assert (again["fact_id_current"], again["fact_id_prior"]) == (
        base["fact_id_current"], base["fact_id_prior"])
    keep = {kit.own_filing(ticker, fy), kit.own_filing(ticker, fy - 1),
            base["current"]["accession_no"], base["prior"]["accession_no"]}
    others = sorted(kit.filings_of(rows) - keep)
    drop = set(data.draw(st.lists(st.sampled_from(others), unique=True))) if others else set()
    after = s1_yoy_pick([r for r in rows if r["accession_no"] not in drop], fy)
    assert isinstance(after, dict)
    assert (after["fact_id_current"], after["fact_id_prior"]) == (
        base["fact_id_current"], base["fact_id_prior"])
    assert after["value"] == pytest.approx(base["value"], abs=GROWTH_TOL)  # same rows


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from(GRID), st.sampled_from(["current", "prior"]),
       st.integers(min_value=1, max_value=9))
def test_a_later_restating_report_becomes_that_leg(kit, item, leg, bump):
    # oracle: S1 by hand — a canonical filing labelled later and filed last that reports the
    # period wins, with its own value; the growth is the hand ratio of the new legs
    ticker, family, fy = item
    rows = kit.store_rows(ticker, [family])
    base = s1_yoy_pick(rows, fy)
    assert isinstance(base, dict)
    restated = dict(base[leg], accession_no="9999999999-30-000001", fiscal_year=2030,
                    filed_date="2030-01-31", document_period_end="2029-12-31",
                    fact_id=999_000_000 + bump, value=base[leg]["value"] + bump * 1e9)
    got = s1_yoy_pick([*rows, restated], fy)
    assert isinstance(got, dict)
    other = "prior" if leg == "current" else "current"
    assert got[leg]["accession_no"] == "9999999999-30-000001"
    assert got[f"fact_id_{leg}"] == 999_000_000 + bump
    assert got[f"fact_id_{other}"] == base[f"fact_id_{other}"]
    c = got["current"]["value"]
    p = got["prior"]["value"]
    assert (c if leg == "current" else p) == pytest.approx(
        base[leg]["value"] + bump * 1e9, abs=TOL)  # the copied value plus the bump
    assert got["value"] == pytest.approx((c - p) / abs(p), abs=GROWTH_TOL)  # same floats
    assert got["value"] != pytest.approx(base["value"], abs=GROWTH_TOL)
