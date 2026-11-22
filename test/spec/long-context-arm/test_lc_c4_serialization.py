"""C4: calls on one filing are serialized; calls on different filings run together.

Each test starts its answers together from separate threads (a start barrier) and
watches the provider boundary with the conftest's ``OverlapAdapter``
(the ``overlap_adapter`` factory), which counts the calls
in flight per filing and in total:

- *Different filings*: the adapter's calls wait on a ``threading.Barrier(2)`` with a
  bounded timeout. Only two calls in flight together can pass it, so an arm that
  serializes across filings breaks the barrier (the call errors) instead of hanging.
- *Same filing*: the adapter holds each call open for up to ``overlap_wait_s`` waiting
  for a second call to arrive, then returns normally either way. A correct arm never
  has two same-filing calls in flight, so nothing here depends on a barrier releasing;
  an arm that does not serialize shows a per-filing count of 2 or more.

Every thread is a daemon joined with a timeout, so a deadlocking arm fails the test
rather than hanging the run.
"""

from __future__ import annotations

import threading

import pytest

from src.eval.core.case import EvalCase

AAA_KEY = "AAA/2024/10-K/canonical.htm"
BBB_KEY = "BBB/2024/10-K/canonical.htm"
AAA_MARK = "Zqmark AAA filing"
BBB_MARK = "Zqmark BBB filing"
MARKERS = (AAA_MARK, BBB_MARK)
JOIN_TIMEOUT_S = 60.0
BARRIER_TIMEOUT_S = 10.0


def _case(question: str, ticker: str, case_id: str) -> EvalCase:
    return EvalCase(id=case_id, question=question, kind="lookup", ticker=ticker, fy=2024)


def _two_filing_arm(make_arm, xhtml, reply, adapter):
    keys = {("AAA", 2024): AAA_KEY, ("BBB", 2024): BBB_KEY}
    objects = {
        AAA_KEY: xhtml(f"<p>{AAA_MARK}</p><p>Revenue was 7 widgets</p>"),
        BBB_KEY: xhtml(f"<p>{BBB_MARK}</p><p>Revenue was 9 widgets</p>"),
    }
    arm, _, _, _ = make_arm(reply_text=reply(7.0), keys=keys, objects=objects, adapter=adapter)
    return arm


def _answer_together(arm, cases: list[EvalCase]) -> list:
    """Answers *cases* from one thread each, released together; re-raises any error."""
    start = threading.Barrier(len(cases), timeout=BARRIER_TIMEOUT_S)
    answers: list = [None] * len(cases)
    errors: list[BaseException] = []

    def run(i: int) -> None:
        try:
            start.wait()
            answers[i] = arm.answer(cases[i])
        except BaseException as exc:  # handed back to the test thread below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(cases))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(JOIN_TIMEOUT_S)
    if errors:
        raise errors[0]
    assert not any(t.is_alive() for t in threads), "an answer did not finish in time"
    return answers


def _assert_one_call_each(adapter, answers, cases) -> None:
    assert adapter.calls == len(cases)
    for ans in answers:
        assert ans is not None and ans.error is None
    for case in cases:
        sent = [i for i in range(adapter.calls) if case.question in adapter.user_text(i)]
        assert len(sent) == 1


@pytest.mark.criterion("C4")
def test_same_filing_never_has_two_calls_in_flight(make_arm, xhtml, reply, overlap_adapter):
    # oracle: requirement 5 — three answers on one filing started together: at most one
    # provider call on that filing is in flight at any time, and each answer makes
    # exactly one call
    adapter = overlap_adapter(reply(7.0), MARKERS, overlap_wait_s=1.0)
    arm = _two_filing_arm(make_arm, xhtml, reply, adapter)
    cases = [
        _case(f"What was AAA Wibble{i} revenue in fiscal 2024?", "AAA", f"s{i}")
        for i in range(3)
    ]
    answers = _answer_together(arm, cases)
    _assert_one_call_each(adapter, answers, cases)
    assert adapter.max_per_filing[AAA_MARK] == 1
    assert adapter.max_total == 1


@pytest.mark.criterion("C4")
def test_different_filings_are_in_flight_together(make_arm, xhtml, reply, overlap_adapter):
    # oracle: requirement 5 — two answers on two filings: both calls pass a barrier only
    # two concurrent calls can pass (a serializing arm breaks it after its timeout)
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_S)
    adapter = overlap_adapter(reply(7.0), MARKERS, barrier=barrier)
    arm = _two_filing_arm(make_arm, xhtml, reply, adapter)
    cases = [
        _case("What was AAA Wibble revenue in fiscal 2024?", "AAA", "d1"),
        _case("What was BBB Frobnic revenue in fiscal 2024?", "BBB", "d2"),
    ]
    answers = _answer_together(arm, cases)
    assert adapter.passed_barrier == 2
    assert not barrier.broken
    _assert_one_call_each(adapter, answers, cases)
    assert adapter.max_total == 2
    assert adapter.max_per_filing[AAA_MARK] == 1
    assert adapter.max_per_filing[BBB_MARK] == 1


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@pytest.mark.parametrize("questions_per_filing", [2, 3])
def test_spreading_answers_over_filings_only_adds_cross_filing_overlap(
    make_arm, xhtml, reply, overlap_adapter, questions_per_filing
):
    # oracle: requirement 5 — the same questions asked all on one filing never overlap;
    # split over two filings, the two filings' calls overlap (max 2 in flight) while each
    # filing still has at most one call in flight; one call per answer either way
    n = 2 * questions_per_filing
    questions = [f"What was Wibble{i} revenue in fiscal 2024?" for i in range(n)]

    one_adapter = overlap_adapter(reply(7.0), MARKERS, overlap_wait_s=0.5)
    one_arm = _two_filing_arm(make_arm, xhtml, reply, one_adapter)
    one_cases = [_case(q, "AAA", f"m1-{i}") for i, q in enumerate(questions)]
    _assert_one_call_each(one_adapter, _answer_together(one_arm, one_cases), one_cases)
    assert one_adapter.max_per_filing[AAA_MARK] == 1
    assert one_adapter.max_total == 1

    two_adapter = overlap_adapter(reply(7.0), MARKERS, overlap_wait_s=1.0)
    two_arm = _two_filing_arm(make_arm, xhtml, reply, two_adapter)
    two_cases = [
        _case(q, "AAA" if i % 2 == 0 else "BBB", f"m2-{i}") for i, q in enumerate(questions)
    ]
    _assert_one_call_each(two_adapter, _answer_together(two_arm, two_cases), two_cases)
    assert two_adapter.max_per_filing[AAA_MARK] == 1
    assert two_adapter.max_per_filing[BBB_MARK] == 1
    assert two_adapter.max_total == 2
