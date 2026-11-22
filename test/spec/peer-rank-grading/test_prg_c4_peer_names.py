"""C4: the candidates and names of a peer case (docs/specs/peer-rank-grading.md, req. 3).

Lexicon names are the committed configs/eval/v2/lexicon.yaml entries (WMT: short "Walmart",
legal "Walmart Inc."; AMZN: "Amazon", "Amazon.com, Inc."). The DEV* tickers are fictional
and in no lexicon.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.v2 import peer_names


@pytest.mark.criterion("C4")
def test_lexicon_names_for_a_real_case(kit):
    # oracle: requirement 3 — keys are the subject and the ranked members; the lexicon gives
    # WMT and AMZN their short and legal names
    names = peer_names(kit.wmt_case())
    assert set(names) == {"WMT", "AMZN", "COST", "HD"}
    assert "Walmart" in names["WMT"] and "Walmart Inc." in names["WMT"]
    assert "Amazon" in names["AMZN"] and "Amazon.com, Inc." in names["AMZN"]


@pytest.mark.criterion("C4")
def test_case_names_come_first(kit):
    # oracle: requirement 3 — subject["names"] first, then the lexicon's when not present
    d = copy.deepcopy(kit.wmt_case().to_dict())
    d["subject"]["names"] = {"WMT": ["Wal-Mart"]}
    names = peer_names(EvalCase.from_dict(d))
    assert names["WMT"][0] == "Wal-Mart"
    assert "Walmart" in names["WMT"]


@pytest.mark.criterion("C4")
def test_fictional_members_and_exclusions(kit):
    # oracle: requirement 3 — ranked, excluded and expected_rows tickers are keys; DEVB's
    # case name is kept; ZZZ (excluded, no names anywhere) maps to []
    d = copy.deepcopy(kit.dev_case().to_dict())
    d["disclosures"]["excluded"] = [{"ticker": "ZZZ", "reason": "not_in_corpus"}]
    d["subject"]["names"] = {"DEVB": ["Devb Holdings"]}
    names = peer_names(EvalCase.from_dict(d))
    assert set(names) == {"DEVA", "DEVB", "DEVC", "DEVD", "ZZZ"}
    assert names["DEVB"] == ["Devb Holdings"]
    assert names["ZZZ"] == []
    assert names["DEVA"] == []


_POOL = ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF", "DEVG", "DEVH"]


@pytest.mark.criterion("C4")
@given(ranked=st.lists(st.sampled_from(_POOL), min_size=2, max_size=4, unique=True),
       excluded=st.lists(st.sampled_from(_POOL), max_size=2, unique=True),
       extra=st.lists(st.sampled_from(_POOL), max_size=2, unique=True),
       named=st.lists(st.sampled_from(_POOL), max_size=2, unique=True))
def test_keys_are_the_union_of_the_sources(ranked, excluded, extra, named):
    # oracle: requirement 3 "Keys" — the set union, computed in the test
    subject = ranked[0]
    case = EvalCase.from_dict({
        "id": "p", "question": "q?", "kind": "screen", "tags": ["shape:peer_relative"],
        "expected_rows": [{"ticker": t, "value": 1.0} for t in ranked + extra],
        "disclosures": {"universe": "peers", "members": len(ranked) + len(excluded),
                        "ranked": ranked,
                        "excluded": [{"ticker": t, "reason": "currency"} for t in excluded]},
        "subject": {"ticker": subject, "rank": 1,
                    "names": {t: [f"{t.capitalize()} Holdings"] for t in named}},
    })
    names = peer_names(case)
    assert set(names) == {subject, *ranked, *excluded, *extra, *named}
    for t in named:
        assert names[t][0] == f"{t.capitalize()} Holdings"
