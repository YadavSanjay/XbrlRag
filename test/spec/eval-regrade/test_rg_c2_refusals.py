"""C2: a re-grade that cannot match each stored answer to the same question refuses."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.regrade import RegradeRefused, regrade_report


def _regrade(source, cases, **kw):
    return regrade_report(
        source, cases, suite="xbrl_v2_dev", suite_version="2", corpus_fingerprint="pm:new", **kw
    )


@pytest.fixture()
def two_case_source(lookup_case, answer, build_report, as_json_source):
    cases = [lookup_case("a1", 100.0), lookup_case("a2", 200.0)]
    report = build_report([(cases[0], answer([100.0])), (cases[1], answer([250.0]))])
    return cases, as_json_source(report)


@pytest.mark.criterion("C2")
def test_empty_results_refuse(two_case_source):
    # oracle: requirement 3(a); RegradeRefused is a ValueError (requirement 3)
    cases, source = two_case_source
    source["results"] = []
    with pytest.raises(ValueError):
        _regrade(source, cases)
    with pytest.raises(RegradeRefused):
        _regrade(source, cases)
    del source["results"]
    with pytest.raises(RegradeRefused):
        _regrade(source, cases)


@pytest.mark.criterion("C2")
def test_a_case_missing_from_the_suite_refuses_naming_it(two_case_source):
    # oracle: requirement 3(b) — never a silent skip
    cases, source = two_case_source
    with pytest.raises(RegradeRefused, match="a2"):
        _regrade(source, cases[:1])


@pytest.mark.criterion("C2")
def test_a_changed_question_refuses_naming_the_case(two_case_source):
    # oracle: requirement 3(c) — exact string equality of the question
    cases, source = two_case_source
    moved = EvalCase.from_dict({**cases[1].to_dict(), "question": cases[1].question + " "})
    with pytest.raises(RegradeRefused, match="a2"):
        _regrade(source, [cases[0], moved])


@pytest.mark.criterion("C2")
def test_a_duplicated_result_refuses(two_case_source):
    # oracle: requirement 3(d)
    cases, source = two_case_source
    source["results"].append(dict(source["results"][0]))
    with pytest.raises(RegradeRefused, match="a1"):
        _regrade(source, cases)


@pytest.mark.criterion("C2")
def test_a_result_without_produced_refuses(two_case_source):
    # oracle: requirement 3(e)
    cases, source = two_case_source
    del source["results"][1]["produced"]
    with pytest.raises(RegradeRefused, match="a2"):
        _regrade(source, cases)


@pytest.mark.criterion("C2")
def test_a_split_mismatch_refuses(lookup_case, answer, build_report, as_json_source):
    # oracle: requirement 3(f) — a dev run never re-grades against validation cases
    dev = [lookup_case("a1", 100.0)]
    source = as_json_source(build_report([(dev[0], answer([100.0]))]))
    validation = [lookup_case("a1", 100.0, split="validation")]
    with pytest.raises(RegradeRefused, match="split"):
        _regrade(source, validation)


@pytest.mark.criterion("C2")
def test_mixed_splits_in_the_matched_cases_refuse(lookup_case, answer, build_report,
                                                  as_json_source):
    # oracle: requirement 3(f)
    cases = [lookup_case("a1", 100.0), lookup_case("a2", 200.0)]
    source = as_json_source(
        build_report([(cases[0], answer([100.0])), (cases[1], answer([200.0]))])
    )
    mixed = [cases[0], lookup_case("a2", 200.0, split="heldout_filers")]
    with pytest.raises(RegradeRefused):
        _regrade(source, mixed)


@pytest.mark.criterion("C2")
def test_reusing_the_source_run_id_refuses(two_case_source):
    # oracle: requirement 3(g) — a re-grade never overwrites the run it read
    cases, source = two_case_source
    with pytest.raises(RegradeRefused, match="eval-aaaaaaaaaaaa"):
        _regrade(source, cases, run_id="eval-aaaaaaaaaaaa")


@pytest.mark.criterion("C2")
def test_extra_suite_cases_are_ignored(two_case_source, lookup_case):
    # oracle: requirement 3 — cases with no result are neither graded nor planned
    cases, source = two_case_source
    report = _regrade(source, [*cases, lookup_case("zz", 1.0)])
    assert sorted(r.case.id for r in report.results) == ["a1", "a2"]
    assert report.n_planned == 2


@pytest.mark.criterion("C2")
@given(position=st.integers(min_value=0, max_value=200), char=st.characters(
    min_codepoint=33, max_codepoint=126))
def test_any_single_character_change_in_a_question_refuses(
    lookup_case, answer, build_report, as_json_source, position, char
):
    # oracle: requirement 3(c) — any edit of the stored question text is a different question
    case = lookup_case("q1", 100.0)
    source = as_json_source(build_report([(case, answer([100.0]))]))
    q = case.question
    i = position % len(q)
    changed = q[:i] + char + q[i + 1:] if q[i] != char else q[:i] + char + char + q[i + 1:]
    moved = EvalCase.from_dict({**case.to_dict(), "question": changed})
    with pytest.raises(RegradeRefused, match="q1"):
        _regrade(source, [moved])
