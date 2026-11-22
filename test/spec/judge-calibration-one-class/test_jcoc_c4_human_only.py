"""C4: the gate is a property of the human labels alone (requirement 4)."""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import JudgeLabels


def _gate_fields(r):
    return getattr(r, "gates", None), getattr(r, "human_majority_share", None)


@pytest.mark.criterion("C4")
def test_judge_errors_do_not_move_the_gate(kit):
    # oracle: requirement 4 by hand - human supported is 19 y and 1 n over all 20 answers
    # (share 19/20 = 0.95 < 0.99, gate "kappa"). The one human n is on answer 19; a
    # judge_error there leaves 19 counted labels all y, but the gate stays "kappa" and the
    # share 19/20, because both are measured over every answer's human labels.
    supported = kit.pairs(19, 0, 0, 0) + [(False, False)]
    answers, human, judged = kit.build(
        kit.pairs(10, 0, 0, 10), supported, kit.pairs(10, 0, 0, 10), errors=frozenset({19})
    )
    r = kit.calibrate(answers, human, judged, min_answers=19, min_items=19)
    gates, shares = _gate_fields(r)
    assert gates == {"correct": "kappa", "supported": "kappa", "covered": "kappa"}
    # tolerance: one exact division
    assert (shares or {}).get("supported") == pytest.approx(0.95, abs=1e-15)
    assert r.dimensions["supported"].n == 19
    # 19 counted items, both raters all y: p_e = 1, kappa None -> the kappa gate fails
    assert r.dimensions["supported"].kappa is None
    assert r.passed is False


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.integers(2, 25), st.data())
def test_judge_labels_never_change_the_gate(kit, n, data):
    # oracle: requirement 4 closed form over all answers' human labels (exact fractions);
    # metamorphic: permuting the judge's labels across answers, replacing them, and turning
    # answers into judge_errors leave the gates and shares as they were
    pair = st.tuples(st.booleans(), st.booleans())
    mostly_y = st.sampled_from([(True, True), (False, True), (True, False)])
    def table(size):
        return data.draw(st.lists(st.one_of(pair, mostly_y), min_size=size, max_size=size))
    correct, covered = table(n), table(n)
    supported = table(data.draw(st.integers(1, n)))
    cut = data.draw(st.sampled_from([0.6, 0.9, 0.99]))
    answers, human, judged = kit.build(correct, supported, covered)
    base = kit.calibrate(answers, human, judged, min_answers=1, one_class_share=cut)
    expected_gates, expected_shares = {}, {}
    for dim, tab in (("correct", correct), ("supported", supported), ("covered", covered)):
        hum = [h for _, h in tab]
        share = Fraction(max(sum(hum), len(hum) - sum(hum)), len(hum))
        expected_shares[dim] = share
        expected_gates[dim] = "agreement" if share >= Fraction(str(cut)) else "kappa"
    gates, shares = _gate_fields(base)
    assert gates == expected_gates
    assert shares is not None
    for dim, share in expected_shares.items():
        # tolerance: one division of integers below 26
        assert shares[dim] == pytest.approx(float(share), abs=1e-15)

    # 1) within each dimension, the judge's labels permuted across the items
    moved: dict = {}
    for dim in kit.DIMS:
        keys = [k for jl in judged for k in jl.labels if k[1] == dim]
        vals = data.draw(st.permutations([j for jl in judged
                                          for k, j in jl.labels.items() if k[1] == dim]))
        moved.update(zip(keys, vals, strict=True))
    permuted = [
        JudgeLabels(answer_id=jl.answer_id, judge_version=jl.judge_version,
                    labels={k: moved[k] for k in jl.labels}, error=None, cost_usd=jl.cost_usd)
        for jl in judged
    ]
    # 2) every judge label replaced by a random one
    replaced = [
        JudgeLabels(answer_id=jl.answer_id, judge_version=jl.judge_version,
                    labels={k: data.draw(st.booleans()) for k in jl.labels}, error=None,
                    cost_usd=jl.cost_usd)
        for jl in judged
    ]
    # 3) some answers turned into judge_errors (at least one answer stays counted)
    errors = set(data.draw(st.lists(st.integers(0, n - 2), max_size=n - 1)))
    errored = [
        JudgeLabels(answer_id=jl.answer_id, judge_version=jl.judge_version, labels={},
                    error="judge_error: x", cost_usd=jl.cost_usd) if i in errors else jl
        for i, jl in enumerate(judged)
    ]
    for variant in (permuted, replaced, errored):
        r = kit.calibrate(answers, human, variant, min_answers=1, one_class_share=cut)
        assert _gate_fields(r) == (gates, shares)
