"""C5: the verifier LLM sees each cited screen's derived facts (Requirement 4).

The evidence is read back from the prompt the fake adapter received (the only fake).
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

AMZN_ROW = ("Amazon ranks 1st with accounts payable of 84981000000.0 USD (period ended "
            "2023-12-31).")
ENDS_DIFFER = ("The screen's fiscal 2023 period end dates differ across the four companies, so "
               "the figures are not for the same date.")
BY_STEP = "The screen covered four retailers (AMZN, WMT, COST, HD) with no exclusions."
LOOKUP_ONLY = "Walmart's accounts payable for fiscal 2023 was 53742000000.0 USD."
BOTH = "Amazon led the retailers and Adobe had the lowest net PP&E of the covered companies."

AP_SUMMARY = {
    "step_id": "ap_screen", "universe": "retailers", "fiscal_year": 2023, "members": 4,
    "ranked_count": 4, "ranked": ["AMZN", "WMT", "COST", "HD"], "rows_returned": 4,
    "exclusions": [],
    "period_ends": {"AMZN": "2023-12-31", "WMT": "2023-01-31", "COST": "2023-09-03",
                    "HD": "2024-01-28"},
    "period_ends_differ": True,
}


def _ppe_summary(vd):
    return {"step_id": "ppe_screen", "universe": "covered", "fiscal_year": 2025, "members": 33,
            "ranked_count": 23, "ranked": list(vd.PPE_RANKED), "rows_returned": 3,
            "exclusions": [dict(e) for e in vd.PPE_EXCLUSIONS],
            "period_ends": dict(vd.PPE_ENDS), "period_ends_differ": True}


def _screens(entry):
    """The (step_id, screen) pairs of one statement's fact_evidence."""
    return [(e.get("step_id"), e["screen"]) for e in entry["fact_evidence"] if "screen" in e]


def _sent(vd, details):
    verifier, adapter = vd.make_verifier()
    verifier.verify(vd.response(details, [vd.wmt_lookup(), vd.ap_screen(), vd.ppe_screen()]))
    return vd.sent_entries(adapter)


def _four_rows(vd):
    return [vd.AP_IDS[t] for t in ("AMZN", "WMT", "COST", "HD")]


@pytest.mark.criterion("C5")
def test_row_entries_carry_rank_and_period_end(vd):
    # oracle: Requirement 4.1 and the stored AMZN row (rank 1, period end 2023-12-31)
    sent = _sent(vd, [vd.detail(AMZN_ROW, [vd.AP_IDS["AMZN"]])])
    rows = [e for e in sent[AMZN_ROW]["fact_evidence"] if "screen" not in e]
    assert len(rows) == 1
    assert rows[0].get("rank") == 1 and rows[0].get("period_end") == "2023-12-31"
    # exact: the stored value is passed through unchanged, never computed
    assert rows[0]["value"] == pytest.approx(84_981_000_000.0, abs=0)
    assert rows[0]["ticker"] == "AMZN"


@pytest.mark.criterion("C5")
def test_one_screen_entry_per_cited_screen(vd):
    # oracle: Requirement 4.2 and C1's summaries written by hand
    details = [vd.detail(ENDS_DIFFER, _four_rows(vd)),
               vd.detail(BY_STEP, ["ap_screen"]),
               vd.detail(LOOKUP_ONLY, ["wmt_ap23"]),
               vd.detail(BOTH, [vd.AP_IDS["AMZN"], vd.PPE_ROWS[0][2]])]
    sent = _sent(vd, details)
    assert _screens(sent[ENDS_DIFFER]) == [("ap_screen", AP_SUMMARY)]
    assert _screens(sent[BY_STEP]) == [("ap_screen", AP_SUMMARY)]
    assert _screens(sent[LOOKUP_ONLY]) == []
    pairs = _screens(sent[BOTH])
    assert len(pairs) == 2
    assert ("ap_screen", AP_SUMMARY) in pairs and ("ppe_screen", _ppe_summary(vd)) in pairs


@pytest.mark.criterion("C5")
def test_step_id_citation_is_the_steps_own_entry(vd):
    # oracle: Requirement 4.2: citing the step id puts the summary on the step's own entry,
    # so the statement has exactly one evidence entry
    sent = _sent(vd, [vd.detail(BY_STEP, ["ap_screen"])])
    entries = sent[BY_STEP]["fact_evidence"]
    assert len(entries) == 1
    assert entries[0]["step_id"] == "ap_screen" and entries[0].get("screen") == AP_SUMMARY


_ids = st.sampled_from(["6971", "205130", "208864", "211999", "ap_screen", "171776", "188452",
                        "265954", "ppe_screen"])


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@settings(max_examples=30)
@given(ids=st.lists(_ids, min_size=1, max_size=6), data=st.data())
def test_screen_entries_follow_the_cited_screens_only(vd, ids, data):
    # oracle: Requirement 4.2: one entry per cited screen; order, repeats and an added
    # lookup id never change the set of (step_id, screen) pairs
    ap_ids = {"6971", "205130", "208864", "211999", "ap_screen"}
    expected = set()
    if ap_ids & set(ids):
        expected.add(("ap_screen", json.dumps(AP_SUMMARY, sort_keys=True)))
    if {"171776", "188452", "265954", "ppe_screen"} & set(ids):
        expected.add(("ppe_screen", json.dumps(_ppe_summary(vd), sort_keys=True)))
    shuffled = data.draw(st.permutations(ids)) + [ids[0]]
    statement = "The screens are as stated."
    variants = [ids, shuffled, ids + ["wmt_ap23"]]
    for variant in variants:
        sent = _sent(vd, [vd.detail(statement, variant)])
        pairs = _screens(sent[statement])
        assert len(pairs) == len(expected)
        assert {(s, json.dumps(v, sort_keys=True)) for s, v in pairs} == expected
