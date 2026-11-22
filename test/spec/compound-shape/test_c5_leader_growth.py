"""C5: ``leader_growth`` — the leader is the first ranked row; its growth of metric B is the
``compute.yoy_growth`` key (S1 one filing, S4 definition); prior <= 0, a leader tie and any
other growth drop drop the intent with their reasons.

Oracle: requirement 4 of docs/specs/compound-shape.md, S1 and S4; hand arithmetic on the
conftest fixture table.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import key_intent


def _dependent(rec):
    assert [p["part"] for p in rec["parts"]] == ["ranking", "dependent"]
    return rec["parts"][1]


# (ranking metric, growth metric) -> (leader, growth), FY2023 -> FY2024, both values from
# the leader's FY2024 10-K (S1). Ranking FY2024: revenue DEVA 500 > DEVB 400; operating
# income DEVB 120 > DEVA 100; net income DEVA 60 > DEVB 48.
EXPECTED = {
    ("Revenue", "OperatingIncome"): ("DEVA", (100 - 90) / 90),
    ("Revenue", "NetIncome"): ("DEVA", (60 - 50) / 50),
    ("OperatingIncome", "Revenue"): ("DEVB", (400 - 420) / 420),
    ("OperatingIncome", "NetIncome"): ("DEVB", (48 - 40) / 40),
    ("NetIncome", "Revenue"): ("DEVA", (500 - 450) / 450),
    ("NetIncome", "OperatingIncome"): ("DEVA", (100 - 90) / 90),
}


@pytest.mark.criterion("C5")
def test_leader_and_growth_point(kit):
    # oracle: hand arithmetic in EXPECTED; growth = (B_Y - B_Y-1) / |B_Y-1|, alt = x 100
    result = kit.build()
    for (a, b), (leader, growth) in EXPECTED.items():
        rec = kit.case_of(result, kit.lg(2024, a, b))
        assert rec["expected_rows"][0]["ticker"] == leader
        dep = _dependent(rec)
        assert dep["kind"] == "compute"
        assert dep["ticker"] == leader
        assert dep["expected_value"] == pytest.approx(growth, rel=1e-12)  # float rounding only
        assert dep["alt_values"] == [pytest.approx(growth * 100, rel=1e-12)]  # same
    assert kit.dropped(result, kit.LG) == {}
    assert result["manifest"]["intents"]["selected"][kit.LG] == 6


@pytest.mark.criterion("C5")
def test_growth_reads_both_years_from_the_later_filing(kit):
    # oracle: S1 — DEVA's FY2023 operating income key is the 10-K/A's 92, but its FY2024
    # 10-K reports FY2023 as 90: growth (100 - 90) / 90, not (100 - 92) / 92; both inputs
    # come from the FY2024 10-K
    result = kit.build()
    rec = kit.case_of(result, kit.lg(2024, "NetIncome", "OperatingIncome"))
    dep = _dependent(rec)
    assert dep["expected_value"] == pytest.approx(1 / 9, rel=1e-12)  # float rounding only
    assert dep["expected_value"] != pytest.approx(8 / 92, rel=1e-6)
    growth_inputs = rec["key"]["inputs"][-2:]
    assert [k["accession"] for k in growth_inputs] == [kit.A24, kit.A24]
    assert [(k["period_start"], k["period_end"]) for k in growth_inputs] == [kit.Q23, kit.Q24]
    assert [k["concept"] for k in growth_inputs] == ["us-gaap:OperatingIncomeLoss"] * 2


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_growth_is_the_compute_yoy_growth_key(kit):
    # oracle: requirement 4 — the dependent equals key_intent of compute.yoy_growth
    # (leader, [Y-1, Y], B), value and inputs
    result = kit.build()
    docs = kit.companyfacts_docs()
    for (a, b), (leader, _growth) in EXPECTED.items():
        rec = kit.case_of(result, kit.lg(2024, a, b))
        keyed = key_intent(Intent("compute", operation="yoy_growth", tickers=[leader],
                                  fiscal_years=[2023, 2024], metrics=[b]),
                           kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs))
        assert _dependent(rec)["expected_value"] == pytest.approx(keyed["value"], rel=1e-12)
        assert rec["key"]["inputs"][-len(keyed["inputs"]):] == keyed["inputs"]


@pytest.mark.criterion("C5")
def test_drop_reasons(kit):
    # oracle: requirement 4 —
    # (a) DEVA's FY2023 net income -5: the revenue leader DEVA's net income growth has a
    #     prior <= 0: dependent_not_meaningful (only (Revenue, NetIncome) has leader DEVA
    #     and B = NetIncome);
    # (b) DEVB's FY2024 revenue 500 = DEVA's: the revenue ranking's first two values tie:
    #     leader_tie for both growth metrics (2);
    # (c) DEVA's FY2023 net income removed from every filing: its growth key is no_key:
    #     dependent:no_key (1)
    docs = kit.companyfacts_docs()
    for e in kit.facts_of(docs, "DEVA", "NetIncomeLoss"):
        if e["end"] == "2023-12-31":
            e["val"] = -5_000_000
    assert kit.dropped(kit.build(docs=docs), kit.LG) == {"dependent_not_meaningful": 1}

    docs = kit.companyfacts_docs()
    for e in kit.facts_of(docs, "DEVB", "Revenues"):
        if e["end"] == "2024-12-31":
            e["val"] = 500_000_000
    result = kit.build(docs=docs)
    assert kit.dropped(result, kit.LG) == {"leader_tie": 2}
    assert kit.cases_of(result, kit.lg(2024, "Revenue", "NetIncome")) == []

    docs = kit.companyfacts_docs()
    ni = kit.facts_of(docs, "DEVA", "NetIncomeLoss")
    ni[:] = [e for e in ni if e["end"] != "2023-12-31"]
    assert kit.dropped(kit.build(docs=docs), kit.LG) == {"dependent:no_key": 1}


@pytest.mark.criterion("C5")
@settings(max_examples=12)
@given(prior=st.integers(min_value=-20, max_value=40))
def test_prior_sign_property(kit, prior):
    # oracle: S4 — DEVA's FY2023 net income `prior` (millions) in both filings; the revenue
    # leader DEVA's net income growth is (60 - prior) / |prior| when prior > 0, else the
    # intent drops as dependent_not_meaningful (prior never reaches 60, so growth != 0)
    docs = kit.companyfacts_docs()
    for e in kit.facts_of(docs, "DEVA", "NetIncomeLoss"):
        if e["end"] == "2023-12-31":
            e["val"] = prior * 1_000_000
    result = kit.build(docs=docs)
    found = kit.cases_of(result, kit.lg(2024, "Revenue", "NetIncome"))
    if prior <= 0:
        assert found == []
        assert kit.dropped(result, kit.LG) == {"dependent_not_meaningful": 1}
    else:
        assert len(found) == 1
        dep = _dependent(found[0])
        assert dep["ticker"] == "DEVA"
        assert dep["expected_value"] == pytest.approx((60 - prior) / prior, rel=1e-12)  # rounding
