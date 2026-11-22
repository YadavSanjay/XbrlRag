"""C4: judge_record_r2_1 scores the base (r2) and candidate (r2.1) rules on a checked sample.

Oracle: the hand-derived synthetic outcomes of docs/specs/witness-rules-r2.md Notes
("Synthetic sample"; the fixtures here are byte copies), and requirement 4 of
docs/specs/witness-rules-r2-1.md applied to them by hand.
"""

from __future__ import annotations

import pytest

from src.eval.keys.witness_measure import judge_record_r2_1

# tolerance: ratios of small integers; rel 1e-12 absorbs binary floating point only.
REL = 1e-12


def _paths(kit):
    s = kit.synth
    return s, s / "rules_r2.yaml", s / "rules_r1.yaml", s / "oracle_synthetic.yaml"


@pytest.mark.criterion("C4")
def test_synthetic_record(kit):
    # oracle: witness-rules-r2 Notes — synthetic "r1" rules hit 3 none 1 ambiguous 1 wrong 1;
    # synthetic "r2" rules hit 5 ambiguous 1; here the first is the base and the second the
    # candidate: ambiguity 1/6 > 0.025 fails; precision 1 passes; no absolute recall mark
    # (recall 5/6 is not judged against a level); 5 >= 3 hits and no family has n >= 10, so
    # no regression code
    sample, cand, base, op = _paths(kit)
    rec = judge_record_r2_1(sample, cand, base, op, kit.synth_hashes())
    c = kit.counts
    assert rec["spec"] == "witness-rules-r2-1"
    assert rec["draw_sha256"] == kit.synth_hashes()
    assert rec["oracle_sha256"] == kit.sha(op)
    assert rec["rules_sha256"] == kit.sha(cand)
    assert rec["base_rules_sha256"] == kit.sha(base)
    assert rec["base"]["families"] == {
        "TotalAssets": c(hit=1, none=1, no_oracle=1, no_oracle_selected=1),
        "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
        "NetIncome": c(hit=1, wrong=1, no_oracle_selected=1, no_oracle=1)}
    assert rec["candidate"]["families"] == {
        "TotalAssets": c(hit=2, no_oracle=1, no_oracle_selected=1),
        "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
        "NetIncome": c(hit=2, no_oracle_selected=1, no_oracle=1)}
    assert rec["base"]["evaluable"] == rec["candidate"]["evaluable"] == 6
    assert rec["candidate"]["recall"] == pytest.approx(5 / 6, rel=REL)
    assert rec["base"]["precision"] == pytest.approx(3 / 4, rel=REL)
    assert rec["verdict"] == {"accept": False, "failed": ["ambiguity"],
                              "regressed_families": 0}
    assert set(rec) == {"spec", "draw_sha256", "oracle_sha256", "rules_sha256",
                        "base_rules_sha256", "base", "candidate", "verdict"}


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_swapping_base_and_candidate_adds_recall_regression(kit):
    # oracle: by hand from the same outcomes — with the better rules as the base, the
    # candidate (3 hits, 1 wrong, 1 ambiguous of 6) fails ambiguity (1/6 > 0.025), precision
    # (3/4 < 0.992) and recall_regression (3 < 5); there is no absolute recall code
    sample, better, worse, op = _paths(kit)
    rec = judge_record_r2_1(sample, worse, better, op, kit.synth_hashes())
    assert rec["verdict"] == {"accept": False,
                              "failed": ["ambiguity", "precision", "recall_regression"],
                              "regressed_families": 0}
    assert rec["base"]["totals"]["hit"] == 5
    assert rec["candidate"]["totals"]["hit"] == 3


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("change", ["wrong_hash", "missing_key", "extra_key"])
def test_hash_map_is_checked(kit, change):
    # oracle: requirement 5 — exactly sub/pre/num/tag, each matching its file
    sample, cand, base, op = _paths(kit)
    h = dict(kit.synth_hashes())
    if change == "wrong_hash":
        h["num"] = "0" * 64
    elif change == "missing_key":
        del h["tag"]
    else:
        h["extra"] = h["sub"]
    with pytest.raises(ValueError):
        judge_record_r2_1(sample, cand, base, op, h)


@pytest.mark.criterion("C4")
def test_missing_table_file(kit, tmp_path):
    # oracle: requirement 5 — a missing table file raises FileNotFoundError
    sample, cand, base, op = _paths(kit)
    for t in ("sub", "pre", "num"):
        (tmp_path / f"{t}.tsv").write_bytes((sample / f"{t}.tsv").read_bytes())
    with pytest.raises(FileNotFoundError):
        judge_record_r2_1(tmp_path, cand, base, op, kit.synth_hashes())
