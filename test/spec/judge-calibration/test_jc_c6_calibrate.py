"""C6: calibrating one judge version, the pass rule and the bridge to the gate."""

from __future__ import annotations

import math
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge import scores_reportable
from src.eval.core.graders.judge_calibration import JudgeLabels, as_report, calibrate

VERSION = "v1:claude-haiku-5-5"
DIMS = ("correct", "supported", "covered")


def _pairs(tt: int, tf: int, ft: int, ff: int) -> list[tuple[bool, bool]]:
    """(judge, human) label pairs of a 2x2 table, judge's label first."""
    return (
        [(True, True)] * tt + [(True, False)] * tf + [(False, True)] * ft + [(False, False)] * ff
    )


def _kappa(pairs) -> float | None:
    """Cohen's kappa by hand (requirement 8): None when p_e == 1."""
    n = len(pairs)
    p_o = Fraction(sum(a == b for a, b in pairs), n)
    pj = Fraction(sum(a for a, _ in pairs), n)
    ph = Fraction(sum(b for _, b in pairs), n)
    p_e = pj * ph + (1 - pj) * (1 - ph)
    return None if p_e == 1 else float((p_o - p_e) / (1 - p_e))


def _build(answer, correct, supported, covered, *, cost=0.01):
    """Answers (one claim, one key point each) and both raters' labels.

    Answer i's correct/covered labels are item i of those pair lists; its claim is cited
    (so it has a supported label) only while i < len(supported).
    """
    n = len(correct)
    answers, human, judged = [], {}, []
    for i in range(n):
        aid = f"a{i:03d}"
        cited = i < len(supported)
        claims = [("s0", "c", ["p"] if cited else [])]
        answers.append(answer(aid, claims=claims, key_points=["k"]))
        pairs = {"correct": correct[i], "covered": covered[i]}
        if cited:
            pairs["supported"] = supported[i]
        keys = {"correct": "s0", "supported": "s0", "covered": "kp0"}
        jl = {}
        for dim, (j, h) in pairs.items():
            human[(aid, dim, keys[dim])] = h
            jl[(aid, dim, keys[dim])] = j
        judged.append(
            JudgeLabels(answer_id=aid, judge_version=VERSION, labels=jl, error=None, cost_usd=cost)
        )
    return answers, human, judged


@pytest.mark.criterion("C6")
def test_kappas_and_the_pass_rule(answer):
    # oracle: hand-computed tables - correct (24,1,1,24): p_o 0.96, p_e 0.5, kappa 0.92;
    # supported (25,0,0,25): kappa 1; covered (20,5,10,15): Wikipedia's 0.4
    answers, human, judged = _build(
        answer, _pairs(24, 1, 1, 24), _pairs(25, 0, 0, 25), _pairs(20, 5, 10, 15)
    )
    r = calibrate(answers, human, judged, threshold=0.7, min_answers=50)
    assert r.judge_version == VERSION
    assert r.model == "claude-haiku-5-5"
    assert r.family == "anthropic"
    assert (r.n_answers, r.n_judge_errors) == (50, 0)
    # tolerance: exact ratios through a few float operations
    assert r.dimensions["correct"].kappa == pytest.approx(0.92, abs=1e-12)
    assert r.dimensions["supported"].kappa == pytest.approx(1.0, abs=1e-12)
    assert r.dimensions["covered"].kappa == pytest.approx(0.4, abs=1e-12)
    assert all(r.dimensions[d].n == 50 for d in DIMS)
    assert r.passed is False  # covered 0.4 < 0.7: every dimension must pass
    assert r.cost_usd == pytest.approx(0.5, abs=1e-9)
    report = as_report(r)
    assert report.kappa == pytest.approx(0.4, abs=1e-12)
    assert (report.n, report.min_items, report.passed) == (50, 50, False)
    # tolerance: the threshold is copied, not computed
    assert report.threshold == pytest.approx(0.7, abs=1e-15)
    assert scores_reportable(VERSION, [report]) is False

    passing = _build(answer, _pairs(24, 1, 1, 24), _pairs(25, 0, 0, 25), _pairs(24, 1, 1, 24))
    good = calibrate(*passing, min_answers=50)
    # tolerance: the default threshold, copied
    assert good.threshold == pytest.approx(0.7, abs=1e-15)
    assert good.passed is True
    assert scores_reportable(VERSION, [as_report(good)]) is True
    # the default min_answers is 100 answers: 50 are too few
    assert calibrate(*passing).passed is False


