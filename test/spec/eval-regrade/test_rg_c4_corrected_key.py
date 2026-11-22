"""C4: a corrected key changes exactly the cases it touches."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.regrade import regrade_report


def _regrade(source, cases, **kw):
    return regrade_report(
        source, cases, suite="xbrl_v2_dev", suite_version="2", corpus_fingerprint="pm:new", **kw
    )


@pytest.mark.criterion("C4")
def test_a_wrong_key_corrected_to_the_arms_value(lookup_case, answer, build_report,
                                                 as_json_source, rekey):
    # oracle: hand count. Before: d1 CORRECT, d2 INCORRECT (the key was wrong; the answer said
    # 2,400,000,000 shares), d3 REFUSED, d4 CORRECT. The key of d2 is corrected to the stated
    # value and the key of d4 moves off it, so after: d1 CORRECT, d2 CORRECT, d3 REFUSED,
    # d4 INCORRECT.
    cases = [
        lookup_case("d1", 100.0),
        lookup_case("d2", 2_350_000_000.0, concept="CommonStockSharesOutstanding"),
        lookup_case("d3", 300.0),
        lookup_case("d4", 400.0),
    ]
    source = as_json_source(build_report([
        (cases[0], answer([100.0])),
        (cases[1], answer([2_400_000_000.0])),
        (cases[2], answer([], refused=True, cited=False)),
        (cases[3], answer([400.0])),
    ]))
    new_cases = [cases[0], rekey(cases[1], 2_400_000_000.0), cases[2], rekey(cases[3], 450.0)]

    report = _regrade(source, new_cases)
    grades = {r.case.id: r.grade.grade.value for r in report.results}
    assert grades == {"d1": "CORRECT", "d2": "CORRECT", "d3": "REFUSED", "d4": "INCORRECT"}
    m = report.metrics
    # tolerance: exact rationals over n=4; abs=1e-12 only absorbs float division
    assert m.accuracy == pytest.approx(2 / 4, abs=1e-12)
    assert m.value_accuracy == pytest.approx(2 / 4, abs=1e-12)
    assert m.n_confident_wrong == 1
    assert m.silent_wrong_rate == pytest.approx(1 / 4, abs=1e-12)
    assert report.config["regrade"]["n_grade_changed"] == 2
    # the new key is what the re-graded case reports as expected
    d2 = next(r for r in report.results if r.case.id == "d2")
    # tolerance: the key is copied, not computed
    assert d2.grade.expected["value"] == pytest.approx(2_400_000_000.0, rel=1e-12)


@pytest.mark.criterion("C4")
@settings(max_examples=60)
@given(
    plan=st.lists(
        st.tuples(st.sampled_from(["right", "wrong", "refused"]), st.booleans()),
        min_size=1,
        max_size=10,
    )
)
def test_only_rekeyed_cases_move(lookup_case, answer, build_report, as_payload_source, rekey,
                                 plan):
    # oracle: requirement 8 and the grade ladder by hand — a case whose key moves onto the
    # stated value becomes CORRECT (a refusal stays REFUSED); every other case keeps its grade,
    # checks and grounding exactly.
    pairs, new_cases, expected = [], [], {}
    for i, (kind, moved) in enumerate(plan):
        key = 1000.0 + 10 * i
        case = lookup_case(f"k{i}", key)
        stated = {"right": [key], "wrong": [key + 5.0], "refused": []}[kind]
        ans = answer(stated, refused=kind == "refused", cited=kind != "refused")
        pairs.append((case, ans))
        if moved:
            new_cases.append(rekey(case, stated[0] if stated else key + 1.0))
            expected[case.id] = "REFUSED" if kind == "refused" else "CORRECT"
        else:
            new_cases.append(case)
    report = build_report(pairs)
    before = {r.case.id: r.grade for r in report.results}

    regraded = _regrade(as_payload_source(report), new_cases)
    for r in regraded.results:
        if r.case.id in expected:
            assert r.grade.grade.value == expected[r.case.id]
        else:
            old = before[r.case.id]
            assert r.grade.grade is old.grade
            assert r.grade.checks == old.checks
            assert r.grade.grounding_precision == old.grounding_precision
            assert r.grade.grounding_recall == old.grounding_recall
    changed = sum(
        1 for r in regraded.results if r.grade.grade is not before[r.case.id].grade
    )
    assert regraded.config["regrade"]["n_grade_changed"] == changed
    assert len(regraded.results) == len(plan)
