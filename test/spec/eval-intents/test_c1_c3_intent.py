"""Spec tests C1-C3 for eval-intents: Intent normalisation, validation and id
(docs/specs/eval-intents.md)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent, intent_id, intent_shape

TICKERS = st.sampled_from(["aapl", "AAPL", " msft ", "Nvda", "googl"])
YEARS = st.integers(min_value=1990, max_value=2040)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_c1_normalisation_point():
    # oracle: the spec's normalisation rules applied by hand.
    i = Intent("trend", tickers=[" aapl "], fiscal_years=[2024, 2022, 2023, 2022],
               metrics=["revenue"])
    assert i.kind == "trend"
    assert i.tickers == ("AAPL",)
    assert i.fiscal_years == (2022, 2023, 2024)
    assert i.metrics == ("revenue",)
    assert (i.dimension, i.operation, i.universe, i.n, i.filing_year) == (None,) * 5
    r = Intent("compute", operation="ratio", tickers=["nvda"], fiscal_years=[2024],
               metrics=["gross_profit", "revenue"])
    assert r.metrics == ("gross_profit", "revenue")  # order kept: numerator first
    assert r.tickers == ("NVDA",)


@pytest.mark.criterion("C1")
def test_c1_shape_point(kit):
    # oracle: shape = kind, or kind.operation for compute and screen (spec, Kinds and shapes).
    for shape, kw in kit.examples.items():
        assert intent_shape(Intent(**kw)) == shape


@pytest.mark.criterion("C1")
def test_c1_equality_point():
    # oracle: equal normalised fields mean equal intents, whatever the keyword order or case.
    a = Intent("screen", operation="top", universe="big_tech", n=3, fiscal_years=[2024],
               metrics=["revenue"])
    b = Intent("screen", metrics=["revenue"], fiscal_years=(2024,), n=3, universe="big_tech",
               operation="top")
    assert a == b and hash(a) == hash(b)
    c = Intent("lookup", tickers=["aapl"], fiscal_years=[2024], metrics=["revenue"])
    d = Intent("lookup", metrics=("revenue",), fiscal_years=(2024,), tickers=("AAPL",))
    e = Intent("lookup", tickers=["MSFT"], fiscal_years=[2024], metrics=["revenue"])
    assert c == d and hash(c) == hash(d)
    assert c != e


@pytest.mark.criterion("C1")
@given(ticker=TICKERS,
       years=st.lists(YEARS, min_size=3, max_size=8).filter(lambda ys: len(set(ys)) >= 3),
       data=st.data())
def test_c1_equality_property(ticker, years, data):
    # oracle: normalisation sorts and deduplicates years and upper-cases tickers, so a permuted,
    # re-cased construction is the same intent.
    perm = data.draw(st.permutations(years))
    a = Intent("trend", tickers=[ticker], fiscal_years=years, metrics=["revenue"])
    b = Intent("trend", metrics=["revenue"], fiscal_years=list(perm),
               tickers=[ticker.strip().lower()])
    assert a.fiscal_years == tuple(sorted(set(years)))
    assert a.tickers == (ticker.strip().upper(),)
    assert a == b and hash(a) == hash(b)


# --------------------------------------------------------------------------- C2
def _bad(kit):
    """(base example, field overrides) pairs that break exactly one rule of the table."""
    del kit
    return [
        ("lookup", {"kind": "lookups"}),                         # unknown kind
        ("lookup", {"tickers": []}),
        ("lookup", {"tickers": ["AAPL", "MSFT"]}),
        ("lookup", {"tickers": [""]}),
        ("lookup", {"fiscal_years": []}),
        ("lookup", {"fiscal_years": [2023, 2024]}),
        ("lookup", {"fiscal_years": ["2024"]}),                  # not an int
        ("lookup", {"fiscal_years": [True]}),                    # bool is not a year
        ("lookup", {"metrics": []}),
        ("lookup", {"metrics": ["revenue", "gross_profit"]}),
        ("lookup", {"metrics": [""]}),
        ("lookup", {"dimension": "segment"}),
        ("lookup", {"operation": "top"}),
        ("lookup", {"universe": "covered"}),
        ("lookup", {"n": 3}),
        ("lookup", {"filing_year": 2025}),
        ("dimensional", {"dimension": None}),
        ("dimensional", {"dimension": ""}),
        ("breakdown", {"dimension": None}),
        ("breakdown", {"universe": "covered"}),
        ("compute.yoy_growth", {"operation": None}),
        ("compute.yoy_growth", {"operation": "top"}),            # a screen op on compute
        ("compute.yoy_growth", {"fiscal_years": [2022, 2024]}),  # not consecutive
        ("compute.yoy_growth", {"fiscal_years": [2022, 2023, 2024]}),
        ("compute.yoy_growth", {"tickers": ["AAPL", "MSFT"]}),
        ("compute.difference", {"fiscal_years": [2024]}),
        ("compute.difference", {"metrics": ["revenue", "gross_profit"]}),
        ("compute.cagr", {"fiscal_years": [2023, 2024]}),        # endpoints less than 2 apart
        ("compute.cagr", {"fiscal_years": [2021, 2022, 2024]}),
        ("compute.ratio", {"metrics": ["revenue"]}),
        ("compute.ratio", {"metrics": ["revenue", "revenue"]}),  # repeated metric
        ("compute.ratio", {"fiscal_years": [2023, 2024]}),
        ("trend", {"fiscal_years": [2023, 2024]}),
        ("trend", {"fiscal_years": [2023, 2024, 2024]}),         # dedupes to two years
        ("trend", {"operation": "yoy_growth"}),
        ("point_in_time", {"filing_year": None}),
        ("point_in_time", {"filing_year": 2022}),                # not after the fiscal year
        ("point_in_time", {"filing_year": 2021}),
        ("screen.top", {"tickers": ["AAPL"]}),
        ("screen.top", {"universe": None}),
        ("screen.top", {"n": None}),
        ("screen.top", {"n": 0}),
        ("screen.top", {"operation": "yoy_growth"}),             # a compute op on screen
        ("screen.top", {"operation": None}),
        ("screen.bottom", {"n": -1}),
        ("screen.sum", {"n": 3}),
        ("screen.median", {"n": 1}),
        ("screen.median", {"fiscal_years": [2023, 2024]}),
        ("peer_relative", {"universe": None}),
        ("peer_relative", {"tickers": []}),
        ("peer_relative", {"tickers": ["AAPL", "aapl"]}),        # repeated after normalisation
        ("peer_relative", {"n": 3}),
    ]


@pytest.mark.criterion("C2")
def test_c2_invalid_intents_raise(kit):
    # oracle: the spec's per-kind validation table and per-operation compute rules; each case
    # breaks exactly one rule of a valid base example.
    bad = _bad(kit)
    assert len(bad) >= 50
    for base, override in bad:
        kw = {**copy.deepcopy(kit.examples[base]), **override}
        with pytest.raises(ValueError):
            Intent(**kw)


@pytest.mark.criterion("C2")
def test_c2_valid_examples_construct(kit):
    # oracle: one valid intent per shape, written from the table; extra valid edge cases.
    for kw in kit.examples.values():
        i = Intent(**kw)
        assert i.kind == kw["kind"]
    i = Intent("compute", operation="cagr", tickers=["AAPL"], fiscal_years=[2020, 2022],
               metrics=["revenue"])
    assert i.fiscal_years == (2020, 2022)
    j = Intent("point_in_time", tickers=["AAPL"], fiscal_years=[2022], metrics=["revenue"],
               filing_year=2023)
    assert j.filing_year == 2023
    k = Intent("trend", tickers=["AAPL"], fiscal_years=[2018, 2020, 2024], metrics=["revenue"])
    assert k.fiscal_years == (2018, 2020, 2024)  # trend years need not be consecutive


@pytest.mark.criterion("C2")
@given(fy=YEARS, filing=YEARS)
def test_c2_point_in_time_filing_year_property(fy, filing):
    # oracle: the table — point_in_time needs filing_year > the fiscal year.
    kw = dict(kind="point_in_time", tickers=["AAPL"], fiscal_years=[fy], metrics=["revenue"],
              filing_year=filing)
    if filing > fy:
        assert Intent(**kw).filing_year == filing
    else:
        with pytest.raises(ValueError):
            Intent(**kw)


@pytest.mark.criterion("C2")
@given(shape=st.sampled_from(["lookup", "dimensional", "breakdown", "compute.ratio", "trend",
                              "point_in_time", "peer_relative", "screen.sum"]),
       field=st.sampled_from(["dimension", "operation", "universe", "n", "filing_year"]))
def test_c2_forbidden_field_property(kit, shape, field):
    # oracle: the table — a field marked "—" for the kind must be empty; setting any one raises.
    allowed = {
        "lookup": set(), "dimensional": {"dimension"}, "breakdown": {"dimension"},
        "compute.ratio": {"operation"}, "trend": set(), "point_in_time": {"filing_year"},
        "peer_relative": {"universe"}, "screen.sum": {"operation", "universe"},
    }[shape]
    kw = copy.deepcopy(kit.examples[shape])
    if field in allowed:
        assert Intent(**kw).kind == kw["kind"]
        return
    kw[field] = 2030 if field in ("n", "filing_year") else "x"
    with pytest.raises(ValueError):
        Intent(**kw)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_c3_intent_id_point():
    # oracle: the C3 rule computed here from a hand-written normalised dict.
    d = {"kind": "lookup", "tickers": ["AAPL"], "fiscal_years": [2024], "metrics": ["revenue"],
         "dimension": None, "operation": None, "universe": None, "n": None, "filing_year": None}
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
    want = "int-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
    assert blob.startswith('{"dimension":null,"filing_year":null,"fiscal_years":[2024],'
                           '"kind":"lookup"')
    got = intent_id(Intent("lookup", tickers=[" aapl"], fiscal_years=[2024], metrics=["revenue"]))
    assert got == want
    assert len(got) == 16


@pytest.mark.criterion("C3")
def test_c3_every_example_point(kit):
    # oracle: the C3 rule (kit.oracle_id) over one hand-written intent per shape; ids distinct.
    ids = {}
    for shape, kw in kit.examples.items():
        ids[shape] = intent_id(Intent(**kw))
        assert ids[shape] == kit.oracle_id(kw)
    assert len(set(ids.values())) == len(ids)


@pytest.mark.criterion("C3")
@given(ticker=TICKERS,
       years=st.lists(YEARS, min_size=3, max_size=6).filter(lambda ys: len(set(ys)) >= 3),
       metric=st.sampled_from(["revenue", "gross_profit", "rd_expense"]), data=st.data())
def test_c3_intent_id_property(kit, ticker, years, metric, data):
    # oracle: the C3 rule; the id is invariant under construction order and changes with a field.
    kw = dict(kind="trend", tickers=[ticker], fiscal_years=years, metrics=[metric])
    perm = list(data.draw(st.permutations(years)))
    a = intent_id(Intent(**kw))
    b = intent_id(Intent("trend", metrics=[metric], fiscal_years=perm, tickers=[ticker.upper()]))
    assert a == b == kit.oracle_id(kw)
    other = "operating_income"
    c = intent_id(Intent("trend", tickers=[ticker], fiscal_years=years, metrics=[other]))
    assert c != a
