"""C3: a cited screen's counts and cited rows' ranks are number-check candidates (Requirement 3)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.citation_integrity import unmatched_numbers

PLAN = {"steps": []}


def _unmatched(vd, statement, fact_ids, steps=None):
    steps = steps if steps is not None else [vd.wmt_lookup(), vd.ap_screen(), vd.ppe_screen()]
    found = unmatched_numbers(vd.detail(statement, fact_ids), vd.execution(steps), PLAN, [])
    return [n["text"] for n in found]


def _ap_rows(vd):
    return [vd.AP_IDS[t] for t in ("WMT", "AMZN", "COST", "HD")]


@pytest.mark.criterion("C3")
def test_rank_statement_count(vd):
    # oracle: Requirement 3: ap_screen ranked 4 retailers, so "4" is a candidate; 5 is no
    # count, rank or value of it
    rank_4 = ("Walmart ranks 2nd of the 4 retailers in the fiscal 2023 accounts payable "
              "screen, ordered from highest to lowest.")
    assert _unmatched(vd, rank_4, _ap_rows(vd)) == []
    assert _unmatched(vd, rank_4.replace("the 4", "the 5"), _ap_rows(vd)) == ["5"]
    assert _unmatched(vd, rank_4, ["ap_screen"]) == []


@pytest.mark.criterion("C3")
def test_ranked_count_before_top_n(vd):
    # oracle: Requirement 3 with Requirement 1: ppe_screen ranked 23 filers (3 rows returned)
    adbe = vd.PPE_ROWS[0][2]
    assert _unmatched(vd, "ADBE had the lowest net PP&E of the 23 ranked covered companies.",
                      [adbe]) == []
    assert _unmatched(vd, "ADBE had the lowest net PP&E of the 24 ranked covered companies.",
                      [adbe]) == ["24"]
    assert _unmatched(vd, "The screen ranked 23 of 33 members and excluded 10.",
                      ["ppe_screen"]) == []


@pytest.mark.criterion("C3")
def test_cited_rows_rank_only(vd):
    # oracle: Requirement 3: a cited row's rank is a candidate (WMT rank 2); AMZN's rank is 1
    # and ap_screen's counts are 0 and 4, so "2" citing AMZN's row matches nothing
    statement = "Walmart is in position 2 on accounts payable."
    assert _unmatched(vd, statement, [vd.AP_IDS["WMT"]]) == []
    assert _unmatched(vd, statement, [vd.AP_IDS["AMZN"]]) == ["2"]


@pytest.mark.criterion("C3")
def test_uncited_screen_and_percent(vd):
    # oracle: Requirement 3: only cited screens' counts; a count never matches a percent
    statement = "Walmart was compared with 23 ranked companies."
    assert _unmatched(vd, statement, _ap_rows(vd)) == ["23"]
    assert _unmatched(vd, "Walmart's share was 4% of the group.", _ap_rows(vd)) == ["4%"]
    # fact 205130 is both the lookup's citation and WMT's row: citing it cites the screen
    # (Requirement 3), so 4 is a candidate; citing the lookup's step id cites no screen
    assert _unmatched(vd, "Walmart ranks 2nd of the 4 retailers.", [205130],
                      steps=[vd.wmt_lookup(), vd.ap_screen()]) == []
    assert _unmatched(vd, "Walmart ranks 2nd of the 4 retailers.", ["wmt_ap23"],
                      steps=[vd.wmt_lookup(), vd.ap_screen()]) == ["4"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(n=st.integers(1, 999).filter(lambda k: not 1900 <= k <= 2100),
       rows=st.sets(st.sampled_from(["AMZN", "WMT", "COST", "HD"]), min_size=1))
def test_unmatched_exactly_when_not_a_count_or_cited_rank(vd, n, rows):
    # oracle: Requirement 3: candidates are ap_screen's counts {0, 4}, the cited rows' ranks
    # (AMZN 1, WMT 2, COST 3, HD 4) and their values (billions); n is unmatched otherwise
    ranks = {"AMZN": 1, "WMT": 2, "COST": 3, "HD": 4}
    allowed = {0, 4} | {ranks[t] for t in rows}
    statement = f"The screen placed the company at {n} on accounts payable."
    got = _unmatched(vd, statement, [vd.AP_IDS[t] for t in sorted(rows)])
    assert got == ([] if n in allowed else [str(n)])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(first=st.sampled_from(["AMZN", "WMT", "COST", "HD"]),
       more=st.sampled_from(["AMZN", "WMT", "COST", "HD"]),
       n=st.integers(1, 60))
def test_citing_another_row_never_adds_an_unmatched_number(vd, first, more, n):
    # oracle: Requirement 3: more citations only add candidates; 4 (ap_screen's ranked count)
    # is a candidate whichever of its rows is cited
    statement = f"Of the {n} retailers ranked, this one is one of 4 listed."
    one = _unmatched(vd, statement, [vd.AP_IDS[first]])
    two = _unmatched(vd, statement, [vd.AP_IDS[first], vd.AP_IDS[more]])
    assert set(two) <= set(one)
    assert "4" not in one and "4" not in two
