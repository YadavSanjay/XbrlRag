"""C5: grading a peer-relative case (docs/specs/peer-rank-grading.md, requirement 4).

The Walmart case's key rank is 2 (AMZN 84.981bn > WMT 53.742bn > COST > HD). ``WMT_TEXT``
states "ranks 2nd of the 4 retailers" and every period end, so every disclosure check
passes and the rank alone decides. Expected grades are derived by hand from requirement 4.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.graders.base import Grade
from src.eval.core.graders.v2 import grade_v2

FACTS = [101, 102, 103, 104]
NO_RANK_TEXT = ("Accounts payable of the 4 retailers for fiscal 2023 are listed in the "
                "rows. ")


def _no_rank_text(kit):
    return NO_RANK_TEXT + kit.PERIOD_ENDS


@pytest.mark.criterion("C5")
def test_c3_class_subject_listed_first_is_correct(kit):
    # oracle: requirement 4 — stated "2nd" equals the key's 2; rows in statement order
    # (WMT first) no longer decide
    g = grade_v2(kit.wmt_case(), kit.answer(kit.WMT_TEXT, kit.rows_in(kit.STATEMENT_ORDER),
                                            fact_ids=FACTS))
    assert g.grade is Grade.CORRECT and g.passed is True
    assert g.checks["rank"] is True
    assert g.matched_value == pytest.approx(2.0, abs=0.0)  # a rank: exact
    assert {k: v for k, v in g.checks.items() if k != "rank"} == {
        "universe": True, "exclusions": True, "period_ends": True}


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("order", ["alphabetical", "none"])
def test_alphabetical_rows_or_no_rows_is_correct(kit, order):
    # oracle: requirement 4.1 — the stated rank decides whatever the rows
    rows = kit.rows_in(kit.ALPHABETICAL) if order == "alphabetical" else []
    g = grade_v2(kit.wmt_case(), kit.answer(kit.WMT_TEXT, rows, fact_ids=FACTS))
    assert g.grade is Grade.CORRECT
    assert g.checks["rank"] is True


@pytest.mark.criterion("C5")
def test_wrong_stated_rank_beats_right_rows(kit):
    # oracle: requirement 4 "Precedence" — "3rd" is stated; the rows by value would put WMT
    # 2nd, but the stated rank decides: INCORRECT. A case with no subject is INCORRECT too.
    text = kit.WMT_TEXT.replace("ranks 2nd", "ranks 3rd").replace(
        "Walmart second, Costco third", "Costco second, Walmart third")
    g = grade_v2(kit.wmt_case(), kit.answer(text, kit.rows_in(["AMZN", "WMT", "COST", "HD"]),
                                            fact_ids=FACTS))
    assert g.grade is Grade.INCORRECT
    assert g.checks["rank"] is False
    assert "WMT" in g.reason

    no_subject = grade_v2(kit.wmt_case(subject=None),
                          kit.answer(kit.WMT_TEXT, [], fact_ids=FACTS))
    assert no_subject.grade is Grade.INCORRECT
    assert no_subject.checks["rank"] is False


@pytest.mark.criterion("C5")
def test_no_stated_rank_complete_rows_decide_by_value(kit):
    # oracle: requirement 4.2 — no rank expression; four valued rows (= the 4 ranked) put WMT
    # 2nd by value although it is listed first
    g = grade_v2(kit.wmt_case(), kit.answer(_no_rank_text(kit),
                                            kit.rows_in(["WMT", "HD", "AMZN", "COST"]),
                                            fact_ids=FACTS))
    assert g.grade is Grade.CORRECT
    assert g.checks["rank"] is True


@pytest.mark.criterion("C5")
def test_partial_rows_fall_to_values_or_nothing(kit):
    # oracle: requirement 4.3-4.4 — two rows of four imply no rank; a structured value 2
    # equals the key's rank (CORRECT); without it there is no rank (INCORRECT), and a
    # refusal stating none is REFUSED
    partial = kit.rows_in(["WMT", "HD"])
    by_value = grade_v2(kit.wmt_case(), kit.answer(_no_rank_text(kit), partial,
                                                   values=[2.0], fact_ids=FACTS))
    assert by_value.grade is Grade.CORRECT

    nothing = grade_v2(kit.wmt_case(), kit.answer(_no_rank_text(kit), partial,
                                                  fact_ids=FACTS))
    assert nothing.grade is Grade.INCORRECT
    assert nothing.checks["rank"] is False

    refused = grade_v2(kit.wmt_case(), kit.answer(
        "The filing does not contain data for the other retailers, so the rank cannot be "
        "determined.", refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C5")
def test_unchanged_downgrades_with_the_new_reading(kit):
    # oracle: requirement 4 "The grade" (unchanged) — a right rank silent on the universe is
    # PARTIAL with only the disclosure checks failing (no count, no member list, no period
    # end; nothing is excluded); a right rank with no fact_ids on a citation-required case
    # is PARTIAL
    silent = grade_v2(kit.wmt_case(), kit.answer(
        "Walmart ranks 2nd.", kit.rows_in(kit.STATEMENT_ORDER), fact_ids=FACTS))
    assert silent.grade is Grade.PARTIAL and silent.passed is False
    assert silent.checks == {"rank": True, "universe": False, "exclusions": True,
                             "period_ends": False}

    uncited = grade_v2(kit.wmt_case(), kit.answer(kit.WMT_TEXT,
                                                  kit.rows_in(kit.STATEMENT_ORDER)))
    assert uncited.grade is Grade.PARTIAL and uncited.passed is False
    assert uncited.checks["rank"] is True


@pytest.mark.criterion("C5")
def test_frontier_style_json_reply(kit):
    # oracle: requirement 4.1 — a baseline's JSON reply, parsed as the arm parses it: rows
    # alphabetical, the rank stated in a statement ("Walmart ranks 2nd")
    reply = json.dumps({
        "value": 53742000000, "unit": "USD",
        "rows": [{"entity": t, "value": kit.WMT_ROWS[t]["value"]} for t in kit.ALPHABETICAL],
        "refused": False, "hedged": False, "cited_chunks": ["S1"],
        "statements": [{"statement": "Walmart ranks 2nd of the 4 retailers in fiscal 2023, "
                                     "behind Amazon. " + kit.PERIOD_ENDS,
                        "sources": ["S1"]}]})
    ans = parse_structured_answer(reply)
    ans.fact_ids = list(FACTS)
    g = grade_v2(kit.wmt_case(), ans)
    assert g.checks["rank"] is True
    assert g.grade is Grade.CORRECT


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(data=st.data())
def test_permuting_rows_never_changes_the_grade(kit, data):
    # oracle: requirement 4 — neither the stated rank nor the value rank reads row order
    order = data.draw(st.permutations(kit.ALPHABETICAL))
    for text in (kit.WMT_TEXT, _no_rank_text(kit)):
        g = grade_v2(kit.wmt_case(), kit.answer(text, kit.rows_in(list(order)),
                                                fact_ids=FACTS))
        assert g.grade is Grade.CORRECT
        assert g.checks == {"rank": True, "universe": True, "exclusions": True,
                            "period_ends": True}


_TARGETS = ["QQA", "QQB", "QQC", "QQD", "QQE", "QQF"]


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(data=st.data())
def test_consistent_renaming_keeps_the_grade(kit, data):
    # oracle: requirement 4 depends on tickers and names only through mentions — renaming
    # every ticker and name of the fictional case and its answer keeps CORRECT (subject DEVB,
    # 200bn, rank 2 of 4; "<Name> ranks second", rows listed subject first)
    targets = data.draw(st.permutations(_TARGETS))
    for rename in ({t: t for t in kit.DEV_TICKERS},
                   dict(zip(kit.DEV_TICKERS, targets[:4], strict=True))):
        case = kit.dev_case(rename)
        order = ["DEVB", "DEVD", "DEVA", "DEVC"]
        rows = [{"ticker": rename[t], "value": kit.DEV_VALUES[t]} for t in order]
        text = (f"{kit.dev_name(rename['DEVB'])} ranks second of the 4 peers, behind "
                f"{kit.dev_name(rename['DEVA'])}.")
        g = grade_v2(case, kit.answer(text, rows, fact_ids=FACTS))
        assert g.grade is Grade.CORRECT
        assert g.checks == {"rank": True, "universe": True, "exclusions": True}


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("order", ["statement", "alphabetical", "none"])
def test_same_text_same_grade_for_every_arm_shape(kit, order):
    # oracle: requirement 4 (arm-neutral) — xbrlrag_full rows (statement order), a baseline's
    # alphabetical rows or a text-only reply (no rows) grade the same text alike: CORRECT
    rows = {"statement": kit.rows_in(kit.STATEMENT_ORDER),
            "alphabetical": kit.rows_in(kit.ALPHABETICAL), "none": []}[order]
    text = kit.WMT_TEXT.replace("ranks 2nd", "is second")
    g = grade_v2(kit.wmt_case(), kit.answer(text, rows, fact_ids=FACTS))
    assert g.grade is Grade.CORRECT
    assert g.checks["rank"] is True
