"""C7: the committed judging record shows one pre-registered read that accepts r2.

Oracle: the pinned judging-draw hashes and the pre-registered r1 baseline of the spec Notes,
and the marks of requirement 5.
"""

from __future__ import annotations

import json

import pytest

from src.eval.keys.witness_measure import acceptance, aggregate

DRAW_SHA256 = {
    "sub": "24d067a9946c0d93a0da244996d05623b5b0c7fa056fed77a3c708fae3d00fe4",
    "pre": "288cf49d1ec5fb140bf2249acc89a7b9e9923317f88e14f1c4350f9433ba3185",
    "num": "0c4046a158c58751da492d719c0571eec9808d0d3420c93a0c1d4fcf0898fca8",
    "tag": "e230c280c076c9112428fd0d2da79af68f79d53c0aed27b8a082cc2af8c01a4f",
}
R2_RULES_SHA256 = "947ba5c6ac81628c96edcfe9661a1de86fd3dd314e0331947fab70fa67ee4a9b"
# tolerance: the baseline is pinned to the 4 decimals it was recorded with.
ABS4 = 5e-5


def _record(kit):
    assert kit.record.exists(), "configs/eval/v2/statement_lines.acceptance.json is missing"
    return json.loads(kit.record.read_text(encoding="utf-8"))


@pytest.mark.criterion("C7")
def test_record_pins_the_draw_oracle_and_rules(kit):
    # oracle: spec Notes (draw hashes, r1 SHA-256) and requirement 7. The judged r2 rules are
    # the byte copy fixtures/statement_lines_r2.yaml (the live file is r2.1 now, spec
    # witness-rules-r2-1); its SHA-256 is pinned here as recorded at acceptance.
    rec = _record(kit)
    assert rec["spec"] == "witness-rules-r2"
    assert rec["draw_sha256"] == DRAW_SHA256
    assert rec["oracle_sha256"] == kit.sha(kit.oracle)
    assert rec["r1_rules_sha256"] == kit.r1_sha == kit.sha(kit.r1_rules)
    assert rec["rules_sha256"] == R2_RULES_SHA256 == kit.sha(kit.r2_rules)
    assert rec["rules_sha256"] != rec["r1_rules_sha256"]


@pytest.mark.criterion("C7")
def test_record_reproduces_the_preregistered_baseline(kit):
    # oracle: spec Notes r1 judging row (3,603 evaluable; 0.8843 / 0.9950 / 0.0414), recorded
    # before the marks were set; evaluability does not depend on the rules (requirement 3)
    rec = _record(kit)
    assert rec["r1"]["evaluable"] == 3603
    assert rec["r2"]["evaluable"] == 3603
    assert rec["r1"]["recall"] == pytest.approx(0.8843, abs=ABS4)
    assert rec["r1"]["precision"] == pytest.approx(0.9950, abs=ABS4)
    assert rec["r1"]["ambiguity"] == pytest.approx(0.0414, abs=ABS4)
    assert len(rec["r1"]["families"]) == 48


@pytest.mark.criterion("C7")
def test_record_accepts_r2(kit):
    # oracle: requirement 5 — the stored verdict is acceptance(r1, r2) and it accepts
    rec = _record(kit)
    v = acceptance(rec["r1"], rec["r2"])
    assert v == rec["verdict"]
    assert v == {"accept": True, "failed": [], "regressed_families": 0}


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_record_scores_are_their_own_aggregates(kit):
    # oracle: requirement 4 — each stored Score equals aggregate over its own family counts
    rec = _record(kit)
    for side in ("r1", "r2"):
        assert aggregate(kit.pairs(rec[side]["families"])) == rec[side]
