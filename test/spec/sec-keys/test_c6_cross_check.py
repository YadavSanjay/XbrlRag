"""C6: ``cross_check`` verifies a key against another source or records the disagreement.

Oracle: requirement 9 of docs/specs/sec-keys.md. The keys are written out by hand (the SEC
key is the fixture's FY2024 revenue of the September filer).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import cross_check

DISAGREEMENT_FIELDS = {
    "reason", "concept", "unit", "period_start", "period_end", "key_source", "key_value",
    "key_accession", "other_source", "other_value", "other_unit", "other_accession",
}


def _sec_key(value=360_000_000_000, decimals=None):
    return {
        "value": value, "unit": "USD", "decimals": decimals, "concept": "us-gaap:Revenues",
        "cik": "0000900001", "period_start": "2023-10-01", "period_end": "2024-09-28",
        "fiscal_year": 2024, "calendar_year": None, "accession": "0000900001-24-000030",
        "filed": "2024-11-01", "form": "10-K", "key_source": "sec_companyfacts",
        "selection": "latest_filed", "policies": ["S1", "S2", "S6"],
        "status": "single-source", "verified_by": [],
    }


def _store_key(value=360_000_000_000, decimals=-6, **over):
    key = {
        "value": value, "unit": "USD", "decimals": decimals, "concept": "us-gaap:Revenues",
        "cik": "0000900001", "period_start": "2023-10-01", "period_end": "2024-09-28",
        "fiscal_year": 2024, "calendar_year": None, "accession": "0000900001-24-000030",
        "filed": None, "form": None, "key_source": "fact_store", "selection": "latest_filed",
        "policies": ["S6"], "status": "single-source", "verified_by": [],
    }
    key.update(over)
    return key


@pytest.mark.criterion("C6")
def test_agreement_verifies_and_adopts_decimals(kit):
    # oracle: requirement 9 — equal values: verified, verified_by ["fact_store"], decimals
    # taken from the store (-6) because the SEC key's is None; value and provenance kept
    res = cross_check(_sec_key(), _store_key())
    assert res["disagreement"] is None
    key = res["key"]
    assert key["status"] == "verified"
    assert key["verified_by"] == ["fact_store"]
    assert key["decimals"] == -6
    assert key["value"] == 360_000_000_000
    assert key["key_source"] == "sec_companyfacts"
    assert key["accession"] == "0000900001-24-000030"


@pytest.mark.criterion("C6")
def test_inf_decimals_stay_inf(kit):
    # oracle: S6 — INF decimals are kept as the string "INF", never 0 or a number
    res = cross_check(_sec_key(), _store_key(decimals="INF"))
    assert res["key"]["decimals"] == "INF"
    assert res["key"]["status"] == "verified"


@pytest.mark.criterion("C6")
def test_value_disagreement_is_recorded_not_overwritten(kit):
    # oracle: requirement 9 — 360.0bn vs 359.0bn differ by ~0.28%, far above 1e-9: reason
    # "value"; the returned key is the input key unchanged (value 360.0bn, single-source)
    sec, store = _sec_key(), _store_key(value=359_000_000_000, accession="0000900001-24-000099")
    sec_before, store_before = copy.deepcopy(sec), copy.deepcopy(store)
    res = cross_check(sec, store)
    assert res["key"] == sec_before
    d = res["disagreement"]
    assert set(d) == DISAGREEMENT_FIELDS
    assert d["reason"] == "value"
    assert d["key_value"] == 360_000_000_000
    assert d["other_value"] == 359_000_000_000
    assert d["key_source"] == "sec_companyfacts"
    assert d["other_source"] == "fact_store"
    assert d["key_accession"] == "0000900001-24-000030"
    assert d["other_accession"] == "0000900001-24-000099"
    assert d["other_unit"] == "USD"
    assert (d["concept"], d["unit"]) == ("us-gaap:Revenues", "USD")
    assert (d["period_start"], d["period_end"]) == ("2023-10-01", "2024-09-28")
    assert sec == sec_before and store == store_before  # inputs not mutated


@pytest.mark.criterion("C6")
def test_each_other_reason(kit):
    # oracle: requirement 9 — reasons in order nil, concept, unit, period; a nil store value
    # is "nil", not a comparison against 0 (and 0 would disagree with 360bn as "value")
    cases = [
        (_store_key(value=None), "nil"),
        (_store_key(concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
         "concept"),
        (_store_key(unit="EUR"), "unit"),
        (_store_key(period_start="2022-09-25", period_end="2023-09-30"), "period"),
        (_store_key(value=None, unit="EUR"), "nil"),
        (_store_key(unit="EUR", period_end="2023-09-30"), "unit"),
    ]
    for other, reason in cases:
        res = cross_check(_sec_key(), other)
        assert res["disagreement"]["reason"] == reason
        assert res["key"]["status"] == "single-source"
        assert res["key"]["value"] == 360_000_000_000
    zero = cross_check(_sec_key(), _store_key(value=0))
    assert zero["disagreement"]["reason"] == "value"
    assert zero["disagreement"]["other_value"] == 0


@pytest.mark.criterion("C6")
def test_same_or_unknown_source_is_refused(kit):
    # oracle: requirement 9 — two keys from one source are not independent
    with pytest.raises(ValueError):
        cross_check(_sec_key(), _sec_key())
    with pytest.raises(ValueError):
        cross_check(_sec_key(), _store_key(key_source="analyst_guess"))


@pytest.mark.criterion("C6")
@given(rel_tol=st.sampled_from([1e-9, 5e-3]),
       frac=st.floats(min_value=0.0, max_value=0.5),
       far=st.floats(min_value=2.0, max_value=100.0),
       sign=st.sampled_from([-1, 1]))
def test_tolerance_boundary(kit, rel_tol, frac, far, sign):
    # oracle: requirement 9 — |a-b| <= rel_tol*max(|a|,|b|) verifies. A relative perturbation
    # of at most rel_tol/2 is inside it; one of at least 2*rel_tol is outside (both with
    # a margin far above float rounding at 3.6e11)
    base = 360_000_000_000
    near = base * (1 + sign * frac * rel_tol)
    away = base * (1 + sign * far * rel_tol)
    ok = cross_check(_sec_key(), _store_key(value=near), rel_tol=rel_tol)
    assert ok["disagreement"] is None
    assert ok["key"]["status"] == "verified"
    bad = cross_check(_sec_key(), _store_key(value=away), rel_tol=rel_tol)
    assert bad["disagreement"]["reason"] == "value"
    assert bad["key"]["status"] == "single-source"
