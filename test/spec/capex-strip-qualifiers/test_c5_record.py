"""C5: the one judge3 read is committed, pins its inputs and accepts the stripping rules.

Oracle: requirement 5 of docs/specs/capex-strip-qualifiers.md; judge3's SHA-256s pinned in the
spec Notes (and conftest) before any read; the accepted r2.1 copy's and the r2.1 oracle's
SHA-256s from witness-rules-r2-1's record; acceptance_r2_1's marks. The record's totals are
not known in advance and are not asserted, only its consistency and verdict.
"""

from __future__ import annotations

import json

import pytest


def _record(kit) -> dict:
    assert kit.record.exists(), "the judge3 record is written once, after the other tests pass"
    return json.loads(kit.record.read_text(encoding="utf-8"))


@pytest.mark.criterion("C5")
def test_record_pins_judge3_the_oracle_and_both_rules_files(kit):
    # oracle: requirement 5 - judge3's four hashes; the r2.1 oracle; the accepted r2.1 copy
    # as the base; the committed stripping copy as the candidate (a different file)
    rec = _record(kit)
    assert rec["spec"] == "capex-strip-qualifiers"
    assert rec["draw_sha256"] == kit.judge3
    assert rec["oracle_sha256"] == kit.oracle_sha == kit.sha(kit.oracle)
    assert rec["base_rules_sha256"] == kit.accepted_r2_1_sha == kit.sha(kit.accepted_r2_1)
    assert kit.accepted_strip.exists()
    assert rec["rules_sha256"] == kit.sha(kit.accepted_strip) != kit.accepted_r2_1_sha


@pytest.mark.criterion("C5")
def test_judged_copy_is_the_live_rules(kit):
    # oracle: requirement 5 - the judged file is a byte copy of the live rules at the read;
    # the live rules load equal to it in every family except Debt and DeferredRevenue (not
    # judged), and its Capex strips
    from src.eval.keys.statement_witness import load_statement_rules

    _record(kit)
    judged = load_statement_rules(kit.accepted_strip)
    live = kit.loaded().live
    assert set(judged) == set(live)
    for f in live:
        if f not in kit.unjudged:
            assert live[f] == judged[f], f
    assert judged["Capex"].get("strip_qualifiers") is True


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_record_is_consistent_and_accepts(kit):
    # oracle: requirement 5 and witness-rules-r2-1 requirement 4 - each Score is aggregate of
    # its own family counts; 48 families; equal evaluable counts per family; the verdict is
    # acceptance_r2_1(base, candidate) and accepts
    from src.eval.keys.witness_measure import acceptance_r2_1, aggregate

    rec = _record(kit)
    for side in ("base", "candidate"):
        score = rec[side]
        pairs = [(f, o) for f, c in score["families"].items() for o, n in c.items()
                 for _ in range(n)]
        assert aggregate(pairs) == score, side
    assert len(rec["base"]["families"]) == 48
    assert set(rec["base"]["families"]) == set(rec["candidate"]["families"])
    ev = ("hit", "none", "ambiguous", "wrong")
    for f in rec["base"]["families"]:
        assert sum(rec["base"]["families"][f][o] for o in ev) == sum(
            rec["candidate"]["families"][f][o] for o in ev), f
    assert rec["verdict"] == acceptance_r2_1(rec["base"], rec["candidate"])
    assert rec["verdict"]["accept"] is True
