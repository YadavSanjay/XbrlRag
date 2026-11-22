"""C4: ``check_case`` for lookup and compute cases.

Oracle: requirement 7 of docs/specs/sec-companyfacts-check.md. The suite case dicts copy the
shape of test/eval/suites/sql_derived_v1.json (ids, ``kind``, ``ticker``, ``fy``,
``expected_value``, ``expected_fact_ids``) with synthetic values; the companyfacts document
and our fact records are built by hand, so every expected status and value is read off them.
"""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_check.core import check_case

ACC24 = "0000320193-24-000123"
ACC23 = "0000320193-23-000106"
AAPL = "0000320193"


def _doc(kit):
    e, (s24, e24), (s23, e23), (s22, e22) = kit.entry, kit.FY24, kit.FY23, kit.FY22
    return kit.companyfacts(AAPL, "Synthetic Apple Inc.", {"us-gaap": {
        "Revenues": {"USD": [
            e(391035e6, ACC24, e24, s24),
            e(383285e6, ACC24, e23, s23),  # the FY2024 10-K's comparative
            e(383285e6, ACC23, e23, s23, fy=2023, filed="2023-11-03"),
            e(394328e6, ACC23, e22, s22, fy=2023, filed="2023-11-03"),
        ]},
        "CostOfGoodsAndServicesSold": {"USD": [e(210352e6, ACC24, e24, s24)]},
        "GrossProfit": {"USD": [e(180683e6, ACC24, e24, s24)]},
        "SellingGeneralAndAdministrativeExpense": {"USD": [
            e(24932e6, ACC23, e23, s23, fy=2023, filed="2023-11-03")]},
        "OperatingIncomeLoss": {"USD": [e(123000e6, ACC24, e24, s24)]},
    }})


def _records(kit):
    s24, e24 = kit.FY24
    s23, e23 = kit.FY23
    us = kit.US_GAAP

    def f(i, local, value, accn, start, end):
        return kit.fact(i, concept=f"{us}:{local}", value=value, accession_no=accn,
                        period_start=start, period_end=end)

    return [
        f(101, "Revenues", 391035e6, ACC24, s24, e24),
        f(102, "Revenues", 383285e6, ACC23, s23, e23),
        f(103, "CostOfGoodsAndServicesSold", 210352e6, ACC24, s24, e24),
        f(104, "GrossProfit", 180683e6, ACC24, s24, e24),
        f(105, "SellingGeneralAndAdministrativeExpense", 26097e6, ACC24, s24, e24),
        f(106, "OperatingIncomeLoss", 123216e6, ACC24, s24, e24),  # SEC says 123000e6
        f(107, "OperatingIncomeLoss", 114301e6, ACC23, s23, e23),  # SEC has no FY2023
    ]


def _store(kit, components=None):
    return kit.FakeStore(_records(kit), components)


def _source(kit):
    return kit.FakeSource({AAPL: _doc(kit)})


def _lookup(fact_id, expected, slug="aq-1a-revenue-lookup", concept="Revenue"):
    return {
        "id": f"{slug}:fy=2024-ticker=AAPL",
        "question": "What was Apple's revenue in fiscal 2024?",
        "kind": "lookup",
        "suite": "sql_derived_v1",
        "expected_value": expected,
        "ticker": "AAPL",
        "fy": 2024,
        "concept": concept,
        "unit": "USD",
        "expected_fact_ids": [fact_id],
        "fact_id_classes": [[fact_id]],
        "origin": "analyst_queries.sql#1a",
        "rel_tol": 0.0001,
    }


def _compute(case_id, expected, fact_ids, concept="Revenue"):
    return {
        "id": case_id,
        "question": "By what percentage did Apple's revenue change in fiscal 2024?",
        "kind": "compute",
        "suite": "sql_derived_v1",
        "expected_value": expected,
        "ticker": "AAPL",
        "fy": 2024,
        "concept": concept,
        "expected_fact_ids": list(fact_ids),
        "fact_id_classes": [[i] for i in fact_ids],
        "origin": "analyst_queries.sql#2c",
        "rel_tol": 0.01,
    }


