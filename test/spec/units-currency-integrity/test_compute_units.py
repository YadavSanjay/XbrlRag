"""Spec tests for units-currency-integrity, compute side: C1 (`expr_unit`), C2 (the
executor applies units) and C3 (a non-OK value never enters a compute).

docs/specs/units-currency-integrity.md. The fact store is faked at its boundary (see
conftest.py); the executor, the calculator and the lookup's decision logic run for real.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import StepStatus
from src.retriever.xbrl.xbrl_calculator_tool import CalculatorError, expr_unit
from src.retriever.xbrl.xbrl_factstore_lookup import LookupStatus

OK, AMB, NF, ERR = (LookupStatus.OK, LookupStatus.AMBIGUOUS, LookupStatus.NOT_FOUND,
                    LookupStatus.ERROR)
_CURRENCY = re.compile(r"^[A-Z]{3}$")


# --------------------------------------------------------------------------- oracle
DIMENSIONLESS = {"pure", "Rate"}  # Requirement 4's closed list, written out


def _parse(unit):
    """Requirement 4's reading of a stored unit as base -> exponent (None: unknown)."""
    if unit is None:
        return None
    m: Counter = Counter()
    if unit in DIMENSIONLESS:
        return m

    def keep(f):
        return f not in DIMENSIONLESS and f != "1"

    if "/" in unit:
        num, den = unit.split("/", 1)
        for f in num.split("*"):
            if keep(f):
                m[f] += 1
        for f in den.split("*"):
            if keep(f):
                m[f] -= 1
        return m
    factors = unit.split("*")
    others = [f for f in factors if f != "shares"]
    if len(factors) == 2 and len(others) == 1:
        other = others[0]
        if _CURRENCY.match(other):
            m[other] += 1
            m["shares"] -= 1
            return m
    for f in factors:
        if keep(f):
            m[f] += 1
    return m


def _canon(m) -> str:
    """Requirement 4's canonical form of an exponent map."""
    pos = [f for f in sorted(m) for _ in range(m[f]) if m[f] > 0]
    neg = [f for f in sorted(m) for _ in range(-m[f]) if m[f] < 0]
    if not pos and not neg:
        return "pure"
    head = "*".join(pos) if pos else "1"
    return head + ("/" + "*".join(neg) if neg else "")


class _RefusedError(Exception):
    pass


def _oracle_unit(tree, units):
    """The exponent algebra written out over an expression tree; raises _RefusedError."""
    names = _names(tree)
    parsed = {}
    for n in names:
        if n not in units or units[n] is None:
            raise _RefusedError(n)
        parsed[n] = _parse(units[n])
    currencies = {k for n in names for k in parsed[n] if _CURRENCY.match(k)}
    if len(currencies) > 1:
        raise _RefusedError("currencies")

    def walk(t):
        kind = t[0]
        if kind == "num":
            return Counter()
        if kind == "name":
            return Counter(parsed[t[1]])
        if kind == "neg":
            return walk(t[1])
        left, right = walk(t[1]), walk(t[2])
        if kind in ("+", "-"):
            if {k: v for k, v in left.items() if v} != {k: v for k, v in right.items() if v}:
                raise _RefusedError("add")
            return left
        out = Counter(left)
        for k, v in right.items():
            out[k] += v if kind == "*" else -v
        return Counter({k: v for k, v in out.items() if v})

    return _canon(walk(tree))


def _names(tree) -> set:
    if tree[0] == "name":
        return {tree[1]}
    if tree[0] == "num":
        return set()
    return set().union(*(_names(t) for t in tree[1:]))


def _render(tree) -> str:
    kind = tree[0]
    if kind == "num":
        return str(tree[1])
    if kind == "name":
        return tree[1]
    if kind == "neg":
        return f"-({_render(tree[1])})"
    return f"({_render(tree[1])} {kind} {_render(tree[2])})"


IDS = ["a", "b", "c", "d"]
leaves = st.one_of(st.tuples(st.just("name"), st.sampled_from(IDS)),
                   st.tuples(st.just("num"), st.sampled_from([2, 100, 365])))
