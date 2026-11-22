"""C3: a fallback-only primary never becomes a filer's facts-count rank 1.

docs/specs/screen-candidate-order.md, requirement 3. The reload's facts-count pass makes the
QName a filer reports in the most fiscal years its rank 1. Short-term debt proceeds and cash
including restricted cash are on most filers' cash-flow statements in every year, so the pass
would put them first and answer Costco's and Vistra's debt issued with their short-term lines
and Intel's FY2025 cash with 14,712M instead of 14,265M. ``facts_count_ranking`` leaves them
out of the choice; they keep their global rank. Pure.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sco_support import tags_by_year

from src.ingest.sec_edgar.ingest.layer0_populator import facts_count_ranking
from src.ingest.util.generate_concept_mapping import load_family_coverage

U = "us-gaap:"
SHORT = U + "ProceedsFromShortTermDebt"
CASH_R = U + "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"

# The families' generic rankings after the change (requirement 2; the IFRS row at 100 and
# the components at 50+ as committed), written out by hand.
DEBT_ISSUED = [(U + q, n) for n, q in enumerate([
    "ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromLongTermLinesOfCredit",
    "ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromIssuanceOfDebt",
    "ProceedsFromIssuanceOfLongTermDebtAndCapitalSecuritiesNet",
    "ProceedsFromDebtMaturingInMoreThanThreeMonths", "ProceedsFromIssuanceOfMediumTermNotes",
    "ProceedsFromIssuanceOfSeniorLongTermDebt", "ProceedsFromShortTermDebt"], start=1)] + [
    ("ifrs-full:ProceedsFromIssueOfBondsNotesAndDebentures", 50),
    ("ifrs-full:ProceedsFromBorrowingsClassifiedAsFinancingActivities", 100)]
CASH = [(U + q, n) for n, q in enumerate([
    "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments",
    "CashAndCashEquivalentsAndShortTermInvestments", CASH_R[len(U):]], start=1)] + [
    ("ifrs-full:CashAndCashEquivalents", 100)]
FALLBACK = {"DebtIssued": {SHORT}, "CashAndEquivalents": {CASH_R}}


def _years(ticker: str) -> dict[str, set[int]]:
    return {U + tag: years for tag, years in tags_by_year(ticker).items()}


# --------------------------------------------------------------------------- points
@pytest.mark.criterion("C3")
def test_the_coverage_file_declares_the_two_fallback_primaries():
    # oracle: requirement 3 (the maintainer's decisions of 2026-11-12)
    fallback = load_family_coverage().get("fallback", {})
    assert {f: set(q) for f, q in fallback.items()} == FALLBACK


@pytest.mark.criterion("C3")
def test_fallback_primaries_never_win_the_facts_count_pass():
    # oracle: companyfacts years of the fixture 10-Ks, hand-counted:
    # INTC cash: plain {2025}, inclusive {2022..2025}; PG: inclusive {2023..2026} only;
    # COST debt issued: long-term {2024, 2025}, short-term {2023..2025};
    # VST: long-term {2022, 2023}, ProceedsFromIssuanceOfDebt {2024, 2025}, short-term
    # {2022..2025}, senior {2023}; AMD: short-term {2022..2025} only. Leaving the fallback
    # QName out, no filer needs an override: the global ranking stands (None).
    assert facts_count_ranking(CASH, _years("INTC"), FALLBACK["CashAndEquivalents"]) is None
    assert facts_count_ranking(CASH, _years("PG"), FALLBACK["CashAndEquivalents"]) is None
    for ticker in ("COST", "VST", "AMD"):
        assert facts_count_ranking(DEBT_ISSUED, _years(ticker), FALLBACK["DebtIssued"]) is None


@pytest.mark.criterion("C3")
def test_an_ordinary_primary_is_still_promoted():
    # oracle: companyfacts years: ORCL ProceedsFromDebtNetOfIssuanceCosts {2022}, senior notes
    # {2023..2026}: senior notes are its rank 1, the rest follow in global order (the facts-
    # count rule of docs/specs/filer-rank-coverage.md, unchanged for non-fallback QNames).
    got = facts_count_ranking(DEBT_ISSUED, _years("ORCL"), FALLBACK["DebtIssued"])
    senior = U + "ProceedsFromIssuanceOfSeniorLongTermDebt"
    want = [(senior, 1)] + [(q, n) for n, q in enumerate(
        [q for q, r in DEBT_ISSUED if r < 50 and q != senior], start=2)] + [
        ("ifrs-full:ProceedsFromBorrowingsClassifiedAsFinancingActivities", 10),
        ("ifrs-full:ProceedsFromIssueOfBondsNotesAndDebentures", 50)]
    assert got == want


# --------------------------------------------------------------------------- properties
def _definition(ranking, years, fallback):
    """The facts-count rule written out: the eligible (not 50-99, not fallback) QNames the
    filer reports in the most years; the first of them in global order wins; None when that
    is the global first eligible QName or nothing is reported."""
    ordered = sorted(ranking, key=lambda qr: (qr[1], qr[0]))
    eligible = [q for q, r in ordered if not 50 <= r < 100]
    choosable = [q for q in eligible if q not in fallback]
    counts = {q: len(years.get(q, ())) for q in choosable}
    best = max(counts.values(), default=0)
    if best == 0:
        return None
    winner = next(q for q in choosable if counts[q] == best)
    if winner == eligible[0]:
        return None
    rest = [q for q in eligible if q != winner]
    return ([(winner, 1)] + [(q, n) for n, q in enumerate(rest, start=2)]
            + [(q, r) for q, r in ordered if 50 <= r < 100])


@pytest.mark.criterion("C3")
@given(st.sampled_from([DEBT_ISSUED, CASH]),
       st.dictionaries(st.sampled_from([q for q, _ in DEBT_ISSUED + CASH]),
                       st.sets(st.integers(2022, 2026), max_size=5), max_size=8),
       st.booleans())
def test_the_winner_is_never_a_fallback_and_follows_the_facts_count_rule(ranking, years,
                                                                          with_fallback):
    # oracle: the definition above (requirement 3); with no fallback QNames it is today's
    # facts-count rule.
    fallback = (FALLBACK["DebtIssued"] | FALLBACK["CashAndEquivalents"]) if with_fallback else set()
    got = facts_count_ranking(ranking, years, fallback)
    assert got == _definition(ranking, years, fallback)
    if got is not None:
        assert got[0][0] not in fallback
        assert sorted(got) != [] and {q for q, _ in got} == {q for q, _ in ranking}
