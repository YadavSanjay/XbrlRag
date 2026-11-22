"""C4: ``rank_by_ratio`` — the winner is the ranked filer with the largest S4 ratio; each
ratio is that filer's ``compute.ratio`` key; missing inputs, mixed units and 3-significant-
figure ties drop the intent with their reasons.

Oracle: requirement 4 of docs/specs/compound-shape.md and S4; hand arithmetic on the
conftest fixture table.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import key_intent

M = 1_000_000
# Base drop counts by hand: rd_intensity and current_ratio over the 6 rankings have no
# inputs in any filer's inventory (12); net_margin over the three FY2024 rankings, all of
# DEVA, DEVB, DEVC, ties DEVA 60/500 = 0.12 with DEVB 48/400 = 0.12 (3).
BASE_DROPS = {"dependent:metric_not_tagged": 12, "dependent_tie": 3}


def _dependent(rec):
    assert [p["part"] for p in rec["parts"]] == ["ranking", "dependent"]
    return rec["parts"][1]


@pytest.mark.criterion("C4")
def test_winner_and_ratio_point(kit):
    # oracle: fixture — FY2024 top 3 by revenue A 500, B 400, C 300; operating margins
    # A 100/500 = 0.2, B 120/400 = 0.3, C 75/300 = 0.25: DEVB wins at 0.3.
    # FY2023 by revenue A 450, B 420, C 280; FY2023 keys are the latest filed, so DEVA's
    # operating income is the 10-K/A's 92: A 92/450, B 100/420, C 70/280 = 0.25: DEVC.
    # FY2023 by net income A 50, B 40, D 4 (C -10 is fourth): A 92/450, B 100/420,
    # D 5/150: DEVB at 100/420
    result = kit.build()
    for kw, winner, ratio in (
            (kit.rbr(2024, "Revenue", "operating_margin"), "DEVB", 120 / 400),
            (kit.rbr(2024, "NetIncome", "operating_margin"), "DEVB", 120 / 400),
            (kit.rbr(2023, "Revenue", "operating_margin"), "DEVC", 70 / 280),
            (kit.rbr(2023, "NetIncome", "operating_margin"), "DEVB", 100 / 420),
            (kit.rbr(2023, "Revenue", "net_margin"), "DEVA", 50 / 450)):
        dep = _dependent(kit.case_of(result, kw))
        assert dep["ticker"] == winner, kw
        assert dep["expected_value"] == pytest.approx(ratio, rel=1e-12)  # float rounding only
    assert kit.dropped(result, kit.RBR) == BASE_DROPS


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_ratios_are_the_compute_ratio_keys(kit):
    # oracle: requirement 4 — each of the n filers' ratio is keyed exactly as its
    # compute.ratio intent; the dependent inputs follow the ranking's, filer by filer in
    # ranking order, numerator then denominator
    result = kit.build()
    docs = kit.companyfacts_docs()
    kw = kit.rbr(2024, "Revenue", "operating_margin")
    rec = kit.case_of(result, kw)
    screen = key_intent(Intent("screen", operation="top", n=3, universe="big",
                               fiscal_years=[2024], metrics=["Revenue"]),
                        kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs))
    dependent_inputs = rec["key"]["inputs"][len(screen["inputs"]):]
    ratios = {}
    expected_inputs = []
    for row in screen["rows"][:3]:
        keyed = key_intent(Intent("compute", operation="ratio", tickers=[row["ticker"]],
                                  fiscal_years=[2024], metrics=["OperatingIncome", "Revenue"]),
                           kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs))
        ratios[row["ticker"]] = keyed["value"]
        expected_inputs += keyed["inputs"]
    assert dependent_inputs == expected_inputs
    winner = max(ratios, key=ratios.get)
    assert _dependent(rec)["ticker"] == winner
    assert _dependent(rec)["expected_value"] == pytest.approx(ratios[winner], rel=1e-12)  # same


@pytest.mark.criterion("C4")
def test_missing_input_drops_as_dependent_reason(kit):
    # oracle: requirement 4 — DEVC's FY2024 operating income removed from its payload: its
    # compute.ratio key is no_key. Rankings holding DEVC with operating_margin: FY2024 by
    # revenue and by net income (2). FY2024 by operating income now excludes DEVC (no_key)
    # and ranks B, A, D: DEVB wins, kept
    docs = kit.companyfacts_docs()
    oil = kit.facts_of(docs, "DEVC", "OperatingIncomeLoss")
    oil[:] = [e for e in oil if e["end"] != "2024-12-31"]
    result = kit.build(docs=docs)
    assert kit.dropped(result, kit.RBR) == {**BASE_DROPS, "dependent:no_key": 2}
    assert kit.cases_of(result, kit.rbr(2024, "Revenue", "operating_margin")) == []
    dep = _dependent(kit.case_of(result, kit.rbr(2024, "OperatingIncome", "operating_margin")))
    assert dep["ticker"] == "DEVB"


@pytest.mark.criterion("C4")
def test_mixed_units_drop_as_unit_mismatch(kit):
    # oracle: requirement 4, S6 — DEVC's FY2024 operating income is in EUR (inventory and
    # payload) while its revenue is USD: the two rankings by revenue and by net income that
    # hold DEVC drop with operating_margin as dependent:unit_mismatch; the ranking by
    # operating income excludes DEVC (currency) and keeps DEVB as winner
    docs = kit.companyfacts_docs()
    oil = docs[kit.CIK["DEVC"]]["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]
    eur = [e for e in oil["USD"] if e["end"] == "2024-12-31"]
    oil["USD"] = [e for e in oil["USD"] if e["end"] != "2024-12-31"]
    oil["EUR"] = eur
    inv = kit.inventory()
    inv["DEVC"]["years"][2024]["OperatingIncome"]["unit"] = "EUR"
    result = kit.build(docs=docs, inv=inv)
    assert kit.dropped(result, kit.RBR) == {**BASE_DROPS, "dependent:unit_mismatch": 2}
    assert kit.cases_of(result, kit.rbr(2024, "NetIncome", "operating_margin")) == []


@pytest.mark.criterion("C4")
def test_tie_at_three_significant_figures(kit):
    # oracle: requirement 4 — DEVA's FY2024 net income 60.1: 60.1/500 = 0.1202, DEVB 0.12:
    # equal at 3 significant figures (0.120), still 3 ties. At 61: 0.122 > 0.120, DEVA wins
    # every FY2024 net_margin ranking, no tie left
    for value, ties in ((60_100_000, 3), (61_000_000, 0)):
        docs = kit.companyfacts_docs()
        for e in kit.facts_of(docs, "DEVA", "NetIncomeLoss"):
            if e["end"] == "2024-12-31":
                e["val"] = value
        result = kit.build(docs=docs)
        drops = kit.dropped(result, kit.RBR)
        assert drops.get("dependent_tie", 0) == ties
        assert drops["dependent:metric_not_tagged"] == 12
        if not ties:
            dep = _dependent(kit.case_of(result, kit.rbr(2024, "Revenue", "net_margin")))
            assert dep["ticker"] == "DEVA"
            assert dep["expected_value"] == pytest.approx(0.122, rel=1e-12)  # 61/500 exactly


@pytest.mark.criterion("C4")
@settings(max_examples=12)
@given(k=st.integers(min_value=550, max_value=700))
def test_tie_band_property(kit, k):
    # oracle: hand arithmetic — DEVA's FY2024 net income k x 0.1m gives margin k/5000; DEVB
    # 0.12, DEVC 0.1. The two largest are A and B; they agree at 3 significant figures iff
    # 0.1195 <= k/5000 < 0.1205, i.e. 598 <= k <= 602 (k never hits the boundaries)
    docs = kit.companyfacts_docs()
    for e in kit.facts_of(docs, "DEVA", "NetIncomeLoss"):
        if e["end"] == "2024-12-31":
            e["val"] = k * 100_000
    result = kit.build(docs=docs)
    found = kit.cases_of(result, kit.rbr(2024, "Revenue", "net_margin"))
    if 598 <= k <= 602:
        assert found == []
        assert kit.dropped(result, kit.RBR)["dependent_tie"] == 3
    else:
        assert len(found) == 1
        dep = _dependent(found[0])
        if k > 602:
            assert dep["ticker"] == "DEVA"
            assert dep["expected_value"] == pytest.approx(k / 5000, rel=1e-12)  # rounding only
        else:
            assert dep["ticker"] == "DEVB"
            assert dep["expected_value"] == pytest.approx(0.12, rel=1e-12)  # 48/400


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=10)
@given(members=st.permutations(["DEVA", "DEVB", "DEVC", "DEVD", "DEVE"]),
       filers=st.permutations(["DEVA", "DEVB", "DEVC", "DEVD", "DEVE"]))
def test_order_of_members_and_filers_never_changes_the_winner(kit, members, filers):
    # oracle: requirement 4 — the winner depends on the ratios only: DEVB at 0.3
    inv = kit.inventory()
    inv = {t: inv[t] for t in filers}
    result = kit.build(inv=inv, uni={"big": list(members)})
    dep = _dependent(kit.case_of(result, kit.rbr(2024, "Revenue", "operating_margin")))
    assert dep["ticker"] == "DEVB"
    assert dep["expected_value"] == pytest.approx(0.3, rel=1e-12)  # 120/400, rounding only