trees = st.recursive(
    leaves,
    lambda kids: st.one_of(
        st.tuples(st.sampled_from(["+", "-", "*", "/"]), kids, kids),
        st.tuples(st.just("neg"), kids)),
    max_leaves=6)
UNIT_POOL = ["USD", "TWD", "EUR", "shares", "pure", "Rate", "Integer", "shares*USD",
             "shares*TWD", "USD/shares", None]
unit_maps = st.fixed_dictionaries({i: st.sampled_from(UNIT_POOL) for i in IDS})


def _relabel(unit, mapping):
    if unit is None:
        return None
    return re.sub(r"[A-Z]{3}", lambda m: mapping.get(m.group(0), m.group(0)), unit)


# =========================================================================== C1
@pytest.mark.criterion("C1")
def test_expr_unit_point_cases_from_stored_units():
    # oracle: Requirement 4 by hand (dimensional analysis) on the stored units in Notes
    assert expr_unit("rev - cost", {"rev": "USD", "cost": "USD"}) == "USD"
    assert expr_unit("ni / rev", {"ni": "USD", "rev": "USD"}) == "pure"
    assert expr_unit("(ocf - capex) / sh",
                     {"ocf": "USD", "capex": "USD", "sh": "shares"}) == "USD/shares"
    # AAPL FY2025 diluted EPS (fact 14430) is stored as "shares*USD": USD per share.
    assert expr_unit("eps * sh", {"eps": "shares*USD", "sh": "shares"}) == "USD"
    assert expr_unit("(a - b) / b * 100", {"a": "TWD", "b": "TWD"}) == "pure"
    assert expr_unit("100 / sh", {"sh": "shares"}) == "1/shares"
    assert expr_unit("x * 2", {"x": "TWD"}) == "TWD"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("expr,units", [
    ("aapl - tsm", {"aapl": "USD", "tsm": "TWD"}),      # two currencies, add
    ("aapl / tsm", {"aapl": "USD", "tsm": "TWD"}),      # two currencies, ratio
    ("ni + sh", {"ni": "USD", "sh": "shares"}),          # money + share count
    ("eps + ni", {"eps": "shares*USD", "ni": "USD"}),   # per-share + money
    ("x + 1", {"x": "USD"}),                             # money + pure literal
    ("a / b", {"a": "USD", "b": None}),                  # unknown unit
    ("a / zz", {"a": "USD"}),                            # no unit entry at all
    ("a ** 2", {"a": "USD"}),                            # not in the grammar
])
def test_expr_unit_refusals(expr, units):
    # oracle: Requirement 4 — each case is a refusal by hand
    with pytest.raises(CalculatorError):
        expr_unit(expr, units)


@pytest.mark.criterion("C1")
@given(trees, unit_maps)
def test_expr_unit_agrees_with_the_exponent_algebra(tree, units):
    # oracle: Requirement 4 written out in _oracle_unit (exponent maps, one currency)
    expr = _render(tree)
    try:
        expected = _oracle_unit(tree, units)
    except _RefusedError:
        with pytest.raises(CalculatorError):
            expr_unit(expr, units)
        return
    assert expr_unit(expr, units) == expected


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(trees, unit_maps, st.sampled_from(["JPY", "GBP", "CNY"]),
       st.sampled_from(["KRW", "CHF", "INR"]))
def test_relabelling_currencies_relabels_the_result(tree, units, usd_to, twd_to):
    # oracle: metamorphic — currency codes are labels; a consistent relabelling of the
    # inputs relabels the result and never changes whether it is refused
    # anchor: a fixed case, so a constant result cannot satisfy the relation
    assert expr_unit("a - b", {"a": "TWD", "b": "TWD"}) == "TWD"
    expr = _render(tree)
    mapping = {"USD": usd_to, "TWD": twd_to}
    relabelled = {k: _relabel(v, mapping) for k, v in units.items()}
    reordered = dict(reversed(list(units.items())))
    try:
        base = expr_unit(expr, units)
    except CalculatorError:
        with pytest.raises(CalculatorError):
            expr_unit(expr, relabelled)
        with pytest.raises(CalculatorError):
            expr_unit(expr, reordered)
        return
    assert isinstance(base, str)
    assert expr_unit(expr, reordered) == base
    got = expr_unit(expr, relabelled)
    # The canonical form sorts factors, so compare as exponent maps.
    assert _parse(got) == _parse(_relabel(base, mapping))


