"""C6: the screen's members are the key side's (key-definitions requirement 6), metamorphic:
``member_status(debt_key(...))`` over the same SEC filings is the oracle."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

M = 1_000_000
LINES = {
    "KO": {"NotesAndLoansPayable", "LongTermDebtAndCapitalLeaseObligationsCurrent",
           "LongTermDebtAndCapitalLeaseObligations"},
    "PG": {"DebtCurrent", "LongTermDebtNoncurrent"},
    "QCOM": {"DebtCurrent", "LongTermDebt"},
}


@pytest.mark.criterion("C6")
def test_each_member_is_the_keys(kit):
    # oracle: the key side on the fixture FSDS tables and companyfacts (debt_key, member_status)
    # and the hand sums KO 45,492M, PG 34,508M, QCOM 14,811M of the FSDS lines.
    hand = {"KO": 45_492 * M, "PG": 34_508 * M, "QCOM": 14_811 * M}
    res = kit.run_screen(kit.Store())
    got = kit.ranked(res)
    for t in kit.FILERS:
        key = kit.key_member(t)
        assert key["status"] == "ranked"
        assert key["value"] == pytest.approx(hand[t], abs=0.5)  # whole dollars
        assert t in got
        assert got[t] == pytest.approx(key["value"], abs=0.5)  # same facts


@pytest.mark.criterion("C6")
@given(st.sets(st.sampled_from(["KO", "QCOM"])).map(lambda s: sorted(s | {"PG"})),
       st.sets(st.sampled_from(["KO", "PG", "QCOM"])), st.data())
def test_any_filers_lines_and_rows_give_the_keys_members(kit, tickers, dropped, data):
    # oracle: member_status(debt_key(...)) on the same edited FSDS tables (metamorphic).
    store = kit.Store(tickers=tickers)
    for t in dropped:
        store.drop_lines(t, LINES[t])
    ids = {t: [r["fact_id"] for r in store.candidate_rows() if r["ticker"] == t]
           for t in tickers}
    keep = {t: data.draw(st.sets(st.sampled_from(ids[t]), min_size=1)) for t in tickers}
    store.row_filter = lambda r: r["fact_id"] in keep[r["ticker"]]
    res = kit.run_screen(store)
    keys = {t: kit.key_member(t, store.tables) for t in tickers}
    assert kit.ranked(res) == {t: pytest.approx(k["value"], abs=0.5)  # same facts
                               for t, k in keys.items() if k["status"] == "ranked"}
    assert kit.exclusions(res) == {t: "metric_not_tagged" for t, k in keys.items()
                                   if k["status"] == "metric_not_tagged"}
    assert all(k["status"] in ("ranked", "metric_not_tagged") for k in keys.values())
