"""C3: calibrate with a one-class dimension, and the bridge to the gate (requirements 3-6)."""

from __future__ import annotations

import math
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge import scores_reportable
from src.eval.core.graders.judge_calibration import as_report


def _gates(r):
    return getattr(r, "gates", None)


def _tables(kit, supported):
    # correct (9,1,1,9): p_o 18/20, p_e 1/2, kappa 0.8;
    # covered (10,0,1,9): p_o 19/20, judge T 10/20, human T 11/20, p_e 1/2, kappa 0.9
    return kit.pairs(9, 1, 1, 9), supported, kit.pairs(10, 0, 1, 9)


@pytest.mark.criterion("C3")
def test_one_class_supported_passes_on_observed_agreement(kit):
    # oracle: hand-computed tables. supported: human 20 y; judge 19 y, 1 n, so p_o = 19/20,
    # p_e = (19/20)(20/20) = 19/20 and kappa = 0; human share 20/20 = 1 >= 0.99 -> agreement
    answers, human, judged = kit.build(*_tables(kit, kit.pairs(19, 0, 1, 0)))
    r = kit.calibrate(answers, human, judged, min_answers=20, min_items=20)
    assert _gates(r) == {"correct": "kappa", "supported": "agreement", "covered": "kappa"}
    shares = getattr(r, "human_majority_share", None) or {}
    # tolerance: exact ratios 10/20, 20/20, 11/20 through one division
    assert shares.get("correct") == pytest.approx(0.5, abs=1e-15)
    assert shares.get("supported") == pytest.approx(1.0, abs=1e-15)
    assert shares.get("covered") == pytest.approx(0.55, abs=1e-15)
    # tolerance: exact ratios through a few float operations
    assert r.dimensions["supported"].p_o == pytest.approx(0.95, abs=1e-12)
    assert r.dimensions["supported"].kappa == pytest.approx(0.0, abs=1e-12)
    assert r.dimensions["correct"].kappa == pytest.approx(0.8, abs=1e-12)
    assert r.dimensions["covered"].kappa == pytest.approx(0.9, abs=1e-12)
    assert r.passed is True
    assert getattr(r, "min_items", None) == 20
    # tolerance: defaults copied, not computed
    assert getattr(r, "one_class_share", None) == pytest.approx(0.99, abs=1e-15)
    assert getattr(r, "agreement_bound", None) == pytest.approx(0.95, abs=1e-15)
    # the report's kappa is the smaller kappa-gated kappa: supported's 0 is left out
    report = as_report(r)
    # tolerance: a copied kappa
    assert report.kappa == pytest.approx(0.8, abs=1e-12)
    assert report.passed is True
    assert scores_reportable(kit.VERSION, [report]) is True


@pytest.mark.criterion("C3")
def test_the_agreement_bound_and_the_extremes(kit):
    # oracle: hand counts. judge 18 of 20 supported: p_o = 18/20 < 0.95 fails on supported
    # alone (correct and covered as in the passing test)
    answers, human, judged = kit.build(*_tables(kit, kit.pairs(18, 0, 2, 0)))
    r = kit.calibrate(answers, human, judged, min_answers=20, min_items=20)
    assert _gates(r) == {"correct": "kappa", "supported": "agreement", "covered": "kappa"}
    assert r.passed is False
    assert scores_reportable(kit.VERSION, [as_report(r)]) is False
    # judge all 20 supported: p_e = 1, kappa None, p_o = 1 -> passes
    answers, human, judged = kit.build(*_tables(kit, kit.pairs(20, 0, 0, 0)))
    r = kit.calibrate(answers, human, judged, min_answers=20, min_items=20)
    assert r.dimensions["supported"].kappa is None
    assert _gates(r) is not None and _gates(r)["supported"] == "agreement"
    assert r.passed is True
    # tolerance: a copied kappa (supported's None is not kappa-gated, so not NaN)
    assert as_report(r).kappa == pytest.approx(0.8, abs=1e-12)
    # judge all 20 unsupported: every claim the minority label, p_o = 0 -> fails
    answers, human, judged = kit.build(*_tables(kit, kit.pairs(0, 0, 20, 0)))
    r = kit.calibrate(answers, human, judged, min_answers=20, min_items=20)
    # tolerance: an exact 0/20
    assert r.dimensions["supported"].p_o == pytest.approx(0.0, abs=1e-15)
    assert _gates(r) is not None and _gates(r)["supported"] == "agreement"
    assert r.passed is False


@pytest.mark.criterion("C3")
def test_below_the_cut_off_kappa_still_gates(kit):
    # oracle: hand-computed table - human correct 19 y, 1 n (share 19/20 = 0.95 < 0.99) and
    # a judge calling every claim correct: p_o = 19/20, p_e = (20/20)(19/20) = 19/20,
    # kappa = 0 -> gate "kappa", fails though p_o >= 0.95 (decision D1)
    answers, human, judged = kit.build(
        kit.pairs(19, 1, 0, 0), kit.pairs(20, 0, 0, 0), kit.pairs(10, 0, 1, 9)
    )
    r = kit.calibrate(answers, human, judged, min_answers=20, min_items=20)
    assert _gates(r) == {"correct": "kappa", "supported": "agreement", "covered": "kappa"}
    # tolerance: exact ratios through a few float operations
    assert r.dimensions["correct"].p_o == pytest.approx(0.95, abs=1e-12)
    assert r.dimensions["correct"].kappa == pytest.approx(0.0, abs=1e-12)
    assert r.passed is False
    # tolerance: the kappa-gated minimum is correct's 0
    assert as_report(r).kappa == pytest.approx(0.0, abs=1e-12)