@pytest.mark.criterion("C1")
def test_dimensionless_units_are_unitless():
    # oracle: Requirement 4 by hand — "pure" and "Rate" are both {} (AMD FY2025 effective
    # tax rate, fact 115827, is "Rate"; the statutory rate beside it, fact 115791, "pure")
    assert expr_unit("oi * (1 - etr)", {"oi": "USD", "etr": "Rate"}) == "USD"
    assert expr_unit("etr - stat", {"etr": "Rate", "stat": "pure"}) == "pure"
    assert expr_unit("etr * 100", {"etr": "Rate"}) == "pure"
    assert expr_unit("oi * (1 - etr) / (eq + d1 + d2)",
                     {"oi": "USD", "etr": "Rate", "eq": "USD", "d1": "USD",
                      "d2": "USD"}) == "pure"
    assert expr_unit("x * r", {"x": "TWD", "r": "Rate"}) == "TWD"
    # An unlisted non-currency unit keeps today's behaviour: a base unit of its own.
    assert expr_unit("n * 2", {"n": "Integer"}) == "Integer"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("expr,units", [
    ("oi + etr", {"oi": "USD", "etr": "Rate"}),          # money + rate
    ("n - 1", {"n": "Integer"}),                         # unlisted unit + pure literal
    ("n - etr", {"n": "Integer", "etr": "Rate"}),        # unlisted unit + rate
    ("etr - r", {"etr": "Rate", "r": "rate"}),           # lower-case "rate" is unlisted
])
def test_dimensionless_refusals(expr, units):
    # oracle: Requirement 4 — a rate is unitless, an unlisted unit is a base unit
    with pytest.raises(CalculatorError):
        expr_unit(expr, units)


def _swap_dimensionless(unit):
    return {"pure": "Rate", "Rate": "pure"}.get(unit, unit)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(trees, unit_maps, st.sets(st.sampled_from(IDS)))
def test_pure_and_rate_are_interchangeable(tree, units, which):
    # oracle: metamorphic — Requirement 4 lists "pure" and "Rate" as the same (no)
    # dimension, so swapping one for the other on any inputs changes neither the result
    # unit nor whether it is refused
    # anchor: a fixed case, so a constant result cannot satisfy the relation
    assert expr_unit("oi * (1 - etr)", {"oi": "USD", "etr": "Rate"}) == "USD"
    expr = _render(tree)
    swapped = {k: (_swap_dimensionless(v) if k in which else v) for k, v in units.items()}
    try:
        base = expr_unit(expr, units)
    except CalculatorError:
        with pytest.raises(CalculatorError):
            expr_unit(expr, swapped)
        return
    assert isinstance(base, str)
    assert expr_unit(expr, swapped) == base


# =========================================================================== C2
@pytest.mark.criterion("C2")
def test_aapl_minus_tsmc_revenue_is_refused(fx):
    # oracle: stored facts 13253 (USD 391,035,000,000) and 87103 (TWD 2,894,307,700,000);
    # S6: no currency conversion, so the difference has no unit and no value
    steps = [fx.lookup_step("aapl24", fx.AAPL_CIK, 2024, "Revenue"),
             fx.lookup_step("tsm24", fx.TSM_CIK, 2024, "Revenue"),
             {"id": "gap24", "op": "compute", "expr": "aapl24 - tsm24"}]
    result = fx.execute(fx.RowStore(), steps)
    tsm = result.step("tsm24")
    assert tsm.status is StepStatus.OK and tsm.unit == "TWD"  # reporting currency
    assert tsm.value == pytest.approx(2_894_307_700_000.0, rel=1e-12)  # stored value
    gap = result.step("gap24")
    assert gap.status is StepStatus.ERROR
    assert gap.value is None
    assert "USD" in (gap.error or "") and "TWD" in (gap.error or "")


