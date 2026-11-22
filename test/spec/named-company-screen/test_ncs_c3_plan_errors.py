"""C3: ``named_company_plan_errors`` (docs/specs/named-company-screen.md, R4).

Pure. A question ranking companies it names ("Of Apple, Microsoft and Alphabet, whose R&D
spend was largest?"; synthetic) resolves AAPL, MSFT, GOOGL and no universe; every screen of
its plan must rank exactly those three. Expected errors are Requirement 4 applied by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.named_company_screen import named_company_plan_errors

NAMED = ["AAPL", "MSFT", "GOOGL"]


def _screen(sid, universe="<absent>"):
    step = {"id": sid, "op": "screen", "fy": 2024, "dimension": "NONE", "metric_kind": "value",
            "concept_family": "ResearchAndDevelopment", "order": "desc", "top_n": 1}
    if universe != "<absent>":
        step["universe"] = universe
    return step


def _assert_names(error, index, sid):
    assert f"step[{index}]" in error
    assert sid in error
    assert "universe" in error
    for t in NAMED:
        assert t in error, (t, error)


@pytest.mark.criterion("C3")
def test_c3_wrong_universes_are_one_error_each():
    # oracle: Requirement 4 by hand — covered, absent, a group, a partial list and a larger
    # list are not exactly {AAPL, MSFT, GOOGL}: one error naming step[0], the id, `universe`
    # and the three tickers
    for universe in ("covered", "<absent>", None, "big_tech", ["AAPL", "MSFT"],
                     ["AAPL", "MSFT", "GOOGL", "AMZN"]):
        errors = named_company_plan_errors({"steps": [_screen("rk", universe)]}, NAMED, None)
        assert len(errors) == 1, (universe, errors)
        _assert_names(errors[0], 0, "rk")


@pytest.mark.criterion("C3")
def test_c3_exact_member_list_passes():
    # oracle: Requirement 4 by hand — the exact set, in any order or case, is no error
    for universe in (["AAPL", "MSFT", "GOOGL"], ["googl", "Aapl", " MSFT "],
                     ["GOOGL", "MSFT", "AAPL", "aapl"]):
        assert named_company_plan_errors({"steps": [_screen("s1", universe)]}, NAMED, None) == []
    # The named tickers' own case does not matter either.
    lower = [t.lower() for t in NAMED]
    assert named_company_plan_errors({"steps": [_screen("s1", NAMED)]}, lower, None) == []
    # ...and a wrong screen is still an error with lower-case named tickers.
    assert len(named_company_plan_errors({"steps": [_screen("s1", "covered")]}, lower, None)) == 1


@pytest.mark.criterion("C3")
def test_c3_lookups_only_and_second_screen():
    # oracle: Requirement 4 by hand — non-screen steps are never errors; with two screens only
    # the wrong one (index 1) is reported
    lookups = {"steps": [
        {"id": "a", "op": "lookup", "cik": "0000000101", "fy": 2024,
         "concept_family": "ResearchAndDevelopment", "dimensions": "NONE"},
        {"id": "b", "op": "lookup", "cik": "0000000102", "fy": 2024,
         "concept_family": "ResearchAndDevelopment", "dimensions": "NONE"},
        {"id": "c", "op": "compute", "expr": "a - b"},
    ]}
    assert named_company_plan_errors(lookups, NAMED, None) == []
    two = {"steps": [_screen("ok", ["MSFT", "GOOGL", "AAPL"]), _screen("bad", "covered"),
                     {"id": "agg", "op": "aggregate", "of": "bad", "fn": "median"}]}
    errors = named_company_plan_errors(two, NAMED, None)
    assert len(errors) == 1
    _assert_names(errors[0], 1, "bad")


@pytest.mark.criterion("C3")
def test_c3_out_of_scope_gives_nothing():
    # oracle: Requirement 4 by hand — a router universe, fewer than two named companies, or no
    # step list: nothing to check
    wrong = {"steps": [_screen("s1", "covered")]}
    assert len(named_company_plan_errors(wrong, NAMED, None)) == 1  # in scope: an error
    assert named_company_plan_errors(wrong, NAMED, "big_tech") == []
    assert named_company_plan_errors(wrong, NAMED, "covered") == []
    assert named_company_plan_errors(wrong, ["AAPL"], None) == []
    assert named_company_plan_errors(wrong, ["AAPL", "aapl"], None) == []
    assert named_company_plan_errors(wrong, [], None) == []
    assert named_company_plan_errors("not a plan", NAMED, None) == []
    assert named_company_plan_errors({"steps": "s1"}, NAMED, None) == []
    assert named_company_plan_errors({"error": "no_plan"}, NAMED, None) == []


POOL = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]
_LIST = st.lists(st.sampled_from(POOL), min_size=0, max_size=5)
_UNIVERSE = st.one_of(st.just("<absent>"), st.none(), st.sampled_from(["covered", "big_tech"]),
                      _LIST)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.lists(st.sampled_from(POOL), min_size=2, max_size=4, unique=True),
       st.lists(_UNIVERSE, min_size=1, max_size=4), st.randoms())
def test_c3_rule_for_any_plan(named, universes, rnd):
    # oracle: Requirement 4 computed in the test — one error per screen whose universe is not
    # a list of exactly the named set, in step order; permuting or re-casing the named
    # tickers changes nothing
    steps = [_screen(f"s{i}", u) for i, u in enumerate(universes)]
    want = set(named)
    bad = [i for i, u in enumerate(universes)
           if not (isinstance(u, list) and {t.upper() for t in u} == want)]
    errors = named_company_plan_errors({"steps": steps}, named, None)
    assert len(errors) == len(bad)
    for error, i in zip(errors, bad, strict=True):
        assert f"step[{i}]" in error and f"s{i}" in error and "universe" in error
        assert all(t in error for t in named)
    shuffled = [t.lower() for t in named]
    rnd.shuffle(shuffled)
    assert len(named_company_plan_errors({"steps": steps}, shuffled, None)) == len(bad)
