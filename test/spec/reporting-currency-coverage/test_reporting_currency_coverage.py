"""Spec tests for reporting-currency-coverage: the reporting currency is the
latest-period unit tagged at the most distinct period ends
(docs/specs/reporting-currency-coverage.md). Pure: no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import choose_lookup_unit

# TSMC cash and cash equivalents, FY2024 20-F (mv_financial_facts, 2026-10-15): USD is
# tagged for the prior year-end too, so both units have a comparative.
TSMC_CASH = [
    ("TWD", "2025-01-01"), ("TWD", "2025-01-01"), ("USD", "2025-01-01"),
    ("TWD", "2024-01-01"), ("USD", "2024-01-01"),
    ("TWD", "2023-01-01"), ("TWD", "2022-01-01"),
]
TSMC_REVENUE = [("TWD", "2025-01-01"), ("USD", "2025-01-01"),
                ("TWD", "2024-01-01"), ("TWD", "2023-01-01")]
ENDS = ["2026-01-01", "2025-01-01", "2024-01-01", "2023-01-01", "2022-01-01"]
UNITS = ["TWD", "USD", "EUR", "JPY"]


def _oracle(obs, requested=None):
    """The rule written out."""
    dated = [(u, e) for u, e in obs if e]
    if not dated:
        return None
    latest = max(e for _, e in dated)
    latest_units = {u for u, e in dated if e == latest and u}
    if requested is not None:
        return requested if requested in latest_units else None
    if len(latest_units) == 1:
        return next(iter(latest_units))
    coverage = {u: len({e for v, e in dated if v == u}) for u in latest_units}
    best = max(coverage.values())
    top = [u for u, n in coverage.items() if n == best]
    return top[0] if len(top) == 1 else None


def _old_rule(obs):
    """The unit-aware-lookup rule this spec refines (for C3)."""
    dated = [(u, e) for u, e in obs if e]
    latest = max(e for _, e in dated)
    latest_units = {u for u, e in dated if e == latest and u}
    if len(latest_units) == 1:
        return next(iter(latest_units))
    with_comp = latest_units & {u for u, e in dated if e != latest and u}
    return next(iter(with_comp)) if len(with_comp) == 1 else None


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases():
    # oracle: the definition, by hand on TSMC's stored facts
    assert choose_lookup_unit(TSMC_CASH) == "TWD"
    assert choose_lookup_unit(TSMC_REVENUE) == "TWD"
    equal = [("USD", ENDS[0]), ("EUR", ENDS[0]), ("USD", ENDS[1]), ("EUR", ENDS[1])]
    assert choose_lookup_unit(equal) is None
    assert choose_lookup_unit(TSMC_CASH, "USD") == "USD"


observations = st.lists(st.tuples(st.sampled_from(UNITS), st.sampled_from(ENDS)),
                        min_size=1, max_size=10)


@pytest.mark.criterion("C1")
@given(observations, st.sampled_from(ENDS[1:]))
def test_more_periods_wins(obs, extra_end):
    # oracle: the rule — give TWD strictly more period ends than any other latest unit
    latest = max(e for _, e in obs)
    twd = [("TWD", latest)] + [("TWD", e) for e in ENDS if e < latest]
    others = [(u, e) for u, e in obs if u != "TWD"]
    other_cov = max([len({e for v, e in others if v == u}) for u in {u for u, _ in others}],
                    default=0)
    if len({e for _, e in twd}) > other_cov:
        assert choose_lookup_unit(twd + others) == "TWD"
    assert choose_lookup_unit(TSMC_CASH) == "TWD"


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@given(observations, st.one_of(st.none(), st.sampled_from(UNITS)))
def test_matches_the_rule(obs, requested):
    # oracle: the rule written out (`_oracle`); duplicates must not count
    assert choose_lookup_unit(obs, requested) == _oracle(obs, requested)
    assert choose_lookup_unit(obs + obs, requested) == _oracle(obs, requested)
    assert choose_lookup_unit(TSMC_CASH) == "TWD"


@pytest.mark.criterion("C2")
def test_duplicates_do_not_count():
    # oracle: the definition — USD repeated many times at two ends is still 2 ends
    padded = TSMC_CASH + [("USD", "2025-01-01")] * 5 + [("USD", "2024-01-01")] * 5
    assert choose_lookup_unit(padded) == "TWD"


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(observations, st.randoms(use_true_random=False), st.sampled_from(ENDS))
def test_properties(obs, rnd, earlier):
    # oracle: the C3 properties, checked directly
    shuffled = list(obs)
    rnd.shuffle(shuffled)
    chosen = choose_lookup_unit(obs)
    assert choose_lookup_unit(shuffled) == chosen
    latest = max(e for _, e in obs)
    if chosen is not None and earlier < latest:
        assert choose_lookup_unit([*obs, (chosen, earlier)]) == chosen
    old = _old_rule(obs)
    if old is not None:
        assert chosen == old
    assert choose_lookup_unit(list(reversed(TSMC_CASH))) == "TWD"


@pytest.mark.criterion("C3")
def test_properties_point_case():
    # oracle: the C3 properties by hand — TSMC revenue keeps the old rule's TWD; adding a
    # TWD comparative to TSMC cash keeps TWD
    assert choose_lookup_unit(TSMC_REVENUE) == _old_rule(TSMC_REVENUE) == "TWD"
    assert choose_lookup_unit([*TSMC_CASH, ("TWD", "2021-01-01")]) == "TWD"
