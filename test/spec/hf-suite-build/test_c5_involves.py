"""C5: ``involves_heldout`` and the candidates a held-out build walks.

Oracle: requirement 5 of docs/specs/hf-suite-build.md. Candidate counts are worked out by
hand from the conftest inventory (DEVA..DEVD hold FY2023 and FY2024 with three USD metrics;
DEVE holds FY2024 EUR revenue only) with the v2-suite-build enumeration rules (requirement 7)
and the compound-shape candidate rule (requirement 3), for ``heldout = [DEVD]``:

    lookup             DEVD: 2 years x 3 metrics                               6
    compute.ratio      DEVD: 2 years x (OperatingIncome, NetIncome over Revenue) 4
    yoy / difference   DEVD: FY2023->FY2024 x 3 metrics                        3 / 3
    point_in_time      DEVD: FY2023 in the FY2024 filing x 3 metrics           3
    cagr / trend       need a two-year gap / three years                       0 / 0
    peer_relative      every subject over `covered` (DEVD is a member):
                       DEVA..DEVD 6 each + DEVE 1; none over `pair`            25
    screen.*           `covered`: (2023, 2024) x 3 metrics; `pair` has no DEVD   6 each
    rank_by_ratio      `covered`: 6 (year, metric) pairs x 4 ratio ids        24
    leader_growth      `covered`: FY2024 x 3 ranking metrics x 2 others        6
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.heldout_build import involves_heldout
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import enumerate_intents

EXPECTED_MAIN = {"lookup": 6, "compute.ratio": 4, "screen.top": 6, "screen.bottom": 6,
                 "screen.sum": 6, "screen.median": 6, "peer_relative": 25,
                 "compute.yoy_growth": 3, "compute.difference": 3, "compute.cagr": 0,
                 "trend": 0, "point_in_time": 3}
EXPECTED_COMPOUND = {"compound.rank_by_ratio": 24, "compound.leader_growth": 6}


@pytest.mark.criterion("C5")
def test_hand_made_intents_classified(kit):
    # oracle: requirement 5, by hand.
    uni = kit.universes()
    lookup_d = Intent("lookup", tickers=["DEVD"], fiscal_years=[2024], metrics=["Revenue"])
    lookup_a = Intent("lookup", tickers=["DEVA"], fiscal_years=[2024], metrics=["Revenue"])
    top_cov = Intent("screen", operation="top", universe="covered", fiscal_years=[2024],
                     metrics=["Revenue"], n=3)
    top_pair = Intent("screen", operation="top", universe="pair", fiscal_years=[2024],
                      metrics=["Revenue"], n=3)
    peer_cov = Intent("peer_relative", tickers=["DEVA"], universe="covered",
                      fiscal_years=[2024], metrics=["Revenue"])
    peer_pair = Intent("peer_relative", tickers=["DEVA"], universe="pair",
                       fiscal_years=[2024], metrics=["Revenue"])
    rbr = Intent("compound", operation="rank_by_ratio", universe="covered", n=3,
                 fiscal_years=[2024], metrics=["Revenue", "net_margin"])
    got = [involves_heldout(i, uni, ["DEVD"])
           for i in (lookup_d, lookup_a, top_cov, top_pair, peer_cov, peer_pair, rbr)]
    assert got == [True, False, True, False, True, False, True]
    assert involves_heldout(lookup_d, uni, [" devd "]) is True
    assert involves_heldout(peer_pair, uni, ["DEVC"]) is True
    with pytest.raises(ValueError):
        involves_heldout(Intent("screen", operation="sum", universe="nowhere",
                                fiscal_years=[2024], metrics=["Revenue"]), uni, ["DEVD"])


@pytest.mark.criterion("C5")
def test_candidate_counts_for_one_heldout_filer(kit):
    # oracle: the hand count in the module docstring.
    main = kit.hf_build(["DEVD"], quotas={s: 0 for s in kit.GENERATED_SHAPES})
    assert main["manifest"]["intents"]["candidates"] == EXPECTED_MAIN
    compound = kit.hf_compound(["DEVD"], quotas={s: 0 for s in kit.COMPOUND_SHAPES})
    assert compound["manifest"]["intents"]["candidates"] == EXPECTED_COMPOUND


@pytest.mark.criterion("C5")
@settings(max_examples=12, deadline=None)
@given(heldout=st.sets(st.sampled_from(("DEVA", "DEVB", "DEVC", "DEVD", "DEVE")),
                       min_size=1))
def test_counts_equal_the_restated_filter(kit, heldout):
    # oracle: requirement 5 restated in conftest (oracle_involves) over the existing
    # enumeration; the build's candidate counts must match it for any held-out set.
    uni = kit.universes()
    every = enumerate_intents(kit.inventory(), uni)
    want = {s: sum(kit.oracle_involves(i, uni, heldout) for i in every[s]) for s in every}
    build = kit.hf_build(sorted(heldout), quotas={s: 0 for s in kit.GENERATED_SHAPES})
    assert build["manifest"]["intents"]["candidates"] == want
    assert sum(want.values()) > 0
