"""C6: judge scores are carried only while the reference text they scored is unchanged."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.regrade import regrade_report

SCORES = {"correctness": 0.75, "completeness": 0.5, "citation_support": 1.0,
          "unsupported_claims": 1}


def _regrade(source, cases):
    return regrade_report(
        source, cases, suite="xbrl_v2_dev", suite_version="2", corpus_fingerprint="pm:new"
    )


def _with_text(case: EvalCase, text: str | None, key_points: list[str] | None = None) -> EvalCase:
    data = case.to_dict()
    data["expected_text"] = text
    if key_points is not None:
        data["key_points"] = key_points
    return EvalCase.from_dict(data)


@pytest.fixture()
def judged(text_case, lookup_case, answer, build_report):
    cases = [
        text_case("t1", "Supply concentration in Taiwan."),
        text_case("t2", "Customer concentration."),
        lookup_case("n1", 100.0),
    ]
    report = build_report(
        [(cases[0], answer([], text="Taiwan supply.")),
         (cases[1], answer([], text="Customers.")),
         (cases[2], answer([100.0]))],
        judge={"t1": SCORES, "t2": SCORES},
    )
    return cases, report


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("form", ["json", "payload"])
def test_unchanged_reference_text_keeps_the_scores(judged, as_json_source, as_payload_source,
                                                   form):
    # oracle: requirement 5 — unchanged expected text, scores carried exactly
    cases, report = judged
    source = as_json_source(report) if form == "json" else as_payload_source(report)
    out = _regrade(source, cases)
    by_id = {r.case.id: r for r in out.results}
    assert by_id["t1"].grade.judge_scores == SCORES
    assert by_id["t2"].grade.judge_scores == SCORES
    assert by_id["n1"].grade.judge_scores is None
    assert out.config["regrade"]["judge_stale"] == []
    assert out.config["regrade"]["judge_rerun"] is False


@pytest.mark.criterion("C6")
def test_changed_text_or_no_longer_judged_drops_and_lists(judged, as_payload_source):
    # oracle: requirement 5 — t1's reference text changed; t2 lost its key points, so it is
    # no longer text-graded. Both are stale, listed sorted, and keep no scores.
    cases, report = judged
    new_cases = [
        _with_text(cases[0], "Supply concentration in Taiwan and Korea."),
        _with_text(cases[1], "Customer concentration.", key_points=[]),
        cases[2],
    ]
    out = _regrade(as_payload_source(report), new_cases)
    by_id = {r.case.id: r for r in out.results}
    assert by_id["t1"].grade.judge_scores is None
    assert by_id["t2"].grade.judge_scores is None
    assert out.config["regrade"]["judge_stale"] == ["t1", "t2"]
    assert out.to_dict()["results"][0]["judge_correctness"] is None


@pytest.mark.criterion("C6")
@given(edits=st.lists(st.booleans(), min_size=1, max_size=6))
def test_stale_set_is_exactly_the_edited_texts(text_case, answer, build_report,
                                               as_json_source, edits):
    # oracle: requirement 5 by hand — the stale ids are exactly those whose text was edited
    cases = [text_case(f"x{i}", f"Reference {i}.") for i in range(len(edits))]
    report = build_report(
        [(c, answer([], text="An answer.")) for c in cases],
        judge={c.id: SCORES for c in cases},
    )
    new_cases = [
        _with_text(c, f"Reference {i}, corrected.") if edited else c
        for i, (c, edited) in enumerate(zip(cases, edits, strict=True))
    ]
    out = _regrade(as_json_source(report), new_cases)
    stale = sorted(c.id for c, edited in zip(cases, edits, strict=True) if edited)
    assert out.config["regrade"]["judge_stale"] == stale
    for r in out.results:
        assert (r.grade.judge_scores is None) == (r.case.id in stale)