@pytest.mark.criterion("C2")
def test_aapl_minus_tsmc_usd_convenience_is_computed(fx):
    # oracle: hand arithmetic — 391,035,000,000 - 88,268,000,000 = 302,767,000,000 (facts
    # 13253 and 87104, both USD)
    steps = [fx.lookup_step("aapl24", fx.AAPL_CIK, 2024, "Revenue"),
             fx.lookup_step("tsm24", fx.TSM_CIK, 2024, "Revenue", unit="USD"),
             {"id": "gap24", "op": "compute", "expr": "aapl24 - tsm24"}]
    gap = fx.execute(fx.RowStore(), steps).step("gap24")
    assert gap.status is StepStatus.OK
    assert gap.value == pytest.approx(302_767_000_000.0, rel=1e-12)  # one exact subtraction
    assert gap.unit == "USD"


def _aapl25_steps(fx, *computes):
    look = [fx.lookup_step(sid, fx.AAPL_CIK, 2025, fam) for sid, fam in (
        ("ocf25", "CashFromOperations"), ("capex25", "Capex"), ("rev25", "Revenue"),
        ("ni25", "NetIncome"), ("eps25", "EPSDiluted"),
        ("sh25", "WeightedAverageSharesDiluted"))]
    return look + [{"id": cid, "op": "compute", "expr": e} for cid, e in computes]


@pytest.mark.criterion("C2")
def test_aapl_fy2025_per_share_margin_and_eps_units(fx):
    # oracle: AAPL FY2025 10-K facts (Notes), hand arithmetic shown inline
    steps = _aapl25_steps(fx, ("fcfps", "(ocf25 - capex25) / sh25"),
                          ("eps_x_sh", "eps25 * sh25"), ("margin", "ni25 / rev25"),
                          ("fcf", "ocf25 - capex25"), ("fcf_ps2", "fcf / sh25"),
                          ("fcf_m", "fcf / rev25"))
    result = fx.execute(fx.RowStore(), steps)
    fcfps = result.step("fcfps")
    assert fcfps.status is StepStatus.OK and fcfps.unit == "USD/shares"
    # (111,482,000,000 - 12,715,000,000) / 15,004,697,000; float division only
    assert fcfps.value == pytest.approx(98_767_000_000 / 15_004_697_000, rel=1e-12)
    eps = result.step("eps_x_sh")
    assert eps.status is StepStatus.OK and eps.unit == "USD"
    assert eps.value == pytest.approx(111_935_039_620.0, rel=1e-12)  # 7.46 x 15,004,697,000
    margin = result.step("margin")
    assert margin.status is StepStatus.OK and margin.unit == "pure"
    assert margin.value == pytest.approx(112_010 / 416_161, rel=1e-12)  # one division
    # A compute over an OK compute uses that compute's unit.
    assert result.step("fcf").unit == "USD"
    assert result.step("fcf_ps2").unit == "USD/shares"
    assert result.step("fcf_ps2").value == pytest.approx(fcfps.value, rel=1e-12)  # same op
    assert result.step("fcf_m").unit == "pure"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("expr,units", [
    ("ni25 + sh25", ("USD", "shares")),
    ("eps25 + ni25", ("USD", "shares")),
    ("fcf - sh25", ("USD", "shares")),
])
def test_money_and_shares_do_not_add(fx, expr, units):
    # oracle: Requirement 5 — a sum needs one unit; the error names the units met
    steps = _aapl25_steps(fx, ("fcf", "ocf25 - capex25"), ("bad", expr))
    bad = fx.execute(fx.RowStore(), steps).step("bad")
    assert bad.status is StepStatus.ERROR and bad.value is None
    assert all(u in (bad.error or "") for u in units)


TEMPLATES = [  # (expr, currency exponent of the result unit)
    ("a + b", 1), ("a - b", 1), ("(a - b) / b", 0), ("a / b", 0), ("(a - b) / s", 1),
    ("a * 2 - b", 1), ("(a + b) / (a + b + c)", 0), ("c / s * 100", 1),
]
positive = st.floats(min_value=1e3, max_value=1e12, allow_nan=False)


