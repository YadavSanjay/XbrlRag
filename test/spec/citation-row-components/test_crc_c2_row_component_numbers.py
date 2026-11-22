"""C2: a row cited by a component id is cited evidence for the statement's numbers (R2.2)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unmatched_numbers, unresolved_citation_ids


def _unmatched(crc, statement, ids):
    er = crc.execution([crc.debt_screen()])
    return [n["text"] for n in unmatched_numbers(crc.detail(statement, ids), er,
                                                 {"steps": []}, [])]


@pytest.mark.criterion("C2")
def test_value_count_and_rank_match_a_row_cited_by_a_component(crc):
    # oracle: hand arithmetic. ORCL row value 86,869,000,000; "$86.9 billion" has step 1e8, so
    # |86.9e9 - 86.869e9| = 3.1e7 <= 5e7. The screen ranks 6 filers (ranked_count 6) and ORCL
    # is rank 2: both are exact candidates when the row is cited (here by part 71859 only).
    statement = "Oracle's total debt of $86.9 billion placed it 2 of the 6 ranked companies."
    assert _unmatched(crc, statement, ["71859"]) == []


@pytest.mark.criterion("C2")
def test_wrong_value_and_rank_and_uncited_row_stay_unmatched(crc):
    # oracle: hand arithmetic. Cited by part 71859, ORCL's candidates are 86,869,000,000 (step
    # 1e8), rank 2 and the counts {1 exclusion, 3 rows, 6 ranked, 7 members}:
    # $91.2 billion is 4.3e9 away (> 5e7) and 4 is none of them; 6 is the ranked count.
    wrong = "Oracle's total debt was $91.2 billion, 4 of the 6 ranked companies."
    assert _unmatched(crc, wrong, ["71859"]) == ["$91.2 billion", "4"]
    # Citing AAPL's part 14502 makes AAPL's row cited, not ORCL's: ORCL's value is unmatched,
    # AAPL's $106.6 billion (|106.6e9 - 106.629e9| = 2.9e7 <= 5e7) and the ranked count match.
    mixed = "Apple's debt was $106.6 billion and Oracle's was $86.9 billion; 6 were ranked."
    assert _unmatched(crc, mixed, ["14502"]) == ["$86.9 billion"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(ticker=st.sampled_from(["AAPL", "ORCL", "AVGO"]), data=st.data())
def test_any_subset_of_a_rows_parts_cites_the_row(crc, ticker, data):
    # oracle: R2.2. The statement restates the row's value to one decimal in billions (step
    # 1e8, error <= 5e7 by construction), its rank and the ranked count (6); whichever non-empty
    # subset of the row's other parts it cites, nothing is unmatched, and adding the row's own
    # fact_id changes nothing. An id outside every step and row stays unresolved.
    parts = crc.DEBT_PARTS[ticker]
    cited = data.draw(st.lists(st.sampled_from(parts[1:]), min_size=1, unique=True))
    rank = crc.DEBT_ORDER.index(ticker) + 1
    value = crc.DEBT_VALUES[ticker] / 1e9
    statement = f"{ticker} total debt was ${value:.1f} billion, {rank} of the 6 ranked companies."
    assert _unmatched(crc, statement, cited) == []
    assert _unmatched(crc, statement, [parts[0], *cited]) == []
    alien = data.draw(st.integers(min_value=5000, max_value=5999))
    er = crc.execution([crc.debt_screen()])
    assert unresolved_citation_ids(crc.detail(statement, [*cited, alien]), er, []) == [str(alien)]
