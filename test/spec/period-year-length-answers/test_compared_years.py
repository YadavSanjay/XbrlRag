"""Spec tests for period-year-length-answers C1 and C2: which compared periods of a compute
or cagr step differ in length (docs/specs/period-year-length-answers.md, Requirement 2).

The stored periods are period-disclosure C3's (SEC companyfacts start/end, stored end = last
day + 1); day counts are worked by hand in the comments.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.year_length_answers import compared_year_lengths

ENTRY_KEYS = {"current_step", "prior_step", "fact_ids", "days_current", "days_prior",
              "current_period", "prior_period", "note"}


def _synthetic(step_id, fact_id, fy, start: dt.date, days: int, **over):
    row = {"step_id": step_id, "op": "lookup", "status": "OK", "cik": 1,
           "concept_family": "Revenue", "fiscal_period": "FY", "ytd": False, "fy": fy,
           "fact_id": fact_id, "period_start": start.isoformat(),
           "period_end": (start + dt.timedelta(days=days)).isoformat()}
    row.update(over)
    return row


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("order", ["current_first", "prior_first"])
def test_aapl_fy2024_against_53_week_fy2023(kit, order):
    # oracle: period-disclosure C3 / companyfacts: FY2024 2023-10-01..2024-09-28 = 364 days,
    # FY2023 2022-09-25..2023-09-30 = 371 days (53 weeks); 7 apart >= 6
    s1 = kit.lookup_input("s1", 14262, 2024)
    s2 = kit.lookup_input("s2", 12096, 2023)
    inputs = [s1, s2] if order == "current_first" else [s2, s1]
    out = compared_year_lengths(inputs)
    assert len(out) == 1
    e = out[0]
    assert set(e) == ENTRY_KEYS
    assert (e["current_step"], e["prior_step"]) == ("s1", "s2")
    assert list(e["fact_ids"]) == [14262, 12096]
    assert (e["days_current"], e["days_prior"]) == (364, 371)
    assert tuple(e["current_period"]) == ("2023-10-01", "2024-09-28")
    assert tuple(e["prior_period"]) == ("2022-09-25", "2023-09-30")
    for word in ("364", "371", "53-week", "reported basis"):
        assert word in e["note"]


@pytest.mark.criterion("C1")
def test_three_years_compare_consecutive_pairs(kit):
    # oracle: by hand, FY2022 364, FY2023 371, FY2024 364: both consecutive pairs 7 apart;
    # ascending years, the later year is current
    inputs = [kit.lookup_input("a", 14262, 2024), kit.lookup_input("b", 10919, 2022),
              kit.lookup_input("c", 12096, 2023)]
    out = compared_year_lengths(inputs)
    assert [(e["current_step"], e["prior_step"]) for e in out] == [("c", "b"), ("a", "c")]
    assert [(e["days_current"], e["days_prior"]) for e in out] == [(371, 364), (364, 371)]
    assert [list(e["fact_ids"]) for e in out] == [[12096, 10919], [14262, 12096]]


@pytest.mark.criterion("C1")
def test_equal_msft_years_add_nothing_beside_aapl(kit):
    # oracle: MSFT FY2026 and FY2025 are 365 days each (companyfacts), so only AAPL differs
    inputs = [
        kit.lookup_input("m1", 105742, 2026, cik=kit.MSFT_CIK),
        kit.lookup_input("m2", 104052, 2025, cik=kit.MSFT_CIK),
        kit.lookup_input("a1", 14262, 2024), kit.lookup_input("a2", 12096, 2023),
    ]
    out = compared_year_lengths(inputs)
    assert [(e["current_step"], e["prior_step"]) for e in out] == [("a1", "a2")]


_starts = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2030, 12, 31))
_lengths = st.integers(min_value=340, max_value=390)


@pytest.mark.criterion("C1")
@given(s1=_starts, d1=_lengths, s2=_starts, d2=_lengths, swap=st.booleans())
def test_entry_exactly_when_six_or_more_days_apart(s1, d1, s2, d2, swap):
    # oracle: period-disclosure R3, differs = |days_current - days_prior| >= 6, worked here
    cur = _synthetic("c", 2, 2025, s1, d1)
    pri = _synthetic("p", 1, 2024, s2, d2)
    out = compared_year_lengths([pri, cur] if swap else [cur, pri])
    if abs(d1 - d2) >= 6:
        assert len(out) == 1
        assert (out[0]["days_current"], out[0]["days_prior"]) == (d1, d2)
        assert (out[0]["current_step"], out[0]["prior_step"]) == ("c", "p")
    else:
        assert out == []


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(perm=st.permutations([0, 1, 2]), shift=st.integers(min_value=-3000, max_value=3000))
def test_permutation_and_shift(kit, perm, shift):
    # oracle: the result depends on the years, not the input order; shifting every date by
    # the same days moves only the periods (C3's shift property)
    base = [kit.lookup_input("a", 14262, 2024), kit.lookup_input("b", 10919, 2022),
            kit.lookup_input("c", 12096, 2023)]
    out = compared_year_lengths(base)
    assert compared_year_lengths([base[i] for i in perm]) == out
    moved = dt.timedelta(days=shift)
    shifted = []
    for row in base:
        r = dict(row)
        r["period_start"] = (dt.date.fromisoformat(r["period_start"]) + moved).isoformat()
        r["period_end"] = (dt.date.fromisoformat(r["period_end"]) + moved).isoformat()
        shifted.append(r)
    got = compared_year_lengths(shifted)
    keep = ("current_step", "prior_step", "days_current", "days_prior")
    assert [tuple(e[k] for k in keep) for e in got] == [tuple(e[k] for k in keep) for e in out]
    assert len(got) == 2
    assert got[0]["current_period"][0] == (dt.date(2022, 9, 25) + moved).isoformat()


# ---------------------------------------------------------------- C2: eligibility, grouping

_INELIGIBLE = {
    "not_ok": {"status": "AMBIGUOUS"},
    "not_lookup": {"op": "compute"},
    "instant": {"period_start": None},
    "no_end": {"period_end": None},
    "fy_none": {"fy": None},
    "fy_bool": {"fy": True},
    "fy_str": {"fy": "2023"},
}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", sorted(_INELIGIBLE))
def test_an_ineligible_input_compares_nothing(kit, name):
    # oracle: Requirement 2.1; the eligible AAPL pair alone is one entry (C1)
    good = kit.lookup_input("s2", 12096, 2023)
    bad = dict(kit.lookup_input("s1", 14262, 2024), **_INELIGIBLE[name])
    assert compared_year_lengths([bad, good]) == []
    assert len(compared_year_lengths([kit.lookup_input("s1", 14262, 2024), good])) == 1


_SPLIT = {
    "cik": {"cik": 1},
    "family": {"concept_family": "NetIncome"},
    "period": {"fiscal_period": "Q4"},
    "ytd": {"ytd": True},
    "same_year": {"fy": 2023},
}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", sorted(_SPLIT))
def test_different_groups_or_one_year_are_never_compared(kit, name):
    # oracle: Requirement 2.2: two filers, two metrics, a year and a quarter, year and ytd,
    # or one year twice compare nothing
    cur = dict(kit.lookup_input("s1", 14262, 2024), **_SPLIT[name])
    pri = kit.lookup_input("s2", 12096, 2023)
    assert compared_year_lengths([cur, pri]) == []
    assert len(compared_year_lengths([kit.lookup_input("s1", 14262, 2024), pri])) == 1


@pytest.mark.criterion("C2")
def test_fiscal_period_none_and_case_mean_fy(kit):
    # oracle: Requirement 2.2: None is "FY", the fiscal period is compared upper-cased,
    # ytd None is False
    cur = kit.lookup_input("s1", 14262, 2024, fiscal_period=None, ytd=None)
    pri = kit.lookup_input("s2", 12096, 2023, fiscal_period="fy")
    out = compared_year_lengths([cur, pri])
    assert [(e["days_current"], e["days_prior"]) for e in out] == [(364, 371)]


@pytest.mark.criterion("C2")
def test_fourteen_week_quarter(kit):
    # oracle: AAPL 10-Q cover periods: Q1 FY2023 2022-09-25..2022-12-31 = 98 days (14 weeks),
    # Q1 FY2022 2021-09-26..2021-12-25 = 91 days; 7 apart, neither 370-372
    cur = kit.lookup_input("q23", 9001, 2023, fiscal_period="Q1")
    pri = kit.lookup_input("q22", 9002, 2022, fiscal_period="Q1")
    out = compared_year_lengths([cur, pri])
    assert len(out) == 1
    e = out[0]
    assert (e["days_current"], e["days_prior"]) == (98, 91)
    assert tuple(e["current_period"]) == ("2022-09-25", "2022-12-31")
    assert tuple(e["prior_period"]) == ("2021-09-26", "2021-12-25")
    assert "98" in e["note"] and "91" in e["note"] and "53-week" not in e["note"]


_noise = st.fixed_dictionaries({
    "which": st.sampled_from(sorted(_INELIGIBLE) + ["cik", "family", "period", "ytd"]),
    "fy": st.integers(min_value=2015, max_value=2030),
    "days": st.integers(min_value=80, max_value=400),
    "start": _starts,
})


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(noise=st.lists(_noise, max_size=6), slots=st.lists(st.integers(0, 10), max_size=6))
def test_ineligible_or_lone_inputs_never_change_the_result(kit, noise, slots):
    # oracle: Requirement 2.1-2.2: an ineligible input, or a lone input of another group,
    # compares with nothing; the AAPL pair alone is C1's single entry
    base = [kit.lookup_input("s1", 14262, 2024), kit.lookup_input("s2", 12096, 2023)]
    expected = compared_year_lengths(base)
    assert len(expected) == 1
    rows = list(base)
    for i, n in enumerate(noise):
        over = dict(_INELIGIBLE.get(n["which"], {}))
        # each split row gets a group of its own, so it is alone in it
        if n["which"] == "cik":
            over["cik"] = 10_000 + i
        elif n["which"] == "family":
            over["concept_family"] = f"Other{i}"
        elif n["which"] == "period":
            over["fiscal_period"] = f"Q{i}"
        elif n["which"] == "ytd":
            over = {"ytd": True, "concept_family": f"Ytd{i}"}
        row = _synthetic(f"n{i}", 50_000 + i, n["fy"], n["start"], n["days"])
        row.update(over)
        pos = slots[i] % (len(rows) + 1) if i < len(slots) else len(rows)
        rows.insert(pos, row)
    assert compared_year_lengths(rows) == expected