@pytest.mark.criterion("C3")
def test_min_items_and_parameter_checks(kit):
    # oracle: requirement 4 - min_items floors every dimension apart from min_answers, and
    # defaults to min_answers; out-of-range cut-off and bound raise
    answers, human, judged = kit.build(*_tables(kit, kit.pairs(20, 0, 0, 0)))
    assert kit.calibrate(answers, human, judged, min_answers=1, min_items=20).passed is True
    assert kit.calibrate(answers, human, judged, min_answers=1, min_items=21).passed is False
    assert kit.calibrate(answers, human, judged, min_answers=20, min_items=21).passed is False
    default = kit.calibrate(answers, human, judged, min_answers=20)
    assert default.passed is True
    assert getattr(default, "min_items", None) == 20
    assert getattr(kit.calibrate(answers, human, judged, min_answers=7), "min_items", None) == 7
    for bad in ({"one_class_share": 0.5}, {"one_class_share": 1.01},
                {"agreement_bound": -0.01}, {"agreement_bound": 1.01}, {"min_items": -1}):
        with pytest.raises(ValueError):
            kit.calibrate(answers, human, judged, min_answers=20, **bad)
    # a passed cut-off and bound are used and kept: at cut-off 1.0 a 20/20 dimension is
    # still one class; at bound 1.0 a judge must agree on every item
    r = kit.calibrate(*kit.build(*_tables(kit, kit.pairs(19, 0, 1, 0))), min_answers=20,
                      one_class_share=1.0, agreement_bound=1.0)
    assert _gates(r) is not None and _gates(r)["supported"] == "agreement"
    assert r.passed is False
    # tolerance: copied parameters
    assert getattr(r, "agreement_bound", None) == pytest.approx(1.0, abs=1e-15)
    assert getattr(r, "one_class_share", None) == pytest.approx(1.0, abs=1e-15)


def _kappa(table) -> Fraction | None:
    """Cohen's kappa of (judge, human) pairs in exact fractions; None when p_e == 1."""
    n = len(table)
    p_o = Fraction(sum(a == b for a, b in table), n)
    pj = Fraction(sum(a for a, _ in table), n)
    ph = Fraction(sum(b for _, b in table), n)
    p_e = pj * ph + (1 - pj) * (1 - ph)
    return None if p_e == 1 else (p_o - p_e) / (1 - p_e)


@pytest.mark.criterion("C3")
@given(st.integers(2, 30), st.data())
def test_pass_rule_closed_form(kit, n, data):
    # oracle: requirements 2-4 closed form computed in the test: per dimension the gate from
    # the human share (exact), then kappa >= threshold or p_o >= bound, n >= min_items
    pair = st.tuples(st.booleans(), st.booleans())
    # dimensions lean one way often enough that both gates are drawn
    lean = st.sampled_from([(True, True), (False, True)])
    def table(size):
        return data.draw(st.lists(st.one_of(pair, lean), min_size=size, max_size=size))
    correct, covered = table(n), table(n)
    supported = table(data.draw(st.integers(0, n)))
    threshold = data.draw(st.sampled_from([0.0, 0.3, 0.7]))
    cut = data.draw(st.sampled_from([0.6, 0.9, 0.99, 1.0]))
    bound = data.draw(st.sampled_from([0.0, 0.8, 0.95]))
    min_answers = data.draw(st.integers(1, 31))
    min_items = data.draw(st.integers(0, 31))
    answers, human, judged = kit.build(correct, supported, covered)
    r = kit.calibrate(answers, human, judged, threshold=threshold, min_answers=min_answers,
                      min_items=min_items, one_class_share=cut, agreement_bound=bound)
    expected_gates, ok = {}, {}
    for dim, tab in (("correct", correct), ("supported", supported), ("covered", covered)):
        m = len(tab)
        hum = [h for _, h in tab]
        share = Fraction(max(sum(hum), m - sum(hum)), m) if m else None
        gate = "agreement" if share is not None and share >= Fraction(str(cut)) else "kappa"
        expected_gates[dim] = gate
        if gate == "agreement":
            ok[dim] = m >= min_items and m > 0 and (
                Fraction(sum(a == b for a, b in tab), m) >= Fraction(str(bound)))
        else:
            k = _kappa(tab) if m else None
            ok[dim] = m >= min_items and k is not None and k >= Fraction(str(threshold))
    assert _gates(r) == expected_gates
    assert r.passed is (n >= min_answers and all(ok.values()))
    assert scores_reportable(kit.VERSION, [as_report(r)]) is r.passed
    kappa_gated = [_kappa(t) if t else None for d, t in
                   (("correct", correct), ("supported", supported), ("covered", covered))
                   if expected_gates[d] == "kappa"]
    if not kappa_gated or any(k is None for k in kappa_gated):
        assert math.isnan(as_report(r).kappa)
    else:
        # tolerance: the same exact tables; float evaluation order may differ
        assert as_report(r).kappa == pytest.approx(float(min(kappa_gated)), abs=1e-9)
