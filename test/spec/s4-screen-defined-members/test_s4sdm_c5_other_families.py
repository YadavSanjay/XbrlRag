"""C5: a family without an S4 definition keeps screen-candidate-order R1's picks."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import (
    select_preferred_concept,
    select_screen_picks,
)

OTHER = "LongTermDebt"  # no S4 definition (s4-lookup requirement 1)


@pytest.mark.criterion("C5")
def test_the_same_rows_under_another_family_list_substitutes_apart(kit):
    # oracle: screen-candidate-order R1: KO's and PG's first answerable rows are stated
    # substitutes (listed apart); QCOM ranks on its presented LongTermDebt, 14,811M in FSDS.
    store = kit.Store(family=OTHER)
    rows = store.candidate_rows()
    assert select_screen_picks(rows, OTHER) == select_preferred_concept(rows)
    res = kit.run_screen(store)
    assert kit.ranked(res) == {"QCOM": pytest.approx(kit.fsds_value("QCOM", "LongTermDebt"),
                                                     abs=0.5)}  # whole dollars
    assert kit.exclusions(res) == {"KO": "component_only", "PG": "component_only"}
    assert "component_fact_ids" not in res.rows[0]  # no balance sheet is read


@pytest.mark.criterion("C5")
@given(st.data())
def test_any_rows_rank_as_select_preferred_concept_keeps_them(kit, data):
    # oracle: screen-candidate-order R1 (select_preferred_concept, unchanged) over any subset
    # of the fixture rows.
    store = kit.Store(family=OTHER)
    ids = [r["fact_id"] for r in store.candidate_rows()]
    keep = data.draw(st.sets(st.sampled_from(ids), min_size=1))
    store.row_filter = lambda r: r["fact_id"] in keep
    rows = store.candidate_rows()
    kept, apart = select_preferred_concept(rows)
    assert select_screen_picks(rows, OTHER) == (kept, apart)
    res = kit.run_screen(store)
    assert kit.ranked(res) == {r["ticker"]: pytest.approx(r["value"], abs=0.5)  # same facts
                               for r in kept}
    absent = {"KO", "PG", "QCOM"} - {r["ticker"] for r in rows}  # no row: not tagged
    assert kit.exclusions(res) == {**{c["ticker"]: "component_only" for c in apart},
                                   **{t: "metric_not_tagged" for t in absent}}
