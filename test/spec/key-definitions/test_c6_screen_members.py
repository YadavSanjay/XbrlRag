"""C6: screens rank defined keys, and never rank a consolidated figure beside parents'.

Oracle: requirements 4 and 6 of docs/specs/key-definitions.md; the hand sums of C1 (spec
Notes, "Hand checks"); hand ranking.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

SAME = 1e-9  # filed values; only float rounding may differ

# oracle: the C1/C2 hand sums of the FY2025 balance sheets, ranked by hand (descending)
FY2025_DEBT = [("AAPL", "2025-09-27", 98_657e6), ("AMZN", "2025-12-31", 65_648e6),
               ("GOOGL", "2025-12-31", 46_547e6), ("KO", "2025-12-31", 45_492e6),
               ("LLY", "2025-12-31", 42_503e6), ("MCD", "2025-12-31", 39_973e6),
               ("TSLA", "2025-12-31", 8_376e6)]


@pytest.mark.criterion("C6")
def test_debt_screen_ranks_every_defined_member(kit):
    # oracle: hand ranking of the FY2025 hand sums above
    from src.eval.keys.key_definitions import member_status

    members = {}
    for ticker, end, _v in reversed(FY2025_DEBT):
        m = member_status(kit.debt(ticker, end, 2025))
        assert (m["status"], m["reason"], m["concept"]) == ("ranked", None, "s4:debt_total")
        members[ticker] = m["value"]
    ranked = sorted(members, key=lambda t: (-members[t], t))
    assert ranked == [t for t, _e, _v in FY2025_DEBT]
    assert [members[t] for t in ranked] == [pytest.approx(v, rel=SAME) for *_x, v in FY2025_DEBT]


@pytest.mark.criterion("C6")
def test_held_untagged_and_blocking_members(kit):
    # oracle: requirement 6 — no_fsds is held, not_presented is metric_not_tagged, a
    # component mismatch stops the build (WitnessBlocked)
    from src.eval.keys.key_definitions import debt_key, member_status
    from src.eval.keys.statement_witness import WitnessBlocked

    e = kit.env()
    gone = debt_key(e.tables, e.rules, kit.companyfacts("MCD"), "0000063908", 2025, kit.ABSENT,
                    "2025-12-31", "USD")
    assert member_status(gone) == {"status": "held", "reason": "no_fsds", "concept": None,
                                   "value": None}
    t = kit.tables_copy()
    kit.drop_lines(t, kit.FILINGS["MCD"][1], {"LongTermDebtNoncurrent"})
    m = member_status(kit.debt("MCD", "2025-12-31", 2025, tables=t))
    assert (m["status"], m["value"]) == ("metric_not_tagged", None)
    cf = kit.copy(kit.companyfacts("MCD"))
    for x in kit.cf_entries(cf, "LongTermDebtNoncurrent"):
        x["val"] = x["val"] + 1_000_000
    with pytest.raises(WitnessBlocked):
        member_status(kit.debt("MCD", "2025-12-31", 2025, cf=cf))


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(("status", "expected"), [
    # oracle: requirement 4 — a consolidated figure with a different parent figure is a
    # stated substitute, never ranked
    ("includes_noncontrolling_interests",
     {"status": "definition_excluded", "reason": "substitute:includes_noncontrolling_interests",
      "concept": "us-gaap:ProfitLoss", "value": 1_675e6}),
    # oracle: requirement 4 — the parent's figure, or a consolidated one standing for it
    ("parent", {"status": "ranked", "reason": None, "concept": "us-gaap:ProfitLoss",
                "value": 1_675e6}),
    ("consolidated_is_parent", {"status": "ranked", "reason": None,
                                "concept": "us-gaap:ProfitLoss", "value": 1_675e6}),
])
def test_income_members_follow_attribution(status, expected):
    # oracle: requirement 6 — INTC 2023 "Net income (loss)" 1,675 as the member's figure
    from src.eval.keys.key_definitions import attribution_member

    assert attribution_member(status, "us-gaap:ProfitLoss", 1_675e6) == expected


@pytest.mark.criterion("C6")
@settings(max_examples=20, deadline=None)
@given(order=st.permutations([t for t, _e, _v in FY2025_DEBT]))
def test_member_values_do_not_depend_on_order(kit, order):
    # oracle: each ranked member's value is its own hand sum, whatever order members are built
    from src.eval.keys.key_definitions import member_status

    want = {t: v for t, _e, v in FY2025_DEBT}
    ends = {t: e for t, e, _v in FY2025_DEBT}
    for t in order:
        assert member_status(kit.debt(t, ends[t], 2025))["value"] == pytest.approx(
            want[t], rel=SAME)