def _assert_one_item(got, label, expected, sec_value, agree):
    assert len(got["items"]) == 1
    item = got["items"][0]
    assert item["label"] == label
    # tolerance: expected values are hand-computed closed forms; rel=1e-12 is float idiom
    assert item["expected"] == pytest.approx(expected, rel=1e-12)
    if sec_value is None:
        assert item["sec_value"] is None
    else:
        assert item["sec_value"] == pytest.approx(sec_value, rel=1e-12)
    assert item["agree"] is agree


def _fact_statuses(got):
    return {fc["fact_id"]: fc["status"] for fc in got["facts"]}


@pytest.mark.criterion("C4")
def test_lookup_whose_fact_matches_is_a_match(kit):
    # oracle: requirement 7 — fact 101 matches the ACC24 entry 391035e6 and the suite
    # expects 391035e6, so the one item agrees and the case is a match
    source = _source(kit)
    got = check_case(_lookup(101, 391035e6), _store(kit), source)
    assert got["case_id"] == "aq-1a-revenue-lookup:fy=2024-ticker=AAPL"
    assert got["kind"] == "lookup"
    assert got["status"] == "match"
    _assert_one_item(got, "AAPL", 391035e6, 391035e6, True)
    assert [fc["fact_id"] for fc in got["facts"]] == [101]
    assert got["facts"][0]["status"] == "match"
    assert source.calls == [AAPL]


@pytest.mark.criterion("C4")
def test_lookup_expected_differs_from_sec_is_a_mismatch(kit):
    # oracle: requirement 7 — the fact matches our DB value (status match) but the suite's
    # expected 391000e6 does not agree with the SEC's 391035e6 (|diff| = 35e6 >> 1e-9 rel)
    got = check_case(_lookup(101, 391000e6), _store(kit), _source(kit))
    assert _fact_statuses(got) == {101: "match"}
    _assert_one_item(got, "AAPL", 391000e6, 391035e6, False)
    assert got["status"] == "mismatch"


@pytest.mark.criterion("C4")
def test_lookup_whose_fact_is_value_mismatch_is_a_mismatch(kit):
    # oracle: requirement 7 status 1 — fact 106 is value_mismatch (ours 123216e6, SEC
    # 123000e6); the item carries the SEC value and does not agree
    got = check_case(_lookup(106, 123216e6, "aq-5a-operating-income-lookup", "OperatingIncome"),
                     _store(kit), _source(kit))
    assert _fact_statuses(got) == {106: "value_mismatch"}
    _assert_one_item(got, "AAPL", 123216e6, 123000e6, False)
    assert got["status"] == "mismatch"


@pytest.mark.criterion("C4")
def test_yoy_compute_picks_current_and_prior_by_period_end(kit):
    # oracle: requirement 7 — no metric parameter and 2 facts means yoy; current is the
    # later period_end (101, FY2024) even though it is listed second; hand calculation:
    # (391035e6 - 383285e6) / |383285e6| = 7750 / 383285
    expected = (391035e6 - 383285e6) / abs(383285e6)
    source = _source(kit)
    case = _compute("aq-2c-yoy-growth:concept=Revenue-fy=2024-ticker=AAPL", expected, [102, 101])
    got = check_case(case, _store(kit), source)
    _assert_one_item(got, "AAPL", expected, expected, True)
    assert got["status"] == "match"
    assert _fact_statuses(got) == {101: "match", 102: "match"}
    assert len(got["facts"]) == 2
    assert source.calls == [AAPL]  # two facts, one CIK: one call


