"""C8: intents with no key are dropped with a reason, counted, never emitted or guessed.

Oracle: requirements 9 and 12 of docs/specs/v2-suite-build.md; candidate and drop counts
worked out by hand from the conftest inventory and payloads.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, build_v2_suites, key_intent


def _reason(kit, intent, docs=None):
    docs = kit.companyfacts_docs() if docs is None else docs
    with pytest.raises(IntentDroppedError) as err:
        key_intent(intent, kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs))
    return err.value.args[0]


def _lookup(t, y, m):
    return Intent("lookup", tickers=[t], fiscal_years=[y], metrics=[m])


def _build(kit, quotas):
    docs = kit.companyfacts_docs()
    return build_v2_suites(kit.inventory(), kit.universes(), kit.sec(docs), kit.store(docs),
                           kit.phrasebook, kit.lexicon, quotas=quotas, per_intent=1,
                           share=0.5, salt="v2-test")


@pytest.mark.criterion("C8")
def test_key_errors_drop_with_their_reason(kit):
    # oracle: payload — CCC reports Revenues only in EUR (inventory: USD) and has no
    # NetIncomeLoss at all
    assert _reason(kit, _lookup("CCC", 2024, "Revenue")) == "unit_mismatch"
    assert _reason(kit, _lookup("CCC", 2024, "NetIncome")) == "no_key"


@pytest.mark.criterion("C8")
def test_same_day_different_values_are_ambiguous(kit):
    # oracle: sec-keys requirement 5 — a second 10-K filed the same day as B24 with another
    # FY2024 revenue makes the latest-filed key ambiguous
    docs = kit.companyfacts_docs()
    start, end = kit.Q24
    docs[kit.BBB_CIK]["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": start, "end": end, "val": 221 * kit.BN, "accn": "0000900102-25-000022",
         "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-14"})
    assert _reason(kit, _lookup("BBB", 2024, "Revenue"), docs) == "ambiguous"


@pytest.mark.criterion("C8")
def test_zero_value_and_zero_denominator(kit):
    # oracle: requirement 9 — with A24's FY2024 net income set to 8bn (= FY2023's 8bn in
    # A24) the difference is 0; with A24's FY2024 revenue set to 0 the net margin divides
    # by zero
    docs = kit.companyfacts_docs()
    for e in docs[kit.AAA_CIK]["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]:
        if e["accn"] == kit.A24 and e["end"] == kit.P24[1]:
            e["val"] = 8 * kit.BN
    for e in docs[kit.AAA_CIK]["facts"]["us-gaap"]["Revenues"]["units"]["USD"]:
        if e["accn"] == kit.A24 and e["end"] == kit.P24[1]:
            e["val"] = 0
    diff = Intent("compute", operation="difference", tickers=["AAA"],
                  fiscal_years=[2023, 2024], metrics=["NetIncome"])
    assert _reason(kit, diff, docs) == "zero_value"
    ratio = Intent("compute", operation="ratio", tickers=["AAA"], fiscal_years=[2024],
                   metrics=["NetIncome", "Revenue"])
    assert _reason(kit, ratio, docs) == "zero_denominator"


@pytest.mark.criterion("C8")
def test_screen_drops(kit):
    # oracle: inventory — FY2022 revenue: only AAA ranks; CCC's own revenue is unit-mismatched,
    # so CCC as a peer-relative subject is not ranked
    screen = Intent("screen", operation="median", universe="covered", fiscal_years=[2022],
                    metrics=["Revenue"])
    assert _reason(kit, screen) == "too_few_ranked"
    peer = Intent("peer_relative", tickers=["CCC"], fiscal_years=[2024], metrics=["Revenue"],
                  universe="covered")
    assert _reason(kit, peer) == "subject_not_ranked"


@pytest.mark.criterion("C8")
def test_build_counts_drops_and_never_emits_them(kit):
    # oracle: hand count. lookup candidates: AAA 3 years x 3 metrics = 9, BBB 2 x 2 = 4,
    # CCC 2, TWX 1 = 16; CCC's two drop (unit_mismatch, no_key): 14 selected.
    # screen.top: per universe (covered, peers) 3 years x 3 metrics = 9 each, 18 in all;
    # every FY2022 pair and every TotalAssets pair has one ranked filer (AAA): 5 drops per
    # universe, 10 too_few_ranked; 8 selected.
    build = _build(kit, {s: 100 for s in kit.SHAPES})
    intents = build["manifest"]["intents"]
    assert intents["candidates"]["lookup"] == 16
    assert intents["selected"]["lookup"] == 14
    assert intents["dropped_by_shape"]["lookup"] == {"unit_mismatch": 1, "no_key": 1}
    assert intents["candidates"]["screen.top"] == 18
    assert intents["selected"]["screen.top"] == 8
    assert intents["dropped_by_shape"]["screen.top"] == {"too_few_ranked": 10}
    cases = build["dev"]["cases"] + build["validation"]["cases"]
    assert cases
    assert all(c.get("ticker") != "CCC" for c in cases)


@pytest.mark.criterion("C8")
def test_walk_moves_past_drops_to_fill_the_quota(kit):
    # oracle: requirement 12 — screen.top has 8 keyable candidates, so a quota of 8 is met
    # exactly whatever order the drops come in; a quota of 9 is not (8 selected)
    for quota, selected in ((8, 8), (9, 8), (3, 3)):
        build = _build(kit, {"screen.top": quota})
        assert build["manifest"]["intents"]["selected"]["screen.top"] == selected
        assert build["manifest"]["intents"]["selected"]["lookup"] == 0


@pytest.mark.criterion("C8")
@settings(max_examples=15)
@given(quotas=st.fixed_dictionaries({s: st.integers(min_value=0, max_value=12) for s in (
    "lookup", "compute.ratio", "screen.top", "screen.bottom", "screen.sum", "screen.median",
    "peer_relative", "compute.yoy_growth", "compute.difference", "compute.cagr", "trend",
    "point_in_time")}))
def test_quota_accounting(kit, quotas):
    # oracle: requirement 12 — the walk stops at the quota or when candidates run out
    intents = _build(kit, quotas)["manifest"]["intents"]
    assert intents["quota"] == quotas
    for shape, quota in quotas.items():
        selected = intents["selected"][shape]
        dropped = sum(intents["dropped_by_shape"].get(shape, {}).values())
        assert selected <= quota
        assert selected + dropped <= intents["candidates"][shape]
        if selected < quota:
            assert selected + dropped == intents["candidates"][shape]
    total = {}
    for per_shape in intents["dropped_by_shape"].values():
        for reason, n in per_shape.items():
            total[reason] = total.get(reason, 0) + n
    assert intents["dropped"] == total
