"""C6: the committed r2.1 judging record shows one read on the fresh draw that accepts r2.1.

Oracle: the pinned judge2 draw hashes of the spec Notes, the accepted r2 rules' SHA-256 (r2's
judging record), the locked r2.1 oracle, and requirement 4 of docs/specs/witness-rules-r2-1.md.
"""

from __future__ import annotations

import json

import pytest

from src.eval.keys.witness_measure import acceptance_r2_1, aggregate

DRAW_SHA256 = {
    "sub": "6cf3c9384d7662f4501d0b39a0ac64298c6ee4a6070a8d7dbf0f29e9fc123354",
    "pre": "ae8aec97e1e86ef5d8f96f624e5a8fbc286c85f7e23686976b508e9bacbb6edf",
    "num": "8a2ce00f08d3c0367af69c5b1e02d4b1e6fc37a65371ee4392332d91a51d3d38",
    "tag": "cdc7e2b7ef1bdd808d90fd3850176f8ec1e6bd1a46f13c4f6d6a5cded3b0f1c8",
}
# The first judging draw (witness-rules-r2, spent): the record must not reuse it.
R2_JUDGE_SUB = "24d067a9946c0d93a0da244996d05623b5b0c7fa056fed77a3c708fae3d00fe4"


def _record(kit):
    assert kit.record.exists(), "configs/eval/v2/statement_lines.acceptance_r2_1.json is missing"
    return json.loads(kit.record.read_text(encoding="utf-8"))


@pytest.mark.criterion("C6")
def test_record_pins_the_draw_oracle_and_rules(kit):
    # oracle: spec Notes (judge2 hashes; r2 rules SHA-256) and requirement 5
    rec = _record(kit)
    assert rec["spec"] == "witness-rules-r2-1"
    assert rec["draw_sha256"] == DRAW_SHA256
    assert rec["draw_sha256"]["sub"] != R2_JUDGE_SUB
    assert rec["oracle_sha256"] == kit.sha(kit.oracle)
    assert rec["base_rules_sha256"] == kit.r2_sha == kit.sha(kit.r2_rules)
    assert rec["rules_sha256"] != rec["base_rules_sha256"]


@pytest.mark.criterion("C6")
def test_record_rules_are_the_accepted_copy_and_the_live_rules_follow_it(kit):
    # oracle: requirement 5 — the judged file is the committed byte copy of the r2.1 rules;
    # the live rules load equal to it in every family except Debt and DeferredRevenue, which
    # are the copy's, key-definitions' or debt-ifrs-elements' locked entries (not judged)
    from src.eval.keys.statement_witness import load_statement_rules

    rec = _record(kit)
    assert kit.accepted_rules.exists()
    assert rec["rules_sha256"] == kit.sha(kit.accepted_rules)
    live = load_statement_rules(kit.live_rules)
    accepted = load_statement_rules(kit.accepted_rules)
    kd = load_statement_rules(kit.kd_rules)
    debt = load_statement_rules(kit.debt_rules)
    assert set(live) == set(accepted)
    for f in live:
        if f in kit.untouched:
            assert live[f] in (accepted[f], kd[f], debt[f])
        elif f == "Capex":
            # capex-strip-qualifiers (judged on judge3) adds `strip_qualifiers: true`
            assert live[f] in (accepted[f], {**accepted[f], "strip_qualifiers": True})
        else:
            assert live[f] == accepted[f]
    assert len(rec["base"]["families"]) == 48
    assert set(rec["base"]["families"]) == set(rec["candidate"]["families"])


@pytest.mark.criterion("C6")
def test_record_accepts_r2_1(kit):
    # oracle: requirement 4 — the stored verdict is acceptance_r2_1(base, candidate), it
    # accepts, and evaluability does not depend on the rules
    rec = _record(kit)
    assert rec["base"]["evaluable"] == rec["candidate"]["evaluable"] > 0
    v = acceptance_r2_1(rec["base"], rec["candidate"])
    assert v == rec["verdict"]
    assert v == {"accept": True, "failed": [], "regressed_families": 0}


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_record_scores_are_their_own_aggregates(kit):
    # oracle: witness-rules-r2 requirement 4 — each stored Score equals aggregate over its
    # own family counts
    rec = _record(kit)
    for side in ("base", "candidate"):
        assert aggregate(kit.pairs(rec[side]["families"])) == rec[side]
