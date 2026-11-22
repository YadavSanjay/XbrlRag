"""C4: one judge call gives the judge's labels for every label key."""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import judge_answer_labels
from src.llm.run_budget import RunBudget, RunHalt
from src.observability.trace_context import trace_query


def _answer(answer, generator_model="deepseek-flash"):
    return answer(
        "a1",
        claims=[
            ("s0", "Revenue grew.", ["Revenue grew 5%."]),
            ("s1", "Margins fell.", ["Margins fell 1pt."]),
            ("s2", "Prices fell.", []),
        ],
        key_points=["revenue grew", "margins fell", "cash rose"],
        generator_model=generator_model,
    )


@pytest.mark.criterion("C4")
def test_labels_from_one_call(answer, make_judge, reply, env):
    # oracle: requirement 7 and eval-judge requirement 3 - the reply's verdicts, keyed by
    # requirement 3's keys; the uncited s2 gets no supported key; cost from pricing.yaml
    text = reply(
        [("s1", False, True), ("s0", True, False), ("s2", True, True)], [True, False, True]
    )
    judge, adapter = make_judge(text)
    got = judge_answer_labels(judge, _answer(answer))
    assert adapter.calls == 1
    assert adapter.stages == ["eval_judge"]
    assert got.answer_id == "a1"
    assert got.error is None
    assert got.judge_version == f"v1:{env.JUDGE_MODEL}"
    assert dict(got.labels) == {
        ("a1", "correct", "s0"): True,
        ("a1", "supported", "s0"): False,
        ("a1", "correct", "s1"): False,
        ("a1", "supported", "s1"): True,
        ("a1", "correct", "s2"): True,
        ("a1", "covered", "kp0"): True,
        ("a1", "covered", "kp1"): False,
        ("a1", "covered", "kp2"): True,
    }
    rates = env.pricing_row(env.JUDGE_MODEL)
    cost = (
        env.PROMPT_TOKENS / 1000 * rates["input"]
        + env.COMPLETION_TOKENS / 1000 * rates["output"]
    )
    # tolerance: a few float products of the pricing rows
    assert got.cost_usd == pytest.approx(cost, rel=1e-9)


@pytest.mark.criterion("C4")
def test_malformed_reply_and_family_rule(answer, make_judge, reply):
    # oracle: requirement 7 and eval-judge requirement 5 - a missing claim entry is a
    # judge_error with no labels; a same-family generator is refused before any call
    judge, adapter = make_judge(reply([("s0", True, True)], [True, True, True]))
    got = judge_answer_labels(judge, _answer(answer))
    assert got.error is not None and got.error.startswith("judge_error: ")
    assert dict(got.labels) == {}
    assert adapter.calls == 1

    judge, adapter = make_judge(reply([], []))
    with pytest.raises(ValueError):
        judge_answer_labels(judge, _answer(answer, generator_model="claude-sonnet-5-5"))
    assert adapter.calls == 0


@pytest.mark.criterion("C4")
def test_run_halt_propagates(answer, make_judge, reply):
    # oracle: requirement 7 - RunHalt is never turned into a judge_error
    text = reply([("s0", True, True), ("s1", True, True), ("s2", True, True)], [True] * 3)
    judge, adapter = make_judge(text, run_budget=RunBudget(max_llm_calls=0))
    with trace_query("judge-calibration spec"):
        with pytest.raises(RunHalt):
            judge_answer_labels(judge, _answer(answer))
    assert adapter.calls == 0


@pytest.mark.criterion("C4")
@given(
    st.lists(st.tuples(st.booleans(), st.booleans(), st.booleans()), min_size=1, max_size=5),
    st.lists(st.booleans(), min_size=1, max_size=4),
)
def test_labels_aggregate_to_the_judge_scores(answer, make_judge, reply, claims, covered):
    # oracle: requirement 7 - the labels are the verdicts ClaimJudge.judge aggregates, so
    # the eval-judge ratios recomputed from them equal judge()'s on the same reply
    ans = answer(
        "a1",
        claims=[
            (f"s{i}", f"claim {i}", ["p"] if cited else [])
            for i, (cited, _, _) in enumerate(claims)
        ],
        key_points=[f"kp {j}" for j in range(len(covered))],
    )
    text = reply([(f"s{i}", c, s) for i, (_, c, s) in enumerate(claims)], covered)
    judge, _ = make_judge(text)
    labels = judge_answer_labels(judge, ans).labels
    result = judge.judge(ans.question, ans.reference_answer, list(ans.key_points), list(ans.claims))
    n, k = len(claims), len(covered)
    correct = sum(labels[("a1", "correct", f"s{i}")] for i in range(n))
    supported = sum(labels.get(("a1", "supported", f"s{i}"), False) for i in range(n))
    cov = sum(labels[("a1", "covered", f"kp{j}")] for j in range(k))
    # tolerance: the same ratios of small integers
    assert result.correctness == pytest.approx(float(Fraction(correct, n)), abs=1e-12)
    assert result.citation_support == pytest.approx(float(Fraction(supported, n)), abs=1e-12)
    assert result.completeness == pytest.approx(float(Fraction(cov, k)), abs=1e-12)
    assert set(labels) == {("a1", "correct", f"s{i}") for i in range(n)} | {
        ("a1", "supported", f"s{i}") for i, (cited, _, _) in enumerate(claims) if cited
    } | {("a1", "covered", f"kp{j}") for j in range(k)}