def _synthetic(fx, expr, values, units):
    store = fx.ResultStore({k: (OK, values[k], units[k], 1000 + i)
                            for i, k in enumerate(("a", "b", "c", "s"))})
    steps = [{"id": k, "op": "lookup", "cik": "x", "fy": 2025, "concept_family": "Revenue",
              "dimensions": "NONE"} for k in ("a", "b", "c", "s")]
    steps.append({"id": "out", "op": "compute", "expr": expr})
    return fx.execute(store, steps).step("out")


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(TEMPLATES), positive, positive, positive,
       st.floats(min_value=1e6, max_value=1e10, allow_nan=False),
       st.floats(min_value=1e-3, max_value=1e3, allow_nan=False),
       st.sampled_from(["USD", "TWD", "EUR", "JPY"]))
def test_scale_invariance(fx, template, a, b, c, s, k, cur):
    # oracle: metamorphic — every currency input x k gives result x k**e, e the currency
    # exponent of the result unit; the unit and status do not change
    expr, e = template
    assume(abs(a - b) > 1e-3 * max(a, b))  # no catastrophic cancellation in a - b
    units = {"a": cur, "b": cur, "c": cur, "s": "shares"}
    base = _synthetic(fx, expr, {"a": a, "b": b, "c": c, "s": s}, units)
    scaled = _synthetic(fx, expr, {"a": a * k, "b": b * k, "c": c * k, "s": s}, units)
    assert base.status is StepStatus.OK and scaled.status is StepStatus.OK
    want = {0: "pure", 1: cur}[e] if "/ s" not in expr else f"{cur}/shares"
    assert base.unit == want and scaled.unit == want
    # tolerance: a few float roundings on each side; rel=1e-9
    assert scaled.value == pytest.approx(base.value * k ** e, rel=1e-9)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(TEMPLATES), positive, positive, positive,
       st.sampled_from([("USD", "TWD"), ("TWD", "USD"), ("EUR", "JPY")]))
def test_two_currencies_refused_and_swapping_labels_changes_nothing(fx, template, a, b, c,
                                                                    pair):
    # oracle: metamorphic — c in a second currency makes any expression that uses c and
    # a or b ERROR; swapping the two labels on every input changes neither status nor value
    expr, _ = template
    x, y = pair
    values = {"a": a, "b": b, "c": c, "s": 1e9}
    mixed = _synthetic(fx, expr, values, {"a": x, "b": x, "c": y, "s": "shares"})
    swapped = _synthetic(fx, expr, values, {"a": y, "b": y, "c": x, "s": "shares"})
    names = set(re.findall(r"[a-z]+", expr))
    if "c" in names and names & {"a", "b"}:
        assert mixed.status is StepStatus.ERROR and mixed.value is None
        assert swapped.status is StepStatus.ERROR and swapped.value is None
    else:
        assert mixed.status is StepStatus.OK and swapped.status is StepStatus.OK
        assert isinstance(mixed.unit, str) and isinstance(swapped.unit, str)
        assert mixed.value == pytest.approx(swapped.value, rel=1e-12)  # same arithmetic
        assert mixed.unit.replace(x, "#").replace(y, "#") == swapped.unit.replace(
            x, "#").replace(y, "#")


def _amd25_steps(fx, *computes):
    look = [fx.lookup_step(sid, fx.AMD_CIK, 2025, fam) for sid, fam in (
        ("oi25", "OperatingIncome"), ("etr25", "EffectiveTaxRate"),
        ("stat25", "StatutoryTaxRate"), ("eq25", "StockholdersEquity"),
        ("ltd25", "LongTermDebtNoncurrent"), ("cur25", "LongTermDebtCurrent"))]
    return look + [{"id": cid, "op": "compute", "expr": e} for cid, e in computes]


