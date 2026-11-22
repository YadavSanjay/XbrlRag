"""C6: judge_record scores both rules files on a hash-checked sample.

Oracle: the hand-derived synthetic outcomes and totals of the spec Notes.
"""

from __future__ import annotations

import pytest

from src.eval.keys.witness_measure import judge_record

# tolerance: ratios of small integers; rel 1e-12 absorbs binary floating point only.
REL = 1e-12


def _paths(kit):
    s = kit.synth
    return s, s / "rules_r2.yaml", s / "rules_r1.yaml", s / "oracle_synthetic.yaml"


@pytest.mark.criterion("C6")
def test_synthetic_record(kit):
    # oracle: spec Notes — r1 hit 3 none 1 ambiguous 1 wrong 1 (recall 1/2, precision 3/4);
    # r2 hit 5 ambiguous 1 (recall 5/6, precision 1); ambiguity 1/6 both; reject on
    # ambiguity and recall
    sample, r2p, r1p, op = _paths(kit)
    rec = judge_record(sample, r2p, r1p, op, kit.synth_hashes())
    c = kit.counts
    assert rec["spec"] == "witness-rules-r2"
    assert rec["draw_sha256"] == kit.synth_hashes()
    assert rec["oracle_sha256"] == kit.sha(op)
    assert rec["rules_sha256"] == kit.sha(r2p)
    assert rec["r1_rules_sha256"] == kit.sha(r1p)
    assert rec["r1"]["families"] == {
        "TotalAssets": c(hit=1, none=1, no_oracle=1, no_oracle_selected=1),
        "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
        "NetIncome": c(hit=1, wrong=1, no_oracle_selected=1, no_oracle=1)}
    assert rec["r2"]["families"] == {
        "TotalAssets": c(hit=2, no_oracle=1, no_oracle_selected=1),
        "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
        "NetIncome": c(hit=2, no_oracle_selected=1, no_oracle=1)}
    assert rec["r1"]["evaluable"] == rec["r2"]["evaluable"] == 6
    assert rec["r1"]["recall"] == pytest.approx(1 / 2, rel=REL)
    assert rec["r1"]["precision"] == pytest.approx(3 / 4, rel=REL)
    assert rec["r2"]["recall"] == pytest.approx(5 / 6, rel=REL)
    assert rec["r2"]["precision"] == pytest.approx(1.0, rel=REL)
    assert rec["r2"]["ambiguity"] == pytest.approx(1 / 6, rel=REL)
    assert rec["verdict"] == {"accept": False, "failed": ["ambiguity", "recall"],
                              "regressed_families": 0}


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("change", ["wrong_hash", "missing_key", "extra_key"])
def test_hash_map_is_checked(kit, change):
    # oracle: requirement 6 step 1 — exactly sub/pre/num/tag, each matching its file
    sample, r2p, r1p, op = _paths(kit)
    h = dict(kit.synth_hashes())
    if change == "wrong_hash":
        h["num"] = "0" * 64
    elif change == "missing_key":
        del h["tag"]
    else:
        h["extra"] = h["sub"]
    with pytest.raises(ValueError):
        judge_record(sample, r2p, r1p, op, h)


@pytest.mark.criterion("C6")
def test_missing_table_file(kit, tmp_path):
    # oracle: requirement 6 step 1 — a missing file raises FileNotFoundError
    sample, r2p, r1p, op = _paths(kit)
    for t in ("sub", "pre", "num"):
        (tmp_path / f"{t}.tsv").write_bytes((sample / f"{t}.tsv").read_bytes())
    with pytest.raises(FileNotFoundError):
        judge_record(tmp_path, r2p, r1p, op, kit.synth_hashes())


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_same_rules_give_equal_scores(kit):
    # oracle: the unperturbed r1 score of test_synthetic_record — scoring r1 as both files
    # gives two equal scores, and the verdict fails on all three global marks
    sample, _, r1p, op = _paths(kit)
    rec = judge_record(sample, r1p, r1p, op, kit.synth_hashes())
    assert rec["r1"] == rec["r2"]
    assert rec["r1"]["totals"] == kit.counts(hit=3, none=1, ambiguous=1, wrong=1,
                                             oracle_ambiguous=1, no_oracle=2,
                                             no_oracle_selected=3)
    assert rec["rules_sha256"] == rec["r1_rules_sha256"]
    assert rec["verdict"] == {"accept": False, "failed": ["ambiguity", "precision", "recall"],
                              "regressed_families": 0}
