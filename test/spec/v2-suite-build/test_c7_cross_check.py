"""C7: every SEC key is cross-checked against the fact store by accession; agreement
verifies, disagreement is recorded and never replaces the SEC value.

Oracle: requirement 10 of docs/specs/v2-suite-build.md (and sec-keys requirement 9).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import key_intent


def _lookup(t, y, m):
    return Intent("lookup", tickers=[t], fiscal_years=[y], metrics=[m])


def _run(kit, intent, store):
    docs = kit.companyfacts_docs()
    return key_intent(intent, kit.inventory(), kit.universes(), kit.sec(docs), store)


@pytest.mark.criterion("C7")
def test_agreement_verifies_and_adopts_decimals(kit):
    # oracle: the fake store serves A24's own FY2022 revenue (102bn) with decimals -6
    store = kit.store()
    keyed = _run(kit, _lookup("AAA", 2022, "Revenue"), store)
    (k,) = keyed["inputs"]
    assert k["status"] == "verified"
    assert k["verified_by"] == ["fact_store"]
    assert k["decimals"] == -6
    assert k["key_source"] == "sec_companyfacts"
    assert keyed["key_status"] == "verified"
    assert keyed["disagreements"] == []
    # the store is asked for exactly that filing's value
    assert store.calls == [(kit.AAA_CIK, "us-gaap:Revenues", "USD", *kit.P22, kit.A24)]


@pytest.mark.criterion("C7")
def test_disagreement_is_recorded_and_the_sec_value_kept(kit):
    # oracle: override — the store says B24's FY2024 revenue is 219bn; SEC says 220bn
    store = kit.store(overrides={(kit.BBB_CIK, "us-gaap:Revenues", *kit.Q24, kit.B24):
                                 219 * kit.BN})
    keyed = _run(kit, _lookup("BBB", 2024, "Revenue"), store)
    assert keyed["value"] == 220 * kit.BN
    assert keyed["key_status"] == "single-source"
    assert keyed["inputs"][0]["status"] == "single-source"
    (d,) = keyed["disagreements"]
    assert d["reason"] == "value"
    assert d["key_value"] == 220 * kit.BN
    assert d["other_value"] == 219 * kit.BN
    assert d["other_source"] == "fact_store"


@pytest.mark.criterion("C7")
def test_missing_store_value_is_single_source_without_disagreement(kit):
    # oracle: requirement 10 — no store value: nothing to verify against, nothing disagrees
    store = kit.store(missing=((kit.TWX_CIK, "ifrs-full:Revenue", *kit.Q24, kit.T25),))
    keyed = _run(kit, _lookup("TWX", 2024, "Revenue"), store)
    assert keyed["value"] == 2_894_307_699_000
    assert keyed["key_status"] == "single-source"
    assert keyed["disagreements"] == []
    assert keyed["inputs"][0]["decimals"] is None


@pytest.mark.criterion("C7")
def test_one_unverified_input_makes_a_screen_single_source(kit):
    # oracle: requirement 10 — verified only when every input is; AAA's FY2024 revenue is
    # missing from the store, BBB's agrees
    store = kit.store(missing=((kit.AAA_CIK, "us-gaap:Revenues", *kit.P24, kit.A24),))
    intent = Intent("screen", operation="top", n=3, universe="covered", fiscal_years=[2024],
                    metrics=["Revenue"])
    keyed = _run(kit, intent, store)
    assert [k["status"] for k in keyed["inputs"]] == ["verified", "single-source"]
    assert keyed["key_status"] == "single-source"


@pytest.mark.criterion("C7")
@given(delta=st.integers(min_value=-10**11, max_value=10**11))
def test_store_never_changes_the_value(kit, delta):
    # oracle: requirement 10 — the SEC value is never replaced; a store value off by more
    # than 1e-9 relative (220 USD on 220bn) is a value disagreement
    store = kit.store(overrides={(kit.BBB_CIK, "us-gaap:Revenues", *kit.Q24, kit.B24):
                                 220 * kit.BN + delta})
    keyed = _run(kit, _lookup("BBB", 2024, "Revenue"), store)
    assert keyed["value"] == 220 * kit.BN
    if abs(delta) > 1000:
        assert keyed["key_status"] == "single-source"
        assert [d["reason"] for d in keyed["disagreements"]] == ["value"]
    if delta == 0:
        assert keyed["key_status"] == "verified"
