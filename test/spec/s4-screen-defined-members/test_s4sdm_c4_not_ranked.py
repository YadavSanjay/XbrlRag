"""C4: a defined-family member is ranked only by its measure; otherwise it is named with the
reason the S4 member status gives (requirements 3 and 4)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_disclosure import screen_disclosure_text

M = 1_000_000
LINES = {
    "KO": {"NotesAndLoansPayable", "LongTermDebtAndCapitalLeaseObligationsCurrent",
           "LongTermDebtAndCapitalLeaseObligations"},
    "PG": {"DebtCurrent", "LongTermDebtNoncurrent"},
    "QCOM": {"DebtCurrent", "LongTermDebt"},
}
HAND = {"KO": 45_492 * M, "PG": 34_508 * M, "QCOM": 14_811 * M}  # FSDS lines added by hand


@pytest.mark.criterion("C4")
def test_no_presented_line_is_metric_not_tagged(kit):
    # oracle: s4-lookup requirement 5.1 (no selected line: not_presented) and 10
    # (screen_member: metric_not_tagged, never ranked by a fallback); key-definitions R6.
    store = kit.Store()
    store.drop_lines("PG", LINES["PG"])
    res = kit.run_screen(store)
    assert kit.ranked(res) == {"KO": pytest.approx(HAND["KO"], abs=0.5),  # whole dollars
                               "QCOM": pytest.approx(HAND["QCOM"], abs=0.5)}
    assert kit.exclusions(res) == {"PG": "metric_not_tagged"}
    assert "PG (metric not reported)" in screen_disclosure_text(res.disclosure)


@pytest.mark.criterion("C4")
def test_a_line_in_another_unit_is_unit_mismatch(kit):
    # oracle: s4-lookup requirement 3 (a selected line in another unit: unit_mismatch) and 10
    # (excluded with that reason).
    store = kit.Store()
    store.unit_override[("KO", "NotesAndLoansPayable")] = "EUR"
    res = kit.run_screen(store)
    assert kit.exclusions(res) == {"KO": "unit_mismatch"}
    assert kit.ranked(res) == {"PG": pytest.approx(HAND["PG"], abs=0.5),  # whole dollars
                               "QCOM": pytest.approx(HAND["QCOM"], abs=0.5)}


@pytest.mark.criterion("C4")
def test_no_filing_to_read_is_component_only_and_an_older_year_is_not_filed(kit):
    # oracle: requirement 3: a non-primary pick that reaches no measure is listed
    # component_only, never ranked on its own row; an earlier latest year is period_not_filed.
    store = kit.Store()
    store.no_accession.add("PG")
    res = kit.run_screen(store)
    assert "PG" not in kit.ranked(res)
    assert kit.exclusions(res) == {"PG": "component_only"}
    older = kit.Store()
    older.fiscal_year["PG"] = 2024
    res = kit.run_screen(older)
    assert kit.exclusions(res) == {"PG": "period_not_filed"}
    assert set(kit.ranked(res)) == {"KO", "QCOM"}


@pytest.mark.criterion("C4")
@given(st.sets(st.sampled_from(["KO", "PG", "QCOM"])))
def test_exactly_the_filers_without_lines_are_not_ranked(kit, dropped):
    # oracle: requirement 3; HAND for the rest (FSDS lines hand-added).
    store = kit.Store()
    for t in dropped:
        store.drop_lines(t, LINES[t])
    res = kit.run_screen(store)
    assert kit.exclusions(res) == {t: "metric_not_tagged" for t in dropped}
    assert set(kit.ranked(res)) == {"KO", "PG", "QCOM"} - dropped
    for t, v in kit.ranked(res).items():
        assert v == pytest.approx(HAND[t], abs=0.5)  # whole dollars