@pytest.mark.criterion("C6")
def test_dimension_item_floor_and_judge_errors(answer):
    # oracle: requirement 9 - only 40 cited claims: supported has n = 40 < min_answers 50
    answers, human, judged = _build(
        answer, _pairs(24, 1, 1, 24), _pairs(20, 0, 0, 20), _pairs(24, 1, 1, 24)
    )
    r = calibrate(answers, human, judged, min_answers=50)
    assert r.dimensions["supported"].n == 40
    assert r.passed is False
    # a judge_error answer is left out of every dimension and counted; its cost still counts
    answers, human, judged = _build(
        answer, _pairs(24, 1, 1, 24), _pairs(25, 0, 0, 25), _pairs(24, 1, 1, 24)
    )
    extra = answer("zzz", claims=[("s0", "c", ["p"])], key_points=["k"])
    human.update(
        {
            ("zzz", "correct", "s0"): True,
            ("zzz", "supported", "s0"): False,
            ("zzz", "covered", "kp0"): True,
        }
    )
    err = JudgeLabels(
        answer_id="zzz", judge_version=VERSION, labels={}, error="judge_error: bad", cost_usd=0.01
    )
    r = calibrate([*answers, extra], human, [*judged, err], min_answers=50)
    assert (r.n_answers, r.n_judge_errors) == (50, 1)
    assert all(r.dimensions[d].n == 50 for d in DIMS)
    # tolerance: exact ratios through a few float operations
    assert r.dimensions["correct"].kappa == pytest.approx(0.92, abs=1e-12)
    assert r.cost_usd == pytest.approx(0.51, abs=1e-9)
    assert r.passed is True


@pytest.mark.criterion("C6")
def test_inconsistent_inputs_raise(answer):
    # oracle: requirement 9
    two = _pairs(2, 0, 0, 2)
    answers, human, judged = _build(answer, two, two, two)
    missing_human = dict(human)
    missing_human.pop(next(iter(human)))
    with pytest.raises(ValueError):
        calibrate(answers, missing_human, judged)
    with pytest.raises(ValueError):
        calibrate(answers, {**human, ("a000", "covered", "kp9"): True}, judged)
    with pytest.raises(ValueError):
        calibrate(answers, human, judged[:-1])
    other = JudgeLabels(
        answer_id=judged[0].answer_id, judge_version="v1:gpt-6-luna",
        labels=dict(judged[0].labels), error=None, cost_usd=0.0,
    )
    with pytest.raises(ValueError):
        calibrate(answers, human, [other, *judged[1:]])
    wrong_keys = JudgeLabels(
        answer_id=judged[0].answer_id, judge_version=VERSION,
        labels={("a000", "correct", "s0"): True}, error=None, cost_usd=0.0,
    )
    with pytest.raises(ValueError):
        calibrate(answers, human, [wrong_keys, *judged[1:]])



@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.integers(4, 30), st.data())
def test_pass_rule_closed_form_and_order(answer, n, data):
    # oracle: requirement 9 closed form, kappas computed by hand in the test; permuting the
    # answers and the judge labels changes nothing
    pair = st.tuples(st.booleans(), st.booleans())

    def mixed(size: int) -> list[tuple[bool, bool]]:
        # judge-calibration-one-class: a one-class human dimension takes the agreement gate,
        # so this test (today's kappa rule) gives every non-empty dimension both human labels
        rest = data.draw(st.lists(pair, min_size=size - 2, max_size=size - 2))
        return [(data.draw(st.booleans()), True), (data.draw(st.booleans()), False), *rest]

    correct = mixed(n)
    covered = mixed(n)
    n_supported = data.draw(st.sampled_from([0, *range(2, n + 1)]))
    supported = mixed(n_supported) if n_supported else []
    threshold = data.draw(st.sampled_from([0.0, 0.3, 0.7]))
    min_answers = data.draw(st.integers(1, 35))
    answers, human, judged = _build(answer, correct, supported, covered)
    r = calibrate(answers, human, judged, threshold=threshold, min_answers=min_answers)
    tables = {"correct": correct, "supported": supported, "covered": covered}
    kappas = {d: (_kappa(p) if p else None) for d, p in tables.items()}
    sizes = {"correct": n, "supported": len(supported), "covered": n}
    expected = n >= min_answers and all(
        sizes[d] >= min_answers and kappas[d] is not None and kappas[d] >= threshold for d in DIMS
    )
    assert r.passed == expected
    for d in DIMS:
        assert r.dimensions[d].n == sizes[d]
        if kappas[d] is None:
            assert r.dimensions[d].kappa is None
        else:
            # tolerance: the same exact table, float summation order may differ
            assert r.dimensions[d].kappa == pytest.approx(kappas[d], abs=1e-9)
    order = data.draw(st.permutations(list(range(n))))
    r2 = calibrate([answers[i] for i in order], human, [judged[i] for i in reversed(order)],
                   threshold=threshold, min_answers=min_answers)
    assert r2.passed == r.passed
    for d in DIMS:
        assert r2.dimensions[d].kappa == r.dimensions[d].kappa or (
            r.dimensions[d].kappa is not None
            and r2.dimensions[d].kappa == pytest.approx(r.dimensions[d].kappa, abs=1e-9)
        )
    report = as_report(r)
    defined = [kappas[d] for d in DIMS]
    if any(k is None for k in defined):
        assert math.isnan(report.kappa)
    else:
        assert report.kappa == pytest.approx(min(defined), abs=1e-9)
    assert scores_reportable(VERSION, [report]) == r.passed
