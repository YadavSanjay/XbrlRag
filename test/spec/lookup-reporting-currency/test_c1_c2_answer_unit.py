"""C1-C2: the unit a lookup answers in (docs/specs/lookup-reporting-currency.md, Requirement 1).

Pure: no database. The TSMC observations are the fixture's stored rows; expected units follow
from the rule applied by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.reporting_currency import answer_unit
from src.retriever.xbrl.xbrl_factstore_screen import select_reporting_currency

ENDS = ["2026-01-01", "2025-01-01", "2024-01-01", "2023-01-01", "2022-01-01", "2021-01-01"]
CODES = ["TWD", "USD", "EUR", "JPY"]


def shown(unit: str | None) -> str | None:
    """The store's per-share product in its shown form (``shares*TWD`` -> ``TWD/shares``)."""
    if unit is not None and unit.startswith("shares*"):
        return unit.split("*", 1)[1] + "/shares"
    return unit


def observations_of(kit, family: str) -> list[tuple[str | None, str | None]]:
    """``(shown unit, period end)`` of every fixture row of one family (every filing)."""
    return [(shown(r["unit"]), r["period_end"]) for r in kit.tsm_rows()
            if r["concept_family"] == family]


def rule(candidate_units, obs):
    """Requirement 1 written out (with the coverage rule of reporting-currency-coverage)."""
    units = {u for u in candidate_units if u is not None}
    if not units:
        return None
    if len(units) == 1:
        return next(iter(units))
    dated = [(u, e) for u, e in obs if e]
    if not dated:
        return None
    latest = max(e for _, e in dated)
    latest_units = {u for u, e in dated if e == latest and u}
    if not latest_units:
        return None
    coverage = {u: len({e for v, e in dated if v == u}) for u in latest_units}
    best = max(coverage.values())
    top = [u for u, n in coverage.items() if n == best]
    reporting = top[0] if len(top) == 1 else None
    return reporting if reporting in units else None


# ------------------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_tsmc_cash_answers_in_twd(kit):
    # oracle: fixture rows of CashAndEquivalents: TWD at 7 distinct period ends
    # (2020-01-01 .. 2026-01-01), USD at 5 (2022-01-01 .. 2026-01-01); both at the latest
    # period, so the reporting currency is TWD (Requirement 1, by hand)
    obs = observations_of(kit, "CashAndEquivalents")
    assert len({e for u, e in obs if u == "TWD"}) == 7
    assert len({e for u, e in obs if u == "USD"}) == 5
    assert answer_unit(["TWD", "USD"], obs) == "TWD"
    assert answer_unit(["USD", "TWD", "TWD"], obs) == "TWD"


@pytest.mark.criterion("C1")
def test_tsmc_per_share_answers_in_twd_per_share(kit):
    # oracle: fixture rows of EarningsPerShareDiluted: TWD/shares tagged at more period ends
    # than USD/shares, both at the latest period (counted below from the fixture)
    obs = observations_of(kit, "EarningsPerShareDiluted")
    twd = {e for u, e in obs if u == "TWD/shares"}
    usd = {e for u, e in obs if u == "USD/shares"}
    assert max(e for _, e in obs) in twd & usd
    assert len(twd) > len(usd)
    assert answer_unit(["TWD/shares", "USD/shares"], obs) == "TWD/shares"


@pytest.mark.criterion("C1")
def test_single_unit_ties_and_foreign_reporting_currency(kit):
    # oracle: Requirement 1 by hand: one candidate unit is the answer whatever the
    # observations; a coverage tie is no unit; a reporting currency no candidate carries is
    # no unit; unitless candidates are no unit
    cash = observations_of(kit, "CashAndEquivalents")
    assert answer_unit(["USD"], cash) == "USD"
    assert answer_unit(["EUR", "EUR"], []) == "EUR"
    tie = [("USD", ENDS[0]), ("EUR", ENDS[0]), ("USD", ENDS[1]), ("EUR", ENDS[1])]
    assert answer_unit(["USD", "EUR"], tie) is None
    assert answer_unit(["USD", "EUR"], cash) is None   # cash names TWD
    assert answer_unit([None, None], cash) is None


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_tsmc_cash_answer_survives_reordering_and_repeats(kit):
    # oracle: Requirement 1 — only distinct period ends count and order is irrelevant, so the
    # fixture's cash observations reversed and doubled still give TWD; with USD kept only at
    # the latest period TWD still covers more ends (by hand, as above)
    obs = observations_of(kit, "CashAndEquivalents")
    assert answer_unit(["USD", "TWD"], list(reversed(obs)) * 2) == "TWD"
    latest = max(e for _, e in obs)
    usd_latest_only = [(u, e) for u, e in obs if u != "USD" or e == latest]
    assert answer_unit(["TWD", "USD"], usd_latest_only) == "TWD"


