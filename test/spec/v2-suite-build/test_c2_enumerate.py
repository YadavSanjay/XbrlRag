"""C2: ``enumerate_intents`` lists the candidates per shape from the inventory.

Oracle: requirement 7 of docs/specs/v2-suite-build.md, the candidates listed by hand for a
two-filer inventory.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import enumerate_intents

REV = {"concept": "us-gaap:Revenues", "unit": "USD"}
NI = {"concept": "us-gaap:NetIncomeLoss", "unit": "USD"}
NI_OTHER = {"concept": "us-gaap:ProfitLoss", "unit": "USD"}


def _inventory():
    # XA holds 2022-2024; its NetIncome concept changes in 2024. XB holds 2024 revenue only.
    return {
        "XA": {"cik": "0000900201", "years": {
            2022: {"Revenue": dict(REV), "NetIncome": dict(NI)},
            2023: {"Revenue": dict(REV), "NetIncome": dict(NI)},
            2024: {"Revenue": dict(REV), "NetIncome": dict(NI_OTHER)},
        }},
        "XB": {"cik": "0000900202", "years": {2024: {"Revenue": dict(REV)}}},
    }


def _universes():
    return {"covered": ["XA", "XB"], "grp": ["XB", "QQQ"]}


def _lk(t, y, m):
    return Intent("lookup", tickers=[t], fiscal_years=[y], metrics=[m])


def _cmp(op, t, years, metrics):
    return Intent("compute", operation=op, tickers=[t], fiscal_years=years, metrics=metrics)


def _hand_listed():
    """Requirement 7 applied by hand to ``_inventory`` and ``_universes``."""
    rn = ("Revenue", "NetIncome")
    out = {
        "lookup": [_lk("XA", y, m) for y in (2022, 2023, 2024) for m in rn]
        + [_lk("XB", 2024, "Revenue")],
        # (NetIncome, Revenue) is the only ratio pair present; same unit in every XA year
        "compute.ratio": [_cmp("ratio", "XA", [y], ["NetIncome", "Revenue"])
                          for y in (2022, 2023, 2024)],
        # NetIncome is consistent only over 2022-2023 (its 2024 concept differs)
        "compute.yoy_growth": [_cmp("yoy_growth", "XA", [2022, 2023], ["Revenue"]),
                               _cmp("yoy_growth", "XA", [2023, 2024], ["Revenue"]),
                               _cmp("yoy_growth", "XA", [2022, 2023], ["NetIncome"])],
        "compute.difference": [_cmp("difference", "XA", ys, ["Revenue"])
                               for ys in ([2022, 2023], [2022, 2024], [2023, 2024])]
        + [_cmp("difference", "XA", [2022, 2023], ["NetIncome"])],
        "compute.cagr": [_cmp("cagr", "XA", [2022, 2024], ["Revenue"])],
        "trend": [Intent("trend", tickers=["XA"], fiscal_years=[2022, 2023, 2024],
                         metrics=["Revenue"])],
        "point_in_time": [
            Intent("point_in_time", tickers=["XA"], fiscal_years=[y], filing_year=f,
                   metrics=["Revenue"]) for y, f in ((2022, 2023), (2022, 2024), (2023, 2024))
        ] + [Intent("point_in_time", tickers=["XA"], fiscal_years=[2022], filing_year=2023,
                    metrics=["NetIncome"])],
        "peer_relative": [
            Intent("peer_relative", tickers=["XA"], fiscal_years=[y], metrics=[m],
                   universe="covered") for y in (2022, 2023, 2024) for m in rn
        ] + [Intent("peer_relative", tickers=["XB"], fiscal_years=[2024], metrics=["Revenue"],
                    universe=u) for u in ("covered", "grp")],
    }
    # covered: every (year, metric) XA has (XB adds nothing new); grp: XB's 2024 revenue
    pairs = [("covered", y, m) for y in (2022, 2023, 2024) for m in rn] + [
        ("grp", 2024, "Revenue")]
    for op in ("top", "bottom", "sum", "median"):
        n = 3 if op in ("top", "bottom") else None
        out[f"screen.{op}"] = [Intent("screen", operation=op, universe=u, fiscal_years=[y],
                                      metrics=[m], n=n) for u, y, m in pairs]
    return out


@pytest.mark.criterion("C2")
def test_candidates_are_the_hand_listed_intents(kit):
    # oracle: requirement 7 applied by hand (_hand_listed): lookup 7, ratio 3, yoy 3,
    # difference 4, cagr 1, trend 1, point_in_time 4, peer_relative 8, each screen op 7
    got = enumerate_intents(_inventory(), _universes())
    expected = _hand_listed()
    assert set(got) == set(kit.SHAPES)
    counts = {s: len(v) for s, v in expected.items()}
    assert counts == {"lookup": 7, "compute.ratio": 3, "compute.yoy_growth": 3,
                      "compute.difference": 4, "compute.cagr": 1, "trend": 1,
                      "point_in_time": 4, "peer_relative": 8, "screen.top": 7,
                      "screen.bottom": 7, "screen.sum": 7, "screen.median": 7}
    for shape in kit.SHAPES:
        assert got[shape] == sorted(expected[shape], key=kit.intent_id), shape


@pytest.mark.criterion("C2")
def test_changed_concept_blocks_multi_year_intents_and_absent_member_adds_none(kit):
    # oracle: requirement 7 — NetIncome's 2024 concept differs, so no NetIncome intent spans
    # 2024; QQQ is not in the inventory, so no intent names it
    got = enumerate_intents(_inventory(), _universes())
    for shape in ("compute.yoy_growth", "compute.difference", "compute.cagr", "trend",
                  "point_in_time"):
        for intent in got[shape]:
            if intent.metrics == ("NetIncome",):
                years = set(intent.fiscal_years) | {intent.filing_year}
                assert 2024 not in years
    assert all("QQQ" not in i.tickers for intents in got.values() for i in intents)


_year_metrics = st.dictionaries(
    st.integers(min_value=2019, max_value=2026),
    st.sets(st.sampled_from(["Revenue", "NetIncome", "TotalAssets"]), min_size=1),
    min_size=1, max_size=4)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=30)
@given(years=_year_metrics)
def test_adding_a_filer_keeps_the_others_candidates(kit, years):
    # oracle: requirement 7 — a single-filer candidate depends only on its own filer's
    # inventory, so adding XC (not in any universe) leaves every XA/XB candidate unchanged
    base = enumerate_intents(_inventory(), _universes())
    inv = _inventory()
    concepts = {"Revenue": REV, "NetIncome": NI,
                "TotalAssets": {"concept": "us-gaap:Assets", "unit": "USD"}}
    inv["XC"] = {"cik": "0000900203",
                 "years": {y: {m: dict(concepts[m]) for m in ms} for y, ms in years.items()}}
    grown = enumerate_intents(inv, _universes())
    for shape in kit.SHAPES:
        if shape.startswith("screen."):
            continue
        kept = [i for i in grown[shape] if i.tickers != ("XC",)]
        assert kept == base[shape], shape


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_input_order_does_not_matter(kit):
    # oracle: requirement 7 — each list is sorted by intent id, so reversing the inventory,
    # its years, the universes and their members changes nothing
    base = enumerate_intents(_inventory(), _universes())
    assert set(base) == set(kit.SHAPES) and len(base["lookup"]) == 7
    inv = {t: {"cik": e["cik"], "years": dict(reversed(list(e["years"].items())))}
           for t, e in reversed(list(_inventory().items()))}
    uni = {u: list(reversed(m)) for u, m in reversed(list(_universes().items()))}
    assert enumerate_intents(inv, uni) == base
