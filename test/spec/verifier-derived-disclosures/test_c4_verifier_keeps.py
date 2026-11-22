"""C4: the verifier keeps a true rank statement and drops a wrong count (Requirements 3-4).

The verifier is the real ``XbrlFaithfulnessVerifier``; its LLM (a scripted adapter behind the
real gateway) answers SUPPORTED for everything, so any drop is the deterministic check's.
The statements are the stored F3a ones (int-f4d823788172-00694060).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

RANK = ("Walmart ranks 2nd of the 4 retailers in the fiscal 2023 accounts payable screen, "
        "ordered from highest to lowest.")
OTHERS = [
    ("Walmart's accounts payable for fiscal 2023 (period ended 2023-01-31) was "
     "53742000000.0 USD.", ["205130"]),
    ("Amazon ranks 1st with accounts payable of 84981000000.0 USD (period ended 2023-12-31).",
     ["6971"]),
    ("The screen's fiscal 2023 period end dates differ across the four companies, so the "
     "figures are not for the same date.", ["6971", "205130", "208864", "211999"]),
]


def _verify(vd, rank_statement, extra=True):
    rows = ["205130", "6971", "208864", "211999"]
    details = [vd.detail(rank_statement, rows)]
    if extra:
        details += [vd.detail(s, f) for s, f in OTHERS]
    verifier, adapter = vd.make_verifier()
    out = verifier.verify(vd.response(details, [vd.wmt_lookup(), vd.ap_screen()]))
    return out, adapter, details


@pytest.mark.criterion("C4")
def test_true_rank_statement_is_kept(vd):
    # oracle: Requirement 3 by hand: "4" is ap_screen's ranked count; the LLM says SUPPORTED
    out, adapter, details = _verify(vd, RANK)
    assert [d["statement"] for d in out.answer_details] == [d["statement"] for d in details]
    assert out.unanswered_parts == []
    assert all(v["verdict"] == "SUPPORTED" for v in out.metadata["faithfulness"]["verdicts"])
    assert RANK in vd.sent_entries(adapter)


@pytest.mark.criterion("C4")
def test_wrong_count_is_still_dropped(vd):
    # oracle: Requirement 3: 5 is no count, rank or value of the cited screen; 4 is its count
    wrong = RANK.replace("the 4 retailers", "the 5 retailers")
    rows = ["205130", "6971", "208864", "211999"]
    details = [vd.detail(wrong, rows), vd.detail(RANK, rows)]
    verifier, adapter = vd.make_verifier()
    out = verifier.verify(vd.response(details, [vd.wmt_lookup(), vd.ap_screen()]))
    assert [d["statement"] for d in out.answer_details] == [RANK]
    verdict = next(v for v in out.metadata["faithfulness"]["verdicts"] if v["index"] == 0)
    assert verdict["verdict"] == "UNSUPPORTED"
    assert verdict["check"] == "number_mismatch"
    assert verdict["unmatched_numbers"] == ["5"]
    sent = vd.sent_entries(adapter)
    assert wrong not in sent and RANK in sent
    assert [p["question_fragment"] for p in out.unanswered_parts] == [wrong]


@pytest.mark.criterion("C4")
@settings(max_examples=40)
@given(n=st.integers(1, 200), extra=st.booleans())
def test_kept_exactly_when_the_count_is_the_screens(vd, n, extra):
    # oracle: Requirement 3: ap_screen's counts are 0 and 4; ranks 1-4 of the cited rows are
    # candidates too, so only n in {1, 2, 3, 4} is kept and any other n is dropped
    statement = RANK.replace("the 4 retailers", f"the {n} retailers")
    out, _, _ = _verify(vd, statement, extra=extra)
    kept = statement in [d["statement"] for d in out.answer_details]
    assert kept is (n in {1, 2, 3, 4})
