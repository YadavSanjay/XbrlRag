"""C3: a Debt screen ranks every filer by its S4 measure (requirement 3), end to end over the
fake database built from the SEC fixtures."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

M = 1_000_000
# Each filer's borrowing lines on its FY2025 balance sheet (FSDS pre/num), in order.
LINES = {
    "KO": ["NotesAndLoansPayable", "LongTermDebtAndCapitalLeaseObligationsCurrent",
           "LongTermDebtAndCapitalLeaseObligations"],
    "PG": ["DebtCurrent", "LongTermDebtNoncurrent"],
    "QCOM": ["DebtCurrent", "LongTermDebt"],
}
# Hand sums of those lines, $M: KO 1,551 + 1,822 + 42,119; PG 9,513 + 24,995; QCOM 0 + 14,811.
HAND = {"KO": 45_492 * M, "PG": 34_508 * M, "QCOM": 14_811 * M}


@pytest.mark.criterion("C3")
def test_the_covered_debt_screen_ranks_each_filers_sum(kit):
    # oracle: FSDS num of the three FY2025 10-Ks (fixtures), hand-added in HAND; the sums equal
    # the lines read from the fixture here.
    for t, tags in LINES.items():
        assert sum(kit.fsds_value(t, tag) for tag in tags) == pytest.approx(HAND[t], abs=0.5)
    res = kit.run_screen(kit.Store())
    assert [r["ticker"] for r in res.rows] == ["KO", "PG", "QCOM"]
    assert [r["rank"] for r in res.rows] == [1, 2, 3]
    for r in res.rows:
        t = r["ticker"]
        assert r["value"] == pytest.approx(HAND[t], abs=0.5)  # whole dollars from SEC
        parts = [kit.fact_id(t, tag) for tag in LINES[t]]
        assert sorted(r["component_fact_ids"].values()) == sorted(parts)
        assert int(r["fact_id"]) in parts
    assert kit.exclusions(res) == {}
    assert res.disclosure["ranked"] == ["KO", "PG", "QCOM"]


@pytest.mark.criterion("C3")
def test_esop_reserve_is_no_part_and_components_alone_give_the_same_sum(kit):
    # oracle: PG's FSDS balance sheet: "Reserve for ESOP debt retirement" (equity, 672M) is not
    # a borrowing line; 9,513 + 24,995 = 34,508 ($M) with or without the substitute row.
    assert kit.fsds_value("PG", "ReserveForEsopDebtRetirement") == pytest.approx(672 * M, abs=0.5)
    store = kit.Store(tickers=["PG"])
    store.row_filter = lambda r: r["concept_role"] == "component"
    res = kit.run_screen(store)
    assert kit.ranked(res) == {"PG": pytest.approx(34_508 * M, abs=0.5)}  # whole dollars
    assert sorted(res.rows[0]["component_fact_ids"].values()) == sorted(
        kit.fact_id("PG", tag) for tag in LINES["PG"])


_subsets = st.sets(st.sampled_from(["KO", "QCOM"])).map(lambda s: sorted(s | {"PG"}))


@pytest.mark.criterion("C3")
@given(_subsets, st.randoms(use_true_random=False), st.data())
def test_order_other_filers_and_row_subsets_never_change_a_member(kit, tickers, rnd, data):
    # oracle: HAND (FSDS lines hand-added); requirement 3: values, order and members depend
    # on nothing but each filer's own filing.
    store = kit.Store(tickers=tickers)
    rows = store.candidate_rows()
    keep = {t: data.draw(st.sets(st.sampled_from([r["fact_id"] for r in rows
                                                  if r["ticker"] == t]), min_size=1))
            for t in tickers}
    store.row_filter = lambda r: r["fact_id"] in keep[r["ticker"]]
    store.flat_order = lambda rs: rnd.sample(rs, len(rs))
    res = kit.run_screen(store)
    want = sorted(tickers, key=lambda t: -HAND[t])
    assert [r["ticker"] for r in res.rows] == want
    for r in res.rows:
        assert r["value"] == pytest.approx(HAND[r["ticker"]], abs=0.5)  # whole dollars
