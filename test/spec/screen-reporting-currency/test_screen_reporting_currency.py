"""Spec tests for screen-reporting-currency: a screen never ranks a filer on a
convenience translation (docs/specs/screen-reporting-currency.md).

`select_reporting_currency` is pure (C1, C2). The `screen()` tests (C3) run over the fake
database in conftest.py, which follows the spec's "Fake database contract". The
reporting-currency rule is written out by hand below (`_reporting_currency`), from
docs/specs/reporting-currency-coverage.md; it is never taken from `choose_lookup_unit`.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import select_reporting_currency

ENDS = ["2023-01-01", "2022-07-01", "2022-01-01", "2021-01-01", "2020-01-01"]
UNITS = ["USD", "TWD", "EUR", "JPY"]
TICKERS = ["AAA", "BBB", "CCC", "DDD"]


def _reporting_currency(obs):
    """The rule written out: among the units at the latest period end, the one tagged at
    the most distinct period ends; a tie, or no dated observation, is no unit."""
    dated = [(u, e) for u, e in obs if e]
    if not dated:
        return None
    latest = max(e for _, e in dated)  # ISO dates order lexically
    at_latest = {u for u, e in dated if e == latest and u}
    if not at_latest:
        return None
    coverage = {u: len({e for v, e in dated if v == u}) for u in at_latest}
    best = max(coverage.values())
    top = [u for u, n in coverage.items() if n == best]
    return top[0] if len(top) == 1 else None


def _expected(rows, observations):
    """Requirement 2 / C1 written out over `_reporting_currency`."""
    foreign = {}
    for t in {r["ticker"] for r in rows}:
        if t not in observations:
            continue
        unit = _reporting_currency(observations[t])
        if unit is not None and unit != "USD":
            foreign[t] = unit
    kept = [r for r in rows if r["ticker"] not in foreign]
    excluded = [{"ticker": t, "reason": "currency", "currency": foreign[t]}
                for t in sorted(foreign)]
    return kept, excluded


def _row(ticker, value, fact_id):
    return {"ticker": ticker, "fiscal_year": 2022, "value": value, "unit": "USD",
            "fact_id": fact_id}


def _exclusions(result) -> list:
    # `disclosure` comes with screen-universe; read it tolerantly so its absence fails an
    # assertion rather than raising.
    return (result.to_dict().get("disclosure") or {}).get("exclusions") or []


def _currency_exclusions(result) -> list:
    return sorted(
        ({"ticker": e.get("ticker"), "reason": e.get("reason"), "currency": e.get("currency")}
         for e in _exclusions(result) if e.get("reason") == "currency"),
        key=lambda e: str(e["ticker"]))


def _ranked(result) -> list:
    return [(r["ticker"], r["value"], r["rank"]) for r in result.rows]


# --------------------------------------------------------------------------- strategies
obs_st = st.lists(st.tuples(st.sampled_from(UNITS), st.one_of(st.none(), st.sampled_from(ENDS))),
                  max_size=8)
rows_st = st.lists(
    st.builds(_row, st.sampled_from(TICKERS),
              st.floats(min_value=0, max_value=1e12, allow_nan=False),
              st.integers(min_value=1, max_value=10**6)),
    min_size=1, max_size=8)
observations_st = st.dictionaries(st.sampled_from(TICKERS + ["EEE"]), obs_st, max_size=5)


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_point_cases(tsmc_capex_observations):
    # oracle: stored facts 80149-80152 (spec Notes) and the rule by hand — TSM's latest
    # period end 2023-01-01 carries TWD and USD; TWD is tagged at 3 period ends, USD at 1,
    # so TSM reports in TWD and is excluded. USD-only: USD. Equal coverage: no unit, kept.
    # Absent from observations: kept.
    rows = [
        _row("AMZN", 63_645_000_000.0, 1001),
        _row("TSM", 35_231_800_000.0, 80152),
        _row("TIE", 1.0, 3),
        _row("NOOBS", 2.0, 4),
        _row("AMZN", 1.5, 1006),
    ]
    observations = {
        "TSM": tsmc_capex_observations,
        "AMZN": [("USD", "2023-01-01"), ("USD", "2022-01-01")],
        "TIE": [("EUR", "2023-01-01"), ("USD", "2023-01-01"),
                ("EUR", "2022-01-01"), ("USD", "2022-01-01")],
    }
    kept, excluded = select_reporting_currency(rows, observations)
    assert kept == [rows[0], rows[2], rows[3], rows[4]]
    assert excluded == [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]

    tsm_only, tsm_excluded = select_reporting_currency([rows[1]], {"TSM": tsmc_capex_observations})
    assert tsm_only == []
    assert tsm_excluded == [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
    usd_kept, usd_excluded = select_reporting_currency(
        [rows[0]], {"AMZN": [("USD", "2023-01-01")]})
    assert usd_kept == [rows[0]] and usd_excluded == []


@pytest.mark.criterion("C1")
@given(rows_st, observations_st)
def test_shape_of_the_result_for_any_input(rows, observations):
    # oracle: C1's statement of the result — kept is the rows of the kept tickers in input
    # order; excluded is one {"ticker", "reason": "currency", "currency"} per other row
    # ticker, sorted by ticker, its currency a unit other than USD
    kept, excluded = select_reporting_currency(rows, observations)
    excluded_tickers = [e["ticker"] for e in excluded]
    assert excluded_tickers == sorted(set(excluded_tickers))
    assert kept == [r for r in rows if r["ticker"] not in set(excluded_tickers)]
    for e in excluded:
        assert set(e) == {"ticker", "reason", "currency"}
        assert e["reason"] == "currency"
        assert e["currency"] not in (None, "USD")
        assert e["ticker"] in observations
    assert (kept, excluded) == _expected(rows, observations)


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_rule_point_cases():
    # oracle: the rule by hand (reporting-currency-coverage C1) — TSMC cash FY2024 tags
    # TWD at 4 period ends and USD at 2: TWD. A filer whose latest period is USD only
    # reports in USD even with older EUR periods. No dated observation: no unit, kept.
    rows = [_row("TSM", 1.0, 1), _row("XUSD", 2.0, 2), _row("NODATE", 3.0, 3)]
    observations = {
        "TSM": [("TWD", "2025-01-01"), ("TWD", "2025-01-01"), ("USD", "2025-01-01"),
                ("TWD", "2024-01-01"), ("USD", "2024-01-01"),
                ("TWD", "2023-01-01"), ("TWD", "2022-01-01")],
        "XUSD": [("USD", "2025-01-01"), ("EUR", "2024-01-01"), ("EUR", "2023-01-01")],
        "NODATE": [("EUR", None), ("EUR", None)],
    }
    kept, excluded = select_reporting_currency(rows, observations)
    assert kept == [rows[1], rows[2]]
    assert excluded == [{"ticker": "TSM", "reason": "currency", "currency": "TWD"}]


@pytest.mark.criterion("C2")
@given(rows_st, observations_st)
def test_agrees_with_the_rule_and_partitions_tickers(rows, observations):
    # oracle: Requirement 2 with the rule written out (_reporting_currency)
    kept, excluded = select_reporting_currency(rows, observations)
    assert (kept, excluded) == _expected(rows, observations)
    kept_tickers = {r["ticker"] for r in kept}
    excluded_tickers = {e["ticker"] for e in excluded}
    assert kept_tickers.isdisjoint(excluded_tickers)
    assert kept_tickers | excluded_tickers == {r["ticker"] for r in rows}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rows_st, observations_st, st.randoms(use_true_random=False),
       st.sampled_from(ENDS[1:]))
def test_order_repeats_and_earlier_periods_do_not_matter(rows, observations, rnd, earlier):
    # oracle: metamorphic (reporting-currency-coverage C2-C3) — shuffling the
    # observations, repeating each one, or adding a period end earlier than the latest for
    # the filer's reporting currency leaves the result unchanged
    base = select_reporting_currency(rows, observations)
    shuffled = {}
    for t, obs in observations.items():
        obs = list(obs)
        rnd.shuffle(obs)
        shuffled[t] = obs
    assert select_reporting_currency(rows, shuffled) == base
    repeated = {t: list(obs) + list(obs) for t, obs in observations.items()}
    assert select_reporting_currency(rows, repeated) == base
    grown = {}
    for t, obs in observations.items():
        unit = _reporting_currency(obs)
        if unit is None:
            grown[t] = list(obs)
            continue
        latest = max(e for _, e in obs if e)
        extra = [(unit, "1999-01-01")] + ([(unit, earlier)] if earlier < latest else [])
        grown[t] = list(obs) + extra
    assert select_reporting_currency(rows, grown) == base
    assert base == _expected(rows, observations)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_fy2022_capex_screen_does_not_rank_tsm(fake_screen, capex_store):
    # oracle: Requirements 1-3 applied by hand to the fake store (spec Notes) — TSM
    # reports in TWD (3 period ends against USD's 1), so it is excluded for currency and
    # the rest rank by USD capex: AMZN 63.645B > GOOGL 31.485B > META 31.431B >
    # INTC 24.844B > MSFT 23.886B; the frozen v2 dev answer key also excludes TSM
    result = fake_screen(capex_store())
    assert result.is_ok
    assert [r["ticker"] for r in result.rows] == ["AMZN", "GOOGL", "META", "INTC", "MSFT"]
    assert [r["rank"] for r in result.rows] == [1, 2, 3, 4, 5]
    # Values pass through float() from the store unchanged; rel=1e-12 only absorbs repr.
    assert [r["value"] for r in result.rows] == pytest.approx(
        [63_645_000_000.0, 31_485_000_000.0, 31_431_000_000.0, 24_844_000_000.0,
         23_886_000_000.0], rel=1e-12)
    assert {r["unit"] for r in result.rows} == {"USD"}
    assert _currency_exclusions(result) == [
        {"ticker": "TSM", "reason": "currency", "currency": "TWD"}]
    # The peer-relative reading: META is rank 3, not 4.
    assert {r["ticker"]: r["rank"] for r in result.rows}["META"] == 3
    # The fiscal-year `excluded` key keeps its meaning: every filer has FY2022.
    assert result.to_dict().get("excluded") == []


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_removing_tsm_twd_ranks_tsm_on_usd(fake_screen, capex_store):
    # oracle: metamorphic, Requirement 2 by hand — without its TWD observations TSM's
    # only unit is USD, so it is ranked at USD 35,231,800,000, second behind AMZN; with
    # them it is excluded for currency
    base = fake_screen(capex_store())
    assert "TSM" not in [r["ticker"] for r in base.rows]
    assert [e["ticker"] for e in _currency_exclusions(base)] == ["TSM"]

    store = capex_store()
    store["TSM"]["obs"] = [ob for ob in store["TSM"]["obs"] if ob[0] != "TWD"]
    result = fake_screen(store)
    assert [r["ticker"] for r in result.rows] == [
        "AMZN", "TSM", "GOOGL", "META", "INTC", "MSFT"]
    assert [r["rank"] for r in result.rows] == [1, 2, 3, 4, 5, 6]
    tsm = result.rows[1]
    # Stored value passed through float(); rel=1e-12 only absorbs repr.
    assert tsm["value"] == pytest.approx(35_231_800_000.0, rel=1e-12)
    assert tsm["fact_id"] == "80152"
    assert _currency_exclusions(result) == []


@st.composite
def _stores(draw):
    """A random FY2022 store: each filer has a current USD row and maybe a foreign unit;
    ZZZ always reports in TWD (TWD at 3 period ends, USD at 1)."""
    tickers = draw(st.lists(st.sampled_from(["AAA", "BBB", "CCC", "DDD", "EEE"]),
                            unique=True, max_size=5))
    values = draw(st.lists(st.integers(min_value=1, max_value=10**12), unique=True,
                           min_size=len(tickers) + 1, max_size=len(tickers) + 1))
    earlier = ["2022-01-01", "2021-01-01", "2020-01-01"]
    store = {}
    for i, t in enumerate(tickers):
        n_usd = draw(st.integers(min_value=0, max_value=2))
        obs = [("USD", "2023-01-01", float(values[i]), 2000 + i, True)]
        obs += [("USD", e, float(values[i]) / 2, 3000 + 10 * i + k, False)
                for k, e in enumerate(earlier[:n_usd])]
        foreign = draw(st.one_of(st.none(), st.sampled_from(["TWD", "EUR", "JPY"])))
        if foreign is not None:
            n_foreign = draw(st.integers(min_value=0, max_value=3))
            obs.append((foreign, "2023-01-01", float(values[i]) * 30, 4000 + i, True))
            obs += [(foreign, e, float(values[i]) * 20, 5000 + 10 * i + k, False)
                    for k, e in enumerate(earlier[:n_foreign])]
        store[t] = {"cik": f"{i + 1:010d}", "fiscal_year": 2022, "obs": obs}
    v = float(values[-1])
    store["ZZZ"] = {"cik": "0000000099", "fiscal_year": 2022, "obs": [
        ("TWD", "2021-01-01", v * 15, 6001, False), ("TWD", "2022-01-01", v * 20, 6002, False),
        ("TWD", "2023-01-01", v * 30, 6003, True), ("USD", "2023-01-01", v, 6004, True)]}
    return store


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(_stores())
def test_screen_equals_the_usd_only_store_without_foreign_filers(fake_screen, store):
    # oracle: Requirements 1-3 by hand with the rule written out — the filers whose
    # reporting currency is not USD are excluded for currency and the screen ranks the
    # rest exactly as a store holding only the USD filers does, which (every filer
    # reporting in USD) is the ranking before this change: USD value descending, ranks 1..n
    currency = {t: _reporting_currency([(u, e) for u, e, _, _, _ in entry["obs"]])
                for t, entry in store.items()}
    foreign = {t: u for t, u in currency.items() if u is not None and u != "USD"}
    usd_value = {t: next(v for u, _, v, _, cur in entry["obs"] if u == "USD" and cur)
                 for t, entry in store.items()}
    kept = sorted((t for t in store if t not in foreign), key=lambda t: -usd_value[t])
    expected = [(t, usd_value[t], i) for i, t in enumerate(kept, start=1)]

    result = fake_screen(store)
    assert _ranked(result) == expected
    assert _currency_exclusions(result) == [
        {"ticker": t, "reason": "currency", "currency": foreign[t]} for t in sorted(foreign)]

    usd_only = {t: {**entry, "obs": [ob for ob in entry["obs"] if ob[0] == "USD"]}
                for t, entry in store.items() if t not in foreign}
    reference = fake_screen(usd_only)
    assert _ranked(reference) == expected
    assert _currency_exclusions(reference) == []
