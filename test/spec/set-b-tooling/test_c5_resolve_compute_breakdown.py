"""C5: ``resolve_case`` for compute and breakdown cases, and ``recompute``.

Oracle: requirement 7 of docs/specs/set-b-tooling.md. The arithmetic for every formula is
shown in the test; the expected classes are read off the hand-built filer-years in
``_store`` with requirement 6.
"""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import recompute, resolve_case

M = 1e6
K = 1e3


def _store(kit):
    """MSFT FY2025 and FY2024 candidates (synthetic)."""
    c = kit.cand
    fy25 = [
        c(51, "Revenues", 1125 * M),
        c(52, "OperatingIncomeLoss", 50 * M),
        c(53, "CostsAndExpenses", 200 * M),
        c(54, "Revenues", 300 * M, member="Cloud"),
        c(55, "GrossProfit", 300 * M),                 # a flat decoy for the member input
        c(56, "ResearchAndDevelopmentExpense", 120 * M),
        c(57, "OtherIncome", 10 * K),
        c(58, "InterestIncome", 20 * K),
        c(70, "ZeroBalance", 0.0),
        c(71, "AccountsPayable", 777 * M),             # 777 twice -> ambiguous
        c(72, "AccruedLiabilities", 777 * M),
        c(81, "Revenues", 10 * M, member="Alpha Segment"),
        c(82, "Revenues", 20 * M, member="Beta Segment"),
        c(83, "Revenues", 30 * M, member="Gamma Segment"),
        c(84, "Revenues", 40 * M, member="Delta Segment"),
        c(85, "OperatingIncomeLoss", 40 * M, member="Delta Segment"),
    ]
    fy24 = [
        c(61, "Revenues", 1000 * M, fy=2024),
        c(59, "OtherExpense", 30 * K, fy=2024),
    ]
    return kit.FakeStore({("MSFT", 2025): fy25, ("MSFT", 2024): fy24})


def _inputs(got):
    return [(i["name"], i["status"], i["classes"]) for i in got["inputs"]]


@pytest.mark.criterion("C5")
def test_yoy_verified_reading_the_prior_year(kit):
    # oracle: requirement 7 — yoy = (current - prior) / |prior| = (1125e6 - 1000e6) / 1000e6
    # = 0.125 = "12.5%"; prior has fy 2024 and 1000e6 exists only in FY2024 (fact 61)
    store = _store(kit)
    case = kit.compute("y1", formula="yoy", expected="12.5%",
                       inputs=[kit.inp("current", "1,125"), kit.inp("prior", "1,000", fy=2024)])
    got = resolve_case(case, store)
    assert got["id"] == "y1"
    assert got["status"] == "verified"
    assert got["classes"] == [[51], [61]]
    assert _inputs(got) == [("current", "verified", [[51]]), ("prior", "verified", [[61]])]
    assert Counter(store.calls) == Counter({("MSFT", 2025): 1, ("MSFT", 2024): 1})


@pytest.mark.criterion("C5")
def test_ratio_verified(kit):
    # oracle: requirement 7 — ratio = numerator / denominator = 50e6 / 200e6 = 0.25;
    # expected "0.25" (half_unit 0.005)
    case = kit.compute("r1", formula="ratio", expected="0.25",
                       inputs=[kit.inp("numerator", "50"), kit.inp("denominator", "200")])
    got = resolve_case(case, _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[52], [53]]


@pytest.mark.criterion("C5")
def test_difference_with_a_dimensional_input(kit):
    # oracle: requirement 7 — difference = a - b = 300e6 - 120e6 = 180e6 = "180" millions;
    # input a has a member, so it is dimensional and resolves to fact 54, not the flat 55
    case = kit.compute("d1", formula="difference", expected="180", scale="millions",
                       inputs=[kit.inp("a", "300", member="cloud"), kit.inp("b", "120")])
    got = resolve_case(case, _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[54], [56]]
    assert _inputs(got) == [("a", "verified", [[54]]), ("b", "verified", [[56]])]