@pytest.mark.criterion("C2")
def test_amd_fy2025_roic_over_a_rate_tax_rate(fx):
    # oracle: AMD FY2025 10-K 0000002488-26-000018 (Notes), hand arithmetic:
    # NOPAT = 3,694,000,000 x (1 - (-0.025)) = 3,786,350,000;
    # invested capital = 62,999,000,000 + 2,348,000,000 + 874,000,000 = 66,221,000,000;
    # ROIC = 3,786,350,000 / 66,221,000,000 = 0.0571774814...
    steps = _amd25_steps(fx, ("nopat", "oi25 * (1 - etr25)"),
                         ("roic", "oi25 * (1 - etr25) / (eq25 + ltd25 + cur25)"),
                         ("roic2", "nopat / (eq25 + ltd25 + cur25)"),
                         ("gap", "stat25 - etr25"))
    result = fx.execute(fx.RowStore(), steps)
    etr = result.step("etr25")
    assert etr.status is StepStatus.OK and etr.unit == "Rate"  # as filed
    assert etr.value == pytest.approx(-0.025, abs=1e-15)  # the stored value
    nopat = result.step("nopat")
    assert nopat.status is StepStatus.OK and nopat.unit == "USD"
    # tolerance: 1.025 is not exact in binary; a few roundings; rel=1e-12
    assert nopat.value == pytest.approx(3_786_350_000.0, rel=1e-12)
    roic = result.step("roic")
    assert roic.status is StepStatus.OK and roic.unit == "pure"
    # tolerance: a product, a sum and a division in float; rel=1e-12
    assert roic.value == pytest.approx(3_786_350_000 / 66_221_000_000, rel=1e-12)
    assert roic.value == pytest.approx(0.0571774814, abs=1e-10)  # printed digits
    roic2 = result.step("roic2")
    assert roic2.status is StepStatus.OK and roic2.unit == "pure"
    assert roic2.value == pytest.approx(roic.value, rel=1e-12)  # same arithmetic, chained
    gap = result.step("gap")  # statutory 0.210 ("pure") less effective -0.025 ("Rate")
    assert gap.status is StepStatus.OK and gap.unit == "pure"
    assert gap.value == pytest.approx(0.235, rel=1e-12)  # one float subtraction


@pytest.mark.criterion("C2")
def test_avgo_fy2024_tax_from_a_rate_matches_the_filed_expense(fx):
    # oracle: AVGO FY2024 10-K 0001730168-24-000139 — pretax 9,916,000,000 (fact 24844) x
    # effective rate 0.378 ("Rate", fact 26258) = 3,748,248,000; the filed tax expense
    # 3,748,000,000 (fact 24847) agrees within the rate's rounding (decimals 3)
    steps = [fx.lookup_step(sid, fx.AVGO_CIK, 2024, fam) for sid, fam in (
        ("pre24", "PretaxIncome"), ("tax24", "TaxExpense"), ("etr24", "EffectiveTaxRate"))]
    steps += [{"id": "tax_est", "op": "compute", "expr": "pre24 * etr24"},
              {"id": "drift", "op": "compute", "expr": "tax24 / pre24 - etr24"}]
    result = fx.execute(fx.RowStore(), steps)
    est = result.step("tax_est")
    assert est.status is StepStatus.OK and est.unit == "USD"
    assert est.value == pytest.approx(3_748_248_000.0, rel=1e-12)  # one float product
    # tolerance: the rate is filed to 3 decimals, so +-0.0005 of pretax income
    assert abs(est.value - 3_748_000_000) <= 0.0005 * 9_916_000_000
    drift = result.step("drift")
    assert drift.status is StepStatus.OK and drift.unit == "pure"
    # 3,748 / 9,916 - 0.378 = -0.0000250100...; float division and subtraction
    assert drift.value == pytest.approx(3_748 / 9_916 - 0.378, rel=1e-9)


RATE_TEMPLATES = [  # (expr, result unit given a and b in currency `cur`; s is the rate)
    ("a * (1 - s)", "{cur}"), ("a * (1 - s) / b", "pure"), ("a * s", "{cur}"),
    ("a / b - s", "pure"), ("s * 100", "pure"), ("(a - b) / b * (1 + s)", "pure"),
]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.sampled_from(RATE_TEMPLATES), positive, positive,
       st.floats(min_value=-0.5, max_value=0.5, allow_nan=False),
       st.sampled_from(["USD", "TWD", "EUR"]))
def test_a_rate_input_computes_like_a_pure_one(fx, template, a, b, r, cur):
    # oracle: metamorphic — Requirement 4 lists "pure" and "Rate" as unitless, so the same
    # inputs labelled either way give the same status, unit and value
    expr, want = template
    want = want.format(cur=cur)
    values = {"a": a, "b": b, "c": 1.0, "s": r}
    as_pure = _synthetic(fx, expr, values, {"a": cur, "b": cur, "c": cur, "s": "pure"})
    as_rate = _synthetic(fx, expr, values, {"a": cur, "b": cur, "c": cur, "s": "Rate"})
    assert as_pure.status is StepStatus.OK and as_rate.status is StepStatus.OK
    assert as_pure.unit == want and as_rate.unit == want
    assert as_rate.value == pytest.approx(as_pure.value, rel=1e-12)  # same arithmetic


