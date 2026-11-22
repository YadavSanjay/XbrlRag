"""C2: one call, the right filing's text before the question; the reply's own fields.

"Contains the filing's text" is checked two ways, with filing lines made only of ASCII
letters, digits, spaces, commas and ``|`` (characters ``json.dumps`` never escapes):
every expected line appears verbatim in the user message, and so does the body of
``json.dumps(expected_text)`` (the text with its newlines escaped, without the outer
quotes), which is what requirement 3's ``json.dumps`` produces for the whole text.
The expected text is the hand-written C1 output, not a run of ``filing_text``.

"Before the question" and the shared prefix are located through that same escaped body:
the filing text ends where the body's first occurrence ends, and the question must
first appear at or after that point. Filing lines and questions use disjoint marker
words, so neither is a substring of the other.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase

KEY = "AAA/2024/10-K/canonical.htm"


def _case(question: str, ticker: str | None = "AAA", fy: int | None = 2024) -> EvalCase:
    return EvalCase(id="c2", question=question, kind="lookup", ticker=ticker, fy=fy)


def _approx(values):
    # tolerance: JSON number text round-trips exactly; 1e-12 relative only guards repr
    return pytest.approx(values, rel=1e-12)


def _body(lines: list[str]) -> str:
    """The filing text as ``json.dumps`` writes it, without the outer quotes."""
    return json.dumps("\n".join(lines))[1:-1]


def _end_of_filing(user: str, lines: list[str]) -> int:
    """The index just past the filing text in *user* (asserting it is there)."""
    body = _body(lines)
    assert body in user
    return user.index(body) + len(body)


def _assert_text_sent(adapter, question: str, lines: list[str], call: int = 0) -> None:
    user = adapter.user_text(call)
    system = adapter.system_text(call)
    assert question in user
    end = _end_of_filing(user, lines)
    # Requirement 3: the whole filing text comes before the question.
    assert user.find(question) >= end
    for line in lines:
        assert line in user
        assert line not in system
    assert question not in system


@pytest.mark.criterion("C2")
def test_resolves_reads_and_sends_the_filing_once(make_arm, xhtml, ix_header, reply):
    # oracle: requirements 1 and 3; the hand-built filing's lines (C1 rules) and the
    # hand-built reply's own fields
    html = xhtml(
        ix_header("ZQHIDDENDEI")
        + "<h2>Zorblax Item 7 overview</h2>"
        + "<p>Zorblax net sales increased 7,431 widgets</p>"
        + "<table><tr><td>Zorblax revenue</td><td>391,035</td></tr></table>"
    )
    lines = [
        "Zorblax Item 7 overview",
        "Zorblax net sales increased 7,431 widgets",
        "Zorblax revenue | 391,035",
    ]
    reply_text = reply(
        391035000000,
        rows=[{"entity": "Americas", "value": 167045000000}],
        hedged=True,
        cited_chunks=["chunk-1"],
    )
    arm, adapter, resolver, store = make_arm(html, reply_text=reply_text)
    question = "What was Zorblax total revenue in fiscal 2024?"
    from_case = arm.answer(_case(question))

    assert resolver.calls == [("AAA", 2024)]
    assert store.reads != [] and set(store.reads) == {KEY}
    assert adapter.calls == 1
    _assert_text_sent(adapter, question, lines)
    assert "ZQHIDDENDEI" not in adapter.user_text()
    assert from_case.error is None
    assert from_case.numeric_values == _approx([391035000000.0, 167045000000.0])
    assert [r["entity"] for r in from_case.rows] == ["Americas"]
    assert from_case.hedged is True
    assert from_case.refused is False
    assert from_case.fact_ids == []
    assert from_case.chunk_ids == []


@pytest.mark.criterion("C2")
def test_year_over_year_case_uses_only_its_own_year(make_arm, xhtml, reply):
    # oracle: requirement 1 — a YoY case reads only its own fiscal year's filing, even
    # when the prior year's filing also resolves
    keys = {("AAA", 2024): KEY, ("AAA", 2023): "AAA/2023/10-K/canonical.htm"}
    objects = {
        KEY: xhtml("<p>Zorblax fiscal 2024 and 2023 comparatives</p>"),
        "AAA/2023/10-K/canonical.htm": xhtml("<p>Quuxian prior year filing</p>"),
    }
    arm, adapter, resolver, store = make_arm(
        reply_text=reply(12.5), keys=keys, objects=objects
    )
    question = "By what percent did AAA revenue change from fiscal 2023 to fiscal 2024?"
    ans = arm.answer(_case(question))
    assert resolver.calls == [("AAA", 2024)]
    assert set(store.reads) == {KEY}
    assert adapter.calls == 1
    assert "Zorblax fiscal 2024 and 2023 comparatives" in adapter.user_text()
    assert "Quuxian" not in adapter.user_text()
    assert ans.numeric_values == _approx([12.5])


@pytest.mark.criterion("C2")
def test_refusal_reply_is_a_refusal(make_arm, reply):
    # oracle: requirement 3 — the reply object's own refused flag, no numbers
    arm, adapter, _, _ = make_arm(reply_text=reply(None, refused=True))
    ans = arm.answer(_case("What was AAA goodwill impairment in fiscal 2024?"))
    assert adapter.calls == 1
    assert ans.error is None
    assert ans.refused is True
    assert ans.numeric_values == []
    assert ans.fact_ids == [] and ans.chunk_ids == []


_WORD = st.text(
    alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")),
    min_size=1,
    max_size=10,
)
# A leading marker keeps every line out of any plausible system prompt.
_LINE = st.lists(_WORD, min_size=1, max_size=5).map(lambda ws: "Zqx " + " ".join(ws))
_OBJECTS = st.fixed_dictionaries(
    {
        "value": st.one_of(st.none(), st.floats(min_value=-1e13, max_value=1e13, allow_nan=False)),
        "rows": st.lists(
            st.fixed_dictionaries(
                {
                    "entity": st.sampled_from(["AAA", "Europe", "Services"]),
                    "value": st.floats(min_value=-1e13, max_value=1e13, allow_nan=False),
                }
            ),
            max_size=4,
        ),
        "refused": st.booleans(),
        "hedged": st.booleans(),
        "cited_chunks": st.lists(st.sampled_from(["c-1", "c-2"]), max_size=2),
    }
)


@pytest.mark.criterion("C2")
@given(
    lines=st.lists(_LINE, min_size=1, max_size=6),
    ticker=st.sampled_from(["AAA", "BBB", "TSM"]),
    fy=st.integers(min_value=2000, max_value=2030),
    obj=_OBJECTS,
    question_tail=_WORD,
)
def test_any_filing_and_reply_round_trip(make_arm, xhtml, lines, ticker, fy, obj,
                                         question_tail):
    # oracle: closed form — the resolver's call is the case's (ticker, fy), the sent text
    # is the random paragraphs (C1 rules), and the answer is the random reply's fields
    key = f"{ticker}/{fy}/10-K/canonical.htm"
    html = xhtml("".join(f"<p>{line}</p>" for line in lines))
    arm, adapter, resolver, store = make_arm(
        reply_text=json.dumps(obj), keys={(ticker, fy): key}, objects={key: html}
    )
    question = f"What was the Qvx revenue of {ticker} in fiscal {fy} {question_tail}?"
    ans = arm.answer(_case(question, ticker=ticker, fy=fy))

    expected_values = ([obj["value"]] if obj["value"] is not None else []) + [
        r["value"] for r in obj["rows"]
    ]
    assert resolver.calls == [(ticker, fy)]
    assert set(store.reads) == {key}
    assert adapter.calls == 1
    _assert_text_sent(adapter, question, lines)
    assert ans.error is None
    assert ans.numeric_values == _approx(expected_values)
    assert [r["entity"] for r in ans.rows] == [r["entity"] for r in obj["rows"]]
    assert ans.refused is obj["refused"]
    assert ans.hedged is obj["hedged"]
    assert ans.fact_ids == []
    assert ans.chunk_ids == []


@pytest.mark.criterion("C2")
def test_system_message_does_not_depend_on_the_case(make_arm, xhtml, reply):
    # oracle: requirement 3 — the system message is case-independent, so two cases on
    # different filers, years, filings and questions get the same system message
    keys = {("AAA", 2024): KEY, ("BBB", 2021): "BBB/2021/10-K/canonical.htm"}
    objects = {
        KEY: xhtml("<p>Zorblax first filing text</p>"),
        "BBB/2021/10-K/canonical.htm": xhtml("<p>Quuxian second filing text</p>"),
    }
    arm, adapter, _, _ = make_arm(reply_text=reply(1.0), keys=keys, objects=objects)
    first = arm.answer(_case("What was Wibble revenue in fiscal 2024?"))
    second = arm.answer(_case("How much Frobnic debt in fiscal 2021?", ticker="BBB", fy=2021))
    assert first.error is None and second.error is None
    assert adapter.calls == 2
    assert adapter.system_text(0) != ""
    assert adapter.system_text(0) == adapter.system_text(1)
    _assert_text_sent(adapter, "What was Wibble revenue in fiscal 2024?",
                      ["Zorblax first filing text"], call=0)
    _assert_text_sent(adapter, "How much Frobnic debt in fiscal 2021?",
                      ["Quuxian second filing text"], call=1)


@pytest.mark.criterion("C2")
@given(
    lines=st.lists(_LINE, min_size=1, max_size=6),
    tails=st.lists(_WORD, min_size=2, max_size=2, unique=True),
)
def test_same_filing_calls_share_a_prefix(make_arm, xhtml, reply, lines, tails):
    # oracle: requirement 3 / C2 shared-prefix property — two different questions on the
    # same filing give identical system messages, and user messages identical up to the
    # end of the filing text (the random paragraphs, C1 rules)
    html = xhtml("".join(f"<p>{line}</p>" for line in lines))
    arm, adapter, _, _ = make_arm(html, reply_text=reply(2.0))
    q1 = f"What was the Qvx revenue in fiscal 2024 {tails[0]}?"
    q2 = f"What was the Qvx revenue in fiscal 2024 {tails[1]}?"
    assert arm.answer(_case(q1)).error is None
    assert arm.answer(_case(q2)).error is None
    assert adapter.calls == 2
    _assert_text_sent(adapter, q1, lines, call=0)
    _assert_text_sent(adapter, q2, lines, call=1)

    assert adapter.system_text(0) == adapter.system_text(1)
    user1, user2 = adapter.user_text(0), adapter.user_text(1)
    end1, end2 = _end_of_filing(user1, lines), _end_of_filing(user2, lines)
    assert end1 == end2
    assert user1[:end1] == user2[:end2]
    assert user1 != user2
