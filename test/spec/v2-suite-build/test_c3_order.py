"""C3: ``order_candidates`` is a hash round-robin over strata (filer, or universe).

Oracle: requirement 8 of docs/specs/v2-suite-build.md, restated in the test with
``hashlib`` over the intent ids (themselves restated in conftest).
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import order_candidates


def _intents():
    out = [Intent("lookup", tickers=[t], fiscal_years=[y], metrics=[m])
           for t, years in (("AAA", (2022, 2023, 2024)), ("BBB", (2023, 2024)), ("TWX", (2024,)))
           for y in years for m in ("Revenue", "NetIncome")]
    return out


def _screens():
    return [Intent("screen", operation="top", n=3, universe=u, fiscal_years=[y],
                   metrics=["Revenue"]) for u in ("covered", "peers") for y in (2022, 2023, 2024)]


def _oracle(kit, intents):
    def digest(i):
        return hashlib.sha1(kit.intent_id(i).encode("utf-8")).hexdigest()

    strata: dict[str, list] = {}
    for i in intents:
        strata.setdefault(i.tickers[0] if i.tickers else i.universe, []).append(i)
    ranked = []
    for items in strata.values():
        for rank, i in enumerate(sorted(items, key=digest)):
            ranked.append((rank, digest(i), i))
    return [i for _r, _d, i in sorted(ranked, key=lambda t: (t[0], t[1]))]


@pytest.mark.criterion("C3")
def test_order_is_the_hash_round_robin(kit):
    # oracle: requirement 8, computed with hashlib in _oracle
    intents = _intents()
    got = order_candidates(intents)
    assert got == _oracle(kit, intents)
    assert len(got) == 12
    # the first three come from the three filers, one each
    assert {i.tickers[0] for i in got[:3]} == {"AAA", "BBB", "TWX"}
    # AAA 6, BBB 4, TWX 2: ranks 0-1 take all three filers (positions 0-5), ranks 2-3
    # AAA and BBB (6-9), ranks 4-5 AAA alone (10-11)
    assert [i.tickers[0] for i in got[:6]].count("TWX") == 2
    assert {i.tickers[0] for i in got[6:10]} == {"AAA", "BBB"}
    assert {i.tickers[0] for i in got[10:]} == {"AAA"}


@pytest.mark.criterion("C3")
def test_screens_stratify_by_universe(kit):
    # oracle: requirement 8 — an intent without a ticker is stratified by its universe
    intents = _screens()
    got = order_candidates(intents)
    assert got == _oracle(kit, intents)
    assert {got[0].universe, got[1].universe} == {"covered", "peers"}


@pytest.mark.criterion("C3")
def test_duplicate_raises():
    # oracle: requirement 8 — a duplicate intent is a ValueError
    intents = _intents()
    with pytest.raises(ValueError):
        order_candidates([*intents, Intent("lookup", tickers=["aaa"], fiscal_years=[2022],
                                           metrics=["Revenue"])])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(perm=st.permutations(list(range(12))))
def test_order_ignores_input_order(kit, perm):
    # oracle: requirement 8 — the order depends only on the set of intents
    intents = _intents()
    expected = _oracle(kit, intents)
    assert order_candidates([intents[k] for k in perm]) == expected
