"""C5: the judge-calibration/2 record, its hash and loading it (requirements 6-8)."""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.judge_calibration import (
    calibration_file_text,
    calibration_record,
    load_calibration_reports,
    record_sha256,
)

HAIKU, LUNA = "v1:claude-haiku-5-5", "v1:gpt-6-luna"
OLD_FIELDS = {"n", "p_o", "p_e", "kappa", "ci_low", "ci_high"}


def _sha(obj) -> str:
    text = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sets(kit):
    """Two candidates on one labelled set of 20 answers.

    correct (9,1,1,9) kappa 0.8 and covered (10,0,1,9) kappa 0.9 for both; human supported
    all y. Haiku calls 19 of 20 supported (p_o 0.95, passes); luna 18 of 20 (p_o 0.9, fails).
    """
    correct, covered = kit.pairs(9, 1, 1, 9), kit.pairs(10, 0, 1, 9)
    answers, human, haiku = kit.build(correct, kit.pairs(19, 0, 1, 0), covered, version=HAIKU)
    _, _, luna = kit.build(correct, kit.pairs(18, 0, 2, 0), covered, version=LUNA)
    return answers, human, haiku, luna


def _results(kit, answers, human, haiku, luna, *, min_answers=20, min_items=20):
    return [
        kit.calibrate(answers, human, jl, min_answers=min_answers, min_items=min_items)
        for jl in (luna, haiku)
    ]


@pytest.mark.criterion("C5")
def test_record_fields(kit):
    # oracle: requirement 7, fields and values listed by hand from the tables in _sets
    answers, human, haiku, luna = _sets(kit)
    rec = calibration_record(_results(kit, answers, human, haiku, luna), answers=answers,
                             human_labels=human)
    assert rec["schema"] == "judge-calibration/2"
    by_version = {c["judge_version"]: c for c in rec["candidates"]}
    assert [c["judge_version"] for c in rec["candidates"]] == [HAIKU, LUNA]
    for version, passed in ((HAIKU, True), (LUNA, False)):
        cand = by_version[version]
        assert cand["passed"] is passed
        assert cand.get("min_answers") == 20
        assert cand.get("min_items") == 20
        # tolerance: copied defaults
        assert cand.get("one_class_share") == pytest.approx(0.99, abs=1e-15)
        assert cand.get("agreement_bound") == pytest.approx(0.95, abs=1e-15)
        dims = cand["dimensions"]
        assert set(dims) == set(kit.DIMS)
        for dim, gate, share in (("correct", "kappa", 0.5), ("supported", "agreement", 1.0),
                                 ("covered", "kappa", 0.55)):
            assert OLD_FIELDS <= set(dims[dim])
            assert dims[dim].get("gate") == gate
            # tolerance: one exact division
            assert dims[dim].get("human_majority_share") == pytest.approx(share, abs=1e-15)
        assert dims["correct"].get("passed") is True
        assert dims["covered"].get("passed") is True
        assert dims["supported"].get("passed") is passed
    # tolerance: exact ratios through a few float operations
    assert by_version[HAIKU]["dimensions"]["supported"]["p_o"] == pytest.approx(0.95, abs=1e-12)
    assert by_version[LUNA]["dimensions"]["supported"]["p_o"] == pytest.approx(0.9, abs=1e-12)


@pytest.mark.criterion("C5")
def test_file_round_trip_and_old_schema(kit):
    # oracle: requirements 6 and 8, hashlib/json in the test - the file's hash is the
    # key-sorted compact JSON's; reports carry each candidate's passed and the smaller
    # kappa-gated kappa (0.8: supported's 0 is left out); a /1 record is refused
    answers, human, haiku, luna = _sets(kit)
    rec = calibration_record(_results(kit, answers, human, haiku, luna), answers=answers,
                             human_labels=human)
    assert record_sha256(rec) == _sha(rec)
    text = calibration_file_text(rec)
    assert json.loads(text) == {"record": rec, "record_sha256": _sha(rec)}
    reports = load_calibration_reports(text)
    assert [(r.judge_version, r.passed) for r in reports] == [(HAIKU, True), (LUNA, False)]
    for report in reports:
        # tolerance: a copied kappa
        assert report.kappa == pytest.approx(0.8, abs=1e-12)
        assert (report.n, report.min_items) == (20, 20)
    old = {**rec, "schema": "judge-calibration/1"}
    with pytest.raises(ValueError):
        load_calibration_reports(json.dumps({"record": old, "record_sha256": _sha(old)}))


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.data())
def test_hash_is_stable_and_carries_the_floors(kit, data):
    # oracle: requirement 7 - no timestamp, so equal inputs give equal hashes; result and
    # label order do not matter; min_answers and min_items are in the record, so changing
    # either changes the hash
    answers, human, haiku, luna = _sets(kit)
    first = calibration_record(_results(kit, answers, human, haiku, luna), answers=answers,
                               human_labels=human)
    order = data.draw(st.permutations(list(human)))
    shuffled = {k: human[k] for k in order}
    again = calibration_record(list(reversed(_results(kit, answers, shuffled, haiku, luna))),
                               answers=list(reversed(answers)), human_labels=shuffled)
    assert record_sha256(again) == record_sha256(first)
    assert first["schema"] == "judge-calibration/2"
    floors = data.draw(st.sampled_from([(19, 20), (20, 19), (1, 20), (20, 0)]))
    other = calibration_record(
        _results(kit, answers, human, haiku, luna, min_answers=floors[0], min_items=floors[1]),
        answers=answers, human_labels=human,
    )
    assert record_sha256(other) != record_sha256(first)
    assert {c.get("min_answers") for c in other["candidates"]} == {floors[0]}
    assert {c.get("min_items") for c in other["candidates"]} == {floors[1]}