@pytest.mark.criterion("C4")
def test_metric_compute_uses_components_and_first_list_element(kit):
    # oracle: requirement 7 + requirement 6 — the metric parameter selects GrossMarginPct,
    # roles come from metric_components, and cost_of_revenue [103, 104] uses 103 (COGS), not
    # 104 (gross profit); hand calculation: (391035e6 - 210352e6) / 391035e6
    expected = (391035e6 - 210352e6) / 391035e6
    store = _store(kit, {("AAPL", 2024, "GrossMarginPct"):
                         {"revenue": 101, "cost_of_revenue": [103, 104]}})
    source = _source(kit)
    case = _compute("aq-7a-derived-metric-lookup:fy=2024-metric=GrossMarginPct-ticker=AAPL",
                    expected, [101, 103, 104], concept="GrossMarginPct")
    got = check_case(case, store, source)
    assert store.component_calls == [("AAPL", 2024, "GrossMarginPct")]
    _assert_one_item(got, "AAPL", expected, expected, True)
    assert got["status"] == "match"
    statuses = _fact_statuses(got)
    assert statuses[101] == "match"
    assert statuses[103] == "match"
    assert Counter(source.calls) == Counter({AAPL: 1})


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("fact_ids", [[101], [101, 103, 104]])
def test_unknown_compute_template(kit, fact_ids):
    # oracle: requirement 7 — no metric parameter and not exactly 2 expected_fact_ids:
    # template_unknown
    case = _compute("aq-9z-custom-ratio:fy=2024-ticker=AAPL", 0.5, fact_ids)
    got = check_case(case, _store(kit), _source(kit))
    assert got["status"] == "template_unknown"
    assert got["case_id"] == "aq-9z-custom-ratio:fy=2024-ticker=AAPL"


@pytest.mark.criterion("C4")
def test_concept_absent_fact_is_unverifiable(kit):
    # oracle: requirement 7 status 2 — fact 105's concept has no FY2024 entry
    # (concept_absent), so the item has no SEC value and the reason names the fact status
    got = check_case(_lookup(105, 26097e6, "aq-6a-sga-lookup", "SGA"), _store(kit), _source(kit))
    assert _fact_statuses(got) == {105: "concept_absent"}
    _assert_one_item(got, "AAPL", 26097e6, None, False)
    assert got["status"] == "unverifiable"
    assert got["reason"] == "concept_absent"


@pytest.mark.criterion("C4")
def test_mismatch_outranks_unverifiable(kit):
    # oracle: requirement 7 status order — 106 is value_mismatch and 107 concept_absent:
    # rule 1 (any value_mismatch) applies before rule 2
    case = _compute("aq-2c-yoy-growth:concept=OperatingIncome-fy=2024-ticker=AAPL",
                    (123216e6 - 114301e6) / 114301e6, [106, 107], concept="OperatingIncome")
    got = check_case(case, _store(kit), _source(kit))
    assert _fact_statuses(got) == {106: "value_mismatch", 107: "concept_absent"}
    assert got["status"] == "mismatch"


# ------------------------------------------------------------------ property: all-match → match

# role -> (us-gaap local name, instant?)
_ROLE_CONCEPT = {
    "revenue": ("Revenues", False),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", False),
    "gross_profit": ("GrossProfit", False),
    "operating_income": ("OperatingIncomeLoss", False),
    "net_income": ("NetIncomeLoss", False),
    "rnd": ("ResearchAndDevelopmentExpense", False),
    "tax": ("IncomeTaxExpenseBenefit", False),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxes", False),
    "effective_tax_rate": ("EffectiveIncomeTaxRateContinuingOperations", False),
    "current_assets": ("AssetsCurrent", True),
    "current_liabilities": ("LiabilitiesCurrent", True),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", False),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", False),
    "dep_amort": ("DepreciationDepletionAndAmortization", False),
}

# metric -> (roles, closed form from requirement 6)
_METRICS = {
    "GrossMarginPct": (("revenue", "cost_of_revenue"),
                       lambda v: (v["revenue"] - v["cost_of_revenue"]) / v["revenue"]),
    "OperatingMarginPct": (("revenue", "operating_income"),
                           lambda v: v["operating_income"] / v["revenue"]),
    "NetMarginPct": (("revenue", "net_income"), lambda v: v["net_income"] / v["revenue"]),
    "RnDIntensityPct": (("revenue", "rnd"), lambda v: v["rnd"] / v["revenue"]),
    "EffectiveTaxRate": (("pretax_income", "tax"), lambda v: v["tax"] / v["pretax_income"]),
    "CurrentRatio": (("current_assets", "current_liabilities"),
                     lambda v: v["current_assets"] / v["current_liabilities"]),
    "FreeCashFlow": (("operating_cash_flow", "capex"),
                     lambda v: v["operating_cash_flow"] - v["capex"]),
    "EBITDA": (("operating_income", "dep_amort"),
               lambda v: v["operating_income"] + v["dep_amort"]),
}

