"""C10: the calculator, failures as results, and untrusted arguments."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import AAA_10K, facts_url

from src.eval.sec_tools.toolbox import calculate
from src.llm.run_budget import RunBudgetExceeded

FILING = {"cik": "1", "accession": AAA_10K}
FRAME = {"taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD", "period": "CY2023"}


@pytest.mark.criterion("C10")
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("(391035 - 383285) / 383285", 7750 / 383285),
        ("2 ** 10", 1024.0),
        ("-3 + 5 * 2", 7.0),
        ("median(1, 3, 2)", 2.0),
        ("median(1, 2, 3, 4)", 2.5),
        ("sum(1, 2, 3)", 6.0),
        ("min(4, 2)", 2.0),
        ("max(4, 2)", 4.0),
        ("abs(-2.5)", 2.5),
        ("round(1.2345, 2)", 1.23),
        ("(150 / 100) ** (1 / 2) - 1", 1.5**0.5 - 1),
    ],
)
def test_the_arithmetic(expression, expected):
    # oracle: each value computed by hand or by Python in this file
    # tolerance: float arithmetic
    assert calculate(expression) == pytest.approx(expected, rel=1e-12)


@pytest.mark.criterion("C10")
@pytest.mark.parametrize(
    "expression",
    [
        "",
        "a + 1",
        "__import__('os')",
        "(1).real",
        "open('x')",
        "[1, 2]",
        "'a' * 3",
        "1 / 0",
        "2 ** 65",
        "9 ** 999",
        "lambda: 1",
        "1" + " + 1" * 700,
    ],
)
def test_what_it_refuses(expression):
    # oracle: requirement 15's list
    with pytest.raises(ValueError):
        calculate(expression)


@pytest.mark.criterion("C10")
def test_a_refused_expression_is_an_outcome(make_toolbox):
    # oracle: requirement 16 — the model reads it and carries on
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("calculate", {"expression": "1 / 0"})
    assert outcome.error_code == "bad_arguments"
    assert outcome.payload["error"] == "bad_arguments"
    assert outcome.payload["message"]


@pytest.mark.criterion("C10")
def test_an_unknown_tool_is_an_outcome(make_toolbox):
    # oracle: requirement 16
    toolbox, sec = make_toolbox()
    outcome = toolbox.execute("shell", {"command": "ls"})
    assert outcome.error_code == "unknown_tool"
    assert sec.calls == []


@pytest.mark.criterion("C10")
def test_an_upstream_failure_is_an_outcome(make_toolbox, fake_sec):
    # oracle: requirement 16 — retries used up on the fake transport
    sec = fake_sec()
    sec.script[facts_url("0000000001")] = [(500, b"")] * 9
    toolbox, _ = make_toolbox(sec)
    outcome = toolbox.execute("list_concepts", {"cik": "1"})
    assert outcome.error_code == "upstream"


@pytest.mark.criterion("C10")
def test_a_transport_exception_is_an_outcome(make_toolbox, fake_sec):
    # oracle: requirement 16
    sec = fake_sec()
    sec.error = ConnectionError("reset")
    toolbox, _ = make_toolbox(sec)
    assert toolbox.execute("list_concepts", {"cik": "1"}).error_code == "upstream"


@pytest.mark.criterion("C10")
def test_a_frozen_miss_is_an_outcome(make_toolbox):
    # oracle: requirements 10, 16
    toolbox, sec = make_toolbox(frozen=True)
    assert toolbox.execute("list_concepts", {"cik": "1"}).error_code == "not_in_snapshot"
    assert sec.calls == []


@pytest.mark.criterion("C10")
def test_a_run_halt_is_not_swallowed(make_toolbox, fake_sec):
    # oracle: requirement 16 and the repo's RAG rules — a RunHalt is never caught
    sec = fake_sec()
    sec.error = RunBudgetExceeded("stop")
    toolbox, _ = make_toolbox(sec)
    with pytest.raises(RunBudgetExceeded):
        toolbox.execute("list_concepts", {"cik": "1"})


@pytest.mark.criterion("C10")
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_filings", None),
        ("list_filings", {}),
        ("list_filings", {"cik": "abc"}),
        ("list_filings", {"cik": "12345678901"}),
        ("list_filings", {"cik": "1", "filed_from": "20240101"}),
        ("list_filings", {"cik": "1", "limit": -1}),
        ("list_filings", {"cik": "1", "offset": "x"}),
        ("list_concepts", {"cik": "1/../2"}),
        ("get_concept", {"cik": "1", "taxonomy": "../..", "tag": "Revenues"}),
        ("get_concept", {"cik": "1", "taxonomy": "us-gaap", "tag": "a/b"}),
        ("get_frame", {**FRAME, "tag": "a?b=c"}),
        ("get_frame", {**FRAME, "period": "../CY2023"}),
        ("get_frame", {**FRAME, "order": "sideways"}),
        ("read_filing", {"cik": "1", "accession": "x"}),
        ("read_filing", {"cik": "1", "accession": "0000000001-24"}),
        ("read_filing", {**FILING, "document": ".."}),
        ("read_filing", {**FILING, "document": "a/b.htm"}),
        ("read_filing", {**FILING, "document": "a\\b.htm"}),
        ("read_filing", {**FILING, "document": "..%2f..%2fetc"}),
        ("search_filing", FILING),
        ("search_filing", {**FILING, "query": ""}),
        ("search_filings_text", {}),
        ("search_filings_text", {"query": "x", "page": 0}),
        ("find_company", {}),
        ("find_company", {"tickers": ["A"] * 201}),
    ],
)
def test_a_bad_argument_sends_no_request(make_toolbox, tool, arguments):
    # oracle: requirement 17's rules
    toolbox, sec = make_toolbox()
    outcome = toolbox.execute(tool, arguments)
    assert outcome.error_code == "bad_arguments"
    assert sec.calls == []


@pytest.mark.criterion("C10")
@given(
    document=st.builds(
        lambda head, sep, tail: f"{head}{sep}{tail}",
        st.text(alphabet="abc.", max_size=6),
        st.sampled_from(["/", "\\", "../", "..\\", "%2f", "%5c"]),
        st.text(alphabet="abc.", max_size=6),
    )
)
def test_a_path_is_never_a_document(make_toolbox, document):
    # oracle: requirement 17 — a single file name, no separator and no ..
    toolbox, sec = make_toolbox()
    outcome = toolbox.execute("read_filing", {**FILING, "document": document})
    assert outcome.error_code == "bad_arguments"
    assert sec.calls == []


_TREE = st.recursive(
    st.integers(-1000, 1000),
    lambda inner: st.tuples(st.sampled_from(["+", "-", "*"]), inner, inner),
    max_leaves=12,
)


def _text(tree) -> str:
    if isinstance(tree, int):
        return f"({tree})"
    op, left, right = tree
    return f"({_text(left)} {op} {_text(right)})"


def _value(tree) -> int:
    if isinstance(tree, int):
        return tree
    op, left, right = tree
    a, b = _value(left), _value(right)
    return a + b if op == "+" else a - b if op == "-" else a * b


@pytest.mark.criterion("C10")
@given(_TREE)
def test_a_random_tree_equals_the_value_computed_here(tree):
    # oracle: the tree's value, computed in this file by recursion on integers
    # tolerance: a float result of an integer computation
    assert calculate(_text(tree)) == pytest.approx(float(_value(tree)), rel=1e-9, abs=1e-9)