# ------------------------------------------------------------------------------------- C2
units_st = st.sampled_from(CODES)
obs_st = st.lists(st.tuples(units_st, st.sampled_from(ENDS)), min_size=0, max_size=12)
cands_st = st.lists(st.one_of(st.none(), units_st), min_size=0, max_size=4)


def _with_reporting(obs, code):
    """Observations where *code* is at the latest period and at strictly the most ends."""
    latest = max([e for _, e in obs] + [ENDS[1]])
    return list(obs) + [(code, e) for e in ENDS if e <= latest] + [(code, ENDS[0])]


@pytest.mark.criterion("C2")
def test_tsmc_cash_one_definition_point(kit):
    # oracle: screen-reporting-currency R2 by hand: TSMC's cash observations name TWD, so the
    # screen excludes TSM for TWD and the lookup answers TWD; a USD filer (USD at three ends,
    # EUR at the latest only) is kept by the screen and answers USD
    obs = observations_of(kit, "CashAndEquivalents")
    rows = [{"ticker": "TSM", "value": 1.0}, {"ticker": "ZZUS", "value": 2.0}]
    usd = [("USD", ENDS[0]), ("USD", ENDS[1]), ("USD", ENDS[2]), ("EUR", ENDS[0])]
    kept, excluded = select_reporting_currency(rows, {"TSM": obs, "ZZUS": usd})
    assert excluded == [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
    assert kept == rows[1:]
    assert answer_unit(["TWD", "USD"], obs) == "TWD"
    assert answer_unit(["USD", "EUR"], usd) == "USD"


@pytest.mark.criterion("C2")
@given(cands_st, obs_st)
def test_agrees_with_the_rule(cands, obs):
    # oracle: Requirement 1 written out in `rule`
    assert answer_unit(cands, obs) == rule(cands, obs)


@pytest.mark.criterion("C2")
@given(st.lists(st.tuples(units_st, st.sampled_from(ENDS[1:])), max_size=8),
       st.sampled_from(CODES), st.sampled_from(CODES), st.randoms(use_true_random=False))
def test_reporting_currency_is_chosen_whatever_the_order(obs, code, other, rnd):
    # oracle: Requirement 1: the code at the latest period with strictly the most period ends
    # is the reporting currency; order and repeats of observations do not matter
    full = _with_reporting(obs, code)
    cands = [code, other] if other != code else [code, "XXX"]
    want = code
    assert answer_unit(cands, full) == want
    shuffled = list(full) + list(full)
    rnd.shuffle(shuffled)
    rev = list(reversed(cands))
    assert answer_unit(rev, shuffled) == want


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.lists(st.tuples(units_st, st.sampled_from(ENDS[1:])), max_size=8),
       st.sampled_from(CODES), st.sampled_from(ENDS))
def test_an_earlier_period_for_the_reporting_currency_changes_nothing(obs, code, end):
    # oracle: Requirement 1: more coverage for the chosen unit keeps it chosen
    full = _with_reporting(obs, code)
    cands = sorted({code, "USD", "EUR"})
    before = answer_unit(cands, full)
    assert before == code
    latest = max(e for _, e in full)
    if end < latest:
        assert answer_unit(cands, full + [(code, end)]) == before


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(st.lists(st.tuples(units_st, st.sampled_from(ENDS[1:])), max_size=8),
       st.sampled_from(CODES), st.permutations(CODES))
def test_no_currency_is_special(obs, code, perm):
    # oracle: Requirement 5: renaming every code by one bijection renames the answer
    full = _with_reporting(obs, code)
    cands = list(CODES)
    m = dict(zip(CODES, perm, strict=True))
    renamed = [(m[u], e) for u, e in full]
    assert answer_unit(cands, full) == code
    assert answer_unit([m[u] for u in cands], renamed) == m[code]


@pytest.mark.criterion("C2")
@given(st.lists(st.tuples(units_st, st.sampled_from(ENDS[1:])), max_size=8),
       st.sampled_from(CODES))
def test_one_definition_with_the_screen(obs, code):
    # oracle: screen-reporting-currency R1-R2: the screen excludes a filer for currency X
    # exactly when X is its reporting currency over the same observations, and keeps it when
    # that currency is USD; the lookup's answer unit is the same currency
    full = _with_reporting(obs, code)
    rows = [{"ticker": "F", "value": 1.0}]
    kept, excluded = select_reporting_currency(rows, {"F": full})
    cands = list(CODES)
    if excluded:
        assert excluded[0]["currency"] == code
        assert answer_unit(cands, full) == excluded[0]["currency"]
    else:
        assert code == "USD" and kept == rows
        assert answer_unit(cands, full) == "USD"
