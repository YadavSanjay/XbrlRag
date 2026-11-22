"""C7: ``check_plan_perturbation`` — the plans differ in exactly the perturbed slots.

Oracle: requirements 8-10 of docs/specs/keyfree-checks.md. Every perturbed plan is built by
hand (or, in the property, by applying the perturbation rule in the test), and the
expected differences are listed by hand.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.perturbation import check_plan_perturbation

AAPL_CIK = "0000320193"
MSFT_CIK = "0000789019"
SWAP = {"kind": "ticker", "from": "AAPL", "to": "MSFT", "from_cik": AAPL_CIK,
        "to_cik": MSFT_CIK}
SWAP_NO_CIK = {"kind": "ticker", "from": "AAPL", "to": "MSFT"}
YEAR = {"kind": "year", "from": 2024, "to": 2023}


def lookup(sid, ticker, cik, fy, concept="Revenue"):
    return {"id": sid, "op": "lookup", "cik": cik, "ticker": ticker, "fy": fy,
            "concept_family": concept, "dimensions": "NONE", "section_hint": "Income Statement"}


def yoy(ticker="AAPL", cik=AAPL_CIK, fy=2024, ids=("r24", "r23")):
    a, b = ids
    return {"steps": [lookup(a, ticker, cik, fy), lookup(b, ticker, cik, fy - 1),
                      {"id": "g", "op": "compute", "expr": f"({a} - {b}) / {b}"}]}


def diff(step, slot, original, perturbed):
    return {"step": step, "slot": slot, "original": original, "perturbed": perturbed}


@pytest.mark.criterion("C7")
def test_ticker_swap_with_ciks_is_consistent():
    # oracle: requirement 10 — ticker and cik change in both lookups exactly as the
    # perturbation says; step ids differ (not slots); changed lists the 4 slots in step
    # order, then slot order (cik before ticker)
    perturbed = yoy("MSFT", MSFT_CIK, ids=("m24", "m23"))
    v = check_plan_perturbation(yoy(), perturbed, SWAP)
    assert v["check"] == "plan_perturbation"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    assert v["differences"] == []
    assert v["changed"] == [
        diff(0, "cik", AAPL_CIK, MSFT_CIK), diff(0, "ticker", "AAPL", "MSFT"),
        diff(1, "cik", AAPL_CIK, MSFT_CIK), diff(1, "ticker", "AAPL", "MSFT"),
    ]


@pytest.mark.criterion("C7")
def test_ticker_swap_without_ciks_accepts_any_cik_on_swapped_steps():
    # oracle: requirement 10 — without from_cik/to_cik, the cik slot may take any value on a
    # step whose original ticker is `from`
    for cik in (MSFT_CIK, "0000000001", None):
        v = check_plan_perturbation(yoy(), yoy("MSFT", cik), SWAP_NO_CIK)
        assert v["verdict"] == "consistent", cik


@pytest.mark.criterion("C7")
def test_one_lookup_left_on_the_old_ticker_is_a_wrong_change():
    # oracle: requirement 10 — step 1 keeps AAPL and Apple's cik: both perturbed slots miss
    # their expected value
    perturbed = yoy("MSFT", MSFT_CIK)
    perturbed["steps"][1]["ticker"] = "AAPL"
    perturbed["steps"][1]["cik"] = AAPL_CIK
    v = check_plan_perturbation(yoy(), perturbed, SWAP)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "wrong_change"
    assert v["differences"] == [diff(1, "cik", AAPL_CIK, AAPL_CIK),
                                diff(1, "ticker", "AAPL", "AAPL")]


@pytest.mark.criterion("C7")
def test_a_changed_concept_is_an_unexpected_change_and_wins():
    # oracle: requirement 10 — a non-perturbed slot changed: unexpected_change, which comes
    # before wrong_change; differences list both, step then slot order (cik < ticker <
    # concept_family is the slot order: cik, ticker, ..., concept_family)
    perturbed = yoy("MSFT", MSFT_CIK)
    perturbed["steps"][0]["concept_family"] = "NetIncome"
    perturbed["steps"][1]["ticker"] = "AAPL"
    perturbed["steps"][1]["cik"] = MSFT_CIK
    v = check_plan_perturbation(yoy(), perturbed, SWAP)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unexpected_change"
    assert v["differences"] == [diff(0, "concept_family", "Revenue", "NetIncome"),
                                diff(1, "ticker", "AAPL", "AAPL")]


@pytest.mark.criterion("C7")
def test_year_shift_moves_every_fy():
    # oracle: requirement 8 — a year perturbation 2024 -> 2023 is a shift by -1: fy 2024 and
    # 2023 become 2023 and 2022
    v = check_plan_perturbation(yoy(fy=2024), yoy(fy=2023, ids=("x", "y")), YEAR)
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    assert v["changed"] == [diff(0, "fy", 2024, 2023), diff(1, "fy", 2023, 2022)]


@pytest.mark.criterion("C7")
def test_year_shift_of_only_one_step_is_a_wrong_change():
    # oracle: requirement 10 — step 1 expected 2023 - 1 = 2022, got 2023
    perturbed = yoy(fy=2024)
    perturbed["steps"][0]["fy"] = 2023
    v = check_plan_perturbation(yoy(fy=2024), perturbed, YEAR)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "wrong_change"
    assert v["differences"] == [diff(1, "fy", 2023, 2023)]


@pytest.mark.criterion("C7")
def test_a_ticker_change_under_a_year_perturbation_is_unexpected():
    # oracle: requirement 10 — under a year perturbation ticker/cik are ordinary slots
    v = check_plan_perturbation(yoy(), yoy("MSFT", MSFT_CIK, fy=2023), YEAR)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unexpected_change"


@pytest.mark.criterion("C7")
def test_structural_mismatches():
    # oracle: requirement 10 — an extra step -> step_count; a lookup turned into a
    # narrative -> op_mismatch (both inconsistent, with no differences)
    extra = yoy("MSFT", MSFT_CIK)
    extra["steps"].append({"id": "n", "op": "narrative", "section_hint": "MD&A"})
    v = check_plan_perturbation(yoy(), extra, SWAP)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "step_count")
    assert v["differences"] == []
    narr = yoy("MSFT", MSFT_CIK)
    narr["steps"][1] = {"id": "r23", "op": "narrative", "section_hint": "MD&A"}
    v = check_plan_perturbation(yoy(), narr, SWAP)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "op_mismatch")


@pytest.mark.criterion("C7")
def test_screen_plan_under_a_ticker_swap():
    # oracle: requirement 10 — a screen names no ticker: unchanged -> not_applicable
    # "slot_absent"; with the order flipped -> unexpected_change
    screen = {"steps": [{"id": "s", "op": "screen", "fy": 2024, "concept_family": "Revenue",
                         "dimension": "NONE", "metric_kind": "value", "order": "desc"}]}
    v = check_plan_perturbation(screen, copy.deepcopy(screen), SWAP)
    assert (v["verdict"], v["reason"]) == ("not_applicable", "slot_absent")
    flipped = copy.deepcopy(screen)
    flipped["steps"][0]["order"] = "asc"
    v = check_plan_perturbation(screen, flipped, SWAP)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "unexpected_change")


@pytest.mark.criterion("C7")
def test_error_plan_is_not_applicable():
    # oracle: requirement 10 — a plan without a non-empty steps list -> no_plan
    for a, b in ((yoy(), {"error": "cannot plan"}), ({"steps": []}, yoy())):
        v = check_plan_perturbation(a, b, SWAP)
        assert (v["verdict"], v["reason"]) == ("not_applicable", "no_plan")


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("pert", [
    {"kind": "metric", "from": "Revenue", "to": "NetIncome"},
    {"kind": "ticker", "from": "aapl", "to": "AAPL"},
    {"kind": "year", "from": 2024, "to": 2024},
])
def test_bad_perturbation_raises(pert):
    # oracle: requirement 10 — unknown kind, or from == to (tickers upper-cased) -> ValueError
    with pytest.raises(ValueError):
        check_plan_perturbation(yoy(), yoy(), pert)


_step = st.tuples(st.sampled_from(["AAPL", "MSFT", "NVDA"]),
                  st.integers(min_value=2020, max_value=2026),
                  st.sampled_from(["Revenue", "NetIncome", "OperatingIncome"]))


def _plan(specs, prefix="s"):
    return {"steps": [{"id": f"{prefix}{i}", "op": "lookup", "ticker": t, "fy": fy,
                       "concept_family": c, "dimensions": "NONE", "section_hint": "x"}
                      for i, (t, fy, c) in enumerate(specs)]}


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(specs=st.lists(_step, min_size=1, max_size=6),
       kind=st.sampled_from(["ticker", "year"]),
       delta=st.sampled_from([-2, -1, 1]),
       which=st.integers(min_value=0, max_value=5))
def test_applying_the_perturbation_is_consistent_and_one_more_change_is_not(
        specs, kind, delta, which):
    # oracle: requirements 8-10 — the perturbed plan is built here by the rule (ticker
    # AAPL -> GOOGL where it is AAPL; every fy shifted by delta), with new step ids: it is
    # consistent unless no slot is a target (no AAPL step under a ticker swap: slot_absent).
    # Changing one step's concept_family on top is always inconsistent
    if kind == "ticker":
        pert = {"kind": "ticker", "from": "AAPL", "to": "GOOGL"}
        moved = [("GOOGL" if t == "AAPL" else t, fy, c) for t, fy, c in specs]
        has_target = any(t == "AAPL" for t, _, _ in specs)
    else:
        pert = {"kind": "year", "from": 2024, "to": 2024 + delta}
        moved = [(t, fy + delta, c) for t, fy, c in specs]
        has_target = True
    v = check_plan_perturbation(_plan(specs), _plan(moved, prefix="p"), pert)
    if has_target:
        assert (v["verdict"], v["reason"]) == ("consistent", "match")
    else:
        assert (v["verdict"], v["reason"]) == ("not_applicable", "slot_absent")
    i = which % len(moved)
    t, fy, c = moved[i]
    moved[i] = (t, fy, "Capex" if c != "Capex" else "Revenue")
    v = check_plan_perturbation(_plan(specs), _plan(moved, prefix="p"), pert)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "unexpected_change")
