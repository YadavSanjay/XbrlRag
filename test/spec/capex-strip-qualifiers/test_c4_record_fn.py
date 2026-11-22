"""C4: judge_record_strip scores base and candidate on a hash-checked sample.

Oracle: the hand-derived outcomes of witness-rules-r2's synthetic sample (its Notes; the
fixtures are witness-rules-r2-1's byte copies), the synthetic "r2" rules as the base, and
acceptance_r2_1's marks (witness-rules-r2-1 requirement 4) applied by hand.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

# tolerance: ratios of small integers; rel 1e-12 absorbs binary floating point only.
REL = 1e-12


def _stripping_copy(kit, tmp) -> object:
    """The synthetic r2 rules with `strip_qualifiers: true` on every family."""
    raw = yaml.safe_load((kit.synth / "rules_r2.yaml").read_text(encoding="utf-8"))
    for rule in raw["families"].values():
        rule["strip_qualifiers"] = True
    path = tmp / "rules_strip.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _r2_families(c) -> dict:
    # witness-rules-r2 Notes: the synthetic r2 rules' outcomes
    return {"TotalAssets": c(hit=2, no_oracle=1, no_oracle_selected=1),
            "Revenue": c(hit=1, ambiguous=1, oracle_ambiguous=1, no_oracle_selected=1),
            "NetIncome": c(hit=2, no_oracle_selected=1, no_oracle=1)}


@pytest.mark.criterion("C4")
def test_synthetic_record_with_no_qualified_label(kit, tmp_path):
    # oracle: by hand - no synthetic label holds a cue, so stripping changes nothing: base and
    # candidate both score r2's outcomes (hit 5, ambiguous 1 of 6 evaluable); the verdict
    # fails ambiguity only (1/6 > 0.025); the record names this spec
    from src.eval.keys.witness_measure import judge_record_strip

    cand = _stripping_copy(kit, tmp_path)
    base, op = kit.synth / "rules_r2.yaml", kit.synth / "oracle_synthetic.yaml"
    rec = judge_record_strip(kit.synth, cand, base, op, kit.table_hashes(kit.synth))
    assert set(rec) == {"spec", "draw_sha256", "oracle_sha256", "rules_sha256",
                        "base_rules_sha256", "base", "candidate", "verdict"}
    assert rec["spec"] == "capex-strip-qualifiers"
    assert rec["draw_sha256"] == kit.table_hashes(kit.synth)
    assert (rec["oracle_sha256"], rec["rules_sha256"], rec["base_rules_sha256"]) == (
        kit.sha(op), kit.sha(cand), kit.sha(base))
    assert rec["base"]["families"] == rec["candidate"]["families"] == _r2_families(kit.counts)
    assert rec["candidate"]["evaluable"] == 6
    assert rec["candidate"]["recall"] == pytest.approx(5 / 6, rel=REL)
    assert rec["verdict"] == {"accept": False, "failed": ["ambiguity"], "regressed_families": 0}


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_a_qualifier_that_hides_a_component_is_caught(kit, tmp_path):
    # oracle: by hand - relabel S2's ProfitLoss line (60) "Net income, excluding
    # noncontrolling interest". The base still rejects it ("noncontrolling") and hits on
    # "Attributable to parent" (55). The candidate sees the head "Net income": two lines,
    # 60 and 55, so S2 NetIncome turns ambiguous: candidate hit 4, ambiguous 2 of 6; the
    # verdict fails ambiguity (2/6) and recall_regression (4 < 5); NetIncome has n = 2 < 10
    from src.eval.keys.witness_measure import judge_record_strip

    sample = tmp_path / "sample"
    shutil.copytree(kit.synth, sample)
    pre = (sample / "pre.tsv").read_bytes()
    old = b"Net income including noncontrolling interest"
    assert pre.count(old) == 1
    (sample / "pre.tsv").write_bytes(
        pre.replace(old, b"Net income, excluding noncontrolling interest"))
    cand = _stripping_copy(kit, tmp_path)
    rec = judge_record_strip(sample, cand, sample / "rules_r2.yaml",
                             sample / "oracle_synthetic.yaml", kit.table_hashes(sample))
    c = kit.counts
    assert rec["base"]["families"] == _r2_families(c)
    assert rec["candidate"]["families"]["NetIncome"] == c(hit=1, ambiguous=1,
                                                          no_oracle_selected=1, no_oracle=1)
    assert rec["candidate"]["totals"]["hit"] == 4
    assert rec["verdict"] == {"accept": False, "failed": ["ambiguity", "recall_regression"],
                              "regressed_families": 0}


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("change", ["wrong_hash", "missing_key", "extra_key", "missing_file"])
def test_the_draw_is_checked(kit, tmp_path, change):
    # oracle: requirement 4 - the hash map has exactly sub, pre, num, tag, each matching its
    # file (ValueError otherwise); a missing table raises FileNotFoundError
    from src.eval.keys.witness_measure import judge_record_strip

    sample = tmp_path / "sample"
    shutil.copytree(kit.synth, sample)
    h = kit.table_hashes(sample)
    err: type[Exception] = ValueError
    if change == "wrong_hash":
        h["num"] = "0" * 64
    elif change == "missing_key":
        del h["tag"]
    elif change == "extra_key":
        h["draw"] = "0" * 64
    else:
        (sample / "tag.tsv").unlink()
        err = FileNotFoundError
    with pytest.raises(err):
        judge_record_strip(sample, sample / "rules_r2.yaml", sample / "rules_r2.yaml",
                           sample / "oracle_synthetic.yaml", h)
