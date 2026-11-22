"""C3: the verifier keeps a statement citing a row's components and shows the LLM that row (R2.3).

The verifier is the real ``XbrlFaithfulnessVerifier``; its LLM (a scripted adapter behind the
real gateway) answers SUPPORTED for every statement it is sent. The evidence is read back from
the prompt the adapter received.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

AAPL = "Apple's total debt for fiscal 2024 was $106.6 billion."
ORCL = "Oracle's total debt for fiscal 2024 was $86.9 billion."
AVGO = "Broadcom's total debt for fiscal 2024 was $67.6 billion."


def _row_entries(entry):
    return [e for e in entry["fact_evidence"] if "screen" not in e]


def _screen_entries(entry):
    return [e for e in entry["fact_evidence"] if "screen" in e]


def _expected_row(crc, ticker):
    # The fields R2.3 lists, from the fixture row (rank = position in DEBT_ORDER).
    return {"step_id": f"debt_screen:{crc.DEBT_PARTS[ticker][0]}", "op": "screen",
            "value": crc.DEBT_VALUES[ticker], "unit": "USD", "ticker": ticker,
            "fiscal_year": 2024, "rank": crc.DEBT_ORDER.index(ticker) + 1,
            "period_end": crc.DEBT_ENDS[ticker]}


@pytest.mark.criterion("C3")
def test_component_citations_are_kept_and_sent_with_their_row(crc):
    # oracle: R2.3 and citation-integrity requirement 4, applied by hand: statements 0 and 1
    # cite only ids of their own rows and are sent (one call) and kept; statement 2 also cites
    # an invented id and is dropped without the LLM.
    details = [crc.detail(AAPL, [14500, 14502, 14506]), crc.detail(ORCL, [71859]),
               crc.detail(AVGO, [26587, 26599999])]
    verifier, adapter = crc.make_verifier()
    out = verifier.verify(crc.response(details, [crc.debt_screen()]))

    assert out.answer_details == details[:2]
    verdicts = {v["index"]: v for v in out.metadata["faithfulness"]["verdicts"]}
    assert verdicts[2]["check"] == "unresolved_ids"
    assert verdicts[2]["unresolved_ids"] == ["26599999"]
    assert adapter.invoked == 1

    sent = crc.sent_entries(adapter)
    assert set(sent) == {AAPL, ORCL}
    for statement, ticker in ((AAPL, "AAPL"), (ORCL, "ORCL")):
        rows = _row_entries(sent[statement])
        assert len(rows) == 1
        want = _expected_row(crc, ticker)
        assert {k: rows[0].get(k) for k in want} == want
        screens = _screen_entries(sent[statement])
        assert len(screens) == 1
        assert screens[0]["step_id"] == "debt_screen"
        assert screens[0]["screen"]["members"] == crc.DEBT_MEMBERS
        assert screens[0]["screen"]["ranked_count"] == len(crc.DEBT_RANKED)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=12)
@given(ticker=st.sampled_from(["AAPL", "ORCL", "AVGO"]), data=st.data())
def test_any_citation_of_a_rows_ids_gives_exactly_that_row(crc, ticker, data):
    # oracle: R2.3. Citing any non-empty set of a row's ids that includes one of its other
    # parts (with or without its fact_id) yields the same single row entry as citing the row's
    # fact_id; the statement is kept.
    parts = crc.DEBT_PARTS[ticker]
    cited = data.draw(st.lists(st.sampled_from(parts[1:]), min_size=1, unique=True))
    if data.draw(st.booleans()):
        cited = [parts[0], *cited]
    statement = {"AAPL": AAPL, "ORCL": ORCL, "AVGO": AVGO}[ticker]
    details = [crc.detail(statement, cited)]
    verifier, adapter = crc.make_verifier()
    out = verifier.verify(crc.response(details, [crc.debt_screen()]))
    assert out.answer_details == details
    rows = _row_entries(crc.sent_entries(adapter)[statement])
    assert [r["step_id"] for r in rows] == [f"debt_screen:{parts[0]}"]
    assert rows[0]["ticker"] == ticker
    # a whole-dollar amount through a JSON round trip: anything under half a dollar is equal
    assert rows[0]["value"] == pytest.approx(crc.DEBT_VALUES[ticker], abs=0.5)
