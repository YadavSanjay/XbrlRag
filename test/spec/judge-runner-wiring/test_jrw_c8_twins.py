"""C8: perturbation twins are deterministic for a seed, carry no key and are bounded."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.runner import perturbation_twins

NAMES = {"AAA": ["Alpha Inc"], "BBB": ["Beta Corp"], "CCC": ["Gamma Ltd"]}


def _suite(make_case):
    return [
        make_case("c1", question="What was AAA's revenue in fiscal 2024?", kind="lookup",
                  ticker="AAA", cik=111, fy=2024, concept="Revenue", unit="USD",
                  origin="o1", expected_value=100.0, expected_fact_ids=[1],
                  alt_values=[1.0], tags=["split:dev", "intent:i1"]),
        make_case("c2", question="How much did Beta Corp earn in FY24?", kind="lookup",
                  ticker="BBB", fy=2024, concept="NetIncome", expected_value=5.0,
                  tags=["split:dev"]),
        # Its question names no ticker or company and no year: no twin at all.
        make_case("c3", question="What was the company's revenue?", kind="lookup",
                  ticker="CCC", concept="Revenue", expected_value=7.0, tags=["split:dev"]),
    ]


def _to(seed: int, case_id: str, own: str) -> str:
    # requirement 12's choice rule, computed here
    others = [t for t in ["AAA", "BBB", "CCC"] if t != own]
    return random.Random(f"{seed}:{case_id}").choice(others)


def _expected(seed: int) -> list[tuple]:
    # oracle: requirement 12 with the replacements worked by hand — a ticker form becomes
    # the new ticker, a company name the new ticker's first name; years shift by -1
    to1, to2 = _to(seed, "c1", "AAA"), _to(seed, "c2", "BBB")
    return [
        (f"c1~ticker:{to1}", f"What was {to1}'s revenue in fiscal 2024?", to1, 2024,
         {"kind": "ticker", "from": "AAA", "to": to1}),
        ("c1~year:2023", "What was AAA's revenue in fiscal 2023?", "AAA", 2023,
         {"kind": "year", "from": 2024, "to": 2023}),
        (f"c2~ticker:{to2}", f"How much did {NAMES[to2][0]} earn in FY24?", to2, 2024,
         {"kind": "ticker", "from": "BBB", "to": to2}),
        ("c2~year:2023", "How much did Beta Corp earn in FY23?", "BBB", 2023,
         {"kind": "year", "from": 2024, "to": 2023}),
    ]


def _summary(twins) -> list[tuple]:
    return [(t["twin"].id, t["twin"].question, t["twin"].ticker, t["twin"].fy,
             t["perturbation"]) for t in twins]


@pytest.mark.criterion("C8")
def test_twins_for_seed_7(make_case):
    # oracle: _expected (requirement 12, by hand)
    suite = _suite(make_case)
    twins = perturbation_twins(suite, seed=7, names=NAMES, max_twins=10)
    assert _summary(twins) == _expected(7)
    assert [t["original"].id for t in twins] == ["c1", "c1", "c2", "c2"]

    ticker_twin = twins[0]["twin"]
    original = suite[0]
    assert ticker_twin.cik is None
    assert twins[1]["twin"].cik == 111
    for t in twins:
        twin, orig = t["twin"], t["original"]
        assert twin.expected_value is None
        assert twin.expected_rows is None
        assert twin.expected_text is None
        assert twin.expected_fact_ids == []
        assert twin.alt_values == []
        assert twin.expected_chunk_ids == []
        assert twin.tags == orig.tags
        assert (twin.kind, twin.suite, twin.fiscal_period, twin.concept, twin.unit,
                twin.origin) == (orig.kind, orig.suite, orig.fiscal_period, orig.concept,
                                 orig.unit, orig.origin)
    # the original cases are untouched
    # tolerance: the suite's own 100.0, carried through unchanged
    assert original.expected_value == pytest.approx(100.0, abs=1e-12)
    assert original.question == "What was AAA's revenue in fiscal 2024?"


@pytest.mark.criterion("C8")
def test_bound(make_case):
    # oracle: requirement 12 — the first max_twins items; a negative bound raises
    suite = _suite(make_case)
    assert _summary(perturbation_twins(suite, seed=7, names=NAMES, max_twins=2)) == \
        _expected(7)[:2]
    assert perturbation_twins(suite, seed=7, names=NAMES, max_twins=0) == []
    with pytest.raises(ValueError):
        perturbation_twins(suite, seed=7, names=NAMES, max_twins=-1)


@pytest.mark.criterion("C8")
@given(seed=st.integers(min_value=0, max_value=10**6))
def test_a_function_of_the_seed(make_case, seed):
    # oracle: requirement 12 — two calls agree; the twins match the rule for any seed
    suite = _suite(make_case)
    first = perturbation_twins(suite, seed=seed, names=NAMES, max_twins=10)
    second = perturbation_twins(suite, seed=seed, names=NAMES, max_twins=10)
    assert _summary(first) == _summary(second)
    assert _summary(first) == _expected(seed)
    for t in first:
        if t["perturbation"]["kind"] == "ticker":
            assert t["twin"].ticker != t["original"].ticker
        else:
            assert t["twin"].fy == t["original"].fy - 1