# =========================================================================== C3
def _ceg_steps(fx, *computes, numerator="ProfitLoss"):
    return [fx.lookup_step("pl22", fx.CEG_CIK, 2022, numerator),
            fx.lookup_step("rev22", fx.CEG_CIK, 2022, "Revenue")] + [
        {"id": cid, "op": "compute", "expr": e} for cid, e in computes]


@pytest.mark.criterion("C3")
def test_ceg_ambiguous_profit_never_enters_the_margin(fx):
    # oracle: stored facts — ProfitLoss -167,000,000 (calendar 2022) vs -318,000,000
    # (2022-02-01..12-31) end on the same day: no single value; Requirement 6
    result = fx.execute(fx.RowStore(), _ceg_steps(fx, ("m22", "pl22 / rev22")))
    pl = result.step("pl22")
    assert pl.status is StepStatus.AMBIGUOUS  # the lookup's own decision
    assert pl.value is not None  # ... and it still reports a candidate value
    m = result.step("m22")
    assert m.status is StepStatus.ERROR and m.value is None
    assert "pl22" in (m.error or "")


@pytest.mark.criterion("C3")
def test_ceg_net_income_control_is_computed(fx):
    # oracle: hand arithmetic — NetIncomeLoss -160,000,000 / revenue 24,440,000,000
    steps = _ceg_steps(fx, ("m22", "pl22 / rev22"), numerator="NetIncome")
    m = fx.execute(fx.RowStore(), steps).step("m22")
    assert m.status is StepStatus.OK and m.unit == "pure"
    assert m.value == pytest.approx(-160 / 24_440, rel=1e-12)  # one division


@pytest.mark.criterion("C3")
def test_ambiguous_base_is_error_not_n_m_and_refusal_propagates(fx):
    # oracle: Requirement 6 — the non-OK check comes before n/m; a compute over a refused
    # compute is refused, naming it
    steps = _ceg_steps(fx, ("chg", "(rev22 - pl22) / pl22"), ("m22", "pl22 / rev22"),
                       ("pct", "m22 * 100"))
    result = fx.execute(fx.RowStore(), steps)
    chg = result.step("chg")
    assert chg.status is StepStatus.ERROR and chg.value is None
    assert "pl22" in (chg.error or "")
    pct = result.step("pct")
    assert pct.status is StepStatus.ERROR and pct.value is None
    assert "m22" in (pct.error or "")


NON_OK = [AMB, NF, ERR]


@pytest.mark.criterion("C3")
@given(st.sampled_from([OK] + NON_OK), st.sampled_from([OK] + NON_OK), st.booleans(),
       positive, positive)
def test_any_non_ok_input_refuses_the_compute(fx, s1, s2, keep_value, v1, v2):
    # oracle: Requirement 6 as a truth table over the two inputs' statuses, whether or
    # not a non-OK lookup reports a value
    def ans(status, v):
        return (status, v if (status is OK or keep_value) else None, "USD", 7)

    store = fx.ResultStore({"inp_alpha": ans(s1, v1), "inp_beta": ans(s2, v2)})
    steps = [{"id": i, "op": "lookup", "cik": "x", "fy": 2024, "concept_family": "Revenue",
              "dimensions": "NONE"} for i in ("inp_alpha", "inp_beta")]
    steps.append({"id": "out", "op": "compute", "expr": "inp_alpha / inp_beta"})
    out = fx.execute(store, steps).step("out")
    if s1 is OK and s2 is OK:
        assert out.status is StepStatus.OK and out.unit == "pure"
        assert out.value == pytest.approx(v1 / v2, rel=1e-12)  # one division
    else:
        assert out.status is StepStatus.ERROR and out.value is None
        for sid, status in (("inp_alpha", s1), ("inp_beta", s2)):
            if status is not OK:
                assert sid in (out.error or "")