@pytest.mark.criterion("C5")
def test_sum_across_years_calls_each_year_once(kit):
    # oracle: requirement 7 — sum = 10e3 + 20e3 + 30e3 = 60e3 = "60" thousands; x and y
    # read FY2025, z reads FY2024; facts_for is called at most once per (ticker, fy)
    store = _store(kit)
    case = kit.compute("s1", formula="sum", expected="60", scale="thousands",
                       inputs=[kit.inp("x", "10", scale="thousands"),
                               kit.inp("y", "20", scale="thousands"),
                               kit.inp("z", "30", scale="thousands", fy=2024)])
    got = resolve_case(case, store)
    assert got["status"] == "verified"
    assert got["classes"] == [[57], [58], [59]]
    assert Counter(store.calls) == Counter({("MSFT", 2025): 1, ("MSFT", 2024): 1})


@pytest.mark.criterion("C5")
def test_relative_arm_of_the_arith_tolerance(kit):
    # oracle: requirement 7 — expected "180.1" millions: half_unit 0.05e6, 1e-3 x 180.1e6
    # = 180100; |180e6 - 180.1e6| = 100000 <= 180100 -> verified
    case = kit.compute("d2", formula="difference", expected="180.1", scale="millions",
                       inputs=[kit.inp("a", "300", member="cloud"), kit.inp("b", "120")])
    assert resolve_case(case, _store(kit))["status"] == "verified"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("expected", ["13.5%", "12.6%"])
def test_off_arithmetic_is_arith_mismatch(kit, expected):
    # oracle: requirement 7 — inputs give 0.125; "13.5%" is 0.01 off and "12.6%" 0.001 off,
    # both above max(0.0005, 1e-3 x 0.126 = 0.000126); inputs verified, so arith_mismatch
    case = kit.compute("y2", formula="yoy", expected=expected,
                       inputs=[kit.inp("current", "1,125"), kit.inp("prior", "1,000", fy=2024)])
    got = resolve_case(case, _store(kit))
    assert got["status"] == "arith_mismatch"
    assert _inputs(got) == [("current", "verified", [[51]]), ("prior", "verified", [[61]])]


@pytest.mark.criterion("C5")
def test_unresolved_input_status_is_passed_up(kit):
    # oracle: requirement 7 — status rule 1: the first non-verified input status wins,
    # before the arithmetic check. prior "999" is not in FY2024 -> not_found; with current
    # "777" (ambiguous, facts 71 and 72) first, ambiguous is passed up
    store = _store(kit)
    case = kit.compute("u1", formula="yoy", expected="12.5%",
                       inputs=[kit.inp("current", "1,125"), kit.inp("prior", "999", fy=2024)])
    got = resolve_case(case, store)
    assert got["status"] == "not_found"
    assert _inputs(got)[1] == ("prior", "not_found", [])
    case = kit.compute("u2", formula="yoy", expected="12.5%",
                       inputs=[kit.inp("current", "777"), kit.inp("prior", "999", fy=2024)])
    got = resolve_case(case, _store(kit))
    assert got["status"] == "ambiguous"
    assert [i["status"] for i in got["inputs"]] == ["ambiguous", "not_found"]


@pytest.mark.criterion("C5")
def test_zero_denominator_is_arith_mismatch(kit):
    # oracle: requirement 7 — denominator "0" in units (half_unit 0.5) matches only fact 70
    # (value 0), so both inputs are
    # verified; recompute divides by zero -> None -> arith_mismatch
    case = kit.compute("z1", formula="ratio", expected="0",
                       inputs=[kit.inp("numerator", "50"), kit.inp("denominator", "0", scale=None)])
    got = resolve_case(case, _store(kit))
    assert [i["status"] for i in got["inputs"]] == ["verified", "verified"]
    assert got["status"] == "arith_mismatch"


