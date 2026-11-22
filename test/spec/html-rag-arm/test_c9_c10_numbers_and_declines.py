"""C9 and C10: the calculator does the sums, and when the arm declines."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.html10k.answerer import printed_value

COLUMN = "2024"


@pytest.fixture
def statement(make_arm, make_case, reply, cell, table_chunk):
    """Factory: answers one reply against the hand-written statement of the conftest."""

    def run(inputs, value_expr, **reply_kwargs):
        table = table_chunk()
        tid = table["chunk_id"]
        env = make_arm(
            [table],
            reply_text=reply(
                [cell(i, tid, r, c, p) for i, r, c, p in inputs], value_expr, **reply_kwargs
            ),
        )
        return env.arm.answer(make_case()), env

    return run


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(
    "inputs, expr, expected",
    [
        pytest.param([("a", "Net sales", COLUMN, "$1,200")], "a", 1.2e9, id="lookup"),
        pytest.param(
            [("a", "Net sales", COLUMN, "$1,200"), ("b", "Cost of sales", COLUMN, "(700)")],
            "a + b", 5e8, id="a-negative-is-signed",
        ),
        pytest.param(
            [("a", "Net sales", "2024", "$1,200"), ("b", "Net sales", "2023", "$1,000")],
            "(a - b) / b * 100", 20.0, id="growth-in-percent",
        ),
    ],
)
def test_the_number_is_the_calculators(statement, inputs, expr, expected):
    # oracle: by hand — 1,200 million; 1,200 - 700 = 500 million; 200 / 1,000 = 20%
    answer, _ = statement(inputs, expr)
    # tolerance: float division on exact integers
    assert answer.numeric_values == pytest.approx([expected], rel=1e-12)
    assert answer.refused is False
    assert answer.selection["status"] == "answered"


@pytest.mark.criterion("C9")
def test_a_breakdown(statement):
    # oracle: by hand — two rows, each its own expression; numeric_values lists them
    answer, _ = statement(
        [("a", "Net sales", "2024", "$1,200"), ("b", "Net sales", "2023", "$1,000")], None,
        rows=[{"entity": "FY24", "expr": "a"}, {"entity": "FY23", "expr": "b"}],
    )
    assert [r["entity"] for r in answer.rows] == ["FY24", "FY23"]
    # tolerance: float product of exact integers
    assert [r["value"] for r in answer.rows] == pytest.approx([1.2e9, 1e9], rel=1e-12)
    assert answer.numeric_values == pytest.approx([1.2e9, 1e9], rel=1e-12)


@pytest.mark.criterion("C9")
def test_a_number_in_the_reply_is_never_read(statement):
    # oracle: requirement 17 — the model's own "value" is ignored, whatever it says
    answer, _ = statement([("a", "Net sales", COLUMN, "$1,200")], "a", value=999, numeric=[1])
    # tolerance: float product of exact integers
    assert answer.numeric_values == pytest.approx([1.2e9], rel=1e-12)


@pytest.mark.criterion("C9")
def test_a_passage_answer_has_no_number(make_arm, make_case, reply, prose_chunk):
    # oracle: requirement 18 — no expression, so no number, even with one written in the text
    prose = prose_chunk()
    env = make_arm(
        [prose],
        reply_text=reply(
            passages=[{"chunk_id": prose["chunk_id"], "quote": "widgets"}],
            value=999, text="Demand rose by 999.",
        ),
    )
    answer = env.arm.answer(make_case())
    assert answer.numeric_values == []
    assert answer.text == "Demand rose by 999."
    assert answer.refused is False


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(
    "printed, scale, expected",
    [
        ("$1,200", 1000000, 1.2e9),
        ("1,200", 1, 1200.0),
        ("(700)", 1000000, -7e8),
        ("$(700)", 1000, -7e5),
        ("-5", 1, -5.0),
        ("−5", 1, -5.0),
        ("12.5%", 1, 12.5),
        ("7,392 [2]", 1000, 7392000.0),
        ("US$3", 1, 3.0),
        ("5", 1000000000, 5e9),
        ("5", 1000000.0, 5e6),
        ("$1.2 billion", 1, 1.2e9),
        ("$1.2 billion", 1000000, 1.2e9),
        ("3 million", 1000, 3e6),
    ],
)
def test_printed_value(printed, scale, expected):
    # oracle: requirement 19, each by hand; a printed scale word wins over `scale`
    # tolerance: float product of exact decimals
    assert printed_value(printed, scale) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(
    "printed, scale",
    [("—", 1), ("–", 1), ("-", 1), ("", 1), ("n/a", 1), ("see note 4", None), ("5", 7),
     ("5", 0), ("5", True), ("5", "1000"), ("5", None), ("Goodwill", 1000)],
)
def test_a_nil_or_bad_scale_is_none_never_zero(printed, scale):
    # oracle: requirement 19 and the RAG rules — nil never becomes 0; the control pins the stub
    assert printed_value(printed, scale) is None
    assert printed_value("1", 1) == pytest.approx(1.0, abs=1e-12)  # tolerance: exact


@pytest.mark.criterion("C9")
def test_a_nil_cell_gives_no_number(statement):
    # oracle: requirement 19 — Goodwill 2024 prints a dash; it must not sum as 0
    answer, _ = statement(
        [("a", "Net sales", COLUMN, "$1,200"), ("b", "Goodwill", COLUMN, "—")], "a + b"
    )
    assert answer.numeric_values == []
    assert answer.refused is True


@pytest.mark.criterion("C9")
def test_not_meaningful(statement):
    # oracle: requirement 23 — a change from a negative base; the grader's n/m path reads hedged,
    # and XbrlCalculatorTool.not_meaningful_reason gives the text
    answer, _ = statement(
        [("a", "Goodwill", "2023", "5"), ("b", "Cost of sales", COLUMN, "(700)")],
        "(a - b) / b",
    )
    assert answer.numeric_values == []
    assert answer.hedged is True
    assert answer.refused is False
    assert "n/m" in answer.text
    assert answer.selection["status"] == "answered"


@pytest.mark.criterion("C9")
@given(
    left=st.integers(1, 999_999),
    right=st.integers(1, 999_999),
    scale=st.sampled_from([1, 1000, 1000000, 1000000000]),
)
def test_any_difference(make_arm, make_case, reply, cell, payload, left, right, scale):
    # oracle: computed here from the integers, with no call to the arm's arithmetic
    text = f"| x | 2024 | 2023 |\n| --- | --- | --- |\n| Sales | {left:,} | {right:,} |"
    chunk = payload("t", text=text, contains_table=True, table_index=0)
    cid = chunk["chunk_id"]
    env = make_arm(
        [chunk],
        reply_text=reply(
            [cell("a", cid, "Sales", "2024", f"{left:,}", scale),
             cell("b", cid, "Sales", "2023", f"{right:,}", scale)],
            "a - b",
        ),
    )
    # tolerance: float arithmetic on integers up to about 1e15
    expected = (left - right) * scale
    assert env.arm.answer(make_case()).numeric_values == pytest.approx([expected], abs=1e-3)


@pytest.mark.criterion("C9")
@given(
    amount=st.integers(0, 10**9),
    negative=st.booleans(),
    scale=st.sampled_from([1, 1000, 1000000, 1000000000]),
)
def test_printed_value_of_any_integer(amount, negative, scale):
    # oracle: computed here; parentheses are the filing's negative
    printed = f"({amount:,})" if negative else f"{amount:,}"
    expected = (-amount if negative else amount) * scale
    # tolerance: float product up to 1e18
    assert printed_value(printed, scale) == pytest.approx(expected, rel=1e-12, abs=1e-9)
    assert printed_value("1", 1) == pytest.approx(1.0, abs=1e-12)  # tolerance: exact


@pytest.mark.criterion("C10")
def test_no_candidates_declines_with_no_answer_call(
    make_arm, make_case, cost_of, router_model
):
    # oracle: requirement 24 — nothing retrieved; the router's call is all it cost
    env = make_arm([])
    answer = env.arm.answer(make_case())
    assert answer.refused is True
    assert answer.error is None
    assert answer.numeric_values == []
    assert answer.text
    assert answer.selection["status"] == "no_candidates"
    assert env.adapter.calls == 0
    # tolerance: float sum of two rates
    assert answer.cost_usd == pytest.approx(cost_of(router_model, 100, 50), rel=1e-9)


def _assert_declined(answer, status: str) -> None:
    assert answer.refused is True
    assert answer.error is None
    assert answer.numeric_values == []
    assert answer.rows == []
    assert answer.chunk_ids == []
    assert answer.text
    assert answer.selection["status"] == status
    assert answer.selection["reason"]


@pytest.mark.criterion("C10")
def test_the_model_declines(statement):
    # oracle: requirement 24 — refused wins over anything else in the reply
    answer, _ = statement([("a", "Net sales", COLUMN, "$1,200")], "a", refused=True)
    _assert_declined(answer, "model_declined")


@pytest.mark.criterion("C10")
def test_a_reply_with_no_object_is_not_mined_for_numbers(make_arm, make_case):
    # oracle: requirement 24 — parse_structured_answer's free-text fallback would read 5 million,
    # a number that didn't come through the calculator
    env = make_arm(reply_text="Revenue was 5 million.")
    _assert_declined(env.arm.answer(make_case()), "unparseable")


@pytest.mark.criterion("C10")
@pytest.mark.parametrize(
    "inputs, expr",
    [
        pytest.param([("a", "Net sales", COLUMN, "9,999")], "a", id="a-number-not-in-the-chunk"),
        pytest.param([("a", "Net sales", COLUMN, "$1,200")], "b", id="an-undefined-id"),
        pytest.param([("a", "Net sales", COLUMN, "$1,200")], "a ** 2", id="a-disallowed-operator"),
        pytest.param([("a", "Net sales", COLUMN, "$1,200")], "__import__('os')", id="a-call"),
        pytest.param([("a", "Net sales", COLUMN, "$1,200")], "a / (a - a)", id="division-by-zero"),
        pytest.param([("1x", "Net sales", COLUMN, "$1,200")], "a", id="a-bad-id"),
        pytest.param(
            [("a", "Net sales", COLUMN, "$1,200"), ("a", "Net sales", "2023", "$1,000")], "b",
            id="a-repeated-id",
        ),
    ],
)
def test_an_unsupported_value(statement, inputs, expr):
    # oracle: requirements 21 and 24, each by hand
    answer, _ = statement(inputs, expr)
    _assert_declined(answer, "unsupported")


@pytest.mark.criterion("C10")
def test_a_cited_chunk_that_was_not_given(make_arm, make_case, reply, cell, table_chunk):
    # oracle: requirement 21 — the model invented a chunk id
    env = make_arm(
        [table_chunk()],
        reply_text=reply([cell("a", "invented", "Net sales", COLUMN, "$1,200")], "a"),
    )
    _assert_declined(env.arm.answer(make_case()), "unsupported")


@pytest.mark.criterion("C10")
def test_an_answer_with_no_citation_declines(make_arm, make_case, reply, table_chunk):
    # oracle: requirement 24 — "when retrieval finds no supporting table or passage"
    env = make_arm([table_chunk()], reply_text=reply(text="Sales were strong."))
    _assert_declined(env.arm.answer(make_case()), "unsupported")


@pytest.mark.criterion("C10")
def test_a_bad_row_is_dropped_and_hedged(statement):
    # oracle: requirement 24 — one good row stands
    answer, _ = statement(
        [("a", "Net sales", COLUMN, "$1,200")], None,
        rows=[{"entity": "good", "expr": "a"}, {"entity": "bad", "expr": "z"}],
    )
    assert [r["entity"] for r in answer.rows] == ["good"]
    assert answer.hedged is True
    assert answer.refused is False
    assert answer.selection["status"] == "answered"


@pytest.mark.criterion("C10")
def test_every_row_bad_declines(statement):
    # oracle: requirement 24
    answer, _ = statement(
        [("a", "Net sales", COLUMN, "$1,200")], None, rows=[{"entity": "bad", "expr": "z"}]
    )
    _assert_declined(answer, "unsupported")


@pytest.mark.criterion("C10")
@given(
    printed=st.sampled_from(["$1,200", "1,000", "9,999", "4,321", "(700)", "—"]),
    rows_too=st.booleans(),
)
def test_no_number_has_an_unresolved_input_behind_it(
    make_arm, make_case, reply, cell, table_chunk, printed, rows_too
):
    # oracle: requirements 21 and 24 — the conftest's table prints the first two and (700) and
    # not the next two, and a dash is a nil
    table = table_chunk()
    resolvable = printed in ("$1,200", "1,000", "(700)")
    env = make_arm(
        [table],
        reply_text=reply(
            [cell("a", table["chunk_id"], None, None, printed)],
            None if rows_too else "a",
            rows=[{"entity": "x", "expr": "a"}] if rows_too else None,
        ),
    )
    answer = env.arm.answer(make_case())
    assert bool(answer.numeric_values) is resolvable
    assert answer.refused is (not resolvable)
