"""C8: hashes, the calibration record and the committed record file."""

from __future__ import annotations

import hashlib
import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import (
    CalibrationResult,
    DimensionAgreement,
    answer_set_sha256,
    calibration_file_text,
    calibration_record,
    label_set_sha256,
    load_calibration_reports,
    record_sha256,
)

DIMS = ("correct", "supported", "covered")


def _sha(obj, **kw) -> str:
    text = json.dumps(obj, separators=(",", ":"), **kw)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _answers(answer):
    return [
        answer("b2", claims=[("s0", "Cash rose.", ["Cash rose 3%."])], key_points=["cash"]),
        answer(
            "a1",
            claims=[("s0", "Révenue grew.", ["p1", "p2"]), ("s1", "x", [])],
            key_points=["k0", "k1"],
        ),
    ]


def _labels() -> dict:
    return {
        ("b2", "correct", "s0"): True,
        ("b2", "supported", "s0"): False,
        ("b2", "covered", "kp0"): True,
        ("a1", "correct", "s0"): False,
        ("a1", "supported", "s0"): True,
        ("a1", "correct", "s1"): True,
        ("a1", "covered", "kp0"): True,
        ("a1", "covered", "kp1"): False,
    }


def _hand_label_hash(labels: dict) -> str:
    # oracle: requirement 12 - sorted [answer, dimension, item, value] lists, compact JSON
    return _sha(sorted([a, d, i, v] for (a, d, i), v in labels.items()))


def _hand_answer_hash(answers) -> str:
    # oracle: requirement 12 - one object per answer sorted by answer_id, sort_keys
    objs = [
        {
            "answer_id": a.answer_id,
            "case_id": a.case_id,
            "split": a.split,
            "generator_model": a.generator_model,
            "question": a.question,
            "reference_answer": a.reference_answer,
            "key_points": list(a.key_points),
            "claims": [
                {"claim_id": c.claim_id, "text": c.text, "cited_passages": list(c.cited_passages)}
                for c in a.claims
            ],
        }
        for a in sorted(answers, key=lambda a: a.answer_id)
    ]
    return _sha(objs, sort_keys=True)


def _dim(kappa):
    if kappa is None:
        return DimensionAgreement(n=2, p_o=1.0, p_e=1.0, kappa=None, ci_low=None, ci_high=None)
    return DimensionAgreement(
        n=100, p_o=0.9, p_e=0.5, kappa=kappa, ci_low=kappa - 0.1, ci_high=min(1.0, kappa + 0.1)
    )


def _result(model, family, kappas, *, passed, cost, ans, lab):
    return CalibrationResult(
        judge_version=f"v1:{model}",
        model=model,
        family=family,
        n_answers=100,
        n_judge_errors=2,
        dimensions={d: _dim(k) for d, k in zip(DIMS, kappas, strict=True)},
        threshold=0.7,
        min_answers=100,
        passed=passed,
        cost_usd=cost,
        answer_set_sha256=ans,
        label_set_sha256=lab,
    )


def _results(answers, labels):
    ans, lab = _hand_answer_hash(answers), _hand_label_hash(labels)
    luna = _result(
        "gpt-6-luna", "openai", (0.8, None, 0.75), passed=False, cost=0.2, ans=ans, lab=lab
    )
    haiku = _result(
        "claude-haiku-5-5", "anthropic", (0.9, 0.72, 0.8), passed=True, cost=0.05, ans=ans, lab=lab
    )
    return [luna, haiku]


@pytest.mark.criterion("C8")
def test_hashes(answer):
    # oracle: requirement 12, recomputed with hashlib and json in the test
    answers, labels = _answers(answer), _labels()
    assert label_set_sha256(labels) == _hand_label_hash(labels)
    assert answer_set_sha256(answers) == _hand_answer_hash(answers)
    rec = {"z": 1, "a": [1.5, None, "é"]}
    assert record_sha256(rec) == _sha(rec, sort_keys=True)