_VAL = st.floats(min_value=1e3, max_value=1e12, allow_nan=False, allow_infinity=False)


def _matching_world(kit, items):
    """Records + a document where each ``(fact_id, local, value, instant, period)`` matches."""
    concepts: dict[str, dict] = {}
    records = []
    for fact_id, local, value, instant, (start, end), accn in items:
        e = kit.entry(value, accn, end, None if instant else start)
        unit = "pure" if local == "EffectiveIncomeTaxRateContinuingOperations" else "USD"
        concepts.setdefault(local, {}).setdefault(unit, []).append(e)
        records.append(kit.fact(fact_id, concept=f"{kit.US_GAAP}:{local}", value=value,
                                accession_no=accn, period_end=end,
                                period_start=None if instant else start, unit=unit))
    return records, kit.companyfacts(AAPL, "Synthetic Apple Inc.", {"us-gaap": concepts})


@pytest.mark.criterion("C4")
@given(data=st.data(), kind=st.sampled_from(["lookup", "yoy", "metric", "metric_list",
                                             "metric_etr"]))
def test_all_facts_matching_and_expected_recomputed_gives_match(kit, data, kind):
    # oracle: requirement 7 — every fact matches (each record has a same-accession,
    # same-value entry) and each expected value is the requirement-6 closed form over those
    # values, so no rule 1/2 condition holds and the status is match
    if kind == "lookup":
        v = data.draw(_VAL, label="value")
        records, doc = _matching_world(kit, [(201, "Revenues", v, False, kit.FY24, ACC24)])
        case, store = _lookup(201, v), kit.FakeStore(records)
    elif kind == "yoy":
        cur, pri = data.draw(_VAL, label="current"), data.draw(_VAL, label="prior")
        order = data.draw(st.sampled_from([[201, 202], [202, 201]]), label="order")
        records, doc = _matching_world(kit, [(201, "Revenues", cur, False, kit.FY24, ACC24),
                                             (202, "Revenues", pri, False, kit.FY23, ACC23)])
        case = _compute("aq-2c-yoy-growth:concept=Revenue-fy=2024-ticker=AAPL",
                        (cur - pri) / abs(pri), order)
        store = kit.FakeStore(records)
    else:
        if kind == "metric_list":
            metric = "GrossMarginPct"
        elif kind == "metric_etr":
            metric = "EffectiveTaxRate"
        else:
            metric = data.draw(st.sampled_from(sorted(_METRICS)), label="metric")
        roles, form = _METRICS[metric]
        values = {r: data.draw(_VAL, label=r) for r in roles}
        expected = form(values)
        items, components = [], {}
        for n, role in enumerate(roles):
            local, instant = _ROLE_CONCEPT[role]
            items.append((300 + n, local, values[role], instant, kit.FY24, ACC24))
            components[role] = 300 + n
        if kind == "metric_list":
            gp = values["revenue"] - values["cost_of_revenue"]
            items.append((399, "GrossProfit", gp, False, kit.FY24, ACC24))
            components["cost_of_revenue"] = [components["cost_of_revenue"], 399]
        if kind == "metric_etr":
            rate = data.draw(st.floats(min_value=0.01, max_value=0.5), label="rate")
            items = [(398, "EffectiveIncomeTaxRateContinuingOperations", rate, False, kit.FY24,
                      ACC24)]
            components = {"effective_tax_rate": 398}
            expected = rate
        records, doc = _matching_world(kit, items)
        store = kit.FakeStore(records, {("AAPL", 2024, metric): components})
        ids = [i[0] for i in items]
        case = _compute(f"aq-7a-derived-metric-lookup:fy=2024-metric={metric}-ticker=AAPL",
                        expected, ids, concept=metric)
    source = kit.FakeSource({AAPL: doc})
    got = check_case(case, store, source)
    assert got["status"] == "match"
    assert all(fc["status"] == "match" for fc in got["facts"])
    assert len(got["facts"]) >= 1
    assert [it["agree"] for it in got["items"]] == [True]
    assert source.calls == [AAPL]
