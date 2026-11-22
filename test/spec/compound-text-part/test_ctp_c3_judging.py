"""C3: a compound's text part is judged by the runner (requirement 3).

The judge is real, on a real gateway whose provider adapter is faked (the LLM boundary); the
fake covers every key point. The system under test is faked (the pipeline boundary).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.judge_wiring import is_text_graded, run_graded
from src.eval.core.runner import RunnerConfig


@pytest.mark.criterion("C3")
def test_is_text_graded(kit):
    # oracle: requirement 3 — true for a compound with a text part; unchanged otherwise
    assert is_text_graded(kit.case(kit.compound())) is True
    assert is_text_graded(kit.case(kit.compound(kit.trend_fields()))) is False
    assert is_text_graded(kit.case(kit.standalone_text())) is True
    assert is_text_graded(kit.case(kit.screen())) is False


def _run(kit, cases):
    judge, adapter = kit.make_judge()
    system = kit.FakeSystem({c.id: kit.answer(values=[250 * kit.M, 300 * kit.M],
                                              fact_ids=[1, 2, 34]) for c in cases})
    report = run_graded(cases, system, judge=judge,
                        generator_profile=kit.GENERATOR_PROFILE,
                        config=RunnerConfig(max_workers=1, progress_every=0),
                        metamorphic=False)
    return report, adapter


@pytest.mark.criterion("C3")
def test_runner_judges_the_text_part_once(kit):
    # oracle: requirement 3 — one judge call for the text compound, none for the trend
    # compound; the payload is the compound's question with the text part's reference
    # answer and key points in order; the grade is C2's
    text_c = kit.case(kit.compound(cid="c01"))
    trend_c = kit.case(kit.compound(kit.trend_fields(), cid="c02"))
    report, adapter = _run(kit, [text_c, trend_c])

    assert len(adapter.payloads) == 1
    payload = adapter.payloads[0]
    assert payload["question"] == kit.QUESTION
    assert payload["reference_answer"] == kit.REFERENCE
    assert payload["key_points"] == [{"index": 0, "text": kit.KEY_POINTS[0]},
                                     {"index": 1, "text": kit.KEY_POINTS[1]}]

    by_id = {r.case.id: r for r in report.results}
    judged = by_id["hx-c01"]
    assert judged.grade.judge_scores is not None
    assert judged.grade.grade is Grade.CORRECT
    assert by_id["hx-c02"].grade.judge_scores is None


@pytest.mark.criterion("C3")
@settings(max_examples=10, deadline=None)  # each example is one fake-judged run
@given(points=st.lists(st.text(alphabet="abcdefgh ", min_size=1, max_size=12)
                       .filter(lambda s: s.strip()), min_size=1, max_size=3))
def test_payload_lists_exactly_the_key_points(kit, points):
    # oracle: requirement 3 — the judge gets the text part's key points, in order
    c = kit.case(kit.compound(kit.text_fields(key_points=points)))
    _, adapter = _run(kit, [c])
    assert len(adapter.payloads) == 1
    assert [p["text"] for p in adapter.payloads[0]["key_points"]] == points
    assert [p["index"] for p in adapter.payloads[0]["key_points"]] == list(range(len(points)))