@pytest.mark.criterion("C5")
def test_recompute_formulas_and_zero_denominators():
    # oracle: requirement 7, arithmetic by hand —
    # yoy (110 - 100) / |100| = 0.1; yoy (-50 - -100) / |-100| = 50 / 100 = 0.5;
    # ratio 3 / 4 = 0.75; difference 5 - 8 = -3; sum 1 + 2 + 3.5 = 6.5;
    # ratio with denominator 0 and yoy with prior 0 -> None
    # tolerance: each value is a short exact decimal; rel=1e-12 is float idiom
    assert recompute("yoy", {"current": 110.0, "prior": 100.0}) == pytest.approx(0.1, rel=1e-12)
    # tolerance: as above
    assert recompute("yoy", {"current": -50.0, "prior": -100.0}) == pytest.approx(0.5, rel=1e-12)
    # tolerance: as above
    assert recompute("ratio", {"numerator": 3.0, "denominator": 4.0}) == pytest.approx(
        0.75, rel=1e-12)
    # tolerance: as above
    assert recompute("difference", {"a": 5.0, "b": 8.0}) == pytest.approx(-3.0, rel=1e-12)
    # tolerance: as above
    assert recompute("sum", {"x": 1.0, "y": 2.0, "z": 3.5}) == pytest.approx(6.5, rel=1e-12)
    assert recompute("ratio", {"numerator": 3.0, "denominator": 0.0}) is None
    assert recompute("yoy", {"current": 3.0, "prior": 0.0}) is None


@pytest.mark.criterion("C5")
def test_breakdown_resolves_every_row(kit):
    # oracle: requirement 7 — each row is a dimensional amount with member=row["member"]:
    # Alpha 10e6 -> 81, Beta 20e6 -> 82, Gamma 30e6 -> 83; one facts_for call
    store = _store(kit)
    rows = [{"member": "Alpha", "expected": "10", "scale": "millions"},
            {"member": "Beta", "expected": "20", "scale": "millions"},
            {"member": "Gamma", "expected": "30", "scale": "millions"}]
    got = resolve_case(kit.breakdown("b1", rows=rows), store)
    assert got["status"] == "verified"
    assert got["classes"] == [[81], [82], [83]]
    assert _inputs(got) == [("Alpha", "verified", [[81]]), ("Beta", "verified", [[82]]),
                            ("Gamma", "verified", [[83]])]
    assert store.calls == [("MSFT", 2025)]


@pytest.mark.criterion("C5")
def test_breakdown_passes_up_the_first_bad_row(kit):
    # oracle: requirement 7 — Beta "99" has no fact (not_found); Delta "40" matches two
    # concepts (84, 85: ambiguous). Rows Alpha, Beta, Delta -> not_found; Alpha, Delta,
    # Beta -> ambiguous
    store = _store(kit)
    alpha = {"member": "Alpha", "expected": "10", "scale": "millions"}
    beta = {"member": "Beta", "expected": "99", "scale": "millions"}
    delta = {"member": "Delta", "expected": "40", "scale": "millions"}
    got = resolve_case(kit.breakdown("b2", rows=[alpha, beta, delta]), store)
    assert got["status"] == "not_found"
    assert [i["status"] for i in got["inputs"]] == ["verified", "not_found", "ambiguous"]
    assert store.calls == [("MSFT", 2025)]
    got = resolve_case(kit.breakdown("b3", rows=[alpha, delta, beta]), _store(kit))
    assert got["status"] == "ambiguous"
    assert [i["status"] for i in got["inputs"]] == ["verified", "ambiguous", "not_found"]


_POS = st.floats(min_value=1e-6, max_value=1e12, allow_nan=False, allow_infinity=False)


@pytest.mark.criterion("C5")
@given(num=_POS, den=_POS)
def test_ratio_times_denominator_is_the_numerator(num, den):
    # oracle: requirement 7 — ratio = numerator / denominator, so ratio x denominator =
    # numerator (closed form)
    r = recompute("ratio", {"numerator": num, "denominator": den})
    assert r is not None
    # tolerance: one division and one multiplication, each rounding by <= 1 ulp
    assert r * den == pytest.approx(num, rel=1e-12)