@pytest.mark.criterion("C8")
def test_record_and_file(answer):
    # oracle: requirements 13-14, fields listed by hand
    answers, labels = _answers(answer), _labels()
    results = _results(answers, labels)
    rec = calibration_record(results, answers=answers, human_labels=labels)
    assert set(rec) == {
        "schema", "answer_set_sha256", "label_set_sha256", "n_answers", "n_labels", "candidates"
    }
    # judge-calibration-one-class moves the schema to /2 and adds fields to each candidate and
    # dimension; its C5 pins those. Here the fields this spec lists are checked as a subset.
    assert rec["schema"] in {"judge-calibration/1", "judge-calibration/2"}
    assert rec["answer_set_sha256"] == _hand_answer_hash(answers)
    assert rec["label_set_sha256"] == _hand_label_hash(labels)
    assert (rec["n_answers"], rec["n_labels"]) == (2, 8)
    versions = [c["judge_version"] for c in rec["candidates"]]
    assert versions == ["v1:claude-haiku-5-5", "v1:gpt-6-luna"]
    haiku = rec["candidates"][0]
    listed = {
        "judge_version": "v1:claude-haiku-5-5",
        "model": "claude-haiku-5-5",
        "family": "anthropic",
        "n_answers": 100,
        "n_judge_errors": 2,
        "threshold": 0.7,
        "min_answers": 100,
        "passed": True,
        "cost_usd": 0.05,
    }
    assert {k: haiku.get(k) for k in listed} == listed
    listed_dim = {
        "n": 100,
        "p_o": 0.9,
        "p_e": 0.5,
        "kappa": 0.72,
        "ci_low": 0.72 - 0.1,
        "ci_high": 0.72 + 0.1,
    }
    supported = haiku["dimensions"]["supported"]
    assert {k: supported.get(k) for k in listed_dim} == listed_dim
    assert rec["candidates"][1]["dimensions"]["supported"]["kappa"] is None

    text = calibration_file_text(rec)
    doc = {"record": rec, "record_sha256": _sha(rec, sort_keys=True)}
    assert text == json.dumps(doc, indent=2, sort_keys=True) + "\n"
    reports = load_calibration_reports(text)
    assert [r.judge_version for r in reports] == ["v1:claude-haiku-5-5", "v1:gpt-6-luna"]
    # tolerance: values copied, not computed
    assert reports[0].kappa == pytest.approx(0.72, abs=1e-12)
    assert (reports[0].n, reports[0].min_items, reports[0].passed) == (100, 100, True)
    assert reports[0].threshold == pytest.approx(0.7, abs=1e-15)
    assert math.isnan(reports[1].kappa) and reports[1].passed is False


@pytest.mark.criterion("C8")
def test_tampering_and_mismatch_raise(answer):
    # oracle: requirements 13-14 - an edited record or a wrong schema is refused; a result
    # from another label set cannot enter the record
    answers, labels = _answers(answer), _labels()
    results = _results(answers, labels)
    rec = calibration_record(results, answers=answers, human_labels=labels)
    doc = json.loads(calibration_file_text(rec))
    doc["record"]["candidates"][1]["passed"] = True
    with pytest.raises(ValueError):
        load_calibration_reports(json.dumps(doc))
    bad_schema = {**rec, "schema": "judge-calibration/0"}
    with pytest.raises(ValueError):
        bad_doc = {"record": bad_schema, "record_sha256": _sha(bad_schema, sort_keys=True)}
        load_calibration_reports(json.dumps(bad_doc))
    other = _result("gpt-5-nano", "openai", (0.9, 0.9, 0.9), passed=True, cost=0.01,
                    ans=_hand_answer_hash(answers), lab="f" * 64)
    with pytest.raises(ValueError):
        calibration_record([*results, other], answers=answers, human_labels=labels)
    with pytest.raises(ValueError):
        calibration_record([results[0], results[0]], answers=answers, human_labels=labels)


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(st.data())
def test_label_change_and_order(answer, data):
    # oracle: requirements 12-13 - a changed label changes the label and record hashes;
    # reordering the label dict or the results changes neither
    answers, labels = _answers(answer), _labels()
    rec = calibration_record(_results(answers, labels), answers=answers, human_labels=labels)
    keys = list(labels)
    order = data.draw(st.permutations(keys))
    shuffled = {k: labels[k] for k in order}
    assert label_set_sha256(shuffled) == label_set_sha256(labels)
    reversed_results = list(reversed(_results(answers, labels)))
    rec2 = calibration_record(reversed_results, answers=answers, human_labels=shuffled)
    assert record_sha256(rec2) == record_sha256(rec)
    flip = data.draw(st.sampled_from(keys))
    changed = {**labels, flip: not labels[flip]}
    assert label_set_sha256(changed) != label_set_sha256(labels)
    rec3 = calibration_record(_results(answers, changed), answers=answers, human_labels=changed)
    assert rec3["label_set_sha256"] == _hand_label_hash(changed)
    assert record_sha256(rec3) != record_sha256(rec)
