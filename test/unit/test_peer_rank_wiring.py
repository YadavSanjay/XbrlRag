"""Unit tests for the peer-rank grading wiring beyond the locked spec.

docs/specs/peer-rank-grading.md requirement 4 adds every answer-row entity as a candidate;
these tests pin the implementer's choices around it: a row named by a candidate's name is
that candidate (not a new one), a row-only entity can own a stated rank, the values
fallback's reason names the rank read, and a hedged answer stating no rank is REFUSED.
Cases are made up; grading is pure, so nothing is faked.
"""

from __future__ import annotations

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2, stated_ranks
from src.eval.core.system import SystemAnswer


def _case() -> EvalCase:
    # DEVB is 2nd of 3 by value: DEVA 30, DEVB 20, DEVC 10.
    return EvalCase.from_dict({
        "id": "p", "question": "Where does DEVB rank?", "kind": "screen",
        "tags": ["shape:peer_relative"],
        "expected_rows": [{"ticker": t, "value": v}
                          for t, v in (("DEVA", 30.0), ("DEVB", 20.0), ("DEVC", 10.0))],
        "disclosures": {"universe": "peers", "members": 3,
                        "ranked": ["DEVA", "DEVB", "DEVC"], "excluded": []},
        "subject": {"ticker": "DEVB", "rank": 2,
                    "names": {"DEVB": ["Devb Holdings"], "DEVA": ["Deva Holdings"]}},
    })


def test_rows_named_by_a_name_rank_by_value():
    rows = [{"entity": "Devb Holdings", "value": 20.0},
            {"entity": "Deva Holdings", "value": 30.0},
            {"entity": "DEVC", "value": 10.0}]
    g = grade_v2(_case(), SystemAnswer(text="The 3 peers are in the rows.", rows=rows,
                                       fact_ids=[1]))
    assert g.grade is Grade.CORRECT
    assert g.checks["rank"] is True


def test_row_only_entity_owns_its_stated_rank():
    # ZZZ is in no case field, only in the rows; "ZZZ ranks 2nd" is ZZZ's, so DEVB's own
    # first stated rank is the 3rd that follows.
    rows = [{"ticker": "ZZZ", "value": 25.0}]
    g = grade_v2(_case(), SystemAnswer(text="ZZZ ranks 2nd of 3. DEVB ranks 3rd of 3.",
                                       rows=rows, fact_ids=[1]))
    assert g.grade is Grade.INCORRECT
    assert "ranked 3" in g.reason


def test_values_fallback_wrong_value_names_it():
    g = grade_v2(_case(), SystemAnswer(text="Among the 3 peers.", numeric_values=[5.0],
                                       fact_ids=[1]))
    assert g.grade is Grade.INCORRECT
    assert g.checks["rank"] is False
    assert "values" in g.reason


def test_hedged_without_rank_is_refused():
    g = grade_v2(_case(), SystemAnswer(text="The rank cannot be determined.", hedged=True))
    assert g.grade is Grade.REFUSED


def test_numbered_rank_across_a_no_dot_split():
    # "No." ends a sentence by the split rule; the expression still starts in DEVB's.
    assert stated_ranks("DEVB is No. 2. DEVA is No. 1.", "DEVB", {"DEVA": []}) == [2]
    assert stated_ranks("DEVB is # 4 overall.", "DEVB", {}) == [4]
