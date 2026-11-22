"""C8: in held-out mode a witness block leaves its candidate out; unused resolutions are counted.

Oracle: requirements 6.3-6.4 and 7 of docs/specs/hf-suite-build.md, applied by hand to the
statement-witness fixture under ``witness/`` (see its README.txt): DEVB's two 10-Ks present
no net income line, so its FY2023 and FY2024 NetIncomeLoss keys are ``note_unlisted``
(blocked); every other key agrees with its statement line. The witness is a real
``KeyWitness``; the only fakes are the conftest's I/O seams.

With ``heldout = [DEVB]`` and lookups only, the six candidates are DEVB's FY2023 and FY2024
revenue, operating income and net income: four keyed, the two net income ones blocked.
Compound, ``leader_growth`` over ``covered`` (compound-shape C10's table, universe ``big``
there, the same five filers): (Revenue, OperatingIncome), (Revenue, NetIncome) and
(OperatingIncome, Revenue) kept; (OperatingIncome, NetIncome), (NetIncome, Revenue) and
(NetIncome, OperatingIncome) meet a DEVB net income key: blocked. ``pair`` lists no DEVB.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.keys.statement_witness import WitnessBlocked

LOOKUPS = {"lookup": 100}


def _ni_id(kit, year: int) -> str:
    return kit.oracle_id({"kind": "lookup", "tickers": ["DEVB"], "fiscal_years": [year],
                          "metrics": ["NetIncome"]})


def _sec(kit):
    return kit.pinned_sec(kit.companyfacts_docs())


@pytest.mark.criterion("C8")
def test_blocked_keys_dropped_and_never_reported(kit):
    # oracle: the hand count in the module docstring.
    w = kit.witness("none")
    build = kit.hf_build(["DEVB"], quotas=LOOKUPS, wit=w, sec=_sec(kit))
    intents = build["manifest"]["intents"]
    assert intents["candidates"]["lookup"] == 6
    assert intents["selected"]["lookup"] == 4
    assert intents["dropped_by_shape"] == {"lookup": {"witness_blocked": 2}}
    tags = {t for c in build["heldout_filers"]["cases"] for t in c["tags"]}
    assert f"intent:{_ni_id(kit, 2023)}" not in tags
    assert f"intent:{_ni_id(kit, 2024)}" not in tags
    w.raise_if_blocked()  # the held-out build's blocks never reach the report
    with pytest.raises(WitnessBlocked):
        kit.dev_build(quotas=LOOKUPS, wit=kit.witness("none"), sec=_sec(kit))


@pytest.mark.criterion("C8")
def test_resolutions_used_and_unused(kit):
    # oracle: requirement 6.4 with the fixture's two "key" resolutions (both original, no
    # review record): used by a DEVB build (values 40e6 and 48e6, the table's), unused by a
    # DEVD build, which must not raise.
    build = kit.hf_build(["DEVB"], quotas=LOOKUPS, wit=kit.witness("key"), sec=_sec(kit))
    assert build["manifest"]["intents"]["selected"]["lookup"] == 6
    for year, value in ((2023, 40e6), (2024, 48e6)):
        found = [c for c in build["heldout_filers"]["cases"]
                 if f"intent:{_ni_id(kit, year)}" in c["tags"]]
        assert found
        assert all(c["expected_value"] == pytest.approx(value, rel=1e-12) for c in found)
    used = build["manifest"]["key_resolutions"]
    assert (used["n"], used["original"], used["reviewed"], used["unused"]) == (2, 2, 0, 0)

    other = kit.hf_build(["DEVD"], quotas=LOOKUPS, wit=kit.witness("key"), sec=_sec(kit))
    unused = other["manifest"]["key_resolutions"]
    assert (unused["n"], unused["original"], unused["reviewed"], unused["unused"]) == (0, 0, 0, 2)
    assert unused["by_bucket"] and set(unused["by_bucket"].values()) == {0}
    assert other["manifest"]["intents"]["selected"]["lookup"] == 6


@pytest.mark.criterion("C8")
def test_compound_blocks_dropped_on_a_shared_witness(kit):
    # oracle: the compound table in the module docstring.
    w = kit.witness("none")
    sec = _sec(kit)
    main = kit.hf_build(["DEVB"], quotas=LOOKUPS, wit=w, sec=sec)
    compound = kit.hf_compound(["DEVB"], quotas={"compound.leader_growth": 100}, wit=w, sec=sec)
    intents = compound["manifest"]["intents"]
    assert intents["candidates"]["compound.leader_growth"] == 6
    assert intents["selected"]["compound.leader_growth"] == 3
    assert intents["dropped_by_shape"] == {"compound.leader_growth": {"witness_blocked": 3}}
    assert main["manifest"]["intents"]["selected"]["lookup"] == 4
    w.raise_if_blocked()


@pytest.mark.criterion("C8")
@settings(max_examples=9, deadline=None)
@given(quota=st.integers(min_value=0, max_value=8))
def test_any_quota_walks_the_keyable_candidates(kit, quota):
    # oracle: requirement 6.2-6.3: four of DEVB's six lookups are keyable, so the build
    # selects min(quota, 4); every candidate tried is selected or dropped, and all six are
    # tried whenever the quota is not met.
    build = kit.hf_build(["DEVB"], quotas={"lookup": quota}, wit=kit.witness("none"),
                         sec=_sec(kit))
    intents = build["manifest"]["intents"]
    selected = intents["selected"]["lookup"]
    dropped = sum(intents["dropped_by_shape"].get("lookup", {}).values())
    assert selected == min(quota, 4)
    assert selected + dropped <= 6
    if selected < quota:
        assert selected + dropped == 6
    assert set(intents["dropped_by_shape"].get("lookup", {})) <= {"witness_blocked"}
