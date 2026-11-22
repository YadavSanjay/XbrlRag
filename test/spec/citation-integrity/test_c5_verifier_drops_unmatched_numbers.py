"""C5: the verifier drops a statement whose numbers do not match its citations (rules 3, 4).

The verifier is real; its LLM answers SUPPORTED to every statement it is sent.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.observability.trace_context import trace_query

PREFIX = "failed faithfulness check:"


def _recorded(ci, details):
    steps, plan_steps, chunks = ci.apple_evidence()
    return ci.response(details, steps, plan_steps=plan_steps, chunks=chunks)


@pytest.mark.criterion("C5")
def test_mismatched_numbers_drop_their_statements_and_are_recorded(ci):
    # oracle: rules 3 and 4 applied by hand; AAPL FY2024 revenue 391,035 m, net margin
    # 93,736 / 391,035 = 23.97 %, chunk text "Services gross margin percentage was 73.9%"
    details = [
        ci.detail("Apple's revenue was $391,035 million.", ["13253"]),                    # 0 keep
        ci.detail("Apple's revenue was $391.0 billion.", ["r_aapl"]),                     # 1 keep
        ci.detail("Microsoft's revenue was $245.1 billion.", ["13253"]),                  # 2 drop
        ci.detail("Apple's net margin was 24.0%.", ["m_aapl"]),                           # 3 keep
        ci.detail("Apple's revenue grew 2.0% from fiscal 2023.", ["13253"]),              # 4 drop
        ci.detail("In fiscal 2024 Apple ranked 3rd by revenue among 5 companies.",
                  ["13253"]),                                                              # 5 keep
        ci.detail("Services gross margin was 73.9% on revenue of $391.0 billion.",
                  ["13253"], [ci.CHUNK_AAPL_MDNA]),                                        # 6 keep
        ci.detail("Services gross margin was 75.0%.", [], [ci.CHUNK_AAPL_MDNA]),          # 7 drop
        ci.detail("Apple's revenue was $999.9 billion.", ["13253", "13253999"]),          # 8 drop
    ]
    verifier, adapter = ci.make_verifier()
    with trace_query("recorded question") as tc:
        out = verifier.verify(_recorded(ci, details))

    assert out.answer_details == [details[i] for i in (0, 1, 3, 5, 6)]
    reasons = {p["question_fragment"]: p["reason"] for p in out.unanswered_parts}
    assert set(reasons) == {details[i]["statement"] for i in (2, 4, 7, 8)}
    for i, number in ((2, "245.1"), (4, "2.0"), (7, "75.0")):
        assert reasons[details[i]["statement"]].startswith(PREFIX)
        assert number in reasons[details[i]["statement"]]

    verdicts = {v["index"]: v for v in out.metadata["faithfulness"]["verdicts"]}
    for i, number in ((2, "245.1"), (4, "2.0"), (7, "75.0")):
        assert verdicts[i]["verdict"] == "UNSUPPORTED"
        assert verdicts[i]["check"] == "number_mismatch"
        assert len(verdicts[i]["unmatched_numbers"]) == 1
        assert number in verdicts[i]["unmatched_numbers"][0]
    # rule 1 is checked first
    assert verdicts[8]["check"] == "unresolved_ids"
    assert verdicts[8]["unresolved_ids"] == ["13253999"]
    assert out.metadata["fully_answered"] is False

    events = [e for e in tc.events if e.get("kind") == "decision"
              and e.get("point") == "citation_integrity" and e.get("check") == "number_mismatch"]
    assert sorted(e["index"] for e in events) == [2, 4, 7]
    assert all(len(e["numbers"]) == 1 for e in events)

    assert adapter.invoked == 1
    for i in (2, 4, 7, 8):
        assert not ci.was_sent(adapter, details[i]["statement"])
    for i in (0, 1, 3, 5, 6):
        assert ci.was_sent(adapter, details[i]["statement"])


@pytest.mark.criterion("C5")
def test_no_llm_call_when_every_statement_misstates_a_number(ci):
    # oracle: requirement 4 — deterministic drops never reach the LLM
    details = [ci.detail("Apple's revenue was $383.3 billion.", ["13253"]),     # FY2023's figure
               ci.detail("Apple's diluted EPS was $6.13.", ["e_aapl"])]          # FY2023's EPS
    verifier, adapter = ci.make_verifier()
    out = verifier.verify(_recorded(ci, details))
    assert out.answer_details == []
    assert adapter.invoked == 0
    assert [v["check"] for v in out.metadata["faithfulness"]["verdicts"]] == [
        "number_mismatch", "number_mismatch"]


_FACTS = [("AAPL", "Revenue", "r_aapl"), ("AAPL", "NetIncome", "n_aapl"),
          ("AAPL", "GrossProfit", "g_aapl")]


@pytest.mark.criterion("C5")
@given(which=st.sampled_from(_FACTS),
       scale=st.sampled_from([("million", 1e6, 1), ("billion", 1e9, 3)]),
       wrong_first=st.booleans(), data=st.data())
def test_a_restated_value_survives_and_a_misstated_one_is_dropped(ci, which, scale, wrong_first,
                                                                   data):
    # oracle: rule 3 — the cited value in another scale, rounded to d decimals, matches; the
    # same text moved by 2 x max(stated step, 1e6) does not; statement order does not matter
    ticker, family, step_id = which
    word, mult, max_d = scale
    d = data.draw(st.integers(0, max_d))
    shown = round(ci.fact(ticker, family)[1] / mult, d)
    bump = 2 * max(10.0 ** -d, 1e6 / mult)
    good = ci.detail(f"The figure was ${shown:,.{d}f} {word}.", [step_id])
    bad = ci.detail(f"The other figure was ${shown + bump:,.{d}f} {word}.", [step_id])
    details = [bad, good] if wrong_first else [good, bad]
    verifier, _ = ci.make_verifier()
    out = verifier.verify(_recorded(ci, details))
    assert out.answer_details == [good]
    assert [p["question_fragment"] for p in out.unanswered_parts] == [bad["statement"]]
