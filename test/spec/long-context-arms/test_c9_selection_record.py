"""C9: the record of what was picked, persisted with the answer."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.system import SystemAnswer

A23 = "AAA/2023/10-K/canonical.htm"
A24 = "AAA/2024/10-K/canonical.htm"
B24 = "BBB/2024/10-K/canonical.htm"


@pytest.mark.criterion("C9")
def test_resolved_record(make_resolved, make_case, doc):
    # oracle: requirement 14, by hand — one filing of 12 characters, limit 4 * 250000
    arm, _, _, _, _ = make_resolved(objects={A24: doc("x" * 12)})
    stored = arm.answer(make_case()).to_dict()
    assert stored["selection"] == {
        "status": "sent",
        "reason": None,
        "filings": [{"ticker": "AAA", "fiscal_year": 2024, "key": A24, "chars": 12}],
        "missing": [],
        "k": 1,
        "total_chars": 12,
        "limit_chars": 1_000_000,
        "fits": 1,
    }


@pytest.mark.criterion("C9")
def test_multi_record(make_multi, make_case, doc, router_reply):
    # oracle: requirement 14, by hand — AAA 5 characters and BBB 7, target order
    arm, _, _, _, _ = make_multi(
        router_text=router_reply(["BBB", "AAA", "CCC"], ["fiscal 2024"]),
        keys={("AAA", 2024): A24, ("BBB", 2024): B24},
        objects={A24: doc("x" * 5), B24: doc("y" * 7)},
    )
    stored = arm.answer(make_case()).to_dict()
    assert stored["selection"] == {
        "status": "sent",
        "reason": None,
        "filings": [
            {"ticker": "AAA", "fiscal_year": 2024, "key": A24, "chars": 5},
            {"ticker": "BBB", "fiscal_year": 2024, "key": B24, "chars": 7},
        ],
        "missing": [{"ticker": "CCC", "fiscal_year": 2024}],
        "k": 2,
        "total_chars": 12,
        "limit_chars": 1_000_000,
        "fits": 2,
    }


@pytest.mark.criterion("C9")
def test_a_refusal_keeps_its_record_and_other_arms_keep_their_payload(
    make_resolved, make_case, router_reply
):
    # oracle: requirement 14 — the reason is stored; an answer with no selection has exactly
    # the keys every other arm persists today
    arm, _, _, _, _ = make_resolved(router_text=router_reply(["Nobody Corp"], ["fiscal 2024"]))
    stored = arm.answer(make_case()).to_dict()
    assert stored["selection"]["status"] == "undecided"
    assert stored["selection"]["reason"]
    assert stored["refused"] is True
    assert set(SystemAnswer().to_dict()) == {
        "text", "numeric_values", "rows", "fact_ids", "chunk_ids", "refused", "hedged", "error",
    }


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(years=st.lists(st.sampled_from([2021, 2022, 2023, 2024]), min_size=1, max_size=4))
def test_filings_lists_exactly_what_was_sent_and_survives_json(
    make_multi, make_case, doc, router_reply, years
):
    # oracle: requirement 14 — every filing the record names is in the message, in order, and
    # the record is JSON-safe (it goes to a jsonb column)
    keys = {("AAA", y): f"AAA/{y}/10-K/canonical.htm" for y in years}
    objects = {key: doc(f"Zqfiling{ticker_year[1]}") for ticker_year, key in keys.items()}
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA"], [f"fiscal {y}" for y in years]),
        keys=keys, objects=objects,
    )
    answer = arm.answer(make_case())
    record = json.loads(json.dumps(answer.to_dict()))["selection"]
    assert [f["fiscal_year"] for f in record["filings"]] == sorted(set(years))
    text = adapter.messages()[1]["content"]
    positions = [text.index(f"Zqfiling{f['fiscal_year']}") for f in record["filings"]]
    assert positions == sorted(positions)
